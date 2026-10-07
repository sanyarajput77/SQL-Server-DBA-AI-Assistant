# AI DBA Assistant

A PowerShell module for read-only SQL Server diagnostics and an explicitly approved database backup operation. The project targets a Development/Lab environment and keeps instance names, paths, thresholds, and trust settings in external JSON configuration.

## Requirements

- Windows with PowerShell 7.6 or later
- dbatools 2.8.4 or later
- SqlServer PowerShell module
- Pester 5.7.1 or later for the test suite
- Python 3.14 or later, a project `.venv`, and the MCP Python SDK
- A SQL Server instance and an appropriately limited account for live, read-only integration checks

The project does not install software or create SQL logins or credentials. Supply database authentication through the existing Windows/SQL Server environment; do not put secrets in configuration files or source control.

## Setup

Run the repeatable prerequisite and configuration check from the project root:

```powershell
.\setup.ps1
```

It checks PowerShell, dbatools, SqlServer, Pester, Python, `.venv`, MCP SDK availability, and each supplied environment JSON file. It creates only missing project directories and runs the unit/configuration tests; it does not install software or contact SQL Server. Use `-WhatIf` to validate without creating directories, or `-SkipTests` to omit Pester. Review `Config/config.development.json` and confirm that `ApprovedInstances` contains only the intended lab instances. Backup, report, and log paths are configured there.

Import the module when ready to use it:

```powershell
$env:DBA_AUTOMATION_ENV = 'Development'
$env:DBA_AUTOMATION_CONFIG = 'Config/config.development.json'
$env:DBA_KNOWLEDGE_DIRECTORY = 'docs/knowledge'
Import-Module .\DbaAutomation\DbaAutomation.psd1
Get-OpsDatabaseInventory
```

Configuration selection is explicit: `Get-OpsConfig` and the MCP server fail closed when `DBA_AUTOMATION_CONFIG` is absent or invalid. `DBA_AUTOMATION_ENV`, when set, must match the selected JSON's `Environment`. Relative config paths resolve from the project root for MCP and from the current PowerShell working directory for direct PowerShell use; use an absolute file path when the process working directory is not controlled. Test/UAT/Production JSON files are examples and must be reviewed and customized before use. The Production example is a schema-valid template that runtime validation intentionally rejects until all placeholder values and paths are replaced.

## Architecture

- `DbaAutomation/Public/` contains the ten exported capabilities: database inventory, health and size; backup status; disk space; failed jobs; blocking sessions; long-running queries; server health report; and `Backup-OpsDatabase`.
- `monitoring/` normalizes results from the existing eight read-only MCP diagnostics without changing the MCP server or Executor.
- `DbaAutomation/Private/` contains configuration loading, instance and database safety checks, session initialization, and sanitized structured logging.
- `Config/` contains non-secret environment-specific settings.
- `Tests/Unit/` contains mocked Pester tests. `Tests/Integration/` contains opt-in, read-only checks against the configured SQL Server.
- `Logs/`, `Reports/`, and `Backups/` are configured output locations.

SQL-facing calls use predefined dbatools operations or a fixed parameterized DMV query. Callers cannot supply SQL text or shell commands. `Backup-OpsDatabase` restricts backup type and destination, rejects system databases, supports `-WhatIf`/`-Confirm`, and requires normal PowerShell confirmation for its high-impact action. It does not restore databases.

## Least privilege and identity

[`docs/sql/create-ai-dba-login.sql`](docs/sql/create-ai-dba-login.sql) is an inert, fully commented reference. It is not provisioning automation and must not be executed. It documents environment identities and the narrow permission scopes identified for the current implementation. No SQL login, user, or permission has been created or changed.

Use separate dedicated identities for Development/Lab, Test, UAT, and Production. Production should use a dedicated Active Directory service identity, preferably a gMSA where supported, with Windows integrated authentication. If SQL authentication is unavoidable outside Production, keep its credential in an approved secret manager and inject it only into the process at runtime. Credentials, tokens, and connection strings do not belong in project files, JSON configuration, logs, prompts, or MCP tool arguments: source control, backups, reports, and transcripts broaden access and retention.

