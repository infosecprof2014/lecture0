<#
.SYNOPSIS
    Start the LocalForge AI core service on http://127.0.0.1:8000.

.DESCRIPTION
    Activates D:\LocalForgeAI\.venv and runs "python -m core". Stop it with Ctrl+C.

    powershell -NoProfile -ExecutionPolicy Bypass -File D:\LocalForgeAI\scripts\start-core.ps1
#>

[CmdletBinding()]
param(
    [string]$Root = '',
    # Make a backup first if the newest one is older than auto_backup_hours (used by auto-start).
    [switch]$Backup
)

# Windows PowerShell 5.1 leaves $PSScriptRoot empty inside param() defaults, so
# work out the D:\LocalForgeAI folder (parent of scripts\) here instead.
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }

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
if ($Backup) {
    python -m core.backup auto
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Automatic backup failed; starting anyway. See the message above.' }
}
Write-Host "Starting LocalForge core from $Root (Ctrl+C to stop)..."
Write-Host "Web interface: http://127.0.0.1:8000"
python -m core
exit $LASTEXITCODE
