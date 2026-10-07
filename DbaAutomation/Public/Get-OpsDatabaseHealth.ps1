<#
.SYNOPSIS
    Returns comprehensive read-only database health diagnostics.
.DESCRIPTION
    Evaluates database operational health, accessibility, page verification mode,
    anti-pattern settings (AutoClose, AutoShrink), and DBCC CHECKDB integrity status.
.PARAMETER SqlInstance
    Target SQL Server instance. Must be in the approved allowlist. Defaults to configuration default.
.PARAMETER Database
    Optional database name(s) to filter by.
.EXAMPLE
    Get-OpsDatabaseHealth
.EXAMPLE
    Get-OpsDatabaseHealth -SqlInstance 'ConfiguredInstance' -Database 'AdventureWorks2022'
#>
function Get-OpsDatabaseHealth {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false, Position = 0)]
        [string]$SqlInstance,

        [Parameter(Mandatory = $false, Position = 1)]
        [string[]]$Database
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

        $dbParams = @{
            SqlInstance     = $sqlConnection
            Database        = $Database
            EnableException = $true
        }

        $databases = Get-DbaDatabase @dbParams

        # Fetch CheckDB integrity records
        $checkDbRecords = @{}
        try {
            $checkDbData = Get-DbaLastGoodCheckDb @dbParams -ErrorAction SilentlyContinue
            foreach ($item in $checkDbData) {
                $checkDbRecords[$item.Database] = $item
            }
        } catch {
            Write-Verbose "Could not query Get-DbaLastGoodCheckDb: $($_.Exception.Message)"
        }

        $results = [System.Collections.Generic.List[PSCustomObject]]::new()

        foreach ($db in $databases) {
            $checkDb = $checkDbRecords[$db.Name]
            $lastCheckDb = if ($checkDb) { $checkDb.LastGoodCheckDb } else { $null }
            $daysSinceCheckDb = if ($checkDb -and $checkDb.DaysSinceLastGoodCheckDb) { [math]::Round([double]$checkDb.DaysSinceLastGoodCheckDb, 1) } else { $null }

            # Health classification
            $healthIssues = [System.Collections.Generic.List[string]]::new()
            $healthStatus = 'Healthy'

            if ($db.Status -ne 'Normal') {
                $healthStatus = 'Critical'
                $healthIssues.Add("Status is '$($db.Status)'")
            }

            if (-not $db.IsAccessible) {
                $healthStatus = 'Critical'
                $healthIssues.Add("Database is not accessible")
            }

            if ($db.PageVerify -ne 'Checksum') {
                if ($healthStatus -ne 'Critical') { $healthStatus = 'Warning' }
                $healthIssues.Add("PageVerify is '$($db.PageVerify)' (Checksum recommended)")
            }

            if ($db.AutoClose) {
                if ($healthStatus -ne 'Critical') { $healthStatus = 'Warning' }
                $healthIssues.Add("AutoClose is ON (anti-pattern)")
            }

            if ($db.AutoShrink) {
                if ($healthStatus -ne 'Critical') { $healthStatus = 'Warning' }
                $healthIssues.Add("AutoShrink is ON (anti-pattern)")
            }

            if (-not $lastCheckDb) {
                if ($healthStatus -ne 'Critical') { $healthStatus = 'Warning' }
                $healthIssues.Add("No recorded LastGoodCheckDB found")
            } elseif ($daysSinceCheckDb -gt 7) {
                if ($healthStatus -ne 'Critical') { $healthStatus = 'Warning' }
                $healthIssues.Add("LastGoodCheckDB is $daysSinceCheckDb days old (> 7 days)")
            }

            $healthSummary = if ($healthIssues.Count -gt 0) { $healthIssues -join '; ' } else { 'All health checks passed' }

            $results.Add([PSCustomObject]@{
                SqlInstance              = $SqlInstance
                DatabaseName             = $db.Name
                HealthStatus             = $healthStatus
                DatabaseStatus           = $db.Status.ToString()
                IsAccessible             = $db.IsAccessible
                ReadOnly                 = $db.ReadOnly
                RecoveryModel            = $db.RecoveryModel.ToString()
                PageVerify               = $db.PageVerify.ToString()
                AutoClose                = [bool]$db.AutoClose
                AutoShrink               = [bool]$db.AutoShrink
                LastGoodCheckDb          = $lastCheckDb
                DaysSinceLastGoodCheckDb = $daysSinceCheckDb
                Notes                    = $healthSummary
            })
        }

        return $results.ToArray()
    } catch {
        $status = 'Error'
        $errorMsg = $_.Exception.Message
        throw
    } finally {
        $stopwatch.Stop()
        Write-OpsLog -FunctionName 'Get-OpsDatabaseHealth' `
            -Parameters @{ SqlInstance = $SqlInstance; Database = ($Database -join ', ') } `
            -Status $status -DurationMs $stopwatch.ElapsedMilliseconds -ErrorMessage $errorMsg
    }
}
