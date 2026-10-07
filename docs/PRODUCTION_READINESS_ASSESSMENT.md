# Production Readiness Gap Assessment

**Scope:** current repository state for a Development/Lab → Test/UAT → Production transition. This is an assessment, not a deployment approval. It does not assign a score or percentage. The system is **not production-ready**.

## Status meanings

- **COMPLETE** — an implementation or document exists in the repository; this does not imply production validation.
- **REQUIRED** — work needed before the stated environment or capability is accepted.
- **RECOMMENDED** — a control or practice to adopt as part of the production design.
- **BLOCKED** — validation or deployment cannot be accepted until a dependency or environment issue is resolved.
- **ENVIRONMENT-SPECIFIC** — must be supplied, approved, and validated by the owning environment.

## 1. Architecture

| Component | Current repository evidence | Status and production gap |
|---|---|---|
| Application | `assistant/application.py`, intake, planner, executor, and response modules implement structured plans, validation, gated execution, and factual responses. Python does not call Codex as an API. | **COMPLETE** as a provider-independent prototype. **REQUIRED:** production host lifecycle, operator identity, approved provider contract, availability, and operational support. |
| LLM host | Codex is configured for Development/Lab in `.codex/config.toml`; ten tool names are listed and backup prompts for approval. | **ENVIRONMENT-SPECIFIC:** the checked-in host config contains local absolute Windows paths. **REQUIRED:** an enterprise-approved production host and managed host configuration. |
| RAG | `rag/` loads direct-child Markdown from `docs/knowledge/`, chunks deterministically, and ranks lexical overlap with citations. Only generic examples are present. | **COMPLETE** as local lexical prototype. **REQUIRED:** governed source approval, ownership, versioning, access controls, freshness, and review before production knowledge is loaded. |
| MCP | `mcp-server/server.py` exposes ten fixed tools over stdio, validates tool parameters and configured targets, and invokes a static PowerShell bridge. Backup has an MCP elicitation gate. | **COMPLETE** as a local host integration. **REQUIRED:** decide and secure production hosting, identity boundaries, authentication/authorization if remote, process isolation, monitoring, and operational logging. |
| PowerShell | `DbaAutomation/` contains ten Ops capabilities and shared validation, connection, configuration, and logging helpers. `invoke_ops_tool.ps1` dispatches a fixed capability. | **COMPLETE** as an implementation. **REQUIRED:** signed/released module, controlled dependency versions, service identity and filesystem ACL validation, and supported-version testing. |
| SQL Server | SQL operations use the configured allowlist, approved databases, protected-system checks, and connection helper. | **BLOCKED:** broad live SQL and effective permission validation has not completed in the Lab. Production targets, versions, certificates, and grants have not been validated. |
| Monitoring/evaluation/reporting/workflow | `monitoring/` provides fixed read-only collection, deterministic configured severity, text/HTML/JSON reports, and a manual workflow entry point. | **COMPLETE** as a manual workflow. **REQUIRED:** operational schedule/owner, concurrency and retry policy, artifact lifecycle, persistent audit, and alert/incident handling before unattended use. |

The existing PowerShell `Get-OpsServerHealthReport` remains a separate behavior that writes its configured HTML report. The Python reporting/workflow path writes its own configured artifacts. Neither is an authenticated multi-user report service.

## 2. Configuration

**COMPLETE:** PowerShell configuration has a JSON schema and separate Development, Test, UAT, and Production examples. It includes environment, approved instances/databases, protected databases, trust policy, paths, thresholds, and timeouts. Monitoring evaluation and workflow each have their own schema/example. Configuration loaders reject unknown properties; credentials are not part of these schemas.

**ENVIRONMENT-SPECIFIC / REQUIRED:** replace example targets, shares, output locations, approved database lists, thresholds, timeouts, and identity settings through an environment-owned configuration release. Do not promote the Development JSON as Production configuration. Keep actual environment configuration out of source control and protect it with deployment ACLs. `.gitignore` excludes `Config/*.secret.json` but does not exclude `Config/config.development.json`; verify repository policy before committing any real environment values.

