"""MCP server exposing only the project's fixed SQL Server Ops functions."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context, Elicit, Resolve
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from assistant.environment_config import load_automation_configuration

BRIDGE_PATH = Path(__file__).resolve().with_name("invoke_ops_tool.ps1")
DEFAULT_TIMEOUT_SECONDS = 300
BACKUP_TIMEOUT_SECONDS = 3600
PROTECTED_DATABASES = {"master", "model", "msdb", "tempdb"}
SENSITIVE_RESULT_FIELDS = {
    "statementtext",
    "blockedquery",
    "blockerquery",
    "errormessage",
    "physicalpath",
    "reportpath",
    "backupfiles",
    "backupdirectory",
}
SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:password|pwd|token|secret|client_secret|access_token|connectionstring)\s*[:=]"
)

logger = logging.getLogger("dba_mcp")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)sZ %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stderr,
)
logging.Formatter.converter = time.gmtime

OPS_PARAMETER_ALLOWLIST: dict[str, set[str]] = {
    "Get-OpsDatabaseInventory": {"SqlInstance", "Database"},
    "Get-OpsDatabaseHealth": {"SqlInstance", "Database"},
    "Get-OpsDatabaseSize": {"SqlInstance", "Database", "Detailed"},
    "Get-OpsBackupStatus": {"SqlInstance", "Database", "NotBackedUpHours"},
    "Get-OpsDiskSpace": {"SqlInstance", "WarningPercent"},
    "Get-OpsFailedJobs": {"SqlInstance", "SinceHours"},
    "Get-OpsBlockingSessions": {"SqlInstance"},
    "Get-OpsLongRunningQueries": {"SqlInstance", "ThresholdSeconds"},
    # OutputPath is intentionally omitted; the Ops function uses its configured Reports directory.
    "Get-OpsServerHealthReport": {"SqlInstance"},
    "Backup-OpsDatabase": {"SqlInstance", "Database", "BackupType"},
}
INPUT_TO_POWERSHELL_PARAMETER = {
    "sql_instance": "SqlInstance",
    "databases": "Database",
    "database": "Database",
    "detailed": "Detailed",
    "not_backed_up_hours": "NotBackedUpHours",
    "warning_percent": "WarningPercent",
    "since_hours": "SinceHours",
    "threshold_seconds": "ThresholdSeconds",
    "backup_type": "BackupType",
}
MCP_TOOL_TO_OPS = {
    "get_ops_database_inventory": "Get-OpsDatabaseInventory",
    "get_ops_database_health": "Get-OpsDatabaseHealth",
    "get_ops_database_size": "Get-OpsDatabaseSize",
    "get_ops_backup_status": "Get-OpsBackupStatus",
    "get_ops_disk_space": "Get-OpsDiskSpace",
    "get_ops_failed_jobs": "Get-OpsFailedJobs",
    "get_ops_blocking_sessions": "Get-OpsBlockingSessions",
    "get_ops_long_running_queries": "Get-OpsLongRunningQueries",
    "get_ops_server_health_report": "Get-OpsServerHealthReport",
    "backup_ops_database": "Backup-OpsDatabase",
}


class BackupApproval(BaseModel):
    approved: bool = Field(
        description="Explicitly approve the named SQL Server database backup."
    )


class SanitizingMCPServer(MCPServer):
    """Suppress SDK validation/handler exception details at the MCP response boundary."""

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        started = time.perf_counter()
        try:
            return await super().call_tool(name, arguments, context)
        except Exception as exc:
            # Pydantic errors can include rejected input values. Never return those values,
            # raw SQL/provider errors, credential fragments, or filesystem paths to a caller.
            if exc.__cause__ is not None and type(exc.__cause__).__name__ == "ValidationError":
                allowed_fields = set(INPUT_TO_POWERSHELL_PARAMETER)
                provided_fields = [key for key in arguments if key in allowed_fields]
                _audit_tool_call(
                    "MCPArgumentValidation",
                    "Error",
                    round((time.perf_counter() - started) * 1000, 2),
                    provided_fields,
                )
            elif name not in MCP_TOOL_TO_OPS:
                _audit_tool_call(
                    "UnapprovedMCPTool",
                    "Error",
                    round((time.perf_counter() - started) * 1000, 2),
                    [],
                )
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text="Tool call failed validation or execution. Details were withheld to protect sensitive data.",
                    )
                ],
                is_error=True,
            )


def _load_target_configuration() -> tuple[list[str], str, list[str], list[str], dict[str, Any]]:
    """Read target and backup policy only from an explicitly selected environment config."""
    try:
        config = load_automation_configuration(project_root=PROJECT_ROOT)
        instances = config["ApprovedInstances"]
        default_instance = config["DefaultInstance"]
        databases = config["ApprovedDatabases"]
        protected = config["ProtectedDatabases"]
        return instances, default_instance, databases, protected, config["BackupPolicy"]
    except Exception:
        # Do not echo a config path or JSON contents to MCP clients or MCP logs.
        raise RuntimeError("DBA automation configuration is invalid or unavailable.") from None


def _canonical_instance(instance: str | None) -> str:
    instances, default_instance, _, _, _ = _load_target_configuration()
    requested = instance or default_instance
    if not isinstance(requested, str) or not requested.strip():
        raise ValueError("SQL Server instance is not allowlisted.")
    approved = next((item for item in instances if item.casefold() == requested.casefold()), None)
    if approved is None:
        raise ValueError("SQL Server instance is not allowlisted.")
    return approved


def _canonical_database(database: str) -> str:
    _, _, databases, protected, _ = _load_target_configuration()
    if not isinstance(database, str) or not database.strip():
        raise ValueError("Database is not approved.")
    if database.casefold() in PROTECTED_DATABASES or database.casefold() in {
        item.casefold() for item in protected
    }:
        raise ValueError("System databases are protected.")
    approved = next((item for item in databases if item.casefold() == database.casefold()), None)
    if approved is None:
        raise ValueError("Database is not approved.")
    return approved


def _clean_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Validate only known, bounded Ops inputs and map tool schema names to PS names."""
    cleaned: dict[str, Any] = {}
    for key, value in parameters.items():
        if key not in INPUT_TO_POWERSHELL_PARAMETER:
            raise ValueError("Unsupported Ops parameter.")
        if value is None:
            continue
        ps_parameter = INPUT_TO_POWERSHELL_PARAMETER[key]
        if ps_parameter in cleaned:
            raise ValueError("A database target was supplied more than once.")
        if key == "sql_instance" and (not isinstance(value, str) or not 1 <= len(value) <= 256):
            raise ValueError("sql_instance must be a non-empty instance name of at most 256 characters.")
        if key == "database":
            if not isinstance(value, str) or not 1 <= len(value) <= 128:
                raise ValueError("database must be a non-empty name of at most 128 characters.")
        if key == "databases":
            if not isinstance(value, list) or not 1 <= len(value) <= 100:
                raise ValueError("databases must contain between 1 and 100 database names.")
            if any(not isinstance(name, str) or not 1 <= len(name) <= 128 for name in value):
                raise ValueError("Each database name must be a non-empty string of at most 128 characters.")
        if key == "backup_type" and value not in {"Full", "Differential", "Log"}:
            raise ValueError("backup_type must be Full, Differential, or Log.")
        if isinstance(value, str) and SECRET_ASSIGNMENT.search(value):
            raise ValueError("Credential-like values are not accepted by Ops tools.")
        cleaned[ps_parameter] = value
    return cleaned


