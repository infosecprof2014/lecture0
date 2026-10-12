<#
.SYNOPSIS
    LocalForge AI - Phase 4 acceptance tests 1-9 (running code safely); writes docs\phase4-report.md.

    powershell -NoProfile -ExecutionPolicy Bypass -File D:\LocalForgeAI\scripts\phase4-acceptance.ps1
#>

[CmdletBinding()]
param(
    [string]$Root = '',
    [string]$Request = 'Write a Python function is_palindrome(s) that returns True if s is a palindrome, ignoring case, spaces and punctuation.'
)
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$ErrorActionPreference = 'Continue'
. (Join-Path (Join-Path $Root 'scripts') 'lib-acceptance.ps1')
$Report = Join-Path $Root 'docs\phase4-report.md'

Write-Host "LocalForge Phase 4 acceptance tests ($Root)"
Test-Prerequisites 'core\sandbox.py'

# ---------------------------------------------------------------- 2-5: sandbox layers on this machine
Write-Host 'Checking the sandbox layers on this PC...'
$probeRaw = (& $Py (Join-Path $Root 'scripts\sandbox_probe.py') 2>&1 | ForEach-Object { $_.ToString() }) -join "`n"
$probe = $null
try { $probe = ($probeRaw.Trim() -split "`n")[-1] | ConvertFrom-Json } catch { }
function P($k) { if ($probe -and $probe.$k) { $probe.$k } else { [pscustomobject]@{ ok = $false; detail = "probe failed: $probeRaw" } } }
$p = P 'static_check';  Add-Result '2' 'Dangerous code is blocked before it runs' ([bool]$p.ok) $p.detail
$p = P 'runtime_hook';  Add-Result '3' 'At runtime, writing files and network access are blocked' ([bool]$p.ok) $p.detail
$p = P 'timeout';       Add-Result '4' 'An endless loop is stopped at the time limit' ([bool]$p.ok) $p.detail
$p = P 'memory_cap';    Add-Result '5' 'Memory cap applied; a memory hog is stopped' ([bool]$p.ok) $p.detail

# ---------------------------------------------------------------- 6-8: a real task
$core = Start-Core
if (-not (Get-Health)) { Write-Host "The core did not start. See $ErrLog"; exit 1 }
Write-Host ''
Write-Host "Task: $Request"
Write-Host 'Planner, coder, reviewer, tester and maybe fixer run on the real model: expect several minutes.' -ForegroundColor Yellow
$run = Invoke-Task $Request
$task = if ($run.TaskId) { Invoke-RestMethod -Uri "$Base/tasks/$($run.TaskId)" -TimeoutSec 30 } else { $null }
$files = if ($run.TaskId) { Invoke-RestMethod -Uri "$Base/tasks/$($run.TaskId)/files" -TimeoutSec 30 } else { $null }
$testRuns = @($run.Events | Where-Object { $_.type -eq 'test_run' })
$done = $run.Events | Where-Object { $_.type -eq 'testing_done' } | Select-Object -First 1

