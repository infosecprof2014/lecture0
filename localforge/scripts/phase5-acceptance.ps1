<#
.SYNOPSIS
    LocalForge AI - Phase 5 acceptance tests 1-8 (web interface); writes docs\phase5-report.md.

    powershell -NoProfile -ExecutionPolicy Bypass -File D:\LocalForgeAI\scripts\phase5-acceptance.ps1
#>

[CmdletBinding()]
param([string]$Root = '')
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$ErrorActionPreference = 'Continue'
. (Join-Path (Join-Path $Root 'scripts') 'lib-acceptance.ps1')
$Report = Join-Path $Root 'docs\phase5-report.md'

Write-Host "LocalForge Phase 5 acceptance tests ($Root)"
Test-Prerequisites 'ui\index.html'
$core = Start-Core
if (-not (Get-Health)) { Write-Host "The core did not start. See $ErrLog"; exit 1 }

function Send-Raw([string]$Method, [string]$Path, [hashtable]$Headers, [string]$Body) {
    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromMinutes(5)
    $req = New-Object System.Net.Http.HttpRequestMessage((New-Object System.Net.Http.HttpMethod($Method)), "$Base$Path")
    foreach ($k in $Headers.Keys) {
        if ($k -eq 'Host') { $req.Headers.Host = $Headers[$k] } else { [void]$req.Headers.TryAddWithoutValidation($k, $Headers[$k]) }
    }
    if ($Body) { $req.Content = New-Object System.Net.Http.StringContent($Body, [Text.Encoding]::UTF8, 'application/json') }
    try {
        $resp = $client.SendAsync($req).GetAwaiter().GetResult()
        $text = $resp.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        return [pscustomobject]@{ Status = [int]$resp.StatusCode; Body = $text; Type = "$($resp.Content.Headers.ContentType)" }
    } finally { $client.Dispose() }
}

# ---------------------------------------------------------------- 2. page served
$page = Send-Raw 'GET' '/' @{} $null
$js = Send-Raw 'GET' '/ui/app.js' @{} $null
$css = Send-Raw 'GET' '/ui/style.css' @{} $null
$ok2 = $page.Status -eq 200 -and $page.Type -like 'text/html*' -and $js.Status -eq 200 -and $css.Status -eq 200
Add-Result '2' 'The page and its files are served at http://127.0.0.1:8000' $ok2 `
    "/: $($page.Status) $($page.Type); app.js: $($js.Status); style.css: $($css.Status)"

# ---------------------------------------------------------------- 3. real browser (Edge, hidden)
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if ($edge) {
    $profileDir = Join-Path $env:TEMP 'localforge-edge-check'
    $dom = (& $edge --headless=new --disable-gpu --no-first-run "--user-data-dir=$profileDir" `
            --virtual-time-budget=8000 --dump-dom 'http://127.0.0.1:8000/' 2>$null) -join "`n"
    Remove-Item -LiteralPath $profileDir -Recurse -Force -ErrorAction SilentlyContinue
    $ready = $dom -match 'data-ready="1"'
    $statusText = if ($dom -match '<p id="status"[^>]*>([^<]*)</p>') { $Matches[1] } else { '' }
    Add-Result '3' 'Microsoft Edge loads the page and its script runs' ($ready -and $statusText -match 'Ollama') `
        "script ready: $ready; status line in the browser: '$statusText'"
} else {
    Add-Result '3' 'Microsoft Edge loads the page and its script runs' $false 'Edge not found; open http://127.0.0.1:8000 by hand to check'
}

# ---------------------------------------------------------------- 4. host check (DNS rebinding)
$evilHost = Send-Raw 'GET' '/health' @{ Host = 'evil.example' } $null
$goodHost = Send-Raw 'GET' '/health' @{ Host = 'localhost:8000' } $null
Add-Result '4' 'Requests addressed to another host name are refused' ($evilHost.Status -eq 400 -and $goodHost.Status -eq 200) `
    "Host evil.example: HTTP $($evilHost.Status); Host localhost: HTTP $($goodHost.Status)"

