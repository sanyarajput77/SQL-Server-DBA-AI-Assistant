"""Phase 14B handoff tests using a fake official MCP ClientSession surface."""

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant.application import execute_llm_plan
from assistant.llm_contract import plan_request


MCP_SERVER_PATH = ROOT / "mcp-server" / "server.py"
_SPEC = importlib.util.spec_from_file_location("phase14b_existing_mcp_server", MCP_SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_SERVER = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _SERVER
_SPEC.loader.exec_module(_SERVER)
CATALOG = asyncio.run(_SERVER.server.list_tools())


def validate_database(name):
    if name == "PracticeDB":
        return name
    raise ValueError("not approved")


def proposed(request):
    return plan_request(
        request,
        mcp_catalog=CATALOG,
        database_validator=validate_database,
    ).to_dict()


class FakeClientSession:
    """Fake MCP session records only SDK-shaped list/call operations."""

    def __init__(self, tools=CATALOG):
        self.tools = tools
        self.calls = []

    async def list_tools(self):
        self.calls.append(("list_tools", None))
        return SimpleNamespace(tools=self.tools)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            is_error=False,
            content=[SimpleNamespace(type="text", text=json.dumps({"status": "ok", "tool": name}))],
        )


class Phase14BExecutionTests(unittest.TestCase):
    def test_inventory_plan_reaches_only_inventory_mcp_tool(self):
        session = FakeClientSession()
        result = asyncio.run(execute_llm_plan(
            proposed("What databases are on my SQL Server?"),
            mcp_catalog=CATALOG,
            mcp_session=session,
        ))
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual([call for call in session.calls if call[0] != "list_tools"],
                         [("get_ops_database_inventory", {})])

    def test_blocking_plan_reaches_only_blocking_mcp_tool(self):
        session = FakeClientSession()
        result = asyncio.run(execute_llm_plan(
            proposed("Show me blocking sessions."),
            mcp_catalog=CATALOG,
            mcp_session=session,
        ))
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual([call[0] for call in session.calls if call[0] != "list_tools"],
                         ["get_ops_blocking_sessions"])

    def test_health_report_remains_write_gated_and_does_not_dispatch(self):
        session = FakeClientSession()
        result = asyncio.run(execute_llm_plan(
            proposed("Give me a SQL Server health report."),
            mcp_catalog=CATALOG,
            mcp_session=session,
        ))
        self.assertEqual(result.status, "APPROVAL_REQUIRED")
        self.assertEqual(session.calls, [])

    def test_rag_only_retrieves_knowledge_without_mcp(self):
        session = FakeClientSession()
        result = asyncio.run(execute_llm_plan(
            proposed("What is a transaction log backup?"),
            mcp_catalog=CATALOG,
            mcp_session=session,
        ))
        self.assertEqual(result.status, "SUCCESS")
        self.assertTrue(result.knowledge_sources)
        self.assertEqual(result.mcp_results, ())
        self.assertEqual(session.calls, [])

    def test_combined_plan_preserves_knowledge_and_live_tool_attribution(self):
        session = FakeClientSession()
        result = asyncio.run(execute_llm_plan(
            proposed("Why is blocking happening and do I currently have blocking sessions?"),
            mcp_catalog=CATALOG,
            mcp_session=session,
        ))
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.knowledge_sources[0]["evidence_type"], "REFERENCE_KNOWLEDGE")
        self.assertEqual(result.knowledge_sources[0]["filename"], "sample-blocking.md")
        self.assertEqual(result.mcp_results[0]["tool"], "get_ops_blocking_sessions")
        self.assertEqual(result.mcp_results[0]["source"], "existing_mcp_server")
        self.assertEqual([call[0] for call in session.calls if call[0] != "list_tools"],
                         ["get_ops_blocking_sessions"])

    def test_reject_plan_never_contacts_mcp(self):
        session = FakeClientSession()
        result = asyncio.run(execute_llm_plan(
            proposed("Run SELECT * FROM sys.databases."),
            mcp_catalog=CATALOG,
            mcp_session=session,
        ))
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(session.calls, [])

    def test_backup_plan_remains_needs_approval_without_mcp(self):
        session = FakeClientSession()
        result = asyncio.run(execute_llm_plan(
            proposed("Back up PracticeDB with a Full backup."),
            mcp_catalog=CATALOG,
            mcp_session=session,
            database_validator=validate_database,
        ))
        self.assertEqual(result.status, "NEEDS_APPROVAL")
        self.assertEqual(result.classification, "NEEDS_APPROVAL")
        self.assertEqual(session.calls, [])

    def test_unapproved_backup_rejected_before_executor_mcp_access(self):
        session = FakeClientSession()
        result = asyncio.run(execute_llm_plan(
            proposed("Back up DatabaseA."),
            mcp_catalog=CATALOG,
            mcp_session=session,
            database_validator=validate_database,
        ))
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
