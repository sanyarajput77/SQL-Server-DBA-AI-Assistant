"""Offline deterministic monitoring-severity evaluation tests."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from monitoring import (
    EvaluationConfig,
    MonitoringCheck,
    MonitoringError,
    MonitoringStatus,
    evaluate_check,
    evaluate_snapshot,
    load_evaluation_config,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "Config" / "monitoring.evaluation.example.json"
INSTANCE = "externally-configured-sql-instance"
STAMP = "2026-09-25T12:30:00+00:00"


def load_config():
    return load_evaluation_config(CONFIG_PATH)


def check(name, data, *, status=MonitoringStatus.SUCCESS, error=None):
    tool_names = {
        "database_inventory": "get_ops_database_inventory",
        "database_health": "get_ops_database_health",
        "database_size": "get_ops_database_size",
        "backup_status": "get_ops_backup_status",
        "disk_space": "get_ops_disk_space",
        "failed_jobs": "get_ops_failed_jobs",
        "blocking_sessions": "get_ops_blocking_sessions",
        "long_running_queries": "get_ops_long_running_queries",
    }
    return MonitoringCheck(name, tool_names[name], STAMP, INSTANCE, status, data, error)


class DeterministicHealthEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_inventory_and_database_health_use_configured_state_maps(self):
        inventory = evaluate_check(check("database_inventory", [
            {"DatabaseName": "Sample", "Status": "Normal", "IsAccessible": True},
        ]), self.config)
        health = evaluate_check(check("database_health", [{"HealthStatus": "Warning"}]), self.config)
        self.assertEqual(inventory.severity, "HEALTHY")
        self.assertEqual(health.severity, "WARNING")

    def test_database_state_critical_and_unmapped_states(self):
        critical = evaluate_check(check("database_inventory", [
            {"DatabaseName": "Sample", "Status": "Offline", "IsAccessible": True},
        ]), self.config)
        unknown = evaluate_check(check("database_inventory", [
            {"DatabaseName": "Sample", "Status": "VendorState", "IsAccessible": True},
        ]), self.config)
        self.assertEqual(critical.severity, "CRITICAL")
        self.assertEqual(unknown.severity, "UNKNOWN")

    def test_size_without_capacity_policy_is_informational(self):
        result = evaluate_check(check("database_size", [
            {"DatabaseName": "Sample", "TotalSizeMB": 100.0},
            {"DatabaseName": "Another", "TotalSizeMB": 25.0},
        ]), self.config)
        self.assertEqual(result.severity, "INFO")
        self.assertEqual(result.measured_values["total_size_mb"], 125.0)

    def test_backup_age_warning_and_critical_use_external_multiplier(self):
        warning = evaluate_check(check("backup_status", [{
            "HoursSinceLatest": 30, "ThresholdHours": 24, "ExceedsThreshold": True,
            "LatestBackupType": "Full",
        }]), self.config)
        critical = evaluate_check(check("backup_status", [{
            "HoursSinceLatest": 48, "ThresholdHours": 24, "ExceedsThreshold": True,
            "LatestBackupType": "Full",
        }]), self.config)
        missing = evaluate_check(check("backup_status", [{
            "HoursSinceLatest": None, "ThresholdHours": 24, "ExceedsThreshold": True,
            "LatestBackupType": "None",
        }]), self.config)
        self.assertEqual(warning.severity, "WARNING")
        self.assertEqual(critical.severity, "CRITICAL")
        self.assertEqual(missing.severity, "WARNING")

    def test_disk_free_warning_and_critical_use_external_policy(self):
        warning = evaluate_check(check("disk_space", [{"PercentFree": 10.0, "ThresholdPercent": 15.0}]), self.config)
        critical = evaluate_check(check("disk_space", [{"PercentFree": 4.0, "ThresholdPercent": 15.0}]), self.config)
        inconsistent = evaluate_check(
            check("disk_space", [{"PercentFree": 4.0, "ThresholdPercent": 15.0}]),
            replace(self.config, disk_free_critical_percent=20.0),
        )
        self.assertEqual(warning.severity, "WARNING")
        self.assertEqual(critical.severity, "CRITICAL")
        self.assertEqual(inconsistent.severity, "UNKNOWN")

    def test_failed_jobs_warning_and_critical_count_rules(self):
        warning = evaluate_check(check("failed_jobs", [{"Status": "Failed"}]), self.config)
        critical = evaluate_check(check("failed_jobs", [{"Status": "Failed"}] * 5), self.config)
        malformed = evaluate_check(check("failed_jobs", [{"Status": "Succeeded"}]), self.config)
        self.assertEqual(warning.severity, "WARNING")
        self.assertEqual(critical.severity, "CRITICAL")
        self.assertEqual(malformed.severity, "UNKNOWN")

    def test_blocking_session_warning_and_critical_count_rules(self):
        warning = evaluate_check(check("blocking_sessions", [{"BlockedSpid": 51}]), self.config)
        critical = evaluate_check(check("blocking_sessions", [{"BlockedSpid": n} for n in range(50, 55)]), self.config)
        self.assertEqual(warning.severity, "WARNING")
        self.assertEqual(critical.severity, "CRITICAL")

    def test_long_running_queries_use_returned_threshold_and_configured_multiplier(self):
        warning = evaluate_check(check("long_running_queries", [{"ElapsedSeconds": 65, "ThresholdSeconds": 60}]), self.config)
        critical = evaluate_check(check("long_running_queries", [{"ElapsedSeconds": 300, "ThresholdSeconds": 60}]), self.config)
        self.assertEqual(warning.severity, "WARNING")
        self.assertEqual(critical.severity, "CRITICAL")

    def test_failed_collection_is_unknown_and_preserves_sanitized_error(self):
        source = check("disk_space", None, status=MonitoringStatus.FAILED,
                       error=MonitoringError("SQL_CONNECTIVITY", "SQL connectivity failed; details withheld."))
        result = evaluate_check(source, self.config)
        self.assertEqual(result.severity, "UNKNOWN")
        self.assertEqual(result.error["category"], "SQL_CONNECTIVITY")
        self.assertEqual(result.original_result, source.to_dict())

    def test_empty_incident_checks_are_healthy_but_empty_inventory_is_unknown(self):
        jobs = evaluate_check(check("failed_jobs", [], status=MonitoringStatus.EMPTY), self.config)
        blocking = evaluate_check(check("blocking_sessions", [], status=MonitoringStatus.EMPTY), self.config)
        inventory = evaluate_check(check("database_inventory", [], status=MonitoringStatus.EMPTY), self.config)
        self.assertEqual(jobs.severity, "HEALTHY")
        self.assertEqual(blocking.severity, "HEALTHY")
        self.assertEqual(inventory.severity, "UNKNOWN")

    def test_missing_measurement_is_unknown_not_healthy(self):
        result = evaluate_check(check("disk_space", [{"Drive": "volume"}]), self.config)
        self.assertEqual(result.severity, "UNKNOWN")
        self.assertEqual(result.reason_code, "DISK_MEASUREMENT_MISSING")

    def test_overall_precedence_and_target_metadata(self):
        checks = [
            check("database_size", [{"TotalSizeMB": 10}]),
            check("failed_jobs", [{"Status": "Failed"}] * 5),
            check("disk_space", [{"PercentFree": 10, "ThresholdPercent": 15}]),
        ]
        overall = evaluate_snapshot(checks, self.config, evaluated_at=datetime(2026, 9, 25, tzinfo=timezone.utc))
        self.assertEqual(overall.severity, "CRITICAL")
        self.assertEqual(overall.sql_instance, INSTANCE)
        self.assertEqual(overall.timestamp_utc, "2026-09-25T00:00:00+00:00")

    def test_all_unknown_and_unknown_plus_healthy_are_overall_unknown(self):
        failed = check("database_health", None, status=MonitoringStatus.FAILED,
                       error=MonitoringError("MCP", "Tool failed; details withheld."))
        unknown = evaluate_snapshot([failed], self.config)
        mixed = evaluate_snapshot([failed, check("failed_jobs", [], status=MonitoringStatus.EMPTY)], self.config)
        self.assertEqual(unknown.severity, "UNKNOWN")
        self.assertEqual(mixed.severity, "UNKNOWN")

    def test_config_requires_all_fields_and_valid_thresholds(self):
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            EvaluationConfig.from_mapping({key: value for key, value in raw.items() if key != "DiskFreeCriticalPercent"})
        raw["DiskFreeCriticalPercent"] = 101
        with self.assertRaises(ValueError):
            EvaluationConfig.from_mapping(raw)
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        raw["FailedJobsCriticalCount"] = 0
        with self.assertRaises(ValueError):
            EvaluationConfig.from_mapping(raw)

    def test_policy_and_evaluation_code_have_no_environment_specific_values(self):
        policy = CONFIG_PATH.read_text(encoding="utf-8")
        source = (ROOT / "monitoring" / "evaluation.py").read_text(encoding="utf-8")
        for marker in ("MSSQL-VM1", "PracticeDB", "C:\\DBA-AI-Assistant"):
            self.assertNotIn(marker, policy)
            self.assertNotIn(marker, source)

    def test_missing_config_file_error_does_not_leak_local_path(self):
        with self.assertRaisesRegex(ValueError, "could not be loaded"):
            load_evaluation_config(ROOT / "Config" / "missing-monitoring-policy.json")


if __name__ == "__main__":
    unittest.main()