# ---------------------------------------------------------------- 5. origin check (cross-site requests)
$evilPost = Send-Raw 'POST' '/chat' @{ Origin = 'https://evil.example' } '{"message":"hi"}'
$okPost = Send-Raw 'POST' '/chat' @{ Origin = 'http://127.0.0.1:8000' } '{"message":"Say OK"}'
$streamed = ($okPost.Body -split "`n" | Where-Object { $_ -match '"type": "token"' }).Count
Add-Result '5' 'Other websites cannot start chats; this page can' ($evilPost.Status -eq 403 -and $okPost.Status -eq 200 -and $streamed -ge 1) `
    "from evil.example: HTTP $($evilPost.Status); from this page: HTTP $($okPost.Status), $streamed streamed piece(s)"

# ---------------------------------------------------------------- 6. offline: nothing loaded from the internet
$external = @()
foreach ($f in 'index.html', 'app.js', 'style.css') {
    $text = Get-Content -LiteralPath (Join-Path $Root "ui\$f") -Raw
    if ($text -match '(src|href)\s*=\s*"https?://|url\(\s*["'']?https?://|fetch\(\s*["'']https?://|import\s.+https?://') { $external += $f }
}
Add-Result '6' 'The page loads nothing from the internet (works offline)' ($external.Count -eq 0) `
    $(if ($external) { 'external references in: ' + ($external -join ', ') } else { 'no external scripts, styles, fonts or requests' })

# ---------------------------------------------------------------- 7. still local only
$listen = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $_.LocalAddress } | Select-Object -Unique)
Add-Result '7' 'The service still listens on 127.0.0.1 only' ($listen.Count -gt 0 -and @($listen | Where-Object { $_ -notin @('127.0.0.1', '::1') }).Count -eq 0) `
    ('listening on: ' + ($listen -join ', '))
Stop-Core $core

# ---------------------------------------------------------------- 1. automated tests
Write-Host ''
Write-Host 'Running automated tests (fast, then real model)...'
$py = Invoke-Pytest
Add-Result '1' 'All automated tests pass' $py.Pass $py.Detail

# ---------------------------------------------------------------- 8. report
New-Report 'LocalForge AI - Phase 5 report (web interface)'
L '## What was built'
L ''
L '- `ui\index.html`, `ui\app.js`, `ui\style.css` - one page at http://127.0.0.1:8000 with Chat (streamed answers, conversation list, Stop) and Tasks (live progress, Cancel, plan, steps, code, reviewer verdicts, test runs, files)'
L '- Plain HTML/CSS/JavaScript: no Node.js, no build step, nothing from the internet; light and dark follow Windows'
L '- Model output is shown as text only (never interpreted as HTML)'
L '- Browser protections in the core: unknown Host names refused (DNS rebinding), POSTs from other websites refused (cross-site requests)'
L ''
L '## Acceptance tests'
L ''
Add-ResultTable '8' $Report
L '## Known limits'
L ''
L '- No login: anyone using this Windows account can open the page. Other devices cannot reach it.'
L '- One chat or task at a time; while a task runs, chat shows "busy".'
L '- The page does not reconnect to a task that is already running if you reload it; the task carries on and its result appears under Tasks.'
L ''
L '## Use it'
L ''
L '```powershell'
L "powershell -NoProfile -ExecutionPolicy Bypass -File $Root\scripts\start-core.ps1"
L '# then open http://127.0.0.1:8000 in your browser'
L '```'
L ''
L '## Repeat each test'
L ''
L '```powershell'
L "powershell -NoProfile -ExecutionPolicy Bypass -File $Root\scripts\phase5-acceptance.ps1"
L '```'
Save-Report $Report '8'
Write-Host ''
Write-Host 'To use the web interface: start the service with scripts\start-core.ps1, then open http://127.0.0.1:8000'
