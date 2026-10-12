<#
.SYNOPSIS
    Restore LocalForge from a backup zip. Stop the core first. Your current data is backed up
    automatically before anything is replaced, so a restore can itself be undone.

    powershell -NoProfile -ExecutionPolicy Bypass -File D:\LocalForgeAI\scripts\restore.ps1 -File D:\LocalForgeAI\backups\localforge-20261012-070000.zip
#>
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$File, [string]$Root = '')
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$py = Join-Path $Root '.venv\Scripts\python.exe'
Push-Location -LiteralPath $Root
try { & $py -m core.backup restore $File; $code = $LASTEXITCODE } finally { Pop-Location }
exit $code