def _validate_configured_targets(ops_function: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize target names against project configuration before PowerShell can log them."""
    validated = dict(parameters)
    validated["SqlInstance"] = _canonical_instance(validated.get("SqlInstance"))
    database_functions = {
        "Get-OpsDatabaseInventory",
        "Get-OpsDatabaseHealth",
        "Get-OpsDatabaseSize",
        "Get-OpsBackupStatus",
        "Backup-OpsDatabase",
    }
    if ops_function not in database_functions:
        return validated
    if "Database" not in validated:
        return validated
    is_backup = ops_function == "Backup-OpsDatabase"
    if is_backup:
        _, _, _, _, backup_policy = _load_target_configuration()
        if not backup_policy["Enabled"]:
            raise PermissionError("Backup is disabled by the selected environment configuration.")
        if validated.get("BackupType") not in backup_policy["AllowedTypes"]:
            raise ValueError("Backup type is disabled by the selected environment configuration.")
        validated["Database"] = _canonical_database(validated["Database"])
    else:
        names = validated["Database"]
        if not isinstance(names, list):
            raise ValueError("Database targets are invalid.")
        validated["Database"] = [_canonical_database(name) for name in names]
    return validated


def _sanitize_ops_result(ops_function: str, value: Any) -> Any:
    """Drop free-text SQL/error/path fields before any Ops result crosses the MCP boundary."""
    if isinstance(value, list):
        return [_sanitize_ops_result(ops_function, item) for item in value]
    if isinstance(value, dict):
        return {
            key: _sanitize_ops_result(ops_function, item)
            for key, item in value.items()
            if str(key).casefold() not in SENSITIVE_RESULT_FIELDS
        }
    if isinstance(value, str) and SECRET_ASSIGNMENT.search(value):
        return "[REDACTED]"
    return value


def _powershell_executable() -> str:
    executable = shutil.which("pwsh")
    if not executable:
        raise RuntimeError("PowerShell 7 (pwsh) was not found on PATH.")
    return executable


def _audit_tool_call(
    ops_function: str, status: str, duration_ms: float, parameter_names: list[str]
) -> None:
    # Values are intentionally omitted; parameter names are allowlisted and contain no credentials.
    logger.info(
        json.dumps(
            {
                "timestamp_utc": datetime.now(UTC).isoformat(),
                "tool": ops_function,
                "parameters": {"provided": sorted(parameter_names)},
                "status": status,
                "duration_ms": duration_ms,
            },
            separators=(",", ":"),
        )
    )


def invoke_ops_tool(
    ops_function: str,
    parameters: dict[str, Any],
    *,
    approval_granted: bool = False,
) -> str:
    """Call one fixed Ops function through a fixed PowerShell bridge, never a command string from a caller."""
    started = time.perf_counter()
    status = "Error"
    audit_name = ops_function if ops_function in OPS_PARAMETER_ALLOWLIST else "UnapprovedOpsFunction"
    allowed_names = OPS_PARAMETER_ALLOWLIST.get(ops_function, set())
    supplied_names = [
        name
        for name, ps_parameter in INPUT_TO_POWERSHELL_PARAMETER.items()
        if name in parameters and ps_parameter in allowed_names
    ]
    try:
        if ops_function not in OPS_PARAMETER_ALLOWLIST:
            raise ValueError("The requested Ops function is not exposed.")
        is_backup = ops_function == "Backup-OpsDatabase"
        if is_backup != approval_granted:
            raise PermissionError("Backup requires explicit approval; read-only operations do not accept approval metadata.")

        if not isinstance(parameters, dict):
            raise ValueError("Ops parameters must be an object.")
        safe_parameters = _clean_parameters(parameters)
        allowed = OPS_PARAMETER_ALLOWLIST[ops_function]
        if set(safe_parameters) - allowed:
            raise ValueError("The requested parameters are not allowed for this Ops function.")
        if is_backup and not {"Database", "BackupType"}.issubset(safe_parameters):
            raise ValueError("Backup requires a database and backup type.")
        safe_parameters = _validate_configured_targets(ops_function, safe_parameters)

        request = {
            "ToolName": ops_function,
            "Parameters": safe_parameters,
            "ApprovalGranted": approval_granted,
        }
        command = [
            _powershell_executable(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(BRIDGE_PATH),
        ]
        timeout = BACKUP_TIMEOUT_SECONDS if is_backup else DEFAULT_TIMEOUT_SECONDS
        completed = subprocess.run(
            command,
            input=json.dumps(request, separators=(",", ":")),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            timeout=timeout,
            shell=False,
            cwd=PROJECT_ROOT,
        )
        if completed.returncode != 0:
            # Do not forward raw PowerShell/SQL/provider errors or connection details to the MCP caller.
            raise RuntimeError("The Ops operation failed. Review the sanitized project log.")
        try:
            result = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError):
            raise RuntimeError("The approved Ops function returned an invalid result.") from None
        result = _sanitize_ops_result(ops_function, result)
        status = "Success"
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    except subprocess.TimeoutExpired:
        raise RuntimeError("The Ops function exceeded its fixed execution timeout.") from None
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        _audit_tool_call(audit_name, status, duration_ms, supplied_names)


server = SanitizingMCPServer(
    name="SQL Server DBA Automation",
    version="0.1.0",
    instructions=(
        "Use only the listed predefined Ops tools. Database and instance access is validated by the PowerShell layer. "
        "The backup tool requires explicit user approval through MCP elicitation."
    ),
    log_level="WARNING",
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
REPORT_WRITE = ToolAnnotations(
    title="Generate configured health report",
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
)
BACKUP_APPROVAL = ToolAnnotations(
    title="NEEDS_APPROVAL: Create database backup",
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
)


@server.tool(annotations=READ_ONLY, structured_output=False)
def get_ops_database_inventory(
    sql_instance: str | None = None, databases: list[str] | None = None
) -> str:
    """READ_ONLY: Read metadata for configured approved databases on an allowlisted SQL Server."""
    return invoke_ops_tool(
        "Get-OpsDatabaseInventory",
        {"sql_instance": sql_instance, "databases": databases},
    )


@server.tool(annotations=READ_ONLY, structured_output=False)
def get_ops_database_health(
    sql_instance: str | None = None, databases: list[str] | None = None
) -> str:
    """READ_ONLY: Read health metadata for configured approved databases; does not run CHECKDB."""
    return invoke_ops_tool(
        "Get-OpsDatabaseHealth", {"sql_instance": sql_instance, "databases": databases}
    )


@server.tool(annotations=READ_ONLY, structured_output=False)
def get_ops_database_size(
    sql_instance: str | None = None,
    databases: list[str] | None = None,
    detailed: bool = False,
) -> str:
    """READ_ONLY: Read size and file usage for configured approved databases."""
    return invoke_ops_tool(
        "Get-OpsDatabaseSize",
        {"sql_instance": sql_instance, "databases": databases, "detailed": detailed},
    )


@server.tool(annotations=READ_ONLY, structured_output=False)
def get_ops_backup_status(
    sql_instance: str | None = None,
    databases: list[str] | None = None,
    not_backed_up_hours: int | None = None,
) -> str:
    """READ_ONLY: Read backup recency for configured approved databases."""
    return invoke_ops_tool(
        "Get-OpsBackupStatus",
        {
            "sql_instance": sql_instance,
            "databases": databases,
            "not_backed_up_hours": not_backed_up_hours,
        },
    )


@server.tool(annotations=READ_ONLY, structured_output=False)
def get_ops_disk_space(
    sql_instance: str | None = None, warning_percent: float | None = None
) -> str:
    """READ_ONLY: Read SQL-visible volume space for volumes containing approved database files."""
    return invoke_ops_tool(
        "Get-OpsDiskSpace",
        {"sql_instance": sql_instance, "warning_percent": warning_percent},
    )


@server.tool(annotations=READ_ONLY, structured_output=False)
def get_ops_failed_jobs(
    sql_instance: str | None = None, since_hours: int | None = None
) -> str:
    """READ_ONLY: Read failed SQL Agent job summary metadata from msdb."""
    return invoke_ops_tool(
        "Get-OpsFailedJobs", {"sql_instance": sql_instance, "since_hours": since_hours}
    )


@server.tool(annotations=READ_ONLY, structured_output=False)
def get_ops_blocking_sessions(sql_instance: str | None = None) -> str:
    """READ_ONLY: Read blocking session diagnostics filtered to configured approved databases."""
    return invoke_ops_tool("Get-OpsBlockingSessions", {"sql_instance": sql_instance})


@server.tool(annotations=READ_ONLY, structured_output=False)
def get_ops_long_running_queries(
    sql_instance: str | None = None, threshold_seconds: int | None = None
) -> str:
    """READ_ONLY: Read active long-running query diagnostics for configured approved databases."""
    return invoke_ops_tool(
        "Get-OpsLongRunningQueries",
        {"sql_instance": sql_instance, "threshold_seconds": threshold_seconds},
    )


@server.tool(annotations=REPORT_WRITE, structured_output=False)
def get_ops_server_health_report(sql_instance: str | None = None) -> str:
    """READ_ONLY_SQL / WRITES_REPORT: Generate HTML in the configured Reports directory."""
    return invoke_ops_tool("Get-OpsServerHealthReport", {"sql_instance": sql_instance})


def _request_backup_approval(
    sql_instance: str | None,
    database: str,
    backup_type: Literal["Full", "Differential", "Log"],
    ctx: Context,
) -> Elicit[BackupApproval]:
    target_instance = _canonical_instance(sql_instance)
    target_database = _canonical_database(database)
    return Elicit(
        message=(
            f"NEEDS_APPROVAL: Approve a {backup_type} backup of database '{target_database}' "
            f"on {target_instance}? The destination is fixed by project configuration."
        ),
        schema=BackupApproval,
    )


@server.tool(annotations=BACKUP_APPROVAL, structured_output=False)
def backup_ops_database(
    database: str,
    backup_type: Literal["Full", "Differential", "Log"],
    sql_instance: str | None = None,
    *,
    approval: Annotated[BackupApproval, Resolve(_request_backup_approval)],
) -> str:
    """NEEDS_APPROVAL: Create a configured-destination backup only after explicit MCP user elicitation approval."""
    if approval.approved is not True:
        _audit_tool_call(
            "Backup-OpsDatabase",
            "NEEDS_APPROVAL",
            0,
            ["database", "backup_type"] + (["sql_instance"] if sql_instance else []),
        )
        return json.dumps(
            {"status": "NEEDS_APPROVAL", "backup_started": False}, separators=(",", ":")
        )
    return invoke_ops_tool(
        "Backup-OpsDatabase",
        {
            "sql_instance": sql_instance,
            "database": database,
            "backup_type": backup_type,
        },
        approval_granted=True,
    )


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
