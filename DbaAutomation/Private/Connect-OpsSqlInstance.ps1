<#
.SYNOPSIS
    Creates an allowlisted dbatools SQL connection with environment-scoped TLS policy.
.DESCRIPTION
    Uses Connect-DbaInstance and returns its connected server object for downstream
    dbatools calls. Certificate trust is passed explicitly for this connection and
    never written to dbatools shared configuration.
.PARAMETER SqlInstance
    SQL Server instance name; must appear in the configured allowlist.
.PARAMETER Config
    Validated non-secret automation configuration.
#>
function Connect-OpsSqlInstance {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$SqlInstance,

        [Parameter(Mandatory = $false)]
        [PSCustomObject]$Config
    )

    if (-not $Config) {
        $Config = Get-OpsConfig
    }

    $approvedInstance = Assert-OpsApprovedInstance -SqlInstance $SqlInstance
    $trustCertificate = [bool]($Config.TrustServerCertificate -and $Config.Environment -in @('Development', 'Lab'))

    $connectParams = @{
        SqlInstance                 = $approvedInstance
        TrustServerCertificate     = $trustCertificate
        AllowTrustServerCertificate = $false
        ErrorAction                = 'Stop'
    }

    Connect-DbaInstance @connectParams
}
