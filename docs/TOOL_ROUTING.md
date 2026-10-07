# Natural-Language Tool Routing

Use only these existing MCP tools. Apply configured instance/database allowlists and system-database protections before acting.

| Example request | MCP tool | Behavior |
| --- | --- | --- |
| “List my databases” | `get_ops_database_inventory` | READ_ONLY |
| “Is DatabaseA healthy?” | `get_ops_database_health` | READ_ONLY; metadata only, no CHECKDB |
| “Which database is using the most space?” | `get_ops_database_size` | READ_ONLY |
| “Which databases haven't been backed up recently?” | `get_ops_backup_status` | READ_ONLY |
| “How much disk space is available?” | `get_ops_disk_space` | READ_ONLY; SQL-visible volumes hosting approved database files |
| “Which SQL Agent jobs failed?” | `get_ops_failed_jobs` | READ_ONLY |
| “Is anything blocking other sessions?” | `get_ops_blocking_sessions` | READ_ONLY |
| “What queries are running for a long time?” | `get_ops_long_running_queries` | READ_ONLY |
| “Give me a SQL Server health report” | `get_ops_server_health_report` | READ_ONLY_SQL / WRITES_REPORT; configured Reports directory only, host write prompt applies |
| “Back up DatabaseA” | `backup_ops_database` | NEEDS_APPROVAL; only if the database is approved; never run without explicit MCP elicitation |

## Routing constraints

- `DatabaseA` is a placeholder. The current configuration approves `AdventureWorks2022`, `PracticeDB`, `DemoDB`, `DistributionDB`, and `ReplicationLab`; reject `DatabaseA` unless a selected environment explicitly approves that exact database.
- Reject `master`, `model`, `msdb`, and `tempdb` for database targets.
- Use only instances in the active configuration's `ApprovedInstances`; use its default only when the user omits an instance.
- Backup type is limited to `Full`, `Differential`, or `Log`. The MCP caller cannot supply or change the backup path.
- Reject requests to run arbitrary SQL, PowerShell, CMD, shell commands, or programs. No such tool is exposed.
- Do not execute a backup based only on natural-language intent. Approval decline, absence, or elicitation failure means no dispatch.
