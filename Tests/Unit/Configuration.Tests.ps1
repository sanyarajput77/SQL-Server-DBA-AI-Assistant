BeforeAll {
    $projectRoot = Split-Path $PSScriptRoot -Parent | Split-Path -Parent
    $script:moduleManifest = Join-Path $projectRoot 'DbaAutomation/DbaAutomation.psd1'
    $script:configDirectory = Join-Path $projectRoot 'Config'
    Import-Module $script:moduleManifest -Force -ErrorAction Stop
}

Describe 'Get-OpsConfig schema and semantic validation' -Tag Unit, Configuration {
    It 'loads every supplied environment configuration through schema validation' {
        $files = Get-ChildItem -LiteralPath $script:configDirectory -File -Filter 'config.*.json*' |
            Where-Object { $_.Name -ne 'config.schema.json' }
        $files.Count | Should -Be 4

        foreach ($file in $files | Where-Object { $_.Name -ne 'config.production.json.example' }) {
            { Get-OpsConfig -ConfigPath $file.FullName } | Should -Not -Throw
        }
        $productionExample = Join-Path $script:configDirectory 'config.production.json.example'
        { Get-OpsConfig -ConfigPath $productionExample } | Should -Throw '*Production contains a Development/Lab or example placeholder*'
    }

    It 'requires the default instance to be present in the approved allowlist' {
        $config = Get-Content (Join-Path $script:configDirectory 'config.development.json') -Raw | ConvertFrom-Json
        $config.DefaultInstance = 'NOT-APPROVED'
        $path = Join-Path $TestDrive 'default-instance-not-approved.json'
        $config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $path

        { Get-OpsConfig -ConfigPath $path } | Should -Throw '*DefaultInstance*must be listed in ApprovedInstances*'
    }

    It 'rejects a threshold outside the supported range' {
        $config = Get-Content (Join-Path $script:configDirectory 'config.development.json') -Raw | ConvertFrom-Json
        $config.Thresholds.BackupWarningHours = 0
        $path = Join-Path $TestDrive 'invalid-threshold.json'
        $config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $path

        { Get-OpsConfig -ConfigPath $path } | Should -Throw '*does not satisfy the required schema*'
    }

    It 'rejects secret or unsupported configuration properties' {
        $config = Get-Content (Join-Path $script:configDirectory 'config.development.json') -Raw | ConvertFrom-Json
        $config | Add-Member -MemberType NoteProperty -Name 'SqlPassword' -Value 'must-not-be-stored'
        $path = Join-Path $TestDrive 'secret-property.json'
        $config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $path

        { Get-OpsConfig -ConfigPath $path } | Should -Throw '*does not satisfy the required schema*'
    }

    It 'rejects TrustServerCertificate outside Development and Lab' {
        $config = Get-Content (Join-Path $script:configDirectory 'config.test.json.example') -Raw | ConvertFrom-Json
        $config.TrustServerCertificate = $true
        $path = Join-Path $TestDrive 'trust-certificate-in-test.json'
        $config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $path

        { Get-OpsConfig -ConfigPath $path } | Should -Throw '*only be enabled for Development or Lab*'
    }

    It 'resolves relative output paths from the selected configuration file' {
        $config = Get-OpsConfig -ConfigPath (Join-Path $script:configDirectory 'config.development.json')

        $config.BackupDirectory | Should -Be ([System.IO.Path]::GetFullPath((Join-Path $script:configDirectory '../Backups')))
        $config.ReportDirectory | Should -Be ([System.IO.Path]::GetFullPath((Join-Path $script:configDirectory '../Reports')))
        $config.LogDirectory | Should -Be ([System.IO.Path]::GetFullPath((Join-Path $script:configDirectory '../Logs')))
    }

    It 'does not silently fall back when DBA_AUTOMATION_CONFIG points to a missing file' {
        $oldValue = $env:DBA_AUTOMATION_CONFIG
        try {
            $env:DBA_AUTOMATION_CONFIG = Join-Path $TestDrive 'does-not-exist.json'
            { Get-OpsConfig } | Should -Throw '*Configuration file is not selected or unavailable*'
        } finally {
            $env:DBA_AUTOMATION_CONFIG = $oldValue
        }
    }

    It 'requires an explicit configuration instead of selecting Development implicitly' {
        $oldValue = $env:DBA_AUTOMATION_CONFIG
        try {
            Remove-Item Env:DBA_AUTOMATION_CONFIG -ErrorAction SilentlyContinue
            { Get-OpsConfig } | Should -Throw '*Configuration file is not selected or unavailable*'
        } finally {
            $env:DBA_AUTOMATION_CONFIG = $oldValue
        }
    }

    It 'rejects a selected environment that differs from the configuration file' {
        $oldConfig = $env:DBA_AUTOMATION_CONFIG
        $oldEnvironment = $env:DBA_AUTOMATION_ENV
        try {
            $env:DBA_AUTOMATION_CONFIG = Join-Path $script:configDirectory 'config.development.json'
            $env:DBA_AUTOMATION_ENV = 'Production'
            { Get-OpsConfig } | Should -Throw '*does not match DBA_AUTOMATION_ENV*'
        } finally {
            $env:DBA_AUTOMATION_CONFIG = $oldConfig
            $env:DBA_AUTOMATION_ENV = $oldEnvironment
        }
    }

    It 'fails clearly for malformed JSON' {
        $path = Join-Path $TestDrive 'malformed.json'
        '{"Environment":' | Set-Content -LiteralPath $path

        { Get-OpsConfig -ConfigPath $path } | Should -Throw '*invalid JSON*'
    }
}

