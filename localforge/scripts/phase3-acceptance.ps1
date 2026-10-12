<#
.SYNOPSIS
    LocalForge AI - Phase 3 acceptance tests 1-10 (agents); writes docs\phase3-report.md.

.DESCRIPTION
    Starts and stops the core service itself and runs two real agent tasks with the
    local model (the first takes several minutes on a slow CPU). Generated code is only
    parsed to check it is valid Python; it is never executed.

    powershell -NoProfile -ExecutionPolicy Bypass -File D:\LocalForgeAI\scripts\phase3-acceptance.ps1
#>

[CmdletBinding()]
param(
    [string]$Root = '',
    [string]$Request = 'Write a Python function is_palindrome(s) that ignores case, spaces and punctuation.',
    [string]$CancelRequest = 'Write a Python class Stack with push, pop, peek and is_empty methods.'
)

# Windows PowerShell 5.1 leaves $PSScriptRoot empty inside param() defaults.
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }

$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Net.Http
$Base = 'http://127.0.0.1:8000'
$Py = Join-Path $Root '.venv\Scripts\python.exe'
$Report = Join-Path $Root 'docs\phase3-report.md'
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

# Streamed POST: open it, then read one NDJSON event at a time.
function Open-Stream([string]$Path, [hashtable]$Body) {
    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromMinutes(45)
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
    $line = switch ($e.type) {
        'agent_start' { "  {0,6:N1}s  {1} started{2}" -f ($e.at_ms / 1000), $e.agent, $(if ($e.step) { " (step $($e.step))" } else { '' }) }
        'agent_done'  { "  {0,6:N1}s  {1} done: {2} tokens, {3:N1}s" -f ($e.at_ms / 1000), $e.agent, $e.tokens, ($e.duration_ms / 1000) }
        'plan'        { "  {0,6:N1}s  plan: {1} step(s)" -f ($e.at_ms / 1000), @($e.steps).Count }
        'revise'      { "  {0,6:N1}s  revision {1} for step {2}: {3}" -f ($e.at_ms / 1000), $e.revision, $e.step, (@($e.issues) -join '; ') }
        'step_done'   { "  {0,6:N1}s  step {1} finished: {2}, {3} revision(s)" -f ($e.at_ms / 1000), $e.step, $e.verdict, $e.revisions }
        'task_end'    { "  {0,6:N1}s  task {1}" -f ($e.at_ms / 1000), $e.status }
        default       { $null }
    }
    if ($line) { Write-Host $line }
}

Write-Host "LocalForge Phase 3 acceptance tests ($Root)"
if (-not (Test-Path -LiteralPath $Py)) { Write-Host "Missing $Py - run Phase 1 install first."; exit 1 }
if (-not (Test-Path -LiteralPath (Join-Path $Root 'agents\orchestrator.py'))) { Write-Host "Missing $Root\agents - copy the Phase 3 code first."; exit 1 }
if (Get-Health) { Write-Host 'The core is already running on port 8000. Stop it (Ctrl+C in its window) and run this again.'; exit 1 }
if (-not (Test-Ollama)) { Write-Host 'Ollama is not running. Start the Ollama app and run this again.'; exit 1 }
$config = Get-Content -LiteralPath (Join-Path $Root 'config\settings.json') -Raw | ConvertFrom-Json
$maxSteps = if ($config.max_plan_steps) { [int]$config.max_plan_steps } else { 5 }
$maxRev = if ($null -ne $config.max_revisions) { [int]$config.max_revisions } else { 2 }

$core = Start-Core
if (-not (Get-Health)) {
    Write-Host "The core service did not start. See $ErrLog"
    exit 1
}