$haveFiles = $files -and $files.files.'solution.py' -and $files.files.'test_solution.py'
$ok6 = $haveFiles -and $testRuns.Count -ge 1
Add-Result '6' 'Tests are written and run in the sandbox; project files saved' ([bool]$ok6) `
    ("{0} test run(s); files in {1}; test status: {2} {3}" -f $testRuns.Count, $files.project_dir, $done.test_status, $done.note)

$ok7 = $done -and $done.test_status -eq 'passed'
$roundsText = ($testRuns | ForEach-Object { "round $($_.round): $($_.status) $($_.passed)/$($_.passed + $_.failed)" }) -join '; '
Add-Result '7' "The final code passes the Tester's tests" ([bool]$ok7) $(if ($roundsText) { $roundsText } else { "no test run: $($done.test_status) $($done.note)" })

$ref = $null
if ($files -and $files.project_dir) {
    $refRaw = (& $Py (Join-Path $Root 'scripts\sandbox_probe.py') --reference $files.project_dir 2>&1 | ForEach-Object { $_.ToString() }) -join "`n"
    try { $ref = ($refRaw.Trim() -split "`n")[-1] | ConvertFrom-Json } catch { $ref = [pscustomobject]@{ status = 'error'; summary = $refRaw } }
}
Add-Result '8' 'Independent check: my reference tests pass on the final code' ([bool]($ref -and $ref.status -eq 'passed')) `
    $(if ($ref) { $ref.summary -replace "`r?`n", ' / ' } else { 'no solution to check' })
Stop-Core $core

# ---------------------------------------------------------------- 1. automated tests
Write-Host ''
Write-Host 'Running automated tests (fast, then real model)...'
$py = Invoke-Pytest
Add-Result '1' 'All automated tests pass' $py.Pass $py.Detail

# ---------------------------------------------------------------- 9. report
New-Report 'LocalForge AI - Phase 4 report (running code safely)'
L '## What was built'
L ''
L '- `core\sandbox.py` - static safety check, runtime audit hook (no file writes, network, programs), isolated process with time limit and memory cap (Windows Job Object, no child processes)'
L '- `agents\tester.py` - Tester writes tests from the request and signatures only (never sees the code); Fixer rewrites the solution from failing test output'
L '- After review: project folder `projects\task-N\` with `solution.py` and `test_solution.py`; tests run; failures go to the Fixer (max 2 rounds); every run saved in `test_runs`'
L '- `GET /tasks/{id}/files`; Reviewer instructions now check return value and type'
L ''
L '## Acceptance tests'
L ''
Add-ResultTable '9' $Report
L "## Task: $Request"
L ''
if ($run.End) { L ("Status: **{0}**, tests: **{1}**, total time {2:N1} s" -f $run.End.status, $done.test_status, ($run.End.at_ms / 1000)) }
L ''
if ($task) {
    L '| Agent | Calls | Tokens | Time (s) |'
    L '|-------|-------|--------|----------|'
    foreach ($a in $task.agent_summary.PSObject.Properties) { L ('| {0} | {1} | {2} | {3:N1} |' -f $a.Name, $a.Value.calls, $a.Value.tokens, ($a.Value.duration_ms / 1000)) }
    L ''
    foreach ($tr in $task.test_runs) { L "**Test run $($tr.round): $($tr.status)**"; L ''; L '~~~~'; L $tr.output; L '~~~~'; L '' }
}
if ($haveFiles) {
    L '### Final solution.py'; L ''; L '~~~~python'; L $files.files.'solution.py'; L '~~~~'; L ''
    L '### Tests written by the Tester (test_solution.py)'; L ''; L '~~~~python'; L $files.files.'test_solution.py'; L '~~~~'; L ''
}
L '## Known limits'
L ''
L '- The sandbox is layered defence, not a virtual machine: a determined attacker with a novel Python escape could get past it. Generated code comes from your own local model, not the internet.'
L '- Reading files is allowed (Python needs it to import); writing, network and starting programs are blocked.'
L '- The Tester is the same small model: its tests can be wrong. Test 8 checks the result against tests written by hand.'
L '- The memory cap is set just after the test process starts (a few milliseconds without it).'
L ''
L '## Repeat each test'
L ''
L '```powershell'
L "cd $Root"
L '# 2-5: sandbox layers'
L '.\.venv\Scripts\python.exe scripts\sandbox_probe.py'
L '# 6-7: run a task and see its tests (with the service running)'
L 'curl.exe -N -X POST http://127.0.0.1:8000/tasks -H "Content-Type: application/json" -d "{\"request\":\"Write is_palindrome(s)\"}"'
L 'Invoke-RestMethod http://127.0.0.1:8000/tasks/1/files | ConvertTo-Json -Depth 4'
L '# 8: reference tests against a task''s code'
L '.\.venv\Scripts\python.exe scripts\sandbox_probe.py --reference projects\task-1'
L '# 1: automated tests'
L '.\.venv\Scripts\python.exe -m pytest -q; .\.venv\Scripts\python.exe -m pytest -q -m slow'
L '# Everything, with this report'
L "powershell -NoProfile -ExecutionPolicy Bypass -File $Root\scripts\phase4-acceptance.ps1"
L '```'
Save-Report $Report '9'
