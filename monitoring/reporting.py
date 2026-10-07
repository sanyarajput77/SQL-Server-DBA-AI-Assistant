"""Deterministic, in-memory DBA health report generation."""

from dataclasses import dataclass
from datetime import datetime, timezone
import html
import json
import math
import re
from typing import Any, Mapping, Sequence
from uuid import uuid4

from assistant.response import sanitize_untrusted_text

from .evaluation import HealthEvaluation, OverallEvaluation
from .model import MonitoringCheck


_SEVERITIES = ("HEALTHY", "INFO", "WARNING", "CRITICAL", "UNKNOWN")
_REPORT_ID = re.compile(r"^[0-9a-f]{32}$")
_SENSITIVE_FIELDS = {
    "password", "passwd", "pwd", "token", "secret", "clientsecret", "accesstoken",
    "connectionstring", "statementtext", "querytext", "query", "sqltext", "commandtext", "blockedquery", "blockerquery",
    "errormessage", "physicalpath", "reportpath", "outputpath", "backuppath",
    "backupfiles", "backupdirectory", "blockedlogin", "blockerlogin", "loginname",
    "blockedhost", "blockerhost", "hostname",
}


def _safe_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        safe = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized in _SENSITIVE_FIELDS or normalized.endswith("path"):
                continue
            safe[str(key)] = _safe_data(item)
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_data(item) for item in value]
    if isinstance(value, str):
        return sanitize_untrusted_text(value)
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError("Report data contains an unsupported value.")


@dataclass(frozen=True)
class ReportCheck:
    """One check with observed data, policy evaluation, and collection error separated."""

    check_name: str
    tool_name: str
    timestamp_utc: str
    collection_status: str
    observed_data: Any
    evaluation_severity: str
    reason_code: str
    explanation: str
    measured_values: Any
    error: Mapping[str, str] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_name": self.check_name,
            "tool_name": self.tool_name,
            "timestamp_utc": self.timestamp_utc,
            "collection_status": self.collection_status,
            "observed_data": _safe_data(self.observed_data),
            "evaluation": {
                "severity": self.evaluation_severity,
                "reason_code": self.reason_code,
                "explanation": sanitize_untrusted_text(self.explanation),
                "measured_values": _safe_data(self.measured_values),
            },
            "error": _safe_data(self.error) if self.error is not None else None,
        }


@dataclass(frozen=True)
class DbaHealthReport:
    report_id: str
    generated_at_utc: str
    sql_instance: str
    overall_severity: str
    overall_reason_code: str
    overall_explanation: str
    summary_by_severity: Mapping[str, int]
    checks: tuple[ReportCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "generated_at_utc": self.generated_at_utc,
            "sql_instance": sanitize_untrusted_text(self.sql_instance),
            "overall": {
                "severity": self.overall_severity,
                "reason_code": self.overall_reason_code,
                "explanation": sanitize_untrusted_text(self.overall_explanation),
            },
            "summary_by_severity": dict(self.summary_by_severity),
            "checks": [check.to_dict() for check in self.checks],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)

    def to_text(self) -> str:
        details = [
            "DBA Health Report",
            f"Report ID: {self.report_id}",
            f"Instance: {sanitize_untrusted_text(self.sql_instance)}",
            f"Generated (UTC): {self.generated_at_utc}",
            f"Overall severity: {self.overall_severity}",
            f"Overall reason: {self.overall_reason_code} — {sanitize_untrusted_text(self.overall_explanation)}",
            f"Executive summary: {sanitize_untrusted_text(self.overall_explanation)}",
            "Summary by severity: " + ", ".join(f"{severity}={self.summary_by_severity[severity]}" for severity in _SEVERITIES),
        ]
        failed = [check for check in self.checks if check.collection_status == "FAILED"]
        unknown = [check for check in self.checks if check.evaluation_severity == "UNKNOWN"]
        details.append("Collection failures: " + (", ".join(item.check_name for item in failed) if failed else "None"))
        details.append("UNKNOWN checks: " + (", ".join(item.check_name for item in unknown) if unknown else "None"))
        for check in self.checks:
            details.extend([
                "",
                f"[{check.evaluation_severity}] {check.check_name} ({check.tool_name})",
                f"Collection status: {check.collection_status}",
                "Observed values: " + json.dumps(_safe_data(check.observed_data), ensure_ascii=False, sort_keys=True),
                f"Evaluation: {check.reason_code} — {sanitize_untrusted_text(check.explanation)}",
                "Evaluation measurements: " + json.dumps(_safe_data(check.measured_values), ensure_ascii=False, sort_keys=True),
            ])
            if check.error is not None:
                safe_error = _safe_data(check.error)
                details.append(f"Error: {safe_error.get('category', 'UNKNOWN')} — {safe_error.get('message', 'Details withheld.')}")
        return "\n".join(details)

    def to_html(self) -> str:
        summary_rows = "".join(
            f"<li><strong>{severity}</strong>: {self.summary_by_severity[severity]}</li>"
            for severity in _SEVERITIES
        )
        check_sections = []
        for check in self.checks:
            observed = json.dumps(_safe_data(check.observed_data), ensure_ascii=False, sort_keys=True, indent=2)
            error_html = ""
            if check.error is not None:
                error_data = _safe_data(check.error)
                error_html = (
                    "<p class=\"error\"><strong>Collection error:</strong> "
                    f"{html.escape(str(error_data.get('category', 'UNKNOWN')))} — "
                    f"{html.escape(str(error_data.get('message', 'Details withheld.')))}</p>"
                )
            check_sections.append(
                "<section class=\"check\">"
                f"<h2>{html.escape(check.check_name)} — {html.escape(check.evaluation_severity)}</h2>"
                f"<p><strong>Collection:</strong> {html.escape(check.collection_status)}"
                f" &nbsp; <strong>Tool:</strong> {html.escape(check.tool_name)}</p>"
                f"<p><strong>Reason:</strong> {html.escape(check.reason_code)} — {html.escape(sanitize_untrusted_text(check.explanation))}</p>"
                f"<p><strong>Evaluation measurements:</strong> {html.escape(json.dumps(_safe_data(check.measured_values), ensure_ascii=False, sort_keys=True))}</p>"
                f"<details><summary>Observed values</summary><pre>{html.escape(observed)}</pre></details>"
                f"{error_html}</section>"
            )
        failed = [check.check_name for check in self.checks if check.collection_status == "FAILED"]
        unknown = [check.check_name for check in self.checks if check.evaluation_severity == "UNKNOWN"]
        return (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>DBA Health Report</title><style>"
            "body{font:15px system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#172033}"
            ".summary,.check{border:1px solid #ccd3df;border-radius:8px;padding:1rem;margin:1rem 0}"
            ".severity{font-size:1.4rem;font-weight:700}pre{white-space:pre-wrap;overflow-wrap:anywhere}"
            ".error{color:#8a1c1c}</style></head><body>"
            "<h1>DBA Health Report</h1><section class=\"summary\">"
            f"<p><strong>Report ID:</strong> {html.escape(self.report_id)}</p>"
            f"<p><strong>Instance:</strong> {html.escape(sanitize_untrusted_text(self.sql_instance))}</p>"
            f"<p><strong>Generated (UTC):</strong> {html.escape(self.generated_at_utc)}</p>"
            f"<p class=\"severity\">Overall: {html.escape(self.overall_severity)}</p>"
            f"<p><strong>Executive summary:</strong> {html.escape(sanitize_untrusted_text(self.overall_explanation))}</p>"
            f"<p><strong>Overall reason:</strong> {html.escape(self.overall_reason_code)}</p>"
            f"<h2>Summary by severity</h2><ul>{summary_rows}</ul>"
            f"<p><strong>Collection failures:</strong> {html.escape(', '.join(failed) if failed else 'None')}</p>"
            f"<p><strong>UNKNOWN checks:</strong> {html.escape(', '.join(unknown) if unknown else 'None')}</p>"
            "</section>" + "".join(check_sections) + "</body></html>"
        )


