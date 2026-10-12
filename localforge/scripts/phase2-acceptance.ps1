<#
.SYNOPSIS
    LocalForge AI - Phase 2 acceptance tests 1-8; writes docs\phase2-report.md.

.DESCRIPTION
    Starts and stops the core service itself. Test 5 stops Ollama for a few
    seconds to check the error message, then starts it again.

    powershell -NoProfile -ExecutionPolicy Bypass -File D:\LocalForgeAI\scripts\phase2-acceptance.ps1
#>

[CmdletBinding()]
param(
    [string]$Root = ''
)

# Windows PowerShell 5.1 leaves $PSScriptRoot empty inside param() defaults, so
# work out the D:\LocalForgeAI folder (parent of scripts\) here instead.
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }

$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Net.Http
$Base = 'http://127.0.0.1:8000'
$Py = Join-Path $Root '.venv\Scripts\python.exe'
$Report = Join-Path $Root 'docs\phase2-report.md'
$OutLog = Join-Path $Root 'logs\start-core.out.log'
$ErrLog = Join-Path $Root 'logs\start-core.err.log'

$results = New-Object System.Collections.Generic.List[object]
function Add-Result([string]$Id, [string]$Name, [bool]$Pass, [string]$Detail) {
    $results.Add([pscustomobject]@{ Id = $Id; Name = $Name; Pass = $Pass; Detail = $Detail })
    $mark = if ($Pass) { 'PASS' } else { 'FAIL' }
    Write-Host ('[{0}] {1}. {2} - {3}' -f $mark, $Id, $Name, $Detail)
}

function Get-Health {
    try { return Invoke-RestMethod -Uri "$Base/health" -TimeoutSec 5 } catch { return $null }
}

function Test-Ollama {
    try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}

function Start-Core {
    $p = Start-Process -FilePath 'powershell' -PassThru -WindowStyle Hidden `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Root 'scripts\start-core.ps1')) `
        -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
    for ($i = 0; $i -lt 60; $i++) {
        if (Get-Health) { return $p }
        if ($p.HasExited) { break }
        Start-Sleep -Seconds 1
    }
    return $p
}

function Stop-Core($p) {
    if ($p -and -not $p.HasExited) { & taskkill.exe /PID $p.Id /T /F | Out-Null }
    for ($i = 0; $i -lt 15 -and (Get-Health); $i++) { Start-Sleep -Seconds 1 }
}

# POST /chat and read the streamed lines as they arrive.
function Invoke-ChatStream([string]$Message) {
    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromMinutes(10)
    $json = @{ message = $Message } | ConvertTo-Json -Compress
    $req = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Post, "$Base/chat")
    $req.Content = New-Object System.Net.Http.StringContent($json, [Text.Encoding]::UTF8, 'application/json')
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $events = @(); $tokenTimes = @()
    $status = 0
    try {
        $resp = $client.SendAsync($req, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        $status = [int]$resp.StatusCode
        $reader = New-Object IO.StreamReader($resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult(), [Text.Encoding]::UTF8)
        while ($null -ne ($line = $reader.ReadLine())) {
            if (-not $line.Trim()) { continue }
            $e = $line | ConvertFrom-Json
            if ($e.type -eq 'token') { $tokenTimes += $sw.ElapsedMilliseconds }
            $events += $e
        }
        $reader.Dispose()
    } catch {
        $events += [pscustomobject]@{ type = 'error'; message = $_.Exception.Message }
    } finally {
        $client.Dispose()
    }
    return [pscustomobject]@{ Status = $status; Events = $events; TokenTimes = $tokenTimes; TotalMs = $sw.ElapsedMilliseconds }
}

Write-Host "LocalForge Phase 2 acceptance tests ($Root)"
if (-not (Test-Path -LiteralPath $Py)) { Write-Host "Missing $Py - run Phase 1 install first."; exit 1 }
if (Get-Health) { Write-Host 'The core is already running on port 8000. Stop it (Ctrl+C in its window) and run this again.'; exit 1 }
if (-not (Test-Ollama)) { Write-Host 'Ollama is not running. Start the Ollama app and run this again.'; exit 1 }
$config = Get-Content -LiteralPath (Join-Path $Root 'config\settings.json') -Raw | ConvertFrom-Json

