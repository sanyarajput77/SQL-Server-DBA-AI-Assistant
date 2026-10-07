import asyncio
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MCP_SERVER_PATH = PROJECT_ROOT / "mcp-server" / "server.py"
spec = importlib.util.spec_from_file_location("dba_mcp_server", MCP_SERVER_PATH)
assert spec is not None and spec.loader is not None
mcp_server = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mcp_server
spec.loader.exec_module(mcp_server)


EXPECTED_OPS_FUNCTIONS = {
    "Get-OpsDatabaseInventory",
    "Get-OpsDatabaseHealth",
    "Get-OpsDatabaseSize",
    "Get-OpsBackupStatus",
    "Get-OpsDiskSpace",
    "Get-OpsFailedJobs",
    "Get-OpsBlockingSessions",
    "Get-OpsLongRunningQueries",
    "Get-OpsServerHealthReport",
    "Backup-OpsDatabase",
}


@contextmanager
def configured_targets():
    with tempfile.TemporaryDirectory() as directory:
        config_path = Path(directory) / "lab-config.json"
        config_path.write_text(
            json.dumps(
                {
                    "Environment": "Test",
                    "EnvironmentId": "Test-MCP",
                    "ApprovedInstances": ["LAB-SQL", "LAB-SQL\\INSTANCE"],
                    "DefaultInstance": "LAB-SQL",
                    "ApprovedDatabases": ["LabDb", "Other Approved DB"],
                    "ProtectedDatabases": ["master", "model", "msdb", "tempdb"],
                    "TrustServerCertificate": False,
                    "BackupDirectory": "../Backups/Test",
                    "ReportDirectory": "../Reports/Test",
                    "LogDirectory": "../Logs/Test",
                    "Thresholds": {"BackupWarningHours": 24, "DiskWarningPercentFree": 15, "LongRunningQuerySeconds": 60, "FailedJobsHours": 24},
                    "Timeouts": {"QueryTimeoutSeconds": 30, "ConnectionTimeoutSeconds": 15},
                    "BackupPolicy": {"Enabled": True, "AllowedTypes": ["Full", "Differential", "Log"], "ApprovalRequired": True},
                }
            ),
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"DBA_AUTOMATION_CONFIG": str(config_path), "DBA_AUTOMATION_ENV": "Test"}):
            yield config_path


