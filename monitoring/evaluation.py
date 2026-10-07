"""Deterministic configured evaluation of Phase 16A collection results."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .model import MonitoringCheck, MonitoringStatus
from .service import MONITORING_TOOLS


SEVERITIES = {"HEALTHY", "INFO", "WARNING", "CRITICAL", "UNKNOWN"}
_SEVERITY_RANK = {"HEALTHY": 0, "UNKNOWN": 1, "INFO": 2, "WARNING": 3, "CRITICAL": 4}
_CONFIG_FIELDS = {
    "SchemaVersion", "DatabaseStateSeverity", "DatabaseHealthSeverity",
    "BackupAgeCriticalMultiplier", "DiskFreeCriticalPercent",
    "FailedJobsWarningCount", "FailedJobsCriticalCount",
    "BlockingWarningCount", "BlockingCriticalCount",
    "LongRunningQueryCriticalMultiplier",
}


def _positive_number(value: Any, name: str, *, minimum_exclusive: float = 0) -> float:
    if type(value) not in {int, float} or not math.isfinite(value) or value <= minimum_exclusive:
        raise ValueError(f"{name} must be a finite number greater than {minimum_exclusive}.")
    return float(value)


def _positive_count(value: Any, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return value


@dataclass(frozen=True)
class EvaluationConfig:
    """Threshold policy loaded from a separate, non-secret JSON file."""

    database_state_severity: Mapping[str, str]
    database_health_severity: Mapping[str, str]
    backup_age_critical_multiplier: float
    disk_free_critical_percent: float
    failed_jobs_warning_count: int
    failed_jobs_critical_count: int
    blocking_warning_count: int
    blocking_critical_count: int
    long_running_query_critical_multiplier: float

    def __post_init__(self) -> None:
        for field_name in ("database_state_severity", "database_health_severity"):
            mapping = getattr(self, field_name)
            if not isinstance(mapping, Mapping) or not mapping:
                raise ValueError(f"{field_name} must be a non-empty mapping.")
            normalized: dict[str, str] = {}
            for key, severity in mapping.items():
                if not isinstance(key, str) or not key.strip() or not isinstance(severity, str) or severity not in SEVERITIES - {"UNKNOWN", "INFO"}:
                    raise ValueError(f"{field_name} contains an invalid state mapping.")
                if key.casefold() in normalized:
                    raise ValueError(f"{field_name} contains duplicate case-insensitive states.")
                normalized[key.casefold()] = severity
            object.__setattr__(self, field_name, MappingProxyType(normalized))

        backup_multiplier = _positive_number(self.backup_age_critical_multiplier, "BackupAgeCriticalMultiplier", minimum_exclusive=1)
        query_multiplier = _positive_number(self.long_running_query_critical_multiplier, "LongRunningQueryCriticalMultiplier", minimum_exclusive=1)
        disk_critical = self.disk_free_critical_percent
        if type(disk_critical) not in {int, float} or not math.isfinite(disk_critical) or not 0 <= disk_critical <= 100:
            raise ValueError("DiskFreeCriticalPercent must be between 0 and 100.")

        counts = (
            (self.failed_jobs_warning_count, self.failed_jobs_critical_count, "failed job"),
            (self.blocking_warning_count, self.blocking_critical_count, "blocking"),
        )
        for warning, critical, label in counts:
            _positive_count(warning, f"{label} warning count")
            _positive_count(critical, f"{label} critical count")
            if critical < warning:
                raise ValueError(f"{label} critical count must be greater than or equal to its warning count.")
        object.__setattr__(self, "backup_age_critical_multiplier", backup_multiplier)
        object.__setattr__(self, "long_running_query_critical_multiplier", query_multiplier)
        object.__setattr__(self, "disk_free_critical_percent", float(disk_critical))

    @classmethod
    def from_mapping(cls, document: Mapping[str, Any]) -> "EvaluationConfig":
        if (
            not isinstance(document, Mapping)
            or (set(document) != _CONFIG_FIELDS and set(document) != _CONFIG_FIELDS | {"$schema"})
            or type(document.get("SchemaVersion")) is not int
            or document.get("SchemaVersion") != 1
            or ("$schema" in document and not isinstance(document["$schema"], str))
        ):
            raise ValueError("Evaluation configuration is missing required fields or has an unsupported schema version.")
        return cls(
            database_state_severity=document["DatabaseStateSeverity"],
            database_health_severity=document["DatabaseHealthSeverity"],
            backup_age_critical_multiplier=document["BackupAgeCriticalMultiplier"],
            disk_free_critical_percent=document["DiskFreeCriticalPercent"],
            failed_jobs_warning_count=document["FailedJobsWarningCount"],
            failed_jobs_critical_count=document["FailedJobsCriticalCount"],
            blocking_warning_count=document["BlockingWarningCount"],
            blocking_critical_count=document["BlockingCriticalCount"],
            long_running_query_critical_multiplier=document["LongRunningQueryCriticalMultiplier"],
        )


def load_evaluation_config(path: str | Path) -> EvaluationConfig:
    """Load an explicitly selected external JSON policy without echoing local paths."""
    try:
        with Path(path).open("r", encoding="utf-8-sig") as stream:
            return EvaluationConfig.from_mapping(json.load(stream))
    except ValueError:
        raise
    except Exception:
        raise ValueError("Monitoring evaluation configuration could not be loaded.") from None


@dataclass(frozen=True)
class HealthEvaluation:
    check_name: str
    sql_instance: str
    collection_status: str
    severity: str
    reason_code: str
    reason: str
    timestamp_utc: str
    measured_values: Mapping[str, Any] = field(default_factory=dict)
    original_result: Mapping[str, Any] = field(default_factory=dict)
    error: Mapping[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OverallEvaluation:
    sql_instance: str
    timestamp_utc: str
    severity: str
    reason_code: str
    reason: str
    checks: tuple[HealthEvaluation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sql_instance": self.sql_instance,
            "timestamp_utc": self.timestamp_utc,
            "severity": self.severity,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "checks": [check.to_dict() for check in self.checks],
        }


def _field(row: Mapping[str, Any], name: str, default: Any = None) -> Any:
    return next((value for key, value in row.items() if str(key).casefold() == name.casefold()), default)


def _number(value: Any) -> float | None:
    if type(value) not in {int, float} or not math.isfinite(value):
        return None
    return float(value)


def _rows(check: MonitoringCheck) -> list[Mapping[str, Any]] | None:
    if isinstance(check.data, list) and all(isinstance(row, Mapping) for row in check.data):
        return check.data
    if isinstance(check.data, Mapping):
        return [check.data]
    return None


def _result(check: MonitoringCheck, severity: str, code: str, reason: str, values: Mapping[str, Any] | None = None) -> HealthEvaluation:
    return HealthEvaluation(
        check_name=check.check_name,
        sql_instance=check.sql_instance,
        collection_status=check.status.value,
        severity=severity,
        reason_code=code,
        reason=reason,
        timestamp_utc=check.timestamp_utc,
        measured_values=dict(values or {}),
        original_result=check.to_dict(),
        error=asdict(check.error) if check.error is not None else None,
    )


def evaluate_check(check: MonitoringCheck, config: EvaluationConfig) -> HealthEvaluation:
    """Evaluate one normalized check with explicit rules and no model-generated judgment."""
    if not isinstance(check, MonitoringCheck) or check.check_name not in MONITORING_TOOLS:
        raise ValueError("Unknown or malformed monitoring result.")
    if check.status == MonitoringStatus.FAILED:
        category = check.error.category if check.error else "UNKNOWN"
        return _result(check, "UNKNOWN", "COLLECTION_FAILED", f"Collection failed ({category}); health is unknown.")

    rows = _rows(check)
    if rows is None:
        return _result(check, "UNKNOWN", "INVALID_DATA", "Collected data does not match a supported structured result shape.")
    if check.status == MonitoringStatus.EMPTY or not rows:
        if check.check_name in {"failed_jobs", "blocking_sessions", "long_running_queries"}:
            return _result(check, "HEALTHY", "NO_ACTIVE_FINDINGS", "No matching incidents were returned by this check.", {"item_count": 0})
        return _result(check, "UNKNOWN", "NO_DATA", "No records were returned; health cannot be determined.", {"item_count": 0})

    name = check.check_name
    if name == "database_inventory":
        decisions = []
        states = []
        inaccessible = False
        for row in rows:
            state = _field(row, "Status")
            accessible = _field(row, "IsAccessible")
            if isinstance(state, str):
                states.append(state)
            if accessible is False:
                inaccessible = True
                decisions.append("CRITICAL")
            elif accessible is True and isinstance(state, str):
                decisions.append(config.database_state_severity.get(state.casefold(), "UNKNOWN"))
            else:
                decisions.append("UNKNOWN")
        worst = max(decisions, key=lambda value: _SEVERITY_RANK[value])
        if worst == "CRITICAL":
            return _result(check, worst, "DATABASE_UNAVAILABLE", "A database is inaccessible or has a configured critical state.", {"database_count": len(rows), "states": states, "inaccessible": inaccessible})
        if worst == "WARNING":
            return _result(check, worst, "DATABASE_STATE_WARNING", "A database has a state configured as WARNING.", {"database_count": len(rows), "states": states})
        if worst == "UNKNOWN":
            return _result(check, worst, "DATABASE_STATE_UNMAPPED", "At least one database state is missing or unmapped in evaluation configuration.", {"database_count": len(rows), "states": states})
        return _result(check, "HEALTHY", "DATABASES_AVAILABLE", "All returned databases are accessible and in configured healthy states.", {"database_count": len(rows), "states": states})

    if name == "database_health":
        evaluated = [config.database_health_severity.get(str(_field(row, "HealthStatus", "")).casefold(), "UNKNOWN") for row in rows]
        worst = max(evaluated, key=lambda value: _SEVERITY_RANK[value])
        code_reason = {
            "CRITICAL": ("DATABASE_HEALTH_CRITICAL", "Database health results include a configured CRITICAL state."),
            "WARNING": ("DATABASE_HEALTH_WARNING", "Database health results include a configured WARNING state."),
            "UNKNOWN": ("DATABASE_HEALTH_UNMAPPED", "A database health state is missing or unmapped in evaluation configuration."),
            "HEALTHY": ("DATABASE_HEALTHY", "All returned database health states are configured as healthy."),
        }
        code, reason = code_reason[worst]
        return _result(check, worst, code, reason, {"database_count": len(rows), "health_states": [str(_field(row, "HealthStatus", "Unknown")) for row in rows]})

    if name == "database_size":
        sizes = [_number(_field(row, "TotalSizeMB", _field(row, "SizeMB"))) for row in rows]
        known_sizes = [value for value in sizes if value is not None]
        if not known_sizes:
            return _result(check, "UNKNOWN", "SIZE_METRIC_MISSING", "No supported database size measurement was present.", {"database_count": len(rows)})
        return _result(check, "INFO", "SIZE_MEASURED", "Database size measurements were collected; no size severity threshold is configured.", {"database_count": len(rows), "total_size_mb": round(sum(known_sizes), 2)})

    if name == "backup_status":
        ages, thresholds, severities = [], [], []
        for row in rows:
            age = _number(_field(row, "HoursSinceLatest"))
            threshold = _number(_field(row, "ThresholdHours"))
            breach = _field(row, "ExceedsThreshold")
            latest_type = _field(row, "LatestBackupType")
            if threshold is not None and type(breach) is bool and breach and isinstance(latest_type, str) and latest_type.casefold() == "none":
                # Ops explicitly marks no recorded backup as exceeding its configured SLA.
                severities.append("WARNING")
            elif age is None or threshold is None or threshold <= 0 or age < 0 or type(breach) is not bool:
                severities.append("UNKNOWN")
            elif age >= threshold * config.backup_age_critical_multiplier:
                severities.append("CRITICAL")
            elif (age > threshold) != breach:
                severities.append("UNKNOWN")
            elif breach:
                severities.append("WARNING")
            else:
                severities.append("HEALTHY")
            ages.append(age)
            thresholds.append(threshold)
        worst = max(severities, key=lambda value: _SEVERITY_RANK[value])
        details = {
            "maximum_backup_age_hours": max((age for age in ages if age is not None), default=None),
            "configured_threshold_hours": max((threshold for threshold in thresholds if threshold is not None), default=None),
            "database_count": len(rows),
        }
        mapping = {
            "CRITICAL": ("BACKUP_AGE_CRITICAL", "Backup age reached the configured critical multiplier."),
            "WARNING": ("BACKUP_AGE_WARNING", "Backup age exceeds a database's configured warning threshold."),
            "UNKNOWN": ("BACKUP_AGE_UNAVAILABLE", "Backup age or its configured threshold is missing."),
            "HEALTHY": ("BACKUP_AGE_WITHIN_THRESHOLD", "Returned backup ages are within configured thresholds."),
        }
        code, reason = mapping[worst]
        return _result(check, worst, code, reason, details)

    if name == "disk_space":
        percentages, warning_thresholds, severities = [], [], []
        inconsistent_policy = False
        for row in rows:
            free_percent = _number(_field(row, "PercentFree"))
            warning_percent = _number(_field(row, "ThresholdPercent"))
            percentages.append(free_percent)
            warning_thresholds.append(warning_percent)
            if (
                free_percent is None or warning_percent is None
                or not 0 <= free_percent <= 100
                or not 0 < warning_percent <= 100
            ):
                severities.append("UNKNOWN")
            elif config.disk_free_critical_percent >= warning_percent:
                inconsistent_policy = True
                severities.append("UNKNOWN")
            elif free_percent <= config.disk_free_critical_percent:
                severities.append("CRITICAL")
            elif free_percent < warning_percent:
                severities.append("WARNING")
            else:
                severities.append("HEALTHY")
        worst = max(severities, key=lambda value: _SEVERITY_RANK[value])
        mapping = {
            "CRITICAL": ("DISK_FREE_CRITICAL", "Disk free space is at or below the configured critical threshold."),
            "WARNING": ("DISK_FREE_WARNING", "Disk free space is below the configured warning threshold."),
            "UNKNOWN": (
                "DISK_POLICY_INVALID" if inconsistent_policy else "DISK_MEASUREMENT_MISSING",
                "Critical disk threshold must be below the Ops warning threshold." if inconsistent_policy else "Disk free percentage or warning threshold is missing.",
            ),
            "HEALTHY": ("DISK_FREE_WITHIN_THRESHOLD", "Returned disk free percentages meet configured thresholds."),
        }
        code, reason = mapping[worst]
        return _result(check, worst, code, reason, {"minimum_percent_free": min((value for value in percentages if value is not None), default=None), "warning_threshold_percent": max((value for value in warning_thresholds if value is not None), default=None), "critical_threshold_percent": config.disk_free_critical_percent, "volume_count": len(rows)})

    if name in {"failed_jobs", "blocking_sessions"}:
        if name == "failed_jobs" and any(not isinstance(_field(row, "Status"), str) for row in rows):
            return _result(check, "UNKNOWN", "FAILED_JOB_RESULT_INVALID", "A failed-job row is missing its status field.", {"row_count": len(rows)})
        if name == "failed_jobs" and any(str(_field(row, "Status")).casefold() != "failed" for row in rows):
            return _result(check, "UNKNOWN", "FAILED_JOB_RESULT_INVALID", "A failed-job row has an unexpected status value.", {"row_count": len(rows)})
        count = sum(1 for row in rows if name != "failed_jobs" or str(_field(row, "Status")).casefold() == "failed")
        if name == "failed_jobs":
            warn, critical = config.failed_jobs_warning_count, config.failed_jobs_critical_count
            label = "failed SQL Agent jobs"
            code_prefix = "FAILED_JOBS"
        else:
            warn, critical = config.blocking_warning_count, config.blocking_critical_count
            label = "blocking sessions"
            code_prefix = "BLOCKING_SESSIONS"
        severity = "CRITICAL" if count >= critical else "WARNING" if count >= warn else "HEALTHY"
        reasons = {
            "CRITICAL": (f"{code_prefix}_CRITICAL", f"The {label} count reached the configured critical threshold."),
            "WARNING": (f"{code_prefix}_WARNING", f"The {label} count reached the configured warning threshold."),
            "HEALTHY": (f"{code_prefix}_CLEAR", f"The {label} count is below the configured warning threshold."),
        }
        code, reason = reasons[severity]
        return _result(check, severity, code, reason, {"item_count": count, "warning_threshold": warn, "critical_threshold": critical})

    if name == "long_running_queries":
        elapsed_values, thresholds, severities = [], [], []
        for row in rows:
            elapsed = _number(_field(row, "ElapsedSeconds"))
            threshold = _number(_field(row, "ThresholdSeconds"))
            elapsed_values.append(elapsed)
            thresholds.append(threshold)
            if elapsed is None or threshold is None or elapsed < 0 or threshold <= 0:
                severities.append("UNKNOWN")
            elif elapsed >= threshold * config.long_running_query_critical_multiplier:
                severities.append("CRITICAL")
            elif elapsed < threshold:
                severities.append("UNKNOWN")
            else:
                severities.append("WARNING")
        worst = max(severities, key=lambda value: _SEVERITY_RANK[value])
        mapping = {
            "CRITICAL": ("LONG_QUERY_CRITICAL", "A query duration reached the configured critical multiplier."),
            "WARNING": ("LONG_QUERY_WARNING", "One or more queries meet the configured long-running threshold."),
            "UNKNOWN": ("LONG_QUERY_MEASUREMENT_MISSING", "Query duration or configured threshold is missing."),
        }
        code, reason = mapping[worst]
        return _result(check, worst, code, reason, {"query_count": len(rows), "maximum_elapsed_seconds": max((value for value in elapsed_values if value is not None), default=None), "threshold_seconds": max((value for value in thresholds if value is not None), default=None)})

    raise ValueError("Monitoring category is not supported.")


def evaluate_snapshot(
    checks: tuple[MonitoringCheck, ...] | list[MonitoringCheck],
    config: EvaluationConfig,
    *,
    evaluated_at: datetime | None = None,
) -> OverallEvaluation:
    if not checks:
        raise ValueError("At least one monitoring check is required.")
    if not all(isinstance(check, MonitoringCheck) for check in checks):
        raise ValueError("Monitoring snapshot contains an invalid result.")
    instances = {check.sql_instance for check in checks}
    if len(instances) != 1:
        raise ValueError("Monitoring checks must target one SQL instance.")
    evaluated = tuple(evaluate_check(check, config) for check in checks)
    severity = max((item.severity for item in evaluated), key=lambda value: _SEVERITY_RANK[value])
    timestamp = evaluated_at or datetime.now(timezone.utc)
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
        raise ValueError("Overall evaluation timestamp must be UTC.")
    reasons = {
        "CRITICAL": ("OVERALL_CRITICAL", "At least one check is CRITICAL."),
        "WARNING": ("OVERALL_WARNING", "No CRITICAL check exists and at least one check is WARNING."),
        "INFO": ("OVERALL_INFO", "No WARNING or CRITICAL check exists and at least one check is INFO."),
        "UNKNOWN": ("OVERALL_UNKNOWN", "No higher-severity result exists and at least one check is UNKNOWN."),
        "HEALTHY": ("OVERALL_HEALTHY", "All evaluated checks are HEALTHY."),
    }
    code, reason = reasons[severity]
    return OverallEvaluation(next(iter(instances)), timestamp.isoformat(), severity, code, reason, evaluated)
