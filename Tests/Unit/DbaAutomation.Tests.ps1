BeforeAll {
    $projectRoot = Split-Path $PSScriptRoot -Parent | Split-Path -Parent

    # Unit tests explicitly use the Development configuration.
    # Runtime code must never infer an environment configuration.
    $env:DBA_AUTOMATION_CONFIG = Join-Path $projectRoot 'Config/config.development.json'
    $env:DBA_AUTOMATION_ENV = 'Development'

    $moduleManifest = Join-Path $projectRoot 'DbaAutomation/DbaAutomation.psd1'
    Import-Module $moduleManifest -Force -ErrorAction Stop
}

Describe 'DbaAutomation public functions' -Tag Unit {
    BeforeEach {
        InModuleScope DbaAutomation {
            Mock Assert-OpsApprovedInstance { 'MSSQL-VM1' }
            Mock Connect-OpsSqlInstance { $SqlInstance }
            Mock Get-OpsConfig {
                [pscustomobject]@{
                    Environment = 'Development'
                    DefaultInstance = 'MSSQL-VM1'
                    ApprovedInstances = @('MSSQL-VM1')
                    ApprovedDatabases = @('LabDb')
                    ProtectedDatabases = @('master', 'model', 'msdb', 'tempdb')
                    BackupDirectory = Join-Path $TestDrive 'backup-destination'
                    ReportDirectory = $TestDrive
                    LogDirectory = $TestDrive
                    TrustServerCertificate = $false
                    BackupPolicy = [pscustomobject]@{ Enabled = $true; AllowedTypes = @('Full', 'Differential', 'Log'); ApprovalRequired = $true }
                    Thresholds = [pscustomobject]@{
                        BackupWarningHours = 24
                        DiskWarningPercentFree = 15
                        LongRunningQuerySeconds = 60
                        FailedJobsHours = 24
                    }
                }
            }
            Mock Initialize-OpsSession {}
            Mock Write-OpsLog {}
            Mock Get-DbaDatabase {
                foreach ($databaseName in $Database) { [pscustomobject]@{ Name = $databaseName } }
            }
        }
    }

    It 'Get-OpsDatabaseInventory returns structured inventory rows' {
        InModuleScope DbaAutomation {
            Mock Get-DbaDatabase {
                [pscustomobject]@{
                    Name = 'LabDb'; Status = 'Normal'; RecoveryModel = 'Simple'
                    CompatibilityLevel = 160; CreateDate = [datetime]'2024-01-01'
                    Collation = 'Latin1_General_100_CI_AS_SC'; Owner = 'sa'
                    IsAccessible = $true; Size = 12.345
                }
            }
            $result = Get-OpsDatabaseInventory -SqlInstance MSSQL-VM1
            $result.DatabaseName | Should -Be 'LabDb'
            $result.SizeMB | Should -Be 12.34
            $result | Should -BeOfType [pscustomobject]
            Should -Invoke Get-DbaDatabase -Times 2 -Exactly -ParameterFilter { $Database -contains 'LabDb' }
        }
    }

    It 'Get-OpsDatabaseHealth returns structured health rows' {
        InModuleScope DbaAutomation {
            Mock Get-DbaDatabase {
                [pscustomobject]@{
                    Name = 'LabDb'; Status = 'Normal'; IsAccessible = $true; ReadOnly = $false
                    RecoveryModel = 'Simple'; PageVerify = 'Checksum'; AutoClose = $false; AutoShrink = $false
                }
            }
            Mock Get-DbaLastGoodCheckDb { @() }
            $result = Get-OpsDatabaseHealth -SqlInstance MSSQL-VM1
            $result.DatabaseName | Should -Be 'LabDb'
            $result.HealthStatus | Should -Be 'Warning'
        }
    }

    It 'Get-OpsDatabaseSize returns per-database size summaries' {
        InModuleScope DbaAutomation {
            Mock Get-DbaDbFile {
                @(
                    [pscustomobject]@{ Database='LabDb'; TypeDescription='ROWS'; Size=100; UsedSpace=70 },
                    [pscustomobject]@{ Database='LabDb'; TypeDescription='LOG'; Size=50; UsedSpace=20 }
                )
            }
            $result = Get-OpsDatabaseSize -SqlInstance MSSQL-VM1
            $result.TotalSizeMB | Should -Be 150
            $result.FreeSpaceMB | Should -Be 60
        }
    }

    It 'Get-OpsBackupStatus evaluates backup recency' {
        InModuleScope DbaAutomation {
            Mock Get-DbaLastBackup {
                [pscustomobject]@{
                    Database='LabDb'; RecoveryModel='Full'; LastFullBackup=(Get-Date).AddHours(-2)
                    LastDiffBackup=$null; LastLogBackup=$null; SinceFull=[timespan]::FromHours(2)
                    SinceDiff=$null; SinceLog=$null
                }
            }
            $result = Get-OpsBackupStatus -SqlInstance MSSQL-VM1
            $result.DatabaseName | Should -Be 'LabDb'
            $result.ExceedsThreshold | Should -BeFalse
        }
    }

    It 'Get-OpsDiskSpace returns disk capacity diagnostics' {
        InModuleScope DbaAutomation {
            Mock Invoke-DbaQuery {
                [pscustomobject]@{
                    DatabaseName='LabDb'; VolumeMountPoint='C:\'; TotalBytes=107374182400L
                    AvailableBytes=53687091200L; PhysicalPath='C:\SQLData\LabDb.mdf'
                }
            }
            Mock Get-DbaDiskSpace { throw 'Windows administrator/WMI disk lookup must not be used.' }
            $result = Get-OpsDiskSpace -SqlInstance MSSQL-VM1
            $result.Drive | Should -Be 'C:\'
            $result.CapacityGB | Should -Be 100
            $result.IsLowSpace | Should -BeFalse
            Should -Invoke Get-DbaDiskSpace -Times 0 -Exactly
        }
    }

    It 'Get-OpsFailedJobs returns structured job history' {
        InModuleScope DbaAutomation {
            Mock Invoke-DbaQuery {
                [pscustomobject]@{
                    JobName='Nightly Lab'; StepName=$null; RunDate=(Get-Date)
                    RunDuration=200; ErrorMessage='Test failure'
                }
            }
            Mock Get-DbaAgentJobHistory { throw 'Broad SQL Agent role based history lookup must not be used.' }
            $result = Get-OpsFailedJobs -SqlInstance MSSQL-VM1
            $result.JobName | Should -Be 'Nightly Lab'
            $result.ErrorMessage | Should -Be 'Test failure'
            $result.Duration | Should -Be '00:02:00'
            Should -Invoke Get-DbaAgentJobHistory -Times 0 -Exactly
            Should -Invoke Invoke-DbaQuery -Times 1 -Exactly -ParameterFilter {
                $Database -eq 'msdb' -and $Query -match 'msdb\.dbo\.sysjobhistory' -and
                $Query -match 'msdb\.dbo\.sysjobs' -and $SqlParameter.StartDate
            }
        }
    }

    It 'Get-OpsFailedJobs surfaces a narrow metadata permission failure without fallback' {
        InModuleScope DbaAutomation {
            Mock Invoke-DbaQuery { throw 'SELECT permission denied on msdb.dbo.sysjobhistory.' }
            Mock Get-DbaAgentJobHistory { throw 'No broader SQL Agent fallback is allowed.' }
            { Get-OpsFailedJobs -SqlInstance MSSQL-VM1 } | Should -Throw '*SELECT permission denied on msdb.dbo.sysjobhistory*'
            Should -Invoke Get-DbaAgentJobHistory -Times 0 -Exactly
        }
    }

    It 'Get-OpsBlockingSessions returns an empty structured result when no blockers exist' {
        InModuleScope DbaAutomation {
            Mock Get-DbaProcess { @() }
            $result = Get-OpsBlockingSessions -SqlInstance MSSQL-VM1
            $result | Should -HaveCount 0
            Should -Invoke Get-DbaProcess -Times 1 -Exactly -ParameterFilter { $Database -contains 'LabDb' }
        }
    }

    It 'Get-OpsLongRunningQueries returns an empty structured result when no long queries exist' {
        InModuleScope DbaAutomation {
            Mock Invoke-DbaQuery { @() }
            $result = Get-OpsLongRunningQueries -SqlInstance MSSQL-VM1
            $result | Should -HaveCount 0
            Should -Invoke Invoke-DbaQuery -Times 1 -Exactly -ParameterFilter {
                $Database -eq 'LabDb' -and $Query -match 'r\.database_id = DB_ID\(\)' -and $SqlParameters.ThresholdSec -eq 60
            }
        }
    }

    It 'Get-OpsBlockingSessions excludes sessions from databases outside ApprovedDatabases' {
        InModuleScope DbaAutomation {
            Mock Get-DbaProcess {
                @([pscustomobject]@{ Spid=71; BlockingSpid=70; Database='OtherDb' })
            }
            $result = Get-OpsBlockingSessions -SqlInstance MSSQL-VM1
            $result | Should -HaveCount 0
            Should -Invoke Get-DbaProcess -Times 1 -Exactly -ParameterFilter { $Database -contains 'LabDb' }
        }
    }

    It 'Get-OpsServerHealthReport creates a report from mocked diagnostics' {
        InModuleScope DbaAutomation {
            Mock Get-OpsDatabaseInventory { @() }
            Mock Get-OpsDatabaseHealth { @() }
            Mock Get-OpsDatabaseSize { @() }
            Mock Get-OpsBackupStatus { @() }
            Mock Get-OpsDiskSpace { @() }
            Mock Get-OpsFailedJobs { @() }
            Mock Get-OpsBlockingSessions { @() }
            Mock Get-OpsLongRunningQueries { @() }
            $reportPath = Join-Path $TestDrive 'health-report.html'
            $result = Get-OpsServerHealthReport -SqlInstance MSSQL-VM1 -OutputPath $reportPath
            $result.OverallStatus | Should -Be 'Healthy'
            Test-Path $reportPath | Should -BeTrue
        }
    }

    It 'Backup-OpsDatabase -WhatIf does not create the backup directory or call dbatools backup' {
        InModuleScope DbaAutomation {
            Mock Assert-OpsSafeDatabase { $Database }
            Mock Get-DbaDatabase { throw 'A log backup is not expected in this unit test' }
            Mock Backup-DbaDatabase { throw 'Backup-DbaDatabase must not execute under WhatIf' }
            $backupDirectory = Join-Path $TestDrive 'must-not-be-created'
            Mock Get-OpsConfig {
                [pscustomobject]@{
                    ApprovedInstances=@('MSSQL-VM1'); DefaultInstance='MSSQL-VM1'
                    ApprovedDatabases=@('LabDb')
                    ProtectedDatabases=@('master','model','msdb','tempdb')
                    BackupDirectory=$backupDirectory; LogDirectory=$TestDrive; TrustServerCertificate=$false
                    BackupPolicy=[pscustomobject]@{ Enabled=$true; AllowedTypes=@('Full','Differential','Log'); ApprovalRequired=$true }
                }
            }
            $result = Backup-OpsDatabase -SqlInstance MSSQL-VM1 -Database LabDb -BackupType Full -WhatIf
            $result.WhatIf | Should -BeTrue
            Test-Path $backupDirectory | Should -BeFalse
            Should -Invoke Backup-DbaDatabase -Times 0 -Exactly
        }
    }

    It 'Backup-OpsDatabase checks completion metadata and checksum without RESTORE VERIFYONLY' {
        InModuleScope DbaAutomation {
            Mock Backup-DbaDatabase {
                [pscustomobject]@{
                    BackupComplete=$true; BackupPath=@('C:\Backups\LabDb.bak')
                    CompressedBackupSize=1048576L; Notes=$null
                }
            }
            $result = Backup-OpsDatabase -SqlInstance MSSQL-VM1 -Database LabDb -BackupType Full -Confirm:$false
            $result.BackupCreated | Should -BeTrue
            $result.Verified | Should -BeNullOrEmpty
            $result.ValidationMethod | Should -Match 'no independent restore verification'
            Should -Invoke Backup-DbaDatabase -Times 1 -Exactly -ParameterFilter {
                $Checksum -eq $true -and -not $Verify -and $Database -eq 'LabDb'
            }
        }
    }

    It 'Backup-OpsDatabase rejects an incomplete backup result' {
        InModuleScope DbaAutomation {
            Mock Backup-DbaDatabase { [pscustomobject]@{ BackupComplete=$false; Notes='Engine reported failure' } }
            { Backup-OpsDatabase -SqlInstance MSSQL-VM1 -Database LabDb -BackupType Full -Confirm:$false } |
                Should -Throw '*did not report successful completion*'
        }
    }
}

