# Purpose: Run the full local pytest suite in an isolated Python 3.12 environment aligned with CI.
# Behavior:
# - Creates (or reuses) `.venv-test` with the selected Python launcher.
# - Installs test dependencies used by CI.
# - Sets `PYTHONPATH` to repo root and runs `pytest -q` for the full repository test suite.
# Usage:
# - `powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1`
# - `powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1 -RecreateVenv`
# - `powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1 -SkipInstall`
# - `powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1 -PythonCommand "py -3.12"`
# - `powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1 -PythonCommand "python3.12"`
# Version: 1.1.0
# Changelog:
# - 1.1.0: Make Python launcher overridable and clarify that the script runs the full pytest suite.
# - 1.0.0: Initial script for repeatable local CI-like test runs.
# Inputs/Environment Variables:
# - `-PythonCommand`: optional Python launcher command used to create the venv. Defaults to `py -3.12`.
# Outputs/Side Effects:
# - Creates/updates `.venv-test` in repo root.
# - Installs packages into `.venv-test`.
# Prerequisites:
# - Windows PowerShell
# - Python 3.12 available via the chosen launcher command

[CmdletBinding()]
param(
    [switch]$RecreateVenv,
    [switch]$SkipInstall,
    [string]$PythonCommand = "py -3.12"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvDir = Join-Path $repoRoot ".venv-test"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

Write-Host "Repository root: $repoRoot"

if ($RecreateVenv -and (Test-Path $venvDir)) {
    Write-Host "Removing existing venv: $venvDir"
    Remove-Item -Recurse -Force -LiteralPath $venvDir
}

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment with Python launcher: $PythonCommand"
    $pythonParts = $PythonCommand -split '\s+'
    if ($pythonParts.Length -gt 1) {
        & $pythonParts[0] $pythonParts[1..($pythonParts.Length - 1)] -m venv $venvDir
    }
    else {
        & $pythonParts[0] -m venv $venvDir
    }
}

if (-not (Test-Path $venvPython)) {
    throw "Virtual environment creation failed. Ensure Python 3.12 is installed and available via '$PythonCommand'."
}

if (-not $SkipInstall) {
    Write-Host "Installing test dependencies..."
    & $venvPython -m pip install --upgrade pip setuptools wheel
    & $venvPython -m pip install pytest pytest-asyncio homeassistant
}

Write-Host "Running pytest..."
$env:PYTHONPATH = $repoRoot
Push-Location $repoRoot
try {
    & $venvPython -m pytest -q
}
finally {
    Pop-Location
}
