$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) { throw 'Run .\setup.ps1 first.' }
$env:FAI_PYTHON = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not $env:TESSERACT_CMD -and -not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    $candidate = Join-Path $env:ProgramFiles 'Tesseract-OCR/tesseract.exe'
    if (Test-Path $candidate) { $env:TESSERACT_CMD = $candidate }
    else { throw 'Install Tesseract OCR with English language data, or set TESSERACT_CMD to tesseract.exe.' }
}
Write-Host 'Open http://127.0.0.1:3210 in your browser after the server starts.'
& node app/server.mjs
