@{
    # Script module or binary module file associated with this manifest.
    RootModule = 'DbaAutomation.psm1'

    # Version number of this module.
    ModuleVersion = '1.0.0'

    # Supported PSEditions
    CompatiblePSEditions = @('Core', 'Desktop')

    # ID used to uniquely identify this module
    GUID = 'a47b458b-9861-419b-8255-7d9cf4bb4e31'

    # Author of this module
    Author = 'DBA Automation Team'

    # Company or vendor of this module
    CompanyName = 'DBA Operations'

    # Description of the functionality provided by this module
    Description = 'Safe, portable, least-privilege SQL Server DBA automation and diagnostics module.'

    # Minimum version of the PowerShell engine required by this module
    PowerShellVersion = '7.0'

    # Modules that must be imported into the global environment prior to importing this module
    RequiredModules = @(
        @{ ModuleName = 'dbatools'; ModuleVersion = '2.0.0' }
    )

    # Functions to export from this module
    FunctionsToExport = @(
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

    # Cmdlets to export from this module
    CmdletsToExport = @()

    # Variables to export from this module
    VariablesToExport = @()

    # Aliases to export from this module
    AliasesToExport = @()

    # Private data to pass to the module specified in RootModule
    PrivateData = @{
        PSData = @{
            Tags = @('SQLServer', 'DBA', 'dbatools', 'Automation')
            ProjectUri = 'https://github.com/example/DBA-AI-Assistant'
        }
    }
}
