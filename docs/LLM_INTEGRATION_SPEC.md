# LLM Integration Specification — Development/Lab

## Status and design decision

For the Development/Lab test version, Codex is the external AI/LLM host. The Python application does not call the Codex CLI as an API and contains no model-provider client. Codex interprets the user's natural-language request and proposes a structured plan; the existing provider-independent Planner and plan-contract serializer validate and normalize that request. Phase 13 validates planning only: it does not invoke the Executor or call MCP tools.

The Python application remains independent of Codex and can later be hosted by another approved LLM provider using the same plan contract. No Azure OpenAI, Microsoft Foundry, Azure AI Search, other model provider, or cloud resource is introduced.

## Request-to-plan flow

```text
User request
   ↓
Codex host interprets intent and returns the defined structured plan proposal
   ↓
Existing Planner / provider-neutral plan validator checks supported intent and route
   ↓
Validated plan is shown to the user (planning only in this phase)
```

The plan is data, not an execution instruction. During Phase 13 it is not passed to the Executor. If later phases enable execution, the Executor must be the only orchestration execution layer and must pass SQL work only to the existing MCP server.

## Structured plan contract

The plan is a JSON object with this shape:

```json
{
  "request": "What databases are on my SQL Server?",
  "intent": "live_dba",
  "knowledge_required": false,
  "tool_required": true,
  "tool_name": "get_ops_database_inventory",
  "classification": "READ_ONLY",
  "parameters": {},
  "approval_required": false,
  "rejection_reason": null
}
```

Contract rules:

- `request` is sanitized. Credential assignments are redacted. Rejected raw SQL, shell requests, paths, and unsupported input are represented by a generic redacted marker; the raw request is not copied into the plan.
- `intent` comes from the existing Planner (`knowledge`, `live_dba`, `combined`, `state_change`, or `unsupported`).
- `knowledge_required` is true for knowledge-only and combined plans. RAG passages and citations are separate response evidence, not MCP parameters.
- `tool_required` indicates an existing MCP action is selected. `tool_name` is null when no tool is needed or the request is rejected.
- `classification` is `REFERENCE_ONLY`, `READ_ONLY`, `NEEDS_APPROVAL`, `READ_ONLY_SQL / WRITES_REPORT`, or `REJECT` as applicable.
- `parameters` contains only arguments already produced by the Planner and present in that tool's discovered schema. It must not contain SQL, command text, credentials, executable paths, or caller-supplied backup/report paths. The MCP server repeats all validation.
- `approval_required` is true for backup and any host write prompt. A backup can be planned only for a database accepted by the existing canonical validator; it is never executed in planning-only mode.
- `rejection_reason` is a sanitized explanation for `REJECT`, otherwise null. Do not expose raw exceptions, secrets, or configuration paths.

The only accepted `tool_name` values are:

`get_ops_database_inventory`, `get_ops_database_health`, `get_ops_database_size`, `get_ops_backup_status`, `get_ops_disk_space`, `get_ops_failed_jobs`, `get_ops_blocking_sessions`, `get_ops_long_running_queries`, `get_ops_server_health_report`, and `backup_ops_database`.

Unknown names or arguments fail closed. `get_ops_server_health_report` is classified `READ_ONLY_SQL / WRITES_REPORT`; its configured HTML report write and host prompt must be disclosed. `backup_ops_database` is `NEEDS_APPROVAL` and is not called during Phase 13.

## Component responsibilities

### Codex host

- Interpret natural-language requests and produce a plan proposal matching this contract.
- Ask clarifying questions where target, scope, or required inputs are missing.
- Never invent tools, parameters, SQL execution, or shell execution.
- Present the validated plan clearly; never claim that a planned tool call has run.

### Planner and plan validation

- Reuse `assistant.orchestrator.orchestrate_request` and its existing route outcomes.
- Normalize the existing `AssistantPlan` to the contract, check selected tools against the exact allowlist and discovered MCP catalog, and sanitize request/rejection text.
- Reject raw SQL, shell requests, unapproved/system database backup targets, unsupported tools/parameters, and caller-supplied paths.
- Delegate target canonicalization to the existing MCP validator where supplied. This is a preflight convenience, not a replacement for server validation.

### RAG

- Use only the local `rag` loader, chunker, and lexical retriever over approved local knowledge files.
- Return attributed `REFERENCE_KNOWLEDGE` evidence. Do not infer current server state from reference documents.

### Executor

- Is the sole application orchestration execution layer after planning-only validation is complete.
- When later enabled, execute read-only plans only through an initialized official MCP `ClientSession`; recheck the existing catalog and tool annotations.
- Never execute backup plans automatically. Preserve MCP elicitation approval and the MCP server's validation as mandatory controls.

### MCP server

- Is the only SQL execution boundary. Its static dispatcher, schemas, instance/database allowlists, protected-system-database checks, result sanitization, and elicitation behavior remain authoritative.
- Rejects invalid inputs even if a host or planner previously accepted them.

## Evidence, response, and approval behavior

Planning responses distinguish reference guidance from live tool evidence. A plan is labeled as pending and contains no live results. RAG citations retain document title/filename/section. MCP results may be attributed to the selected tool only after a later successful Executor call; plans must not be reported as observations.

For an approved target backup request, the plan may identify `backup_ops_database` with `NEEDS_APPROVAL`, but Phase 13 stops at the plan. In later execution, the MCP server's explicit elicitation is required; decline, missing approval, unavailable elicitation, invalid database, or system database fails closed. No conversational intent or LLM-produced boolean substitutes for MCP approval.

Reject arbitrary SQL, arbitrary PowerShell/CMD/shell, unknown tool names, system databases, unapproved targets, credential inputs, and user-selected backup destinations. Do not pass rejected source text into the plan. Sanitize any displayed request, plan, error, or result.

## Provider independence and future enterprise provider

Codex is an external Development/Lab host choice, not a Python dependency or API endpoint. Keep the plan schema and Python application provider-neutral. A future replacement may be an enterprise-approved provider only after security, identity, data classification, retention, residency, network, and audit requirements are reviewed. It must emit the same plan contract and remain subject to the same Planner validation, Executor boundary, RAG attribution, and MCP security controls. Provider migration must not add tools or broaden execution privileges.

## Validation scope and limitations

Automated tests exercise the existing Planner and its structured plan contract for representative natural-language requests, using the actual MCP tool catalog and existing canonical target validator. They do not call the Codex CLI/API, Executor, an MCP tool, SQL Server, or a backup operation. This validates deterministic plan behavior and the contract, not a live Codex-to-application transport or LLM semantic accuracy. Codex output must always be treated as untrusted input and validated before any future execution.
