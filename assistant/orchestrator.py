"""Plan assistant responses using local RAG and the existing MCP tool catalog.

This module never calls MCP tools. It creates a plan for an MCP host to review
and, where allowed, dispatch through the existing MCP server. The MCP server
remains responsible for final input validation and approval enforcement.
"""

from dataclasses import dataclass, field
import re
from typing import Any, Callable, Iterable

from rag import KnowledgeChunk, KnowledgeDocument, RetrievalResult
from rag import chunk_documents, load_documents, retrieve


_UNSUPPORTED_PATTERNS = (
    re.compile(r"\bselect\b.{1,240}\bfrom\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(?:insert\s+into|update\s+\w+\s+set|delete\s+from|drop\s+\w+|alter\s+\w+|exec(?:ute)?\s+)\b", re.IGNORECASE),
    re.compile(r"\b(?:run|execute|invoke)\s+(?:this\s+)?(?:sql|query|powershell|pwsh|cmd(?:\.exe)?|shell)\b", re.IGNORECASE),
    re.compile(r"\b(?:powershell|pwsh|cmd(?:\.exe)?|shell)\b", re.IGNORECASE),
    re.compile(r"\bget-process\b", re.IGNORECASE),
)
_PATH_PATTERN = re.compile(r"(?:\b[A-Za-z]:[\\/]|\\\\[^\s]+)")
_BACKUP_ACTION_PATTERN = re.compile(
    r"\b(?:back\s+up|(?:create|perform|start|take)\s+(?:a\s+)?backup(?:\s+of)?)\b", re.IGNORECASE
)
_BACKUP_DATABASE_PATTERN = re.compile(
    r"\b(?:back\s+up|(?:create|perform|start|take)\s+(?:a\s+)?backup(?:\s+of)?)\s+(?:database\s+)?([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
_BACKUP_TYPE_PATTERN = re.compile(r"\b(full|differential|log)\b", re.IGNORECASE)
_KNOWLEDGE_CUE_PATTERN = re.compile(
    r"\b(?:what\s+is|explain|describe|why|how\s+does|what\s+should\s+i\s+check|guidance|concept)\b",
    re.IGNORECASE,
)
_BACKUP_KNOWLEDGE_PATTERN = re.compile(r"\b(?:backup|backups|recovery|transaction\s+log|rpo|rto)\b", re.IGNORECASE)
_BLOCKING_KNOWLEDGE_PATTERN = re.compile(r"\b(?:blocking|blocked|deadlock|deadlocking|lock\s+contention)\b", re.IGNORECASE)


@dataclass(frozen=True)
class MCPAction:
    tool_name: str
    behavior: str
    arguments: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    required_inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssistantPlan:
    intent: str
    classification: str
    status: str
    explanation: str
    knowledge_results: tuple[RetrievalResult, ...] = ()
    mcp_actions: tuple[MCPAction, ...] = ()
    dispatch_performed: bool = False

    def to_response(self) -> dict[str, Any]:
        """Return a host-safe response with relative source identifiers only."""

        return {
            "intent": self.intent,
            "classification": self.classification,
            "status": self.status,
            "explanation": self.explanation,
            "knowledge_sources": [
                {
                    "title": result.source_document.title,
                    "filename": result.source_document.filename,
                    "category": result.source_document.category,
                    "section": result.relevant_chunk.section,
                    "chunk": result.relevant_chunk.content,
                    "relevance_score": result.relevance_score,
                    "attribution": result.source_attribution,
                    "evidence_type": "REFERENCE_KNOWLEDGE",
                }
                for result in self.knowledge_results
            ],
            "mcp_actions": [
                {
                    "tool": action.tool_name,
                    "behavior": action.behavior,
                    "arguments": dict(action.arguments),
                    "requires_approval": action.requires_approval,
                    "required_inputs": list(action.required_inputs),
                    "dispatch_performed": False,
                    "evidence_type": "LIVE_SQL_TOOL_PENDING",
                }
                for action in self.mcp_actions
            ],
            "dispatch_performed": False,
        }


_LIVE_ROUTES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\b(?:backup\s+(?:problems?|status|history|recency)|not\s+backed\s+up|backups?\s+(?:failed|missing|overdue))\b", re.I), "get_ops_backup_status", "READ_ONLY"),
    (re.compile(r"\b(?:blocking\s+sessions?|sessions?\s+(?:are\s+)?blocked|blocking\s+other\s+sessions?)\b", re.I), "get_ops_blocking_sessions", "READ_ONLY"),
    (re.compile(r"\b(?:database\s+size|largest\s+databases?|space\s+used\s+by\s+(?:the\s+)?database)\b", re.I), "get_ops_database_size", "READ_ONLY"),
    (re.compile(r"\b(?:disk\s+space|free\s+space|available\s+space)\b", re.I), "get_ops_disk_space", "READ_ONLY"),
    (re.compile(r"\b(?:failed\s+(?:sql\s+agent\s+)?jobs?|job\s+failures?)\b", re.I), "get_ops_failed_jobs", "READ_ONLY"),
    (re.compile(r"\b(?:long[- ]running\s+queries|queries\s+running\s+for\s+a\s+long\s+time)\b", re.I), "get_ops_long_running_queries", "READ_ONLY"),
    (re.compile(r"\b(?:health\s+(?:status|report)|server\s+health)\b", re.I), "get_ops_server_health_report", "READ_ONLY_SQL / WRITES_REPORT"),
    (re.compile(r"\b(?:is\s+\w+\s+healthy|database\s+health)\b", re.I), "get_ops_database_health", "READ_ONLY"),
    (re.compile(r"\b(?:list|show|what|which|inventory)\b.*\bdatabases?\b", re.I), "get_ops_database_inventory", "READ_ONLY"),
)


