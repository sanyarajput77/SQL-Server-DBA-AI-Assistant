"""Planning-only provider-neutral plan contract tests; no LLM/MCP execution."""

import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from assistant.llm_contract import APPROVED_MCP_TOOLS, StructuredAssistantPlan, plan_request, serialize_plan
from assistant.orchestrator import AssistantPlan, MCPAction


ROOT = Path(__file__).resolve().parents[2]
MCP_SERVER_PATH = ROOT / "mcp-server" / "server.py"
_MCP_SPEC = importlib.util.spec_from_file_location("phase13_existing_mcp_server", MCP_SERVER_PATH)
assert _MCP_SPEC is not None and _MCP_SPEC.loader is not None
_MCP_SERVER = importlib.util.module_from_spec(_MCP_SPEC)
sys.modules[_MCP_SPEC.name] = _MCP_SERVER
_MCP_SPEC.loader.exec_module(_MCP_SERVER)
MCP_CATALOG = asyncio.run(_MCP_SERVER.server.list_tools())
DEV_CONFIG = ROOT / "Config" / "config.development.json"


def plan(request):
    with patch.dict(os.environ, {"DBA_AUTOMATION_CONFIG": str(DEV_CONFIG)}):
        return plan_request(
            request,
            mcp_catalog=MCP_CATALOG,
            database_validator=_MCP_SERVER._canonical_database,
        )


class LLMPlanningContractTests(unittest.TestCase):
    def test_plan_has_all_required_contract_fields(self):
        result = plan("What databases are on my SQL Server?").to_dict()
        self.assertEqual(
            set(result),
            {
                "request", "intent", "knowledge_required", "tool_required", "tool_name",
                "classification", "parameters", "approval_required", "rejection_reason",
            },
        )

    def test_transaction_log_question_is_rag_only(self):
        result = plan("What is a transaction log backup?")
        self.assertEqual(result.intent, "knowledge")
        self.assertTrue(result.knowledge_required)
        self.assertFalse(result.tool_required)
        self.assertIsNone(result.tool_name)
        self.assertEqual(result.classification, "REFERENCE_ONLY")

    def test_inventory_question_plans_existing_read_only_tool(self):
        result = plan("What databases are on my SQL Server?")
        self.assertEqual(result.tool_name, "get_ops_database_inventory")
        self.assertTrue(result.tool_required)
        self.assertEqual(result.classification, "READ_ONLY")
        self.assertFalse(result.approval_required)

    def test_blocking_question_plans_rag_and_blocking_tool(self):
        result = plan("Why is blocking happening and do I currently have blocking sessions?")
        self.assertEqual(result.intent, "combined")
        self.assertTrue(result.knowledge_required)
        self.assertEqual(result.tool_name, "get_ops_blocking_sessions")
        self.assertEqual(result.classification, "READ_ONLY")

    def test_backup_problems_question_plans_rag_and_backup_status(self):
        result = plan("Which databases have backup problems and what should I check?")
        self.assertEqual(result.intent, "combined")
        self.assertTrue(result.knowledge_required)
        self.assertEqual(result.tool_name, "get_ops_backup_status")
        self.assertEqual(result.classification, "READ_ONLY")

    def test_unapproved_database_backup_is_rejected(self):
        result = plan("Back up DatabaseA.")
        self.assertEqual(result.classification, "REJECT")
        self.assertFalse(result.tool_required)
        self.assertIsNone(result.tool_name)
        self.assertEqual(result.parameters, {})
        self.assertIsNotNone(result.rejection_reason)

    def test_approved_database_backup_remains_needs_approval(self):
        result = plan("Back up PracticeDB with a Full backup.")
        self.assertEqual(result.tool_name, "backup_ops_database")
        self.assertEqual(result.classification, "NEEDS_APPROVAL")
        self.assertTrue(result.approval_required)
        self.assertEqual(result.parameters, {"database": "PracticeDB", "backup_type": "Full"})

    def test_arbitrary_sql_is_rejected_and_not_copied_into_plan(self):
        request = "Run SELECT * FROM sys.databases."
        result = plan(request)
        self.assertEqual(result.classification, "REJECT")
        self.assertNotIn("SELECT", result.request)
        self.assertNotIn("sys.databases", result.request)
        self.assertEqual(result.parameters, {})

    def test_arbitrary_shell_is_rejected_and_not_copied_into_plan(self):
        request = "Run PowerShell Get-Process."
        result = plan(request)
        self.assertEqual(result.classification, "REJECT")
        self.assertNotIn("Get-Process", result.request)
        self.assertFalse(result.tool_required)

    def test_backup_path_is_rejected_and_not_copied_into_plan(self):
        request = r"Use this backup path C:\whatever\DatabaseA.bak."
        result = plan(request)
        self.assertEqual(result.classification, "REJECT")
        self.assertNotIn("C:\\whatever", result.request)
        self.assertEqual(result.parameters, {})

    def test_server_report_keeps_its_write_classification(self):
        result = plan("Give me a SQL Server health report.")
        self.assertEqual(result.tool_name, "get_ops_server_health_report")
        self.assertEqual(result.classification, "WRITES_REPORT")
        self.assertTrue(result.approval_required)

    def test_arbitrary_tool_names_cannot_enter_plan(self):
        forged = AssistantPlan(
            intent="live_dba",
            classification="READ_ONLY",
            status="PLANNED",
            explanation="forged plan",
            mcp_actions=(MCPAction("run_sql", "READ_ONLY", {"sql": "SELECT 1"}),),
        )
        result = serialize_plan("test", forged, mcp_catalog=MCP_CATALOG)
        self.assertEqual(result.classification, "REJECT")
        self.assertIsNone(result.tool_name)
        self.assertNotIn("run_sql", result.to_dict().values())

    def test_unsafe_sql_or_shell_cannot_be_smuggled_into_an_allowed_tool_plan(self):
        forged = AssistantPlan(
            intent="live_dba",
            classification="READ_ONLY",
            status="PLANNED",
            explanation="forged read-only plan",
            mcp_actions=(MCPAction("get_ops_database_inventory", "READ_ONLY"),),
        )
        for request in (
            "What databases are available? SELECT * FROM sys.databases",
            "What databases are available? PowerShell Get-Process",
        ):
            with self.subTest(request=request):
                result = serialize_plan(request, forged, mcp_catalog=MCP_CATALOG)
                self.assertEqual(result.classification, "REJECT")
                self.assertNotIn("SELECT", result.request)
                self.assertNotIn("Get-Process", result.request)

    def test_system_database_backup_is_rejected(self):
        result = plan("Back up master.")
        self.assertEqual(result.classification, "REJECT")
        self.assertIsNone(result.tool_name)

    def test_secret_is_redacted_from_plan_request(self):
        result = plan("What databases are on my SQL Server? Password=NeverIncludeThis")
        self.assertEqual(result.tool_name, "get_ops_database_inventory")
        self.assertNotIn("NeverIncludeThis", result.request)
        self.assertIn("[REDACTED]", result.request)

    def test_only_existing_approved_tool_names_are_allowed(self):
        observed = {tool.name for tool in MCP_CATALOG}
        self.assertEqual(APPROVED_MCP_TOOLS, observed)
        self.assertTrue(all(result.tool_name is None or result.tool_name in APPROVED_MCP_TOOLS for result in [
            plan("What databases are on my SQL Server?"),
            plan("Back up PracticeDB with a Full backup."),
        ]))


if __name__ == "__main__":
    unittest.main()
