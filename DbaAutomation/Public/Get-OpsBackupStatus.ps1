<#
.SYNOPSIS
    Evaluates database backup recency against an SLA threshold.
.DESCRIPTION
    Identifies databases whose latest full, differential, or transaction log backup
    exceeds the specified hour threshold.
.PARAMETER SqlInstance
    Target SQL Server instance. Must be in the approved allowlist. Defaults to configuration default.
.PARAMETER Database
    Optional database name(s) to filter by.
.PARAMETER NotBackedUpHours
    Threshold in hours. Databases without a backup within this window are flagged. Defaults to config (24 hrs).
.EXAMPLE
    Get-OpsBackupStatus
.EXAMPLE
    Get-OpsBackupStatus -NotBackedUpHours 12
.EXAMPLE
    Get-OpsBackupStatus -SqlInstance 'ConfiguredInstance' -Database 'AdventureWorks2022' -NotBackedUpHours 24
#>
function Get-OpsBackupStatus {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false, Position = 0)]
        [string]$SqlInstance,

        [Parameter(Mandatory = $false, Position = 1)]
        [string[]]$Database,

        [Parameter(Mandatory = $false, Position = 2)]
        [ValidateRange(1, 8760)]
        [int]$NotBackedUpHours
    )

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $status = 'Success'
    $errorMsg = $null

    try {
        $SqlInstance = Assert-OpsApprovedInstance -SqlInstance $SqlInstance
        $config = Get-OpsConfig
        $Database = Get-OpsApprovedDatabaseTargets -Database $Database -Config $config
        Initialize-OpsSession -Config $config
        $sqlConnection = Connect-OpsSqlInstance -SqlInstance $SqlInstance -Config $config
        $Database = Assert-OpsSafeDatabase -SqlInstance $SqlInstance -Database $Database -Config $config -SqlConnection $sqlConnection

        if ($NotBackedUpHours -le 0) {
            $NotBackedUpHours = if ($config.Thresholds.BackupWarningHours) { [int]$config.Thresholds.BackupWarningHours } else { 24 }
        }

        $cmdParams = @{
            SqlInstance     = $sqlConnection
            Database        = $Database
            EnableException = $true
        }

        $backupHistory = Get-DbaLastBackup @cmdParams

        $results = [System.Collections.Generic.List[PSCustomObject]]::new()

        foreach ($b in $backupHistory) {
            $hoursSinceFull = if ($b.SinceFull) { [math]::Round([double]$b.SinceFull.TotalHours, 1) } else { $null }
            $hoursSinceDiff = if ($b.SinceDiff -and $b.LastDiffBackup) { [math]::Round([double]$b.SinceDiff.TotalHours, 1) } else { $null }
            $hoursSinceLog  = if ($b.SinceLog -and $b.LastLogBackup) { [math]::Round([double]$b.SinceLog.TotalHours, 1) } else { $null }

            $lastFull = if ($b.LastFullBackup) { [datetime]$b.LastFullBackup } else { $null }
            $lastDiff = if ($b.LastDiffBackup) { [datetime]$b.LastDiffBackup } else { $null }
            $lastLog  = if ($b.LastLogBackup)  { [datetime]$b.LastLogBackup }  else { $null }

            # Determine latest backup timestamp across all types
            $latestBackupDate = $null
            $latestBackupType = 'None'

            if ($lastFull) {
                $latestBackupDate = $lastFull
                $latestBackupType = 'Full'
            }
            if ($lastDiff -and (-not $latestBackupDate -or $lastDiff -gt $latestBackupDate)) {
                $latestBackupDate = $lastDiff
                $latestBackupType = 'Differential'
            }
            if ($lastLog -and (-not $latestBackupDate -or $lastLog -gt $latestBackupDate)) {
                $latestBackupDate = $lastLog
                $latestBackupType = 'Log'
            }

            $hoursSinceLatest = if ($latestBackupDate) {
                [math]::Round(((Get-Date) - [datetime]$latestBackupDate).TotalHours, 1)
            } else {
                $null
            }

            $exceedsThreshold = ($null -eq $hoursSinceLatest) -or ($hoursSinceLatest -gt $NotBackedUpHours)

            $results.Add([PSCustomObject]@{
                SqlInstance       = $SqlInstance
                DatabaseName      = $b.Database
                RecoveryModel     = $b.RecoveryModel.ToString()
                LastFullBackup    = $b.LastFullBackup
                LastDiffBackup    = $b.LastDiffBackup
                LastLogBackup     = $b.LastLogBackup
                HoursSinceFull    = $hoursSinceFull
                LatestBackupDate  = $latestBackupDate
                LatestBackupType  = $latestBackupType
                HoursSinceLatest  = $hoursSinceLatest
                ThresholdHours    = $NotBackedUpHours
                ExceedsThreshold  = $exceedsThreshold
                StatusMessage     = if ($exceedsThreshold) { "WARNING: Latest backup is older than $NotBackedUpHours hours ($hoursSinceLatest hrs)" } else { "OK" }
            })
        }

        return $results.ToArray()
    } catch {
        $status = 'Error'
        $errorMsg = $_.Exception.Message
        throw
    } finally {
        $stopwatch.Stop()
        Write-OpsLog -FunctionName 'Get-OpsBackupStatus' `
            -Parameters @{ SqlInstance = $SqlInstance; Database = ($Database -join ', '); NotBackedUpHours = $NotBackedUpHours } `
            -Status $status -DurationMs $stopwatch.ElapsedMilliseconds -ErrorMessage $errorMsg
    }
}
