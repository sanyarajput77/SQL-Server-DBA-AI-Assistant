"""Provider-neutral structured-plan contract over the existing planner output."""

from dataclasses import asdict, dataclass
import re
from typing import Any, Callable, Iterable

from .orchestrator import AssistantPlan, orchestrate_request
from .response import sanitize_untrusted_text


APPROVED_MCP_TOOLS = frozenset(
    {
        "get_ops_database_inventory",
        "get_ops_database_health",
        "get_ops_database_size",
        "get_ops_backup_status",
        "get_ops_disk_space",
        "get_ops_failed_jobs",
        "get_ops_blocking_sessions",
        "get_ops_long_running_queries",
        "get_ops_server_health_report",
        "backup_ops_database",
    }
)

_SECRET_TEXT = re.compile(
    r"(?i)\b(?:password|passwd|pwd|token|secret|client_secret|access_token|connectionstring)\s*[:=]"
)
_PATH_TEXT = re.compile(r"(?i)(?:\b[A-Z]:[\\/]|\\\\[^\s]+|(?<![\w:])/(?:[^/\s]+/)+)")
_SQL_TEXT = re.compile(r"(?i)\b(?:select|insert|update|delete|drop|alter|exec(?:ute)?)\b")
_UNSAFE_SQL_REQUEST = re.compile(
    r"(?i)\b(?:select\b|insert\s+into\b|update\s+\w+\s+set\b|delete\s+from\b|drop\s+\w+\b|alter\s+\w+\b|exec(?:ute)?\b)"
)
_UNSAFE_SHELL_REQUEST = re.compile(
    r"(?i)\b(?:powershell|pwsh|cmd(?:\.exe)?|shell|get-process|invoke-expression)\b"
)
_FORBIDDEN_PARAMETER_NAMES = {
    "sql", "query", "command", "shell", "executable", "password", "credential", "token",
    "path", "backup_path", "backup_directory", "output_path",
}


@dataclass(frozen=True)
class StructuredAssistantPlan:
    request: str
    intent: str
    knowledge_required: bool
    tool_required: bool
    tool_name: str | None
    classification: str
    parameters: dict[str, Any]
    approval_required: bool
    rejection_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _catalog_items(catalog: Iterable[Any]) -> dict[str, Any]:
    items: dict[str, Any] = {}
    for item in catalog:
        name = item if isinstance(item, str) else getattr(item, "name", None)
        if isinstance(name, str):
            items[name] = item
    return items


def _rejected_plan(reason: str = "The request or proposed action failed plan validation.") -> StructuredAssistantPlan:
    return StructuredAssistantPlan(
        request="[REDACTED REJECTED REQUEST]",
        intent="unsupported",
        knowledge_required=False,
        tool_required=False,
        tool_name=None,
        classification="REJECT",
        parameters={},
        approval_required=False,
        rejection_reason=reason,
    )


def serialize_plan(
    request: str,
    plan: AssistantPlan,
    *,
    mcp_catalog: Iterable[Any],
    database_validator: Callable[[str], str] | None = None,
) -> StructuredAssistantPlan:
    """Map the existing planner result into a sanitized LLM-facing contract.

    This validates only the plan envelope and catalog membership. The existing
    MCP server remains the final authority for every input and target check.
    """

    if plan.classification == "REJECT" or plan.intent == "unsupported":
        return _rejected_plan("The request is unsupported, unsafe, or failed configured target validation.")
    if len(plan.mcp_actions) > 1:
        return _rejected_plan("Only one existing MCP action may be planned per request.")

    if _UNSAFE_SQL_REQUEST.search(request) or _UNSAFE_SHELL_REQUEST.search(request):
        return _rejected_plan("Arbitrary SQL and shell requests cannot be included in a plan.")
    if _PATH_TEXT.search(request):
        return _rejected_plan("Caller-supplied filesystem paths cannot be included in a plan.")
    safe_request = sanitize_untrusted_text(request)
    if _PATH_TEXT.search(safe_request):
        return _rejected_plan("Caller-supplied filesystem paths cannot be included in a plan.")

    catalog = _catalog_items(mcp_catalog)
    tool_name: str | None = None
    parameters: dict[str, Any] = {}
    approval_required = plan.classification == "NEEDS_APPROVAL"
    if plan.mcp_actions:
        action = plan.mcp_actions[0]
        tool_name = action.tool_name
        if tool_name not in APPROVED_MCP_TOOLS or tool_name not in catalog:
            return _rejected_plan("The plan references a tool outside the approved MCP catalog.")
        if not isinstance(action.arguments, dict):
            return _rejected_plan()
        catalog_tool = catalog[tool_name]
        schema = getattr(catalog_tool, "input_schema", getattr(catalog_tool, "inputSchema", {}))
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        for name, value in action.arguments.items():
            if (
                not isinstance(name, str)
                or name.casefold() in _FORBIDDEN_PARAMETER_NAMES
                or name not in properties
            ):
                return _rejected_plan("The plan contains an unsupported MCP parameter.")
            if isinstance(value, str) and (
                _SECRET_TEXT.search(value) or _PATH_TEXT.search(value) or _SQL_TEXT.search(value)
            ):
                return _rejected_plan("Sensitive, path, or SQL-like values cannot be included in plan parameters.")
        parameters = dict(action.arguments)
        approval_required = approval_required or action.requires_approval

        if tool_name == "backup_ops_database":
            if plan.classification != "NEEDS_APPROVAL" or not action.requires_approval:
                return _rejected_plan("Backup plans must remain NEEDS_APPROVAL.")
            if database_validator is None or "database" not in parameters:
                return _rejected_plan("The backup target could not be validated.")
            try:
                canonical_database = database_validator(parameters["database"])
            except Exception:
                return _rejected_plan("The backup target is not approved.")
            parameters["database"] = canonical_database
    elif plan.classification == "NEEDS_APPROVAL":
        return _rejected_plan("An approval-required plan must identify its existing MCP tool.")

    if tool_name is not None and tool_name == "backup_ops_database":
        approval_required = True
    rejection_reason = None
    return StructuredAssistantPlan(
        request=safe_request,
        intent=plan.intent,
        knowledge_required=plan.intent in {"knowledge", "combined"},
        tool_required=tool_name is not None,
        tool_name=tool_name,
        classification=plan.classification,
        parameters=parameters,
        approval_required=approval_required,
        rejection_reason=rejection_reason,
    )


def plan_request(
    request: str,
    *,
    mcp_catalog: Iterable[Any],
    database_validator: Callable[[str], str] | None = None,
) -> StructuredAssistantPlan:
    """Run the existing deterministic planner and return its structured contract."""

    plan = orchestrate_request(
        request,
        mcp_catalog=mcp_catalog,
        database_validator=database_validator,
    )
    return serialize_plan(
        request,
        plan,
        mcp_catalog=mcp_catalog,
        database_validator=database_validator,
    )
