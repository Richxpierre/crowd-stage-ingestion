# Run from PowerShell: .\scripts\setup.ps1
# Installs development dependencies and runs local checks. Does not connect to Supabase.
[CmdletBinding()]
param([string]$PythonCommand = 'python')

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'

Push-Location -LiteralPath $projectRoot
try {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $PythonCommand -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
    }
    & $venvPython -m pip install -r requirements.txt --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw 'Could not install Python dependencies.' }
    & $venvPython -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed.' }
    & $venvPython -m compileall -q collector scripts
    if ($LASTEXITCODE -ne 0) { throw 'Python syntax checks failed.' }
    Write-Host 'Backend is ready for local development. Tests passed.'
}
finally {
    Pop-Location
}