**Phase 17B COMPLETE:** the MCP target reader and PowerShell loader now require an explicitly selected `DBA_AUTOMATION_CONFIG`; neither falls back to Development. If `DBA_AUTOMATION_ENV` is present, it must match the selected configuration. The shared PowerShell schema now includes `EnvironmentId` and `BackupPolicy`; MCP preflight and `Backup-OpsDatabase` enforce backup enablement and fixed allowed types, while approval remains mandatory. Production validation rejects known Development/Lab/example placeholders, requires trust disabled, and requires absolute backup/report/log paths. The checked-in Production JSON is intentionally a schema-valid template that runtime validation rejects until replaced. Monitoring workflow configuration carries its own environment identifier and output/check policy, so deployment still must verify its SQL-instance allowlist agrees with the automation configuration.

Relative PowerShell output paths are resolved from the selected configuration file. Workflow policy/output paths are config-relative. The output path, share availability, and ACL must be tested under the actual service identity. Threshold examples are policy samples, not DBA-approved production SLAs.

## 3. Secrets and identity

**COMPLETE:** no password/token is required in MCP arguments or committed configuration schema. The MCP server inherits its launching process identity and environment. PowerShell logs and MCP responses have sanitization controls. The least-privilege reference recommends distinct environment identities and a managed Windows identity such as a gMSA for Production where supported.

**REQUIRED:** define a non-human runtime identity for each environment, its Windows logon rights, service/process ownership, SQL authentication path, rotation/revocation process, and filesystem access. No production service account or secret store is provisioned by this repository. Prefer Windows integrated authentication with a dedicated managed identity if approved by the platform team; any exception requiring a SQL password must use an organization-approved secret store and never be passed as a tool argument or written to project files/logs.

**ENVIRONMENT-SPECIFIC:** Development sets `TrustServerCertificate: true`; Test/UAT/Production examples set it false. The connection helper passes the setting per connection rather than modifying shared dbatools state. Production still needs a valid SQL certificate chain, name matching, trust-store distribution, and verification under the actual runtime. The Lab TLS failure remains unresolved by design and is not a proposed production workaround.

## 4. SQL Server permissions

The reference in `docs/sql/create-ai-dba-login.sql` is commented documentation only and has not been executed. It calls out these scopes:

| Capability | Permission scope described by current implementation | Production validation required |
|---|---|---|
| Inventory, health, size | Connection and catalog/file metadata for explicitly approved user databases; exact dbatools/engine metadata needs may vary. Health reads metadata and does not run `DBCC CHECKDB`. | Confirm exact access on every supported SQL Server/dbatools combination. |
| Backup status | Approved-database metadata plus read-only backup history via `Get-DbaLastBackup`/`msdb`. | Inspect actual dbatools SQL reads and grant only tested `msdb` SELECT access. |
| Failed SQL Agent jobs | Fixed parameterized query over `msdb.dbo.sysjobs` and `sysjobhistory`; documentation proposes narrow column SELECT grants instead of SQL Agent roles. | Validate effective grants, row visibility, job metadata exposure, and behavior with the Production engine version. |
| Blocking, long queries, disk space | Server-wide DMV/state visibility; function output is filtered to approved databases, but the SQL permission itself is instance-scoped. Version-specific state permission and DMV access are documented. | Security owner must accept or reject the unavoidable broader visibility and validate each exact DMV/DMF permission. |
| Backup | `BACKUP DATABASE` in approved databases; `BACKUP LOG` only when log backup is enabled; SQL Server service identity needs write access to the configured destination. The implementation checks dbatools completion/checksum and does not use `RESTORE VERIFYONLY`. | Validate the exact grant and file destination. A separate authorized restore-test process is needed where recovery validation is required; this automation identity is not granted `CREATE DATABASE`. |
| Composite server report | Reuses diagnostic functions and writes a local HTML file. | Same SQL permissions as its component diagnostics plus report-directory write permission. |

**BLOCKED:** no live effective-grant validation has completed because Development/Lab SQL connectivity has a documented TLS problem. No Production grants or logins exist from this project. Before Test/UAT, a SQL security owner must review the exact permission plan and confirm that instance-wide DMV visibility is acceptable. Do not add `sysadmin`, `db_owner`, broad SQL Agent roles, or `CREATE DATABASE` to suppress permission errors.

## 5. MCP deployment

**COMPLETE:** the current local stdio server has exactly ten predefined tools; it accepts no raw SQL, arbitrary shell/PowerShell, executable path, or client-supplied backup destination. Instance/database allowlists and the MCP backup elicitation are server-side boundaries. The PowerShell bridge uses a fixed argument list and `shell=False`.

