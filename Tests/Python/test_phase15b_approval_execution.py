"""Phase 15B app-to-executor tests use a fake MCP session only."""

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
from assistant.approval import ApprovalAuthority, ApprovalError, ApprovalGrant
from assistant.intake import validate_llm_plan
from assistant.llm_contract import plan_request
from assistant.orchestrator import orchestrate_request


MCP_SERVER_PATH = ROOT / "mcp-server" / "server.py"
_SPEC = importlib.util.spec_from_file_location("phase15b_existing_mcp_server", MCP_SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_SERVER = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _SERVER
_SPEC.loader.exec_module(_SERVER)
CATALOG = asyncio.run(_SERVER.server.list_tools())
INSTANCES = ("MSSQL-VM1", "MSSQL-VM2")
DATABASES = {"practicedb": "PracticeDB", "demodb": "DemoDB"}


def validate_database(name):
    if isinstance(name, str) and name.casefold() in DATABASES:
        return DATABASES[name.casefold()]
    raise ValueError("not approved")


def proposed(request):
    return plan_request(
        request,
        mcp_catalog=CATALOG,
        database_validator=validate_database,
    ).to_dict()


def approved_operation(request_text="Back up PracticeDB with a Full backup.", instance="MSSQL-VM1"):
    plan = orchestrate_request(
        request_text,
        mcp_catalog=CATALOG,
        database_validator=validate_database,
    )
    authority = ApprovalAuthority()
    approval_request = authority.create_request(
        plan,
        approved_instances=INSTANCES,
        default_instance=instance,
        database_validator=validate_database,
    )
    grant = authority.record_explicit_approval(
        approval_request,
        explicitly_approved_by_human=True,
    )
    return authority, plan, approval_request, grant


class FakeMcpSession:
    """Records SDK-shaped operations and never connects to SQL Server."""

    def __init__(self):
        self.tools = CATALOG
        self.calls = []

    async def list_tools(self):
        self.calls.append(("list_tools", None))
        return SimpleNamespace(tools=self.tools)

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return SimpleNamespace(
            is_error=False,
            content=[SimpleNamespace(type="text", text=json.dumps({"status": "fake-success"}))],
        )


class Phase15BApprovalExecutionTests(unittest.TestCase):
    def run_application(self, request, session, **approval):
        return asyncio.run(execute_llm_plan(
            proposed(request),
            mcp_catalog=CATALOG,
            mcp_session=session,
            database_validator=validate_database,
            approved_instances=INSTANCES,
            default_instance="MSSQL-VM1",
            **approval,
        ))

    def test_read_only_executes_without_approval(self):
        session = FakeMcpSession()
        result = self.run_application("What databases are on my SQL Server?", session)
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual([call for call in session.calls if call[0] != "list_tools"],
                         [("get_ops_database_inventory", {})])

    def test_reject_never_executes_or_accepts_approval(self):
        session = FakeMcpSession()
        result = self.run_application("Run SELECT * FROM sys.databases.", session)
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(session.calls, [])

    def test_needs_approval_without_grant_does_not_call_mcp(self):
        session = FakeMcpSession()
        result = self.run_application("Back up PracticeDB with a Full backup.", session)
        self.assertEqual(result.status, "NEEDS_APPROVAL")
        self.assertEqual(session.calls, [])

    def test_valid_approval_dispatches_exact_backup_tool_and_parameters_to_fake(self):
        authority, _, approval_request, grant = approved_operation()
        session = FakeMcpSession()
        result = self.run_application(
            "Back up PracticeDB with a Full backup.",
            session,
            approval_authority=authority,
            approval_request=approval_request,
            approval_grant=grant,
        )
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual([call for call in session.calls if call[0] != "list_tools"], [(
            "backup_ops_database",
            {"database": "PracticeDB", "backup_type": "Full", "sql_instance": "MSSQL-VM1"},
        )])

    def test_wrong_database_after_approval_does_not_call_mcp(self):
        authority, _, approval_request, grant = approved_operation()
        session = FakeMcpSession()
        result = self.run_application(
            "Back up DemoDB with a Full backup.", session,
            approval_authority=authority, approval_request=approval_request, approval_grant=grant,
        )
        self.assertEqual(result.status, "NEEDS_APPROVAL")
        self.assertEqual(session.calls, [])

    def test_wrong_instance_after_approval_does_not_call_mcp(self):
        authority, _, approval_request, grant = approved_operation()
        session = FakeMcpSession()
        result = asyncio.run(execute_llm_plan(
            proposed("Back up PracticeDB with a Full backup."),
            mcp_catalog=CATALOG,
            mcp_session=session,
            database_validator=validate_database,
            approved_instances=INSTANCES,
            default_instance="MSSQL-VM2",
            approval_authority=authority,
            approval_request=approval_request,
            approval_grant=grant,
        ))
        self.assertEqual(result.status, "NEEDS_APPROVAL")
        self.assertEqual(session.calls, [])

    def test_wrong_backup_type_after_approval_does_not_call_mcp(self):
        authority, _, approval_request, grant = approved_operation()
        session = FakeMcpSession()
        result = self.run_application(
            "Back up PracticeDB with a Differential backup.", session,
            approval_authority=authority, approval_request=approval_request, approval_grant=grant,
        )
        self.assertEqual(result.status, "NEEDS_APPROVAL")
        self.assertEqual(session.calls, [])

    def test_modified_canonical_parameters_after_approval_do_not_call_mcp(self):
        authority, _, approval_request, grant = approved_operation()
        changed = replace(approval_request, parameters=(("database", "DemoDB"),))
        session = FakeMcpSession()
        result = self.run_application(
            "Back up PracticeDB with a Full backup.", session,
            approval_authority=authority, approval_request=changed, approval_grant=grant,
        )
        self.assertEqual(result.status, "NEEDS_APPROVAL")
        self.assertEqual(session.calls, [])

    def test_modified_destination_policy_after_approval_does_not_call_mcp(self):
        authority, _, approval_request, grant = approved_operation()
        changed = replace(approval_request, backup_destination_policy="caller-supplied")
        session = FakeMcpSession()
        result = self.run_application(
            "Back up PracticeDB with a Full backup.", session,
            approval_authority=authority, approval_request=changed, approval_grant=grant,
        )
        self.assertEqual(result.status, "NEEDS_APPROVAL")
        self.assertEqual(session.calls, [])

    def test_modified_tool_name_after_approval_does_not_call_mcp(self):
        authority, _, approval_request, grant = approved_operation()
        session = FakeMcpSession()
        candidate = proposed("Back up PracticeDB with a Full backup.")
        candidate["tool_name"] = "get_ops_database_inventory"
        with self.assertRaises(ValueError):
            asyncio.run(execute_llm_plan(
                candidate,
                mcp_catalog=CATALOG,
                mcp_session=session,
                database_validator=validate_database,
                approved_instances=INSTANCES,
                default_instance="MSSQL-VM1",
                approval_authority=authority,
                approval_request=approval_request,
                approval_grant=grant,
            ))
        self.assertEqual(session.calls, [])

    def test_operation_id_mismatch_does_not_call_mcp(self):
        authority, _, approval_request, grant = approved_operation()
        changed = replace(approval_request, operation_id="0" * 64)
        session = FakeMcpSession()
        result = self.run_application(
            "Back up PracticeDB with a Full backup.", session,
            approval_authority=authority, approval_request=changed, approval_grant=grant,
        )
        self.assertEqual(result.status, "NEEDS_APPROVAL")
        self.assertEqual(session.calls, [])

    def test_approval_replay_does_not_dispatch_again(self):
        authority, _, approval_request, grant = approved_operation()
        session = FakeMcpSession()
        first = self.run_application(
            "Back up PracticeDB with a Full backup.", session,
            approval_authority=authority, approval_request=approval_request, approval_grant=grant,
        )
        second = self.run_application(
            "Back up PracticeDB with a Full backup.", session,
            approval_authority=authority, approval_request=approval_request, approval_grant=grant,
        )
        self.assertEqual(first.status, "SUCCESS")
        self.assertEqual(second.status, "NEEDS_APPROVAL")
        self.assertEqual(sum(call[0] == "backup_ops_database" for call in session.calls), 1)

    def test_forged_approval_does_not_call_mcp(self):
        authority, _, approval_request, _ = approved_operation()
        forged = ApprovalGrant(operation_id=approval_request.operation_id, _nonce="not-issued")
        session = FakeMcpSession()
        result = self.run_application(
            "Back up PracticeDB with a Full backup.", session,
            approval_authority=authority, approval_request=approval_request, approval_grant=forged,
        )
        self.assertEqual(result.status, "NEEDS_APPROVAL")
        self.assertEqual(session.calls, [])

    def test_unapproved_database_cannot_become_executable(self):
        session = FakeMcpSession()
        result = self.run_application("Back up UnapprovedDB with a Full backup.", session)
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(session.calls, [])

    def test_caller_supplied_backup_path_cannot_become_executable(self):
        session = FakeMcpSession()
        result = self.run_application(r"Back up PracticeDB to C:\temp\db.bak", session)
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(session.calls, [])

    def test_forged_execution_eligibility_does_not_call_mcp(self):
        authority, plan, _, _ = approved_operation()
        from assistant.approval import ExecutionEligibility
        forged = ExecutionEligibility(
            operation_id="0" * 64,
            tool_name="backup_ops_database",
            sql_instance="MSSQL-VM1",
            database="PracticeDB",
            backup_type="Full",
            classification="NEEDS_APPROVAL",
            parameters=(("backup_type", "Full"), ("database", "PracticeDB"), ("destination_policy", "configured"), ("sql_instance", "MSSQL-VM1")),
            backup_destination_policy="configured",
            _proof="fake-proof",
        )
        session = FakeMcpSession()
        from assistant.executor import execute_plan
        response = asyncio.run(execute_plan(
            plan,
            query="Back up PracticeDB with a Full backup.",
            mcp_session=session,
            approval_authority=authority,
            execution_eligibility=forged,
        ))
        self.assertEqual(response.status, "NEEDS_APPROVAL")
        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
