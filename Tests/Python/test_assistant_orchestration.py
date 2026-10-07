"""Offline orchestration tests using the existing MCP catalog and RAG package."""

import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from assistant import orchestrate_request


ROOT = Path(__file__).resolve().parents[2]
MCP_SERVER_PATH = ROOT / "mcp-server" / "server.py"
_MCP_SPEC = importlib.util.spec_from_file_location("phase11_existing_mcp_server", MCP_SERVER_PATH)
assert _MCP_SPEC is not None and _MCP_SPEC.loader is not None
_MCP_SERVER = importlib.util.module_from_spec(_MCP_SPEC)
sys.modules[_MCP_SPEC.name] = _MCP_SERVER
_MCP_SPEC.loader.exec_module(_MCP_SERVER)


def existing_tool_catalog():
    return asyncio.run(_MCP_SERVER.server.list_tools())


class AssistantOrchestrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = existing_tool_catalog()

    def test_knowledge_question_uses_rag_only(self):
        plan = orchestrate_request("What is a transaction log backup?", mcp_catalog=self.catalog)
        self.assertEqual(plan.intent, "knowledge")
        self.assertEqual(plan.classification, "REFERENCE_ONLY")
        self.assertEqual(plan.mcp_actions, ())
        self.assertTrue(plan.knowledge_results)
        self.assertEqual(plan.knowledge_results[0].source_document.filename, "sample-backup-recovery.md")
        self.assertFalse(plan.dispatch_performed)

    def test_live_inventory_question_selects_inventory_only(self):
        plan = orchestrate_request("What databases are on my SQL Server?", mcp_catalog=self.catalog)
        self.assertEqual(plan.intent, "live_dba")
        self.assertEqual(plan.classification, "READ_ONLY")
        self.assertEqual([action.tool_name for action in plan.mcp_actions], ["get_ops_database_inventory"])
        self.assertEqual(plan.knowledge_results, ())
        self.assertFalse(plan.dispatch_performed)

    def test_blocking_question_combines_reference_and_live_tool_plan(self):
        plan = orchestrate_request(
            "Why is blocking happening, and do I currently have blocking sessions?",
            mcp_catalog=self.catalog,
        )
        self.assertEqual(plan.intent, "combined")
        self.assertEqual(plan.classification, "READ_ONLY")
        self.assertEqual([action.tool_name for action in plan.mcp_actions], ["get_ops_blocking_sessions"])
        self.assertEqual(plan.knowledge_results[0].source_document.filename, "sample-blocking.md")

    def test_backup_problem_question_combines_status_tool_and_guidance(self):
        plan = orchestrate_request(
            "Which databases have backup problems and what should I check?",
            mcp_catalog=self.catalog,
        )
        self.assertEqual(plan.intent, "combined")
        self.assertEqual([action.tool_name for action in plan.mcp_actions], ["get_ops_backup_status"])
        self.assertEqual(plan.knowledge_results[0].source_document.filename, "sample-backup-recovery.md")
        self.assertTrue(all(not action.requires_approval for action in plan.mcp_actions))

    def test_unapproved_database_backup_is_rejected_before_approval(self):
        config_path = ROOT / "Config" / "config.development.json"
        with patch.dict(os.environ, {"DBA_AUTOMATION_CONFIG": str(config_path)}):
            plan = orchestrate_request(
                "Back up DatabaseA.",
                mcp_catalog=self.catalog,
                database_validator=_MCP_SERVER._canonical_database,
            )
        self.assertEqual(plan.classification, "REJECT")
        self.assertEqual(plan.mcp_actions, ())

    def test_approved_database_backup_is_only_a_gated_plan(self):
        config_path = ROOT / "Config" / "config.development.json"
        with patch.dict(os.environ, {"DBA_AUTOMATION_CONFIG": str(config_path)}):
            plan = orchestrate_request(
                "Back up PracticeDB with a Full backup.",
                mcp_catalog=self.catalog,
                database_validator=_MCP_SERVER._canonical_database,
            )
        self.assertEqual(plan.classification, "NEEDS_APPROVAL")
        self.assertEqual(plan.status, "AWAITING_APPROVAL")
        action = plan.mcp_actions[0]
        self.assertEqual(action.tool_name, "backup_ops_database")
        self.assertTrue(action.requires_approval)
        self.assertEqual(action.arguments, {"database": "PracticeDB", "backup_type": "Full"})
        self.assertFalse(plan.dispatch_performed)

    def test_raw_sql_request_is_rejected(self):
        plan = orchestrate_request("Run SELECT * FROM sys.databases.", mcp_catalog=self.catalog)
        self.assertEqual(plan.classification, "REJECT")
        self.assertEqual(plan.mcp_actions, ())

    def test_shell_request_is_rejected(self):
        plan = orchestrate_request("Run PowerShell Get-Process.", mcp_catalog=self.catalog)
        self.assertEqual(plan.classification, "REJECT")
        self.assertEqual(plan.mcp_actions, ())

    def test_response_separates_reference_from_pending_live_evidence(self):
        plan = orchestrate_request(
            "Why is blocking happening, and do I currently have blocking sessions?",
            mcp_catalog=self.catalog,
        )
        response = plan.to_response()
        self.assertEqual(response["knowledge_sources"][0]["evidence_type"], "REFERENCE_KNOWLEDGE")
        self.assertEqual(response["mcp_actions"][0]["evidence_type"], "LIVE_SQL_TOOL_PENDING")
        self.assertFalse(response["dispatch_performed"])
        self.assertNotIn(str(ROOT), response["knowledge_sources"][0]["attribution"])

    def test_report_route_preserves_file_write_classification(self):
        plan = orchestrate_request("Give me the health status of my SQL Server.", mcp_catalog=self.catalog)
        self.assertEqual(plan.classification, "WRITES_REPORT")
        self.assertEqual(plan.mcp_actions[0].behavior, "READ_ONLY_SQL / WRITES_REPORT")
        self.assertTrue(plan.mcp_actions[0].requires_approval)
        self.assertFalse(plan.dispatch_performed)

    def test_missing_mcp_capability_fails_closed(self):
        plan = orchestrate_request("What databases are on my SQL Server?", mcp_catalog=[])
        self.assertEqual(plan.classification, "REJECT")
        self.assertEqual(plan.mcp_actions, ())


if __name__ == "__main__":
    unittest.main()
