<#
.SYNOPSIS
    Performs a SQL Server database backup to the configured backup directory.
.DESCRIPTION
    Safely creates Full, Differential, or Log backups and checks dbatools' reported
    completion metadata. It does not run RESTORE VERIFYONLY. Enforces instance
    allowlist, database existence, system database protection, and configured backup path.
    Supports -WhatIf and returns structured output.
.PARAMETER SqlInstance
    Target SQL Server instance. Must be in the approved allowlist. Defaults to configuration default.
.PARAMETER Database
    Name of the user database to back up. System databases are strictly rejected.
.PARAMETER BackupType
    Type of backup. Allowed values: 'Full', 'Differential', 'Log'.
.EXAMPLE
    Backup-OpsDatabase -Database 'AdventureWorks2022' -BackupType Full -WhatIf
.EXAMPLE
    Backup-OpsDatabase -Database 'AdventureWorks2022' -BackupType Full
#>
function Backup-OpsDatabase {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
    param(
        [Parameter(Mandatory = $false, Position = 0)]
        [string]$SqlInstance,

        [Parameter(Mandatory = $true, Position = 1)]
        [string]$Database,

        [Parameter(Mandatory = $true, Position = 2)]
        [ValidateSet('Full', 'Differential', 'Log', IgnoreCase = $false)]
        [string]$BackupType
    )

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $status = 'Success'
    $errorMsg = $null
    $backupResult = $null

    try {
        # 1. Safety validation: Instance allowlist (Rule 8)
        $SqlInstance = Assert-OpsApprovedInstance -SqlInstance $SqlInstance

        # 2. Safety validation: Configured backup destination only (Rule 12)
        $config = Get-OpsConfig
        if (-not $config.BackupPolicy.Enabled) {
            throw [System.InvalidOperationException]::new("Backup operations are disabled by the selected environment configuration.")
        }
        if ($BackupType -notin $config.BackupPolicy.AllowedTypes) {
            throw [System.InvalidOperationException]::new("BackupType is not enabled by the selected environment configuration.")
        }
        $backupDir = $config.BackupDirectory
        if ([string]::IsNullOrWhiteSpace($backupDir)) {
            throw [System.InvalidOperationException]"Configuration missing mandatory 'BackupDirectory'."
        }
        # Reject protected/unapproved databases locally before connecting.
        $Database = Get-OpsApprovedDatabaseTargets -Database @($Database) -Config $config
        $sqlConnection = Connect-OpsSqlInstance -SqlInstance $SqlInstance -Config $config
        # 3. Confirm database existence/access after local allowlist validation.
        $Database = Assert-OpsSafeDatabase -SqlInstance $SqlInstance -Database $Database -Config $config -SqlConnection $sqlConnection

        # 4. Check recovery model compatibility for Log backups
        Initialize-OpsSession -Config $config
        if ($BackupType -eq 'Log') {
            $dbObj = Get-DbaDatabase -SqlInstance $sqlConnection -Database $Database
            if ($dbObj.RecoveryModel -eq 'Simple') {
                throw [System.InvalidOperationException]"Cannot take Log backup of database '$Database' in Simple recovery model."
            }
        }

        $targetDesc = "$SqlInstance\$Database ($BackupType backup to $backupDir)"

        # 5. Support -WhatIf (Rule 14)
        if ($PSCmdlet.ShouldProcess($targetDesc, "Create database backup")) {
            # Create the configured destination only after approval. This keeps -WhatIf read-only.
            if (-not (Test-Path -Path $backupDir)) {
                New-Item -ItemType Directory -Path $backupDir -Force -ErrorAction Stop | Out-Null
            }

            # Checksum protects backup pages during creation. Independent
            # RESTORE VERIFYONLY is deliberately omitted because it requires
            # CREATE DATABASE permission, which is outside the least-privilege
            # backup identity.
            $dbaParams = @{
                SqlInstance     = $sqlConnection
                Database        = $Database
                Path            = $backupDir
                Type            = $BackupType
                Checksum        = $true
                CompressBackup  = $true
                EnableException = $true
            }

            $rawResult = Backup-DbaDatabase @dbaParams

            if (-not $rawResult) {
                throw [System.InvalidOperationException]::new('Backup-DbaDatabase returned no completion result; backup success cannot be confirmed.')
            }
            $incomplete = @($rawResult | Where-Object { -not $_.BackupComplete })
            if ($incomplete.Count -gt 0) {
                $notes = @($incomplete | ForEach-Object { $_.Notes } | Where-Object { $_ }) -join '; '
                throw [System.InvalidOperationException]::new("Backup-DbaDatabase did not report successful completion for '$Database'. $notes")
            }

            $filePaths = @()
            $totalSizeMb = 0
            if ($rawResult) {
                foreach ($r in $rawResult) {
                    if ($r.BackupPath) { $filePaths += @($r.BackupPath) }
                    elseif ($r.Path) { $filePaths += @($r.Path) }
                    if ($r.CompressedBackupSize) {
                        $totalSizeMb += [double]$r.CompressedBackupSize / 1MB
                    }
                }
            }

            $backupResult = [PSCustomObject]@{
                SqlInstance       = $SqlInstance
                DatabaseName      = $Database
                BackupType        = $BackupType
                Status            = 'Success'
                BackupFiles       = ($filePaths -join '; ')
                BackupDirectory   = $backupDir
                BackupSizeMB      = [math]::Round($totalSizeMb, 2)
                Verified          = $null
                BackupCreated     = $true
                ValidationMethod  = 'dbatools reported completion; checksum enabled; no independent restore verification.'
                Timestamp         = (Get-Date)
                DurationMs        = $null
                WhatIf            = $false
            }
        } else {
            $status = 'Success'
            $backupResult = [PSCustomObject]@{
                SqlInstance       = $SqlInstance
                DatabaseName      = $Database
                BackupType        = $BackupType
                Status            = 'WhatIf'
                BackupFiles       = "(Target: $backupDir)"
                BackupDirectory   = $backupDir
                BackupSizeMB      = 0
                Verified          = $null
                BackupCreated     = $false
                ValidationMethod  = 'Not run (WhatIf).'
                Timestamp         = (Get-Date)
                DurationMs        = 0
                WhatIf            = $true
            }
        }

        return $backupResult
    } catch {
        $status = 'Error'
        $errorMsg = $_.Exception.Message
        throw
    } finally {
        $stopwatch.Stop()
        if ($backupResult) {
            $backupResult.DurationMs = [math]::Round($stopwatch.ElapsedMilliseconds, 2)
        }
        Write-OpsLog -FunctionName 'Backup-OpsDatabase' `
            -Parameters @{ SqlInstance = $SqlInstance; Database = $Database; BackupType = $BackupType } `
            -Status $status -DurationMs $stopwatch.ElapsedMilliseconds -ErrorMessage $errorMsg
    }
}
