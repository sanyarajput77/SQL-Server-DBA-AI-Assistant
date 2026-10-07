# DbaAutomation Root Module
# Dot-source Private helper functions
$privateScripts = Get-ChildItem -Path (Join-Path -Path $PSScriptRoot -ChildPath 'Private\*.ps1') -ErrorAction SilentlyContinue
foreach ($script in $privateScripts) {
    . $script.FullName
}

# Dot-source Public capability functions
$publicScripts = Get-ChildItem -Path (Join-Path -Path $PSScriptRoot -ChildPath 'Public\*.ps1') -ErrorAction SilentlyContinue
foreach ($script in $publicScripts) {
    . $script.FullName
}

# Export approved public functions
Export-ModuleMember -Function @(
    'Get-OpsDatabaseInventory',
    'Get-OpsDatabaseHealth',
    'Get-OpsDatabaseSize',
    'Get-OpsBackupStatus',
    'Get-OpsDiskSpace',
    'Get-OpsFailedJobs',
    'Get-OpsBlockingSessions',
    'Get-OpsLongRunningQueries',
    'Get-OpsServerHealthReport',
    'Backup-OpsDatabase',
    'Get-OpsConfig'
)
