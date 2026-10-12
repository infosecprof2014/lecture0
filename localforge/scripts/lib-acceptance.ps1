# Shared helpers for the Phase 4-6 acceptance scripts. Dot-source after setting $Root:
#   . (Join-Path $PSScriptRoot 'lib-acceptance.ps1')

Add-Type -AssemblyName System.Net.Http
$Base = 'http://127.0.0.1:8000'
$Py = Join-Path $Root '.venv\Scripts\python.exe'
$OutLog = Join-Path $Root 'logs\start-core.out.log'
$ErrLog = Join-Path $Root 'logs\start-core.err.log'
$results = New-Object System.Collections.Generic.List[object]

function Add-Result([string]$Id, [string]$Name, [bool]$Pass, [string]$Detail) {
    $results.Add([pscustomobject]@{ Id = $Id; Name = $Name; Pass = $Pass; Detail = $Detail })
    $mark = if ($Pass) { 'PASS' } else { 'FAIL' }
    Write-Host ('[{0}] {1}. {2} - {3}' -f $mark, $Id, $Name, $Detail)
}

function Get-Health { try { Invoke-RestMethod -Uri "$Base/health" -TimeoutSec 5 } catch { $null } }
function Test-Ollama { try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 3 | Out-Null; $true } catch { $false } }

