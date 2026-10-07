"""Minimal structured audit-event contract; storage and logging are host-owned.

Events deliberately contain bounded metadata only. They never include request
text, tool arguments, raw exceptions, credentials, or connection strings.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import re
from uuid import uuid4

from .llm_contract import APPROVED_MCP_TOOLS
from .response import sanitize_untrusted_text


_CLASSIFICATIONS = {
    "READ_ONLY", "READ_ONLY_SQL / WRITES_REPORT", "WRITES_REPORT",
    "NEEDS_APPROVAL", "REJECT",
}
_APPROVAL_OUTCOMES = {"NOT_REQUIRED", "PENDING", "GRANTED", "DECLINED", "INVALID", "STALE"}
_EXECUTION_RESULTS = {"NOT_ATTEMPTED", "SUCCESS", "FAILURE"}
_FAILURE_CATEGORIES = {None, "VALIDATION", "APPROVAL", "MCP", "RESPONSE"}
_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_OPERATION_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _metadata_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(f"Invalid {field_name} audit metadata.")
    sanitized = sanitize_untrusted_text(value)
    if sanitized != value:
        raise ValueError(f"Unsafe {field_name} audit metadata.")
    return value


@dataclass(frozen=True)
class AuditEvent:
    """A validated event shape, not a logger or persistent audit service."""

    tool_name: str | None
    classification: str
    approval_required: bool
    approval_outcome: str
    execution_attempted: bool
    execution_result: str
    failure_category: str | None = None
    sql_instance: str | None = None
    database: str | None = None
    operation_id: str | None = None
    request_id: str = field(default_factory=lambda: uuid4().hex)
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if self.tool_name is not None and self.tool_name not in APPROVED_MCP_TOOLS:
            raise ValueError("Unknown tool in audit event.")
        if self.classification not in _CLASSIFICATIONS:
            raise ValueError("Invalid audit classification.")
        if type(self.approval_required) is not bool or type(self.execution_attempted) is not bool:
            raise ValueError("Audit flags must be Boolean.")
        if self.approval_outcome not in _APPROVAL_OUTCOMES:
            raise ValueError("Invalid approval outcome.")
        if self.execution_result not in _EXECUTION_RESULTS:
            raise ValueError("Invalid execution result.")
        if self.failure_category not in _FAILURE_CATEGORIES:
            raise ValueError("Invalid failure category.")
        if not _ID_PATTERN.fullmatch(self.request_id):
            raise ValueError("Invalid audit request identifier.")
        if self.operation_id is not None and not _OPERATION_PATTERN.fullmatch(self.operation_id):
            raise ValueError("Invalid audit operation identifier.")
        try:
            timestamp = datetime.fromisoformat(self.timestamp_utc)
        except (TypeError, ValueError):
            raise ValueError("Invalid audit timestamp.") from None
        if timestamp.tzinfo is None or timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
            raise ValueError("Audit timestamp must be UTC.")
        _metadata_text(self.sql_instance, "SQL instance")
        _metadata_text(self.database, "database")
        if self.execution_attempted is False and self.execution_result != "NOT_ATTEMPTED":
            raise ValueError("Unattempted execution must have NOT_ATTEMPTED result.")
        if self.execution_result == "FAILURE" and self.failure_category is None:
            raise ValueError("Failed execution requires a failure category.")

    def to_dict(self) -> dict[str, object]:
        """Return only the fixed metadata contract; no free-form payload exists."""
        return asdict(self)
