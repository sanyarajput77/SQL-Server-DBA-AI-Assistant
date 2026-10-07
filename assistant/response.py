"""Structured assistant response composition and MCP result sanitization."""

from dataclasses import dataclass, field
import json
import re
from typing import Any

from rag import RetrievalResult


_SECRET_VALUE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|secret|client_secret|access_token|connectionstring)\s*[:=]\s*([^\s,;]+)"
)
_WINDOWS_PATH = re.compile(r"(?i)(?:\b[A-Z]:\\|\\\\[^\\\s]+\\)[^\s\"',;]*")
_UNIX_PATH = re.compile(r"(?<![\w:])/(?:[^/\s]+/)*[^/\s]*")
_SENSITIVE_FIELDS = {
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "clientsecret",
    "accesstoken",
    "connectionstring",
    "statementtext",
    "querytext",
    "blockedquery",
    "blockerquery",
    "errormessage",
    "physicalpath",
    "reportpath",
    "outputpath",
    "backuppath",
    "backupfiles",
    "backupdirectory",
}


def _safe_text(value: str) -> str:
    value = _SECRET_VALUE.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    value = _WINDOWS_PATH.sub("[PATH_REDACTED]", value)
    return _UNIX_PATH.sub("[PATH_REDACTED]", value)


def sanitize_untrusted_text(value: str) -> str:
    """Sanitize request/response text using the shared secret and path filters."""

    return _safe_text(value)


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized in _SENSITIVE_FIELDS or normalized.endswith("path"):
                continue
            safe[str(key)] = _safe_value(item)
        return safe
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, str):
        return _safe_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[UNSUPPORTED_RESULT_VALUE]"


def sanitize_mcp_result(tool_name: str, result: Any) -> dict[str, Any]:
    """Create a sanitized, attributed result from the official MCP result shape."""

    if bool(getattr(result, "is_error", getattr(result, "isError", False))):
        return {
            "tool": tool_name,
            "source": "existing_mcp_server",
            "status": "ERROR",
            "error": "The MCP operation failed validation or execution; details were withheld.",
        }

    payloads: list[Any] = []
    for block in getattr(result, "content", ()) or ():
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                payloads.append(_safe_text(text))
            else:
                payloads.append(_safe_value(decoded))
        else:
            # Do not return image/audio/blob payloads through this text result layer.
            payloads.append({"type": getattr(block, "type", "unknown"), "omitted": True})
    data: Any = payloads[0] if len(payloads) == 1 else payloads
    return {
        "tool": tool_name,
        "source": "existing_mcp_server",
        "status": "SUCCESS",
        "data": data,
    }


@dataclass(frozen=True)
class AssistantResponse:
    status: str
    intent: str
    classification: str
    explanation: str
    knowledge_sources: tuple[dict[str, Any], ...] = ()
    planned_actions: tuple[dict[str, Any], ...] = ()
    mcp_results: tuple[dict[str, Any], ...] = ()
    dispatch_performed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "intent": self.intent,
            "classification": self.classification,
            "explanation": self.explanation,
            "knowledge_sources": [dict(item) for item in self.knowledge_sources],
            "planned_actions": [dict(item) for item in self.planned_actions],
            "mcp_results": [dict(item) for item in self.mcp_results],
            "dispatch_performed": self.dispatch_performed,
        }


def knowledge_attributions(results: tuple[RetrievalResult, ...] | list[RetrievalResult]) -> tuple[dict[str, Any], ...]:
    """Render RAG evidence with source citations and no absolute source paths."""

    output: list[dict[str, Any]] = []
    for result in results:
        source_path = result.source_document.source_path
        if re.match(r"(?i)^(?:[a-z]:[\\/]|\\\\|/)", source_path):
            source_path = result.source_document.filename
        output.append(
            {
                "evidence_type": "REFERENCE_KNOWLEDGE",
                "title": _safe_text(result.source_document.title),
                "filename": result.source_document.filename,
                "source": source_path,
                "category": result.source_document.category,
                "section": _safe_text(result.relevant_chunk.section),
                "passage": _safe_text(result.relevant_chunk.content),
                "relevance_score": result.relevance_score,
                "attribution": _safe_text(result.source_attribution),
            }
        )
    return tuple(output)
