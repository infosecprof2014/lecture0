<#
.SYNOPSIS
    LocalForge AI - Phase 1, Step 4 + acceptance tests + report.

.DESCRIPTION
    - Runs inspect.ps1 again and keeps its output (acceptance test 1).
    - Checks python/git/ollama versions (test 2), "Say OK" (test 3) and the
      venv (test 4).
    - Benchmarks the Phase 1 model on three short prompts using Ollama's own
      timing counters, sampling RAM every 250 ms for the peak.
    - Writes D:\LocalForgeAI\docs\phase1-report.md (test 5).

    Run:
        powershell -NoProfile -ExecutionPolicy Bypass -File .\benchmark.ps1
#>

[CmdletBinding()]
param(
    [string]$Root = 'D:\LocalForgeAI',
    [string]$Model = ''
)

$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName Microsoft.VisualBasic
$ci = New-Object Microsoft.VisualBasic.Devices.ComputerInfo
$Report = Join-Path $Root 'docs\phase1-report.md'
$cfgPath = Join-Path $Root 'config\phase1.json'
$cfg = $null
if (Test-Path -LiteralPath $cfgPath) { $cfg = Get-Content -LiteralPath $cfgPath -Raw | ConvertFrom-Json }
if (-not $Model -and $cfg) { $Model = $cfg.model }
if (-not $Model) { Write-Host 'No model given and no config\phase1.json. Run install.ps1 first or pass -Model.'; exit 1 }

$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
$ollama = (Get-Command ollama -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $ollama) {
    $guess = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    if (Test-Path -LiteralPath $guess) { $ollama = $guess }
}

function GB($b) { '{0:N2} GB' -f ($b / 1GB) }

$tests = New-Object System.Collections.Generic.List[object]
function Add-Test([string]$Name, [bool]$Pass, [string]$Detail) {
    $tests.Add([pscustomobject]@{ Name = $Name; Pass = $Pass; Detail = $Detail })
    $mark = if ($Pass) { 'PASS' } else { 'FAIL' }
    Write-Host ('[{0}] {1} - {2}' -f $mark, $Name, $Detail)
}

# ---------------------------------------------------------------- Test 1: inspect
Write-Host '=== Test 1: inspect.ps1'
$inspectPath = Join-Path $PSScriptRoot 'inspect.ps1'
if (-not (Test-Path -LiteralPath $inspectPath)) { $inspectPath = Join-Path $Root 'scripts\inspect.ps1' }
$inspectText = ''
if (Test-Path -LiteralPath $inspectPath) {
    $before = @(Get-ChildItem -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName + '|' + $_.LastWriteTimeUtc.Ticks })
    $raw = & powershell -NoProfile -ExecutionPolicy Bypass -File $inspectPath 2>&1
    $code = $LASTEXITCODE
    $after = @(Get-ChildItem -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName + '|' + $_.LastWriteTimeUtc.Ticks })
    $errs = @($raw | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] })
    $inspectText = ($raw | ForEach-Object { $_.ToString() }) -join "`r`n"
    $changed = @(Compare-Object $before $after).Count
    Add-Test 'inspect.ps1 runs without errors and changes nothing' (($code -eq 0) -and ($errs.Count -eq 0) -and ($changed -eq 0)) `
        ("exit $code, $($errs.Count) error(s), $changed file change(s) under $Root")
} else {
    Add-Test 'inspect.ps1 runs without errors and changes nothing' $false 'inspect.ps1 not found'
}

# ---------------------------------------------------------------- Test 2: versions
Write-Host '=== Test 2: versions'
$versions = [ordered]@{}
foreach ($t in @(@('python', '--version'), @('git', '--version'), @('ollama', '--version'))) {
    $exe = Get-Command $t[0] -CommandType Application -ErrorAction SilentlyContinue |
           Where-Object { $_.Source -notlike '*\Microsoft\WindowsApps\*' } | Select-Object -First 1
    if (-not $exe -and $t[0] -eq 'ollama' -and $ollama) { $exe = [pscustomobject]@{ Source = $ollama } }
    if ($exe) {
        $out = (& $exe.Source $t[1] 2>&1 | ForEach-Object { $_.ToString() } | Where-Object { $_ -match '\d+\.\d+' } | Select-Object -Last 1)
        $versions[$t[0]] = $out
        Add-Test ("$($t[0]) --version") ([bool]$out) $(if ($out) { $out } else { 'no version returned' })
    } else {
        $versions[$t[0]] = 'missing'
        Add-Test ("$($t[0]) --version") $false 'not found on PATH'
    }
}

# ---------------------------------------------------------------- Test 3: Say OK
Write-Host "=== Test 3: ollama run $Model"
$sayOk = ''
if ($ollama) {
    $sayOk = ((& $ollama run $Model 'Say OK' 2>$null) -join ' ').Trim()
    Add-Test ("ollama run $Model `"Say OK`"") ([bool]$sayOk) $(if ($sayOk) { "response: $sayOk" } else { 'no response' })
} else {
    Add-Test ("ollama run $Model `"Say OK`"") $false 'ollama not installed'
}

# ---------------------------------------------------------------- Test 4: venv
Write-Host '=== Test 4: venv'
$activate = Join-Path $Root '.venv\Scripts\Activate.ps1'
if (Test-Path -LiteralPath $activate) {
    $probe = & powershell -NoProfile -ExecutionPolicy Bypass -Command "& '$activate'; if (`$env:VIRTUAL_ENV) { python -c 'import sys; print(sys.prefix)' } else { 'not activated' }" 2>&1
    $probe = ($probe | ForEach-Object { $_.ToString() }) -join ' '
    $ok = (Test-Path -LiteralPath $Root) -and ($probe -like '*LocalForgeAI*.venv*')
    Add-Test "$Root exists and venv activates" $ok $probe
} else {
    Add-Test "$Root exists and venv activates" $false "$activate not found"
}

