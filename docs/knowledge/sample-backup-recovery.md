# SQL Server Backup and Recovery Concepts

This generic learning note describes common SQL Server concepts. Confirm behavior against the applicable SQL Server version and an approved organization procedure before operational use.

## Backup types

- **Full database backup:** Captures the database data needed as a full database backup baseline. It does not, by itself, capture subsequent changes made after it completes.
- **Differential backup:** Captures extents changed since the differential base, normally the most recent full backup that established that base. A restore sequence commonly uses the full backup followed by the selected differential.
- **Transaction log backup:** Captures log records since the preceding log backup and advances the log backup chain. In the Full and Bulk-Logged recovery models, regular log backups are needed to manage log truncation and support point-in-time recovery. A log backup sequence depends on an unbroken chain and compatible preceding backups.

Backup schedules should match workload, recovery objectives, storage capacity, retention, and operational policy. Copy-only backups and file/filegroup strategies have additional behavior and should be covered by a specific reviewed procedure when used.

## Recovery models

- **Simple:** SQL Server reuses inactive log space automatically; transaction log backups are not supported. Recovery is limited by the available database backups.
- **Full:** Supports log backups and point-in-time recovery when an unbroken log chain and required backup sequence are available.
- **Bulk-Logged:** Can reduce logging for certain bulk operations while retaining log backups, but some operations can limit point-in-time recovery within the affected log backup. Follow version-specific guidance for recovery planning.

Changing recovery model affects recovery behavior and should follow an approved change procedure. A switch to Full or Bulk-Logged requires an appropriate data backup to establish a log chain before relying on log backups.

## Backup verification concepts

Verification has multiple levels. A command reporting successful completion and backup metadata/checksum checks provide useful evidence, but do not prove that every required recovery sequence is usable. SQL Server's `RESTORE VERIFYONLY` performs checks without restoring the database, but it is not equivalent to a restore test and may require elevated permissions unsuitable for a least-privilege monitoring/backup identity. Never infer that a backup is recoverable solely from a file's existence or a successful backup command.

Use an independently authorized validation process for stronger media checks where required. Keep validation permissions separate from routine monitoring and backup permissions, and use the organization's approved isolated recovery environment.

## Restore testing concepts

A restore test validates that selected backup media and a documented recovery sequence can produce a usable database in a controlled environment. A test plan should define an isolated target, correct backup chain, encryption/key dependencies if applicable, consistency/application checks, measured elapsed time, cleanup approval, and evidence retention. Restore tests are state-changing and must only be performed under a separately approved procedure. Never restore over a protected or production database as an ad hoc test.

## RPO and RTO

- **Recovery Point Objective (RPO):** The maximum acceptable amount of data loss, usually expressed as time. Backup cadence and log-chain continuity contribute to achievable recovery points.
- **Recovery Time Objective (RTO):** The target time to restore service. Backup size, storage throughput, restore sequence, database recovery, validation, and dependencies all affect actual recovery time.

RPO/RTO are service requirements, not guarantees made by the backup schedule alone. Validate them with measured restore exercises and documented assumptions.

## Common monitoring checks

- Confirm recent successful full, differential, and log backups according to the database's recovery model and schedule.
- Check that required log backup chains are continuous and that backup history reflects the expected sequence.
- Monitor backup duration, size trends, warning/error outcomes, and retention expiration.
- Confirm the configured destination and its available capacity through the approved monitoring method; SQL-visible database-file volumes may not include a separate backup volume.
- Check that SQL Server can write to the destination using its service identity, without placing credentials in scripts or logs.
- Confirm checksum policy and review job history/alerts for failures.
- Track whether independent validation and restore exercises meet required cadence and RPO/RTO.
- Distinguish recorded backup history from proof of recoverability; use an approved restore test for that evidence.
