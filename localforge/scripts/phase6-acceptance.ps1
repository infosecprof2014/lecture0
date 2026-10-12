<#
.SYNOPSIS
    LocalForge AI - Phase 6 acceptance tests 1-10 (hardening); writes docs\phase6-report.md.

    Makes one real backup of your data (kept in D:\LocalForgeAI\backups). Turns auto-start on and
    off again to test it, unless you pass -KeepAutostart.

    powershell -NoProfile -ExecutionPolicy Bypass -File D:\LocalForgeAI\scripts\phase6-acceptance.ps1
#>

[CmdletBinding()]
param([string]$Root = '', [switch]$KeepAutostart)
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$ErrorActionPreference = 'Continue'
. (Join-Path (Join-Path $Root 'scripts') 'lib-acceptance.ps1')
$Report = Join-Path $Root 'docs\phase6-report.md'
$Scripts = Join-Path $Root 'scripts'
function Run-Script([string]$Name, [string[]]$More = @()) {
    $out = (& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Scripts $Name) @More 2>&1 | ForEach-Object { $_.ToString() })
    return [pscustomobject]@{ Code = $LASTEXITCODE; Text = ($out -join "`n") }
}

Write-Host "LocalForge Phase 6 acceptance tests ($Root)"
Test-Prerequisites 'core\backup.py'
Push-Location -LiteralPath $Root
& $Py -m core.maintenance merge-settings
Pop-Location

# ---------------------------------------------------------------- 2. backup + verify
$b = Run-Script 'backup.ps1'
$zip = if ($b.Text -match 'Backup created: (.+\.zip)') { $Matches[1].Trim() } else { $null }
$v = if ($zip) { Run-Script 'backup.ps1' @('-Verify', $zip) } else { $null }
$vInfo = $null
try { if ($v) { $vInfo = $v.Text | ConvertFrom-Json } } catch { }
$ok2 = $zip -and $vInfo -and $vInfo.database_ok -and $vInfo.has_settings -and $vInfo.prompts -ge 5
Add-Result '2' 'A backup is made and passes verification' ([bool]$ok2) `
    $(if ($vInfo) { "$(Split-Path $zip -Leaf): database ok=$($vInfo.database_ok), $($vInfo.rows.conversations) conversation(s), $($vInfo.rows.tasks) task(s), settings=$($vInfo.has_settings), $($vInfo.prompts) prompt file(s), $($vInfo.project_files) project file(s)" } else { "backup failed: $($b.Text) $($v.Text)" })

# ---------------------------------------------------------------- 3. restore round trip (temporary copy)
Push-Location -LiteralPath $Root
$st = (& $Py -m core.backup selftest 2>&1 | ForEach-Object { $_.ToString() }) -join ''
Pop-Location
$stInfo = $null; try { $stInfo = $st | ConvertFrom-Json } catch { }
Add-Result '3' 'Restore brings back database, prompts and projects (tested on a temporary copy)' ([bool]($stInfo -and $stInfo.ok)) `
    $(if ($stInfo) { ($stInfo.checks.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ', ' } else { $st })

# ---------------------------------------------------------------- 4. retention
$config = Get-Content -LiteralPath (Join-Path $Root 'config\settings.json') -Raw | ConvertFrom-Json
$keep = if ($config.keep_backups) { [int]$config.keep_backups } else { 10 }
$count = @(Get-ChildItem -LiteralPath (Join-Path $Root 'backups') -Filter 'localforge-*.zip' -ErrorAction SilentlyContinue).Count
Add-Result '4' "Old backups are pruned (at most $keep kept)" ($count -ge 1 -and $count -le $keep) "$count backup(s) in $Root\backups"

# ---------------------------------------------------------------- 5. auto-start on / off
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'LocalForge AI.lnk'
$hadAuto = Test-Path -LiteralPath $lnk
[void](Run-Script 'register-autostart.ps1')
$created = Test-Path -LiteralPath $lnk
$target = ''
if ($created) { $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk); $target = "$($sc.TargetPath) $($sc.Arguments)" }
$pointsRight = $target -match [regex]::Escape("$Root\scripts\start-core.ps1") -and $target -match '-Backup' -and $target -match 'Hidden'
if (-not $KeepAutostart -and -not $hadAuto) { [void](Run-Script 'unregister-autostart.ps1') }
$removedOk = $KeepAutostart -or $hadAuto -or -not (Test-Path -LiteralPath $lnk)
Add-Result '5' 'Auto-start can be turned on (hidden, with backup) and off' ($created -and $pointsRight -and $removedOk) `
    ("shortcut created: $created; runs start-core.ps1 -Backup hidden: $pointsRight; " + $(if ($KeepAutostart -or $hadAuto) { 'left ON' } else { "turned off again: $removedOk" }))

# ---------------------------------------------------------------- 6. start the way auto-start does
$core = Start-Core @('-Backup')
$health = Get-Health
$autoLine = if (Test-Path -LiteralPath $OutLog) { (Get-Content -LiteralPath $OutLog | Where-Object { $_ -match 'Auto backup' } | Select-Object -Last 1) } else { '' }
$page = $null; try { $page = Invoke-WebRequest -UseBasicParsing -Uri "$Base/" -TimeoutSec 10 } catch { }
$chat = $null
try {
    $s = Open-Stream '/chat' @{ message = 'Say OK' }
    $events = @(); while ($null -ne ($e = Read-Event $s)) { $events += $e }; Close-Stream $s
    $chat = $events | Where-Object { $_.type -eq 'done' } | Select-Object -First 1
} catch { }
Add-Result '6' 'start-core.ps1 -Backup starts everything: page and chat work' ([bool]($health -and $page -and $page.StatusCode -eq 200 -and $chat)) `
    ("health: $($health.status); page: $($page.StatusCode); chat: " + $(if ($chat) { "$($chat.tokens) tokens" } else { 'no answer' }) + "; $autoLine")