**REQUIRED for Production:** choose between an isolated local stdio worker and a service-hosted MCP endpoint. Stdio is appropriate to a single trusted host/process boundary; it does not provide network authentication or per-user identity propagation. A remote/service host needs approved transport encryption, authentication, authorization by tool and target, client identity propagation, rate/timeout limits, process/container or OS isolation, secret-safe telemetry, and a threat-reviewed service boundary. Do not treat tool annotations or host prompts as authorization; retain server-side allowlists and elicitation/approval. Define which identity owns the MCP process and prevent it from inheriting an administrator's interactive session.

## 6. PowerShell execution

**COMPLETE:** MCP launches `pwsh` with `-NoProfile`, `-NonInteractive`, a fixed script path, bounded timeouts, and no shell interpolation. `setup.ps1` validates PowerShell 7, dbatools, SqlServer, Pester, Python, the venv, and MCP SDK without installing them. The module manifest requires dbatools at least 2.0.0; setup currently checks a higher minimum (2.8.4). The code does not set Windows execution policy.

**REQUIRED:** agree the effective execution policy and script-signing policy with Windows operations; sign and verify released PowerShell artifacts if organizational policy requires it. Pin and validate exact PowerShell/dbatools/SqlServer versions, operating system patch level, and module source. Run under the dedicated service identity with read access to code/config and write access only to necessary log/report/backup destinations. Restrict modification of the bridge and module to deployment principals. The current PATH lookup for `pwsh` must resolve to an approved installation in the deployed host.

## 7. Monitoring

**COMPLETE:** enabled checks come from a strict workflow config and map only to the existing eight read-only MCP diagnostics. Evaluation thresholds are external and deterministic. Individual collection failures stay in the report; infrastructure failures become workflow `FAILED`. The manual workflow provides fixed exit codes and no automatic retry.

**REQUIRED before unattended use:** choose scheduler, run identity, cadence, maintenance windows, maximum runtime, missed-run policy, concurrency/overlap behavior, timeout handling, and an approved bounded retry policy. Any retry must be limited to idempotent read-only calls and reviewed for duplicate load; do not automatically retry state-changing operations. Validate check selection, business thresholds, time zones, and failure status handling with DBA owners. No Windows Task Scheduler task or other scheduler is defined.

## 8. Reporting and artifact storage

**COMPLETE:** the Python workflow emits configured text, HTML, and JSON artifacts with UTC timestamp/workflow ID names and exclusive create semantics. Existing report paths come from external configuration. The PowerShell health-report tool similarly writes the configured report directory.

**REQUIRED:** provision storage, ACLs, encryption-at-rest policy, backup/recovery, retention duration, cleanup owner, capacity limits, and access auditing. Reports may contain database names, job names, instance names, operational findings, and volume metadata. The local PowerShell HTML report also includes job error text and query/session details in its tables; HTML escaping prevents markup execution but does not remove sensitive operational content. Review/redact the content contract and restrict report access. No retention cleanup or artifact access policy is implemented.

## 9. Audit

**COMPLETE:** `assistant/audit.py` defines a validated metadata-only event shape. MCP calls emit bounded parameter-name/status/timing logs; PowerShell writes daily structured logs with sanitized error text. The workflow returns audit events with its result.

**REQUIRED:** provide a durable audit sink before unattended or Production operations. Define delivery guarantees, access separation, append/tamper protection, retention/legal hold, clock synchronization, correlation IDs, event schema/versioning, alerting on missing events, and secure export. The workflow audit events are currently in-memory/result-only; PowerShell daily log writes fail safely with a warning and are local files. No tamper-evident or centralized audit retention is implemented.

## 10. LLM integration

**COMPLETE:** Codex is the Development/Lab host; the Python application is provider-independent and uses structured plans, intake validation, tool allowlists, a controlled executor, source attribution, and a separate response formatter. The MCP server remains the SQL execution boundary. Backup remains `NEEDS_APPROVAL` and requires application and MCP approval gates.

**REQUIRED for Production:** select an enterprise-approved LLM host/provider and identity, data residency/retention terms, prompt/model version management, availability controls, and a human approval experience with named approver and auditable decision. Test provider output against the exact structured contract. Add adversarial prompt-injection and plan-mutation evaluations, especially for retrieved material; treat all model/RAG content as untrusted. Continue to rebuild/canonicalize plans through the existing planner/intake rather than trusting model-supplied classifications or tool names. Keep the MCP allowlist and PowerShell validation as final authorities. No live LLM API, production provider, or approval UI is implemented.

## 11. RAG and knowledge governance

