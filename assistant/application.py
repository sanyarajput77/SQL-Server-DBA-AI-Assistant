"""Application handoff from a structured LLM proposal to the existing executor."""

from typing import Any, Callable, Iterable, Mapping

from mcp import ClientSession

from .approval import ApprovalAuthority, ApprovalError, ApprovalGrant, ApprovalRequest
from .executor import execute_plan
from .intake import validate_llm_plan
from .response import AssistantResponse


async def execute_llm_plan(
    proposed: Mapping[str, Any],
    *,
    mcp_catalog: Iterable[Any],
    mcp_session: ClientSession | None = None,
    database_validator: Callable[[str], str] | None = None,
    approval_authority: ApprovalAuthority | None = None,
    approval_request: ApprovalRequest | None = None,
    approval_grant: ApprovalGrant | None = None,
    approved_instances: Iterable[str] = (),
    default_instance: str | None = None,
    top_k: int = 3,
) -> AssistantResponse:
    """Validate an LLM proposal, then hand its canonical plan to the Executor.

    The caller supplies the host-discovered catalog and initialized official
    MCP session. The Executor still performs its own tool/annotation/schema
    checks and dispatches only through that session. REJECT, backup approval,
    and report-write behavior remain governed by the existing Executor.
    """

    plan = validate_llm_plan(
        proposed,
        mcp_catalog=mcp_catalog,
        database_validator=database_validator,
    )
    # Intake guarantees this exact field and canonical sanitized request.
    query = proposed["request"]
    if plan.classification == "NEEDS_APPROVAL":
        if (
            approval_authority is None
            or approval_request is None
            or approval_grant is None
            or database_validator is None
            or default_instance is None
        ):
            return await execute_plan(plan, query=query, mcp_session=mcp_session, top_k=top_k)
        try:
            eligibility = approval_authority.authorize(
                plan,
                approval_request,
                approval_grant,
                approved_instances=approved_instances,
                default_instance=default_instance,
                database_validator=database_validator,
            )
        except ApprovalError:
            # Preserve the Executor's safe NEEDS_APPROVAL response on any mismatch.
            return await execute_plan(plan, query=query, mcp_session=mcp_session, top_k=top_k)
        return await execute_plan(
            plan,
            query=query,
            mcp_session=mcp_session,
            top_k=top_k,
            approval_authority=approval_authority,
            execution_eligibility=eligibility,
        )
    return await execute_plan(
        plan,
        query=query,
        mcp_session=mcp_session,
        top_k=top_k,
    )
