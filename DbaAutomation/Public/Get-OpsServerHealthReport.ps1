<#
.SYNOPSIS
    Generates a comprehensive HTML health report for a SQL Server instance.
.DESCRIPTION
    Collects diagnostics from all read-only diagnostic functions and renders
    a self-contained, styled HTML report stored in the Reports/ directory.
.PARAMETER SqlInstance
    Target SQL Server instance. Must be in the approved allowlist. Defaults to configuration default.
.PARAMETER OutputPath
    Optional output file path. Defaults to configured Reports directory with timestamped filename.
.EXAMPLE
    Get-OpsServerHealthReport
.EXAMPLE
    Get-OpsServerHealthReport -SqlInstance 'ConfiguredInstance'
#>
function Get-OpsServerHealthReport {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false, Position = 0)]
        [string]$SqlInstance,

        [Parameter(Mandatory = $false, Position = 1)]
        [string]$OutputPath
    )

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $status = 'Success'
    $errorMsg = $null

    try {
        $SqlInstance = Assert-OpsApprovedInstance -SqlInstance $SqlInstance
        $config = Get-OpsConfig
        Initialize-OpsSession -Config $config

        # 1. Gather all diagnostics
        Write-Verbose "Collecting database inventory..."
        $inventory = Get-OpsDatabaseInventory -SqlInstance $SqlInstance -ErrorAction SilentlyContinue

        Write-Verbose "Collecting database health..."
        $health = Get-OpsDatabaseHealth -SqlInstance $SqlInstance -ErrorAction SilentlyContinue

        Write-Verbose "Collecting database sizes..."
        $sizes = Get-OpsDatabaseSize -SqlInstance $SqlInstance -ErrorAction SilentlyContinue

        Write-Verbose "Collecting backup status..."
        $backups = Get-OpsBackupStatus -SqlInstance $SqlInstance -ErrorAction SilentlyContinue

        Write-Verbose "Collecting disk space..."
        $disks = Get-OpsDiskSpace -SqlInstance $SqlInstance -ErrorAction SilentlyContinue

        Write-Verbose "Collecting failed jobs..."
        $failedJobs = Get-OpsFailedJobs -SqlInstance $SqlInstance -ErrorAction SilentlyContinue

        Write-Verbose "Collecting blocking sessions..."
        $blockers = Get-OpsBlockingSessions -SqlInstance $SqlInstance -ErrorAction SilentlyContinue

        Write-Verbose "Collecting long-running queries..."
        $longQueries = Get-OpsLongRunningQueries -SqlInstance $SqlInstance -ErrorAction SilentlyContinue

        # Determine target file path
        if (-not $OutputPath) {
            $reportDir = $config.ReportDirectory
            if (-not (Test-Path -Path $reportDir)) {
                New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
            }
            $cleanInstance = $SqlInstance -replace '[^a-zA-Z0-9_\-]', '_'
            $timestamp = (Get-Date).ToString('yyyyMMdd-HHmmss')
            $OutputPath = Join-Path -Path $reportDir -ChildPath "ServerHealth-$cleanInstance-$timestamp.html"
        }

        # Calculate summary metrics
        $totalDbs = if ($inventory) { $inventory.Count } else { 0 }
        $healthIssues = if ($health) { ($health | Where-Object { $_.HealthStatus -ne 'Healthy' }).Count } else { 0 }
        $backupWarnings = if ($backups) { ($backups | Where-Object { $_.ExceedsThreshold }).Count } else { 0 }
        $lowDiskWarnings = if ($disks) { ($disks | Where-Object { $_.IsLowSpace }).Count } else { 0 }
        $failedJobCount = if ($failedJobs) { $failedJobs.Count } else { 0 }
        $blockerCount = if ($blockers) { $blockers.Count } else { 0 }
        $longQueryCount = if ($longQueries) { $longQueries.Count } else { 0 }

        $overallStatus = 'Healthy'
        if ($healthIssues -gt 0 -or $backupWarnings -gt 0 -or $lowDiskWarnings -gt 0 -or $failedJobCount -gt 0) {
            $overallStatus = 'Warning'
        }
        if ($blockerCount -gt 0 -or ($health | Where-Object { $_.HealthStatus -eq 'Critical' }).Count -gt 0) {
            $overallStatus = 'Critical'
        }

        # HTML Generation Helper
        function Convert-ToHtmlTable {
            param($Data, [string[]]$Properties, [string[]]$Headers)
            if (-not $Data -or $Data.Count -eq 0) {
                return "<p class='no-data'>No active items or alerts detected.</p>"
            }
            $sb = [System.Text.StringBuilder]::new()
            [void]$sb.AppendLine("<table>")
            [void]$sb.AppendLine("  <thead><tr>")
            foreach ($h in $Headers) { [void]$sb.AppendLine("    <th>$h</th>") }
            [void]$sb.AppendLine("  </tr></thead>")
            [void]$sb.AppendLine("  <tbody>")
            foreach ($row in $Data) {
                [void]$sb.AppendLine("  <tr>")
                foreach ($p in $Properties) {
                    $val = $row.$p
                    $display = if ($null -eq $val) { '-' } else { [System.Web.HttpUtility]::HtmlEncode($val.ToString()) }
                    $class = ''
                    if ($display -match '(?i)warning') { $class = ' class="badge-warn"' }
                    elseif ($display -match '(?i)critical|error|failed') { $class = ' class="badge-crit"' }
                    elseif ($display -eq 'Healthy' -or $display -eq 'OK' -or $display -eq 'Normal') { $class = ' class="badge-ok"' }
                    [void]$sb.AppendLine("    <td$class>$display</td>")
                }
                [void]$sb.AppendLine("  </tr>")
            }
            [void]$sb.AppendLine("  </tbody></table>")
            return $sb.ToString()
        }

        $invHtml = Convert-ToHtmlTable -Data $inventory `
            -Properties @('DatabaseName', 'Status', 'RecoveryModel', 'CompatibilityLevel', 'CreateDate', 'Collation', 'SizeMB') `
            -Headers @('Database', 'Status', 'Recovery Model', 'Compatibility', 'Created', 'Collation', 'Size (MB)')

        $healthHtml = Convert-ToHtmlTable -Data $health `
            -Properties @('DatabaseName', 'HealthStatus', 'DatabaseStatus', 'PageVerify', 'AutoClose', 'AutoShrink', 'LastGoodCheckDb', 'Notes') `
            -Headers @('Database', 'Health', 'Status', 'Page Verify', 'AutoClose', 'AutoShrink', 'Last Good CheckDB', 'Diagnostics')

        $sizeHtml = Convert-ToHtmlTable -Data $sizes `
            -Properties @('DatabaseName', 'TotalSizeMB', 'DataSizeMB', 'DataUsedMB', 'LogSizeMB', 'LogUsedMB', 'PercentFree') `
            -Headers @('Database', 'Total (MB)', 'Data (MB)', 'Data Used (MB)', 'Log (MB)', 'Log Used (MB)', '% Free')

        $backupHtml = Convert-ToHtmlTable -Data $backups `
            -Properties @('DatabaseName', 'RecoveryModel', 'LatestBackupDate', 'LatestBackupType', 'HoursSinceLatest', 'StatusMessage') `
            -Headers @('Database', 'Recovery Model', 'Latest Backup', 'Type', 'Hours Ago', 'SLA Status')

        $diskHtml = Convert-ToHtmlTable -Data $disks `
            -Properties @('Drive', 'VolumeLabel', 'CapacityGB', 'FreeSpaceGB', 'PercentFree', 'Status') `
            -Headers @('Drive', 'Label', 'Capacity (GB)', 'Free (GB)', '% Free', 'Status')

        $jobsHtml = Convert-ToHtmlTable -Data $failedJobs `
            -Properties @('JobName', 'StepName', 'RunDate', 'Duration', 'ErrorMessage') `
            -Headers @('Job Name', 'Step', 'Run Date', 'Duration', 'Error Message')

        $blockHtml = Convert-ToHtmlTable -Data $blockers `
            -Properties @('BlockedSpid', 'BlockerSpid', 'DatabaseName', 'BlockedCommand', 'BlockedStatus', 'BlockedQuery') `
            -Headers @('Blocked SPID', 'Lead Blocker', 'Database', 'Command', 'Status', 'Executing SQL')

        $longHtml = Convert-ToHtmlTable -Data $longQueries `
            -Properties @('Spid', 'DatabaseName', 'ElapsedSeconds', 'Command', 'Status', 'LoginName', 'StatementText') `
            -Headers @('SPID', 'Database', 'Elapsed (s)', 'Command', 'Status', 'Login', 'Executing Statement')

        $badgeClass = switch ($overallStatus) {
            'Healthy'  { 'badge-ok' }
            'Warning'  { 'badge-warn' }
            'Critical' { 'badge-crit' }
        }

        $htmlContent = @"
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>SQL Server Health Report - $SqlInstance</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; margin: 20px 40px; background: #f8fafc; color: #1e293b; }
  h1 { margin-bottom: 4px; color: #0f172a; font-size: 24px; }
  .subtitle { color: #64748b; font-size: 14px; margin-bottom: 24px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 28px; }
  .card { background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
  .card-title { font-size: 12px; color: #64748b; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; }
  .card-value { font-size: 28px; font-weight: 700; color: #0f172a; margin-top: 6px; }
  .section { background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
  h2 { font-size: 16px; color: #0f172a; margin-top: 0; margin-bottom: 14px; border-bottom: 2px solid #f1f5f9; padding-bottom: 8px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 10px 12px; background: #f8fafc; color: #475569; font-weight: 600; border-bottom: 2px solid #e2e8f0; }
  td { padding: 9px 12px; border-bottom: 1px solid #f1f5f9; color: #334155; }
  tr:hover { background: #f8fafc; }
  .badge-ok { color: #15803d; font-weight: 600; }
  .badge-warn { color: #b45309; font-weight: 600; }
  .badge-crit { color: #b91c1c; font-weight: 700; }
  .status-pill { display: inline-block; padding: 4px 12px; border-radius: 9999px; font-weight: 700; font-size: 13px; }
  .pill-Healthy { background: #dcfce7; color: #166534; }
  .pill-Warning { background: #fef3c7; color: #92400e; }
  .pill-Critical { background: #fee2e2; color: #991b1b; }
  .no-data { color: #64748b; font-style: italic; font-size: 13px; }
  footer { margin-top: 40px; font-size: 12px; color: #94a3b8; text-align: center; }
</style>
</head>
<body>
  <h1>SQL Server Health Report: $SqlInstance</h1>
  <div class="subtitle">Generated on $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) | Overall Status: <span class="status-pill pill-$overallStatus">$overallStatus</span></div>

  <div class="cards">
    <div class="card"><div class="card-title">Databases</div><div class="card-value">$totalDbs</div></div>
    <div class="card"><div class="card-title">Health Warnings</div><div class="card-value">$healthIssues</div></div>
    <div class="card"><div class="card-title">Backup SLA Breaches</div><div class="card-value">$backupWarnings</div></div>
    <div class="card"><div class="card-title">Low Disk Volumes</div><div class="card-value">$lowDiskWarnings</div></div>
    <div class="card"><div class="card-title">Failed Agent Jobs</div><div class="card-value">$failedJobCount</div></div>
    <div class="card"><div class="card-title">Active Blockers</div><div class="card-value">$blockerCount</div></div>
  </div>

  <div class="section">
    <h2>1. Database Inventory</h2>
    $invHtml
  </div>

  <div class="section">
    <h2>2. Database Health &amp; Integrity</h2>
    $healthHtml
  </div>

  <div class="section">
    <h2>3. Database Storage &amp; Space Allocation</h2>
    $sizeHtml
  </div>

  <div class="section">
    <h2>4. Backup Recency &amp; SLA Status</h2>
    $backupHtml
  </div>

  <div class="section">
    <h2>5. Storage Volume Free Space</h2>
    $diskHtml
  </div>

  <div class="section">
    <h2>6. SQL Server Agent Failed Jobs (Recent)</h2>
    $jobsHtml
  </div>

  <div class="section">
    <h2>7. Active Blocking Sessions</h2>
    $blockHtml
  </div>

  <div class="section">
    <h2>8. Active Long-Running Queries</h2>
    $longHtml
  </div>

  <footer>
    Automated DBA Assistant &bull; Read-Only Health Diagnostic Report &bull; Environment: $($config.Environment)
  </footer>
</body>
</html>
"@

        [System.IO.File]::WriteAllText($OutputPath, $htmlContent, [System.Text.Encoding]::UTF8)

        return [PSCustomObject]@{
            SqlInstance           = $SqlInstance
            ReportPath            = $OutputPath
            GeneratedAt           = (Get-Date)
            OverallStatus         = $overallStatus
            DatabaseCount         = $totalDbs
            HealthIssueCount      = $healthIssues
            BackupWarningCount    = $backupWarnings
            LowDiskVolumeCount    = $lowDiskWarnings
            FailedJobCount        = $failedJobCount
            ActiveBlockerCount    = $blockerCount
            LongRunningQueryCount = $longQueryCount
        }
    } catch {
        $status = 'Error'
        $errorMsg = $_.Exception.Message
        throw
    } finally {
        $stopwatch.Stop()
        Write-OpsLog -FunctionName 'Get-OpsServerHealthReport' `
            -Parameters @{ SqlInstance = $SqlInstance; OutputPath = $OutputPath } `
            -Status $status -DurationMs $stopwatch.ElapsedMilliseconds -ErrorMessage $errorMsg
    }
}