# ---------------------------------------------------------------- Benchmark
Write-Host "=== Benchmark: $Model"
$prompts = [ordered]@{
    'Summary' = 'Summarize in exactly three sentences: Local AI tools let people run language models on their own computers. This keeps data private and avoids per-request costs, but speed depends on CPU, memory and whether a GPU is available. Smaller models run faster but are less capable, so choosing a model is a trade-off between quality and responsiveness.'
    'Python function' = 'Write a Python function is_prime(n) with a docstring and type hints. Return only the code.'
    'JSON plan' = 'Return only JSON: a 4-step plan to set up a local AI project. Use the shape {"steps":[{"id":1,"title":"...","done":false}]}.'
}

function Get-OllamaRam {
    $s = 0
    foreach ($p in @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like 'ollama*' })) { $s += $p.WorkingSet64 }
    return $s
}

$results = @()
$sysTotal = [double]$ci.TotalPhysicalMemory
foreach ($name in $prompts.Keys) {
    $body = @{ model = $Model; prompt = $prompts[$name]; stream = $false; options = @{ temperature = 0; num_predict = 400 } }
    if ($name -eq 'JSON plan') { $body.format = 'json' }
    $json = $body | ConvertTo-Json -Depth 5
    $baseUsed = $sysTotal - [double]$ci.AvailablePhysicalMemory
    $job = Start-Job -ScriptBlock {
        param($j)
        Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/generate' -Method Post -Body $j -ContentType 'application/json' -TimeoutSec 600
    } -ArgumentList $json
    $peakOllama = 0; $peakSys = 0
    while ($job.State -eq 'Running' -or $job.State -eq 'NotStarted') {
        $o = Get-OllamaRam; if ($o -gt $peakOllama) { $peakOllama = $o }
        $u = $sysTotal - [double]$ci.AvailablePhysicalMemory; if ($u -gt $peakSys) { $peakSys = $u }
        Start-Sleep -Milliseconds 250
    }
    $r = Receive-Job $job -ErrorAction SilentlyContinue -ErrorVariable jobErr
    Remove-Job $job -Force
    if (-not $r -or -not $r.eval_count) {
        Write-Host "  $name : FAILED ($jobErr)"
        $results += [pscustomobject]@{ Prompt = $name; Ok = $false; Note = "$jobErr" }
        continue
    }
    $gen = $r.eval_count / ($r.eval_duration / 1e9)
    $pp = if ($r.prompt_eval_duration -gt 0) { $r.prompt_eval_count / ($r.prompt_eval_duration / 1e9) } else { 0 }
    $note = ''
    if ($name -eq 'JSON plan') {
        try { $null = $r.response | ConvertFrom-Json -ErrorAction Stop; $note = 'valid JSON' } catch { $note = 'INVALID JSON' }
    }
    $row = [pscustomobject]@{
        Prompt = $name; Ok = $true
        OutTokens = $r.eval_count; GenTps = [Math]::Round($gen, 1)
        PromptTokens = $r.prompt_eval_count; PromptTps = [Math]::Round($pp, 1)
        LoadSec = [Math]::Round($r.load_duration / 1e9, 2); TotalSec = [Math]::Round($r.total_duration / 1e9, 2)
        PeakOllama = $peakOllama; PeakSys = $peakSys; BaseSys = $baseUsed
        Note = $note; Output = $r.response
    }
    $results += $row
    Write-Host ('  {0}: {1} tok/s gen, {2} tokens, peak Ollama RAM {3}' -f $name, $row.GenTps, $row.OutTokens, (GB $peakOllama))
}

