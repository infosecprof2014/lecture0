<#
.SYNOPSIS
    Stop LocalForge starting automatically at sign-in (removes the Startup shortcut).
#>
[CmdletBinding()]
param()
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'LocalForge AI.lnk'
if (Test-Path -LiteralPath $lnk) { Remove-Item -LiteralPath $lnk; Write-Host "Auto-start off (removed $lnk)" }
else { Write-Host 'Auto-start was already off.' }
