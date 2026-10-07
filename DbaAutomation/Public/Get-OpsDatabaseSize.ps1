<#
.SYNOPSIS
    Returns database and file size, usage, and capacity information.
.DESCRIPTION
    Queries database space utilization, calculating data file size, log file size,
    used space, available space, and percentage free.
.PARAMETER SqlInstance
    Target SQL Server instance. Must be in the approved allowlist. Defaults to configuration default.
.PARAMETER Database
    Optional database name(s) to filter by.
.PARAMETER Detailed
    If specified, returns file-level breakdown instead of database summary.
.EXAMPLE
    Get-OpsDatabaseSize
.EXAMPLE
    Get-OpsDatabaseSize -SqlInstance 'ConfiguredInstance' -Database 'AdventureWorks2022'
#>
function Get-OpsDatabaseSize {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false, Position = 0)]
        [string]$SqlInstance,

        [Parameter(Mandatory = $false, Position = 1)]
        [string[]]$Database,

        [Parameter(Mandatory = $false)]
        [switch]$Detailed
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

        $cmdParams = @{
            SqlInstance     = $sqlConnection
            Database        = $Database
            EnableException = $true
        }

        $files = Get-DbaDbFile @cmdParams

        if ($Detailed) {
            $detailedResults = [System.Collections.Generic.List[PSCustomObject]]::new()
            foreach ($file in $files) {
                $sizeMb = if ($file.Size.Megabytes) { [math]::Round($file.Size.Megabytes, 2) } else { [math]::Round([double]$file.Size, 2) }
                $usedMb = if ($file.UsedSpace.Megabytes) { [math]::Round($file.UsedSpace.Megabytes, 2) } else { [math]::Round([double]$file.UsedSpace, 2) }
                $availMb = if ($file.AvailableSpace.Megabytes) { [math]::Round($file.AvailableSpace.Megabytes, 2) } else { [math]::Round([double]$file.AvailableSpace, 2) }
                $pctFree = if ($sizeMb -gt 0) { [math]::Round(($availMb / $sizeMb) * 100, 2) } else { 0 }

                $detailedResults.Add([PSCustomObject]@{
                    SqlInstance      = $SqlInstance
                    DatabaseName     = $file.Database
                    LogicalName      = $file.LogicalName
                    FileType         = $file.TypeDescription
                    PhysicalPath     = $file.PhysicalName
                    FileSizeMB       = $sizeMb
                    UsedSpaceMB      = $usedMb
                    AvailableSpaceMB = $availMb
                    PercentFree      = $pctFree
                })
            }
            return $detailedResults.ToArray()
        }

        # Aggregate per database
        $grouped = $files | Group-Object -Property Database
        $summaryResults = [System.Collections.Generic.List[PSCustomObject]]::new()

        foreach ($group in $grouped) {
            $dbName = $group.Name
            $dataFiles = $group.Group | Where-Object { $_.TypeDescription -eq 'ROWS' }
            $logFiles  = $group.Group | Where-Object { $_.TypeDescription -eq 'LOG' }

            $dataSizeMb = ($dataFiles | ForEach-Object { if ($_.Size.Megabytes) { $_.Size.Megabytes } else { [double]$_.Size } } | Measure-Object -Sum).Sum
            $dataUsedMb = ($dataFiles | ForEach-Object { if ($_.UsedSpace.Megabytes) { $_.UsedSpace.Megabytes } else { [double]$_.UsedSpace } } | Measure-Object -Sum).Sum
            $logSizeMb  = ($logFiles  | ForEach-Object { if ($_.Size.Megabytes) { $_.Size.Megabytes } else { [double]$_.Size } } | Measure-Object -Sum).Sum
            $logUsedMb  = ($logFiles  | ForEach-Object { if ($_.UsedSpace.Megabytes) { $_.UsedSpace.Megabytes } else { [double]$_.UsedSpace } } | Measure-Object -Sum).Sum

            $dataSizeMb = if ($dataSizeMb) { [math]::Round($dataSizeMb, 2) } else { 0 }
            $dataUsedMb = if ($dataUsedMb) { [math]::Round($dataUsedMb, 2) } else { 0 }
            $logSizeMb  = if ($logSizeMb) { [math]::Round($logSizeMb, 2) } else { 0 }
            $logUsedMb  = if ($logUsedMb) { [math]::Round($logUsedMb, 2) } else { 0 }

            $totalSizeMb = [math]::Round($dataSizeMb + $logSizeMb, 2)
            $totalUsedMb = [math]::Round($dataUsedMb + $logUsedMb, 2)
            $totalFreeMb = [math]::Round($totalSizeMb - $totalUsedMb, 2)
            $pctFree = if ($totalSizeMb -gt 0) { [math]::Round(($totalFreeMb / $totalSizeMb) * 100, 2) } else { 0 }

            $summaryResults.Add([PSCustomObject]@{
                SqlInstance   = $SqlInstance
                DatabaseName  = $dbName
                TotalSizeMB   = $totalSizeMb
                DataSizeMB    = $dataSizeMb
                DataUsedMB    = $dataUsedMb
                LogSizeMB     = $logSizeMb
                LogUsedMB     = $logUsedMb
                FreeSpaceMB   = $totalFreeMb
                PercentFree   = $pctFree
                FileCount     = $group.Group.Count
            })
        }

        return $summaryResults.ToArray()
    } catch {
        $status = 'Error'
        $errorMsg = $_.Exception.Message
        throw
    } finally {
        $stopwatch.Stop()
        Write-OpsLog -FunctionName 'Get-OpsDatabaseSize' `
            -Parameters @{ SqlInstance = $SqlInstance; Database = ($Database -join ', '); Detailed = [bool]$Detailed } `
            -Status $status -DurationMs $stopwatch.ElapsedMilliseconds -ErrorMessage $errorMsg
    }
}