Describe 'Sanitized audit logging' -Tag Unit {
    It 'withholds raw exception secrets and paths from log files' {
        InModuleScope DbaAutomation {
            Mock Get-OpsConfig {
                [pscustomobject]@{ LogDirectory = $TestDrive }
            }

            Write-OpsLog -FunctionName 'Get-OpsDatabaseInventory' `
                -Status Error -ErrorMessage 'Password=TopSecret C:\\private\\connection.txt'

            $logFile = Get-ChildItem -Path $TestDrive -Filter 'dba-automation-*.log' |
                Select-Object -First 1
            $entry = Get-Content -Path $logFile.FullName | ConvertFrom-Json
            $entry.Error | Should -Be 'Operation failed; details withheld.'
            $entry | ConvertTo-Json -Compress | Should -Not -Match 'TopSecret|connection.txt|C:\\private'
        }
    }
}

Describe 'DbaAutomation safety validation' -Tag Unit {
    BeforeEach {
        InModuleScope DbaAutomation {
            Mock Connect-OpsSqlInstance { $SqlInstance }
            Mock Initialize-OpsSession {}
            Mock Write-OpsLog {}
        }
    }

    It 'rejects an unapproved SQL Server instance before querying SQL Server' {
        InModuleScope DbaAutomation {
            Mock Get-DbaDatabase { throw 'SQL must not be queried for a denied instance' }
            { Get-OpsDatabaseInventory -SqlInstance 'UNAPPROVED-SQL' } | Should -Throw '*not in the approved configuration allowlist*'
            Should -Invoke Get-DbaDatabase -Times 0 -Exactly
        }
    }

    It 'rejects a missing database' {
        InModuleScope DbaAutomation {
            Mock Get-OpsConfig {
                [pscustomobject]@{ ApprovedDatabases=@('MissingDb'); ProtectedDatabases=@('master','model','msdb','tempdb') }
            }
            Mock Get-DbaDatabase { $null }
            { Assert-OpsSafeDatabase -SqlInstance MSSQL-VM1 -Database MissingDb } | Should -Throw "*Database(s) 'MissingDb' do not exist*"
        }
    }

    It 'rejects a database outside ApprovedDatabases before connecting or querying' {
        InModuleScope DbaAutomation {
            Mock Get-OpsConfig {
                [pscustomobject]@{ DefaultInstance='MSSQL-VM1'; ApprovedInstances=@('MSSQL-VM1'); ApprovedDatabases=@('LabDb'); ProtectedDatabases=@('master','model','msdb','tempdb') }
            }
            Mock Get-DbaDatabase { throw 'No SQL metadata query may run for an unapproved database.' }
            { Get-OpsDatabaseInventory -SqlInstance MSSQL-VM1 -Database SecretDb } | Should -Throw '*not in the configured ApprovedDatabases allowlist*'
            Should -Invoke Connect-OpsSqlInstance -Times 0 -Exactly
            Should -Invoke Get-DbaDatabase -Times 0 -Exactly
        }
    }

    It 'rejects a database outside ApprovedDatabases for the size diagnostic before connecting' {
        InModuleScope DbaAutomation {
            Mock Get-OpsConfig {
                [pscustomobject]@{ DefaultInstance='MSSQL-VM1'; ApprovedInstances=@('MSSQL-VM1'); ApprovedDatabases=@('LabDb'); ProtectedDatabases=@('master','model','msdb','tempdb') }
            }
            Mock Get-DbaDbFile { throw 'Database size query must not run for an unapproved database.' }
            { Get-OpsDatabaseSize -SqlInstance MSSQL-VM1 -Database OtherDb } | Should -Throw '*not in the configured ApprovedDatabases allowlist*'
            Should -Invoke Connect-OpsSqlInstance -Times 0 -Exactly
            Should -Invoke Get-DbaDbFile -Times 0 -Exactly
        }
    }

    It 'rejects an unapproved database in health and backup-status diagnostics before connecting' {
        InModuleScope DbaAutomation {
            Mock Get-OpsConfig {
                [pscustomobject]@{ DefaultInstance='MSSQL-VM1'; ApprovedInstances=@('MSSQL-VM1'); ApprovedDatabases=@('LabDb'); ProtectedDatabases=@('master','model','msdb','tempdb'); Thresholds=[pscustomobject]@{ BackupWarningHours=24 } }
            }
            Mock Get-DbaDatabase { throw 'Denied database must not be queried.' }
            Mock Get-DbaLastBackup { throw 'Denied database must not be queried.' }
            { Get-OpsDatabaseHealth -SqlInstance MSSQL-VM1 -Database SecretDb } | Should -Throw '*not in the configured ApprovedDatabases allowlist*'
            { Get-OpsBackupStatus -SqlInstance MSSQL-VM1 -Database SecretDb } | Should -Throw '*not in the configured ApprovedDatabases allowlist*'
            Should -Invoke Connect-OpsSqlInstance -Times 0 -Exactly
            Should -Invoke Get-DbaDatabase -Times 0 -Exactly
            Should -Invoke Get-DbaLastBackup -Times 0 -Exactly
        }
    }

    It 'rejects an unapproved database backup before connecting or calling backup cmdlets' {
        InModuleScope DbaAutomation {
            Mock Get-OpsConfig {
                [pscustomobject]@{ DefaultInstance='MSSQL-VM1'; ApprovedInstances=@('MSSQL-VM1'); ApprovedDatabases=@('LabDb'); ProtectedDatabases=@('master','model','msdb','tempdb'); BackupDirectory='C:\Backups'; BackupPolicy=[pscustomobject]@{ Enabled=$true; AllowedTypes=@('Full','Differential','Log'); ApprovalRequired=$true } }
            }
            Mock Backup-DbaDatabase { throw 'Unapproved database must not be backed up.' }
            { Backup-OpsDatabase -SqlInstance MSSQL-VM1 -Database SecretDb -BackupType Full -Confirm:$false } | Should -Throw '*not in the configured ApprovedDatabases allowlist*'
            Should -Invoke Connect-OpsSqlInstance -Times 0 -Exactly
            Should -Invoke Backup-DbaDatabase -Times 0 -Exactly
        }
    }

    It 'rejects protected system databases before querying SQL Server' {
        InModuleScope DbaAutomation {
            Mock Get-DbaDatabase { throw 'SQL must not be queried for a protected database' }
            { Assert-OpsSafeDatabase -SqlInstance MSSQL-VM1 -Database master } | Should -Throw '*protected system database*'
            Should -Invoke Get-DbaDatabase -Times 0 -Exactly
        }
    }

    It 'rejects an invalid backup type' {
        InModuleScope DbaAutomation {
            { Backup-OpsDatabase -Database LabDb -BackupType CopyOnly } | Should -Throw
        }
    }

    It 'rejects an invalid threshold' {
        InModuleScope DbaAutomation {
            { Get-OpsFailedJobs -SinceHours 721 } | Should -Throw
        }
    }

    It 'rejects invalid configuration without an ApprovedInstances allowlist' {
        InModuleScope DbaAutomation {
            $invalidConfigPath = Join-Path $TestDrive 'invalid-config.json'
            Set-Content -Path $invalidConfigPath -Value '{"Environment":"Test","ApprovedInstances":[]}'
            { Get-OpsConfig -ConfigPath $invalidConfigPath } | Should -Throw '*does not satisfy the required schema*'
        }
    }
}