Describe 'Development certificate trust configuration' -Tag Unit, Configuration {
    It 'does not apply enabled Development trust policy to shared dbatools settings' {
        InModuleScope DbaAutomation {
            Mock Set-DbatoolsConfig {}
            $config = [pscustomobject]@{ TrustServerCertificate = $true; Environment = 'Development' }
            Initialize-OpsSession -Config $config

            Should -Invoke Set-DbatoolsConfig -Times 0 -Exactly
        }
    }

    It 'does not change dbatools settings when trust is disabled' {
        InModuleScope DbaAutomation {
            Mock Set-DbatoolsConfig {}
            $config = [pscustomobject]@{ TrustServerCertificate = $false; Environment = 'Production' }
            Initialize-OpsSession -Config $config

            Should -Invoke Set-DbatoolsConfig -Times 0 -Exactly
        }
    }
}

Describe 'Per-connection certificate trust' -Tag Unit, Configuration {
    It 'passes explicit Development trust settings to Connect-DbaInstance only' {
        InModuleScope DbaAutomation {
            Mock Assert-OpsApprovedInstance { 'MSSQL-VM1' }
            Mock Set-DbatoolsConfig {}
            Mock Connect-DbaInstance {
                [pscustomobject]@{
                    ServerName = $SqlInstance
                    TrustServerCertificate = $TrustServerCertificate
                    AllowTrustServerCertificate = $AllowTrustServerCertificate
                }
            }
            $config = [pscustomobject]@{ Environment='Development'; TrustServerCertificate=$true; ApprovedInstances=@('MSSQL-VM1') }

            $connection = Connect-OpsSqlInstance -SqlInstance MSSQL-VM1 -Config $config

            $connection.TrustServerCertificate | Should -BeTrue
            $connection.AllowTrustServerCertificate | Should -BeFalse
            Should -Invoke Connect-DbaInstance -Times 1 -Exactly -ParameterFilter {
                $TrustServerCertificate -eq $true -and $AllowTrustServerCertificate -eq $false
            }
            Should -Invoke Set-DbatoolsConfig -Times 0 -Exactly
        }
    }

    It 'explicitly disables certificate trust for Production connections' {
        InModuleScope DbaAutomation {
            Mock Assert-OpsApprovedInstance { 'PROD-SQL-01' }
            Mock Connect-DbaInstance {
                [pscustomobject]@{ TrustServerCertificate = $TrustServerCertificate }
            }
            $config = [pscustomobject]@{ Environment='Production'; TrustServerCertificate=$false; ApprovedInstances=@('PROD-SQL-01') }

            $connection = Connect-OpsSqlInstance -SqlInstance PROD-SQL-01 -Config $config

            $connection.TrustServerCertificate | Should -BeFalse
            Should -Invoke Connect-DbaInstance -Times 1 -Exactly -ParameterFilter {
                $TrustServerCertificate -eq $false -and $AllowTrustServerCertificate -eq $false
            }
        }
    }
}