# ---------------------------------------------------------------- 7. status
$status = Run-Script 'status.ps1'
Write-Host $status.Text
$fails = @($status.Text -split "`n" | Where-Object { $_ -match '^\[FAIL\]' })
Add-Result '7' 'status.ps1 reports every part healthy' ($status.Code -eq 0 -and $fails.Count -eq 0 -and $status.Text -match 'Core service\s+running') `
    $(if ($fails) { 'problems: ' + ($fails -join ' / ') } else { (@($status.Text -split "`n" | Where-Object { $_ -match '^\[' }) | ForEach-Object { $_.Trim() }) -join ' / ' })
Stop-Core $core

# ---------------------------------------------------------------- 8. update check
$u = Run-Script 'update.ps1' @('-CheckOnly')
Add-Result '8' 'update.ps1 can check for a new version' ($u.Code -eq 0 -and $u.Text -match 'Up to date|Update available|No source copy') ($u.Text.Trim() -replace "`r?`n", ' ')

# ---------------------------------------------------------------- 9. user guide
$guide = Join-Path $Root 'docs\USER-GUIDE.md'
$gtext = if (Test-Path -LiteralPath $guide) { Get-Content -LiteralPath $guide -Raw } else { '' }
$missing = @('start-core.ps1', 'status.ps1', 'backup.ps1', 'restore.ps1', 'register-autostart.ps1', 'unregister-autostart.ps1', 'update.ps1', 'http://127.0.0.1:8000', 'Troubleshooting') |
           Where-Object { $gtext -notmatch [regex]::Escape($_) }
Add-Result '9' 'User guide covers starting, using, every script and troubleshooting' ($gtext -and $missing.Count -eq 0) `
    $(if ($missing) { 'missing: ' + ($missing -join ', ') } else { "$guide" })

# ---------------------------------------------------------------- 1. automated tests
Write-Host ''
Write-Host 'Running automated tests (fast, then real model)...'
$py = Invoke-Pytest
Add-Result '1' 'All automated tests pass' $py.Pass $py.Detail

# ---------------------------------------------------------------- 10. report
New-Report 'LocalForge AI - Phase 6 report (hardening)'
L '## What was built'
L ''
L '- `core\backup.py` + `scripts\backup.ps1` / `restore.ps1`: one zip with database (consistent snapshot, safe while running), settings, prompts and projects; verify; keep newest 10; restore makes a safety backup first and refuses while the core runs'
L '- `start-core.ps1 -Backup`: backup first if the newest is older than 24 h'
L '- `register-autostart.ps1` / `unregister-autostart.ps1`: Startup-folder shortcut (no admin), hidden, with backup'
L '- `core\maintenance.py` + `scripts\status.ps1`: health of settings, service, Ollama, model, database, backups, disk, memory, auto-start'
L '- `scripts\update.ps1`: fetch, copy without touching data/settings, add new settings, ask before installing packages, run tests'
L '- `docs\USER-GUIDE.md`'
L ''
L '## Acceptance tests'
L ''
Add-ResultTable '10' $Report
L '## Status at the end of the run'
L ''
L '```'
L $status.Text
L '```'
L ''
L '## Known limits'
L ''
L '- Backups stay on the same D: drive: copy `D:\LocalForgeAI\backups` to a USB stick or another disk now and then.'
L '- Auto-start uses a hidden PowerShell window started at sign-in; it does not restart LocalForge if it crashes. Run status.ps1 if the page does not open.'
L '- update.ps1 follows the development branch on GitHub.'
L ''
L '## Repeat each test'
L ''
L '```powershell'
L "cd $Root\scripts"
L '.\backup.ps1; .\backup.ps1 -List'
L '..\.venv\Scripts\python.exe -m core.backup selftest'
L '.\register-autostart.ps1; .\unregister-autostart.ps1'
L '.\status.ps1'
L '.\update.ps1 -CheckOnly'
L "powershell -NoProfile -ExecutionPolicy Bypass -File $Root\scripts\phase6-acceptance.ps1"
L '```'
Save-Report $Report '10'
