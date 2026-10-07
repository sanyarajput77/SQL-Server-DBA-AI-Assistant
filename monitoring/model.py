"""Provider-neutral result schema for one monitoring data-collection check."""

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from assistant.response import sanitize_untrusted_text


class MonitoringStatus(StrEnum):
    SUCCESS = "SUCCESS"
    EMPTY = "EMPTY"
    FAILED = "FAILED"


@dataclass(frozen=True)
class MonitoringError:
    category: str
    message: str

    def __post_init__(self) -> None:
        if self.category not in {"SQL_CONNECTIVITY", "MCP", "MCP_CATALOG", "MALFORMED_RESULT"}:
            raise ValueError("Unsupported monitoring failure category.")
        if not isinstance(self.message, str) or not self.message:
            raise ValueError("Monitoring failures require a safe message.")
        if sanitize_untrusted_text(self.message) != self.message:
            raise ValueError("Monitoring failure messages must be sanitized.")


@dataclass(frozen=True)
class MonitoringCheck:
    """Collection state, not a DBA severity judgment."""

    check_name: str
    tool_name: str
    timestamp_utc: str
    sql_instance: str
    status: MonitoringStatus
    data: Any = None
    error: MonitoringError | None = None

    def __post_init__(self) -> None:
        try:
            timestamp = datetime.fromisoformat(self.timestamp_utc)
        except (TypeError, ValueError):
            raise ValueError("Monitoring timestamp must be ISO 8601.") from None
        if timestamp.tzinfo is None or timestamp.utcoffset().total_seconds() != 0:
            raise ValueError("Monitoring timestamp must be UTC.")
        if not self.check_name or not self.tool_name or not self.sql_instance.strip():
            raise ValueError("Monitoring check identity is required.")
        if sanitize_untrusted_text(self.sql_instance) != self.sql_instance:
            raise ValueError("Monitoring instance metadata must be sanitized.")
        if not isinstance(self.status, MonitoringStatus):
            raise ValueError("Monitoring check status is invalid.")
        if self.status == MonitoringStatus.FAILED:
            if self.error is None or self.data is not None:
                raise ValueError("Failed checks require an error and no data payload.")
        elif self.error is not None:
            raise ValueError("Successful or empty checks cannot include an error.")
        elif self.status == MonitoringStatus.EMPTY and self.data != []:
            raise ValueError("Empty checks must contain an empty list.")
        elif self.status == MonitoringStatus.SUCCESS and self.data in (None, [], {}):
            raise ValueError("Successful checks require non-empty structured data.")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result
