<#
.SYNOPSIS
    Validates local DBA automation prerequisites and project configuration.
.DESCRIPTION
    Checks installed PowerShell modules, Python virtual environment, MCP SDK,
    and environment configuration files. Creates missing project directories
    only through ShouldProcess. Runs unit/configuration tests only; it never
    connects to SQL Server or installs software.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Low')]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$startedAt = [datetime]::UtcNow
$checks = [System.Collections.Generic.List[object]]::new()

function Add-SetupCheck {
    param([string]$Name, [bool]$Passed, [string]$Detail)
    $checks.Add([pscustomobject]@{ Name = $Name; Passed = $Passed; Detail = $Detail })
}

function Get-InstalledModuleVersion {
    param([Parameter(Mandatory)][string]$Name)
    $module = Get-Module -ListAvailable -Name $Name |
        Sort-Object Version -Descending |
        Select-Object -First 1
    if ($null -eq $module) { return $null }
    return $module.Version
}

$requiredDirectories = @('Config', 'DbaAutomation', 'Logs', 'Reports', 'Backups', 'Tests', 'mcp-server', 'docs')
foreach ($relativeDirectory in $requiredDirectories) {
    $directory = Join-Path $PSScriptRoot $relativeDirectory
    if (Test-Path -LiteralPath $directory -PathType Container) {
        Add-SetupCheck "Directory: $relativeDirectory" $true 'Already exists.'
    } elseif ($PSCmdlet.ShouldProcess($directory, 'Create missing project directory')) {
        try {
            New-Item -Path $directory -ItemType Directory -Force -ErrorAction Stop | Out-Null
            Add-SetupCheck "Directory: $relativeDirectory" $true 'Created.'
        } catch {
            Add-SetupCheck "Directory: $relativeDirectory" $false $_.Exception.Message
        }
    } else {
        Add-SetupCheck "Directory: $relativeDirectory" $true 'Missing; creation was not performed (WhatIf or declined).'
    }
}

$powerShellVersion = $PSVersionTable.PSVersion
Add-SetupCheck 'PowerShell 7' ($powerShellVersion.Major -ge 7) "Detected $powerShellVersion."

foreach ($moduleName in @('dbatools', 'SqlServer', 'Pester')) {
    $version = Get-InstalledModuleVersion -Name $moduleName
    $minimumVersion = switch ($moduleName) {
        'dbatools' { [version]'2.8.4' }
        'SqlServer' { [version]'1.0.0' }
        'Pester' { [version]'5.7.1' }
    }
    $modulePassed = $null -ne $version -and $version -ge $minimumVersion
    $detail = if ($null -eq $version) { 'Not installed.' } else { "Detected $version; minimum required is $minimumVersion." }
    Add-SetupCheck "$moduleName module" $modulePassed $detail
}

$systemPython = Get-Command python -ErrorAction SilentlyContinue
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$venvExists = Test-Path -LiteralPath $venvPython -PathType Leaf
Add-SetupCheck 'Python command' ($null -ne $systemPython) $(if ($systemPython) { $systemPython.Source } else { 'python was not found on PATH.' })
Add-SetupCheck '.venv Python' $venvExists $(if ($venvExists) { $venvPython } else { 'Expected .venv\Scripts\python.exe was not found.' })

$venvPythonVersion = 'Unavailable'
if ($venvExists) {
    try {
        $venvPythonVersion = (& $venvPython --version 2>&1 | Out-String).Trim()
        $mcpProbe = & $venvPython -c "import importlib.util, importlib.metadata; found = importlib.util.find_spec('mcp') is not None; print(('present:' + importlib.metadata.version('mcp')) if found else 'missing')" 2>&1
        if ($LASTEXITCODE -eq 0 -and "$mcpProbe".Trim() -match '^present:(.+)$') {
            Add-SetupCheck 'MCP Python SDK' $true "Available in project .venv (version $($Matches[1]))."
        } else {
            Add-SetupCheck 'MCP Python SDK' $false "Not importable in project .venv: $($mcpProbe -join ' ')"
        }
    } catch {
        Add-SetupCheck 'MCP Python SDK' $false $_.Exception.Message
    }
}
$venvVersionMatch = [regex]::Match($venvPythonVersion, '^Python (\d+\.\d+)')
$venvVersionPassed = $venvExists -and $venvVersionMatch.Success -and [version]$venvVersionMatch.Groups[1].Value -ge [version]'3.14'
Add-SetupCheck 'Python in .venv' $venvVersionPassed "$venvPythonVersion (minimum required: 3.14)."

