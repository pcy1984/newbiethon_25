$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
$pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'

if ($pythonCommand -and $pythonCommand.Source -notlike '*WindowsApps*') {
    & $pythonCommand.Source (Join-Path $PSScriptRoot 'backend\app.py')
} elseif ($pyCommand) {
    & $pyCommand.Source -3 (Join-Path $PSScriptRoot 'backend\app.py')
} elseif (Test-Path -LiteralPath $bundledPython) {
    & $bundledPython (Join-Path $PSScriptRoot 'backend\app.py')
} else {
    Write-Host 'Python 3.10+ is required. Install Python, then run this file again.'
    exit 1
}
