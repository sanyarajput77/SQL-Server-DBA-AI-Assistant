"""Phase 15A tests use fake plans/config only; no MCP or SQL operations."""

import asyncio
from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant.approval import ApprovalAuthority, ApprovalError, ApprovalGrant
from assistant.executor import execute_plan
from assistant.llm_contract import APPROVED_MCP_TOOLS, plan_request
from assistant.orchestrator import MCPAction, orchestrate_request


INSTANCES = ("MSSQL-VM1", "MSSQL-VM2")
DATABASES = {"practicedb": "PracticeDB", "demodb": "DemoDB"}


def validate_database(name):
    if isinstance(name, str) and name.casefold() in DATABASES:
        return DATABASES[name.casefold()]
    raise ValueError("not approved")


def backup_plan(database="PracticeDB", backup_type="Full", sql_instance=None):
    plan = orchestrate_request(
        f"Back up {database} with a {backup_type} backup.",
        mcp_catalog=APPROVED_MCP_TOOLS,
        database_validator=validate_database,
    )
    if plan.classification != "NEEDS_APPROVAL":
        # Build a canonical approved plan first, then forge arguments for negative tests.
        plan = orchestrate_request(
            "Back up PracticeDB with a Full backup.",
            mcp_catalog=APPROVED_MCP_TOOLS,
            database_validator=validate_database,
        )
    action = plan.mcp_actions[0]
    arguments = {"database": database, "backup_type": backup_type}
    if sql_instance is not None:
        arguments["sql_instance"] = sql_instance
    return replace(plan, mcp_actions=(replace(action, arguments=arguments),))


def inventory_plan():
    return orchestrate_request("What databases are on my SQL Server?", mcp_catalog=APPROVED_MCP_TOOLS)


class NoMcpSession:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        self.calls.append("list_tools")
        raise AssertionError("No MCP operation is expected in Phase 15A tests")

    async def call_tool(self, *_args, **_kwargs):
        self.calls.append("call_tool")
        raise AssertionError("No MCP operation is expected in Phase 15A tests")


