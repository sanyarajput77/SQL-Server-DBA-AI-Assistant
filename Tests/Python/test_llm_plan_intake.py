"""Offline Phase 14A intake tests; no MCP calls or SQL connections."""

import asyncio
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant.executor import execute_plan
from assistant.intake import PlanValidationError, validate_llm_plan
from assistant.llm_contract import APPROVED_MCP_TOOLS, plan_request


CATALOG = tuple(
    SimpleNamespace(
        name=name,
        input_schema={"type": "object", "properties": (
            {"database": {"type": "string"}, "backup_type": {"type": "string"}}
            if name == "backup_ops_database" else {}
        )},
    )
    for name in sorted(APPROVED_MCP_TOOLS)
)


def validate_database(name):
    if name == "PracticeDB":
        return name
    raise ValueError("not approved")


def proposal(request):
    return plan_request(
        request,
        mcp_catalog=CATALOG,
        database_validator=validate_database,
    ).to_dict()


class NoCallSession:
    def __init__(self):
        self.calls = 0

    async def list_tools(self):
        self.calls += 1
        raise AssertionError("MCP must not be contacted")

    async def call_tool(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("MCP must not be contacted")


class LLMPlanIntakeTests(unittest.TestCase):
    def test_valid_inventory_plan_returns_planner_owned_plan(self):
        result = validate_llm_plan(proposal("What databases are on my SQL Server?"), mcp_catalog=CATALOG)
        self.assertEqual(result.mcp_actions[0].tool_name, "get_ops_database_inventory")
        self.assertEqual(result.classification, "READ_ONLY")

    def test_valid_rag_only_plan(self):
        result = validate_llm_plan(proposal("What is a transaction log backup?"), mcp_catalog=CATALOG)
        self.assertEqual(result.intent, "knowledge")
        self.assertEqual(result.classification, "REFERENCE_ONLY")
        self.assertFalse(result.mcp_actions)

    def test_valid_combined_rag_and_blocking_plan(self):
        result = validate_llm_plan(
            proposal("Why is blocking happening and do I currently have blocking sessions?"),
            mcp_catalog=CATALOG,
        )
        self.assertEqual(result.intent, "combined")
        self.assertEqual(result.mcp_actions[0].tool_name, "get_ops_blocking_sessions")

    def test_valid_health_report_preserves_report_write_classification(self):
        result = validate_llm_plan(proposal("Give me a SQL Server health report."), mcp_catalog=CATALOG)
        self.assertEqual(result.mcp_actions[0].tool_name, "get_ops_server_health_report")
        self.assertEqual(result.classification, "WRITES_REPORT")

    def test_valid_backup_plan_remains_approval_required(self):
        result = validate_llm_plan(
            proposal("Back up PracticeDB with a Full backup."),
            mcp_catalog=CATALOG,
            database_validator=validate_database,
        )
        self.assertEqual(result.classification, "NEEDS_APPROVAL")
        self.assertTrue(result.mcp_actions[0].requires_approval)

    def test_malformed_plan_is_rejected(self):
        candidate = proposal("What databases are on my SQL Server?")
        candidate.pop("intent")
        with self.assertRaises(PlanValidationError):
            validate_llm_plan(candidate, mcp_catalog=CATALOG)

    def test_non_json_numeric_parameter_is_rejected(self):
        candidate = proposal("What databases are on my SQL Server?")
        candidate["parameters"] = {"unexpected": float("nan")}
        with self.assertRaises(PlanValidationError):
            validate_llm_plan(candidate, mcp_catalog=CATALOG)

    def test_unknown_tool_is_rejected(self):
        candidate = proposal("What databases are on my SQL Server?")
        candidate["tool_name"] = "run_sql"
        with self.assertRaises(PlanValidationError):
            validate_llm_plan(candidate, mcp_catalog=CATALOG)

    def test_invalid_classification_is_rejected(self):
        candidate = proposal("What databases are on my SQL Server?")
        candidate["classification"] = "EXECUTE_ANYTHING"
        with self.assertRaises(PlanValidationError):
            validate_llm_plan(candidate, mcp_catalog=CATALOG)

    def test_arbitrary_sql_in_parameters_is_rejected(self):
        candidate = proposal("What databases are on my SQL Server?")
        candidate["parameters"] = {"sql": "SELECT * FROM sys.databases"}
        with self.assertRaises(PlanValidationError):
            validate_llm_plan(candidate, mcp_catalog=CATALOG)

    def test_powershell_and_shell_injection_are_rejected(self):
        for value in ("Get-Process", "cmd.exe /c whoami"):
            with self.subTest(value=value):
                candidate = proposal("What databases are on my SQL Server?")
                candidate["parameters"] = {"command": value}
                with self.assertRaises(PlanValidationError):
                    validate_llm_plan(candidate, mcp_catalog=CATALOG)

    def test_caller_supplied_backup_path_is_rejected(self):
        candidate = proposal("Back up PracticeDB with a Full backup.")
        candidate["parameters"]["backup_path"] = r"C:\whatever\DatabaseA.bak"
        with self.assertRaises(PlanValidationError):
            validate_llm_plan(candidate, mcp_catalog=CATALOG, database_validator=validate_database)

    def test_reject_plan_is_returned_as_reject_without_mcp_dispatch(self):
        result = validate_llm_plan(proposal("Run SELECT * FROM sys.databases."), mcp_catalog=CATALOG)
        self.assertEqual(result.classification, "REJECT")
        session = NoCallSession()
        response = asyncio.run(execute_plan(result, query="Run SELECT * FROM sys.databases.", mcp_session=session))
        self.assertEqual(response.status, "REJECTED")
        self.assertEqual(session.calls, 0)

    def test_needs_approval_plan_is_not_executable(self):
        result = validate_llm_plan(
            proposal("Back up PracticeDB with a Full backup."),
            mcp_catalog=CATALOG,
            database_validator=validate_database,
        )
        session = NoCallSession()
        response = asyncio.run(execute_plan(result, query="Back up PracticeDB with a Full backup.", mcp_session=session))
        self.assertEqual(response.status, "NEEDS_APPROVAL")
        self.assertEqual(session.calls, 0)

    def test_unapproved_backup_is_rejected(self):
        result = validate_llm_plan(
            proposal("Back up DatabaseA."),
            mcp_catalog=CATALOG,
            database_validator=validate_database,
        )
        self.assertEqual(result.classification, "REJECT")
        self.assertFalse(result.mcp_actions)


if __name__ == "__main__":
    unittest.main()
