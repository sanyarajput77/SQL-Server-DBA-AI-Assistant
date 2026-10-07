"""Local, lexical retrieval prototype for approved generic DBA Markdown notes."""

from .chunker import KnowledgeChunk, chunk_documents
from .loader import KnowledgeDocument, load_documents
from .retriever import RetrievalResult, retrieve

__all__ = [
    "KnowledgeChunk",
    "KnowledgeDocument",
    "RetrievalResult",
    "chunk_documents",
    "load_documents",
    "retrieve",
]