class Phase15AApprovalTests(unittest.TestCase):
    def setUp(self):
        self.authority = ApprovalAuthority()
        self.plan = backup_plan()
        self.request = self.authority.create_request(
            self.plan,
            approved_instances=INSTANCES,
            default_instance="MSSQL-VM1",
            database_validator=validate_database,
        )
        self.assertIsNotNone(self.request)

    def approve(self, request=None):
        return self.authority.record_explicit_approval(
            request or self.request,
            explicitly_approved_by_human=True,
        )

    def authorize(self, plan=None, request=None, grant=None):
        return self.authority.authorize(
            plan or self.plan,
            request or self.request,
            grant,
            approved_instances=INSTANCES,
            default_instance="MSSQL-VM1",
            database_validator=validate_database,
        )

    def test_valid_approval_request_contains_exact_operation_fields(self):
        request = self.request
        self.assertEqual(request.tool_name, "backup_ops_database")
        self.assertEqual(request.sql_instance, "MSSQL-VM1")
        self.assertEqual(request.database, "PracticeDB")
        self.assertEqual(request.backup_type, "Full")
        self.assertEqual(request.classification, "NEEDS_APPROVAL")
        self.assertEqual(dict(request.parameters), {
            "backup_type": "Full",
            "database": "PracticeDB",
            "destination_policy": "configured",
            "sql_instance": "MSSQL-VM1",
        })
        self.assertEqual(request.backup_destination_policy, "configured")
        self.assertEqual(request.operation_id, self.authority.create_request(
            self.plan,
            approved_instances=INSTANCES,
            default_instance="MSSQL-VM1",
            database_validator=validate_database,
        ).operation_id)

    def test_explicit_approval_yields_eligibility_without_execution(self):
        grant = self.approve()
        eligibility = self.authorize(grant=grant)
        self.assertEqual(eligibility.operation_id, self.request.operation_id)
        self.assertEqual(eligibility.tool_name, "backup_ops_database")
        self.assertEqual(eligibility.classification, "NEEDS_APPROVAL")

    def test_missing_approval_fails_closed(self):
        with self.assertRaises(ApprovalError):
            self.authorize()

    def test_explicit_decline_does_not_issue_approval(self):
        with self.assertRaises(ApprovalError):
            self.authority.record_explicit_approval(self.request, explicitly_approved_by_human=False)

    def test_forged_or_invalid_grant_fails_closed(self):
        forged = ApprovalGrant(operation_id=self.request.operation_id, _nonce="model-created")
        with self.assertRaises(ApprovalError):
            self.authorize(grant=forged)

    def test_approval_for_wrong_database_fails(self):
        grant = self.approve()
        other_plan = backup_plan(database="DemoDB")
        other_request = self.authority.create_request(
            other_plan,
            approved_instances=INSTANCES,
            default_instance="MSSQL-VM1",
            database_validator=validate_database,
        )
        with self.assertRaises(ApprovalError):
            self.authorize(plan=other_plan, request=other_request, grant=grant)

    def test_approval_for_wrong_instance_fails(self):
        grant = self.approve()
        other_plan = backup_plan(sql_instance="MSSQL-VM2")
        other_request = self.authority.create_request(
            other_plan,
            approved_instances=INSTANCES,
            default_instance="MSSQL-VM1",
            database_validator=validate_database,
        )
        with self.assertRaises(ApprovalError):
            self.authorize(plan=other_plan, request=other_request, grant=grant)

    def test_unapproved_instance_cannot_get_an_approval_request(self):
        unapproved = backup_plan(sql_instance="OTHER-SQL")
        with self.assertRaises(ApprovalError):
            self.authority.create_request(
                unapproved,
                approved_instances=INSTANCES,
                default_instance="MSSQL-VM1",
                database_validator=validate_database,
            )

    def test_approval_for_wrong_backup_type_fails(self):
        grant = self.approve()
        other_plan = backup_plan(backup_type="Differential")
        other_request = self.authority.create_request(
            other_plan,
            approved_instances=INSTANCES,
            default_instance="MSSQL-VM1",
            database_validator=validate_database,
        )
        with self.assertRaises(ApprovalError):
            self.authorize(plan=other_plan, request=other_request, grant=grant)

    def test_parameters_changed_after_approval_fail(self):
        grant = self.approve()
        changed = backup_plan(backup_type="Log")
        with self.assertRaises(ApprovalError):
            self.authorize(plan=changed, request=self.request, grant=grant)

    def test_llm_cannot_disable_approval_or_add_approval_marker(self):
        catalog = tuple(type("Tool", (), {"name": name, "input_schema": {"properties": {"database": {}, "backup_type": {}, "sql_instance": {}}}})() for name in APPROVED_MCP_TOOLS)
        candidate = plan_request(
            "Back up PracticeDB with a Full backup.",
            mcp_catalog=catalog,
            database_validator=validate_database,
        ).to_dict()
        candidate["approval_required"] = False
        with self.assertRaises(ValueError):
            from assistant.intake import validate_llm_plan
            validate_llm_plan(candidate, mcp_catalog=catalog, database_validator=validate_database)
        candidate["approval_required"] = True
        candidate["approved"] = True
        with self.assertRaises(ValueError):
            validate_llm_plan(candidate, mcp_catalog=catalog, database_validator=validate_database)

    def test_needs_approval_plan_cannot_execute_without_approval(self):
        session = NoMcpSession()
        response = asyncio.run(execute_plan(
            self.plan,
            query="Back up PracticeDB with a Full backup.",
            mcp_session=session,
        ))
        self.assertEqual(response.status, "NEEDS_APPROVAL")
        self.assertEqual(session.calls, [])
        with self.assertRaises(ApprovalError):
            self.authorize(grant=None)

    def test_read_only_operation_does_not_require_approval(self):
        result = self.authority.create_request(
            inventory_plan(),
            approved_instances=INSTANCES,
            default_instance="MSSQL-VM1",
            database_validator=validate_database,
        )
        self.assertIsNone(result)

    def test_reject_plan_cannot_be_approved_into_execution(self):
        rejected = replace(self.plan, classification="REJECT", intent="unsupported", mcp_actions=())
        with self.assertRaises(ApprovalError):
            self.authority.create_request(
                rejected,
                approved_instances=INSTANCES,
                default_instance="MSSQL-VM1",
                database_validator=validate_database,
            )

    def test_approval_cannot_authorize_wrong_or_unknown_tool(self):
        for tool_name in ("get_ops_database_inventory", "run_sql"):
            with self.subTest(tool_name=tool_name):
                forged = replace(self.plan, mcp_actions=(replace(
                    self.plan.mcp_actions[0], tool_name=tool_name, behavior="NEEDS_APPROVAL"
                ),))
                with self.assertRaises(ApprovalError):
                    self.authority.create_request(
                        forged,
                        approved_instances=INSTANCES,
                        default_instance="MSSQL-VM1",
                        database_validator=validate_database,
                    )

    def test_approval_cannot_authorize_unapproved_database(self):
        forged = backup_plan(database="UnapprovedDB")
        with self.assertRaises(ApprovalError):
            self.authority.create_request(
                forged,
                approved_instances=INSTANCES,
                default_instance="MSSQL-VM1",
                database_validator=validate_database,
            )

    def test_approval_cannot_authorize_caller_supplied_backup_path(self):
        forged = replace(self.plan, mcp_actions=(replace(
            self.plan.mcp_actions[0],
            arguments={"database": "PracticeDB", "backup_type": "Full", "backup_path": r"C:\\outside\\x.bak"},
        ),))
        with self.assertRaises(ApprovalError):
            self.authority.create_request(
                forged,
                approved_instances=INSTANCES,
                default_instance="MSSQL-VM1",
                database_validator=validate_database,
            )

    def test_grant_is_one_use_and_cannot_be_replayed(self):
        grant = self.approve()
        self.authorize(grant=grant)
        with self.assertRaises(ApprovalError):
            self.authorize(grant=grant)

    def test_destination_policy_cannot_change_after_approval(self):
        grant = self.approve()
        altered_request = replace(self.request, backup_destination_policy="caller-supplied")
        with self.assertRaises(ApprovalError):
            self.authorize(request=altered_request, grant=grant)


if __name__ == "__main__":
    unittest.main()
