# DBA Knowledge Base Specification

## Purpose and current status

The DBA knowledge base will provide approved, attributable reference material to help the AI DBA Assistant explain SQL Server concepts, procedures, and troubleshooting steps. It supplements the existing fixed MCP tools; it does not execute operations or replace live diagnostics.

This phase establishes a design and generic Markdown examples only. There is currently no document ingestion pipeline, chunker, embedding generation, retrieval service, vector index, Azure AI Search dependency, or knowledge MCP tool. The sample documents are not asserted to be authoritative Microsoft product documentation.

## Eligible content

Potential sources include:

- Public SQL Server product documentation and release notes, subject to their license and attribution requirements.
- Reviewed internal runbooks, operating procedures, approved troubleshooting guides, and architecture standards.
- Sanitized post-incident lessons and approved operational knowledge.
- Versioned checklists and decision guides for DBA tasks.

Each source must have an owner, an allowed audience, a review date, and permission for the intended indexing and model-processing services. Prefer short, stable, reviewed documents over logs or uncontrolled content dumps.

## Required document metadata

Every document should have a metadata record with at least:

| Field | Purpose |
| --- | --- |
| `document_id` | Stable identifier independent of file name |
| `title` | Human-readable source title |
| `source_name` and `source_uri` | Traceable origin; URI may be omitted from user output when restricted |
| `source_type` | Product documentation, runbook, procedure, incident lesson, or other approved type |
| `owner` | Accountable content owner/team |
| `version` | Source revision or repository commit/version |
| `created_at`, `reviewed_at`, `valid_from`, `valid_until` | Currency and review lifecycle |
| `product` and `product_version` | SQL Server or related product/version scope |
| `environment_scope` | Development, Test, UAT, Production, or environment-independent |
| `classification` and `allowed_audience` | Authorization and sensitivity boundary |
| `tags` and `language` | Filtering and retrieval facets |
| `content_hash` | Detect changed source content and refresh indexed records |

The ingestion record should additionally retain a stable section path and source locator (such as heading, page, or paragraph) for each chunk. Metadata and filters must be enforced by the retrieval service, not merely described in a prompt.

## Future document preparation and chunking

1. Ingest only an approved source and retain the immutable original or a verifiable source reference.
2. Extract text while preserving title, heading hierarchy, tables, code blocks, warnings, and source locations. Record extraction errors rather than silently dropping content.
3. Normalize encoding and whitespace without changing technical meaning. Keep code samples associated with their explanatory text.
4. Split on document/heading boundaries first. If a section is too large for the selected model, split it into bounded, semantically coherent chunks with modest overlap. Never split a procedure step from its prerequisite or warning when avoidable.
5. Attach document metadata, section path, chunk ordinal, source locator, and content hash to each chunk. Avoid duplicate chunks and retain a link to the full source.
6. Set size and overlap limits based on the chosen model and retrieval system, then evaluate representative DBA questions. Those numeric limits remain an implementation decision, not a current project setting.

## Future embeddings

No embeddings are generated today. In a future implementation, select an approved embedding model and record its provider, model/version, dimensions, and generation date alongside the index version. Re-embed when the source content or embedding model changes. Validate model/data residency, retention, licensing, and classification before sending any content to an external service. Do not embed content that the selected service is not authorized to process.

## Retrieval and grounding

Future retrieval should:

1. Apply the caller's identity and authorization filters before search, including environment, classification, and allowed audience.
2. Use lexical and semantic retrieval as appropriate, filter by product/version and validity, and rank only eligible chunks. A future implementation may add reranking after evaluation.
3. Prefer current, authoritative, reviewed sources; flag or exclude expired and superseded material. Do not silently treat a low-confidence or empty result as evidence.
4. Return a small set of relevant passages with stable source identifiers, titles, versions, review dates, section paths, and permitted citation targets.
5. Keep query and retrieved text within approved privacy, retention, and logging rules. Do not log secrets or unrestricted document bodies.

