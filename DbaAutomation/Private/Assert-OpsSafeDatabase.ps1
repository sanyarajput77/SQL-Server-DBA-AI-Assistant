<#
.SYNOPSIS
    Validates database names against the allowlist and confirms they exist.
.DESCRIPTION
    Rejects system and non-approved databases before making any connection,
    then confirms that every approved target exists using a read-only lookup.
.PARAMETER SqlInstance
    Validated SQL Server instance.
.PARAMETER Database
    Database name(s) to validate.
.PARAMETER Config
    Validated configuration object.
.PARAMETER SqlConnection
    Optional already connected dbatools server object.
.OUTPUTS
    [string[]] Validated database names.
#>
function Get-OpsApprovedDatabaseTargets {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false)]
        [string[]]$Database,

        [Parameter(Mandatory = $true)]
        [PSCustomObject]$Config
    )

    $targets = if ($Database -and $Database.Count -gt 0) { @($Database) } else { @($Config.ApprovedDatabases) }
    if ($targets.Count -eq 0) {
        throw [System.InvalidOperationException]::new('Configuration error: ApprovedDatabases must contain at least one user database.')
    }

    $protectedList = @('master', 'model', 'msdb', 'tempdb') + @($Config.ProtectedDatabases)
    foreach ($target in $targets) {
        if ([string]::IsNullOrWhiteSpace($target)) {
            throw [System.ArgumentException]::new('Database names cannot be empty.')
        }

        if ($target.Trim() -in $protectedList) {
            throw [System.InvalidOperationException]::new("SAFETY VIOLATION: Operation on protected system database '$target' is strictly forbidden.")
        }

        $approved = @($Config.ApprovedDatabases | Where-Object { $_ -and $_.Trim().Equals($target.Trim(), [System.StringComparison]::OrdinalIgnoreCase) })
        if ($approved.Count -eq 0) {
            throw [System.Security.SecurityException]::new("SECURITY VIOLATION: Database '$target' is not in the configured ApprovedDatabases allowlist.")
        }
    }

    return ,@($targets | ForEach-Object { $_.Trim() } | Select-Object -Unique)
}

function Assert-OpsSafeDatabase {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$SqlInstance,

        [Parameter(Mandatory = $false)]
        [string[]]$Database,

        [Parameter(Mandatory = $false)]
        [PSCustomObject]$Config,

        [Parameter(Mandatory = $false)]
        [object]$SqlConnection
    )

    if (-not $Config) { $Config = Get-OpsConfig }
    $targets = Get-OpsApprovedDatabaseTargets -Database $Database -Config $Config

    # The allowlist and system database checks above are pure local validation.
    # No connection or dbatools command occurs until every requested name passes.
    if (-not $SqlConnection) {
        $SqlConnection = Connect-OpsSqlInstance -SqlInstance $SqlInstance -Config $Config
    }
    $existingDatabases = @(Get-DbaDatabase -SqlInstance $SqlConnection -Database $targets -EnableException:$false -ErrorAction SilentlyContinue)
    $existingNames = @($existingDatabases | ForEach-Object { [string]$_.Name })
    $missing = @($targets | Where-Object { $_ -notin $existingNames })

    if ($missing.Count -gt 0) {
        throw [System.Management.Automation.ItemNotFoundException]::new("VALIDATION ERROR: Database(s) '$($missing -join ', ')' do not exist or are not visible on instance '$SqlInstance'.")
    }

    return ,$targets
}
