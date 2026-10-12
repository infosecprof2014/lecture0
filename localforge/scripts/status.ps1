<#
.SYNOPSIS
    Show the health of LocalForge: settings, core service, Ollama, model, database, backups,
    disk, memory and auto-start.

    powershell -NoProfile -ExecutionPolicy Bypass -File D:\LocalForgeAI\scripts\status.ps1
#>
[CmdletBinding()]
param([string]$Root = '')
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$py = Join-Path $Root '.venv\Scripts\python.exe'
Push-Location -LiteralPath $Root
try { & $py -m core.maintenance status; $code = $LASTEXITCODE } finally { Pop-Location }
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'LocalForge AI.lnk'
$auto = if (Test-Path -LiteralPath $lnk) { 'on (starts at sign-in)' } else { 'off (turn on with scripts\register-autostart.ps1)' }
Write-Host ("[--  ] {0,-13} {1}" -f 'Auto-start', $auto)
exit $code
