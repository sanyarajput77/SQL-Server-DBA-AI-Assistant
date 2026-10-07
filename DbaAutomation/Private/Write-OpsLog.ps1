<#
.SYNOPSIS
    Writes structured, sanitized log entries for DBA automation operations.
.DESCRIPTION
    Mandatory audit log (Rules 13, 18, 19). Strips credentials and records
    timing, parameters, and results to a structured daily log file.
.PARAMETER FunctionName
    Name of the function or tool invoked.
.PARAMETER Parameters
    Hashtable of invocation parameters.
.PARAMETER Status
    Result status: Success, Warning, or Error.
.PARAMETER DurationMs
    Execution duration in milliseconds.
.PARAMETER ErrorMessage
    Optional error details.
#>
function Write-OpsLog {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$FunctionName,

        [Parameter(Mandatory = $false)]
        [hashtable]$Parameters = @{},

        [Parameter(Mandatory = $true)]
        [ValidateSet('Success', 'Warning', 'Error')]
        [string]$Status,

        [Parameter(Mandatory = $false)]
        [double]$DurationMs = 0,

        [Parameter(Mandatory = $false)]
        [string]$ErrorMessage = $null
    )

    try {
        $config = Get-OpsConfig

        # Sanitize parameters (Rule 13 & 19)
        $sanitizedParams = @{}
        $secretKeyPatterns = '(?i)(password|secret|token|cred|pwd|passphrase|connectionstring)'

        if ($Parameters) {
            foreach ($key in $Parameters.Keys) {
                if ($key -match $secretKeyPatterns) {
                    $sanitizedParams[$key] = '***REDACTED***'
                } elseif ($Parameters[$key] -is [System.Management.Automation.PSCredential]) {
                    $sanitizedParams[$key] = $Parameters[$key].UserName
                } else {
                    $val = $Parameters[$key]
                    if ($val -is [string] -and $val -match $secretKeyPatterns) {
                        $sanitizedParams[$key] = '***REDACTED***'
                    } else {
                        $sanitizedParams[$key] = $val
                    }
                }
            }
        }

        $safeError = $null
        if ($ErrorMessage) {
            $safeError = 'Operation failed; details withheld.'
        }

        $logEntry = [PSCustomObject]@{
            TimestampUtc = (Get-Date).ToUniversalTime().ToString('o')
            TimestampLocal = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
            Function     = $FunctionName
            Parameters   = $sanitizedParams
            Status       = $Status
            DurationMs   = [math]::Round($DurationMs, 2)
            # Exception text can contain connection strings, credentials, or local paths.
            # Keep the audit status while withholding provider-controlled details.
            Error        = $safeError
        }

        $logDir = $config.LogDirectory
        if (-not (Test-Path -Path $logDir)) {
            New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        }

        $dateStamp = (Get-Date).ToString('yyyyMMdd')
        $logFile = Join-Path -Path $logDir -ChildPath "dba-automation-$dateStamp.log"
        $jsonLine = $logEntry | ConvertTo-Json -Compress

        [System.IO.File]::AppendAllLines($logFile, [string[]]@($jsonLine))
    } catch {
        # Failsafe: Logging failures must never crash the core DBA command
        Write-Warning 'Failed to write audit log; details withheld.'
    }
}