class McpToolSurfaceTests(unittest.TestCase):
    def test_exactly_predefined_ops_capabilities_are_registered_and_dispatched(self):
        tools = asyncio.run(mcp_server.server.list_tools())
        names = {tool.name for tool in tools}
        self.assertEqual(
            names,
            {
                "get_ops_database_inventory",
                "get_ops_database_health",
                "get_ops_database_size",
                "get_ops_backup_status",
                "get_ops_disk_space",
                "get_ops_failed_jobs",
                "get_ops_blocking_sessions",
                "get_ops_long_running_queries",
                "get_ops_server_health_report",
                "backup_ops_database",
            },
        )
        self.assertEqual(set(mcp_server.OPS_PARAMETER_ALLOWLIST), EXPECTED_OPS_FUNCTIONS)
        self.assertEqual(set(mcp_server.MCP_TOOL_TO_OPS.values()), EXPECTED_OPS_FUNCTIONS)
        public_functions = {
            re.search(r"(?m)^function\s+(\S+)", path.read_text(encoding="utf-8")).group(1)
            for path in (PROJECT_ROOT / "DbaAutomation" / "Public").glob("*.ps1")
        }
        self.assertEqual(public_functions, EXPECTED_OPS_FUNCTIONS)
        bridge = mcp_server.BRIDGE_PATH.read_text(encoding="utf-8")
        dispatched = set(
            re.findall(r"^\s*'((?:Get-Ops|Backup-Ops)[A-Za-z]+)'\s*\{", bridge, re.M)
        )
        self.assertEqual(dispatched, EXPECTED_OPS_FUNCTIONS)
        self.assertNotIn("get_ops_config", names)

    def test_tool_descriptions_label_behavior_and_exclude_arbitrary_inputs(self):
        tools = asyncio.run(mcp_server.server.list_tools())
        for tool in tools:
            properties = {name.casefold() for name in tool.input_schema["properties"]}
            self.assertFalse(
                properties
                & {
                    "sql", "query", "command", "shell", "executable", "pwsh_path",
                    "script_path", "password", "credential", "token", "secret",
                }
            )
            if tool.name == "backup_ops_database":
                self.assertIn("NEEDS_APPROVAL", tool.description)
                self.assertFalse(properties & {"path", "backup_path", "backup_directory"})
            elif tool.name == "get_ops_server_health_report":
                self.assertIn("READ_ONLY_SQL", tool.description)
                self.assertIn("WRITES_REPORT", tool.description)
            else:
                self.assertIn("READ_ONLY:", tool.description)

    def test_backup_is_destructive_approval_is_hidden_and_type_is_fixed(self):
        tools = asyncio.run(mcp_server.server.list_tools())
        backup = next(tool for tool in tools if tool.name == "backup_ops_database")
        self.assertTrue(backup.annotations.destructive_hint)
        self.assertFalse(backup.annotations.read_only_hint)
        self.assertIn("NEEDS_APPROVAL", backup.annotations.title)
        self.assertNotIn("approval", backup.input_schema["properties"])
        self.assertEqual(
            backup.input_schema["properties"]["backup_type"]["enum"],
            ["Full", "Differential", "Log"],
        )

    def test_report_does_not_expose_arbitrary_output_path(self):
        tools = asyncio.run(mcp_server.server.list_tools())
        report = next(tool for tool in tools if tool.name == "get_ops_server_health_report")
        self.assertNotIn("output_path", report.input_schema["properties"])
        self.assertFalse(report.annotations.read_only_hint)


