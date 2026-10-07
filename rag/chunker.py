"""Deterministic paragraph-aware chunking for local Markdown documents."""

from dataclasses import dataclass

from .loader import KnowledgeDocument


@dataclass(frozen=True)
class KnowledgeChunk:
    filename: str
    title: str
    source_path: str
    category: str
    chunk_index: int
    section: str
    content: str


def _split_oversized(text: str, max_chars: int, overlap: int) -> list[str]:
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return pieces


def chunk_documents(
    documents: list[KnowledgeDocument], *, max_chars: int = 900, overlap: int = 100
) -> list[KnowledgeChunk]:
    """Create stable chunks, retaining document identity and heading context."""

    if max_chars < 1:
        raise ValueError("max_chars must be at least 1.")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap must be non-negative and smaller than max_chars.")

    chunks: list[KnowledgeChunk] = []
    for document in documents:
        section = document.title
        blocks: list[tuple[str, str]] = []
        current: list[str] = []
        current_section = section

        def flush() -> None:
            nonlocal current
            body = "\n\n".join(current).strip()
            if body:
                blocks.append((current_section, body))
            current = []

        for line in document.content.splitlines():
            if line.startswith("## "):
                flush()
                current_section = line[3:].strip() or document.title
                current.append(line.strip())
            elif not line.strip():
                if current and current[-1] != "":
                    current.append("")
            else:
                current.append(line.rstrip())
        flush()

        # Merge adjacent short blocks only when they share the same heading.
        pending_section = ""
        pending_text = ""
        per_document: list[tuple[str, str]] = []
        for block_section, block_text in blocks:
            if len(block_text) > max_chars:
                if pending_text:
                    per_document.append((pending_section, pending_text))
                    pending_section, pending_text = "", ""
                per_document.extend(
                    (block_section, piece)
                    for piece in _split_oversized(block_text, max_chars, overlap)
                )
            elif not pending_text:
                pending_section, pending_text = block_section, block_text
            elif pending_section == block_section and len(pending_text) + 2 + len(block_text) <= max_chars:
                pending_text = f"{pending_text}\n\n{block_text}"
            else:
                per_document.append((pending_section, pending_text))
                pending_section, pending_text = block_section, block_text
        if pending_text:
            per_document.append((pending_section, pending_text))

        for index, (chunk_section, text) in enumerate(per_document):
            chunks.append(
                KnowledgeChunk(
                    filename=document.filename,
                    title=document.title,
                    source_path=document.source_path,
                    category=document.category,
                    chunk_index=index,
                    section=chunk_section,
                    content=text,
                )
            )
    return chunks