# ---------------------------------------------------------------- task 1 (tests 2-5, 8, 9)
Write-Host ''
Write-Host "Task 1: $Request"
Write-Host 'This runs the real model several times; expect a few minutes.' -ForegroundColor Yellow
$events = @(); $chatWhileBusy = $null
$s = Open-Stream '/tasks' @{ request = $Request }
if ($s.Status -ne 200) {
    $first = $s.Reader.ReadToEnd()
    Write-Host "Task was refused: HTTP $($s.Status) $first"
    $events = @([pscustomobject]@{ type = 'refused'; status = $s.Status; body = $first })
} else {
    while ($null -ne ($e = Read-Event $s)) {
        $events += $e
        Show-Event $e
        if ($e.type -eq 'agent_start' -and $null -eq $chatWhileBusy) {
            # Test 8: the model is busy with the task, so chat must be refused.
            try {
                Invoke-RestMethod -Uri "$Base/chat" -Method Post -ContentType 'application/json' -Body '{"message":"hi"}' -TimeoutSec 10 | Out-Null
                $chatWhileBusy = 'accepted (HTTP 200)'
            } catch {
                $code = [int]$_.Exception.Response.StatusCode
                $chatWhileBusy = "HTTP $code"
            }
        }
    }
}
Close-Stream $s
$taskId = ($events | Where-Object { $_.type -eq 'task_start' } | Select-Object -First 1).task_id
$end = $events | Where-Object { $_.type -eq 'task_end' } | Select-Object -First 1
$task = if ($taskId) { Invoke-RestMethod -Uri "$Base/tasks/$taskId" -TimeoutSec 30 } else { $null }

# 2. plan
$planEvent = $events | Where-Object { $_.type -eq 'plan' } | Select-Object -First 1
$nSteps = if ($planEvent) { @($planEvent.steps).Count } else { 0 }
$ok2 = $end -and $end.status -eq 'done' -and $nSteps -ge 1 -and $nSteps -le $maxSteps
Add-Result '2' "A real request produces a valid plan of 1-$maxSteps steps" ([bool]$ok2) `
    $(if ($planEvent) { "$nSteps step(s): " + ((@($planEvent.steps) | ForEach-Object { $_.title }) -join ' | ') + "; task status: $($end.status) $($end.error)" } else { "no plan; task status: $($end.status) $($end.error)" })

# 3. code is valid Python (parsed, never run)
$syntaxDetail = 'no task'
$ok3 = $false
if ($task) {
    $tmp = Join-Path $Root 'logs\phase3-task.json'
    [IO.File]::WriteAllText($tmp, ($task | ConvertTo-Json -Depth 10), (New-Object System.Text.UTF8Encoding($false)))
    $check = @'
import ast, json, sys
task = json.load(open(sys.argv[1], encoding="utf-8"))
bad = 0
for s in task["steps"]:
    try:
        ast.parse(s["code"] or "")
        ok = bool((s["code"] or "").strip())
    except SyntaxError as e:
        ok = False
    bad += not ok
    print(f"step {s['number']}: {'valid Python' if ok else 'NOT valid Python'}")
sys.exit(1 if bad or not task["steps"] else 0)
'@
    # Run from a file: Windows PowerShell 5.1 mangles double quotes in native arguments.
    $checkPy = Join-Path $Root 'logs\phase3-syntax-check.py'
    [IO.File]::WriteAllText($checkPy, $check, (New-Object System.Text.UTF8Encoding($false)))
    $checkOut = (& $Py -I $checkPy $tmp 2>&1 | ForEach-Object { $_.ToString() })
    $ok3 = ($LASTEXITCODE -eq 0)
    $syntaxDetail = ($checkOut -join '; ')
}
Add-Result '3' "Every step's code is valid Python (parsed, not run)" $ok3 $syntaxDetail

# 4. verdicts and revision cap
$ok4 = $false; $d4 = 'no steps'
if ($task -and @($task.steps).Count) {
    $ok4 = @($task.steps | Where-Object { -not $_.verdict -or $_.revisions -gt $maxRev }).Count -eq 0
    $d4 = (@($task.steps) | ForEach-Object { "step $($_.number): $($_.verdict), $($_.revisions) revision(s)" }) -join '; '
}
Add-Result '4' "Every step has a reviewer verdict; at most $maxRev revisions" $ok4 $d4

# 5. live progress
$progress = @($events | Where-Object { $_.type -in @('agent_start', 'agent_done') })
$firstAt = if ($progress.Count) { $progress[0].at_ms } else { 0 }
$ok5 = $progress.Count -ge 4 -and $end -and ($end.at_ms - $firstAt) -gt 1000
Add-Result '5' 'Progress shows live while the task runs' $ok5 `
    ("{0} progress events; first after {1:N1}s, task ended after {2:N1}s" -f $progress.Count, ($firstAt / 1000), $(if ($end) { $end.at_ms / 1000 } else { 0 }))

# 8. chat refused while busy
Add-Result '8' 'While a task runs, /chat answers busy' ($chatWhileBusy -eq 'HTTP 429') "chat during task: $chatWhileBusy"

