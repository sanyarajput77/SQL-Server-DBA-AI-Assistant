"""Read-only monitoring collection using only the established MCP catalog."""

from datetime import datetime, timezone
from typing import Any, Callable

from mcp import ClientSession

from assistant.response import sanitize_mcp_result, sanitize_untrusted_text

from .model import MonitoringCheck, MonitoringError, MonitoringStatus


# Fixed mapping: this service intentionally excludes report-writing and backup tools.
MONITORING_TOOLS = {
    "database_inventory": "get_ops_database_inventory",
    "database_health": "get_ops_database_health",
    "database_size": "get_ops_database_size",
    "backup_status": "get_ops_backup_status",
    "disk_space": "get_ops_disk_space",
    "failed_jobs": "get_ops_failed_jobs",
    "blocking_sessions": "get_ops_blocking_sessions",
    "long_running_queries": "get_ops_long_running_queries",
}


class SqlConnectivityError(ConnectionError):
    """A caller-classified SQL connectivity failure with unsafe details withheld."""


def _read_field(value: Any, snake_name: str, camel_name: str | None = None, default: Any = None) -> Any:
    field = getattr(value, snake_name, None)
    if field is None and camel_name:
        field = getattr(value, camel_name, None)
    return default if field is None else field


class MonitoringService:
    """Collect fixed read-only checks; do not infer health or severity."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _timestamp(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset().total_seconds() != 0:
            raise ValueError("Monitoring clock must return a timezone-aware UTC datetime.")
        return value.isoformat()

    def _failure(
        self,
        check_name: str,
        tool_name: str,
        sql_instance: str,
        category: str,
        message: str,
    ) -> MonitoringCheck:
        safe_message = sanitize_untrusted_text(message)
        if not safe_message or safe_message != message:
            safe_message = "The monitoring operation failed; sensitive details were withheld."
        return MonitoringCheck(
            check_name=check_name,
            tool_name=tool_name,
            timestamp_utc=self._timestamp(),
            sql_instance=sql_instance,
            status=MonitoringStatus.FAILED,
            error=MonitoringError(category, safe_message),
        )

    async def collect(
        self,
        session: ClientSession,
        *,
        sql_instance: str,
        check_names: tuple[str, ...] | list[str] | None = None,
    ) -> tuple[MonitoringCheck, ...]:
        """Collect selected fixed read-only checks sequentially via ClientSession."""
        if not isinstance(sql_instance, str) or not sql_instance.strip():
            raise ValueError("A configured SQL instance name is required.")
        if sanitize_untrusted_text(sql_instance) != sql_instance:
            raise ValueError("Unsafe SQL instance metadata.")
        selected = tuple(MONITORING_TOOLS) if check_names is None else tuple(check_names)
        if (
            not selected
            or any(not isinstance(name, str) for name in selected)
            or len(set(selected)) != len(selected)
            or any(name not in MONITORING_TOOLS for name in selected)
        ):
            raise ValueError("Monitoring check selection is invalid.")
        selected_tools = {name: MONITORING_TOOLS[name] for name in selected}

        try:
            result = await session.list_tools()
            catalog = _read_field(result, "tools", default=()) or ()
        except Exception:
            return tuple(
                self._failure(name, tool, sql_instance, "MCP_CATALOG", "The configured MCP catalog is unavailable.")
                for name, tool in selected_tools.items()
            )

        discovered = {
            _read_field(item, "name"): item
            for item in catalog
            if isinstance(_read_field(item, "name"), str)
        }
        checks: list[MonitoringCheck] = []
        for check_name, tool_name in selected_tools.items():
            tool = discovered.get(tool_name)
            if tool is None:
                checks.append(self._failure(
                    check_name, tool_name, sql_instance, "MCP_CATALOG", "The required read-only monitoring tool is unavailable."
                ))
                continue
            annotations = _read_field(tool, "annotations")
            if (
                _read_field(annotations, "read_only_hint", "readOnlyHint", False) is not True
                or _read_field(annotations, "destructive_hint", "destructiveHint", False) is True
            ):
                checks.append(self._failure(
                    check_name, tool_name, sql_instance, "MCP_CATALOG", "The discovered tool is not classified as read-only."
                ))
                continue
            schema = _read_field(tool, "input_schema", "inputSchema", {}) or {}
            properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
            if "sql_instance" not in properties:
                checks.append(self._failure(
                    check_name, tool_name, sql_instance, "MCP_CATALOG", "The tool schema does not accept the configured SQL instance."
                ))
                continue

            try:
                raw = await session.call_tool(tool_name, {"sql_instance": sql_instance})
            except SqlConnectivityError:
                checks.append(self._failure(
                    check_name, tool_name, sql_instance, "SQL_CONNECTIVITY", "SQL connectivity failed; provider details were withheld."
                ))
                continue
            except Exception:
                checks.append(self._failure(
                    check_name, tool_name, sql_instance, "MCP", "The MCP monitoring operation failed; details were withheld."
                ))
                continue

            content = _read_field(raw, "content", default=()) or ()
            if not content or not any(isinstance(_read_field(block, "text"), str) for block in content):
                checks.append(self._failure(
                    check_name, tool_name, sql_instance, "MALFORMED_RESULT", "The monitoring tool returned no structured result."
                ))
                continue
            safe = sanitize_mcp_result(tool_name, raw)
            if safe["status"] == "ERROR":
                checks.append(self._failure(
                    check_name, tool_name, sql_instance, "MCP", safe["error"]
                ))
                continue
            data = safe.get("data")
            if isinstance(data, dict) and str(data.get("status", "")).casefold() in {"error", "failed", "failure"}:
                checks.append(self._failure(
                    check_name, tool_name, sql_instance, "MCP", "The monitoring tool reported an operation failure."
                ))
            elif isinstance(data, list) and not data:
                checks.append(MonitoringCheck(
                    check_name=check_name,
                    tool_name=tool_name,
                    timestamp_utc=self._timestamp(),
                    sql_instance=sql_instance,
                    status=MonitoringStatus.EMPTY,
                    data=[],
                ))
            elif isinstance(data, (list, dict)) and data:
                checks.append(MonitoringCheck(
                    check_name=check_name,
                    tool_name=tool_name,
                    timestamp_utc=self._timestamp(),
                    sql_instance=sql_instance,
                    status=MonitoringStatus.SUCCESS,
                    data=data,
                ))
            else:
                checks.append(self._failure(
                    check_name, tool_name, sql_instance, "MALFORMED_RESULT", "The monitoring tool returned an invalid result shape."
                ))
        return tuple(checks)
