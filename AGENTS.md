# AI DBA Assistant - Safety Rules and Operating Guidelines

This document outlines the mandatory safety rules, architectural boundaries, and operating constraints for all AI agents and human contributors working within this project.

## Mandatory Safety Rules

1. **Use dbatools wherever practical.** Prefer standardized, community-tested dbatools cmdlets over bespoke or low-level scripts.
2. **Verify commands before use.** Before using any dbatools command, function, parameter, or configuration setting, verify it using `Get-Command`, `Get-Help`, or official dbatools documentation.
3. **Never invent commands or parameters.** Adhere strictly to verified cmdlets and parameter names.
4. **One reusable function per DBA capability.** Structure functionality into modular, reusable PowerShell functions (`Ops-<Capability>`).
5. **No one-off scripts.** Do not create ad-hoc scripts when a reusable function is appropriate.
6. **NEVER accept raw SQL.** Never create or expose a tool or interface that accepts raw SQL supplied by an AI agent or user.
7. **NEVER accept arbitrary shell commands.** Never create or expose a tool or interface that accepts arbitrary PowerShell, CMD, or shell commands.
8. **Validate SQL Server instances against an allowlist.** Every operation must validate the requested instance against an explicit configuration allowlist.
9. **Validate database existence.** Confirm target databases exist before performing any actions on them.
10. **Protect system databases.** System databases (`master`, `model`, `msdb`, `tempdb`) must never be modified by automation.
11. **Fixed backup types only.** Allowed backup types must strictly come from the predefined set: `Full`, `Differential`, and `Log`.
12. **Configured backup destinations only.** Backup destination paths must come from verified configuration and must NEVER be dynamically supplied by an AI agent.
13. **Never log secrets.** Never record passwords, credentials, access tokens, or connection strings containing secrets in logs, reports, or transcripts.
14. **Support `-WhatIf`.** All state-changing functions must support PowerShell's `-WhatIf` / `-Confirm` mechanism (`[CmdletBinding(SupportsShouldProcess)]`).
15. **Return structured objects.** State-changing and diagnostic functions must return structured PowerShell objects (PSCustomObject), not unformatted text.
16. **Read-only remains read-only.** Diagnostic and health functions must be purely read-only and must never cause hidden or side-effect modifications.
17. **NO destructive operations.** The following operations are strictly forbidden:
    - `DROP DATABASE`
    - `DROP TABLE`
    - `DELETE` data
    - Database deletion
    - Overwrite/restore-over operations
    - SQL Server service restart
    - Windows service restart
    - Arbitrary command execution
18. **Comprehensive structured logging.** Every automation and tool invocation must log:
    - Timestamp (UTC / Local)
    - Function or tool name
    - Sanitized parameters (no credentials)
    - Execution result / status (Success, Warning, Error)
    - Duration in milliseconds
19. **Sanitized logs.** Logging mechanisms must sanitize input parameters to prevent accidental credential leakage.
20. **Least privilege.** The automation and associated SQL logins must operate under least privilege, without `sysadmin` or `db_owner` rights.
21. **Fail safely.** When validation fails, functions must immediately abort execution and return a clear error without partial modifications.
22. **No silent security bypasses.** Never silently ignore validation errors or bypass security controls.
23. **Explicit human approval for disruptive operations.** Any operation that creates/modifies credentials, restores databases, changes configuration, or alters state must require explicit human approval (`NEEDS_APPROVAL`).
24. **Portable across environments.** Maintain complete portability across Development, Test/UAT, and Production by isolating environment-specific configuration, credentials, paths, and server names.
