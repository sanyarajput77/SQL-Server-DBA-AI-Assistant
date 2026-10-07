"""Strict intake boundary for provider-generated structured plans.

Incoming plans are proposals only. This module checks the Phase 13 envelope,
recomputes the canonical plan with the existing planner/contract, and returns
that planner-owned AssistantPlan. It does not dispatch MCP calls.
"""

from typing import Any, Callable, Iterable, Mapping
import math

from .llm_contract import StructuredAssistantPlan, serialize_plan
from .orchestrator import AssistantPlan, orchestrate_request


_FIELDS = {
    "request", "intent", "knowledge_required", "tool_required", "tool_name",
    "classification", "parameters", "approval_required", "rejection_reason",
}
_INTENTS = {"knowledge", "live_dba", "combined", "state_change", "unsupported"}
_CLASSIFICATIONS = {"REFERENCE_ONLY", "READ_ONLY", "WRITES_REPORT", "NEEDS_APPROVAL", "REJECT"}


class PlanValidationError(ValueError):
    """Raised when a proposed plan is malformed or differs from canonical planning."""


def _json_value(value: Any, depth: int = 0) -> bool:
    if depth > 8:
        return False
    if value is None or type(value) in {str, bool, int}:
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_value(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_value(item, depth + 1) for key, item in value.items())
    return False


def validate_llm_plan(
    proposed: Mapping[str, Any],
    *,
    mcp_catalog: Iterable[Any],
    database_validator: Callable[[str], str] | None = None,
) -> AssistantPlan:
    """Validate an LLM plan and return the canonical existing ``AssistantPlan``.

    The incoming plan must match the existing planner's serialized Phase 13
    contract exactly. Consequently callers cannot choose tools, classifications,
    arguments, paths, commands, or approval flags independently of the planner.
    """

    if not isinstance(proposed, Mapping) or set(proposed) != _FIELDS:
        raise PlanValidationError("Plan structure is malformed.")
    if not isinstance(proposed.get("request"), str) or not proposed["request"].strip() or len(proposed["request"]) > 4096:
        raise PlanValidationError("Plan request is invalid.")
    if proposed.get("intent") not in _INTENTS:
        raise PlanValidationError("Plan intent is invalid.")
    if proposed.get("classification") not in _CLASSIFICATIONS:
        raise PlanValidationError("Plan classification is invalid.")
    for field in ("knowledge_required", "tool_required", "approval_required"):
        if type(proposed.get(field)) is not bool:
            raise PlanValidationError("Plan flags must be Boolean.")
    if proposed.get("tool_name") is not None and not isinstance(proposed["tool_name"], str):
        raise PlanValidationError("Plan tool name is invalid.")
    if proposed.get("rejection_reason") is not None and not isinstance(proposed["rejection_reason"], str):
        raise PlanValidationError("Plan rejection reason is invalid.")
    if not isinstance(proposed.get("parameters"), dict) or not _json_value(proposed["parameters"]):
        raise PlanValidationError("Plan parameters are invalid.")

    # Snapshot once: the catalog may be a generator and both existing layers
    # need the same discovered tool set.
    catalog = tuple(mcp_catalog)
    request = proposed["request"]
    canonical: AssistantPlan = orchestrate_request(
        request,
        mcp_catalog=catalog,
        database_validator=database_validator,
    )
    expected: StructuredAssistantPlan = serialize_plan(
        request,
        canonical,
        mcp_catalog=catalog,
        database_validator=database_validator,
    )
    if expected.to_dict() != dict(proposed):
        raise PlanValidationError("Plan does not match the validated planner contract.")
    return canonical
