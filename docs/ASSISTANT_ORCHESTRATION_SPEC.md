# AI Assistant Orchestration Specification

## Purpose and boundary

The orchestration layer creates a structured plan that can combine the existing local lexical RAG package with the existing ten-tool MCP catalog. It is a planner, not another MCP server and not an execution engine. It does not call MCP, PowerShell, SQL Server, or external services. An MCP host may dispatch only the selected existing tool, and the existing MCP server remains the authority for schema checks, approved target validation, sanitization, and approval elicitation.

The orchestrator reuses `rag.load_documents`, `rag.chunk_documents`, and `rag.retrieve`. It accepts the discovered tool catalog from the MCP host and selects only a matching listed tool. It does not reimplement the MCP bridge or database/instance validators. For backup preflight, a host may supply the existing MCP canonical database validator; the MCP server must still validate again at execution time.

## Intent classification

Classify each request into one of these outcomes:

| Intent | Meaning | Result |
| --- | --- | --- |
| `knowledge` | A supported conceptual/procedural question with no live-state request | Local RAG references only (`REFERENCE_ONLY`) |
| `live_dba` | A request for current SQL Server state | One matching existing read-only MCP tool (`READ_ONLY`) |
| `combined` | Both a knowledge question and a current-state question | RAG references plus a matching read-only MCP action |
| `state_change` | A backup request or other operation that changes state | `NEEDS_APPROVAL`; never invoke Ops automatically |
| `unsupported` | Arbitrary SQL/shell, unsupported action, invalid target, or no supported route | `REJECT`; no tool action |

Unsupported execution requests are rejected before routing or retrieval. In particular, raw SQL, arbitrary PowerShell/CMD/shell commands, executable paths, and user-supplied backup paths are not capabilities. If ambiguous whether a request is explanatory or asks for current state, ask a clarifying question rather than infer an operation.

## Knowledge retrieval

For knowledge intent, call the local loader/chunker/retriever over `docs/knowledge/`. The current retriever is lexical and in-memory. Return a small top-k set of passages with a positive match, source filename, title, category, section, and relevance score. Scores are ranking signals only. If there are no results, say that the local knowledge set has no matching reference; do not invent an answer or silently treat a no-match as evidence.

## MCP selection

For live intent, select only a tool name present in the catalog discovered from the existing MCP server. The planned read-only routes use the existing capabilities: database inventory, database health/size, backup status, disk space, failed jobs, blocking sessions, and long-running queries. A general server health report uses `get_ops_server_health_report`, which is `READ_ONLY_SQL / WRITES_REPORT`; the host write prompt applies and it must not be described as side-effect-free. Never create a tool if the catalog lacks the required capability.

The plan contains a tool name and any safely extracted fixed arguments. The orchestrator itself does not dispatch. At host handoff, the MCP server repeats all database and instance validation. A failed or unavailable canonical target preflight fails closed; client-side planning never replaces server checks.

## Combined RAG and live requests

When the question requests both explanation and current status, retrieve documentation and select the narrowest live read-only MCP tool. Example: “Why is blocking happening, and do I currently have blocking sessions?” returns reference passages from the blocking note and plans `get_ops_blocking_sessions`. “Which databases have backup problems and what should I check?” returns backup guidance and plans `get_ops_backup_status`.

Never infer live status from documentation. If MCP dispatch fails, report the sanitized tool failure and keep the retrieved guidance labeled as reference material. If retrieval has no match but a live request is valid, the live tool plan may still be returned; explain that no reference passage matched.

## Approval handling

Backup intent produces a `NEEDS_APPROVAL` plan only after a supplied existing MCP validator confirms that the database is allowlisted. An unapproved or unverifiable database is rejected before an approval prompt. If a required input such as backup type is absent, request it without assuming a default. The plan never starts a backup. The host must obtain explicit user approval through the existing MCP elicitation; only the MCP server's existing approval resolver may then dispatch `Backup-OpsDatabase`. Declined, absent, or unavailable approval fails closed. System databases remain prohibited. The client cannot supply a destination path.

The orchestration code exposes no approval bypass and does not accept raw confirmation booleans as a substitute for the MCP elicitation.

## Rejection handling

Return a clear `REJECT` result and no MCP actions for arbitrary SQL, arbitrary shell/PowerShell/CMD, caller-supplied paths, unapproved/system databases, unsupported requests, or missing catalog capabilities. Do not reinterpret a rejected request as a different operation. Do not return raw internal exceptions, secrets, absolute filesystem paths, or configuration contents.

## Attribution and evidence distinction

Every RAG result is labeled `REFERENCE_KNOWLEDGE` and includes the knowledge document title, filename, section, matching passage, and lexical relevance score. Every planned MCP action is labeled `LIVE_SQL_TOOL_PENDING`; it is not a result until the MCP call actually succeeds. Final responses must keep “Reference guidance” separate from “Live tool result,” attribute live observations to the tool/time when available, and never claim that a retrieved passage proves current database health, backup status, blocking, or capacity.

## Response structure

The planning response contains:

- `intent`, `classification`, `status`, and a concise `explanation`.
- `knowledge_sources`: zero or more attributed passages, clearly marked as reference knowledge.
- `mcp_actions`: zero or more selected existing tool names, behavior classification, fixed arguments, required inputs, and approval requirement.
- `dispatch_performed`: always `false` for this planner. Actual tool dispatch and results belong to the existing MCP host/server flow.

The assistant should explain reference material in its own words, preserve source attribution, summarize live results only after actual MCP output exists, disclose report file writes, and state when either source is unavailable. It must not merge general guidance and live evidence into a single unsupported claim.

## Tested scenarios and limits

Offline tests cover the representative seven requests from Phase 11, including combined retrieval/tool plans, unapproved `DatabaseA`, and raw SQL/shell rejection. Tests inspect the existing MCP catalog and use the existing canonical database validator for target preflight; they do not call an MCP tool, connect to SQL Server, or perform a backup. This layer uses deterministic phrase/keyword rules and the local lexical retriever; it is not an LLM intent model or semantic RAG system. Human review and the existing MCP safeguards remain required.