# ---------------------------------------------------------------- task 2: cancel (test 6)
Write-Host ''
Write-Host "Task 2 (will be cancelled): $CancelRequest"
$cancelResp = $null; $cancelId = $null; $events2 = @()
$s = Open-Stream '/tasks' @{ request = $CancelRequest }
if ($s.Status -eq 200) {
    while ($null -ne ($e = Read-Event $s)) {
        $events2 += $e
        Show-Event $e
        if ($e.type -eq 'task_start') { $cancelId = $e.task_id }
        if ($e.type -eq 'agent_start' -and -not $cancelResp) {
            Start-Sleep -Seconds 2  # let the model call get going
            $cancelResp = Invoke-RestMethod -Uri "$Base/tasks/$cancelId/cancel" -Method Post -TimeoutSec 30
            Write-Host ("  cancel answered: {0} in {1} ms" -f $cancelResp.status, $cancelResp.stopped_in_ms)
        }
    }
}
Close-Stream $s
$end2 = $events2 | Where-Object { $_.type -eq 'task_end' } | Select-Object -First 1
$ok6 = $cancelResp -and $cancelResp.status -eq 'cancelled' -and $cancelResp.stopped_in_ms -lt 10000 -and $end2.status -eq 'cancelled'
Add-Result '6' 'Cancel stops a task within 10 s' ([bool]$ok6) `
    $(if ($cancelResp) { "status $($cancelResp.status) after $($cancelResp.stopped_in_ms) ms; stream ended with: $($end2.status)" } else { "could not cancel (HTTP $($s.Status))" })

# ---------------------------------------------------------------- 7. restart keeps tasks
$before1 = $task
$before2 = if ($cancelId) { Invoke-RestMethod -Uri "$Base/tasks/$cancelId" -TimeoutSec 30 } else { $null }
Stop-Core $core
$core = Start-Core
$after1 = $null; $after2 = $null
try { if ($taskId) { $after1 = Invoke-RestMethod -Uri "$Base/tasks/$taskId" -TimeoutSec 30 } } catch { }
try { if ($cancelId) { $after2 = Invoke-RestMethod -Uri "$Base/tasks/$cancelId" -TimeoutSec 30 } } catch { }
$same1 = $after1 -and $before1 -and @($after1.steps).Count -eq @($before1.steps).Count -and @($after1.agent_runs).Count -eq @($before1.agent_runs).Count
$same2 = $after2 -and $after2.status -eq 'cancelled'
Add-Result '7' 'Tasks and all model calls survive a restart' ([bool]($same1 -and $same2)) `
    ("task $taskId : $(@($after1.steps).Count) step(s), $(@($after1.agent_runs).Count) model call(s) after restart; task $cancelId : $($after2.status)")
Stop-Core $core

# ---------------------------------------------------------------- 1. automated tests
Write-Host ''
Write-Host 'Running automated tests (the slow ones use the real model; a few minutes)...'
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
Add-Result '1' 'All automated tests pass' (($fastCode -eq 0) -and ($slowCode -eq 0)) "fast: $fastSum | slow (real model): $slowSum"