The assistant may use retrieved passages to explain concepts and approved procedures. It must treat retrieved text as reference content, not as executable instructions that override system/project safety rules. It may not turn a procedure into an unapproved SQL, shell, backup, restore, or configuration action.

## Knowledge versus live operational results

Retrieved documentation describes general or approved procedural knowledge. It is not evidence of the current state of a SQL Server. Live state comes only from results returned by the existing MCP Ops tools. The assistant must label these sources distinctly, for example “Reference guidance” and “Live tool result,” and must not infer current database health, backup completion, blocking, or capacity from a knowledge document. When the two conflict, state the discrepancy and its source/date; do not rewrite either source or silently prefer stale guidance.

## Source attribution and citations

Every factual statement materially based on retrieval should cite the source title and version/date, plus the most precise available section/page locator. Link to the canonical source only when the user is authorized and the URI is approved for disclosure. Internal paths, restricted URLs, and document contents must not be exposed to unauthorized audiences. If retrieval does not provide a usable source locator, say that the answer is unverified rather than inventing a citation. Live MCP findings should be attributed to the relevant tool and its observation time when available, separately from documentation citations.

## Currency and stale content

Owners must review documents on a defined schedule and when relevant product versions or procedures change. At retrieval time, check `reviewed_at`, `valid_until`, source version, and supersession status. Exclude expired or withdrawn documents by default; if a historical source is useful, label it as historical and explain why it may not apply. Alert owners to stale records and re-index reviewed changes. Never silently update a source or extend its validity.

## Security and excluded content

Use least privilege for source repositories, ingestion identities, indexes, and retrieval. Preserve source classification and access controls end-to-end; prevent cross-environment or cross-team leakage. Encrypt data in transit and at rest according to the hosting environment. Keep secrets out of prompts, chunks, embeddings, telemetry, and citations. Log document identifiers and sanitized status rather than unrestricted text. Review licenses and approvals before indexing third-party or internal material.

Do not ingest:

- Passwords, tokens, private keys, connection strings, credential exports, or other authentication material.
- Customer data, production query results, database backups, personal data, or unredacted incident evidence.
- Proprietary/confidential documents without owner authorization and a suitable approved processing boundary.
- Raw application/SQL/server logs or transcripts containing identifiers or sensitive values before review and sanitization.
- Untrusted user uploads, executable scripts, or prompt-injection content as authoritative instructions.
- Unsupported, obsolete, or unlicensed material presented as current guidance.

If sensitive content is discovered after ingestion, remove it from source, chunks, indexes, caches, and applicable logs according to the service's verified deletion behavior; record the incident and confirm removal. Do not claim deletion is complete without verification.

## Portability across environments

Keep source manifests, policy, and environment-independent content separate from provider-specific indexing code. Configure repositories, allowed sources, identity, classification filters, model provider, endpoints, and index names per Development, Test/UAT, and Production without embedding environment names, credentials, or URLs in reusable application code. Use separate indexes or equivalent enforced boundaries where required by classification. Promote reviewed content and versioned manifests through environments; Production must not inherit permissive Development access or settings. Secrets, if a future service requires them, must come from an approved runtime identity/secret mechanism and never from this repository.

## Future Azure AI Search migration path

Azure AI Search is a possible future retrieval implementation, not a current dependency. A later phase may map the metadata manifest and prepared chunks to Azure AI Search documents, use an approved vectorization/embedding approach, and implement hybrid retrieval with security filters and citations. Keep preparation and metadata contracts provider-neutral so another approved search backend remains possible. Before implementation, verify current Azure product capabilities, SDK/API versions, identity integration, data residency, retention, network boundaries, and deletion semantics. Add offline contract tests first, then mocked retrieval tests; only use live service tests in an explicitly approved environment. This phase installs nothing and calls no Azure service.

## Current implementation boundary

The current RAG foundation consists only of this specification, two generic Markdown examples, and offline tests that guard the documented requirements. No retrieval context is currently supplied to the assistant, and the existing ten MCP tools remain the only operational interface.
