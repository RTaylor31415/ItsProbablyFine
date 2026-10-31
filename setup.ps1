$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Install Node.js 22 or newer, then reopen PowerShell.' }
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Install Python 3.11 or newer with the Windows Python launcher.' }
& py -3 -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    & py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment.' }
}
& ./.venv/Scripts/python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& ./.venv/Scripts/python.exe -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Validation failed.' }
Write-Host 'Setup complete. Run .\start.ps1 to launch Local FAI.'