The MCP server runs over local stdio under the environment's service identity. The SQL client uses that identity through the existing integrated-authentication path. The identity is never returned to a tool caller or logged. The Python layer exposes only the ten predefined Ops capabilities through a static PowerShell dispatcher; it accepts neither SQL text, shell commands, nor executable paths. The MCP layer canonicalizes instances and database names against configuration before dispatch, rejects system databases, and exposes no backup destination or report output path. Tool responses suppress SQL text, credentials, raw provider errors, and internal paths. The report tool writes only to the configured Reports directory and is explicitly annotated as a report write. Backup is marked `NEEDS_APPROVAL` and requires the SDK's user elicitation gate before the bridge will invoke `Backup-OpsDatabase`; declined or unavailable elicitation fails closed.

The MCP SDK is already present in `.venv` and pinned by `mcp-server/requirements.txt`. Start the local server with:

```powershell
.\.venv\Scripts\python.exe .\mcp-server\server.py
```

The server uses stdio, so configure it in an MCP host as a local process with that command and the project directory as its working directory. It requires an explicit `DBA_AUTOMATION_CONFIG`; the project Codex config explicitly selects the Development JSON and environment. It inherits the host's process identity. It does not accept credentials as tool inputs. Run the MCP unit tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s Tests/Python -v
```

Latest full Python suite: 231 passed, 0 failed. Tests cover the ten-tool MCP surface, dispatch safety, assistant routing, knowledge-base requirements, local lexical RAG, controlled orchestration plans, execution using fake and real-stdio MCP sessions, provider-neutral LLM planning/intake/execution, approval binding, monitoring normalization, deterministic configured health evaluation/reporting, the configured monitoring workflow, and environment configuration validation. The latest recorded full Pester run discovered 40 tests: 38 passed, 0 failed, and 2 read-only integration tests were skipped by default (the Pester suite was not rerun for Phase 17B).

## Codex host integration

The project-scoped [Codex configuration](.codex/config.toml) registers `dba_automation` over stdio using the existing `.venv` Python executable and `mcp-server/server.py`. Codex CLI and the Codex VS Code extension share this MCP configuration. Codex loads project `.codex/` settings only for trusted projects; trust this repository in Codex before expecting the tools to appear. The configuration explicitly allowlists the ten existing tool names, prompts for write-classified tools, and sets `backup_ops_database` to `prompt`; the server's separate MCP elicitation approval gate remains required for backup execution.

Host validation on Codex VS Code extension `0.155.0-alpha.16.3` succeeded: the runtime initialized, the stdio server connected, and `tools/list` exposed exactly ten tools with descriptions and input schemas. READ_ONLY metadata was verified; `backup_ops_database` is marked destructive and `NEEDS_APPROVAL`. An invalid database request was rejected through Codex. One valid `get_ops_database_inventory` call for configured `PracticeDB` ran live as a read-only lab query because the temporary PowerShell mock did not load. No state-changing SQL, backup, or restore was performed. Broader live SQL validation remains deferred due to the documented Development/Lab TLS connectivity issue.

All database-targeting Ops functions validate targets against `Config.ApprovedDatabases` and reject system databases before connecting. Blocking and long-running query results are filtered to approved databases, although their DMV permission is server-wide. `Get-OpsFailedJobs` uses a fixed query over only `msdb.dbo.sysjobs` and `msdb.dbo.sysjobhistory`; the documented custom read-only role avoids SQLAgentReaderRole's job-management capabilities. `Get-OpsDiskSpace` uses SQL-visible `sys.dm_os_volume_stats` for volumes containing approved database files, so it does not need Windows Administrator/WMI access. It cannot confirm free space for a backup destination that is on a separate volume. `Backup-OpsDatabase` enables backup checksums and validates dbatools' completion result without `RESTORE VERIFYONLY`; this is not independent restore validation. A separate privileged backup-validation process is required where stronger verification is mandated.

Permission scope remains version- and deployment-dependent. Database catalog/file/health calls need connection and metadata visibility only in approved user databases; confirm exact catalog access with the deployed dbatools and SQL Server versions. `Get-OpsBackupStatus` reads backup history through dbatools and its exact `msdb` SELECT requirements must be verified for that version. Long-query, blocking, and volume diagnostics need SQL Server's version-appropriate server-state permission and therefore expose server-wide DMV data despite filtering results to approved databases. The SQL login reference calls out these broader visibility boundaries; it does not claim database-level least privilege can scope server-state permissions. Backup requires `BACKUP DATABASE` in approved databases and `BACKUP LOG` only if log backups are enabled; SQL Server service-account filesystem access to the backup destination is separate. No `CREATE DATABASE`, `db_backupoperator`, `db_owner`, or `sysadmin` grant is recommended.

Permission references: [SQL Server DMV permissions](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/system-dynamic-management-objects?view=sql-server-ver17), [sys.dm_os_volume_stats](https://learn.microsoft.com/nb-no/sql/relational-databases/system-dynamic-management-objects/sys-dm-os-volume-stats-transact-sql?view=sql-server-ver17), [BACKUP permissions and service-account file access](https://learn.microsoft.com/en-us/sql/relational-databases/backup-restore/create-a-full-database-backup-sql-server?view=sql-server-ver17), [SQL Server Agent fixed roles](https://learn.microsoft.com/en-us/ssms/agent/sql-server-agent-fixed-database-roles), and dbatools [Get-DbaDiskSpace](https://docs.dbatools.io/Get-DbaDiskSpace.html) / [Backup-DbaDatabase](https://dbatools.io/Backup-DbaDatabase/) references.

## Configuration

`Config/config.development.json` is the active lab example and currently allows `MSSQL-VM1`, `localhost`, and `.`. This server name exists only in Development configuration, not in reusable function code. Review this allowlist before connecting. Test, UAT, and Production examples are in `Config/config.test.json.example`, `Config/config.uat.json.example`, and `Config/config.production.json.example`.

`Config/config.schema.json` defines required properties, accepted environments, field types, threshold and timeout bounds, and rejects unsupported properties (including secret fields). `Get-OpsConfig` validates every selected file against this schema, verifies that `DefaultInstance` is allowlisted, verifies that protected system databases are included, and allows `TrustServerCertificate` only in Development/Lab. It fails closed on a missing/invalid configuration path. Relative backup, report, and log paths resolve from the configuration file location; absolute local or UNC paths are also supported. Change environment values in configuration rather than application code.

Phase 17B adds required `EnvironmentId` and `BackupPolicy` fields. Backup types remain limited to `Full`, `Differential`, and `Log`; the selected config can disable backups or narrow allowed types, and `ApprovalRequired` must remain true. MCP preflight and `Backup-OpsDatabase` both enforce the selected policy. Development is enabled for the existing fixed types; Test/UAT/Production examples disable backup until an environment owner explicitly reviews and configures the policy. Backup still requires the existing MCP approval gate.

Development, Test, UAT, Production, and the retained Lab compatibility value use the same PowerShell schema. Production validation rejects known lab/example placeholders, requires certificate trust disabled, and requires absolute backup/report/log paths. The Production JSON in this repository is only a schema-valid template; it is not a usable deployment configuration. Never place passwords, tokens, connection strings, or other secrets in these files. `DBA_KNOWLEDGE_DIRECTORY` selects the packaged/local knowledge directory; Production RAG loading requires it explicitly, while the Codex Development config sets it to `docs/knowledge`. Monitoring workflow configuration carries its own `Environment`/`EnvironmentId`, fixed enabled-check allowlist, evaluation policy path, output directory, and artifact-format list. Deployment owners must keep its target allowlist aligned with the selected automation config; neither config layer tests connectivity.

The Development MCP host configuration at `.codex/config.toml` supplies `DBA_AUTOMATION_CONFIG=Config/config.development.json`, `DBA_AUTOMATION_ENV=Development`, and `DBA_KNOWLEDGE_DIRECTORY=docs/knowledge`. Test/UAT/Production hosts must inject their matching config and knowledge directory outside application code. Production identity and secrets remain external deployment responsibilities; no secret-management product is selected here.

`TrustServerCertificate` is a Development/Lab-only setting. `Connect-OpsSqlInstance` passes it explicitly to dbatools `Connect-DbaInstance` for each allowlisted SQL connection, then the Ops functions pass that connected server object to their dbatools commands. It does not mutate shared dbatools configuration or persist the setting. Automatic certificate-trust fallback is explicitly disabled; Production connections pass trust disabled.

## Testing

Run the unit and integration test containers (integration tests are skipped unless explicitly enabled) without Pester’s optional Windows registry test drive:

```powershell
$pester = New-PesterConfiguration
$pester.Run.Path = @('Tests')
$pester.Run.PassThru = $true
$pester.TestRegistry.Enabled = $false
Invoke-Pester -Configuration $pester
```

Unit tests mock SQL-facing commands and do not connect to SQL Server. The integration tests are read-only and are skipped by default. To opt in after confirming the Development allowlist and access are appropriate, set `$env:DBA_AUTOMATION_RUN_INTEGRATION = '1'` and run Pester again. No automated test performs a backup or restore.

The Pester 5.7.1 unit/configuration suite is run from `Tests/Unit`; see `PROGRESS.md` for the latest result after changes. This includes checks for approved/unapproved databases, system database rejection, database-scoped diagnostic filtering, job query scope, and backup completion reporting without restore verification. The full live integration suite was previously run with integration enabled: both read-only checks failed during SQL pre-login TLS negotiation before any diagnostic query. This Development/Lab connectivity limitation remains unresolved; no backup or restore was performed and no SQL Server, Windows, registry, certificate, firewall, or TLS settings were changed.

Latest Phase 17B validation: 12 focused environment-hardening tests passed, 0 failed; the full Python suite passed 231 tests, 0 failed. Config JSON and PowerShell syntax/schema parsing were also checked. Pester was not run in this phase; its last recorded complete run remains 40 discovered, 38 passed, 0 failed, and 2 expected read-only integration skips. Broader live SQL validation remains deferred due to the documented Development/Lab TLS limitation.

## AI DBA Assistant behavior

[`docs/AI_ASSISTANT_SPEC.md`](docs/AI_ASSISTANT_SPEC.md) defines how natural-language requests map to the existing tool surface; [`docs/TOOL_ROUTING.md`](docs/TOOL_ROUTING.md) provides the concise routing table. The assistant may use only the ten existing MCP tools. It must reject arbitrary SQL, shell commands, unsupported inputs, system databases, unapproved databases, and instances outside the configured allowlist. It must never invent an SQL execution capability that MCP does not expose.

Diagnostic tools are read-only. `get_ops_server_health_report` reads SQL data and writes an HTML report to the configured Reports directory, so it is classified `READ_ONLY_SQL / WRITES_REPORT` and follows the host write prompt. Backup is `NEEDS_APPROVAL`, only for an approved database, with a fixed configured destination and explicit MCP elicitation approval. The current Development configuration does not approve the example name `DatabaseA`.

Phase 8 Codex routing validation classified all eight requested natural-language scenarios against the configured MCP tool catalog without invoking MCP tools. Inventory, health report, backup status, and blocking requests mapped to their expected tools; the report's file-write behavior was retained. The `DatabaseA` backup request was rejected before dispatch because that database is not approved; user-supplied SQL, PowerShell, and backup paths were also rejected. No SQL query, report write, backup, or restore was performed in this validation. Offline Python specification tests passed (23/23); Pester reported 40 total, 38 passed, 0 failed, and 2 expected skips.

## DBA knowledge / RAG foundation

[`docs/KNOWLEDGE_BASE_SPEC.md`](docs/KNOWLEDGE_BASE_SPEC.md) defines the future knowledge lifecycle and controls: approved sources and metadata, section-aware chunking, future embedding metadata, access-filtered retrieval, source citations, stale-content handling, protection of internal material, separation from live MCP results, and Development/Test/UAT/Production portability. Generic learning examples are in [`docs/knowledge/sample-backup-recovery.md`](docs/knowledge/sample-backup-recovery.md) and [`docs/knowledge/sample-blocking.md`](docs/knowledge/sample-blocking.md).

Phase 9 established the documentation and generic-content foundation. Phase 10A adds the standalone local lexical retriever described below. There is still no production ingestion pipeline, embedding generation, vector database, Azure AI Search dependency, or knowledge MCP tool. Azure AI Search remains a future option; preparation and metadata contracts are intended to remain provider-neutral. Phase 9's Python suite passed 31 tests with 0 failures, including offline checks for metadata, attribution, security rules, knowledge/live-result separation, future-only Azure status, sample presence, and credential-like content.

## Local RAG prototype

The standalone standard-library package in [`rag/`](rag/) loads direct-child Markdown files only from `docs/knowledge/`, extracts filename/title/category/content, and creates deterministic heading/paragraph-aware chunks with source metadata. Its lexical TF-IDF-like keyword ranking returns a source document, relevant chunk, score, and filename/section citation. Traversal, absolute/outside filenames, and symlink escapes are rejected; source attribution uses a relative filename rather than an absolute checkout path. A user query is text only and cannot select a path or execute a command.

This is lexical keyword retrieval, not semantic or vector search. It has no embeddings, persistence, access-filtered multi-user retrieval, or generated answer layer. The prototype does not connect to SQL Server. Focused tests passed 12/12 and the Phase 10A Python suite passed 43/43. Example top results: “transaction log backup” → `sample-backup-recovery.md`, “Backup types” (score 3.0672); “blocking sessions” → `sample-blocking.md`, “Blocking versus deadlocking” (score 1.6076). Azure AI Search remains a future option: first define and approve the source/access policy, then evaluate embedding and retrieval contracts, metadata mapping, security filters, freshness, citations, and deletion behavior before any Azure resources are created.

## Assistant orchestration

[`assistant/orchestrator.py`](assistant/orchestrator.py) creates structured plans using the local RAG package and the existing MCP host's discovered tool catalog. It classifies knowledge-only, live read-only, combined, state-changing, report-write, and unsupported requests. Knowledge passages are labeled as reference evidence; selected MCP actions are labeled as pending live evidence. The planner does not call MCP tools. The host must hand any approved read-only action to the existing MCP server, which remains responsible for input/allowlist validation and tool execution.

Backup requests produce a `NEEDS_APPROVAL` plan only after the existing MCP database validator accepts the target; the planner never starts a backup, and MCP elicitation plus server-side validation remain required. Unapproved targets, arbitrary SQL, and shell requests are rejected. The health-report tool retains its `READ_ONLY_SQL / WRITES_REPORT` classification and host write prompt. Phase 11's offline tests cover all seven requested scenarios plus approval and catalog-boundary cases; the complete Python suite passed 54 tests with 0 failures. No live SQL operation was performed.

## Controlled assistant execution

[`assistant/executor.py`](assistant/executor.py) consumes a plan and uses the local RAG loader/chunker/retriever for reference answers. For live read-only requests, it uses an initialized official Python MCP `ClientSession`, checks the discovered catalog, tool annotations, and argument schema, then calls only an explicitly allowlisted existing read-only Ops tool. It never launches PowerShell or a shell and has no SQL driver. [`assistant/response.py`](assistant/response.py) keeps reference citations distinct from MCP tool results and sanitizes returned secrets and internal paths.

`NEEDS_APPROVAL`, report-writing, rejected, and unapproved backup plans do not dispatch. Backup continues through the existing MCP elicitation and server validation only after human approval; the executor does not execute it automatically. Phase 12's 11 execution tests use a fake that implements the official client-session interface and the actual MCP catalog. The full Python suite passed 65 tests with 0 failures. A real stdio MCP session was not dispatched during this phase, so live host-to-executor execution remains to be validated; no SQL operation or backup was performed.

## LLM integration — Development/Lab

Codex is the external LLM host for this test version. The Python application does not call the Codex CLI or expose a provider API. [`docs/LLM_INTEGRATION_SPEC.md`](docs/LLM_INTEGRATION_SPEC.md) defines the structured plan contract and responsibilities for Codex, the existing Planner, local RAG, Executor, and MCP server. [`assistant/llm_contract.py`](assistant/llm_contract.py) maps existing Planner results to that provider-neutral plan shape, sanitizes request/rejection text, and limits tool names to the ten existing MCP capabilities. The MCP server remains the final authority for input and target validation.

Phase 13 validates planning only. Tests cover knowledge-only, live read-only, combined, backup approval, report-write, and rejection plans; no Codex CLI/API, Executor, MCP operation, SQL, or backup was invoked. Approved backups remain `NEEDS_APPROVAL`; unapproved/system databases, arbitrary SQL/shell, caller paths, and unknown tools are rejected. The complete Python suite passed 81 tests with 0 failures. A future approved enterprise LLM provider can replace Codex by emitting the same contract, subject to the same validation and execution boundaries.

Phase 14A adds [`assistant/intake.py`](assistant/intake.py), which accepts a provider-generated plan only when its fields match the Phase 13 contract and a fresh canonical result from the existing Planner. Phase 14B adds [`assistant/application.py`](assistant/application.py) to pass that validated planner-owned plan to the existing Executor. Executor checks and MCP server validation remain in force; the handoff adds no alternate execution path. Tests verify read-only inventory/blocking dispatch through fake MCP sessions, RAG-only behavior, combined source attribution, report-write gating, and no dispatch for rejected or approval-required backup plans. Phase 14A focused tests passed 15/15, Phase 14B focused tests passed 8/8, and the complete Python suite passed 104/104. No live SQL/MCP operation, backup, external LLM API, or Codex CLI/API was used.

Phase 14C validated the real stdio boundary using the official Python MCP SDK. Initialization succeeded with protocol version `2025-11-25`; `tools/list` returned exactly the ten approved tools. One inventory request traveled through the application, intake, executor, MCP client, real MCP server, and PowerShell bridge. The PowerShell Ops function ran but failed while connecting because of the documented Development/Lab encryption/TLS compatibility limitation; diagnostic SQL did not execute. The MCP response withheld internal error details. No retry, backup, restore, or state-changing operation occurred. The real-stdio protocol smoke test passed 1/1 and the full Python suite passed 105/105. Successful live SQL execution remains deferred due to the existing Lab TLS limitation.

Phase 14D adds [`assistant/response_generation.py`](assistant/response_generation.py), a deterministic presentation-only layer for Executor results. It presents source-attributed local reference passages separately from sanitized structured live MCP values, explicitly marks reference material as non-live, and distinguishes rejected, approval-required, and failed executions from successful results. It does not call tools or generate inferred DBA findings. Focused response tests passed 11/11 and the complete Python suite passed 116/116. All Phase 14D tests use deterministic fake results; no real SQL or backup operation was performed.

Phase 15A adds [`assistant/approval.py`](assistant/approval.py), an application-side approval contract for the existing backup capability. It creates an immutable request bound to the tool, configured/allowlisted SQL instance, approved database, backup type, `NEEDS_APPROVAL` classification, canonical parameters, and fixed configured-destination policy. Only a trusted host signal of explicit human approval can cause the in-memory authority to issue a one-use grant; the authority revalidates the exact current operation before returning execution eligibility. Missing, declined, forged, mismatched, modified, unapproved, rejected, and replayed requests fail closed. No expiry field is used; grants are ephemeral and process-local. This phase does not execute the operation, add an approval UI, or replace the existing MCP elicitation gate. Focused tests passed 19/19 and the full Python suite passed 135/135. No MCP or SQL operation, backup, or restore was performed.

Phase 15B connects that contract to [`assistant/application.py`](assistant/application.py) and [`assistant/executor.py`](assistant/executor.py). The application passes a backup plan onward only with matching authority-issued eligibility; the Executor consumes that proof once and revalidates the exact operation before calling only the existing backup MCP tool with the approved SQL instance, database, and backup type. Read-only operations remain approval-free; rejected, altered, forged, missing, or replayed approvals do not dispatch. The MCP server's separate elicitation gate remains intact and unchanged. Focused Phase 15B tests passed 16/16 and the full Python suite passed 151/151. All state-changing-path tests use fake MCP sessions; no actual MCP backup call, SQL operation, backup, or restore occurred.

## Phase 15C safety review

The review confirmed the ten-tool catalog contains eight `READ_ONLY` diagnostics, `get_ops_server_health_report` (`READ_ONLY_SQL / WRITES_REPORT`, host-gated), and `backup_ops_database` (destructive `NEEDS_APPROVAL`). The application and direct Executor paths reject missing, forged, altered, unknown, rejected, and replayed approval eligibility before MCP dispatch. The existing MCP elicitation gate remains unchanged.

[`assistant/audit.py`](assistant/audit.py) defines a bounded, metadata-only audit event contract with UTC time, request/operation IDs, tool and target identifiers, classification, approval outcome, execution attempt/result, and a limited failure category. It is a schema only: no persistence sink exists, and events exclude raw request text, parameters, and exception details. The private PowerShell `Write-OpsLog` helper now records a fixed sanitized error message instead of raw exception text; public Ops functions and SQL behavior were not changed.

Phase 15C focused tests passed 11/11 and the full Python suite passed 162/162. PowerShell helper syntax passed validation. Pester discovered its DbaAutomation test file but was unable to run because the environment denied creation of Pester's temporary `HKCU\Software\Pester` registry key; this did not change Windows settings. No real MCP dispatch, SQL operation, backup, restore, or other state change occurred. The audit schema is not wired to persistent logging, and live SQL validation remains deferred due to the existing Lab TLS limitation.

## Deterministic monitoring foundation

[`monitoring/`](monitoring/) provides a separate in-memory collector over the existing official MCP client session. It calls only the eight fixed read-only diagnostic tools after checking the discovered catalog annotations and instance input schema. It excludes the report-writing and backup tools, and it delegates actual instance/database allowlist enforcement to the existing server and PowerShell functions. [`monitoring/evaluation.py`](monitoring/evaluation.py) applies fixed deterministic rules to those normalized results without using an LLM.

Each collection result has `check_name`, `tool_name`, `timestamp_utc`, `sql_instance`, `status`, `data`, and optional `error` (`category` and sanitized `message`). The separate evaluation preserves that result and adds `severity`, `reason_code`, `reason`, timestamp/instance, measured values, and any sanitized error. Severity rules are supplied through the separate policy model and [example policy](Config/monitoring.evaluation.example.json), validated against its [schema](Config/monitoring.evaluation.schema.json); review and customize policy values for each environment before deployment.

Rules cover database state/accessibility and health, backup age, disk free space, failed-job count, blocking-session count, and long-running-query duration. Database size is `INFO` because the project has no configured size/capacity target. Missing/unmapped data and failed collection are `UNKNOWN`; empty failed-job, blocking, and long-query lists are `HEALTHY`, while empty inventory/health/size/backup/disk results remain `UNKNOWN`. Overall severity precedence is `CRITICAL > WARNING > INFO > UNKNOWN > HEALTHY`.

Phase 16A focused tests passed 10/10; Phase 16B focused tests passed 16/16; Phase 16C reporting tests passed 12/12; Phase 16D workflow tests passed 17/17. The full Python suite passed 217/217. No live SQL operation was performed. Monitoring results and evaluation objects remain in memory; Phase 16D persists the configured report artifacts but does not schedule runs. The MCP server may reduce provider failures to a generic error, which remains a failed collection; exact live SQL/TLS diagnostics remain deferred.

### Deterministic DBA health reports

[`monitoring/reporting.py`](monitoring/reporting.py) consumes matching monitoring checks and deterministic evaluations to build a report with a generated ID, UTC timestamp, configured instance, overall severity, severity counts, and per-check results. Collection observations, evaluation severity/reason/measured values, and sanitized collection errors remain separate so a failed or unknown check cannot appear healthy. Missing values are not synthesized.

Reports can be rendered as readable text, escaped HTML, or machine-readable JSON. Rendering is in-memory only: there is no web server, file persistence, scheduling, or notification integration. The existing PowerShell `Get-OpsServerHealthReport` behavior remains unchanged and continues to write its configured HTML report; this Python model is a separate consumer of the 16A/16B structures. Focused Phase 16C tests passed 12/12. Live SQL/MCP data has not been validated through this report layer because of the documented Development/Lab TLS limitation.

### Scheduled monitoring workflow foundation

[`monitoring/workflow.py`](monitoring/workflow.py) connects the existing collector, evaluator, and report builder as an independent orchestration layer. It loads [workflow configuration](Config/monitoring.workflow.example.json) validated against [its schema](Config/monitoring.workflow.schema.json), including the selected SQL instance and allowlist, evaluation policy path, report output directory, enabled fixed checks, and requested artifact formats. Paths are resolved relative to the workflow configuration file; no credentials or executable paths are accepted. The collector now accepts an explicit subset of its fixed eight read-only checks, so disabled checks are not called.

Manual invocation (after copying and editing the example for the target environment):

```powershell
.\.venv\Scripts\python.exe -m monitoring.run_workflow --config .\Config\monitoring.workflow.json
```

The entry point starts only the existing `mcp-server/server.py` using the current Python interpreter and official stdio MCP client. It returns structured counts, severity, collection-failure count, artifact locations, sanitized failure information, and metadata-only audit events. Infrastructure/configuration/evaluation/report/persistence failures produce `FAILED`. Otherwise `CRITICAL`, `WARNING`, and `UNKNOWN` retain their matching statuses. HEALTHY/INFO produce `SUCCESS` unless any collection failed, in which case status is `UNKNOWN`; failed checks remain visible in the report and are counted. A check failure does not stop other checks or report creation. Artifact filenames include UTC timestamp and workflow ID and are opened exclusively, so collisions fail without overwriting. Exit codes match the statuses: 0, 1, 2, 3, and 4 respectively.

Text, HTML, and JSON artifacts are written under the configured output directory. Audit events are returned with the workflow result but are not persisted; there is no scheduling, Windows Task Scheduler task, notification integration, or live SQL validation. Phase 16D focused tests passed 17/17 and the complete Python suite passed 217/217. Live SQL validation remains deferred due to the documented Development/Lab TLS limitation.

## Current limitations

- [Production readiness gap assessment](docs/PRODUCTION_READINESS_ASSESSMENT.md): Phase 17A documents Development/Lab, Test/UAT, and Production gaps. This repository has not been approved or validated for Production use.
- The earlier opt-in live Pester integration checks failed during TLS negotiation before diagnostic SQL. One `Get-OpsDatabaseInventory` call through the configured Codex MCP host later succeeded against the lab. Broader live SQL and effective-grant validation remains deferred; the prior TLS failures were not investigated or changed.
- Phase 5 implementation and permission documentation are functionally complete for the current Development/Lab. Live validation of effective grants and dbatools metadata access remains deferred: earlier TLS pre-login failures were not investigated, even though one inventory request later succeeded through Codex MCP. Repeat broader read-only permission validation when planned; do not change TLS settings as part of this work.
- Antigravity-specific MCP integration documentation remains unfinished; Phase 7 here covers the Codex VS Code host.
- `Get-OpsServerHealthReport` writes an HTML report to the configured report path.
- Phase 8 natural-language validation was routing-only. It did not execute the four read-only tool scenarios against SQL Server; live validation remains subject to the documented lab TLS limitation.
