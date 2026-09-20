# Builds a standalone square-drive.exe into .\dist, then stages an installable
# copy in .\dist\install alongside .env.example.
#
# The installed copy under C:\SquareDriveConnector is what Task Scheduler runs.
# Never point the scheduled task at .\dist - rebuilding overwrites it.

Set-Location $PSScriptRoot

# PyInstaller logs progress to stderr; under Windows PowerShell 5.1 that would be
# promoted to a terminating error, so exit status is checked explicitly instead.
$ErrorActionPreference = 'Continue'

.\.venv\Scripts\python.exe -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --name square-drive `
  --collect-submodules uvicorn `
  --collect-data tzdata `
  --hidden-import tzdata `
  --hidden-import anyio `
  --hidden-import h11 `
  launcher.py

if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller exited with $LASTEXITCODE"; exit 1 }
if (-not (Test-Path .\dist\square-drive.exe)) { Write-Error 'Build produced no executable.'; exit 1 }

New-Item -ItemType Directory -Force .\dist\install | Out-Null
Copy-Item .\dist\square-drive.exe .\dist\install\ -Force
Copy-Item .\.env.example .\dist\install\ -Force

Write-Host "Built: $(Resolve-Path .\dist\square-drive.exe)"
Write-Host "Stage: $(Resolve-Path .\dist\install)"
