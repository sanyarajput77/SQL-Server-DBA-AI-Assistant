# SQL Server Blocking Concepts

This generic learning note describes common SQL Server behavior. It is not a live diagnosis or authorization to change a session.

## What blocking is

Blocking occurs when a session holds a lock that another session needs, causing the waiting request to pause. SQL Server uses locks to preserve isolation and consistency. Some waiting is expected; duration, workload impact, blocking-chain depth, and the affected application determine whether an incident exists.

## Blocking versus deadlocking

- **Blocking:** One or more requests wait for a lock held by another request. The blocking chain may clear when the holder commits, rolls back, or otherwise releases the lock.
- **Deadlock:** Two or more transactions form a cycle of resource dependencies. SQL Server detects the cycle and chooses a victim to roll back so the others can proceed. A deadlock is not merely a long blocking wait.

Deadlocks are commonly diagnosed from deadlock graph/event data; a point-in-time blocking snapshot may not capture a completed deadlock.

## Common causes

- A transaction remains open longer than intended or performs user interaction while holding locks.
- A query scans more rows/pages than necessary or uses an access path that holds locks broadly.
- Concurrent reads and writes interact under the selected isolation level.
- A schema modification lock conflicts with application activity.
- Batch size, workload spikes, or missing/ineffective indexing increase lock duration or contention.
- A request is waiting on something other than a lock; confirm the wait type before labeling it blocking.

## What a DBA should investigate

Identify the head blocker and the full blocking chain; capture observation time, wait type/resource, wait duration, database, session/request identifiers, transaction age, and impact. Determine whether requests are still active and whether the same pattern recurs. Correlate with application release/workload changes, transaction boundaries, query plans, and relevant SQL Server wait/lock diagnostics. Use authorized monitoring output and respect the configured approved-database scope.

Do not assume the oldest session is the root cause. Do not infer query text if it is not returned by an approved tool. A snapshot can change immediately after collection.

## Safe troubleshooting sequence

1. Confirm the report is current and represents lock waits rather than a different wait class.
2. Assess user impact and identify affected applications and databases.
3. Trace the chain to the head blocker and gather session/transaction context using approved read-only diagnostics.
4. Coordinate with the application owner and review the responsible transaction/query behavior and recent changes.
5. Select a corrective action through the normal change/incident process, such as correcting transaction scope or query/index design. Evaluate isolation-level changes carefully because they alter behavior.
6. Do not kill sessions, cancel requests, change database settings, or modify application state without explicit authorization and an approved procedure. The existing read-only MCP diagnostics do not authorize those actions.
7. Recheck the condition and document findings, impact, resolution, and prevention follow-up.

## Useful SQL Server concepts

- Locks have modes and compatibility rules; a request can wait even when the resource is not a whole database.
- A blocking chain is a relationship between waiting and blocking requests; the head blocker may not itself be waiting.
- Transaction duration and isolation level influence lock lifetime and concurrency behavior.
- Wait statistics and session/request DMVs are point-in-time or cumulative diagnostic evidence, not a complete causal explanation by themselves.
- Deadlock graphs describe a cycle and victim selection; they should be distinguished from a blocking snapshot.
