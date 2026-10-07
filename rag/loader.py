"""Load Markdown knowledge documents from one configured directory only."""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable


DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "docs" / "knowledge"


@dataclass(frozen=True)
class KnowledgeDocument:
    """A Markdown document and safe, relative source metadata."""

    filename: str
    title: str
    source_path: str
    category: str
    content: str


def _category_for(filename: str) -> str:
    stem = Path(filename).stem
    if stem.startswith("sample-"):
        stem = stem[len("sample-") :]
    return stem.replace("_", "-").casefold()


def _validate_filename(value: str | Path) -> str:
    raw = str(value)
    candidate = Path(raw)
    # Accept only a single Markdown filename. Do not interpret caller input as
    # a path relative to or outside the configured knowledge directory.
    if candidate.is_absolute() or candidate.name != raw or raw in {".", ".."}:
        raise ValueError("Knowledge document must be a filename inside the configured directory.")
    if "\\" in raw or "/" in raw or candidate.suffix.casefold() != ".md":
        raise ValueError("Knowledge document must be a Markdown filename inside the configured directory.")
    return raw


def load_documents(
    knowledge_dir: str | Path | None = None,
    filenames: Iterable[str | Path] | None = None,
) -> list[KnowledgeDocument]:
    """Load direct-child Markdown files from the configured directory.

    ``knowledge_dir`` is trusted application configuration, not user query
    input. ``filenames`` may contain only direct-child Markdown filenames.
    Returned source paths are filenames, never absolute/internal paths.
    """

    if knowledge_dir is not None:
        root = Path(knowledge_dir)
    else:
        configured_directory = os.environ.get("DBA_KNOWLEDGE_DIRECTORY")
        deployment_environment = os.environ.get("DBA_AUTOMATION_ENV")
        if configured_directory:
            root = Path(configured_directory)
            if not root.is_absolute():
                root = Path(__file__).resolve().parents[1] / root
        elif deployment_environment == "Production":
            raise ValueError("Production requires an explicit DBA_KNOWLEDGE_DIRECTORY.")
        else:
            root = DEFAULT_KNOWLEDGE_DIR
    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("Configured knowledge directory is unavailable.") from exc
    if not resolved_root.is_dir():
        raise ValueError("Configured knowledge directory is not a directory.")

    if filenames is None:
        requested = sorted(path.name for path in resolved_root.glob("*.md"))
    else:
        requested = sorted({_validate_filename(name) for name in filenames})

    documents: list[KnowledgeDocument] = []
    for filename in requested:
        safe_name = _validate_filename(filename)
        candidate = resolved_root / safe_name
        try:
            resolved_candidate = candidate.resolve(strict=True)
            resolved_candidate.relative_to(resolved_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("Knowledge document must remain inside the configured directory.") from exc
        if not resolved_candidate.is_file() or resolved_candidate.suffix.casefold() != ".md":
            raise ValueError("Knowledge source must be a Markdown file inside the configured directory.")

        content = resolved_candidate.read_text(encoding="utf-8")
        title = next(
            (line[2:].strip() for line in content.splitlines() if line.startswith("# ") and line[2:].strip()),
            Path(safe_name).stem.replace("-", " ").replace("_", " ").title(),
        )
        documents.append(
            KnowledgeDocument(
                filename=safe_name,
                title=title,
                source_path=safe_name,
                category=_category_for(safe_name),
                content=content,
            )
        )
    return documents
