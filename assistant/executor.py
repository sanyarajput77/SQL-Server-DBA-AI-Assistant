"""Controlled executor for local RAG and the existing official MCP client.

SQL-facing work is sent only through an initialized official MCP ClientSession.
This module has no PowerShell, shell, SQL driver, or subprocess execution path.
"""

from typing import Any

from mcp import ClientSession

from rag import chunk_documents, load_documents, retrieve

from .approval import ApprovalAuthority, ExecutionEligibility
from .orchestrator import AssistantPlan
from .response import AssistantResponse, knowledge_attributions, sanitize_mcp_result


_ALLOWED_READ_ONLY_TOOLS = {
    "get_ops_database_inventory",
    "get_ops_database_health",
    "get_ops_database_size",
    "get_ops_backup_status",
    "get_ops_disk_space",
    "get_ops_failed_jobs",
    "get_ops_blocking_sessions",
    "get_ops_long_running_queries",
}


def _field(obj: Any, snake_name: str, camel_name: str | None = None, default: Any = None) -> Any:
    value = getattr(obj, snake_name, None)
    if value is None and camel_name:
        value = getattr(obj, camel_name, None)
    return default if value is None else value


def _result(
    plan: AssistantPlan,
    status: str,
    explanation: str,
    *,
    knowledge: tuple[dict[str, Any], ...] = (),
    planned_actions: tuple[dict[str, Any], ...] = (),
    mcp_results: tuple[dict[str, Any], ...] = (),
) -> AssistantResponse:
    return AssistantResponse(
        status=status,
        intent=plan.intent,
        classification=plan.classification,
        explanation=explanation,
        knowledge_sources=knowledge,
        planned_actions=planned_actions,
        mcp_results=mcp_results,
        dispatch_performed=bool(mcp_results),
    )


