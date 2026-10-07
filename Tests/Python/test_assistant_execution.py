"""Execution tests use the real local RAG and a fake official-MCP-shaped session."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from assistant import orchestrate_request
from assistant.executor import execute_plan


ROOT = Path(__file__).resolve().parents[2]
MCP_SERVER_PATH = ROOT / "mcp-server" / "server.py"
_MCP_SPEC = importlib.util.spec_from_file_location("phase12_existing_mcp_server", MCP_SERVER_PATH)
assert _MCP_SPEC is not None and _MCP_SPEC.loader is not None
_MCP_SERVER = importlib.util.module_from_spec(_MCP_SPEC)
sys.modules[_MCP_SPEC.name] = _MCP_SERVER
_MCP_SPEC.loader.exec_module(_MCP_SERVER)


def discovered_tools():
    return asyncio.run(_MCP_SERVER.server.list_tools())


class FakeMCPClientSession:
    """Fake implementing the official ClientSession list_tools/call_tool surface."""

    def __init__(self, tools, result=None):
        self.tools = tools
        self.result = result or SimpleNamespace(
            is_error=False,
            content=[SimpleNamespace(type="text", text=json.dumps({"status": "ok", "count": 2}))],
        )
        self.calls = []

    async def list_tools(self):
        return SimpleNamespace(tools=self.tools)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


class AssistantExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = discovered_tools()

    def test_rag_only_execution_uses_local_retriever(self):
        plan = orchestrate_request("What is a transaction log backup?", mcp_catalog=self.tools)
        response = asyncio.run(execute_plan(plan, query="What is a transaction log backup?"))
        self.assertEqual(response.status, "SUCCESS")
        self.assertTrue(response.knowledge_sources)
        self.assertEqual(response.knowledge_sources[0]["filename"], "sample-backup-recovery.md")
        self.assertEqual(response.mcp_results, ())
        self.assertFalse(response.dispatch_performed)

    def test_read_only_mcp_execution_uses_official_session_methods(self):
        plan = orchestrate_request("What databases are on my SQL Server?", mcp_catalog=self.tools)
        session = FakeMCPClientSession(self.tools)
        response = asyncio.run(execute_plan(plan, query="What databases are on my SQL Server?", mcp_session=session))
        self.assertEqual(response.status, "SUCCESS")
        self.assertEqual(session.calls, [("get_ops_database_inventory", {})])
        self.assertEqual(response.mcp_results[0]["tool"], "get_ops_database_inventory")
        self.assertEqual(response.mcp_results[0]["source"], "existing_mcp_server")
        self.assertTrue(response.dispatch_performed)

    def test_combined_execution_returns_rag_and_read_only_mcp_result(self):
        query = "Why is blocking happening, and do I currently have blocking sessions?"
        plan = orchestrate_request(query, mcp_catalog=self.tools)
        session = FakeMCPClientSession(self.tools)
        response = asyncio.run(execute_plan(plan, query=query, mcp_session=session))
        self.assertEqual(response.status, "SUCCESS")
        self.assertEqual(response.knowledge_sources[0]["filename"], "sample-blocking.md")
        self.assertEqual(response.knowledge_sources[0]["evidence_type"], "REFERENCE_KNOWLEDGE")
        self.assertEqual(response.mcp_results[0]["tool"], "get_ops_blocking_sessions")
        self.assertEqual(response.mcp_results[0]["data"]["count"], 2)

    def test_rejected_arbitrary_sql_does_not_call_mcp(self):
        plan = orchestrate_request("Run SELECT * FROM sys.databases.", mcp_catalog=self.tools)
        session = FakeMCPClientSession(self.tools)
        response = asyncio.run(execute_plan(plan, query="Run SELECT * FROM sys.databases.", mcp_session=session))
        self.assertEqual(response.status, "REJECTED")
        self.assertEqual(session.calls, [])

    def test_rejected_shell_request_does_not_call_mcp(self):
        plan = orchestrate_request("Run PowerShell Get-Process.", mcp_catalog=self.tools)
        session = FakeMCPClientSession(self.tools)
        response = asyncio.run(execute_plan(plan, query="Run PowerShell Get-Process.", mcp_session=session))
        self.assertEqual(response.status, "REJECTED")
        self.assertEqual(session.calls, [])

    def test_unapproved_backup_is_rejected_without_mcp_call(self):
        config_path = ROOT / "Config" / "config.development.json"
        query = "Back up DatabaseA."
        with patch.dict(os.environ, {"DBA_AUTOMATION_CONFIG": str(config_path)}):
            plan = orchestrate_request(
                query,
                mcp_catalog=self.tools,
                database_validator=_MCP_SERVER._canonical_database,
            )
        session = FakeMCPClientSession(self.tools)
        response = asyncio.run(execute_plan(plan, query=query, mcp_session=session))
        self.assertEqual(response.status, "REJECTED")
        self.assertEqual(session.calls, [])

    def test_approved_backup_returns_needs_approval_without_dispatch(self):
        config_path = ROOT / "Config" / "config.development.json"
        query = "Back up PracticeDB with a Full backup."
        with patch.dict(os.environ, {"DBA_AUTOMATION_CONFIG": str(config_path)}):
            plan = orchestrate_request(
                query,
                mcp_catalog=self.tools,
                database_validator=_MCP_SERVER._canonical_database,
            )
        session = FakeMCPClientSession(self.tools)
        response = asyncio.run(execute_plan(plan, query=query, mcp_session=session))
        self.assertEqual(response.status, "NEEDS_APPROVAL")
        self.assertEqual(session.calls, [])
        self.assertFalse(response.dispatch_performed)
        self.assertEqual(response.planned_actions[0]["tool"], "backup_ops_database")
        self.assertTrue(response.planned_actions[0]["requires_approval"])

    def test_arbitrary_mcp_tool_names_are_rejected(self):
        plan = orchestrate_request("What databases are on my SQL Server?", mcp_catalog=self.tools)
        plan = plan.__class__(
            intent=plan.intent,
            classification=plan.classification,
            status=plan.status,
            explanation=plan.explanation,
            mcp_actions=(plan.mcp_actions[0].__class__("run_anything", "READ_ONLY"),),
        )
        session = FakeMCPClientSession(self.tools)
        response = asyncio.run(execute_plan(plan, query="What databases are on my SQL Server?", mcp_session=session))
        self.assertEqual(response.status, "REJECTED")
        self.assertEqual(session.calls, [])

    def test_no_direct_powershell_or_sql_execution_code_exists(self):
        source = (ROOT / "assistant" / "executor.py").read_text(encoding="utf-8").casefold()
        for forbidden in (
            "subprocess.run",
            "popen(",
            "invoke-expression",
            "pyodbc.connect",
            "cursor.execute",
            "sqlcmd",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_rag_attribution_is_preserved_without_absolute_paths(self):
        query = "What is a transaction log backup?"
        plan = orchestrate_request(query, mcp_catalog=self.tools)
        response = asyncio.run(execute_plan(plan, query=query))
        source = response.knowledge_sources[0]
        self.assertEqual(source["evidence_type"], "REFERENCE_KNOWLEDGE")
        self.assertEqual(source["source"], "sample-backup-recovery.md")
        self.assertIn("SQL Server Backup and Recovery Concepts", source["attribution"])
        self.assertNotIn(str(ROOT), source["attribution"])

    def test_mcp_attribution_is_preserved_and_secrets_paths_are_removed(self):
        plan = orchestrate_request("What databases are on my SQL Server?", mcp_catalog=self.tools)
        result = SimpleNamespace(
            is_error=False,
            content=[SimpleNamespace(
                type="text",
                text=json.dumps({
                    "status": "ok",
                    "report_path": r"C:\private\report.html",
                    "note": r"Token=not-returned; diagnostic at C:\private\report.html",
                    "count": 3,
                }),
            )],
        )
        session = FakeMCPClientSession(self.tools, result=result)
        response = asyncio.run(execute_plan(plan, query="What databases are on my SQL Server?", mcp_session=session))
        attributed = response.mcp_results[0]
        self.assertEqual(attributed["tool"], "get_ops_database_inventory")
        self.assertEqual(attributed["source"], "existing_mcp_server")
        self.assertNotIn("report_path", attributed["data"])
        self.assertNotIn("not-returned", json.dumps(attributed))
        self.assertNotIn(r"C:\private", json.dumps(attributed))


if __name__ == "__main__":
    unittest.main()
