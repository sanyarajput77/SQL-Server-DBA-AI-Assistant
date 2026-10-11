# Project Progress

This file tracks the implementation milestones for the safe, portable SQL Server DBA automation system.

- [x] Phase 0 — Safety/project setup
- [x] Phase 1 — PowerShell automation
- [x] Phase 2 — Configuration
- [x] Phase 3 — Pester tests
- [x] Phase 4 — Setup/validation
- [x] Phase 5 — Least privilege (functionally complete for the current lab; live grant validation is deferred due to Development/Lab TLS connectivity)
- [x] Phase 6 — MCP Server: COMPLETE
- [x] Phase 6.5 — MCP Protocol Validation: COMPLETE
- [x] Phase 7 — MCP Host Integration: COMPLETE
- [x] Phase 8 — AI DBA Assistant Behavior: COMPLETE
- [x] Phase 9 — DBA Knowledge / RAG Foundation: COMPLETE
- [x] Phase 10A — Local RAG Prototype: COMPLETE
- [x] Phase 11 — AI Assistant Integration: COMPLETE
- [x] Phase 12 — Controlled Assistant Execution: COMPLETE
- [x] Phase 13 — LLM Integration (Development/Lab planning validation): COMPLETE
- [x] Phase 14A — LLM Plan Intake and Validation: COMPLETE
- [x] Phase 14B — Validated Plan to Executor: COMPLETE
- [x] Phase 14C — Real MCP Execution Boundary Validation: COMPLETE (protocol and dispatch validated; SQL operation deferred due to documented Lab TLS limitation)
- [x] Phase 14D — Controlled DBA Response Generation: COMPLETE
- [x] Phase 15A — Approval Contract and Fail-Closed Safety Boundary: COMPLETE
- [x] Phase 15B — Approval-Aware Application Execution: COMPLETE
- [x] Phase 15C — Safety Hardening and Audit Review: COMPLETE (PowerShell Pester regression test remains unexecuted because the environment denies Pester's temporary registry key)
- [x] Phase 16A — Deterministic DBA Monitoring Foundation: COMPLETE (offline/fake-session validation; live SQL remains deferred)
- [x] Phase 16B — Deterministic Health Evaluation: COMPLETE (external policy and offline/fake-result validation)
- [x] Phase 16C — DBA Health Reporting: COMPLETE (in-memory text, HTML, and JSON reports; offline deterministic validation)
- [x] Phase 16D — Scheduled Monitoring Workflow: COMPLETE (manual invocation and collision-safe report artifact persistence; scheduler deferred)
- [x] Phase 17A — Production Readiness Assessment: COMPLETE (documentation-only gap assessment; no production changes)
- [x] Phase 17B — Environment and Configuration Hardening: COMPLETE (explicit environment selection; schema and runtime checks; no production targets supplied)
- [x] Phase 18 — Comprehensive Documentation Audit and Standardization: COMPLETE
- [x] Final validation: COMPLETE

---

## Detailed Status

### Phase 0 — Safety/project setup
- [x] Create `AGENTS.md` with 24 mandatory safety rules
- [x] Initialize `PROGRESS.md`
- [x] Create directory skeleton (`DbaAutomation/`, `Config/`, `Logs/`, `Reports/`, `mcp-server/`, `Tests/`, `docs/`)
- [x] Create `.gitignore` to prevent secret/log leakage

### Phase 1 — PowerShell automation
- [x] Implement the ten requested `Ops` capabilities in `DbaAutomation/Public/`
- [x] Add configuration loading, instance allowlist validation, protected database validation, session initialization, and sanitized structured logging helpers
- [x] Import the module and confirm all requested public functions are exported
- [x] Parse all PowerShell source files without syntax errors
- [x] Verify the dbatools cmdlets and parameters used against installed dbatools 2.8.4
- [x] Keep backup destination directory creation inside `ShouldProcess` so `-WhatIf` does not create it
- [x] Mocked unit behavior is covered by Phase 3 tests; live SQL Server behavior remains unverified

### Phase 2 — Configuration
- [x] Add external Development configuration and Test/Production example configurations
- [x] Add Development, Test, UAT, and Production example configurations
- [x] Add formal JSON schema for required settings, types, accepted environments, thresholds, and timeouts
- [x] Validate configuration schema and semantic rules in `Get-OpsConfig`
- [x] Resolve relative output directories from the selected configuration file and remove fixed project-path fallbacks
- [x] Keep `MSSQL-VM1` in Development configuration only; no reusable function embeds that instance name
- [x] Restrict `TrustServerCertificate` to Development/Lab and avoid applying it to shared dbatools settings
- [x] Pass explicit per-connection trust policy through verified `Connect-DbaInstance` switches and use its connected object for SQL operations
- [x] Add Pester tests for valid environment files, required fields, allowlist consistency, ranges, secrets, trust policy, paths, and fail-closed loading
- [x] Verify dbatools 2.8.4 `Connect-DbaInstance` parameters and connected-server-object use against installed help and official docs
- [x] Pester unit suite: 28 passed, 0 failed
- [x] Run both live read-only integration tests against configured MSSQL-VM1: 0 passed, 2 failed during TLS pre-login negotiation; tests were not skipped
- [x] Full suite with integration enabled: 30 total, 28 passed, 2 failed, 0 skipped; dbatools shared trust settings remained false after the run
- [x] Document the live Development/Lab TLS pre-login failure as an environment limitation; resolution is deferred and no TLS troubleshooting or configuration changes were performed

### Phase 3 — Pester tests
- [x] Add mocked unit coverage for all ten public Ops functions
- [x] Cover missing database, protected system database, unapproved instance, invalid backup type, invalid threshold, invalid configuration, and `Backup-OpsDatabase -WhatIf`
- [x] Separate opt-in, read-only SQL Server integration checks from unit tests
- [x] Run Pester 5.7.1 on PowerShell 7.6.6 with Pester's TestRegistry provider disabled: latest full suite 30 total, 28 passed, 2 failed (live integration), 0 skipped
- [x] Create `README.md` documenting setup, architecture, configuration, testing, and limitations
- [x] Live SQL Server integration was attempted: inventory and health both failed before diagnostic queries during TLS pre-login negotiation; documented as a Development/Lab limitation, with no backup or restore

### Phase 4 — Setup/validation
- [x] Inspect project structure and confirm no prior `setup.ps1` existed
- [x] Add repeatable prerequisite/configuration validation and missing-directory creation guarded by `ShouldProcess`
- [x] Detect PowerShell 7, dbatools, SqlServer, Pester 5.7.1, Python, project `.venv`, and MCP SDK without installing software
- [x] Validate all supplied environment configurations through `Get-OpsConfig`
- [x] Run only unit/configuration tests from setup; integration tests are not invoked by setup
- [x] Run `setup.ps1 -WhatIf`: 28 tests passed, 0 failed, 0 skipped; prerequisite and configuration checks passed
- [x] Document setup behavior, current prerequisites, and the Development/Lab live SQL TLS limitation in `README.md`

### Phase 5 — Least privilege
- [x] Enforce `ApprovedDatabases` in database-targeting functions before SQL/dbatools operations; block system and unapproved targets; scope blocking and long-running query results to approved databases
- [x] Replace broad SQL Agent history cmdlet with a fixed, parameterized query of only `msdb.dbo.sysjobs` and `msdb.dbo.sysjobhistory`; document a custom SELECT-only role rather than SQLAgentReaderRole
- [x] Replace Windows-admin-dependent `Get-DbaDiskSpace` with SQL-visible `sys.dm_os_volume_stats` for volumes hosting approved database files; explicitly report that separate backup-path volumes are not verified
- [x] Remove `RESTORE VERIFYONLY` from `Backup-OpsDatabase`; validate dbatools completion result and enable backup checksums; no `CREATE DATABASE` grant. Strong independent validation is outside the automation identity.
- [x] Review every public function's permission scope and update the SQL reference to state database-scoped, `msdb`-scoped, and server-wide permissions/limitations without granting broad roles
- [x] Add unit coverage for approved/unapproved/system database cases, invalid instance, filtered server diagnostics, job history query scope, backup completion and WhatIf behavior
- [x] Update `README.md` and `docs/sql/create-ai-dba-login.sql` to match current implementation and permission requirements
- [x] Run complete Pester 5.7.1 unit/configuration suite after Phase 5 changes: 38 total, 38 passed, 0 failed, 0 skipped
- [ ] Deferred Development/Lab limitation: verify exact effective grants and dbatools metadata access with the intended identities after the documented TLS connectivity issue is resolved. No TLS troubleshooting, SQL connection, or permission changes were performed for this validation.

### Phase 6 — MCP server
- [x] Inspect the installed official MCP Python SDK 2.2.0 and current Ops public function contracts
- [x] Expose the ten existing Ops capabilities only; `Get-OpsConfig` is not exposed, and neither raw SQL nor arbitrary shell commands are accepted
- [x] Use a static PowerShell dispatcher with an operation-specific parameter allowlist; no dynamic command execution
- [x] Keep nine diagnostic/report tools separate from destructive `Backup-OpsDatabase`; backup is annotated `NEEDS_APPROVAL` and gated by MCP user elicitation
- [x] Add Python unit tests, including MCP tool-surface/schema checks and a real PowerShell bridge rejection check that does not import the module or contact SQL Server
- [x] Document stdio startup, process identity, configuration inheritance, test command, and approval behavior in `README.md`
- [x] Canonicalize SQL instance and database inputs against configured allowlists before invoking PowerShell; reject system and unapproved databases
- [x] Confirm no tool accepts SQL, command text, executable paths, backup destinations, or unsupported parameters; PowerShell dispatch remains static
- [x] Sanitize Ops results, SDK validation failures, process errors, and audit logs so credentials, query text, and internal paths are not returned or logged
- [x] Gate backup with MCP elicitation; decline or unavailable elicitation fails closed before PowerShell dispatch
- [x] Verify discovery returns exactly the ten predefined Ops tools and stdio startup/clean EOF works without a SQL connection
- [x] Run Python MCP tests in the existing `.venv`: 19 passed, 0 failed
- [x] Run full Pester tree: 40 total, 38 passed, 0 failed, 2 read-only SQL integration tests skipped by default
- [x] Phase 7 verified one live read-only MCP inventory request against configured Development instance MSSQL-VM1; broader live validation remains deferred and TLS was not investigated

### Phase 6.5 — MCP protocol validation
- [x] Used the official MCP Python SDK stdio client/session against a spawned server process; initialization negotiated protocol version `2025-11-25`
- [x] Called `tools/list`; exactly 10 tools returned with names, descriptions, and input schemas; READ_ONLY and NEEDS_APPROVAL behavior is documented in the tool descriptions
- [x] Called a representative read-only tool with mocked PowerShell dispatch; no SQL connection was opened
- [x] Confirmed invalid database, system database, invalid instance, invalid backup type, and backup without elicitation approval are rejected over MCP
- [x] Confirmed there is no arbitrary SQL or shell execution interface; approval-unavailable and declined-approval fail-closed behavior is covered by the MCP tests
- [x] MCP Inspector was not present in the existing environment; no software was installed because SDK protocol validation was available
- [x] Re-ran Python MCP tests: 19 passed, 0 failed
- [x] Re-ran the full Pester tree: 40 total, 38 passed, 0 failed, 2 read-only SQL integration tests skipped by default
- [ ] Remaining limitation: live SQL validation is deferred because of the documented Development/Lab TLS issue

### Phase 7 — MCP host integration
- [x] Codex MCP runtime initialized; stdio server connected successfully
- [x] `tools/list` exposed exactly 10 tools; descriptions and input schemas were verified
- [x] Verified READ_ONLY metadata and that `backup_ops_database` is marked destructive and `NEEDS_APPROVAL`; backup approval gates remain enforced
- [x] Confirmed an invalid database request is rejected through Codex
- [x] One read-only inventory request did run live against the Development/Lab SQL Server. No state-changing SQL, backup, or restore was performed
- [x] Python MCP tests: 19 passed, 0 failed
- [x] Pester tests: 40 total, 38 passed, 0 failed, 2 expected integration skips
- [x] Combined: 59 tests, 0 failed, 2 expected skips
- [ ] Remaining limitation: broader live SQL validation remains deferred because of the documented Development/Lab TLS connectivity issue

### Phase 8 — AI DBA Assistant behavior
- [x] Added `docs/AI_ASSISTANT_SPEC.md` defining supported requests, routing, validation, safe result/error handling, prohibited behavior, and examples; it explicitly forbids inventing SQL execution capability
- [x] Added `docs/TOOL_ROUTING.md` mapping natural-language examples to exactly the ten existing MCP tools; no MCP tools or automation functions were added or changed
- [x] Added offline Python specification tests for exact tool coverage and the documented safety/routing rules
- [x] Codex natural-language routing validation classified all eight requested scenarios using the configured MCP tool catalog; this was a routing-only run with no MCP tool invocation and no SQL/report/backup operation
- [x] Scenarios 1, 3, and 4 routed to inventory, backup status, and blocking sessions as expected; scenario 2 routed to server health and correctly retained its `READ_ONLY_SQL / WRITES_REPORT` behavior
- [x] Scenario 5 was rejected before dispatch because `DatabaseA` is not in the current Development `ApprovedDatabases`; for an approved target, backup remains `NEEDS_APPROVAL` and requires explicit MCP elicitation
- [x] Scenarios 6, 7, and 8 were rejected as unsupported arbitrary SQL, shell execution, and caller-supplied backup destination
- [x] Python MCP/specification tests: 23 passed, 0 failed
- [x] Pester 5.7.1 full tree: 40 total, 38 passed, 0 failed, 2 read-only SQL integration tests skipped by default
- [x] No live SQL operation or report write was performed during Phase 8 routing validation; broader live SQL validation remains deferred due to the Development/Lab TLS limitation

### Phase 9 — DBA Knowledge / RAG foundation
- [x] Inspected project safety rules, progress/readme, assistant behavior and routing specs, configuration, automation, MCP implementation, and Python tests before changes
- [x] Added `docs/KNOWLEDGE_BASE_SPEC.md` defining eligible content, metadata, future chunking/embedding/retrieval, citations, freshness, security, environment portability, and a provider-neutral Azure AI Search migration path
- [x] Added generic `docs/knowledge/sample-backup-recovery.md` and `docs/knowledge/sample-blocking.md`; neither contains credentials, customer details, internal URLs, or proprietary company content
- [x] Added eight offline specification/content tests in `Tests/Python/test_knowledge_spec.py`; no live SQL or external service is used
- [x] Complete Python suite: 31 passed, 0 failed
- [x] Updated `README.md` and this progress record after the Python suite passed
- [ ] Future work: approved source inventory and ownership, document ingestion/chunking implementation, embeddings, access-controlled retrieval, citations in assistant responses, freshness/re-index workflow, and Azure AI Search evaluation/implementation
- [x] Phase 9 completion boundary: documentation and sample files only; Phase 10A adds the separate local lexical prototype without embeddings, search index, Azure dependency, or new MCP server

### Phase 10A — Local RAG prototype
- [x] Added standard-library `rag` package with safe Markdown loading, deterministic heading/paragraph-aware chunking, and lexical retrieval
- [x] Restrict document loading to direct-child Markdown files in the configured knowledge directory; reject traversal, absolute/outside paths, and resolved symlink escapes
- [x] Retain source filename, title, relative source identifier, category, section, and chunk ordinal; retrieval returns source document, matching chunk, lexical score, and attribution without exposing absolute paths
- [x] Added `docs/LOCAL_RAG_SPEC.md` documenting prototype architecture, boundaries, limitations, and future embedding/vector and Azure AI Search directions
- [x] Added 12 offline local RAG tests covering load/metadata, deterministic chunks, attribution, two topic queries, top-k, empty/unknown queries, traversal, outside paths, and query path non-disclosure
- [x] Focused local RAG tests: 12 passed, 0 failed
- [x] Complete Python suite: 43 passed, 0 failed
- [x] Example retrieval: `transaction log backup` ranked `sample-backup-recovery.md` / “Backup types” (score 3.0672); `blocking sessions` ranked `sample-blocking.md` / “Blocking versus deadlocking” (score 1.6076)
- [x] Updated `README.md` and this progress record only after both test runs passed
- [x] No MCP/Codex integration, external service, Azure resource, embedding, vector database, live SQL operation, or software installation was added/performed

### Phase 11 — AI Assistant Integration
- [x] Added `assistant/orchestrator.py` as a controlled planning layer that reuses local RAG functions and selects only tools present in the existing discovered MCP catalog
- [x] Planner produces structured knowledge-only, read-only MCP, combined RAG+MCP, `NEEDS_APPROVAL`, report-write, or `REJECT` outcomes; it never dispatches MCP calls itself
- [x] Backup preflight delegates to the existing MCP canonical database validator when supplied; the MCP server remains responsible for final allowlist validation and elicitation approval
- [x] Added `docs/ASSISTANT_ORCHESTRATION_SPEC.md` defining intent classification, routing, combination, approval/rejection, evidence attribution, and response structure
- [x] Added 11 offline tests for the seven requested scenarios, approved-backup gating, report-write classification, response evidence separation, and missing catalog capability
- [x] Representative routing: transaction-log concept → RAG only; database inventory → inventory tool only; blocking explanation/status → blocking RAG + tool; backup problems/checklist → backup-status tool + backup RAG; unapproved DatabaseA backup → reject; arbitrary SQL and PowerShell → reject
- [x] Approved PracticeDB backup produces only a `NEEDS_APPROVAL` plan; no backup or MCP call is performed by orchestration
- [x] Complete Python suite: 54 passed, 0 failed
- [x] No PowerShell automation or MCP server/security implementation was modified; no live SQL operation was performed
- [ ] Limitation: plans are not dispatched and do not include live tool results; an MCP host must hand selected operations to the existing server, which performs validation and approval

### Phase 12 — Controlled Assistant Execution
- [x] Added `assistant/executor.py` to execute local RAG retrieval and permitted MCP read-only plans, plus `assistant/response.py` for structured, attributed, sanitized responses
- [x] MCP calls use the official Python SDK `ClientSession` `list_tools()` and `call_tool()` methods on a caller-provided initialized session; no subprocess, SQL driver, PowerShell, shell, or direct SQL path exists
- [x] Before read-only dispatch, require an allowlisted existing Ops tool, discovery in the session catalog, read-only/non-destructive annotations, and planned argument keys allowed by its discovered schema
- [x] Keep `NEEDS_APPROVAL`, `WRITES_REPORT`, `REJECT`, missing-session, and unapproved backup outcomes fail-closed without dispatch; existing MCP validation and elicitation remain authoritative
- [x] Preserve RAG source filename/title/section and MCP tool/source attribution; sanitize credential-like text and path/internal fields in returned MCP data
- [x] Added 11 execution tests covering all requested cases, using local RAG and fake MCP ClientSession behavior; no live MCP operation or SQL connection was made
- [x] Full Python suite: 65 passed, 0 failed
- [x] No PowerShell automation, MCP server/security implementation, SQL Server, or Windows configuration was modified
- [ ] Limitation: read-only MCP call behavior is verified with a fake implementing the official client-session interface and the actual existing catalog; no real stdio session was dispatched during this phase

### Phase 13 — LLM Integration (Development/Lab planning validation)
- [x] Selected Codex as the external Development/Lab LLM host; no Python dependency on Codex CLI/API and no additional model provider
- [x] Added `assistant/llm_contract.py` to normalize the existing Planner output into a provider-neutral structured plan; it does not execute plans
- [x] Added `docs/LLM_INTEGRATION_SPEC.md` defining the Codex host role, Planner/Executor/MCP/RAG boundaries, plan fields, approval, provider independence, and future enterprise-provider migration
- [x] Contract fields: sanitized request, intent, knowledge/tool required flags, existing tool name, classification, schema-limited parameters, approval_required, and sanitized rejection_reason
- [x] Allowed plan tools are exactly the ten existing MCP tools; existing MCP canonical validation remains the final authority
- [x] Added 16 planning-only tests for all ten requested scenarios and additional tool allowlist, secret redaction, system/unapproved target, and approval controls
- [x] Full Python suite: 81 passed, 0 failed
- [x] No Codex CLI/API call, Executor call, MCP operation, live SQL, backup, restore, Azure resource, or package installation occurred
- [ ] Limitation: validates deterministic Planner-to-contract behavior, not a live Codex transport or LLM semantic accuracy; no plan was executed

### Phase 14A — LLM Plan Intake and Validation
- [x] Added `assistant/intake.py` as the provider-independent application boundary for proposed Phase 13 structured plans
- [x] Intake enforces the exact contract shape and JSON-safe values, then compares the proposal against a fresh canonical plan from the existing Planner and serializer
- [x] Intake returns the Planner-owned `AssistantPlan`; it does not dispatch MCP calls or bypass the existing MCP validation boundary
- [x] Unknown tools, changed classifications/parameters/approval flags, malformed plans, arbitrary SQL/shell, and caller-supplied backup paths fail validation
- [x] REJECT plans remain rejected and NEEDS_APPROVAL plans remain non-executable through the existing Executor
- [x] Added 15 offline tests covering valid inventory, RAG-only, combined blocking, report-write, approval-required backup, malformed/forged inputs, rejection, and no-dispatch checks
- [x] Focused tests: 15 passed, 0 failed; full Python suite: 96 passed, 0 failed
- [x] No external LLM API, MCP tool call, SQL operation, backup, restore, PowerShell, MCP server, or PowerShell automation change
- [ ] Limitation: intake validates structural and canonical plan consistency; it does not evaluate LLM semantic accuracy or connect a live LLM transport

### Phase 14B — Validated Plan to Executor
- [x] Added `assistant/application.py` as the handoff from a structured LLM proposal through Phase 14A validation to the existing Executor
- [x] The handoff supplies only the canonical planner-owned `AssistantPlan`; the existing Executor still checks tool allowlist, MCP discovery, read-only annotations, and input schema
- [x] Inventory and blocking plans invoke only their selected existing read-only MCP tool through the supplied fake official-session interface
- [x] RAG-only requests retrieve local knowledge without MCP; combined requests preserve separate reference and MCP result attribution
- [x] Health-report requests remain approval-gated by the Executor; REJECT and `NEEDS_APPROVAL` backup plans make no MCP calls
- [x] Added 8 focused fake-session tests; focused suite: 8 passed, 0 failed; full Python suite: 104 passed, 0 failed
- [x] No live SQL/MCP operation, backup, restore, external LLM, Codex CLI/API, PowerShell automation, or MCP server change
- [ ] Limitation: end-to-end validation uses fake MCP sessions; live SQL remains deferred under the existing lab TLS limitation

### Phase 14C — Real MCP Execution Boundary Validation
- [x] Added `Tests/Python/test_phase14c_protocol.py`; real stdio protocol smoke test initializes the official MCP `ClientSession` and verifies exactly the ten approved tools without calling a tool
- [x] Focused stdio protocol tests: 1 passed, 0 failed; full Python suite: 105 passed, 0 failed
- [x] Real official-SDK stdio session initialized successfully (protocol version `2025-11-25`); `tools/list` succeeded with exactly ten approved tools
- [x] One `get_ops_database_inventory` request went through Application → Intake → Executor → official `ClientSession` → real MCP server
- [x] MCP tool dispatch reached the existing server; the fixed PowerShell bridge ran and the `Get-OpsDatabaseInventory` function recorded an error while connecting
- [x] SQL diagnostic execution failed before the query due to the existing Development/Lab encryption/TLS compatibility limitation; the MCP client received only the server's sanitized generic failure
- [x] No retry was made. No backup, restore, state-changing SQL, SQL/Windows configuration change, TLS troubleshooting, or software installation occurred
- [ ] Limitation: successful SQL execution through the real boundary remains deferred until the separately documented Development/Lab TLS connectivity issue is resolved

### Phase 14D — Controlled DBA Response Generation
- [x] Added `assistant/response_generation.py` as a deterministic presentation-only layer over existing `AssistantResponse` results; it has no tool, MCP session, SQL, or command execution interface
- [x] RAG-only answers include source-attributed passages and explicitly state they are not live SQL evidence
- [x] MCP-only and combined answers show sanitized structured values returned by the tool; combined output separates reference context from live MCP data and preserves both attributions
- [x] REJECT, report-write approval, backup `NEEDS_APPROVAL`, no-match, and MCP failure results receive distinct factual wording; failures are never presented as successful/no-issue results
- [x] Sanitizes sensitive fields, secrets, filesystem paths, and raw error text from presentation output
- [x] Added 11 deterministic response tests for RAG, inventory, combined blocking, health report, rejection, approval, MCP failure, factual output, attribution, sanitization, and presentation-only behavior
- [x] Focused Phase 14D tests: 11 passed, 0 failed; full Python suite: 116 passed, 0 failed
- [x] No real SQL/MCP operation, backup, restore, live LLM, Codex CLI/API, or Azure service was used
- [ ] Limitation: response generation is deterministic formatting of supplied result structures; it does not generate LLM paraphrases or independently validate source data

### Phase 15A — Approval Contract and Fail-Closed Safety Boundary
- [x] Added `assistant/approval.py` with canonical `ApprovalRequest`, opaque app-issued `ApprovalGrant`, `ExecutionEligibility`, and `ApprovalAuthority`
- [x] Approval requests are created only for the existing `backup_ops_database` / `NEEDS_APPROVAL` plan and include operation ID, tool, approved SQL instance, approved database, backup type, classification, canonical parameters, and fixed configured-destination policy
- [x] Operation ID is a SHA-256 fingerprint of the canonical operation; an in-memory one-use nonce is issued only after literal explicit human approval, and authorization revalidates the current plan/configuration before returning eligibility
- [x] Missing, declined, forged, mismatched, changed, disallowed, replayed, and rejected operations fail closed; read-only plans do not require approval
- [x] No expiry field was introduced; grants are process-local and one-use, and consumed or process-lost grants cannot be reused
- [x] Added 19 fake-data tests covering required approval cases, changed parameters/instance/database/type/policy, LLM bypass attempts, allowlist violations, and no execution without approval
- [x] Focused tests: 19 passed, 0 failed; full Python suite: 135 passed, 0 failed
- [x] No Executor/MCP/PowerShell behavior was changed; no real MCP/SQL operation, backup, restore, or state-changing operation occurred
- [ ] Limitation: trusted host/UI must supply the explicit human approval signal in a future phase; this contract returns eligibility only and does not execute operations or replace MCP elicitation

### Phase 15B — Approval-Aware Application Execution
- [x] Connected `execute_llm_plan` to `ApprovalAuthority`; `NEEDS_APPROVAL` plans require a matching application-issued grant before passing eligibility to the Executor
- [x] The Executor consumes the opaque one-use eligibility proof and rechecks the backup tool, classification, database, backup type, SQL instance, configured destination policy, canonical parameters, and operation fingerprint before dispatch
- [x] Only the existing `backup_ops_database` MCP tool can be dispatched from an approved state-change plan; configured SQL instance is explicitly added to the approved MCP arguments
- [x] `READ_ONLY` plans still use the existing executor checks without approval; `REJECT` and missing/invalid/replayed approvals fail closed without MCP calls
- [x] Added 16 fake-session tests for read-only, reject, missing/valid/forged/replayed approval, exact parameters, operation changes, allowlist failures, and caller-path rejection
- [x] Focused Phase 15B tests: 16 passed, 0 failed; Phase 15A + 15B + Phase 14B focused tests: 43 passed, 0 failed; full Python suite: 151 passed, 0 failed
- [x] No actual MCP backup tool call, real SQL operation, backup, restore, or PowerShell automation change occurred
- [x] Existing MCP server backup elicitation and approval gate remain unchanged and required for any real dispatch
- [ ] Limitation: Phase 15B's successful backup dispatch is validated only against fake MCP sessions; the real MCP elicitation and backup execution path remains intentionally untested

### Phase 15C — Safety Hardening and Audit Review
- [x] Reviewed all ten MCP tools: eight diagnostic tools are `READ_ONLY`; `get_ops_server_health_report` is `READ_ONLY_SQL / WRITES_REPORT` and is host-gated; `backup_ops_database` is destructive `NEEDS_APPROVAL`. No state-changing tool is classified read-only.
- [x] Traced application and direct Executor entry points. Missing, forged, mismatched, altered, unknown, rejected, and replayed approval paths fail closed; changed eligibility is consumed and rejected before MCP dispatch. Regression tests cover direct Executor invocation and alternate/modified plan cases.
- [x] Exact approval binding remains unchanged across tool, approved instance/database, backup type, canonical parameters, configured destination policy, and operation fingerprint. Grants remain process-local and one-use; no expiry concept exists, and consumed/lost grants cannot be replayed.
- [x] Added `assistant/audit.py` with a fixed metadata-only `AuditEvent` contract: UTC timestamp, request/operation IDs, approved tool, target metadata, classification, approval outcome, execution attempt/result, and bounded failure category. It intentionally does not persist events or include raw request text, parameters, or exceptions.
- [x] Hardened private `Write-OpsLog` error recording to replace raw exception details and logging failures with fixed sanitized messages; added a Pester regression test. No public PowerShell Ops function, MCP behavior, or SQL behavior changed.
- [x] Added 11 Phase 15C Python tests for tool classifications, direct/forged/replayed/modified approval paths, malformed/rejected plans, MCP and response failures, sanitization, and the audit contract.
- [x] Focused Phase 15C Python tests: 11 passed, 0 failed. Full Python suite: 162 passed, 0 failed. PowerShell helper syntax validation passed.
- [ ] Pester execution was blocked before test bodies: Pester 5.7.1 could not create its temporary `HKCU\Software\Pester` registry key because this environment denies registry access (25 discovered tests; 0 entered/passed). No registry or Windows settings were changed.
- [x] No real MCP dispatch, SQL operation, backup, restore, or other state-changing operation occurred.
- [ ] Limitations: application audit events are not yet connected to a persistent sink; approval remains host-signal-driven and in-memory. Live SQL validation remains deferred due to the known Development/Lab TLS issue.

### Phase 16A — Deterministic DBA Monitoring Foundation
- [x] Added the separate `monitoring` package with a normalized `MonitoringCheck` schema: check/tool name, UTC timestamp, externally supplied SQL instance, collection status, structured data, and a sanitized optional error category/message.
- [x] Added fixed collection mappings for the existing eight read-only MCP tools only: inventory, database health, database size, backup status, disk space, failed jobs, blocking sessions, and long-running queries. Report generation and backup are excluded.
- [x] The service uses an initialized official MCP `ClientSession`, verifies discovered read-only annotations and schema before calling tools, and relies on the existing MCP/PowerShell layers for target allowlist validation.
- [x] Collection status is limited to `SUCCESS`, `EMPTY`, or `FAILED`; the monitoring layer does not assign DBA severity or infer health from natural-language output. Malformed results and connectivity/tool errors remain explicit failures.
- [x] Added 10 fake-session tests for all categories, successful/empty/failed/malformed responses, SQL connectivity classification, metadata/schema, read-only annotation enforcement, no state-changing tools, and external instance input.
- [x] Focused Phase 16A tests: 10 passed, 0 failed. Full Python suite: 172 passed, 0 failed.
- [x] No live MCP tool dispatch, SQL connection, backup, restore, persistence, scheduling, alerting, or environment configuration change occurred.
- [ ] Limitations: results are in-memory only and collection is not scheduled. The current MCP server intentionally sanitizes provider exceptions, so some real SQL connectivity failures may arrive only as a generic MCP execution failure; they remain `FAILED` and are never treated as healthy. Exact live SQL/TLS validation remains deferred.

### Phase 16B — Deterministic Health Evaluation
- [x] Added `monitoring/evaluation.py` with immutable policy, per-check evaluation, and overall evaluation models. It preserves each normalized Phase 16A result and adds severity, machine-readable reason code, explanation, timestamp, target instance, and measured values.
- [x] Added external JSON policy schema and portable example under `Config/monitoring.evaluation.schema.json` and `Config/monitoring.evaluation.example.json`. Required state maps, backup/disk thresholds, failed-job/blocking count limits, and long-query multiplier are validated; no instance, database, path, or credential is embedded in evaluator logic.
- [x] Implemented deterministic rules for database state/accessibility, database health, backup age, disk free percentage, failed job count, blocking session count, and long-running query duration. Database size is `INFO` because no capacity target is configured.
- [x] Failed collection and unmapped/missing measurements evaluate as `UNKNOWN`; empty failed-job, blocking, and long-query result sets are `HEALTHY`, while empty inventory/health/size/backup/disk sets are `UNKNOWN`.
- [x] Overall precedence is `CRITICAL > WARNING > INFO > UNKNOWN > HEALTHY`; unknown therefore remains visible when mixed only with healthy checks, and all-unknown stays unknown.
- [x] Added 16 fake-result tests for all categories, configured thresholds, empty/failed/malformed data, overall precedence, invalid/missing config, and absence of environment-specific constants.
- [x] Focused Phase 16B tests: 16 passed, 0 failed. Full Python suite: 188 passed, 0 failed.
- [x] No live MCP call, SQL operation, backup, restore, persistence, schedule, notification, LLM evaluation, or environment configuration change occurred.
- [ ] Limitations: policy thresholds in the example require operational review and environment-specific configuration before deployment. Size remains informational. Real collection/severity correlation was not tested against SQL because live Lab connectivity validation remains deferred.

### Phase 16C — DBA Health Reporting
- [x] Added `monitoring/reporting.py` with deterministic report and per-check models consuming Phase 16A collection results and matching Phase 16B evaluations.
- [x] Preserved collection observations, policy severity/reason/measured values, and sanitized collection errors as distinct fields.
- [x] Added in-memory human-readable text, escaped HTML, and machine-readable JSON serialization; reports are not persisted.
- [x] Reports summarize HEALTHY, INFO, WARNING, CRITICAL, and UNKNOWN checks, collection failures, and unknown results without fabricating missing values.
- [x] Focused Python tests: 12 passed, 0 failed. Full Python suite: 200 passed, 0 failed.
- [x] No live SQL/MCP or state-changing operation was performed; live validation remains deferred due to the documented Development/Lab TLS limitation.

### Phase 16D — Scheduled Monitoring Workflow
- [x] Added `monitoring/workflow.py` to load strict external workflow/evaluation configuration and orchestrate existing collection, evaluation, reporting, and artifact persistence without merging those modules.
- [x] Added `Config/monitoring.workflow.schema.json` and a portable non-secret example. SQL instance selection must match the configured allowlist; enabled checks must be existing fixed diagnostics; policy path is config-relative; report directory and text/HTML/JSON formats are external configuration.
- [x] Added validated check selection to `MonitoringService.collect`; disabled checks are not dispatched, and only its existing fixed read-only tool catalog is selectable.
- [x] Persists only configured text, HTML, and JSON artifacts. Names include UTC timestamp and workflow ID; exclusive file creation prevents overwriting an existing artifact.
- [x] Returns structured counts, status, artifact locations, sanitized failure information, and metadata-only `AuditEvent` records. Collection check failures are included in report generation; configuration/collection infrastructure/evaluation/report/persistence failures return `FAILED`.
- [x] Added manual entry point: `python -m monitoring.run_workflow --config <workflow-config.json>`; it launches only the existing MCP server with the current Python interpreter. Exit codes are SUCCESS=0, WARNING=1, CRITICAL=2, UNKNOWN=3, FAILED=4. No scheduled task was created.
- [x] Focused Phase 16D tests: 17 passed, 0 failed. Monitoring + reporting + workflow focused tests: 39 passed, 0 failed. Full Python suite: 217 passed, 0 failed.
- [x] No live SQL/MCP operation, backup, restore, or state-changing SQL was performed; live SQL validation remains deferred due to the documented Development/Lab TLS limitation.

### Phase 17A — Production Readiness Assessment
- [x] Reviewed repository safety rules, progress/readme, assistant, monitoring, RAG, MCP server/bridge, PowerShell automation, environment/evaluation/workflow configuration, tests, and current design documentation.
- [x] Created `docs/PRODUCTION_READINESS_ASSESSMENT.md` covering architecture, configuration, identity/secrets, SQL permissions, MCP, PowerShell, monitoring, reporting, audit, LLM, RAG, testing, deployment, operations, concrete risks, stage-specific blockers, and dependency-based work order.
- [x] Assessment uses COMPLETE / REQUIRED / RECOMMENDED / BLOCKED / ENVIRONMENT-SPECIFIC statuses; it contains no readiness score and does not call the system production-ready.
- [x] Existing Python suite after assessment: 217 passed, 0 failed.
- [x] No application code, SQL, permissions, credentials, infrastructure, scheduler, or environment settings were changed; no production connection, backup, restore, or state-changing operation occurred.

### Phase 17B — Environment and Configuration Hardening
- [x] Removed implicit Development configuration selection from both `Get-OpsConfig` and the MCP target loader. Both require an explicit `DBA_AUTOMATION_CONFIG`; `DBA_AUTOMATION_ENV`, when supplied, must match the file.
- [x] Extended the common PowerShell configuration schema and environment examples with `EnvironmentId` and `BackupPolicy`. Backup policy is enforced in MCP preflight and `Backup-OpsDatabase`; only fixed types are accepted and approval is required by schema/runtime.
- [x] Added deterministic Python automation-config validation and environment metadata/policy validation for the monitoring workflow. Production rejects known Lab/example placeholders, certificate trust, and relative artifact paths. The checked-in Production file is intentionally a schema-valid template and fails deployability validation.
- [x] Made Production RAG loading require explicit `DBA_KNOWLEDGE_DIRECTORY`; the project Codex Development config explicitly selects its Development automation and knowledge config.
- [x] Added 12 focused offline environment-hardening tests covering environment selection, Development/Test/UAT/Production shape, placeholders, paths, secrets, monitoring policy/checks/artifacts, backup policy, and RAG directory selection.
- [x] Focused Phase 17B tests: 12 passed, 0 failed. Related config/workflow/MCP/RAG tests: 62 passed, 0 failed. Full Python suite: 231 passed, 0 failed.
- [x] All nine Config JSON files parsed; `Test-Json` accepted the four automation config examples and monitoring workflow example against their schemas; modified PowerShell files parsed successfully.
- [x] Final validation: full Python suite 231 passed, 0 failed; Pester 5.7.1 discovered 25 tests and passed 25, with 0 failed, 0 skipped, and 0 inconclusive.
- [x] No Production connection, SQL operation, backup/restore, permission change, credential operation, Azure resource, scheduled task, Windows/system configuration change, or TLS troubleshooting occurred. Live Lab SQL validation remains deferred because of the documented TLS limitation.

### Phase 18 — Comprehensive Documentation Audit and Standardization
- [x] Conducted full repository audit across Python sources, PowerShell automation module, MCP server, local lexical RAG, planning/intake/approval layers, monitoring workflows, schemas, and test suites.
- [x] Rewrote root `README.md` into an enterprise-grade technical overview featuring Mermaid architecture diagrams, detailed capability inventories, strict safety rules, testing breakdowns, workflows, and a 4-priority future roadmap.
- [x] Clarified operational boundaries: explicitly distinguished implemented and unit-tested components from prototypes (RAG) and deferred live SQL operations (TLS pre-login limitation).
- [x] Verified cross-platform standard-library Python tests (35 passed, 0 failed across RAG, knowledge spec, response generation, and assistant spec).
- [x] Audited markdown links across the repository and eliminated broken references.
- [x] Reaffirmed that no credentials, passwords, tokens, connection strings, or internal paths are present in source code, configuration examples, or documentation.
