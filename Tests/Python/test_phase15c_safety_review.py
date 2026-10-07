"""Phase 15C boundary review; no real MCP, SQL, or state-changing calls."""

import asyncio
from dataclasses import replace
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
from assistant.approval import ApprovalAuthority, ApprovalGrant
from assistant.audit import AuditEvent
from assistant.executor import execute_plan
from assistant.llm_contract import APPROVED_MCP_TOOLS, plan_request
from assistant.orchestrator import orchestrate_request
from assistant.response_generation import generate_response


_SPEC = importlib.util.spec_from_file_location("phase15c_mcp_server", ROOT / "mcp-server" / "server.py")
assert _SPEC is not None and _SPEC.loader is not None
_SERVER = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _SERVER
_SPEC.loader.exec_module(_SERVER)
CATALOG = asyncio.run(_SERVER.server.list_tools())
INSTANCES = ("MSSQL-VM1",)


def validate_database(value):
    if value == "PracticeDB":
        return value
    raise ValueError("not approved")


def plan(request):
    return orchestrate_request(
        request,
        mcp_catalog=CATALOG,
        database_validator=validate_database,
    )


def proposal(request):
    return plan_request(
        request,
        mcp_catalog=CATALOG,
        database_validator=validate_database,
    ).to_dict()


class FakeSession:
    def __init__(self, *, fail_call=False):
        self.tools = CATALOG
        self.fail_call = fail_call
        self.calls = []

    async def list_tools(self):
        self.calls.append(("list_tools", None))
        return SimpleNamespace(tools=CATALOG)

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if self.fail_call:
            raise RuntimeError("password=hidden Server=C:\\private\\connection.txt")
        return SimpleNamespace(
            is_error=False,
            content=[SimpleNamespace(type="text", text=json.dumps({"status": "fake"}))],
        )


def approved_eligibility():
    backup = plan("Back up PracticeDB with a Full backup.")
    authority = ApprovalAuthority()
    request = authority.create_request(
        backup,
        approved_instances=INSTANCES,
        default_instance="MSSQL-VM1",
        database_validator=validate_database,
    )
    grant = authority.record_explicit_approval(request, explicitly_approved_by_human=True)
    eligibility = authority.authorize(
        backup,
        request,
        grant,
        approved_instances=INSTANCES,
        default_instance="MSSQL-VM1",
        database_validator=validate_database,
    )
    return backup, authority, eligibility


