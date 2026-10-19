$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:HOST = '0.0.0.0'
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path -LiteralPath $bundledPython) {
    & $bundledPython app.py
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    python app.py
} else {
    Write-Host 'Please install Python 3.12 or newer, then run python app.py.'
}
