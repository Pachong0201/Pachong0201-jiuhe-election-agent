# Start the Feishu bot (Windows) and restart it if it exits. See windows.md.
# Logs: data\bot.log (written by the bot itself, UTF-8, rotated at 10MB).
param(
    [string]$RepoDir = (Join-Path $PSScriptRoot "..")
)
Set-Location (Resolve-Path $RepoDir)
$env:PYTHONUTF8 = "1"

$venvPy = Join-Path (Get-Location) ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPy) { $venvPy } else { "python" }

while ($true) {
    & $python -m app.main *> $null
    if ($LASTEXITCODE -eq 2) { break }   # missing .env settings: do not loop
    Start-Sleep -Seconds 30
}
