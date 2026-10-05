# Start the Feishu bot (Windows). Run from the repo root or pass -RepoDir.
# Register as a scheduled task — see windows.md.
param(
    [string]$RepoDir = $PSScriptRoot + "\.."
)
Set-Location (Resolve-Path $RepoDir)

# Prefer the project venv if it exists
$venvPy = Join-Path $RepoDir ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPy) { $venvPy } else { "python" }

& $python -m app.main 2>&1 | Tee-Object -FilePath (Join-Path $RepoDir "data\bot.log") -Append
