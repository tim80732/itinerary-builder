# Run Itinerary Builder on this PC, no Docker needed.
#
#   powershell -ExecutionPolicy Bypass -File .\run-local.ps1
#
# Data goes in .\data next to this script (same layout the container uses),
# so anything you create here carries over if you copy the folder to Unraid.
# Ctrl+C stops the server.

param(
    [int]$Port = 8099,
    [string]$Password = ""     # set to require a login, like APP_PASSWORD on the server
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# --- find a real Python (the Microsoft Store stub on PATH is useless) --------
$py = $null
foreach ($name in 'py', 'python') {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notmatch '\\WindowsApps\\' } | Select-Object -First 1
    if ($cmd) { $py = $cmd.Source; break }
}
if (-not $py) {
    Write-Error "No Python found. Install Python 3.11+ from python.org and re-run."
}
Write-Host "Python: $py"

# --- make sure the dependencies are present ---------------------------------
& $py -c "import flask, waitress" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing dependencies..." -ForegroundColor Yellow
    & $py -m pip install --quiet --disable-pip-version-check --no-warn-script-location -r requirements.txt
}
& $py -c "import PIL, pypdfium2" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Note: Pillow / pypdfium2 missing - PDF uploads will not render page images." -ForegroundColor Yellow
}

# --- run --------------------------------------------------------------------
$env:DATA_DIR = Join-Path $PSScriptRoot 'data'
$env:PORT = "$Port"
$env:APP_PASSWORD = $Password

Write-Host ""
Write-Host "Itinerary Builder -> http://localhost:$Port" -ForegroundColor Green
Write-Host "Data folder       -> $($env:DATA_DIR)"
Write-Host "Press Ctrl+C to stop."
Write-Host ""

& $py -m app.main
