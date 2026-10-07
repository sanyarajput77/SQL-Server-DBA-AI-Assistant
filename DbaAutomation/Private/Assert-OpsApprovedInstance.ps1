<#
.SYNOPSIS
    Validates a SQL Server instance against the configured allowlist.
.DESCRIPTION
    Mandatory safety check (Rule 8). Aborts immediately if the instance is not approved.
.PARAMETER SqlInstance
    The instance name to validate.
.OUTPUTS
    [string] The validated instance name.
#>
function Assert-OpsApprovedInstance {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false)]
        [string]$SqlInstance
    )

    $config = Get-OpsConfig

    if ([string]::IsNullOrWhiteSpace($SqlInstance)) {
        $SqlInstance = $config.DefaultInstance
    }

    $isApproved = $false
    foreach ($approved in $config.ApprovedInstances) {
        if ($SqlInstance.Trim() -eq $approved.Trim() -or
            $SqlInstance.Trim().ToLowerInvariant() -eq $approved.Trim().ToLowerInvariant()) {
            $isApproved = $true
            break
        }
    }

    if (-not $isApproved) {
        $msg = "SECURITY VIOLATION: SQL Server instance '$SqlInstance' is not in the approved configuration allowlist. Approved: ($($config.ApprovedInstances -join ', '))"
        throw [System.Security.SecurityException]::new($msg)
    }

    return $SqlInstance
}
