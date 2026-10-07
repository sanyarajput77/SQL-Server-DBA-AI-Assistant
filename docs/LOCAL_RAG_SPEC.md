# Local RAG Prototype Specification

## Status and architecture

This phase adds a small, local lexical retrieval prototype over the generic Markdown files under `docs/knowledge/`. Its pipeline is:

```text
configured knowledge directory -> Markdown loader -> deterministic chunks -> lexical ranking -> attributed results
```

The implementation is the `rag` Python package and uses the Python standard library. It is standalone: it is not called from the MCP server, Codex, PowerShell, or a live SQL connection. It adds no tool and performs no database operation.

## Document loading

`rag.loader.load_documents()` reads direct-child `.md` files from the configured knowledge directory (by default `docs/knowledge/`). It extracts the first level-one heading as the title, derives a simple category from the filename, and retains the file content, filename, and relative source filename. Loading is non-recursive. Requested filenames must be direct-child Markdown names; absolute paths, separators, traversal segments, missing files, and symlinks that resolve outside the configured root are rejected. A caller query is never used as a path or file selector. The configured directory is an application setting, not user-query input.

Returned attribution uses the filename rather than an absolute path, so retrieval does not disclose the machine's checkout location.

## Deterministic chunking

`rag.chunker.chunk_documents()` splits documents at Markdown heading and paragraph boundaries, preserves heading context, and applies stable character-size limits. Oversized blocks use deterministic overlapping character windows. Each chunk keeps filename, title, relative source path, category, section, and stable per-document ordinal. The default size and overlap are prototype defaults, not an evaluated embedding-model token budget.

## Retrieval

`rag.retriever.retrieve()` tokenizes query and chunk text and scores lexical overlap with a normalized TF-IDF-like weight. It returns only positive-match chunks, sorts ties deterministically, and accepts a positive configurable `top_k`. Empty or tokenless queries and invalid `top_k` values raise clear `ValueError`s; an unknown topic returns an empty list. Results include the source document, matching chunk, relevance score, and an attribution string.

This is keyword/lexical retrieval. It does not understand paraphrases, intent, synonyms, or semantic similarity, and a numeric relevance score is only a ranking signal—not confidence or proof. The query is treated as ordinary text; it cannot choose a file, filesystem path, command, or SQL operation. Results contain a relative source filename, not arbitrary filesystem paths.

## Source attribution and knowledge boundaries

Every result identifies its source document and section, and includes the matched chunk for citation and inspection. A future assistant integration should cite that attribution separately from a live tool result. Retrieved guidance describes documentation; it does not establish current SQL Server state. This prototype has no live-result integration and does not call MCP. It must not be used to infer current health, backups, blocking, or disk capacity.

## Limitations

- The two sample documents are generic educational notes, not authoritative or organization-approved procedures.
- There is no ingestion manifest, document authorization policy, source freshness check, approval workflow, parser for PDF/Office formats, telemetry, or index persistence.
- Retrieval is in-memory and reloads Markdown files when the application loads them.
- Lexical scoring misses semantically related wording when query terms differ and can rank repeated common terms highly.
- Chunking uses characters and simple Markdown boundaries, not model token counts or evaluated semantic boundaries.
- There are no embeddings, vector search, hybrid search, reranking, access-filtered multi-user retrieval, or generated answer/citation layer.

## Future embedding/vector retrieval

A future phase may add approved embeddings and a vector or hybrid retrieval backend after defining document authorization, classification enforcement, model/data residency, retention, evaluation, source updates, and deletion guarantees. It should preserve document/chunk metadata and source attribution, keep lexical fallback available where useful, and evaluate retrieval on representative DBA questions. Embedding or vector dependencies are not part of this prototype.

## Future Azure AI Search migration

Azure AI Search is a possible future implementation, not a current dependency. The loader/chunk metadata can later map to provider-neutral records and then to Azure AI Search fields, with an approved embedding/vectorization strategy, hybrid queries, authorization filters, and citations. Before adding it, verify current service/API capabilities, identity and network design, data residency, retention, and deletion behavior. Implement offline mapping/contract tests and mocked retrieval first; create or connect to Azure resources only under a separately approved later task. Do not add another MCP server merely for this prototype.
