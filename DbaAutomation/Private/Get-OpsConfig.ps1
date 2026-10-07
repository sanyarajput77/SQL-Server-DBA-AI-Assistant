<#
.SYNOPSIS
    Loads and returns the DBA automation configuration.
.DESCRIPTION
    Loads and validates non-secret JSON configuration using the module's JSON schema.
    Relative output paths are resolved from the selected configuration file.
.PARAMETER ConfigPath
    Optional path to configuration JSON file.
.OUTPUTS
    [PSCustomObject] Configuration settings.
#>
function Get-OpsConfig {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false)]
        [string]$ConfigPath
    )

    # Require an explicitly selected environment file; never infer Development.
    if (-not $ConfigPath) {
        if (-not [string]::IsNullOrWhiteSpace($env:DBA_AUTOMATION_CONFIG)) {
            $ConfigPath = $env:DBA_AUTOMATION_CONFIG
        }
    }

    if ([string]::IsNullOrWhiteSpace($ConfigPath) -or -not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        throw [System.IO.FileNotFoundException]::new("Configuration file is not selected or unavailable. Set DBA_AUTOMATION_CONFIG to an explicit environment JSON file or provide -ConfigPath.")
    }

    $resolvedConfigPath = (Resolve-Path -LiteralPath $ConfigPath -ErrorAction Stop).Path
    $schemaPath = Join-Path -Path $PSScriptRoot -ChildPath '..\..\Config\config.schema.json'
    if (-not (Test-Path -LiteralPath $schemaPath -PathType Leaf)) {
        throw [System.IO.FileNotFoundException]::new("Configuration schema not found: '$schemaPath'.")
    }

    try {
        $schemaIsValid = Test-Json -LiteralPath $resolvedConfigPath -SchemaFile $schemaPath -ErrorAction Stop
    } catch {
        if ($_.Exception.Message -match '(?i)not valid with the schema') {
            throw [System.InvalidOperationException]::new("Configuration '$resolvedConfigPath' does not satisfy the required schema in '$schemaPath': $($_.Exception.Message)")
        }
        throw [System.InvalidOperationException]::new("Configuration '$resolvedConfigPath' is invalid JSON or could not be checked against the schema: $($_.Exception.Message)")
    }
    if (-not $schemaIsValid) {
        throw [System.InvalidOperationException]::new("Configuration '$resolvedConfigPath' does not satisfy the required schema in '$schemaPath'. Check required fields, types, threshold ranges, and unsupported properties (including secret fields).")
    }

    $config = Get-Content -LiteralPath $resolvedConfigPath -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop

    if (-not [string]::IsNullOrWhiteSpace($env:DBA_AUTOMATION_ENV) -and $env:DBA_AUTOMATION_ENV -cne $config.Environment) {
        throw [System.InvalidOperationException]::new("Configuration environment does not match DBA_AUTOMATION_ENV.")
    }

    if ($config.DefaultInstance -notin $config.ApprovedInstances) {
        throw [System.InvalidOperationException]::new("Configuration invalid: DefaultInstance '$($config.DefaultInstance)' must be listed in ApprovedInstances.")
    }

    $requiredProtectedDatabases = @('master', 'model', 'msdb', 'tempdb')
    $missingProtectedDatabases = @($requiredProtectedDatabases | Where-Object { $_ -notin $config.ProtectedDatabases })
    if ($missingProtectedDatabases.Count -gt 0) {
        throw [System.InvalidOperationException]::new("Configuration invalid: ProtectedDatabases must include all system databases: $($requiredProtectedDatabases -join ', ').")
    }

    if ($config.TrustServerCertificate -and $config.Environment -notin @('Development', 'Lab')) {
        throw [System.InvalidOperationException]::new("Configuration invalid: TrustServerCertificate can only be enabled for Development or Lab environments.")
    }

    if ($config.BackupPolicy.ApprovalRequired -ne $true) {
        throw [System.InvalidOperationException]::new("Configuration invalid: BackupPolicy.ApprovalRequired must remain true.")
    }
    if ($config.BackupPolicy.Enabled -and $config.BackupPolicy.AllowedTypes.Count -eq 0) {
        throw [System.InvalidOperationException]::new("Configuration invalid: an enabled BackupPolicy must allow at least one fixed backup type.")
    }

    $configDirectory = Split-Path -Parent $resolvedConfigPath
    foreach ($pathProperty in @('BackupDirectory', 'ReportDirectory', 'LogDirectory')) {
        $configuredPath = [string]$config.$pathProperty
        if ($configuredPath -match '[\x00-\x1F<>"|?*]' -or $configuredPath -match '^[A-Za-z][A-Za-z0-9+.-]*://') {
            throw [System.InvalidOperationException]::new("Configuration invalid: '$pathProperty' contains unsupported path characters.")
        }
        if ($config.Environment -eq 'Production' -and -not [System.IO.Path]::IsPathRooted($configuredPath)) {
            throw [System.InvalidOperationException]::new("Configuration invalid: Production requires an absolute '$pathProperty'.")
        }
        try {
            if ([System.IO.Path]::IsPathRooted($configuredPath)) {
                $absolutePath = [System.IO.Path]::GetFullPath($configuredPath)
            } else {
                $absolutePath = [System.IO.Path]::GetFullPath((Join-Path -Path $configDirectory -ChildPath $configuredPath))
            }
        } catch {
            throw [System.InvalidOperationException]::new("Configuration invalid: '$pathProperty' is not a valid filesystem path: $($_.Exception.Message)")
        }
        $config.$pathProperty = $absolutePath
    }

    if ($config.Environment -eq 'Production') {
        $productionValues = @($config.ApprovedInstances) + @($config.DefaultInstance) + @($config.ApprovedDatabases) + @($config.BackupDirectory, $config.ReportDirectory, $config.LogDirectory)
        $placeholderPattern = '(?i)replace[-_ ]?with|example\.(com|org|net|invalid)|domain\.local|mssql-vm1|localhost|(^|[\\/])desktop([\\/]|$)|^\.$|^PROD-SQL-(\d+|AOAG)$|AppProductionDB|prod-backup-share'
        if ($config.TrustServerCertificate -or @($productionValues | Where-Object { [string]$_ -match $placeholderPattern }).Count -gt 0) {
            throw [System.InvalidOperationException]::new("Configuration invalid: Production contains a Development/Lab or example placeholder value.")
        }
    }

    return $config
}
