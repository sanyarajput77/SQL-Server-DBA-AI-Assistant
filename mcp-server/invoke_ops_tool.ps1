[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

try {
    $requestText = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($requestText)) {
        throw 'The MCP bridge received an empty request.'
    }

    $request = ConvertFrom-Json -InputObject $requestText -AsHashtable -Depth 12 -ErrorAction Stop
    if ($request -isnot [System.Collections.IDictionary] -or
        ($request.Keys | Where-Object { $_ -notin @('ToolName', 'Parameters', 'ApprovalGranted') })) {
        throw 'The MCP bridge request shape is invalid.'
    }

    $parameterAllowlist = @{
        'Get-OpsDatabaseInventory' = @('SqlInstance', 'Database')
        'Get-OpsDatabaseHealth' = @('SqlInstance', 'Database')
        'Get-OpsDatabaseSize' = @('SqlInstance', 'Database', 'Detailed')
        'Get-OpsBackupStatus' = @('SqlInstance', 'Database', 'NotBackedUpHours')
        'Get-OpsDiskSpace' = @('SqlInstance', 'WarningPercent')
        'Get-OpsFailedJobs' = @('SqlInstance', 'SinceHours')
        'Get-OpsBlockingSessions' = @('SqlInstance')
        'Get-OpsLongRunningQueries' = @('SqlInstance', 'ThresholdSeconds')
        'Get-OpsServerHealthReport' = @('SqlInstance')
        'Backup-OpsDatabase' = @('SqlInstance', 'Database', 'BackupType')
    }

    $toolName = [string]$request.ToolName
    if (-not $parameterAllowlist.ContainsKey($toolName)) {
        throw 'The requested Ops capability is not exposed by this bridge.'
    }
    if ($request.Parameters -isnot [System.Collections.IDictionary]) {
        throw 'The MCP bridge parameters must be a JSON object.'
    }

    $unexpectedParameters = @($request.Parameters.Keys | Where-Object { $_ -notin $parameterAllowlist[$toolName] })
    if ($unexpectedParameters.Count -gt 0) {
        throw "Unsupported parameter(s) for '$toolName'."
    }

    $isBackup = $toolName -eq 'Backup-OpsDatabase'
    if ($isBackup -and $request.ApprovalGranted -cne $true) {
        throw 'NEEDS_APPROVAL: Backup execution requires explicit user approval through MCP elicitation.'
    }
    if (-not $isBackup -and $request.ApprovalGranted) {
        throw 'Approval metadata is invalid for a read-only operation.'
    }

    $modulePath = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\DbaAutomation\DbaAutomation.psd1'))
    Import-Module -Name $modulePath -Force -ErrorAction Stop | Out-Null
    $opsParameters = @{}
    foreach ($key in $request.Parameters.Keys) {
        $opsParameters[$key] = $request.Parameters[$key]
    }

    # Static dispatch only. Tool input never selects a command, script, or SQL.
    switch ($toolName) {
        'Get-OpsDatabaseInventory' { $result = Get-OpsDatabaseInventory @opsParameters -ErrorAction Stop }
        'Get-OpsDatabaseHealth' { $result = Get-OpsDatabaseHealth @opsParameters -ErrorAction Stop }
        'Get-OpsDatabaseSize' { $result = Get-OpsDatabaseSize @opsParameters -ErrorAction Stop }
        'Get-OpsBackupStatus' { $result = Get-OpsBackupStatus @opsParameters -ErrorAction Stop }
        'Get-OpsDiskSpace' { $result = Get-OpsDiskSpace @opsParameters -ErrorAction Stop }
        'Get-OpsFailedJobs' { $result = Get-OpsFailedJobs @opsParameters -ErrorAction Stop }
        'Get-OpsBlockingSessions' { $result = Get-OpsBlockingSessions @opsParameters -ErrorAction Stop }
        'Get-OpsLongRunningQueries' { $result = Get-OpsLongRunningQueries @opsParameters -ErrorAction Stop }
        'Get-OpsServerHealthReport' { $result = Get-OpsServerHealthReport @opsParameters -ErrorAction Stop }
        'Backup-OpsDatabase' {
            $opsParameters['Confirm'] = $false
            $result = Backup-OpsDatabase @opsParameters -ErrorAction Stop
        }
    }

    $jsonResult = ConvertTo-Json -InputObject $result -Depth 12 -Compress -ErrorAction Stop
    [Console]::Out.WriteLine($jsonResult)
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