async def execute_plan(
    plan: AssistantPlan,
    *,
    query: str,
    mcp_session: ClientSession | None = None,
    approval_authority: ApprovalAuthority | None = None,
    execution_eligibility: ExecutionEligibility | None = None,
    top_k: int = 3,
) -> AssistantResponse:
    """Execute RAG and approved read-only tool plans without bypassing MCP.

    The MCP session must already be initialized by the caller using the
    official MCP client transport. This function only calls ``list_tools`` and
    ``call_tool`` on that session; it never launches a process or opens SQL.
    """

    if plan.classification == "REJECT" or plan.intent == "unsupported":
        return _result(plan, "REJECTED", plan.explanation)
    approved_state_change = False
    if plan.classification == "NEEDS_APPROVAL":
        if (
            approval_authority is not None
            and execution_eligibility is not None
            and approval_authority.consume_eligibility(plan, execution_eligibility)
        ):
            approved_state_change = True
        else:
            needs_input = any(action.required_inputs for action in plan.mcp_actions)
            planned_actions = tuple(
                {
                    "tool": action.tool_name,
                    "behavior": action.behavior,
                    "requires_approval": action.requires_approval,
                    "required_inputs": list(action.required_inputs),
                    "dispatched": False,
                }
                for action in plan.mcp_actions
            )
            return _result(
                plan,
                "NEEDS_INPUT" if needs_input else "NEEDS_APPROVAL",
                plan.explanation if needs_input else "Approval is required. No MCP call or state change was performed.",
                planned_actions=planned_actions,
            )
    if plan.classification == "WRITES_REPORT":
        return _result(plan, "APPROVAL_REQUIRED", "The report tool writes a file; host approval is required before MCP dispatch.")
    if plan.classification not in {"READ_ONLY", "REFERENCE_ONLY"} and not approved_state_change:
        return _result(plan, "REJECTED", "The plan classification is not supported for execution.")

    knowledge_results = ()
    if plan.intent in {"knowledge", "combined"}:
        try:
            documents = load_documents()
            chunks = chunk_documents(documents)
            knowledge_results = tuple(retrieve(query, chunks, documents, top_k=top_k))
        except Exception:
            # Local errors may contain machine paths; keep them outside the response.
            return _result(plan, "ERROR", "Local knowledge retrieval failed; internal details were withheld.")
        if not knowledge_results:
            if plan.intent == "knowledge":
                return _result(plan, "NO_MATCH", "No matching local reference was found.")

    knowledge = knowledge_attributions(knowledge_results)
    if plan.classification == "REFERENCE_ONLY":
        return _result(
            plan,
            "SUCCESS" if knowledge else "NO_MATCH",
            "Retrieved local reference material; it is not live SQL Server evidence.",
            knowledge=knowledge,
        )

    if (
        (plan.intent not in {"live_dba", "combined"} and not approved_state_change)
        or len(plan.mcp_actions) != 1
    ):
        return _result(plan, "REJECTED", "The read-only plan does not contain exactly one supported MCP action.", knowledge=knowledge)
    if mcp_session is None:
        return _result(plan, "MCP_UNAVAILABLE", "No initialized MCP client session was provided.", knowledge=knowledge)

    action = plan.mcp_actions[0]
    is_approved_backup = approved_state_change and action.tool_name == "backup_ops_database"
    if approved_state_change and not is_approved_backup:
        return _result(plan, "REJECTED", "The approved operation is outside the backup capability.", knowledge=knowledge)
    if not is_approved_backup and (
        action.tool_name not in _ALLOWED_READ_ONLY_TOOLS
        or action.behavior != "READ_ONLY"
        or action.requires_approval
    ):
        return _result(plan, "REJECTED", "The selected operation is not an approved read-only MCP capability.", knowledge=knowledge)

    try:
        catalog_result = await mcp_session.list_tools()
        catalog = _field(catalog_result, "tools", default=()) or ()
    except Exception:
        return _result(plan, "MCP_UNAVAILABLE", "The existing MCP catalog could not be read.", knowledge=knowledge)

    tool = next((item for item in catalog if _field(item, "name") == action.tool_name), None)
    if tool is None:
        return _result(plan, "REJECTED", "The selected tool is not present in the discovered MCP catalog.", knowledge=knowledge)
    annotations = _field(tool, "annotations")
    if is_approved_backup:
        if (
            _field(annotations, "destructive_hint", "destructiveHint", False) is not True
            or _field(annotations, "read_only_hint", "readOnlyHint", False) is True
        ):
            return _result(plan, "REJECTED", "The discovered backup tool is not marked as approval-gated.", knowledge=knowledge)
    elif (
        _field(annotations, "read_only_hint", "readOnlyHint", False) is not True
        or _field(annotations, "destructive_hint", "destructiveHint", False) is True
    ):
        return _result(plan, "REJECTED", "The discovered MCP tool is not marked read-only.", knowledge=knowledge)
    input_schema = _field(tool, "input_schema", "inputSchema", {}) or {}
    properties = input_schema.get("properties", {}) if isinstance(input_schema, dict) else {}
    arguments = dict(action.arguments) if isinstance(action.arguments, dict) else None
    if is_approved_backup and arguments is not None and execution_eligibility is not None:
        arguments["sql_instance"] = execution_eligibility.sql_instance
    if arguments is None or not set(arguments).issubset(properties):
        return _result(plan, "REJECTED", "The planned arguments do not match the discovered MCP input schema.", knowledge=knowledge)

    try:
        raw_result = await mcp_session.call_tool(action.tool_name, arguments)
    except Exception:
        return _result(plan, "MCP_ERROR", "The existing MCP operation failed; internal details were withheld.", knowledge=knowledge)
    safe_result = sanitize_mcp_result(action.tool_name, raw_result)
    if safe_result["status"] == "ERROR":
        return _result(plan, "MCP_ERROR", safe_result["error"], knowledge=knowledge, mcp_results=(safe_result,))
    return _result(
        plan,
        "SUCCESS",
        "MCP output is live tool evidence; local reference material, if present, remains separately attributed.",
        knowledge=knowledge,
        mcp_results=(safe_result,),
    )