# ---------------------------------------------------------------- 9. per-agent time and tokens
$summary = if ($after1) { $after1.agent_summary } elseif ($task) { $task.agent_summary } else { $null }
$ok9 = $summary -and $summary.planner -and $summary.coder -and $summary.reviewer
Add-Result '9' 'Report shows time and tokens per agent' ([bool]$ok9) `
    $(if ($ok9) { (@('planner', 'coder', 'reviewer') | ForEach-Object { $a = $summary.$_; "{0}: {1} call(s), {2} tokens, {3:N1}s" -f $_, $a.calls, $a.tokens, ($a.duration_ms / 1000) }) -join '; ' } else { 'no agent summary' })

# ---------------------------------------------------------------- 10. report
$sb = New-Object System.Text.StringBuilder
function L([string]$t = '') { [void]$sb.AppendLine($t) }
L '# LocalForge AI - Phase 3 report (agents)'
L ''
L ('Generated: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm'))
L ''
L '## What was built'
L ''
L '- `agents\` - Planner (JSON plan, 1-5 steps), Coder (Python per step), Reviewer (approve / revise, JSON), Orchestrator (plan -> code -> syntax check -> review -> revise)'
L '- `agents\prompts\*.md` - each agent''s instructions as editable text'
L '- `core\tasks_api.py` - `POST /tasks` (streams progress), `GET /tasks`, `GET /tasks/{id}`, `POST /tasks/{id}/cancel`'
L '- Tables `tasks`, `task_steps`, `agent_runs` in `database\localforge.db`'
L '- One model job at a time shared by chat and tasks; code with a syntax error goes straight back to the Coder without a Reviewer call; generated code is never executed'
L ''
L '## Acceptance tests'
L ''
L '| # | Test | Result | Detail |'
L '|---|------|--------|--------|'
foreach ($r in ($results | Sort-Object { [int]$_.Id })) { L ('| {0} | {1} | {2} | {3} |' -f $r.Id, $r.Name, $(if ($r.Pass) { 'PASS' } else { 'FAIL' }), ($r.Detail -replace '\|', '/')) }
L ('| 10 | Report saved to ' + $Report + ' | PASS | this file |')
L ''
L "## Task 1: $Request"
L ''
if ($end) { L ("Status: **{0}**, total time {1:N1} s" -f $end.status, ($end.at_ms / 1000)) }
L ''
if ($ok9) {
    L '| Agent | Calls | Tokens | Time (s) | Avg tokens/s |'
    L '|-------|-------|--------|----------|--------------|'
    foreach ($a in 'planner', 'coder', 'reviewer') {
        $x = $summary.$a
        $tps = if ($x.duration_ms -gt 0) { '{0:N1}' -f ($x.tokens / ($x.duration_ms / 1000)) } else { '-' }
        L ('| {0} | {1} | {2} | {3:N1} | {4} |' -f $a, $x.calls, $x.tokens, ($x.duration_ms / 1000), $tps)
    }
    L ''
    L 'Time per call includes prompt reading and, for the first call, loading the model.'
    L ''
}
if ($after1) {
    foreach ($st in $after1.steps) {
        L "### Step $($st.number): $($st.title)"
        L ''
        L "$($st.description)"
        L ''
        L ("Verdict: **$($st.verdict)**, revisions: $($st.revisions)" + $(if (@($st.issues).Count) { '; open issues: ' + (@($st.issues) -join '; ') } else { '' }))
        L ''
        L '~~~~python'
        L "$($st.code)"
        L '~~~~'
        L ''
    }
}
L '## Known limits'
L ''
L '- Generated code is checked for valid syntax and reviewed by the same small model; it is not run or tested yet (Phase 4).'
L '- A 3B model''s reviewer can approve code with subtle bugs, or ask for changes that are not needed.'
L '- One task at a time, and chat is unavailable while a task runs.'
L '- Cancel stops the current model call at once; a cancelled task cannot be resumed.'
L ''
L '## Repeat each test'
L ''
L '```powershell'
L '# Start the service (leave this window open)'
L "powershell -NoProfile -ExecutionPolicy Bypass -File $Root\scripts\start-core.ps1"
L '# In a second window: run a task and watch progress'
L 'curl.exe -N -X POST http://127.0.0.1:8000/tasks -H "Content-Type: application/json" -d "{\"request\":\"Write a Python function is_even(n)\"}"'
L '# Task details (plan, steps, code, verdicts, model calls, per-agent summary)'
L 'Invoke-RestMethod http://127.0.0.1:8000/tasks/1 | ConvertTo-Json -Depth 6'
L '# Cancel a running task'
L 'Invoke-RestMethod -Method Post http://127.0.0.1:8000/tasks/1/cancel'
L '# Automated tests'
L "cd $Root; .\.venv\Scripts\python.exe -m pytest -q; .\.venv\Scripts\python.exe -m pytest -q -m slow"
L '# Everything above, with this report'
L "powershell -NoProfile -ExecutionPolicy Bypass -File $Root\scripts\phase3-acceptance.ps1"
L '```'
L ''
L '## Automated test output'
L ''
L '```'
foreach ($l in ($fastOut | Select-Object -Last 10)) { L $l }
L '--- slow ---'
foreach ($l in ($slowOut | Select-Object -Last 10)) { L $l }
L '```'

New-Item -ItemType Directory -Force -Path (Split-Path $Report) | Out-Null
[IO.File]::WriteAllText($Report, $sb.ToString(), (New-Object System.Text.UTF8Encoding($false)))
$failed = @($results | Where-Object { -not $_.Pass }).Count
Write-Host ''
Write-Host "Report written: $Report"
Write-Host ("Acceptance: {0} of {1} tests passed, plus report saved (test 10)." -f ($results.Count - $failed), $results.Count)
