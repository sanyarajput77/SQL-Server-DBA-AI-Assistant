<#
.SYNOPSIS
    Identifies active queries exceeding a duration threshold.
.DESCRIPTION
    Queries dynamic management views (DMVs) for active executing requests
    whose total elapsed time exceeds the specified threshold.
.PARAMETER SqlInstance
    Target SQL Server instance. Must be in the approved allowlist. Defaults to configuration default.
.PARAMETER ThresholdSeconds
    Duration threshold in seconds. Queries running longer than this are returned. Defaults to config (60s).
.EXAMPLE
    Get-OpsLongRunningQueries
.EXAMPLE
    Get-OpsLongRunningQueries -ThresholdSeconds 30
.EXAMPLE
    Get-OpsLongRunningQueries -SqlInstance 'ConfiguredInstance' -ThresholdSeconds 10
#>
function Get-OpsLongRunningQueries {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false, Position = 0)]
        [string]$SqlInstance,

        [Parameter(Mandatory = $false, Position = 1)]
        [ValidateRange(1, 86400)]
        [int]$ThresholdSeconds
    )

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $status = 'Success'
    $errorMsg = $null

    try {
        $SqlInstance = Assert-OpsApprovedInstance -SqlInstance $SqlInstance
        $config = Get-OpsConfig
        $approvedDatabases = Get-OpsApprovedDatabaseTargets -Database $null -Config $config
        Initialize-OpsSession -Config $config
        $sqlConnection = Connect-OpsSqlInstance -SqlInstance $SqlInstance -Config $config

        if ($ThresholdSeconds -le 0) {
            $ThresholdSeconds = if ($config.Thresholds.LongRunningQuerySeconds) { [int]$config.Thresholds.LongRunningQuerySeconds } else { 60 }
        }

        # Safe internal parameterized DMV query
        $query = @"
SELECT
    r.session_id AS Spid,
    ISNULL(DB_NAME(r.database_id), 'Unknown') AS DatabaseName,
    r.status AS Status,
    r.command AS Command,
    (r.total_elapsed_time / 1000) AS ElapsedSeconds,
    r.cpu_time AS CpuTimeMs,
    r.logical_reads AS LogicalReads,
    r.wait_type AS WaitType,
    r.wait_time AS WaitTimeMs,
    ISNULL(s.login_name, '') AS LoginName,
    ISNULL(s.host_name, '') AS HostName,
    ISNULL(s.program_name, '') AS ProgramName,
    ISNULL(SUBSTRING(t.text, (r.statement_start_offset/2)+1,
        ((CASE r.statement_end_offset
            WHEN -1 THEN DATALENGTH(t.text)
            ELSE r.statement_end_offset
          END - r.statement_start_offset)/2) + 1), '') AS StatementText
FROM sys.dm_exec_requests r
INNER JOIN sys.dm_exec_sessions s ON r.session_id = s.session_id
CROSS APPLY sys.dm_exec_sql_text(r.sql_handle) t
WHERE r.session_id > 50
  AND r.session_id <> @@SPID
  AND (r.total_elapsed_time / 1000) >= @ThresholdSec
  AND r.database_id = DB_ID()
"@

        $queries = foreach ($databaseName in $approvedDatabases) {
            Invoke-DbaQuery -SqlInstance $sqlConnection -Database $databaseName -Query $query -SqlParameters @{ ThresholdSec = $ThresholdSeconds } -EnableException
        }

        $results = [System.Collections.Generic.List[PSCustomObject]]::new()
        if ($queries) {
            foreach ($row in $queries) {
                $results.Add([PSCustomObject]@{
                    SqlInstance      = $SqlInstance
                    Spid             = $row.Spid
                    DatabaseName     = $row.DatabaseName
                    Status           = $row.Status
                    Command          = $row.Command
                    ElapsedSeconds   = [int]$row.ElapsedSeconds
                    ThresholdSeconds = $ThresholdSeconds
                    CpuTimeMs        = [int]$row.CpuTimeMs
                    LogicalReads     = [int64]$row.LogicalReads
                    WaitType         = $row.WaitType
                    WaitTimeMs       = [int]$row.WaitTimeMs
                    LoginName        = $row.LoginName
                    HostName         = $row.HostName
                    ProgramName      = $row.ProgramName
                    StatementText    = $row.StatementText
                })
            }
        }

        return $results.ToArray()
    } catch {
        $status = 'Error'
        $errorMsg = $_.Exception.Message
        throw
    } finally {
        $stopwatch.Stop()
        Write-OpsLog -FunctionName 'Get-OpsLongRunningQueries' `
            -Parameters @{ SqlInstance = $SqlInstance; ThresholdSeconds = $ThresholdSeconds } `
            -Status $status -DurationMs $stopwatch.ElapsedMilliseconds -ErrorMessage $errorMsg
    }
}
