"""Deterministic, offline tests for Phase 16C report generation."""

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from monitoring import (
    MonitoringCheck, MonitoringError, MonitoringStatus, build_health_report,
    evaluate_snapshot, load_evaluation_config,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = load_evaluation_config(ROOT / "Config" / "monitoring.evaluation.example.json")
STAMP = "2026-09-25T12:30:00+00:00"
REPORT_STAMP = datetime(2026, 9, 25, 12, 45, tzinfo=timezone.utc)
INSTANCE = "configured-instance"
REPORT_ID = "0123456789abcdef0123456789abcdef"


def check(name, data, *, status=MonitoringStatus.SUCCESS, error=None):
    tool = {
        "database_inventory": "get_ops_database_inventory",
        "database_size": "get_ops_database_size",
        "failed_jobs": "get_ops_failed_jobs",
        "blocking_sessions": "get_ops_blocking_sessions",
        "database_health": "get_ops_database_health",
        "backup_status": "get_ops_backup_status",
    }[name]
    return MonitoringCheck(name, tool, STAMP, INSTANCE, status, data, error)


def make_report(*checks):
    evaluations = tuple(__import__("monitoring").evaluate_check(item, CONFIG) for item in checks)
    overall = evaluate_snapshot(checks, CONFIG, evaluated_at=REPORT_STAMP)
    return build_health_report(checks, evaluations, overall, report_id=REPORT_ID, generated_at=REPORT_STAMP)


class DbaHealthReportingTests(unittest.TestCase):
    def test_healthy_report_has_deterministic_identity_timestamp_and_summary(self):
        report = make_report(check("database_inventory", [{"DatabaseName": "AppDb", "Status": "Normal", "IsAccessible": True}]))
        self.assertEqual(report.overall_severity, "HEALTHY")
        self.assertEqual(report.report_id, REPORT_ID)
        self.assertEqual(report.generated_at_utc, REPORT_STAMP.isoformat())
        self.assertEqual(report.summary_by_severity["HEALTHY"], 1)
        self.assertIn("Executive summary", report.to_text())

    def test_warning_and_critical_reports_reflect_policy_evaluation(self):
        warning = make_report(check("failed_jobs", [{"Status": "Failed"}]))
        critical = make_report(check("failed_jobs", [{"Status": "Failed"}] * 5))
        self.assertEqual(warning.overall_severity, "WARNING")
        self.assertEqual(critical.overall_severity, "CRITICAL")

    def test_mixed_severities_and_summary_counts_are_preserved(self):
        report = make_report(
            check("database_inventory", [{"DatabaseName": "AppDb", "Status": "Normal", "IsAccessible": True}]),
            check("failed_jobs", [{"Status": "Failed"}]),
            check("database_size", [{"DatabaseName": "AppDb", "TotalSizeMB": 20}]),
        )
        self.assertEqual(report.overall_severity, "WARNING")
        self.assertEqual(report.summary_by_severity["HEALTHY"], 1)
        self.assertEqual(report.summary_by_severity["WARNING"], 1)
        self.assertEqual(report.summary_by_severity["INFO"], 1)

    def test_unknown_failed_collection_and_empty_data_remain_distinct(self):
        failed = check("database_health", None, status=MonitoringStatus.FAILED,
                       error=MonitoringError("SQL_CONNECTIVITY", "SQL connectivity failed; details withheld."))
        unknown = check("database_inventory", [], status=MonitoringStatus.EMPTY)
        report = make_report(failed, unknown)
        self.assertEqual(report.overall_severity, "UNKNOWN")
        self.assertEqual(report.checks[0].collection_status, "FAILED")
        self.assertEqual(report.checks[0].evaluation_severity, "UNKNOWN")
        self.assertEqual(report.checks[1].collection_status, "EMPTY")
        self.assertEqual(report.checks[1].observed_data, [])
        self.assertIn("Collection failures: database_health", report.to_text())
        self.assertIn("UNKNOWN checks:", report.to_text())

    def test_empty_incident_check_is_healthy_and_does_not_invent_rows(self):
        report = make_report(check("failed_jobs", [], status=MonitoringStatus.EMPTY))
        self.assertEqual(report.checks[0].evaluation_severity, "HEALTHY")
        self.assertEqual(report.checks[0].observed_data, [])

    def test_values_reason_codes_and_evaluation_metrics_are_retained_separately(self):
        report = make_report(check("failed_jobs", [{"JobName": "Nightly", "Status": "Failed"}]))
        check_report = report.checks[0].to_dict()
        self.assertEqual(check_report["observed_data"][0]["JobName"], "Nightly")
        self.assertEqual(check_report["evaluation"]["reason_code"], "FAILED_JOBS_WARNING")
        self.assertEqual(check_report["evaluation"]["measured_values"]["item_count"], 1)

    def test_sanitized_collection_error_is_preserved_without_raw_diagnostics(self):
        error = MonitoringError("SQL_CONNECTIVITY", "SQL connectivity failed; provider details were withheld.")
        report = make_report(check("database_health", None, status=MonitoringStatus.FAILED, error=error))
        self.assertEqual(report.checks[0].to_dict()["error"], {
            "category": "SQL_CONNECTIVITY", "message": "SQL connectivity failed; provider details were withheld."
        })

    def test_html_is_human_readable_and_escapes_untrusted_values(self):
        report = make_report(check("database_inventory", [{"DatabaseName": "<script>alert(1)</script>", "Status": "Normal", "IsAccessible": True}]))
        page = report.to_html()
        self.assertIn("Executive summary", page)
        self.assertIn("HEALTHY", page)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_machine_readable_output_is_json_with_distinct_sections(self):
        report = make_report(check("database_size", [{"DatabaseName": "AppDb", "TotalSizeMB": 30}]))
        data = json.loads(report.to_json())
        self.assertEqual(data["sql_instance"], INSTANCE)
        self.assertIn("overall", data)
        self.assertIn("observed_data", data["checks"][0])
        self.assertIn("evaluation", data["checks"][0])
        self.assertIn("error", data["checks"][0])

    def test_sensitive_values_and_internal_paths_are_not_emitted(self):
        report = make_report(check("backup_status", [{
            "DatabaseName": "AppDb", "HoursSinceLatest": 2, "ThresholdHours": 24,
            "ExceedsThreshold": False, "Password": "secret-value", "BackupPath": "C:\\private\\backup.bak",
        }]))
        rendered = report.to_json() + report.to_text() + report.to_html()
        for forbidden in ("secret-value", "C:\\private", "BackupPath"):
            self.assertNotIn(forbidden, rendered)

    def test_report_requires_matching_nonempty_monitoring_and_evaluation_inputs(self):
        with self.assertRaises(ValueError):
            build_health_report([], [], None)
        result = check("failed_jobs", [], status=MonitoringStatus.EMPTY)
        overall = evaluate_snapshot([result], CONFIG, evaluated_at=REPORT_STAMP)
        with self.assertRaises(ValueError):
            build_health_report([result], [], overall)

    def test_report_schema_does_not_add_missing_observed_values(self):
        report = make_report(check("database_size", [{"DatabaseName": "AppDb"}]))
        data = report.to_dict()["checks"][0]
        self.assertEqual(data["observed_data"], [{"DatabaseName": "AppDb"}])
        self.assertEqual(data["evaluation"]["severity"], "UNKNOWN")
        self.assertNotIn("TotalSizeMB", data["observed_data"][0])


if __name__ == "__main__":
    unittest.main()
