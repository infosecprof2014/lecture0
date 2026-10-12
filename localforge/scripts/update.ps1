<#
.SYNOPSIS
    Update LocalForge to the latest code: fetch, copy (your settings, data, backups and projects
    are never overwritten), add any new settings, install new packages only after you type yes,
    then run the fast tests.

    powershell -NoProfile -ExecutionPolicy Bypass -File D:\LocalForgeAI\scripts\update.ps1
    ... update.ps1 -CheckOnly     only show whether an update is available
#>
[CmdletBinding()]
param(
    [string]$Root = '',
    [string]$Source = "$HOME\LocalForge-src",
    [string]$Branch = 'claude/localforge-env-setup-tgrkrl',
    [switch]$CheckOnly
)
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$py = Join-Path $Root '.venv\Scripts\python.exe'
$repo = 'https://github.com/infosecprof2014/lecture0'

if (-not (Test-Path -LiteralPath (Join-Path $Source '.git'))) {
    if ($CheckOnly) { Write-Host "No source copy at $Source yet; an update would download it."; exit 0 }
    git clone --depth 1 -b $Branch $repo $Source
    if ($LASTEXITCODE -ne 0) { Write-Error 'Could not download the code.'; exit 1 }
}
git -C $Source fetch --depth 1 origin $Branch 2>&1 | Out-Null
$local = (git -C $Source rev-parse HEAD).Trim()
$remote = (git -C $Source rev-parse FETCH_HEAD).Trim()
if ($CheckOnly) {
    if ($local -eq $remote) { Write-Host "Up to date ($($local.Substring(0,7)))." }
    else { Write-Host "Update available: $($local.Substring(0,7)) -> $($remote.Substring(0,7))" }
    exit 0
}
git -C $Source reset --hard FETCH_HEAD | Out-Null

$req = Join-Path $Root 'config\requirements.txt'
$oldReq = if (Test-Path -LiteralPath $req) { Get-Content -LiteralPath $req -Raw } else { '' }
robocopy (Join-Path $Source 'localforge') $Root /E /XF settings.json /XD database logs backups projects .venv __pycache__ /NJH /NJS /NP /NFL /NDL | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Error "Copying files failed (robocopy code $LASTEXITCODE)."; exit 1 }
$settingsFile = Join-Path $Root 'config\settings.json'
if (-not (Test-Path -LiteralPath $settingsFile)) { Copy-Item (Join-Path $Source 'localforge\config\settings.json') $settingsFile }
Push-Location -LiteralPath $Root
& $py -m core.maintenance merge-settings
$newReq = Get-Content -LiteralPath $req -Raw
if ($newReq -ne $oldReq) {
    Write-Host ''
    Write-Host 'The required Python packages changed:' -ForegroundColor Yellow
    Compare-Object ($oldReq -split "`r?`n") ($newReq -split "`r?`n") | Where-Object { $_.InputObject -notmatch '^\s*(#|$)' } |
        ForEach-Object { '  {0} {1}' -f $(if ($_.SideIndicator -eq '=>') { 'new:' } else { 'old:' }), $_.InputObject }
    Write-Host 'Source: PyPI (pypi.org), installed only into D:\LocalForgeAI\.venv'
    if ((Read-Host 'Type yes to install them') -eq 'yes') { & $py -m pip install -r $req }
    else { Write-Host 'Skipped. LocalForge may not start until they are installed.' -ForegroundColor Yellow }
}
& $py -m pytest -q -p no:cacheprovider
$code = $LASTEXITCODE
Pop-Location
Write-Host ''
Write-Host $(if ($code -eq 0) { "Updated to $($remote.Substring(0,7)); fast tests pass. Restart the core to use it." } else { 'Updated, but some tests failed - see above.' })
exit $code