def build_health_report(
    monitoring_results: Sequence[MonitoringCheck],
    evaluations: Sequence[HealthEvaluation],
    overall: OverallEvaluation,
    *,
    report_id: str | None = None,
    generated_at: datetime | None = None,
) -> DbaHealthReport:
    """Combine matching collection and evaluation records without inferring values."""
    monitoring = tuple(monitoring_results)
    evaluated = tuple(evaluations)
    if not isinstance(overall, OverallEvaluation):
        raise ValueError("Overall evaluation is required.")
    if not monitoring or len(monitoring) != len(evaluated) or len(overall.checks) != len(evaluated):
        raise ValueError("Monitoring and evaluation results do not form a complete report set.")
    if not all(isinstance(item, MonitoringCheck) for item in monitoring) or not all(isinstance(item, HealthEvaluation) for item in evaluated):
        raise ValueError("Report inputs have an invalid structure.")
    if len({item.check_name for item in monitoring}) != len(monitoring):
        raise ValueError("Duplicate monitoring check names are not allowed.")
    by_name = {item.check_name: item for item in monitoring}
    report_checks: list[ReportCheck] = []
    for evaluation in evaluated:
        check = by_name.get(evaluation.check_name)
        if (
            check is None
            or evaluation.original_result != check.to_dict()
            or evaluation.sql_instance != check.sql_instance
            or evaluation.collection_status != check.status.value
        ):
            raise ValueError("Evaluation does not match its original monitoring result.")
        report_checks.append(ReportCheck(
            check_name=check.check_name,
            tool_name=check.tool_name,
            timestamp_utc=check.timestamp_utc,
            collection_status=check.status.value,
            observed_data=_safe_data(check.data),
            evaluation_severity=evaluation.severity,
            reason_code=evaluation.reason_code,
            explanation=sanitize_untrusted_text(evaluation.reason),
            measured_values=_safe_data(evaluation.measured_values),
            error=_safe_data(evaluation.error) if evaluation.error is not None else None,
        ))
    if len(by_name) != len(report_checks) or overall.sql_instance != monitoring[0].sql_instance:
        raise ValueError("Overall and per-check targets do not match.")
    if tuple(overall.checks) != evaluated:
        raise ValueError("Overall evaluation does not contain the supplied individual evaluations.")
    if overall.severity not in _SEVERITIES or any(item.evaluation_severity not in _SEVERITIES for item in report_checks):
        raise ValueError("Report contains an unsupported severity.")

    selected_id = report_id or uuid4().hex
    if not isinstance(selected_id, str) or not _REPORT_ID.fullmatch(selected_id):
        raise ValueError("Report ID must be a 32-character lowercase hexadecimal identifier.")
    timestamp = generated_at or datetime.now(timezone.utc)
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
        raise ValueError("Report generation timestamp must be UTC.")
    summary = {severity: sum(item.evaluation_severity == severity for item in report_checks) for severity in _SEVERITIES}
    return DbaHealthReport(
        report_id=selected_id,
        generated_at_utc=timestamp.isoformat(),
        sql_instance=monitoring[0].sql_instance,
        overall_severity=overall.severity,
        overall_reason_code=overall.reason_code,
        overall_explanation=sanitize_untrusted_text(overall.reason),
        summary_by_severity=summary,
        checks=tuple(report_checks),
    )
