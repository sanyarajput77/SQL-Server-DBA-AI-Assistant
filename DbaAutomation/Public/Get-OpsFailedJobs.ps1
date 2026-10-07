<#
.SYNOPSIS
    Returns recently failed SQL Server Agent jobs and execution history.
.DESCRIPTION
    Uses a fixed, parameterized read-only query over the two msdb metadata tables
    needed for job outcome summaries. It does not use the SQLAgentReaderRole.
.PARAMETER SqlInstance
    Target SQL Server instance. Must be in the approved allowlist. Defaults to configuration default.
.PARAMETER SinceHours
    Time window in hours to look back for failed jobs. Defaults to config (24 hrs).
.EXAMPLE
    Get-OpsFailedJobs
.EXAMPLE
    Get-OpsFailedJobs -SinceHours 48
.EXAMPLE
    Get-OpsFailedJobs -SqlInstance 'ConfiguredInstance' -SinceHours 12
#>
function Get-OpsFailedJobs {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false, Position = 0)]
        [string]$SqlInstance,

        [Parameter(Mandatory = $false, Position = 1)]
        [ValidateRange(1, 720)]
        [int]$SinceHours
    )

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $status = 'Success'
    $errorMsg = $null

    try {
        $SqlInstance = Assert-OpsApprovedInstance -SqlInstance $SqlInstance
        $config = Get-OpsConfig
        Initialize-OpsSession -Config $config
        $sqlConnection = Connect-OpsSqlInstance -SqlInstance $SqlInstance -Config $config

        if ($SinceHours -le 0) {
            $SinceHours = if ($config.Thresholds.FailedJobsHours) { [int]$config.Thresholds.FailedJobsHours } else { 24 }
        }

        $startDate = (Get-Date).AddHours(-$SinceHours)

        $query = @'
SELECT
    j.name AS JobName,
    CAST(NULL AS nvarchar(128)) AS StepName,
    run.RunDate,
    h.run_duration AS RunDuration,
    h.message AS ErrorMessage
FROM msdb.dbo.sysjobhistory AS h
INNER JOIN msdb.dbo.sysjobs AS j
    ON j.job_id = h.job_id
CROSS APPLY (
    SELECT DATEADD(SECOND,
        (h.run_time / 10000) * 3600
        + ((h.run_time / 100) % 100) * 60
        + (h.run_time % 100),
        CONVERT(datetime2(0), CONVERT(char(8), h.run_date), 112)
    ) AS RunDate
) AS run
WHERE h.step_id = 0
  AND h.run_status = 0
  AND h.run_date > 0
  AND run.RunDate >= @StartDate
ORDER BY run.RunDate DESC;
'@

        $failedHist = Invoke-DbaQuery -SqlInstance $sqlConnection -Database msdb `
            -Query $query -SqlParameter @{ StartDate = $startDate } -EnableException

        $results = [System.Collections.Generic.List[PSCustomObject]]::new()
        if ($failedHist) {
            foreach ($job in $failedHist) {
                $results.Add([PSCustomObject]@{
                    SqlInstance      = $SqlInstance
                    JobName          = $job.JobName
                    StepName         = $job.StepName
                    RunDate          = $job.RunDate
                    Duration         = ([timespan]::FromSeconds(
                        ([math]::Floor([double]$job.RunDuration / 10000) * 3600) +
                        ([math]::Floor(([int]$job.RunDuration % 10000) / 100) * 60) +
                        ([int]$job.RunDuration % 100)
                    )).ToString()
                    Status           = 'Failed'
                    ErrorMessage     = $job.ErrorMessage
                })
            }
        }

        return $results.ToArray()
    } catch {
        $status = 'Error'
        $errorMsg = $_.Exception.Message
        throw
    } finally {
        $stopwatch.Stop()
        Write-OpsLog -FunctionName 'Get-OpsFailedJobs' `
            -Parameters @{ SqlInstance = $SqlInstance; SinceHours = $SinceHours } `
            -Status $status -DurationMs $stopwatch.ElapsedMilliseconds -ErrorMessage $errorMsg
    }
}