# ---------------------------------------------------------------- 1. start
$core = Start-Core
$health = Get-Health
$errText = if (Test-Path -LiteralPath $ErrLog) { Get-Content -LiteralPath $ErrLog -Raw } else { '' }
# Match the level column case-sensitively: the logger "uvicorn.error" logs normal INFO lines.
$badLines = @(($errText -split "`r?`n") | Where-Object { $_ -cmatch '^Traceback|^\S+ \S+ (ERROR|CRITICAL) ' })
Add-Result '1' 'start-core.ps1 starts the service with no errors' ([bool]$health -and $badLines.Count -eq 0) `
    $(if (-not $health) { "service did not answer within 60 s; see $ErrLog" } elseif ($badLines.Count) { 'error lines: ' + ($badLines[0..1] -join ' / ') } else { 'running, no error lines in startup output' })

# ---------------------------------------------------------------- 2. health
$ok2 = $health -and $health.status -eq 'ok' -and $health.ollama.reachable
Add-Result '2' 'GET /health: status ok and Ollama reachable' ([bool]$ok2) `
    $(if ($health) { "status=$($health.status), ollama reachable=$($health.ollama.reachable), version=$($health.ollama.version), free RAM=$($health.free_ram_mb) MB" } else { 'no response' })

# ---------------------------------------------------------------- 3. chat
$chat = Invoke-ChatStream 'Say OK'
$done = $chat.Events | Where-Object { $_.type -eq 'done' } | Select-Object -First 1
$text = (($chat.Events | Where-Object { $_.type -eq 'token' } | ForEach-Object { $_.content }) -join '')
$firstMs = if ($chat.TokenTimes.Count) { $chat.TokenTimes[0] } else { $null }
$lastMs = if ($chat.TokenTimes.Count) { $chat.TokenTimes[-1] } else { $null }
$convId = if ($done) { $done.conversation_id } else { $null }
$ok3 = ($chat.Status -eq 200) -and $done -and $chat.TokenTimes.Count -ge 1
$detail3 = if ($ok3) {
    "reply '$($text.Trim())'; $($chat.TokenTimes.Count) pieces streamed, first after $firstMs ms, last after $lastMs ms; total $($chat.TotalMs) ms; $($done.tokens_per_s) tokens/s"
} else {
    "HTTP $($chat.Status): " + (($chat.Events | Select-Object -First 1 | ConvertTo-Json -Compress))
}
Add-Result '3' 'POST /chat "Say OK" streams back a response' ([bool]$ok3) $detail3

# Extra measurement (not an acceptance test): model now loaded, longer answer.
$warm = Invoke-ChatStream 'Count from 1 to 10, separated by commas.'
$warmDone = $warm.Events | Where-Object { $_.type -eq 'done' } | Select-Object -First 1
$warmText = (($warm.Events | Where-Object { $_.type -eq 'token' } | ForEach-Object { $_.content }) -join '').Trim()
$warmLine = if ($warmDone -and $warm.TokenTimes.Count) {
    "reply '$warmText'; $($warm.TokenTimes.Count) pieces, first after $($warm.TokenTimes[0]) ms, last after $($warm.TokenTimes[-1]) ms; total $($warm.TotalMs) ms; $($warmDone.tokens_per_s) tokens/s"
} else { "failed: HTTP $($warm.Status)" }
Write-Host "      warm chat: $warmLine"

# ---------------------------------------------------------------- 4. restart keeps history
Stop-Core $core
$core = Start-Core
$convs = $null
try { $convs = Invoke-RestMethod -Uri "$Base/conversations" -TimeoutSec 10 } catch { }
$found = $convs -and $convId -and (@($convs.conversations | Where-Object { $_.id -eq $convId }).Count -eq 1)
Add-Result '4' 'After a restart, the test 3 conversation is still listed' ([bool]$found) `
    $(if ($found) { "conversation $convId found after restart" } else { "conversation $convId not found" })

# ---------------------------------------------------------------- 6. loopback only
$listen = @(Get-NetTCPConnection -LocalPort $config.port -State Listen -ErrorAction SilentlyContinue)
$addrs = @($listen | ForEach-Object { $_.LocalAddress } | Select-Object -Unique)
$onlyLoopback = $addrs.Count -gt 0 -and @($addrs | Where-Object { $_ -notin @('127.0.0.1', '::1') }).Count -eq 0
$lanIps = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { $_.IPAddress })
$reachable = @()
foreach ($ip in $lanIps) {
    $tcp = New-Object System.Net.Sockets.TcpClient
    try { if ($tcp.ConnectAsync($ip, $config.port).Wait(2000) -and $tcp.Connected) { $reachable += $ip } } catch { } finally { $tcp.Dispose() }
}
Add-Result '6' 'Not reachable from other devices (127.0.0.1 only)' ($onlyLoopback -and $reachable.Count -eq 0) `
    ("listening on: " + ($addrs -join ', ') + "; connect via own network IPs (" + $(if ($lanIps) { $lanIps -join ', ' } else { 'none' }) + "): " + $(if ($reachable) { 'REACHABLE on ' + ($reachable -join ', ') } else { 'refused' }))