$moduleManifest = Join-Path $PSScriptRoot 'DbaAutomation\DbaAutomation.psd1'
$configFiles = @(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'Config') -File -Filter 'config.*.json*' -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne 'config.schema.json' })
$configPassed = $true
$configDetail = [System.Collections.Generic.List[string]]::new()
if (Test-Path -LiteralPath $moduleManifest -PathType Leaf) {
    try {
        Import-Module $moduleManifest -Force -ErrorAction Stop
        foreach ($file in $configFiles) {
            try {
                if ($file.Name -eq 'config.production.json.example') {
                    $schemaPath = Join-Path $PSScriptRoot 'Config\config.schema.json'
                    $schemaValid = Test-Json -LiteralPath $file.FullName -SchemaFile $schemaPath -ErrorAction Stop
                    if (-not $schemaValid) {
                        throw 'Production template does not match the configuration schema.'
                    }
                    $configDetail.Add("$($file.Name): schema valid; template placeholders are intentionally not deployable")
                } else {
                    Get-OpsConfig -ConfigPath $file.FullName -ErrorAction Stop | Out-Null
                    $configDetail.Add("$($file.Name): valid")
                }
            } catch {
                $configPassed = $false
                $configDetail.Add("$($file.Name): $($_.Exception.Message)")
            }
        }
        if ($configFiles.Count -eq 0) {
            $configPassed = $false
            $configDetail.Add('No environment JSON configuration files found.')
        }
    } catch {
        $configPassed = $false
        $configDetail.Add($_.Exception.Message)
    }
} else {
    $configPassed = $false
    $configDetail.Add('Module manifest is missing.')
}
Add-SetupCheck 'Environment configuration' $configPassed ($configDetail -join '; ')

if (-not $SkipTests) {
    $pesterVersion = Get-InstalledModuleVersion -Name Pester
    if ($null -ne $pesterVersion -and $pesterVersion -ge [version]'5.7.1') {
        try {
            $pesterConfiguration = New-PesterConfiguration
            $pesterConfiguration.Run.Path = Join-Path $PSScriptRoot 'Tests\Unit'
            $pesterConfiguration.Run.PassThru = $true
            $pesterConfiguration.TestRegistry.Enabled = $false
            # -WhatIf applies to missing-directory creation only. Pester writes
            # its fixtures inside TestDrive and must not inherit that preference.
            $savedWhatIfPreference = $WhatIfPreference
            try {
                $WhatIfPreference = $false
                $testResult = Invoke-Pester -Configuration $pesterConfiguration
            } finally {
                $WhatIfPreference = $savedWhatIfPreference
            }
            $testPassed = $testResult.FailedCount -eq 0 -and $testResult.SkippedCount -eq 0
            $testDetail = "Total $($testResult.TotalCount); passed $($testResult.PassedCount); failed $($testResult.FailedCount); skipped $($testResult.SkippedCount)."
            Add-SetupCheck 'Unit/configuration tests' $testPassed $testDetail
        } catch {
            Add-SetupCheck 'Unit/configuration tests' $false $_.Exception.Message
        }
    } else {
        Add-SetupCheck 'Unit/configuration tests' $false 'Pester 5.7.1 or later is required.'
    }
}

$failedChecks = @($checks | Where-Object { -not $_.Passed })
$summary = [pscustomobject]@{
    Status = if ($failedChecks.Count -eq 0) { 'Passed' } else { 'Failed' }
    Passed = @($checks | Where-Object Passed).Count
    Failed = $failedChecks.Count
    DurationMs = [math]::Round(([datetime]::UtcNow - $startedAt).TotalMilliseconds)
    Checks = @($checks)
}
$summary
if ($failedChecks.Count -gt 0) {
    throw "Setup validation failed: $($failedChecks.Name -join ', '). No SQL Server connection or system configuration changes were made."
}