class McpDispatchTests(unittest.TestCase):
    def test_missing_config_does_not_fall_back_to_development(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "invalid or unavailable"):
                mcp_server._load_target_configuration()

    def test_disabled_backup_policy_rejects_before_powerShell_dispatch(self):
        with configured_targets() as config_path, patch.object(mcp_server.subprocess, "run") as run:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["BackupPolicy"] = {"Enabled": False, "AllowedTypes": [], "ApprovalRequired": True}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(PermissionError, "Backup is disabled"):
                mcp_server.invoke_ops_tool(
                    "Backup-OpsDatabase",
                    {"database": "LabDb", "backup_type": "Full"},
                    approval_granted=True,
                )
            run.assert_not_called()

    def test_arbitrary_function_and_arbitrary_parameters_are_rejected(self):
        with self.assertRaises(ValueError):
            mcp_server.invoke_ops_tool("Invoke-Expression", {})
        with self.assertRaises(ValueError):
            mcp_server.invoke_ops_tool(
                "Get-OpsDatabaseInventory", {"SqlQuery": "SELECT 1"}
            )

    def test_instance_and_database_allowlists_precede_powershell(self):
        with configured_targets(), patch.object(mcp_server.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "instance is not allowlisted"):
                mcp_server.invoke_ops_tool(
                    "Get-OpsDatabaseInventory", {"sql_instance": "NOT-ALLOWED"}
                )
            with self.assertRaisesRegex(ValueError, "Database is not approved"):
                mcp_server.invoke_ops_tool(
                    "Get-OpsDatabaseInventory",
                    {"sql_instance": "LAB-SQL", "databases": ["SecretDb"]},
                )
            with self.assertRaisesRegex(ValueError, "System databases are protected"):
                mcp_server.invoke_ops_tool(
                    "Backup-OpsDatabase",
                    {"sql_instance": "LAB-SQL", "database": "master", "backup_type": "Full"},
                    approval_granted=True,
                )
            run.assert_not_called()

    def test_credential_like_input_is_rejected_and_not_logged(self):
        secret = "Password=TopSecret123"
        with self.assertLogs(mcp_server.logger, level="INFO") as captured:
            with self.assertRaisesRegex(ValueError, "Credential-like values") as caught:
                mcp_server.invoke_ops_tool(
                    "Get-OpsDatabaseInventory", {"sql_instance": secret}
                )
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, "\n".join(captured.output))

    def test_invalid_config_fails_closed_without_exposing_config_path(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "private-config.json"
            config_path.write_text("not-json", encoding="utf-8")
            with patch.dict(os.environ, {"DBA_AUTOMATION_CONFIG": str(config_path)}):
                with self.assertRaises(RuntimeError) as caught:
                    mcp_server._load_target_configuration()
        self.assertNotIn(str(config_path), str(caught.exception))

    def test_backup_requires_approval_before_powershell_and_decline_does_not_dispatch(self):
        with patch.object(mcp_server.subprocess, "run") as run:
            with self.assertRaises(PermissionError):
                mcp_server.invoke_ops_tool(
                    "Backup-OpsDatabase",
                    {"database": "LabDb", "backup_type": "Full"},
                )
            run.assert_not_called()
        with patch.object(mcp_server, "invoke_ops_tool") as invoke:
            result = mcp_server.backup_ops_database(
                database="LabDb",
                backup_type="Full",
                sql_instance=None,
                approval=mcp_server.BackupApproval(approved=False),
            )
        self.assertEqual(json.loads(result)["status"], "NEEDS_APPROVAL")
        invoke.assert_not_called()

    def test_backup_handler_requires_a_resolved_approval_value(self):
        with patch.object(mcp_server, "invoke_ops_tool") as invoke:
            with self.assertRaises(TypeError):
                mcp_server.backup_ops_database(database="LabDb", backup_type="Full")
            invoke.assert_not_called()

    def test_backup_fails_closed_when_client_cannot_complete_elicitation(self):
        with configured_targets(), patch.object(mcp_server, "invoke_ops_tool") as invoke:
            result = asyncio.run(
                mcp_server.server.call_tool(
                    "backup_ops_database",
                    {"sql_instance": "LAB-SQL", "database": "LabDb", "backup_type": "Full"},
                )
            )
        self.assertTrue(result.is_error)
        self.assertIn("Details were withheld", result.content[0].text)
        invoke.assert_not_called()

    def test_sdk_argument_validation_error_does_not_echo_secret_input(self):
        secret = "Password=TopSecret123"
        with patch.object(mcp_server.subprocess, "run") as run:
            result = asyncio.run(
                mcp_server.server.call_tool(
                    "get_ops_failed_jobs", {"since_hours": secret}
                )
            )
        self.assertTrue(result.is_error)
        self.assertNotIn(secret, result.content[0].text)
        run.assert_not_called()

    def test_accepted_backup_dispatches_only_fixed_function_and_approved_targets(self):
        with configured_targets(), patch.object(
            mcp_server, "invoke_ops_tool", return_value="{}"
        ) as invoke:
            mcp_server.backup_ops_database(
                database="LabDb",
                backup_type="Differential",
                sql_instance="lab-sql",
                approval=mcp_server.BackupApproval(approved=True),
            )
        invoke.assert_called_once_with(
            "Backup-OpsDatabase",
            {
                "sql_instance": "lab-sql",
                "database": "LabDb",
                "backup_type": "Differential",
            },
            approval_granted=True,
        )

    def test_fixed_dispatch_uses_static_bridge_and_canonical_config_targets(self):
        completed = SimpleNamespace(returncode=0, stdout='[{"DatabaseName":"LabDb"}]', stderr="")
        with (
            configured_targets(),
            patch.object(mcp_server.shutil, "which", return_value="C:\\PowerShell\\pwsh.exe"),
            patch.object(mcp_server.subprocess, "run", return_value=completed) as run,
        ):
            output = mcp_server.invoke_ops_tool(
                "Get-OpsDatabaseInventory",
                {"sql_instance": "lab-sql", "databases": ["labdb"]},
            )

        self.assertEqual(json.loads(output)[0]["DatabaseName"], "LabDb")
        command = run.call_args.args[0]
        self.assertIn(str(mcp_server.BRIDGE_PATH), command)
        self.assertNotIn("-Command", command)
        self.assertFalse(run.call_args.kwargs["shell"])
        payload = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(payload["ToolName"], "Get-OpsDatabaseInventory")
        self.assertEqual(payload["Parameters"]["Database"], ["LabDb"])
        self.assertEqual(payload["Parameters"]["SqlInstance"], "LAB-SQL")

    def test_errors_and_logs_do_not_expose_secrets_or_internal_paths(self):
        secret = "Password=TopSecret123"
        private_path = "C:\\private\\sql\\data.mdf"
        completed = SimpleNamespace(returncode=1, stdout="", stderr=f"{secret} {private_path}")
        with configured_targets(), patch.object(
            mcp_server.subprocess, "run", return_value=completed
        ):
            with self.assertLogs(mcp_server.logger, level="INFO") as captured:
                with self.assertRaises(RuntimeError) as caught:
                    mcp_server.invoke_ops_tool(
                        "Get-OpsDatabaseInventory", {"sql_instance": "LAB-SQL"}
                    )
        combined = str(caught.exception) + "\n" + "\n".join(captured.output)
        self.assertNotIn(secret, combined)
        self.assertNotIn(private_path, combined)
        self.assertNotIn("stderr", str(caught.exception))

    def test_sensitive_free_text_and_path_fields_are_removed_from_responses(self):
        result = {
            "StatementText": "select 'Password=TopSecret123'",
            "BlockedQuery": "select 'pwd=TopSecret123'",
            "ErrorMessage": "Token=TopSecret123",
            "PhysicalPath": "C:\\private\\sql\\data.mdf",
            "ReportPath": "C:\\private\\reports\\health.html",
            "BackupDirectory": "C:\\private\\backup",
            "Status": "Warning",
            "DatabaseName": "LabDb",
            "Message": "client_secret=TopSecret123",
        }
        encoded = json.dumps(mcp_server._sanitize_ops_result("Get-OpsLongRunningQueries", result))
        for sensitive_value in ("TopSecret123", "data.mdf", "health.html", "private\\backup"):
            self.assertNotIn(sensitive_value, encoded)
        self.assertIn('"Status": "Warning"', encoded)
        self.assertIn('"DatabaseName": "LabDb"', encoded)
        self.assertIn("[REDACTED]", encoded)

    def test_fixed_bridge_has_no_dynamic_command_or_sql_execution(self):
        source = mcp_server.BRIDGE_PATH.read_text(encoding="utf-8")
        self.assertIn("switch ($toolName)", source)
        self.assertNotIn("Invoke-Expression", source)
        self.assertNotIn("Invoke-Sqlcmd", source)
        self.assertNotIn("Start-Process", source)

    def test_powershell_bridge_rejects_unknown_capability_before_module_or_sql(self):
        pwsh = shutil.which("pwsh")
        self.assertIsNotNone(pwsh, "PowerShell 7 is required by project setup.")
        request = json.dumps(
            {"ToolName": "Invoke-Expression", "Parameters": {}, "ApprovalGranted": False}
        )
        result = subprocess.run(
            [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(mcp_server.BRIDGE_PATH)],
            input=request,
            text=True,
            encoding="utf-8",
            capture_output=True,
            shell=False,
            timeout=15,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("not exposed", result.stderr)

    def test_stdio_server_starts_and_exits_cleanly_on_eof_without_sql(self):
        result = subprocess.run(
            [sys.executable, str(MCP_SERVER_PATH)],
            input="",
            text=True,
            encoding="utf-8",
            capture_output=True,
            shell=False,
            timeout=15,
            cwd=PROJECT_ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
