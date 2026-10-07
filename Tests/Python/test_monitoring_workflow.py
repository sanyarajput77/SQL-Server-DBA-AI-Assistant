"""Offline tests for Phase 16D monitoring orchestration and persistence."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monitoring import (
    MonitoringCheck, MonitoringError, MonitoringStatus, MonitoringWorkflow,
    MonitoringWorkflowConfig, evaluate_check, evaluate_snapshot,
    load_evaluation_config,
)
from monitoring.service import MONITORING_TOOLS


ROOT = Path(__file__).resolve().parents[2]
POLICY_SOURCE = ROOT / "Config" / "monitoring.evaluation.example.json"
FIXED_TIME = datetime(2026, 9, 25, 12, 30, 0, 123000, tzinfo=timezone.utc)
WORKFLOW_ID = "0123456789abcdef0123456789abcdef"
INSTANCE = "configured-instance"


class FakeService:
    def __init__(self, checks=None, failure=None):
        self.checks = tuple(checks or ())
        self.failure = failure
        self.calls = []

    async def collect(self, session, *, sql_instance, check_names):
        self.calls.append((sql_instance, tuple(check_names)))
        if self.failure:
            raise self.failure
        return self.checks


class MonitoringWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_dir = self.root / "Config"
        self.config_dir.mkdir()
        self.id_counter = 0
        (self.config_dir / "policy.json").write_text(POLICY_SOURCE.read_text(encoding="utf-8"), encoding="utf-8")
        self.output_dir = self.root / "Artifacts"
        self.config_path = self.config_dir / "workflow.json"
        self.write_config()

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self, **changes):
        config = {
            "SchemaVersion": 1,
            "Environment": "Test",
            "EnvironmentId": "Test-Workflow",
            "SqlInstance": INSTANCE,
            "AllowedSqlInstances": [INSTANCE],
            "EvaluationPolicyPath": "policy.json",
            "ReportOutputDirectory": "../Artifacts",
            "EnabledChecks": ["database_inventory"],
            "ArtifactFormats": ["text", "html", "json"],
        }
        config.update(changes)
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

    def check(self, name="database_inventory", data=None, *, status=MonitoringStatus.SUCCESS, error=None):
        if data is None and status == MonitoringStatus.SUCCESS:
            data = [{"DatabaseName": "AppDb", "Status": "Normal", "IsAccessible": True}]
        return MonitoringCheck(name, MONITORING_TOOLS[name], FIXED_TIME.isoformat(), INSTANCE, status, data, error)

    def next_id(self):
        selected = f"{int(WORKFLOW_ID, 16) + self.id_counter:032x}"
        self.id_counter += 1
        return selected

    def workflow(self, checks=(), *, service=None, report_builder=None, artifact_writer=None, workflow_id=None):
        return MonitoringWorkflow(
            service=service or FakeService(checks),
            clock=lambda: FIXED_TIME,
            report_builder=report_builder or __import__("monitoring").build_health_report,
            artifact_writer=artifact_writer or __import__("monitoring.workflow", fromlist=["_write_artifacts"])._write_artifacts,
            id_factory=lambda: workflow_id or self.next_id(),
        )

    async def run_for(self, check, *, workflow_id=None, **kwargs):
        return await self.workflow([check], workflow_id=workflow_id, **kwargs).run(object(), self.config_path)

    async def test_successful_workflow_persists_all_requested_artifacts_and_audit(self):
        result = await self.run_for(self.check())
        self.assertEqual(result.workflow_status, "SUCCESS")
        self.assertEqual(result.overall_severity, "HEALTHY")
        self.assertEqual(result.check_count, 1)
        self.assertEqual(set(result.artifact_locations), {"text", "html", "json"})
        self.assertEqual(len(result.audit_events), 1)
        self.assertEqual(result.audit_events[0].tool_name, "get_ops_database_inventory")
        self.assertTrue(result.audit_events[0].execution_attempted)
        self.assertEqual(result.audit_events[0].execution_result, "SUCCESS")

    async def test_warning_critical_and_unknown_severity_map_to_matching_workflow_status(self):
        info = await self.run_for(self.check("database_size", [{"DatabaseName": "AppDb", "TotalSizeMB": 20}]))
        warning = await self.run_for(self.check("failed_jobs", [{"Status": "Failed"}]))
        critical = await self.run_for(self.check("failed_jobs", [{"Status": "Failed"}] * 5))
        unknown = await self.run_for(self.check("database_health", [], status=MonitoringStatus.EMPTY))
        self.assertEqual((info.workflow_status, info.info_count), ("SUCCESS", 1))
        self.assertEqual((warning.workflow_status, warning.warning_count), ("WARNING", 1))
        self.assertEqual((critical.workflow_status, critical.critical_count), ("CRITICAL", 1))
        self.assertEqual((unknown.workflow_status, unknown.unknown_count), ("UNKNOWN", 1))

    async def test_collection_check_failure_still_generates_report_as_unknown(self):
        failed = self.check("database_health", None, status=MonitoringStatus.FAILED,
                            error=MonitoringError("SQL_CONNECTIVITY", "SQL connectivity failed; details withheld."))
        result = await self.run_for(failed)
        self.assertEqual(result.workflow_status, "UNKNOWN")
        self.assertEqual(result.unknown_count, 1)
        self.assertEqual(result.collection_failure_count, 1)
        self.assertEqual(set(result.artifact_locations), {"text", "html", "json"})
        self.assertIsNone(result.failure)

    async def test_collection_failure_cannot_be_hidden_by_informational_result(self):
        self.write_config(EnabledChecks=["database_health", "database_size"])
        failed = self.check("database_health", None, status=MonitoringStatus.FAILED,
                            error=MonitoringError("MCP", "MCP call failed; details withheld."))
        info = self.check("database_size", [{"DatabaseName": "AppDb", "TotalSizeMB": 20}])
        result = await self.workflow([failed, info]).run(object(), self.config_path)
        self.assertEqual(result.overall_severity, "INFO")
        self.assertEqual(result.workflow_status, "UNKNOWN")
        self.assertEqual(result.collection_failure_count, 1)

    async def test_monitoring_infrastructure_failure_is_workflow_failure(self):
        service = FakeService(failure=RuntimeError("sensitive diagnostic"))
        result = await self.workflow(service=service).run(object(), self.config_path)
        self.assertEqual(result.workflow_status, "FAILED")
        self.assertEqual(result.failure.stage, "monitoring_collection")
        self.assertNotIn("sensitive diagnostic", json.dumps(result.to_dict()))

    async def test_invalid_configuration_fails_without_collection(self):
        self.write_config(SqlInstance="not-in-allowlist")
        service = FakeService()
        result = await self.workflow(service=service).run(object(), self.config_path)
        self.assertEqual(result.workflow_status, "FAILED")
        self.assertEqual(result.failure.stage, "configuration")
        self.assertEqual(service.calls, [])

    async def test_evaluation_failure_is_not_reported_as_success(self):
        with patch("monitoring.workflow.evaluate_check", side_effect=RuntimeError("raw")):
            result = await self.run_for(self.check())
        self.assertEqual(result.workflow_status, "FAILED")
        self.assertEqual(result.failure.stage, "evaluation")
        self.assertEqual(result.failure.message, "The monitoring workflow failed; details were withheld.")

    async def test_report_generation_failure_is_not_reported_as_success(self):
        def fail_report(*args, **kwargs):
            raise RuntimeError("private report details")
        result = await self.run_for(self.check(), report_builder=fail_report)
        self.assertEqual(result.workflow_status, "FAILED")
        self.assertEqual(result.failure.stage, "report_generation")
        self.assertEqual(result.check_count, 1)
        self.assertEqual(result.overall_severity, "HEALTHY")
        self.assertNotIn("private report details", json.dumps(result.to_dict()))

    async def test_artifact_persistence_failure_is_distinct_and_sanitized(self):
        def fail_write(*args, **kwargs):
            raise OSError("C:\\private\\secret")
        result = await self.run_for(self.check(), artifact_writer=fail_write)
        self.assertEqual(result.workflow_status, "FAILED")
        self.assertEqual(result.failure.stage, "artifact_persistence")
        self.assertEqual((result.check_count, result.overall_severity), (1, "HEALTHY"))
        self.assertNotIn("C:\\private", json.dumps(result.to_dict()))

    async def test_repeated_deterministic_run_does_not_overwrite_artifacts(self):
        first = await self.run_for(self.check(), workflow_id=WORKFLOW_ID)
        second = await self.run_for(self.check(), workflow_id=WORKFLOW_ID)
        self.assertEqual(first.workflow_status, "SUCCESS")
        self.assertEqual(second.workflow_status, "FAILED")
        self.assertEqual(second.failure.stage, "artifact_persistence")
        self.assertEqual(second.artifact_locations, {})
        self.assertTrue(Path(first.artifact_locations["json"]).is_file())

    async def test_artifact_names_include_deterministic_utc_stamp_and_run_id(self):
        result = await self.run_for(self.check())
        name = Path(result.artifact_locations["json"]).name
        self.assertEqual(name, f"dba-health-20260925T123000123000Z-{WORKFLOW_ID}.json")

    async def test_enabled_checks_are_the_only_collection_requests_and_are_read_only(self):
        self.write_config(EnabledChecks=["blocking_sessions"])
        check = self.check("blocking_sessions", [], status=MonitoringStatus.EMPTY)
        service = FakeService([check])
        result = await self.workflow(service=service).run(object(), self.config_path)
        self.assertEqual(service.calls, [(INSTANCE, ("blocking_sessions",))])
        self.assertEqual(result.audit_events[0].tool_name, "get_ops_blocking_sessions")
        self.assertNotIn("backup_ops_database", [event.tool_name for event in result.audit_events])

    async def test_config_rejects_secrets_unknown_fields_and_unapproved_checks(self):
        for change in (
            {"Password": "secret"},
            {"EnabledChecks": ["arbitrary_tool"]},
            {"SqlInstance": "other-instance"},
        ):
            self.write_config(**change)
            with self.assertRaises(ValueError):
                MonitoringWorkflowConfig.load(self.config_path)

    async def test_portable_example_configuration_loads_without_creating_outputs(self):
        config, policy = MonitoringWorkflowConfig.load(ROOT / "Config" / "monitoring.workflow.example.json")
        self.assertEqual(config.sql_instance, "MSSQL-VM1")
        self.assertEqual(len(config.enabled_checks), 8)
        self.assertEqual(config.artifact_formats, ("text", "html", "json"))
        self.assertEqual(policy.failed_jobs_warning_count, 1)
        self.assertFalse(config.report_output_directory.exists())

    async def test_artifacts_do_not_contain_secret_fields_or_raw_exceptions(self):
        sensitive = self.check(data=[{
            "DatabaseName": "AppDb", "Status": "Normal", "IsAccessible": True,
            "Password": "dont-print-this", "QueryText": "SELECT confidential", "BackupPath": "C:\\secret\\backup.bak",
        }])
        result = await self.run_for(sensitive)
        combined = "".join(Path(path).read_text(encoding="utf-8") for path in result.artifact_locations.values())
        for forbidden in ("dont-print-this", "SELECT confidential", "C:\\secret", "BackupPath"):
            self.assertNotIn(forbidden, combined)

    async def test_workflow_result_has_required_counts_identity_and_safe_audit_metadata(self):
        result = await self.run_for(self.check())
        payload = result.to_dict()
        for key in ("workflow_id", "timestamp_utc", "sql_instance", "overall_severity", "check_count", "collection_failure_count",
                    "healthy_count", "info_count", "warning_count", "critical_count", "unknown_count",
                    "artifact_locations", "workflow_status"):
            self.assertIn(key, payload)
        audit = payload["audit_events"][0]
        self.assertEqual(audit["request_id"], result.workflow_id)
        self.assertNotIn("parameters", audit)

    async def test_collection_failure_audit_uses_existing_metadata_contract(self):
        check = self.check("database_health", None, status=MonitoringStatus.FAILED,
                           error=MonitoringError("MCP", "MCP call failed; details withheld."))
        result = await self.run_for(check)
        self.assertEqual(result.audit_events[0].execution_result, "FAILURE")
        self.assertEqual(result.audit_events[0].failure_category, "MCP")
        self.assertNotIn("details withheld", json.dumps(result.audit_events[0].to_dict()))


if __name__ == "__main__":
    unittest.main()
