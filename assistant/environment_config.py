"""Strict loading and environment checks for the non-secret Ops configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any


SUPPORTED_ENVIRONMENTS = frozenset({"Development", "Test", "UAT", "Production", "Lab"})
BACKUP_TYPES = frozenset({"Full", "Differential", "Log"})
PROTECTED_DATABASES = frozenset({"master", "model", "msdb", "tempdb"})
_ENVIRONMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SQL_NAME = re.compile(r"^[A-Za-z0-9_.\\,:-]{1,256}$")
_SECRET_ASSIGNMENT = re.compile(r"(?i)\b(?:password|passwd|pwd|token|secret|client_secret|access_token|connectionstring)\s*[:=]")
_PRODUCTION_PLACEHOLDER = re.compile(
    r"(?i)(?:replace[-_ ]?with|example\.(?:com|org|net|invalid)|domain\.local|mssql-vm1|localhost|"
    r"(?:^|[\\/])desktop(?:[\\/]|$)|^\.$|^PROD-SQL-(?:\d+|AOAG)$|AppProductionDB|"
    r"prod-backup-share)"
)
_CONFIG_FIELDS = {
    "$schema", "Environment", "EnvironmentId", "ApprovedInstances", "DefaultInstance",
    "TrustServerCertificate", "BackupDirectory", "ReportDirectory", "LogDirectory",
    "Thresholds", "Timeouts", "ApprovedDatabases", "ProtectedDatabases", "BackupPolicy",
}


def _valid_path(value: Any, *, production: bool) -> bool:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        return False
    if any(char in value for char in '<>"|?*') or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value):
        return False
    if production and not Path(value).is_absolute():
        return False
    return True


def load_automation_configuration(
    config_path: str | Path | None = None,
    *,
    project_root: str | Path,
    expected_environment: str | None = None,
) -> dict[str, Any]:
    """Load a complete config only from an explicitly selected file.

    Any details are intentionally collapsed to one safe error at callers; this
    helper itself does not log configuration values or file contents.
    """
    selected = config_path if config_path is not None else os.environ.get("DBA_AUTOMATION_CONFIG")
    try:
        if not isinstance(selected, (str, Path)) or not str(selected).strip():
            raise ValueError
        path = Path(selected)
        if not path.is_absolute():
            path = Path(project_root) / path
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(document, dict) or set(document) - _CONFIG_FIELDS:
            raise ValueError
        if _SECRET_ASSIGNMENT.search(json.dumps(document, ensure_ascii=True)):
            raise ValueError
        required = _CONFIG_FIELDS - {"$schema"}
        if not required.issubset(document):
            raise ValueError
        environment = document["Environment"]
        environment_id = document["EnvironmentId"]
        if environment not in SUPPORTED_ENVIRONMENTS or not isinstance(environment_id, str) or not _ENVIRONMENT_ID.fullmatch(environment_id):
            raise ValueError
        selected_environment = expected_environment or os.environ.get("DBA_AUTOMATION_ENV")
        if selected_environment is not None and selected_environment != environment:
            raise ValueError

        instances = document["ApprovedInstances"]
        default = document["DefaultInstance"]
        databases = document["ApprovedDatabases"]
        protected = document["ProtectedDatabases"]
        if (
            not isinstance(instances, list) or not instances
            or any(not isinstance(item, str) or not _SQL_NAME.fullmatch(item) for item in instances)
            or len(set(instances)) != len(instances)
            or not isinstance(default, str) or default.casefold() not in {name.casefold() for name in instances}
            or not isinstance(databases, list) or not databases
            or any(not isinstance(item, str) or not item.strip() for item in databases)
            or any(item.casefold() in PROTECTED_DATABASES for item in databases)
            or not isinstance(protected, list)
            or not PROTECTED_DATABASES.issubset({str(item).casefold() for item in protected})
        ):
            raise ValueError
        trust = document["TrustServerCertificate"]
        if type(trust) is not bool or (trust and environment not in {"Development", "Lab"}):
            raise ValueError

        policy = document["BackupPolicy"]
        if (
            not isinstance(policy, dict)
            or set(policy) != {"Enabled", "AllowedTypes", "ApprovalRequired"}
            or type(policy["Enabled"]) is not bool
            or type(policy["ApprovalRequired"]) is not bool
            or policy["ApprovalRequired"] is not True
            or not isinstance(policy["AllowedTypes"], list)
            or len(set(policy["AllowedTypes"])) != len(policy["AllowedTypes"])
            or any(item not in BACKUP_TYPES for item in policy["AllowedTypes"])
            or (policy["Enabled"] and not policy["AllowedTypes"])
        ):
            raise ValueError

        for name in ("BackupDirectory", "ReportDirectory", "LogDirectory"):
            if not _valid_path(document[name], production=environment == "Production"):
                raise ValueError

        if environment == "Production":
            production_values = [*instances, default, *databases, document["BackupDirectory"], document["ReportDirectory"], document["LogDirectory"]]
            if trust or any(_PRODUCTION_PLACEHOLDER.search(value) for value in production_values):
                raise ValueError

        # Validate required policy and timeout/threshold sections without
        # accepting unknown nested properties or secret-like additions.
        thresholds = document["Thresholds"]
        timeouts = document["Timeouts"]
        if (
            not isinstance(thresholds, dict)
            or set(thresholds) != {"BackupWarningHours", "DiskWarningPercentFree", "LongRunningQuerySeconds", "FailedJobsHours"}
            or any(type(value) not in {int, float} or value <= 0 for value in thresholds.values())
            or not 1 <= thresholds["BackupWarningHours"] <= 8760
            or not 0 < thresholds["DiskWarningPercentFree"] <= 100
            or not 1 <= thresholds["LongRunningQuerySeconds"] <= 86400
            or not 1 <= thresholds["FailedJobsHours"] <= 720
            or not isinstance(timeouts, dict)
            or set(timeouts) != {"QueryTimeoutSeconds", "ConnectionTimeoutSeconds"}
            or any(type(value) is not int or value <= 0 for value in timeouts.values())
            or timeouts["QueryTimeoutSeconds"] > 86400
            or timeouts["ConnectionTimeoutSeconds"] > 3600
        ):
            raise ValueError
        return document
    except Exception:
        raise ValueError("DBA automation configuration is invalid or unavailable.") from None