function Start-Core([string[]]$ExtraArgs = @()) {
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Root 'scripts\start-core.ps1')) + $ExtraArgs
    $p = Start-Process -FilePath 'powershell' -PassThru -WindowStyle Hidden -ArgumentList $argList `
        -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
    for ($i = 0; $i -lt 90; $i++) {
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

function Test-Prerequisites([string]$NeedFile) {
    if (-not (Test-Path -LiteralPath $Py)) { Write-Host "Missing $Py - run Phase 1 install first."; exit 1 }
    if ($NeedFile -and -not (Test-Path -LiteralPath (Join-Path $Root $NeedFile))) { Write-Host "Missing $Root\$NeedFile - copy the latest code first."; exit 1 }
    if (Get-Health) { Write-Host 'The core is already running on port 8000. Stop it (Ctrl+C in its window) and run this again.'; exit 1 }
    if (-not (Test-Ollama)) { Write-Host 'Ollama is not running. Start the Ollama app and run this again.'; exit 1 }
}

function Open-Stream([string]$Path, [hashtable]$Body) {
    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromMinutes(60)
    $req = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Post, "$Base$Path")
    $req.Content = New-Object System.Net.Http.StringContent(($Body | ConvertTo-Json -Compress), [Text.Encoding]::UTF8, 'application/json')
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $resp = $client.SendAsync($req, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
    $reader = New-Object IO.StreamReader($resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult(), [Text.Encoding]::UTF8)
    return [pscustomobject]@{ Client = $client; Reader = $reader; Watch = $watch; Status = [int]$resp.StatusCode }
}
function Read-Event($s) {
    while ($null -ne ($line = $s.Reader.ReadLine())) {
        if ($line.Trim()) {
            $e = $line | ConvertFrom-Json
            $e | Add-Member -NotePropertyName at_ms -NotePropertyValue $s.Watch.ElapsedMilliseconds -Force
            return $e
        }
    }
    return $null
}
function Close-Stream($s) { $s.Reader.Dispose(); $s.Client.Dispose() }

function Show-Event($e) {
    $t = '{0,6:N1}s' -f ($e.at_ms / 1000)
    $line = switch ($e.type) {
        'agent_start'   { "  $t  $($e.agent) started" + $(if ($e.step) { " (step $($e.step))" } else { '' }) }
        'agent_done'    { "  $t  $($e.agent) done: $($e.tokens) tokens, {0:N1}s" -f ($e.duration_ms / 1000) }
        'plan'          { "  $t  plan: $(@($e.steps).Count) step(s)" }
        'revise'        { "  $t  revision $($e.revision) for step $($e.step): " + (@($e.issues) -join '; ') }
        'step_done'     { "  $t  step $($e.step) finished: $($e.verdict), $($e.revisions) revision(s)" }
        'testing_start' { "  $t  writing project files and tests" }
        'test_run'      { "  $t  test run $($e.round): $($e.status), $($e.passed) passed, $($e.failed) failed (memory cap: $($e.memory_cap))" }
        'fix'           { "  $t  fixer rewrote the solution (round $($e.round))" }
        'fix_rejected'  { "  $t  fix rejected: " + (@($e.problems) -join '; ') }
        'testing_done'  { "  $t  tests: $($e.test_status) $($e.note)" }
        'task_end'      { "  $t  task $($e.status) $($e.error)" }
        default         { $null }
    }
    if ($line) { Write-Host $line }
}

# Runs one task to the end, printing progress. Returns @{ Events; TaskId; End; Status }.
function Invoke-Task([string]$Request) {
    $events = @()
    $s = Open-Stream '/tasks' @{ request = $Request }
    if ($s.Status -ne 200) {
        $body = $s.Reader.ReadToEnd()
        Close-Stream $s
        Write-Host "Task refused: HTTP $($s.Status) $body"
        return [pscustomobject]@{ Events = @(); TaskId = $null; End = $null; Status = $s.Status; Body = $body }
    }
    while ($null -ne ($e = Read-Event $s)) { $events += $e; Show-Event $e }
    Close-Stream $s
    return [pscustomobject]@{
        Events = $events
        TaskId = ($events | Where-Object { $_.type -eq 'task_start' } | Select-Object -First 1).task_id
        End = ($events | Where-Object { $_.type -eq 'task_end' } | Select-Object -First 1)
        Status = 200; Body = ''
    }
}

function Invoke-Pytest {
    Push-Location -LiteralPath $Root
    $fast = (& $Py -m pytest -q -p no:cacheprovider 2>&1 | ForEach-Object { $_.ToString() })
    $fastCode = $LASTEXITCODE
    $env:LOCALFORGE_REQUIRE_REAL = '1'
    $slow = (& $Py -m pytest -q -p no:cacheprovider -m slow 2>&1 | ForEach-Object { $_.ToString() })
    $slowCode = $LASTEXITCODE
    Remove-Item Env:\LOCALFORGE_REQUIRE_REAL
    Pop-Location
    $sum = { param($o) ($o | Where-Object { $_ -match '\d+ (passed|failed|error)' } | Select-Object -Last 1) }
    return [pscustomobject]@{
        Pass = ($fastCode -eq 0) -and ($slowCode -eq 0)
        Detail = "fast: $(& $sum $fast) | slow (real model): $(& $sum $slow)"
        Fast = $fast; Slow = $slow
    }
}

function New-Report([string]$Title) {
    $script:sb = New-Object System.Text.StringBuilder
    L "# $Title"
    L ''
    L ('Generated: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm'))
    L ''
}
function L([string]$t = '') { [void]$script:sb.AppendLine($t) }
function Add-ResultTable([string]$LastId, [string]$ReportPath) {
    L '| # | Test | Result | Detail |'
    L '|---|------|--------|--------|'
    foreach ($r in ($results | Sort-Object { [int]$_.Id })) {
        L ('| {0} | {1} | {2} | {3} |' -f $r.Id, $r.Name, $(if ($r.Pass) { 'PASS' } else { 'FAIL' }), ($r.Detail -replace '\|', '/' -replace "`r?`n", ' '))
    }
    L ('| {0} | Results saved to {1} | PASS | this file |' -f $LastId, $ReportPath)
    L ''
}
function Save-Report([string]$ReportPath, [string]$LastId) {
    New-Item -ItemType Directory -Force -Path (Split-Path $ReportPath) | Out-Null
    [IO.File]::WriteAllText($ReportPath, $script:sb.ToString(), (New-Object System.Text.UTF8Encoding($false)))
    $failed = @($results | Where-Object { -not $_.Pass }).Count
    Write-Host ''
    Write-Host "Report written: $ReportPath"
    Write-Host ("Acceptance: {0} of {1} tests passed, plus report saved (test {2})." -f ($results.Count - $failed), $results.Count, $LastId)
}
