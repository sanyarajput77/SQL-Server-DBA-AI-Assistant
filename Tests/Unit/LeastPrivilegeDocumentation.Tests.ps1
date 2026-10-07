Describe 'Least-privilege SQL reference safety' -Tag Unit {
    It 'keeps the SQL reference entirely commented and non-executable' {
        $projectRoot = Split-Path $PSScriptRoot -Parent | Split-Path -Parent
        $referencePath = Join-Path $projectRoot 'docs/sql/create-ai-dba-login.sql'
        $lines = Get-Content -LiteralPath $referencePath

        $referencePath | Should -Exist
        $executableLines = @($lines | Where-Object { $_.Trim() -and $_ -notmatch '^\s*--' })
        $executableLines | Should -BeNullOrEmpty
    }

    It 'contains no password, secret, or credential assignment' {
        $projectRoot = Split-Path $PSScriptRoot -Parent | Split-Path -Parent
        $referencePath = Join-Path $projectRoot 'docs/sql/create-ai-dba-login.sql'
        $contents = Get-Content -LiteralPath $referencePath -Raw

        $contents | Should -Not -Match '(?im)^\s*--\s*(?:PASSWORD|SECRET)\s*='
        $contents | Should -Not -Match "(?im)^\s*--\s*CREATE\s+LOGIN[^\r\n]*WITH\s+PASSWORD"
    }
}