def _tool_names(catalog: Iterable[Any]) -> set[str]:
    names: set[str] = set()
    for item in catalog:
        name = item if isinstance(item, str) else getattr(item, "name", None)
        if isinstance(name, str):
            names.add(name)
    return names


def _rejected(explanation: str) -> AssistantPlan:
    return AssistantPlan(
        intent="unsupported",
        classification="REJECT",
        status="REJECTED",
        explanation=explanation,
    )


def _knowledge_relevant(query: str) -> bool:
    if not _KNOWLEDGE_CUE_PATTERN.search(query):
        return False
    return bool(_BACKUP_KNOWLEDGE_PATTERN.search(query) or _BLOCKING_KNOWLEDGE_PATTERN.search(query))


def _retrieve(query: str, documents: list[KnowledgeDocument] | None, top_k: int) -> tuple[list[KnowledgeDocument], list[RetrievalResult]]:
    docs = documents if documents is not None else load_documents()
    chunks: list[KnowledgeChunk] = chunk_documents(docs)
    return docs, retrieve(query, chunks, docs, top_k=top_k)


def orchestrate_request(
    query: str,
    *,
    mcp_catalog: Iterable[Any],
    database_validator: Callable[[str], str] | None = None,
    documents: list[KnowledgeDocument] | None = None,
    top_k: int = 3,
) -> AssistantPlan:
    """Classify a request and create a RAG/MCP plan without dispatching it.

    ``mcp_catalog`` should be the existing MCP host's discovered tool list.
    ``database_validator`` should delegate to the existing MCP canonical
    database validator when backup preflight is needed. Final validation and
    approval remain enforced by the MCP server regardless of this preflight.
    """

    if not isinstance(query, str) or not query.strip():
        return _rejected("The request is empty or unsupported.")
    if any(pattern.search(query) for pattern in _UNSUPPORTED_PATTERNS):
        return _rejected("Arbitrary SQL and shell execution are not supported.")

    available_tools = _tool_names(mcp_catalog)
    if _BACKUP_ACTION_PATTERN.search(query):
        if _PATH_PATTERN.search(query):
            return _rejected("Caller-supplied backup destinations are not supported.")
        match = _BACKUP_DATABASE_PATTERN.search(query)
        if match is None or database_validator is None:
            return _rejected("The backup target could not be validated against the configured database allowlist.")
        requested_database = match.group(1).rstrip(".,;:!?").strip()
        try:
            approved_database = database_validator(requested_database)
        except Exception:
            return _rejected("The requested database is not approved or could not be validated.")
        if "backup_ops_database" not in available_tools:
            return _rejected("The approved backup capability is not available in the MCP catalog.")
        backup_type_match = _BACKUP_TYPE_PATTERN.search(query)
        backup_type = backup_type_match.group(1).title() if backup_type_match else None
        if backup_type == "Log":
            backup_type = "Log"
        required_inputs = () if backup_type else ("backup_type",)
        arguments: dict[str, Any] = {"database": approved_database}
        if backup_type:
            arguments["backup_type"] = backup_type
        if required_inputs:
            approval_status = "NEEDS_INPUT"
            approval_explanation = (
                "The database is approved, but the backup type is missing. No backup was started; "
                "provide a supported type and complete the existing MCP elicitation before dispatch."
            )
        else:
            approval_status = "AWAITING_APPROVAL"
            approval_explanation = (
                "A backup is a state-changing operation. No backup was started; the existing MCP elicitation "
                "and server-side validation must approve it before the Ops function can run."
            )
        return AssistantPlan(
            intent="state_change",
            classification="NEEDS_APPROVAL",
            status=approval_status,
            explanation=approval_explanation,
            mcp_actions=(
                MCPAction(
                    tool_name="backup_ops_database",
                    behavior="NEEDS_APPROVAL",
                    arguments=arguments,
                    requires_approval=True,
                    required_inputs=required_inputs,
                ),
            ),
        )

    selected_route = next((route for route in _LIVE_ROUTES if route[0].search(query)), None)
    wants_knowledge = _knowledge_relevant(query)
    if selected_route is None and not wants_knowledge:
        return _rejected("No supported knowledge topic or existing MCP capability matches this request.")

    knowledge_results: list[RetrievalResult] = []
    intent = "live_dba"
    if wants_knowledge:
        _, knowledge_results = _retrieve(query, documents, top_k)
        intent = "combined" if selected_route else "knowledge"

    actions: tuple[MCPAction, ...] = ()
    classification = "REFERENCE_ONLY" if selected_route is None else "READ_ONLY"
    status = "PLANNED"
    explanation = "Retrieved reference knowledge only; no MCP tool was called."
    if selected_route is not None:
        _, tool_name, behavior = selected_route
        if tool_name not in available_tools:
            return _rejected("The matching capability is not present in the discovered MCP tool catalog.")
        actions = (MCPAction(
            tool_name=tool_name,
            behavior=behavior,
            requires_approval=(behavior == "READ_ONLY_SQL / WRITES_REPORT"),
        ),)
        if behavior == "READ_ONLY_SQL / WRITES_REPORT":
            classification = "WRITES_REPORT"
            status = "HOST_APPROVAL_REQUIRED"
            explanation = "This tool reads SQL data and writes the configured HTML report; honor the MCP host write prompt."
        elif wants_knowledge:
            explanation = "Reference knowledge was retrieved and a live read-only MCP tool was selected; no tool was called."
        else:
            explanation = "A live read-only MCP tool was selected; no tool was called."

    return AssistantPlan(
        intent=intent,
        classification=classification,
        status=status,
        explanation=explanation,
        knowledge_results=tuple(knowledge_results),
        mcp_actions=actions,
    )
