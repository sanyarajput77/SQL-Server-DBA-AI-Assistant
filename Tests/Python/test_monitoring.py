"""Deterministic monitoring service tests; the fake MCP session never connects to SQL."""

import asyncio
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring import MonitoringService, MonitoringStatus, SqlConnectivityError
from monitoring.service import MONITORING_TOOLS


_SPEC = importlib.util.spec_from_file_location("monitoring_test_mcp_server", ROOT / "mcp-server" / "server.py")
assert _SPEC is not None and _SPEC.loader is not None
_SERVER = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _SERVER
_SPEC.loader.exec_module(_SERVER)
CATALOG = asyncio.run(_SERVER.server.list_tools())
TOOL_BY_NAME = {tool.name: tool for tool in CATALOG}
INSTANCE = "Configured-Test-Instance"
FIXED_TIME = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


class FakeSession:
    def __init__(self, responses=None, *, list_error=False, tools=None):
        self.responses = responses or {}
        self.list_error = list_error
        self.tools = CATALOG if tools is None else tools
        self.calls = []

    async def list_tools(self):
        self.calls.append(("list_tools", None))
        if self.list_error:
            raise RuntimeError("private MCP transport detail")
        return SimpleNamespace(tools=self.tools)

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        outcome = self.responses.get(name, [{"SqlInstance": INSTANCE, "Status": "Sample"}])
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome == "malformed":
            payload = "not-json"
        elif outcome == "no-content":
            return SimpleNamespace(is_error=False, content=[])
        else:
            payload = json.dumps(outcome)
        return SimpleNamespace(
            is_error=False,
            content=[SimpleNamespace(type="text", text=payload)],
        )


class MonitoringServiceTests(unittest.TestCase):
    def service(self):
        return MonitoringService(clock=lambda: FIXED_TIME)

    def test_collects_all_eight_existing_read_only_monitoring_categories(self):
        session = FakeSession()
        checks = asyncio.run(self.service().collect(session, sql_instance=INSTANCE))
        self.assertEqual({check.check_name for check in checks}, set(MONITORING_TOOLS))
        self.assertTrue(all(check.status == MonitoringStatus.SUCCESS for check in checks))
        calls = [name for name, _ in session.calls if name != "list_tools"]
        self.assertEqual(set(calls), set(MONITORING_TOOLS.values()))
        self.assertEqual(len(calls), 8)
        self.assertNotIn("backup_ops_database", calls)
        self.assertNotIn("get_ops_server_health_report", calls)

    def test_empty_structured_arrays_are_distinct_from_failures(self):
        session = FakeSession({name: [] for name in MONITORING_TOOLS.values()})
        checks = asyncio.run(self.service().collect(session, sql_instance=INSTANCE))
        self.assertTrue(all(check.status == MonitoringStatus.EMPTY for check in checks))
        self.assertTrue(all(check.data == [] and check.error is None for check in checks))

    def test_explicit_sql_connectivity_failure_is_visible_and_sanitized(self):
        failed_tool = MONITORING_TOOLS["database_inventory"]
        session = FakeSession({failed_tool: SqlConnectivityError("password=hidden C:\\private\\tls.log")})
        checks = asyncio.run(self.service().collect(session, sql_instance=INSTANCE))
        check = next(item for item in checks if item.check_name == "database_inventory")
        self.assertEqual(check.status, MonitoringStatus.FAILED)
        self.assertEqual(check.error.category, "SQL_CONNECTIVITY")
        serialized = repr(check.to_dict())
        self.assertIn("connectivity failed", serialized)
        self.assertNotIn("hidden", serialized)
        self.assertNotIn("private", serialized)
        self.assertNotIn("tls.log", serialized)

    def test_mcp_operation_failure_is_not_normalized_as_healthy(self):
        failed_tool = MONITORING_TOOLS["blocking_sessions"]
        session = FakeSession({failed_tool: RuntimeError("opaque provider error")})
        checks = asyncio.run(self.service().collect(session, sql_instance=INSTANCE))
        check = next(item for item in checks if item.tool_name == failed_tool)
        self.assertEqual(check.status, MonitoringStatus.FAILED)
        self.assertEqual(check.error.category, "MCP")
        self.assertNotIn("opaque", repr(check.to_dict()))

    def test_malformed_result_is_a_failed_check(self):
        tool = MONITORING_TOOLS["database_size"]
        checks = asyncio.run(self.service().collect(
            FakeSession({tool: "malformed"}), sql_instance=INSTANCE,
        ))
        check = next(item for item in checks if item.tool_name == tool)
        self.assertEqual(check.status, MonitoringStatus.FAILED)
        self.assertEqual(check.error.category, "MALFORMED_RESULT")

    def test_missing_result_content_is_not_treated_as_an_empty_success(self):
        tool = MONITORING_TOOLS["failed_jobs"]
        checks = asyncio.run(self.service().collect(
            FakeSession({tool: "no-content"}), sql_instance=INSTANCE,
        ))
        check = next(item for item in checks if item.tool_name == tool)
        self.assertEqual(check.status, MonitoringStatus.FAILED)
        self.assertEqual(check.error.category, "MALFORMED_RESULT")

    def test_result_schema_contains_utc_timestamp_instance_data_and_error_state(self):
        checks = asyncio.run(self.service().collect(FakeSession(), sql_instance=INSTANCE))
        for check in checks:
            value = check.to_dict()
            self.assertEqual(set(value), {
                "check_name", "tool_name", "timestamp_utc", "sql_instance", "status", "data", "error",
            })
            self.assertEqual(value["timestamp_utc"], FIXED_TIME.isoformat())
            self.assertEqual(value["sql_instance"], INSTANCE)
            self.assertIsNotNone(datetime.fromisoformat(value["timestamp_utc"]).tzinfo)

    def test_read_only_annotation_mismatch_fails_without_tool_dispatch(self):
        inventory = TOOL_BY_NAME[MONITORING_TOOLS["database_inventory"]]
        altered = inventory.model_copy(update={
            "annotations": inventory.annotations.model_copy(update={"read_only_hint": False})
        })
        catalog = [altered if tool.name == inventory.name else tool for tool in CATALOG]
        session = FakeSession(tools=catalog)
        checks = asyncio.run(self.service().collect(session, sql_instance=INSTANCE))
        check = next(item for item in checks if item.tool_name == inventory.name)
        self.assertEqual(check.status, MonitoringStatus.FAILED)
        self.assertEqual(check.error.category, "MCP_CATALOG")
        self.assertFalse(any(name == inventory.name for name, _ in session.calls))

    def test_catalog_failure_produces_eight_explicit_failures(self):
        checks = asyncio.run(self.service().collect(
            FakeSession(list_error=True), sql_instance=INSTANCE,
        ))
        self.assertEqual(len(checks), 8)
        self.assertTrue(all(check.status == MonitoringStatus.FAILED for check in checks))
        self.assertTrue(all(check.error.category == "MCP_CATALOG" for check in checks))

    def test_instance_is_required_and_not_hard_coded_or_taken_from_user_query(self):
        session = FakeSession()
        with self.assertRaises(ValueError):
            asyncio.run(self.service().collect(session, sql_instance=""))
        self.assertEqual(session.calls, [])
        checks = asyncio.run(self.service().collect(session, sql_instance=INSTANCE))
        self.assertTrue(all(check.sql_instance == INSTANCE for check in checks))
        self.assertTrue(all(args == {"sql_instance": INSTANCE} for name, args in session.calls if name != "list_tools"))


if __name__ == "__main__":
    unittest.main()
