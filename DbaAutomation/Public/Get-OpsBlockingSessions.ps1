<#
.SYNOPSIS
    Identifies active blocking and blocked sessions on SQL Server.
.DESCRIPTION
    Analyzes current sessions to detect head blockers, blocked SPIDs, wait times,
    and associated SQL text without modifying any database state.
.PARAMETER SqlInstance
    Target SQL Server instance. Must be in the approved allowlist. Defaults to configuration default.
.EXAMPLE
    Get-OpsBlockingSessions
.EXAMPLE
    Get-OpsBlockingSessions -SqlInstance 'ConfiguredInstance'
#>
function Get-OpsBlockingSessions {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false, Position = 0)]
        [string]$SqlInstance
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

        # Restrict collection and returned blocked sessions to configured databases.
        $processes = Get-DbaProcess -SqlInstance $sqlConnection -Database $approvedDatabases -EnableException:$false -ErrorAction SilentlyContinue

        $procBySpid = @{}
        $blockedProcs = [System.Collections.Generic.List[PSCustomObject]]::new()

        if ($processes) {
            foreach ($p in $processes) {
                $procBySpid[$p.Spid] = $p
                if ($p.BlockingSpid -and $p.BlockingSpid -gt 0) {
                    $blockedProcs.Add($p)
                }
            }
        }

        $results = [System.Collections.Generic.List[PSCustomObject]]::new()

        foreach ($blocked in $blockedProcs) {
            if ($blocked.Database -notin $approvedDatabases) { continue }
            $blockerSpid = $blocked.BlockingSpid
            $blocker = $procBySpid[$blockerSpid]

            $results.Add([PSCustomObject]@{
                SqlInstance     = $SqlInstance
                BlockedSpid     = $blocked.Spid
                BlockerSpid     = $blockerSpid
                DatabaseName    = $blocked.Database
                BlockedCommand  = $blocked.Command
                BlockedStatus   = $blocked.Status
                BlockedLogin    = $blocked.Login
                BlockedHost     = $blocked.Host
                BlockedProgram  = $blocked.Program
                BlockedQuery    = $blocked.LastQuery
                BlockerStatus   = if ($blocker) { $blocker.Status } else { 'Unknown' }
                BlockerCommand  = if ($blocker) { $blocker.Command } else { 'Unknown' }
                BlockerLogin    = if ($blocker) { $blocker.Login } else { 'Unknown' }
                BlockerHost     = if ($blocker) { $blocker.Host } else { 'Unknown' }
                BlockerProgram  = if ($blocker) { $blocker.Program } else { 'Unknown' }
                BlockerQuery    = if ($blocker) { $blocker.LastQuery } else { 'Unknown' }
            })
        }

        return $results.ToArray()
    } catch {
        $status = 'Error'
        $errorMsg = $_.Exception.Message
        throw
    } finally {
        $stopwatch.Stop()
        Write-OpsLog -FunctionName 'Get-OpsBlockingSessions' `
            -Parameters @{ SqlInstance = $SqlInstance } `
            -Status $status -DurationMs $stopwatch.ElapsedMilliseconds -ErrorMessage $errorMsg
    }
}
