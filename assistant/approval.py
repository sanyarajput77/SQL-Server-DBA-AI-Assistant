"""In-memory approval contract for binding human approval to one exact operation.

This module creates execution eligibility only. It never invokes the Executor,
MCP, PowerShell, SQL Server, or a backup operation.
"""

from dataclasses import dataclass, field
import hashlib
import json
import secrets
from typing import Any, Callable, Iterable

from .orchestrator import AssistantPlan


_BACKUP_TOOL = "backup_ops_database"
_BACKUP_TYPES = {"Full", "Differential", "Log"}


class ApprovalError(ValueError):
    """Raised when an approval request or grant cannot be safely validated."""


@dataclass(frozen=True)
class ApprovalRequest:
    """Canonical description of one proposed operation; contains no secrets or paths."""

    operation_id: str
    tool_name: str
    sql_instance: str
    database: str
    backup_type: str
    classification: str
    parameters: tuple[tuple[str, str], ...]
    backup_destination_policy: str = "configured"


@dataclass(frozen=True)
class ApprovalGrant:
    """Opaque, one-use app-issued receipt; the private token is never serialized."""

    operation_id: str
    _nonce: str = field(repr=False)


@dataclass(frozen=True)
class ExecutionEligibility:
    """Proof that an exact pending operation passed this approval contract."""

    operation_id: str
    tool_name: str
    sql_instance: str
    database: str
    backup_type: str
    classification: str
    parameters: tuple[tuple[str, str], ...]
    backup_destination_policy: str
    _proof: str = field(repr=False)


