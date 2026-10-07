"""Simple lexical retrieval; this module does not perform semantic search."""

from collections import Counter
from dataclasses import dataclass
import math
import re

from .chunker import KnowledgeChunk
from .loader import KnowledgeDocument


_TOKEN_PATTERN = re.compile(r"[a-z0-9]+", re.IGNORECASE)


@dataclass(frozen=True)
class RetrievalResult:
    source_document: KnowledgeDocument
    relevant_chunk: KnowledgeChunk
    relevance_score: float
    source_attribution: str


def _tokens(text: str) -> list[str]:
    return [token.casefold() for token in _TOKEN_PATTERN.findall(text)]


def retrieve(
    query: str,
    chunks: list[KnowledgeChunk],
    documents: list[KnowledgeDocument],
    *,
    top_k: int = 3,
) -> list[RetrievalResult]:
    """Rank chunks with normalized TF-IDF-like lexical overlap.

    Query text is treated only as text. It is never interpreted as a path,
    command, SQL statement, or filesystem selector.
    """

    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must contain at least one searchable term.")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        raise ValueError("top_k must be a positive integer.")

    query_terms = set(_tokens(query))
    if not query_terms:
        raise ValueError("query must contain at least one searchable term.")
    if not chunks or not documents:
        return []

    document_by_filename = {document.filename: document for document in documents}
    tokenized = [_tokens(chunk.content) for chunk in chunks]
    document_frequency: Counter[str] = Counter()
    for terms in tokenized:
        document_frequency.update(set(terms))

    ranked: list[tuple[float, KnowledgeChunk]] = []
    corpus_size = len(chunks)
    for chunk, terms in zip(chunks, tokenized):
        frequencies = Counter(terms)
        score = 0.0
        for term in query_terms:
            count = frequencies.get(term, 0)
            if count:
                inverse_frequency = math.log(1 + corpus_size / (1 + document_frequency[term]))
                score += inverse_frequency * (1 + math.log(count))
        score /= len(query_terms)
        if score > 0 and chunk.filename in document_by_filename:
            ranked.append((score, chunk))

    ranked.sort(key=lambda item: (-item[0], item[1].filename.casefold(), item[1].chunk_index))
    results: list[RetrievalResult] = []
    for score, chunk in ranked[:top_k]:
        source = document_by_filename[chunk.filename]
        results.append(
            RetrievalResult(
                source_document=source,
                relevant_chunk=chunk,
                relevance_score=score,
                source_attribution=f"{source.title} — {source.filename}, section: {chunk.section}",
            )
        )
    return results