$ps = $null
try { $ps = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/ps' -TimeoutSec 5 } catch { }
$proc = ''
if ($ps -and $ps.models) {
    $m = $ps.models | Select-Object -First 1
    $gpuShare = if ($m.size -gt 0) { [Math]::Round(100 * $m.size_vram / $m.size) } else { 0 }
    $proc = "Model loaded size $(GB $m.size); $gpuShare% in GPU memory (0% = CPU only)."
}

# ---------------------------------------------------------------- Compatibility (from inspect output)
function Grab([string]$Label) {
    $line = ($inspectText -split "`r?`n") | Where-Object { $_ -match ('^' + [regex]::Escape($Label) + '\s*:') } | Select-Object -First 1
    if ($line) { return ($line -split ':', 2)[1].Trim() } else { return '' }
}
$ready = @(); $missing = @(); $risks = @()
$totalRamGB = [Math]::Round($sysTotal / 1GB, 1)
if ((Grab '64-bit OS') -eq 'True') { $ready += '64-bit Windows: ' + (Grab 'Edition') + ' build ' + (Grab 'Build') } else { $risks += '64-bit OS not confirmed' }
$avx = Grab 'AVX2'
if ($avx -like 'YES*') { $ready += "AVX2: $avx" } else { $risks += "AVX2: $avx - Ollama runs much slower or not at all without AVX2" }
if ($totalRamGB -lt 8) { $risks += "RAM $totalRamGB GB: only ~1-2B models are practical" }
elseif ($totalRamGB -lt 16) { $ready += "RAM $totalRamGB GB: 1-4B models comfortable"; $risks += 'Under 16 GB: 7-8B models will be tight alongside other apps' }
else { $ready += "RAM $totalRamGB GB: 3-8B models fit" }
$free = Grab 'Free space'
if ($free) { $ready += "D: free space $free" }
foreach ($k in 'python', 'git', 'ollama') { if ($versions[$k] -and $versions[$k] -ne 'missing') { $ready += "$k $($versions[$k])" } else { $missing += $k } }
$node = Grab 'Node.js'
if ($node -like 'missing*' -or -not $node) { $missing += 'Node.js (not needed for Phase 1; not installed)' } else { $ready += "Node.js $node" }
foreach ($p in 8000, 11434) {
    $v = Grab "Port $p"
    if ($v -like 'IN USE*' -and -not ($p -eq 11434 -and $v -like '*ollama*')) { $risks += "Port ${p}: $v" }
}
if ($proc -like '*0% in GPU*') { $risks += 'Inference is CPU-only on this machine (measured)' }

# ---------------------------------------------------------------- Report
$sb = New-Object System.Text.StringBuilder
function L([string]$t = '') { [void]$sb.AppendLine($t) }
L '# LocalForge AI - Phase 1 report'
L ''
L ('Generated: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm') + ' on ' + $env:COMPUTERNAME)
L ''
L '## Acceptance tests'
L ''
L '| # | Test | Result | Detail |'
L '|---|------|--------|--------|'
$n = 1
foreach ($t in $tests) { L ('| {0} | {1} | {2} | {3} |' -f $n, $t.Name, $(if ($t.Pass) { 'PASS' } else { 'FAIL' }), ($t.Detail -replace '\|', '/')); $n++ }
L ('| 5 | Report saved to ' + $Report + ' | PASS | this file |')
L ''
L '## Compatibility'
L ''
L '**Ready**'; L ''; foreach ($x in $ready) { L "- $x" }; L ''
L '**Missing**'; L ''; if ($missing.Count) { foreach ($x in $missing) { L "- $x" } } else { L '- nothing required' }; L ''
L '**Risks**'; L ''; if ($risks.Count) { foreach ($x in $risks) { L "- $x" } } else { L '- none detected' }; L ''
L '**Model shortlist** (chosen by RAM/AVX2 rule in install.ps1; only the benchmarked model is verified, the others are assumptions until tested)'
L ''
if ($cfg) { foreach ($s in $cfg.shortlist) { L ("- $s" + $(if ($s -eq $Model) { ' - pulled and benchmarked' } else { ' - assumption, not tested' })) } }
L ''
L "## Benchmark: $Model"
L ''
if ($proc) { L $proc; L '' }
L '| Prompt | Output tokens | Generation tok/s | Prompt tok/s | Load s | Total s | Peak Ollama RAM | Peak system RAM used | Note |'
L '|--------|---------------|------------------|--------------|--------|---------|-----------------|----------------------|------|'
foreach ($r in $results) {
    if ($r.Ok) { L ('| {0} | {1} | {2} | {3} | {4} | {5} | {6} | {7} | {8} |' -f $r.Prompt, $r.OutTokens, $r.GenTps, $r.PromptTps, $r.LoadSec, $r.TotalSec, (GB $r.PeakOllama), (GB $r.PeakSys), $r.Note) }
    else { L ('| {0} | - | - | - | - | - | - | - | FAILED: {1} |' -f $r.Prompt, $r.Note) }
}
L ''
L 'Token rates come from Ollama''s own eval counters. RAM is sampled every 250 ms (working set of all ollama* processes, and total system RAM in use), so short spikes between samples can be missed.'
L ''
foreach ($r in $results | Where-Object { $_.Ok }) {
    L "<details><summary>Output: $($r.Prompt)</summary>"; L ''; L '```'; L $r.Output; L '```'; L ''; L '</details>'; L ''
}
L '## Known limits'
L ''
L '- Write access to D:\ was inferred from the folder ACL by inspect.ps1; install.ps1 then created the folders for real.'
L '- AVX2 "not reported" can mean an older Windows build that does not expose the flag.'
L '- WMI reports GPU memory capped at 4 GB; the GPU share above comes from Ollama itself.'
L '- Ollama models are stored in its default location (%USERPROFILE%\.ollama\models), not D:\LocalForgeAI\models.'
L '- One run per prompt at temperature 0; numbers vary with background load.'
L '- Node.js is reported but not installed in Phase 1.'
L ''
L '## Repeat each test'
L ''
L '```powershell'
L "# 1. Inspect (read-only)"
L "powershell -NoProfile -ExecutionPolicy Bypass -File $Root\scripts\inspect.ps1"
L '# 2. Versions'
L 'python --version; git --version; ollama --version'
L '# 3. Model responds'
L "ollama run $Model `"Say OK`""
L '# 4. venv activates'
L "& $Root\.venv\Scripts\Activate.ps1; python -c `"import sys; print(sys.prefix)`"; deactivate"
L '# 5. Report exists'
L "Get-Item $Report"
L '# Full re-run of tests + benchmark'
L "powershell -NoProfile -ExecutionPolicy Bypass -File $Root\scripts\benchmark.ps1"
L '```'
L ''
L '## Raw inspect.ps1 output'
L ''
L '```'
L $inspectText
L '```'

New-Item -ItemType Directory -Path (Split-Path $Report) -Force | Out-Null
[IO.File]::WriteAllText($Report, $sb.ToString(), (New-Object System.Text.UTF8Encoding($false)))
Write-Host ''
Write-Host "Report written: $Report"
$failed = @($tests | Where-Object { -not $_.Pass }).Count
Write-Host ("Acceptance: {0} of {1} automated tests passed (+ report saved)." -f ($tests.Count - $failed), $tests.Count)