**COMPLETE:** the current local retriever is lexical, restricted to Markdown files in the configured knowledge directory, and returns filename/section attribution. Repository samples are generic and contain no company/customer material. There is no embedding, vector store, ingestion pipeline, or Azure AI Search dependency.

**REQUIRED:** assign a knowledge owner, authoritative source catalog, licensing/IP review, document approver, classification/ACL rules, version/effective dates, expiration/revalidation, ingestion review, deletion workflow, citation validation, and incident process for poisoned/stale material. Do not ingest secrets, credentials, customer records, or unauthorized proprietary content. A managed retrieval service is justified only if approved scale, multi-user ACL filtering, freshness, and operational requirements exceed the local corpus; it is not a prerequisite for this prototype and is not currently deployed.

## 12. Testing and validation

**COMPLETE:** the repository's Python suite has 217 passing tests at this assessment baseline. It includes offline models, routing/intake, mocked execution and approval cases, MCP schemas and stdio discovery, RAG, monitoring/evaluation/reporting/workflow failure cases, and no-overwrite/sanitization checks. Pester unit/configuration tests cover public functions, target validation, configuration, backup `-WhatIf`, and permission-doc safety. Two SQL integration tests cover read-only inventory and health and are opt-in.

**BLOCKED / REQUIRED:** live SQL connectivity, exact effective grants, all eight live diagnostic paths, certificate validation, SQL-version matrix, load/concurrency, and production identity are not validated. The last recorded Pester execution was blocked before test bodies because the environment denied Pester's temporary `HKCU\Software\Pester` key; the registry was not changed. Re-run Pester in an approved test host. Add UAT tests for all configured read-only tools, PowerShell/MCP process isolation, approval decline/absence, report ACLs, audit delivery failure, config promotion, and recovery/rollback. Add security testing for dependency vulnerabilities, source signing, permission boundary, prompt injection, and logging/data exposure. Do not treat fake MCP results as SQL integration evidence.

## 13. Deployment and promotion

**COMPLETE:** the source tree has setup validation and separate environment configuration examples. `.gitignore` excludes the venv/cache, selected root-level log/report formats, backup file extensions, and files named `*.secret.json`. **REQUIRED:** extend repository rules/review for Phase 16D nested `Reports/Monitoring` output and text artifacts; they are not covered by the current `Reports/*.html`/`Reports/*.json` patterns. Verify generated artifacts cannot be committed accidentally.

**REQUIRED:** define branch/review protection, reproducible build, dependency lockfiles with hashes/transitive versions, SBOM and vulnerability scanning, signed package/artifact provenance, environment-specific config injection, approval gates, immutable release identifiers, promotion evidence, database permission change approval, and rollback procedure. There is no deployment/build pipeline, package artifact, dependency lock for the Python environment, promotion automation, or rollback tooling. Keep configuration promotion separate from code and ensure target allowlists are reviewed at each stage. Rollback must disable the MCP/workflow host and restore the prior signed application/configuration package without changing SQL state.

## 14. Operations

**REQUIRED:** assign service, SQL, Windows, security, knowledge, and report owners; write runbooks for startup, credential/identity failure, SQL connectivity, certificate expiry, missed monitoring run, permission denied, report storage full, audit-sink failure, approval rejection, and incident escalation. Define log collection, alert routing, service health probes, capacity, on-call coverage, and change windows. The project has local log/report output but no alerting or incident-response integration. Do not send alerts until recipients, confidentiality, and routing are approved.

## 15. Concrete security risks and mitigations

| Remaining risk | Mitigation required |
|---|---|
| Environment-specific automation and workflow configs are separate files and their allowlists can drift. | Keep explicit matching environment identifiers and validate the monitoring target/check policy against the deployment-owned automation config before use. |
| The stdio process inherits the host identity and can reach every capability available to that identity. | Dedicated non-human identity, OS/process isolation, least filesystem rights, and server-side target allowlists. |
| DMV permissions reveal instance-wide activity even when result rows are filtered by database. | Have the SQL security owner explicitly accept the scope or disable those checks; use isolated instances if the exposure is unacceptable. |
| Local report files contain sensitive operational data; HTML escaping is not confidentiality control. | Minimize report fields, apply restrictive ACLs/encryption, retention and cleanup, and audit access. |
| Audit events are not durably stored; local logging is not tamper-evident. | Add a protected append-only/central sink, delivery monitoring, retention, and access separation before unattended/production operation. |
| Approval grants are process-local and the production approver identity/UI is not implemented. | Add an authenticated approval experience and durable decision correlation; preserve the existing exact-operation binding and MCP elicitation. |
| Local lexical RAG has no production document ACL/freshness controls. | Govern source ingestion and apply document-level access controls before adding internal content or multi-user retrieval. |
| Python transitive dependencies and PowerShell modules are not fully locked to a reproducible release. | Generate and review lockfiles/SBOM, pin hashes/versions, scan and sign artifacts. |
| Development defaults and host config include local paths/targets. | Inject stage-specific config and host settings at deployment; do not reuse the Development configuration or `.codex/config.toml` in Production. |
| Sanitized provider errors can make production diagnosis difficult. | Use a protected operator-only diagnostic channel with correlation IDs and secret-safe detail; keep client responses sanitized. |

