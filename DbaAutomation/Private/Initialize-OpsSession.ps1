<#
.SYNOPSIS
    Initializes the DBA automation session without changing shared dbatools settings.
.DESCRIPTION
    TrustServerCertificate remains an environment policy value validated by Get-OpsConfig.
    It is deliberately not applied through Set-DbatoolsConfig because dbatools settings
    are shared by later calls in the current PowerShell process. No global or persistent
    connection settings are changed here.
#>
function Initialize-OpsSession {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false)]
        [PSCustomObject]$Config
    )

    if (-not $Config) {
        $Config = Get-OpsConfig
    }

    # Intentionally no shared dbatools configuration mutation. Connection-specific
    # certificate trust must be handled by a future per-connection implementation.
}
