<#
.SYNOPSIS
    Start LocalForge automatically when you sign in to Windows (hidden window, with a daily
    backup first). Uses a shortcut in your Startup folder: no admin rights, easy to see and remove.

    powershell -NoProfile -ExecutionPolicy Bypass -File D:\LocalForgeAI\scripts\register-autostart.ps1
    Undo: scripts\unregister-autostart.ps1
#>
[CmdletBinding()]
param([string]$Root = '')
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'LocalForge AI.lnk'
$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($lnk)
$s.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$s.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Root\scripts\start-core.ps1`" -Backup"
$s.WorkingDirectory = $Root
$s.WindowStyle = 7  # minimized
$s.Description = 'Start the LocalForge AI core service'
$s.Save()
Write-Host "Auto-start on: $lnk"
Write-Host 'LocalForge will start (hidden) the next time you sign in. Web interface: http://127.0.0.1:8000'