# ---------------------------------------------------------------- 7. automated tests
Push-Location -LiteralPath $Root
$fastOut = (& $Py -m pytest -q -p no:cacheprovider 2>&1 | ForEach-Object { $_.ToString() })
$fastCode = $LASTEXITCODE
$env:LOCALFORGE_REQUIRE_REAL = '1'
$slowOut = (& $Py -m pytest -q -p no:cacheprovider -m slow 2>&1 | ForEach-Object { $_.ToString() })
$slowCode = $LASTEXITCODE
Remove-Item Env:\LOCALFORGE_REQUIRE_REAL
Pop-Location
$fastSum = ($fastOut | Where-Object { $_ -match '\d+ (passed|failed|error)' } | Select-Object -Last 1)
$slowSum = ($slowOut | Where-Object { $_ -match '\d+ (passed|failed|error)' } | Select-Object -Last 1)
Add-Result '7' 'All automated tests pass' (($fastCode -eq 0) -and ($slowCode -eq 0)) "fast: $fastSum | slow (real model): $slowSum"

# ---------------------------------------------------------------- 5. Ollama stopped (run last; restarts Ollama)
Write-Host ''
Write-Host 'Test 5: stopping Ollama for a few seconds, then starting it again...' -ForegroundColor Yellow
$appExe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama app.exe'
$hadApp = [bool](Get-Process -Name 'ollama app' -ErrorAction SilentlyContinue)
Get-Process -Name 'ollama app', 'ollama' -ErrorAction SilentlyContinue | Stop-Process -Force
for ($i = 0; $i -lt 15 -and (Test-Ollama); $i++) { Start-Sleep -Seconds 1 }
$down = Invoke-ChatStream 'Say OK'
$err = $down.Events | Select-Object -First 1
$stillUp = Get-Health
$ok5 = ($down.Status -eq 503) -and ($err.error -eq 'ollama_unavailable') -and $stillUp -and $stillUp.status -eq 'ok'
Add-Result '5' 'With Ollama stopped: clear error, service keeps running' ([bool]$ok5) `
    ("HTTP $($down.Status): '$($err.message)'; /health afterwards: " + $(if ($stillUp) { $stillUp.status } else { 'NO RESPONSE' }))

$restartHow = 'not restarted'
if ($hadApp -and (Test-Path -LiteralPath $appExe)) {
    Start-Process -FilePath $appExe
    for ($i = 0; $i -lt 30 -and -not (Test-Ollama); $i++) { Start-Sleep -Seconds 1 }
    if (Test-Ollama) { $restartHow = 'Ollama app' }
}
if (-not (Test-Ollama)) {
    # The tray app may not start the server (for example from an admin window); start it directly.
    $ollamaExe = (Get-Command ollama -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if (-not $ollamaExe) { $ollamaExe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe' }
    if (Test-Path -LiteralPath $ollamaExe) {
        Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden
        for ($i = 0; $i -lt 60 -and -not (Test-Ollama); $i++) { Start-Sleep -Seconds 1 }
        if (Test-Ollama) { $restartHow = 'ollama serve (hidden window)' }
    }
}
$ollamaBack = Test-Ollama
Write-Host ("Ollama restarted: $ollamaBack ($restartHow)")
if (-not $ollamaBack) { Write-Host 'Please start Ollama from the Start menu.' -ForegroundColor Yellow }

Stop-Core $core

# ---------------------------------------------------------------- 8. report
$ordered = $results | Sort-Object { [int]$_.Id }
$sb = New-Object System.Text.StringBuilder
function L([string]$t = '') { [void]$sb.AppendLine($t) }
L '# LocalForge AI - Phase 2 report (core engine)'
L ''
L ('Generated: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm'))
L ''
L '## What was built'
L ''
L '- `core\` - Python web service (FastAPI + uvicorn) on http://127.0.0.1:8000, between the user and Ollama'
L '- Endpoints: `GET /health`, `GET /models`, `POST /chat` (streams NDJSON lines), `GET /conversations`, `GET /conversations/{id}`'
L '- SQLite history in `database\localforge.db` (tables `conversations`, `messages`)'
L '- Settings in `config\settings.json`; rotating log `logs\core.log` (5 MB x 5)'
L '- Safety: one chat at a time (HTTP 429), free-RAM check before loading a model (HTTP 503), one model kept in memory, clear error when Ollama is down (HTTP 503), partial answers saved if a stream breaks'
L '- `scripts\start-core.ps1`, `tests\` (fast tests with a fake Ollama + one slow real-model test)'
L ''
L '## Acceptance tests'
L ''
L '| # | Test | Result | Detail |'
L '|---|------|--------|--------|'
foreach ($r in $ordered) { L ('| {0} | {1} | {2} | {3} |' -f $r.Id, $r.Name, $(if ($r.Pass) { 'PASS' } else { 'FAIL' }), ($r.Detail -replace '\|', '/')) }
L ('| 8 | Results saved to ' + $Report + ' | PASS | this file |')
L ''
L '## /chat response time (test 3, "Say OK")'
L ''
if ($ok3) {
    L "- Time to first streamed piece: $firstMs ms"
    L "- Total time: $($chat.TotalMs) ms"
    L "- Generation speed reported by Ollama: $($done.tokens_per_s) tokens/s ($($done.tokens) tokens)"
    L "- This first chat includes loading the model from disk into memory."
    L "- Warm chat (model already loaded, 'Count from 1 to 10'): $warmLine"
} else { L '- Not measured: test 3 failed.' }
L ''
L '## Known limits'
L ''
L '- One chat at a time by design; a long answer (10-30 s on this CPU) makes other requests wait with HTTP 429.'
L '- The free-RAM check is a safeguard, not a guarantee: other programs can still use memory after a chat starts.'
L '- Conversation history sent to the model is capped by `max_history_messages` (default 20); very long chats lose early context.'
L '- No authentication: anyone using this Windows account can call the API. It is not reachable from the network.'
L ('- Test 5 stops and restarts Ollama. Ollama restarted afterwards: ' + $ollamaBack + ' (' + $restartHow + ')')
L ''
L '## Repeat each test'
L ''
L '```powershell'
L '# Start the service (leave this window open; Ctrl+C stops it)'
L "powershell -NoProfile -ExecutionPolicy Bypass -File $Root\scripts\start-core.ps1"
L '# In a second window:'
L '# 2. Health'
L 'Invoke-RestMethod http://127.0.0.1:8000/health | ConvertTo-Json -Depth 4'
L '# 3. Chat (streamed lines)'
L 'curl.exe -N -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" -d "{\"message\":\"Say OK\"}"'
L '# 4. History (restart the service first)'
L 'Invoke-RestMethod http://127.0.0.1:8000/conversations | ConvertTo-Json -Depth 4'
L '# 5. Quit Ollama from the tray, run the chat command again: expect "Ollama is not running"'
L '# 6. Listening address must be 127.0.0.1 only'
L 'Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object LocalAddress, LocalPort'
L '# 7. Automated tests'
L "cd $Root; .\.venv\Scripts\python.exe -m pytest -q; .\.venv\Scripts\python.exe -m pytest -q -m slow"
L '# All of the above, with this report'
L "powershell -NoProfile -ExecutionPolicy Bypass -File $Root\scripts\phase2-acceptance.ps1"
L '```'
L ''
L '## Automated test output'
L ''
L '```'
foreach ($l in ($fastOut | Select-Object -Last 15)) { L $l }
L '--- slow ---'
foreach ($l in ($slowOut | Select-Object -Last 10)) { L $l }
L '```'

New-Item -ItemType Directory -Force -Path (Split-Path $Report) | Out-Null
[IO.File]::WriteAllText($Report, $sb.ToString(), (New-Object System.Text.UTF8Encoding($false)))
$failed = @($results | Where-Object { -not $_.Pass }).Count
Write-Host ''
Write-Host "Report written: $Report"
Write-Host ("Acceptance: {0} of {1} tests passed, plus report saved (test 8)." -f ($results.Count - $failed), $results.Count)
