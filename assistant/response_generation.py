"""Presentation-only generation for deterministic assistant execution results."""

from dataclasses import dataclass
import json
import re
from typing import Any

from .response import AssistantResponse, sanitize_untrusted_text


_PRIVATE_KEYS = {
    "password", "passwd", "pwd", "token", "secret", "clientsecret",
    "accesstoken", "connectionstring", "statementtext", "querytext",
    "blockedquery", "blockerquery", "errormessage", "physicalpath",
    "reportpath", "outputpath", "backuppath", "backupfiles", "backupdirectory",
}
_FAILURE_STATES = {"ERROR", "MCP_ERROR", "MCP_UNAVAILABLE"}


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _safe_value(value: Any) -> Any:
    """Keep structured result values while dropping sensitive fields and paths."""
    if isinstance(value, dict):
        return {
            str(key): _safe_value(item)
            for key, item in value.items()
            if _normalized_key(key) not in _PRIVATE_KEYS and not _normalized_key(key).endswith("path")
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if isinstance(value, str):
        return sanitize_untrusted_text(value)
    if value is None or type(value) in {bool, int, float}:
        return value
    return "[UNSUPPORTED_RESULT_VALUE]"


def _reference_text(references: tuple[dict[str, Any], ...]) -> str:
    return "\n".join(
        f"- {item.get('title', 'Reference')} - {item.get('section', 'Section')} "
        f"({item.get('filename', item.get('source', 'source'))}): {item.get('passage', '')}"
        for item in references
    )


def _live_text(results: tuple[dict[str, Any], ...]) -> str:
    lines = []
    for item in results:
        tool = item.get("tool", "MCP tool")
        if item.get("status") == "SUCCESS" and "data" in item:
            # Show only the sanitized returned structure; do not infer DBA findings.
            data = json.dumps(item["data"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            lines.append(f"- {tool} returned: {data}")
        else:
            lines.append(f"- {tool}: {item.get('error', 'No successful tool result was returned.')}")
    return "\n".join(lines)


@dataclass(frozen=True)
class DBAAssistantResponse:
    """A factual, attributed presentation with no execution capability."""

    status: str
    answer: str
    reference_context: tuple[dict[str, Any], ...] = ()
    live_results: tuple[dict[str, Any], ...] = ()
    tool_attempted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "answer": self.answer,
            "reference_context": [dict(item) for item in self.reference_context],
            "live_results": [dict(item) for item in self.live_results],
            "tool_attempted": self.tool_attempted,
        }


def generate_response(result: AssistantResponse) -> DBAAssistantResponse:
    """Present an existing Executor result; never plans or executes an operation."""
    if not isinstance(result, AssistantResponse):
        raise TypeError("A validated AssistantResponse is required.")

    references = tuple(
        {
            str(key): _safe_value(value)
            for key, value in source.items()
            if key in {
                "evidence_type", "title", "filename", "source", "category",
                "section", "passage", "relevance_score", "attribution",
            }
        }
        for source in result.knowledge_sources
    )
    live = tuple(
        {
            "tool": _safe_value(item.get("tool")),
            "source": _safe_value(item.get("source")),
            "status": _safe_value(item.get("status")),
            **(
                {"data": _safe_value(item["data"])}
                if item.get("status") == "SUCCESS" and "data" in item else {}
            ),
            **(
                {"error": "The MCP operation failed; details were withheld."}
                if item.get("status") in _FAILURE_STATES or item.get("status") == "ERROR" else {}
            ),
        }
        for item in result.mcp_results
    )

    status = result.status
    if status == "REJECTED":
        answer = "This request is not supported or permitted. No operation was executed."
    elif status in {"NEEDS_APPROVAL", "NEEDS_INPUT"}:
        backup_requested = any(item.get("tool") == "backup_ops_database" for item in result.planned_actions)
        answer = (
            "A backup requires explicit approval. No backup was executed."
            if backup_requested
            else "This operation requires approval or additional input. No operation was executed."
        )
    elif status == "APPROVAL_REQUIRED":
        answer = "The health report writes a file and requires host approval. No report was generated."
    elif status in _FAILURE_STATES:
        answer = "The live DBA tool execution failed. This attempt did not establish whether a database issue exists."
    elif status == "NO_MATCH":
        answer = "No matching local reference material was found; no live SQL conclusion is available."
    elif references and live:
        answer = (
            "Reference context (documentation, not live SQL evidence):\n"
            f"{_reference_text(references)}\n\n"
            f"Live MCP result:\n{_live_text(live)}"
        )
    elif references:
        answer = (
            "Reference information from local knowledge (not verified against live SQL Server data):\n"
            f"{_reference_text(references)}"
        )
    elif live:
        answer = f"Live/current MCP tool result (returned values, without inference):\n{_live_text(live)}"
    else:
        answer = sanitize_untrusted_text(result.explanation)

    return DBAAssistantResponse(
        status=status,
        answer=sanitize_untrusted_text(answer),
        reference_context=references,
        live_results=live,
        tool_attempted=result.dispatch_performed,
    )
