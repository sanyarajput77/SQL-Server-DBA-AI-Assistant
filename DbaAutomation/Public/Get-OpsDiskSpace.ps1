<#
.SYNOPSIS
    Returns SQL-visible volume diagnostics for approved database file volumes.
.DESCRIPTION
    Uses sys.dm_os_volume_stats through a fixed query, avoiding remote Windows
    administrator/WMI access. Configured backup-path volume coverage is not
    assumed because the path may be on a separate or client-side volume.
.PARAMETER SqlInstance
    Target SQL Server instance or computer name. Must be in the approved allowlist. Defaults to config.
.PARAMETER WarningPercent
    Free-space percentage threshold below which volumes are flagged. Defaults to config (15%).
.EXAMPLE
    Get-OpsDiskSpace
.EXAMPLE
    Get-OpsDiskSpace -SqlInstance 'ConfiguredInstance' -WarningPercent 20
#>
function Get-OpsDiskSpace {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false, Position = 0)]
        [string]$SqlInstance,

        [Parameter(Mandatory = $false, Position = 1)]
        [ValidateRange(1, 100)]
        [double]$WarningPercent
    )

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $status = 'Success'
    $errorMsg = $null

    try {
        $SqlInstance = Assert-OpsApprovedInstance -SqlInstance $SqlInstance
        $config = Get-OpsConfig
        $Database = Get-OpsApprovedDatabaseTargets -Database $null -Config $config
        Initialize-OpsSession -Config $config

        if ($WarningPercent -le 0) {
            $WarningPercent = if ($config.Thresholds.DiskWarningPercentFree) { [double]$config.Thresholds.DiskWarningPercentFree } else { 15.0 }
        }

        # The allowlist is validated locally before opening a SQL connection.
        $sqlConnection = Connect-OpsSqlInstance -SqlInstance $SqlInstance -Config $config
        $Database = Assert-OpsSafeDatabase -SqlInstance $SqlInstance -Database $Database -Config $config -SqlConnection $sqlConnection

        $query = @'
SELECT
    DB_NAME() AS DatabaseName,
    vs.volume_mount_point AS VolumeMountPoint,
    vs.total_bytes AS TotalBytes,
    vs.available_bytes AS AvailableBytes,
    mf.physical_name AS PhysicalPath
FROM sys.database_files AS mf
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), mf.file_id) AS vs;
'@

        $results = [System.Collections.Generic.List[PSCustomObject]]::new()
        foreach ($databaseName in $Database) {
            $volumeRows = Invoke-DbaQuery -SqlInstance $sqlConnection -Database $databaseName -Query $query -EnableException
            foreach ($volume in $volumeRows) {
                $totalBytes = [double]$volume.TotalBytes
                $freeBytes = [double]$volume.AvailableBytes
                $pctFree = if ($totalBytes -gt 0) { [math]::Round(($freeBytes / $totalBytes) * 100, 2) } else { $null }
                $isLowSpace = $null -ne $pctFree -and $pctFree -lt $WarningPercent

                $results.Add([PSCustomObject]@{
                    SqlInstance                 = $SqlInstance
                    DatabaseName                = $volume.DatabaseName
                    Drive                       = $volume.VolumeMountPoint
                    VolumeLabel                 = $null
                    PhysicalPath                = $volume.PhysicalPath
                    CapacityGB                  = if ($totalBytes -gt 0) { [math]::Round($totalBytes / 1GB, 2) } else { $null }
                    FreeSpaceGB                 = [math]::Round($freeBytes / 1GB, 2)
                    PercentFree                 = $pctFree
                    ThresholdPercent            = $WarningPercent
                    IsLowSpace                  = $isLowSpace
                    ConfiguredBackupDirectory   = $config.BackupDirectory
                    BackupDirectoryCoverage     = 'Not verified; only volumes containing approved database files are reported.'
                    Status                      = if ($null -eq $pctFree) { 'Unknown: SQL Server did not return volume capacity.' } elseif ($isLowSpace) { "WARNING: Free space ($pctFree%) is below threshold ($WarningPercent%)" } else { 'OK' }
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
        Write-OpsLog -FunctionName 'Get-OpsDiskSpace' `
            -Parameters @{ SqlInstance = $SqlInstance; WarningPercent = $WarningPercent } `
            -Status $status -DurationMs $stopwatch.ElapsedMilliseconds -ErrorMessage $errorMsg
    }
}
