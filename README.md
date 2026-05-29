# ha-garmin-livetrack
Garin Livetrack integration for Home Assistant

## Local testing
Run tests locally in a Python 3.12 virtual environment that matches CI expectations:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1
```

Useful options:

```powershell
# Recreate environment from scratch
powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1 -RecreateVenv

# Skip dependency installation and only run tests
powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1 -SkipInstall
```
