"""Configured orchestration for collect → evaluate → report → persist."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping
from uuid import uuid4

from assistant.audit import AuditEvent
from assistant.response import sanitize_untrusted_text

from .evaluation import EvaluationConfig, OverallEvaluation, evaluate_check, evaluate_snapshot, load_evaluation_config
from .reporting import DbaHealthReport, build_health_report
from .service import MONITORING_TOOLS, MonitoringService


_WORKFLOW_CONFIG_FIELDS = {
    "SchemaVersion", "Environment", "EnvironmentId", "SqlInstance", "AllowedSqlInstances", "EvaluationPolicyPath",
    "ReportOutputDirectory", "EnabledChecks", "ArtifactFormats",
}
_FORMATS = {"text", "html", "json"}
_NAME = re.compile(r"^[a-zA-Z0-9_.\\,:-]{1,256}$")
_ENVIRONMENTS = {"Development", "Test", "UAT", "Production", "Lab"}
_ENVIRONMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PRODUCTION_PLACEHOLDER = re.compile(r"(?i)replace[-_ ]?with|example\.(?:com|org|net|invalid)|domain\.local|mssql-vm1|localhost|PROD-SQL-(?:\d+|AOAG)")


class ArtifactPersistenceError(OSError):
    """Sanitized persistence failure retaining only already-created locations."""

    def __init__(self, locations: Mapping[str, str]) -> None:
        super().__init__("Monitoring report artifact persistence failed.")
        self.locations = dict(locations)


@dataclass(frozen=True)
class MonitoringWorkflowConfig:
    environment: str
    environment_id: str
    sql_instance: str
    evaluation_policy_path: Path
    report_output_directory: Path
    enabled_checks: tuple[str, ...]
    artifact_formats: tuple[str, ...]

    @classmethod
    def load(cls, path: str | Path) -> tuple["MonitoringWorkflowConfig", EvaluationConfig]:
        """Load the exact non-secret workflow contract and referenced policy."""
        try:
            config_path = Path(path).resolve(strict=True)
            document = json.loads(config_path.read_text(encoding="utf-8-sig"))
            if (
                not isinstance(document, dict)
                or (set(document) != _WORKFLOW_CONFIG_FIELDS and set(document) != _WORKFLOW_CONFIG_FIELDS | {"$schema"})
                or ("$schema" in document and not isinstance(document["$schema"], str))
                or type(document["SchemaVersion"]) is not int
                or document["SchemaVersion"] != 1
                or document["Environment"] not in _ENVIRONMENTS
                or not isinstance(document["EnvironmentId"], str)
                or not _ENVIRONMENT_ID.fullmatch(document["EnvironmentId"])
            ):
                raise ValueError
            expected_environment = os.environ.get("DBA_AUTOMATION_ENV")
            if expected_environment is not None and expected_environment != document["Environment"]:
                raise ValueError
            instance = document["SqlInstance"]
            allowlist = document["AllowedSqlInstances"]
            checks = document["EnabledChecks"]
            formats = document["ArtifactFormats"]
            if (
                not isinstance(instance, str) or not _NAME.fullmatch(instance)
                or not isinstance(allowlist, list) or not allowlist
                or any(not isinstance(name, str) or not _NAME.fullmatch(name) for name in allowlist)
                or len(set(allowlist)) != len(allowlist) or instance not in allowlist
                or not isinstance(checks, list) or not checks
                or any(not isinstance(name, str) or name not in MONITORING_TOOLS for name in checks)
                or len(set(checks)) != len(checks)
                or not isinstance(formats, list) or not formats
                or any(not isinstance(item, str) or item not in _FORMATS for item in formats)
                or len(set(formats)) != len(formats)
                or not isinstance(document["EvaluationPolicyPath"], str) or not document["EvaluationPolicyPath"]
                or not isinstance(document["ReportOutputDirectory"], str) or not document["ReportOutputDirectory"]
                or any(any(ord(char) < 32 for char in value) or any(char in value for char in '<>"|?*') for value in (document["EvaluationPolicyPath"], document["ReportOutputDirectory"]))
            ):
                raise ValueError
            if document["Environment"] == "Production":
                targets = [instance, *allowlist, document["ReportOutputDirectory"]]
                if any(_PRODUCTION_PLACEHOLDER.search(target) for target in targets):
                    raise ValueError
                if not Path(document["ReportOutputDirectory"]).is_absolute():
                    raise ValueError
            policy_path = (config_path.parent / document["EvaluationPolicyPath"]).resolve()
            output_path = (config_path.parent / document["ReportOutputDirectory"]).resolve()
            try:
                policy_path.relative_to(config_path.parent)
            except ValueError:
                raise ValueError from None
            if output_path == config_path.parent or policy_path == output_path:
                raise ValueError
            config = cls(
                document["Environment"], document["EnvironmentId"], instance,
                policy_path, output_path, tuple(checks), tuple(formats),
            )
            policy = load_evaluation_config(policy_path)
            return config, policy
        except Exception:
            raise ValueError("Monitoring workflow configuration is invalid or unavailable.") from None


@dataclass(frozen=True)
class WorkflowFailure:
    stage: str
    category: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"stage": self.stage, "category": self.category, "message": self.message}


@dataclass(frozen=True)
class MonitoringWorkflowResult:
    workflow_id: str
    timestamp_utc: str
    sql_instance: str | None
    overall_severity: str
    check_count: int
    collection_failure_count: int
    healthy_count: int
    info_count: int
    warning_count: int
    critical_count: int
    unknown_count: int
    artifact_locations: Mapping[str, str]
    workflow_status: str
    failure: WorkflowFailure | None
    audit_events: tuple[AuditEvent, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "timestamp_utc": self.timestamp_utc,
            "sql_instance": self.sql_instance,
            "overall_severity": self.overall_severity,
            "check_count": self.check_count,
            "collection_failure_count": self.collection_failure_count,
            "healthy_count": self.healthy_count,
            "info_count": self.info_count,
            "warning_count": self.warning_count,
            "critical_count": self.critical_count,
            "unknown_count": self.unknown_count,
            "artifact_locations": dict(self.artifact_locations),
            "workflow_status": self.workflow_status,
            "failure": self.failure.to_dict() if self.failure else None,
            "audit_events": [event.to_dict() for event in self.audit_events],
        }


def _audit_event(
    workflow_id: str,
    timestamp: str,
    *,
    tool_name: str | None = None,
    sql_instance: str | None = None,
    failed: bool = False,
    failure_category: str | None = None,
) -> AuditEvent:
    return AuditEvent(
        tool_name=tool_name,
        classification="READ_ONLY",
        approval_required=False,
        approval_outcome="NOT_REQUIRED",
        execution_attempted=tool_name is not None,
        execution_result=("FAILURE" if failed else "SUCCESS") if tool_name is not None else "NOT_ATTEMPTED",
        failure_category="MCP" if failed else failure_category,
        sql_instance=sql_instance,
        request_id=workflow_id,
        timestamp_utc=timestamp,
    )


def _write_artifacts(report: DbaHealthReport, config: MonitoringWorkflowConfig, workflow_id: str, timestamp: datetime) -> dict[str, str]:
    stamp = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
    content = {
        "text": report.to_text(),
        "html": report.to_html(),
        "json": report.to_json(),
    }
    extensions = {"text": "txt", "html": "html", "json": "json"}
    created: dict[str, str] = {}
    try:
        config.report_output_directory.mkdir(parents=True, exist_ok=True)
        for fmt in config.artifact_formats:
            location = config.report_output_directory / f"dba-health-{stamp}-{workflow_id}.{extensions[fmt]}"
            # Exclusive creation prevents a repeated run or collision from overwriting an artifact.
            with location.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content[fmt])
                stream.flush()
            created[fmt] = str(location)
    except Exception:
        raise ArtifactPersistenceError(created) from None
    return created


class MonitoringWorkflow:
    """Run the existing collector, deterministic evaluator and report builder."""

    def __init__(
        self,
        *,
        service: MonitoringService | None = None,
        clock: Callable[[], datetime] | None = None,
        config_loader: Callable[[str | Path], tuple[MonitoringWorkflowConfig, EvaluationConfig]] = MonitoringWorkflowConfig.load,
        report_builder: Callable[..., DbaHealthReport] = build_health_report,
        artifact_writer: Callable[..., dict[str, str]] = _write_artifacts,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._service = service or MonitoringService(clock=clock)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._config_loader = config_loader
        self._report_builder = report_builder
        self._artifact_writer = artifact_writer
        self._id_factory = id_factory or (lambda: uuid4().hex)

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("Workflow clock must return an aware UTC timestamp.")
        return value

    def _failed(
        self, workflow_id: str, timestamp: datetime, stage: str, instance: str | None,
        *, category: str = "WORKFLOW", message: str = "The monitoring workflow failed; details were withheld.",
        audit: tuple[AuditEvent, ...] = (), artifacts: Mapping[str, str] | None = None,
        checks: tuple[Any, ...] = (), evaluations: tuple[Any, ...] = (),
        overall: OverallEvaluation | None = None, report: DbaHealthReport | None = None,
    ) -> MonitoringWorkflowResult:
        failure = WorkflowFailure(stage, category, sanitize_untrusted_text(message))
        audit_category = category if category in {"VALIDATION", "APPROVAL", "MCP", "RESPONSE"} else "RESPONSE"
        event = _audit_event(
            workflow_id, timestamp.isoformat(), sql_instance=instance,
            failure_category=audit_category,
        )
        counts = dict(report.summary_by_severity) if report is not None else {
            severity: sum(item.severity == severity for item in evaluations)
            for severity in ("HEALTHY", "INFO", "WARNING", "CRITICAL", "UNKNOWN")
        }
        check_count = len(report.checks) if report is not None else len(checks or evaluations)
        collection_failures = (
            sum(item.collection_status == "FAILED" for item in report.checks)
            if report is not None else sum(getattr(item, "status", None).value == "FAILED" for item in checks)
        )
        if report is None and not evaluations and checks:
            counts["UNKNOWN"] = len(checks)
        return MonitoringWorkflowResult(
            workflow_id, timestamp.isoformat(), instance,
            report.overall_severity if report is not None else overall.severity if overall is not None else "UNKNOWN",
            check_count, collection_failures, counts.get("HEALTHY", 0), counts.get("INFO", 0),
            counts.get("WARNING", 0), counts.get("CRITICAL", 0), counts.get("UNKNOWN", 0),
            dict(artifacts or {}), "FAILED", failure, (*audit, event),
        )

    async def run(self, session: Any, config_path: str | Path) -> MonitoringWorkflowResult:
        workflow_id = self._id_factory()
        if not isinstance(workflow_id, str) or not re.fullmatch(r"[0-9a-f]{32}", workflow_id):
            workflow_id = uuid4().hex
        try:
            timestamp = self._now()
        except Exception:
            timestamp = datetime.now(timezone.utc)
            return self._failed(workflow_id, timestamp, "workflow", None)

        try:
            config, evaluation_config = self._config_loader(config_path)
        except Exception:
            return self._failed(workflow_id, timestamp, "configuration", None, category="VALIDATION", message="Monitoring workflow configuration is invalid or unavailable.")

        try:
            checks = await self._service.collect(
                session, sql_instance=config.sql_instance, check_names=config.enabled_checks,
            )
            audit = tuple(
                _audit_event(
                    workflow_id, timestamp.isoformat(), tool_name=check.tool_name,
                    sql_instance=config.sql_instance, failed=check.status.value == "FAILED",
                )
                for check in checks
            )
        except Exception:
            return self._failed(workflow_id, timestamp, "monitoring_collection", config.sql_instance, category="MCP")

        try:
            evaluations = tuple(evaluate_check(check, evaluation_config) for check in checks)
            overall = evaluate_snapshot(checks, evaluation_config, evaluated_at=timestamp)
        except Exception:
            return self._failed(
                workflow_id, timestamp, "evaluation", config.sql_instance,
                category="VALIDATION", audit=audit, checks=checks,
            )

        try:
            report = self._report_builder(
                checks, evaluations, overall, report_id=workflow_id, generated_at=timestamp,
            )
        except Exception:
            return self._failed(
                workflow_id, timestamp, "report_generation", config.sql_instance,
                category="RESPONSE", audit=audit, checks=checks,
                evaluations=evaluations, overall=overall,
            )

        try:
            artifacts = self._artifact_writer(report, config, workflow_id, timestamp)
        except Exception as exc:
            return self._failed(
                workflow_id, timestamp, "artifact_persistence", config.sql_instance,
                category="WORKFLOW", audit=audit,
                artifacts=getattr(exc, "locations", {}),
                report=report,
            )

        counts = report.summary_by_severity
        status = {"HEALTHY": "SUCCESS", "INFO": "SUCCESS", "WARNING": "WARNING", "CRITICAL": "CRITICAL", "UNKNOWN": "UNKNOWN"}[report.overall_severity]
        collection_failures = sum(check.collection_status == "FAILED" for check in report.checks)
        if collection_failures and status == "SUCCESS":
            status = "UNKNOWN"
        return MonitoringWorkflowResult(
            workflow_id=workflow_id,
            timestamp_utc=timestamp.isoformat(),
            sql_instance=config.sql_instance,
            overall_severity=report.overall_severity,
            check_count=len(report.checks),
            collection_failure_count=collection_failures,
            healthy_count=counts.get("HEALTHY", 0),
            info_count=counts.get("INFO", 0),
            warning_count=counts.get("WARNING", 0),
            critical_count=counts.get("CRITICAL", 0),
            unknown_count=counts.get("UNKNOWN", 0),
            artifact_locations=artifacts,
            workflow_status=status,
            failure=None,
            audit_events=audit,
        )
