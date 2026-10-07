<#
.SYNOPSIS
    Returns inventory and metadata for SQL Server databases.
.DESCRIPTION
    Retrieves read-only database metadata including name, state, recovery model,
    compatibility level, creation date, size, and collation.
.PARAMETER SqlInstance
    Target SQL Server instance. Must be in the approved allowlist. Defaults to configuration default.
.PARAMETER Database
    Optional database name(s) to filter by.
.EXAMPLE
    Get-OpsDatabaseInventory
.EXAMPLE
    Get-OpsDatabaseInventory -SqlInstance 'ConfiguredInstance' -Database 'AdventureWorks2022'
#>
function Get-OpsDatabaseInventory {
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

        $params = @{
            SqlInstance     = $sqlConnection
            Database        = $Database
            EnableException = $true
        }

        $databases = Get-DbaDatabase @params

        $results = [System.Collections.Generic.List[PSCustomObject]]::new()
        foreach ($db in $databases) {
            $results.Add([PSCustomObject]@{
                SqlInstance        = $SqlInstance
                DatabaseName       = $db.Name
                Status             = $db.Status.ToString()
                RecoveryModel      = $db.RecoveryModel.ToString()
                CompatibilityLevel = $db.CompatibilityLevel.ToString()
                CreateDate         = $db.CreateDate
                Collation          = $db.Collation
                Owner              = $db.Owner
                IsAccessible       = $db.IsAccessible
                SizeMB             = [math]::Round($db.Size, 2)
            })
        }

        return $results.ToArray()
    } catch {
        $status = 'Error'
        $errorMsg = $_.Exception.Message
        throw
    } finally {
        $stopwatch.Stop()
        Write-OpsLog -FunctionName 'Get-OpsDatabaseInventory' `
            -Parameters @{ SqlInstance = $SqlInstance; Database = ($Database -join ', ') } `
            -Status $status -DurationMs $stopwatch.ElapsedMilliseconds -ErrorMessage $errorMsg
    }
}
