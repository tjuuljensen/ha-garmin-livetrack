# Purpose: Run local tests in an isolated Python 3.12 environment aligned with CI.
# Behavior:
# - Creates (or reuses) `.venv-test` with Python 3.12.
# - Installs test dependencies used by CI.
# - Sets `PYTHONPATH` to repo root and runs `pytest -q`.
# Usage:
# - `powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1`
# - `powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1 -RecreateVenv`
# - `powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1 -SkipInstall`
# Version: 1.0.0
# Changelog:
# - 1.0.0: Initial script for repeatable local CI-like test runs.
# Inputs/Environment Variables:
# - Requires Python 3.12 discoverable via `py -3.12`.
# Outputs/Side Effects:
# - Creates/updates `.venv-test` in repo root.
# - Installs packages into `.venv-test`.
# Prerequisites:
# - Windows PowerShell
# - Python launcher (`py`) with Python 3.12 installed

[CmdletBinding()]
param(
    [switch]$RecreateVenv,
    [switch]$SkipInstall
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
    Write-Host "Creating virtual environment with Python 3.12..."
    & py -3.12 -m venv $venvDir
}

if (-not (Test-Path $venvPython)) {
    throw "Virtual environment creation failed. Ensure Python 3.12 is installed and available as 'py -3.12'."
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
