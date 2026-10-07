BeforeAll {
    $moduleManifest = Join-Path (Split-Path $PSScriptRoot -Parent | Split-Path -Parent) 'DbaAutomation/DbaAutomation.psd1'
    Import-Module $moduleManifest -Force -ErrorAction Stop
}

Describe 'DbaAutomation SQL Server read-only integration' -Tag Integration {
    It 'connects to the configured instance and returns database inventory' -Skip:($env:DBA_AUTOMATION_RUN_INTEGRATION -ne '1') {
        $result = Get-OpsDatabaseInventory
        $result | Should -Not -BeNullOrEmpty
        $result[0].SqlInstance | Should -Be (Get-OpsConfig).DefaultInstance
    }

    It 'connects to the configured instance and returns database health' -Skip:($env:DBA_AUTOMATION_RUN_INTEGRATION -ne '1') {
        $result = Get-OpsDatabaseHealth
        $result | Should -Not -BeNullOrEmpty
        $result[0].PSObject.Properties.Name | Should -Contain 'HealthStatus'
    }
}
