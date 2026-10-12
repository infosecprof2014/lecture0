<#
.SYNOPSIS
    Back up LocalForge (database, settings, agent prompts, projects) to D:\LocalForgeAI\backups.
    Safe while the core is running. Keeps the newest 10 (setting keep_backups).

    powershell -NoProfile -ExecutionPolicy Bypass -File D:\LocalForgeAI\scripts\backup.ps1
    ... backup.ps1 -List          list backups
    ... backup.ps1 -Verify <zip>  check a backup
#>
[CmdletBinding()]
param([string]$Root = '', [switch]$List, [string]$Verify = '')
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$py = Join-Path $Root '.venv\Scripts\python.exe'
Push-Location -LiteralPath $Root
try {
    if ($List) { & $py -m core.backup list }
    elseif ($Verify) { & $py -m core.backup verify $Verify }
    else { & $py -m core.backup create }
    $code = $LASTEXITCODE
} finally { Pop-Location }
exit $code
