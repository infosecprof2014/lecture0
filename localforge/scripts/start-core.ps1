<#
.SYNOPSIS
    Start the LocalForge AI core service on http://127.0.0.1:8000.

.DESCRIPTION
    Activates D:\LocalForgeAI\.venv and runs "python -m core". Stop it with Ctrl+C.

    powershell -NoProfile -ExecutionPolicy Bypass -File D:\LocalForgeAI\scripts\start-core.ps1
#>

[CmdletBinding()]
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

$activate = Join-Path $Root '.venv\Scripts\Activate.ps1'
if (-not (Test-Path -LiteralPath $activate)) {
    Write-Error "Virtual environment not found at $Root\.venv. Run the Phase 1 install.ps1 first."
    exit 1
}
if (-not (Test-Path -LiteralPath (Join-Path $Root 'core\app.py'))) {
    Write-Error "Core code not found in $Root\core."
    exit 1
}

. $activate
$env:LOCALFORGE_ROOT = $Root
Set-Location -LiteralPath $Root
Write-Host "Starting LocalForge core from $Root (Ctrl+C to stop)..."
python -m core
exit $LASTEXITCODE