## 16. Current blockers by stage

### Development/Lab

- **BLOCKED:** earlier live integrations failed during TLS pre-login negotiation. Do not weaken TLS or trust policy to make them pass. One prior Codex-host inventory call succeeded, but that does not establish broad SQL or grant validation.
- **BLOCKED:** effective grants and dbatools metadata needs have not been checked against the intended least-privilege identity.
- **ENVIRONMENT-SPECIFIC:** Pester could not execute in the last recorded run because registry access was denied; use an approved test environment rather than changing Windows registry settings.

### Test/UAT

- **REQUIRED:** no UAT execution evidence is recorded. Establish separate targets/identity/config, certificate chain validation, service filesystem ACLs, effective SQL grants, all diagnostic integration cases, approval behavior, and artifact/audit handling before Production consideration.
- **REQUIRED:** validate the complete workflow with the actual scheduler identity and outputs, but create no production-like destructive operation without its separate approval and change plan.

### Production

- **BLOCKED:** no approved production host/LLM, service identity, secure MCP hosting decision, production certificate validation, or Production permission sign-off is implemented/evidenced. The project-scoped Codex MCP configuration explicitly selects Development only and is not a Production launch configuration.
- **BLOCKED:** no persistent audit sink, report retention/access control, scheduler/operations ownership, deployment/promotion/rollback process, or dependency-locked signed release exists. No real Production target, database allowlist, artifact location, identity, or backup policy has been supplied or validated.
- **BLOCKED:** the current Development fallback and project-local Codex MCP configuration are not acceptable as Production launch configuration.

## 17. Dependency-based implementation order

1. **REQUIRED — Agree operating scope:** supported SQL Server/Windows/PowerShell/dbatools versions, monitoring SLAs, database/tool scope, data classification, owners, and environment boundaries.
2. **REQUIRED — Design identity and network:** provision separate Test/UAT identity, establish connectivity, certificates and trust chain, and define least-privilege SQL grants for each supported engine version.
3. **REQUIRED — Make releases reproducible:** lock Python and PowerShell dependencies, build/sign artifacts, generate SBOM, scan dependencies, and decide script-signing/execution-policy controls.
4. **REQUIRED — Establish Test/UAT configuration and host:** inject explicit config, prevent Development fallback, reconcile monitoring and automation allowlists, isolate MCP/PowerShell process identity, and set filesystem ACLs.
5. **REQUIRED — Validate permission and behavior:** run Pester on an approved host, validate each read-only function and all workflow paths in UAT, test TLS/cert validation, and have the SQL owner sign off exact effective grants and DMV exposure.
6. **REQUIRED — Complete audit and artifact controls:** persist metadata-only events to a protected sink; define report ACL, retention, cleanup, storage capacity, and recovery.
7. **REQUIRED — Define unattended operations:** select scheduler, concurrency/missed-run behavior, bounded read-only retry rules, alert routing, service health checks, runbooks, and incident ownership.
8. **REQUIRED — Select Production LLM/approval host:** approve provider, data handling, plan-contract conformance, identity, explicit named human approval, and adversarial prompt/RAG evaluations. Keep the existing MCP and PowerShell boundaries.
9. **RECOMMENDED — Govern RAG sources:** approve authoritative docs, version/freshness, classification/access, ingestion/removal, and citations. Assess managed retrieval only against accepted requirements.
10. **REQUIRED — Controlled promotion:** promote the same signed release with stage-specific configuration and recorded validation evidence; rehearse disabling the host and restoring the previous release before any Production enablement.

No deployment, production connection, login/permission change, Windows task, backup, restore, or state-changing SQL was performed for this assessment. Live SQL validation remains deferred due to the known Development/Lab TLS limitation.