def _canonical_request(
    plan: AssistantPlan,
    *,
    approved_instances: Iterable[str],
    default_instance: str,
    database_validator: Callable[[str], str],
) -> ApprovalRequest | None:
    if plan.classification == "REJECT" or plan.intent == "unsupported":
        raise ApprovalError("Rejected plans cannot be approved.")
    if plan.classification == "READ_ONLY":
        return None
    if plan.classification != "NEEDS_APPROVAL" or len(plan.mcp_actions) != 1:
        raise ApprovalError("This plan is not an approval-gated operation.")

    action = plan.mcp_actions[0]
    if (
        action.tool_name != _BACKUP_TOOL
        or action.behavior != "NEEDS_APPROVAL"
        or action.requires_approval is not True
        or action.required_inputs
        or not isinstance(action.arguments, dict)
        or set(action.arguments) - {"database", "backup_type", "sql_instance"}
        or not {"database", "backup_type"}.issubset(action.arguments)
    ):
        raise ApprovalError("The proposed operation does not match the approved backup contract.")

    instances = tuple(item for item in approved_instances if isinstance(item, str) and item.strip())
    if not instances or not isinstance(default_instance, str) or not default_instance.strip():
        raise ApprovalError("The approved SQL instance configuration is invalid.")
    canonical_default = next((item for item in instances if item.casefold() == default_instance.casefold()), None)
    requested_instance = action.arguments.get("sql_instance") or canonical_default
    if not isinstance(requested_instance, str):
        raise ApprovalError("The SQL instance is not approved.")
    sql_instance = next((item for item in instances if item.casefold() == requested_instance.casefold()), None)
    if sql_instance is None:
        raise ApprovalError("The SQL instance is not approved.")

    database = action.arguments.get("database")
    backup_type = action.arguments.get("backup_type")
    if not isinstance(database, str) or not isinstance(backup_type, str) or backup_type not in _BACKUP_TYPES:
        raise ApprovalError("The database or backup type is invalid.")
    try:
        canonical_database = database_validator(database)
    except Exception:
        raise ApprovalError("The database is not approved.") from None
    if not isinstance(canonical_database, str) or canonical_database.casefold() != database.casefold():
        raise ApprovalError("The database is not approved.")

    parameters = tuple(sorted({
        "backup_type": backup_type,
        "database": canonical_database,
        "sql_instance": sql_instance,
        "destination_policy": "configured",
    }.items()))
    operation = {
        "tool_name": _BACKUP_TOOL,
        "sql_instance": sql_instance,
        "database": canonical_database,
        "backup_type": backup_type,
        "classification": "NEEDS_APPROVAL",
        "parameters": parameters,
        "backup_destination_policy": "configured",
    }
    operation_id = hashlib.sha256(
        json.dumps(operation, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ApprovalRequest(operation_id=operation_id, **operation)


class ApprovalAuthority:
    """Issue one-use in-memory grants only after an explicit trusted approval signal."""

    def __init__(self) -> None:
        self._pending: dict[str, ApprovalRequest] = {}
        self._issued: dict[str, str] = {}
        self._eligibility_proofs: dict[str, str] = {}

    def create_request(
        self,
        plan: AssistantPlan,
        *,
        approved_instances: Iterable[str],
        default_instance: str,
        database_validator: Callable[[str], str],
    ) -> ApprovalRequest | None:
        request = _canonical_request(
            plan,
            approved_instances=approved_instances,
            default_instance=default_instance,
            database_validator=database_validator,
        )
        if request is not None:
            self._pending[request.operation_id] = request
        return request

    def record_explicit_approval(
        self,
        request: ApprovalRequest,
        *,
        explicitly_approved_by_human: bool,
    ) -> ApprovalGrant:
        if explicitly_approved_by_human is not True:
            raise ApprovalError("Explicit human approval is required.")
        if not isinstance(request, ApprovalRequest) or self._pending.get(request.operation_id) != request:
            raise ApprovalError("The approval request is not pending or has been altered.")
        nonce = secrets.token_urlsafe(32)
        self._issued[nonce] = request.operation_id
        return ApprovalGrant(operation_id=request.operation_id, _nonce=nonce)

    def authorize(
        self,
        plan: AssistantPlan,
        request: ApprovalRequest,
        grant: ApprovalGrant | None,
        *,
        approved_instances: Iterable[str],
        default_instance: str,
        database_validator: Callable[[str], str],
    ) -> ExecutionEligibility:
        if grant is None:
            raise ApprovalError("Approval is missing.")
        current = _canonical_request(
            plan,
            approved_instances=approved_instances,
            default_instance=default_instance,
            database_validator=database_validator,
        )
        if (
            current is None
            or current != request
            or self._pending.get(request.operation_id) != request
            or not isinstance(grant, ApprovalGrant)
            or grant.operation_id != request.operation_id
            or self._issued.get(grant._nonce) != request.operation_id
        ):
            raise ApprovalError("Approval does not authorize this exact operation.")

        del self._issued[grant._nonce]
        del self._pending[request.operation_id]
        proof = secrets.token_urlsafe(32)
        self._eligibility_proofs[proof] = current.operation_id
        return ExecutionEligibility(
            operation_id=current.operation_id,
            tool_name=current.tool_name,
            sql_instance=current.sql_instance,
            database=current.database,
            backup_type=current.backup_type,
            classification=current.classification,
            parameters=current.parameters,
            backup_destination_policy=current.backup_destination_policy,
            _proof=proof,
        )

    def consume_eligibility(self, plan: AssistantPlan, eligibility: ExecutionEligibility | None) -> bool:
        """Validate and consume the app-issued eligibility exactly once for this plan."""
        if not isinstance(eligibility, ExecutionEligibility):
            return False

        # Consume first, including on a mismatch, so a changed operation cannot
        # be followed by a retry using the same eligibility proof.
        registered_id = self._eligibility_proofs.pop(eligibility._proof, None)
        if registered_id is None or registered_id != eligibility.operation_id:
            return False
        if (
            plan.classification != "NEEDS_APPROVAL"
            or plan.intent == "unsupported"
            or len(plan.mcp_actions) != 1
            or eligibility.tool_name != _BACKUP_TOOL
            or eligibility.classification != "NEEDS_APPROVAL"
            or eligibility.backup_destination_policy != "configured"
        ):
            return False

        action = plan.mcp_actions[0]
        if (
            action.tool_name != eligibility.tool_name
            or action.behavior != "NEEDS_APPROVAL"
            or action.requires_approval is not True
            or action.required_inputs
            or not isinstance(action.arguments, dict)
            or set(action.arguments) - {"database", "backup_type", "sql_instance"}
            or not isinstance(action.arguments.get("database"), str)
            or action.arguments["database"].casefold() != eligibility.database.casefold()
            or action.arguments.get("backup_type") != eligibility.backup_type
            or (
                action.arguments.get("sql_instance") is not None
                and (
                    not isinstance(action.arguments["sql_instance"], str)
                    or action.arguments["sql_instance"].casefold() != eligibility.sql_instance.casefold()
                )
            )
        ):
            return False

        expected_parameters = tuple(sorted({
            "backup_type": eligibility.backup_type,
            "database": eligibility.database,
            "sql_instance": eligibility.sql_instance,
            "destination_policy": eligibility.backup_destination_policy,
        }.items()))
        if eligibility.parameters != expected_parameters:
            return False
        operation = {
            "tool_name": eligibility.tool_name,
            "sql_instance": eligibility.sql_instance,
            "database": eligibility.database,
            "backup_type": eligibility.backup_type,
            "classification": eligibility.classification,
            "parameters": eligibility.parameters,
            "backup_destination_policy": eligibility.backup_destination_policy,
        }
        fingerprint = hashlib.sha256(
            json.dumps(operation, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return fingerprint == eligibility.operation_id