class Phase15CSafetyReviewTests(unittest.TestCase):
    def test_exact_ten_tool_classifications_match_catalog(self):
        by_name = {tool.name: tool for tool in CATALOG}
        self.assertEqual(set(by_name), set(APPROVED_MCP_TOOLS))
        for name in APPROVED_MCP_TOOLS - {"get_ops_server_health_report", "backup_ops_database"}:
            annotations = by_name[name].annotations
            self.assertIs(annotations.read_only_hint, True)
            self.assertIs(annotations.destructive_hint, False)
        report = by_name["get_ops_server_health_report"]
        self.assertIs(report.annotations.read_only_hint, False)
        self.assertIs(report.annotations.destructive_hint, False)
        self.assertIn("WRITES_REPORT", report.description)
        backup = by_name["backup_ops_database"]
        self.assertIs(backup.annotations.read_only_hint, False)
        self.assertIs(backup.annotations.destructive_hint, True)
        self.assertIn("NEEDS_APPROVAL", backup.description)

    def test_direct_executor_backup_invocation_without_eligibility_fails_closed(self):
        session = FakeSession()
        result = asyncio.run(execute_plan(
            plan("Back up PracticeDB with a Full backup."),
            query="Back up PracticeDB with a Full backup.",
            mcp_session=session,
        ))
        self.assertEqual(result.status, "NEEDS_APPROVAL")
        self.assertEqual(session.calls, [])

    def test_forged_eligibility_cannot_authorize_unknown_or_modified_operation(self):
        backup, authority, valid = approved_eligibility()
        forged = replace(valid, operation_id="0" * 64, _proof="model-forged")
        session = FakeSession()
        result = asyncio.run(execute_plan(
            backup,
            query="backup",
            mcp_session=session,
            approval_authority=authority,
            execution_eligibility=forged,
        ))
        self.assertEqual(result.status, "NEEDS_APPROVAL")
        self.assertEqual(session.calls, [])

    def test_changed_plan_after_authorization_consumes_and_rejects_eligibility(self):
        backup, authority, eligibility = approved_eligibility()
        action = replace(backup.mcp_actions[0], arguments={"database": "PracticeDB", "backup_type": "Log"})
        changed = replace(backup, mcp_actions=(action,))
        session = FakeSession()
        result = asyncio.run(execute_plan(
            changed,
            query="backup",
            mcp_session=session,
            approval_authority=authority,
            execution_eligibility=eligibility,
        ))
        self.assertEqual(result.status, "NEEDS_APPROVAL")
        self.assertEqual(session.calls, [])

    def test_replayed_execution_eligibility_cannot_dispatch_twice(self):
        backup, authority, eligibility = approved_eligibility()
        session = FakeSession()
        first = asyncio.run(execute_plan(
            backup, query="backup", mcp_session=session,
            approval_authority=authority, execution_eligibility=eligibility,
        ))
        second = asyncio.run(execute_plan(
            backup, query="backup", mcp_session=session,
            approval_authority=authority, execution_eligibility=eligibility,
        ))
        self.assertEqual(first.status, "SUCCESS")
        self.assertEqual(second.status, "NEEDS_APPROVAL")
        self.assertEqual(sum(name == "backup_ops_database" for name, _ in session.calls), 1)

    def test_unknown_tool_cannot_be_made_read_only_by_alternate_plan(self):
        candidate = plan("What databases are on my SQL Server?")
        action = replace(candidate.mcp_actions[0], tool_name="run_sql", behavior="READ_ONLY")
        session = FakeSession()
        result = asyncio.run(execute_plan(
            replace(candidate, mcp_actions=(action,)), query="list", mcp_session=session,
        ))
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(session.calls, [])

    def test_reject_and_malformed_application_plans_do_not_dispatch(self):
        session = FakeSession()
        rejected = asyncio.run(execute_llm_plan(
            proposal("Run SELECT * FROM sys.databases."),
            mcp_catalog=CATALOG,
            mcp_session=session,
            database_validator=validate_database,
        ))
        self.assertEqual(rejected.status, "REJECTED")
        malformed = proposal("What databases are on my SQL Server?")
        malformed["tool_name"] = "run_sql"
        with self.assertRaises(ValueError):
            asyncio.run(execute_llm_plan(
                malformed, mcp_catalog=CATALOG, mcp_session=session,
                database_validator=validate_database,
            ))
        self.assertEqual(session.calls, [])

    def test_failed_mcp_result_and_response_are_sanitized(self):
        session = FakeSession(fail_call=True)
        result = asyncio.run(execute_llm_plan(
            proposal("What databases are on my SQL Server?"),
            mcp_catalog=CATALOG,
            mcp_session=session,
            database_validator=validate_database,
        ))
        rendered = generate_response(result)
        self.assertEqual(result.status, "MCP_ERROR")
        self.assertIn("failed", rendered.answer.casefold())
        combined = repr(result.to_dict()) + repr(rendered.to_dict())
        for marker in ("hidden", "private", "connection.txt", "RuntimeError"):
            self.assertNotIn(marker, combined)

    def test_response_generation_failure_has_no_execution_interface(self):
        session = FakeSession()
        with self.assertRaises(TypeError):
            generate_response(None)
        self.assertEqual(session.calls, [])

    def test_audit_contract_has_bounded_fields_and_no_raw_payload(self):
        event = AuditEvent(
            tool_name="backup_ops_database",
            classification="NEEDS_APPROVAL",
            approval_required=True,
            approval_outcome="GRANTED",
            execution_attempted=False,
            execution_result="NOT_ATTEMPTED",
            sql_instance="MSSQL-VM1",
            database="PracticeDB",
            operation_id="a" * 64,
        ).to_dict()
        self.assertEqual(event["execution_result"], "NOT_ATTEMPTED")
        self.assertEqual(event["approval_outcome"], "GRANTED")
        self.assertFalse({"parameters", "request", "error", "password", "connection_string"} & set(event))
        self.assertIn("timestamp_utc", event)
        self.assertIn("request_id", event)

    def test_audit_contract_rejects_unknown_tools_secrets_and_unbounded_status(self):
        common = dict(
            classification="NEEDS_APPROVAL", approval_required=True,
            approval_outcome="PENDING", execution_attempted=False,
            execution_result="NOT_ATTEMPTED",
        )
        with self.assertRaises(ValueError):
            AuditEvent(tool_name="run_sql", **common)
        with self.assertRaises(ValueError):
            AuditEvent(tool_name="backup_ops_database", database="password=secret", **common)
        with self.assertRaises(ValueError):
            AuditEvent(tool_name="backup_ops_database", failure_category="secret: value", **common)


if __name__ == "__main__":
    unittest.main()
