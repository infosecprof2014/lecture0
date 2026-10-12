<#
.SYNOPSIS
    LocalForge AI - Phase 1, Step 3: install base tools (safe to run twice).

.DESCRIPTION
    - Creates D:\LocalForgeAI and its standard folders (only the ones missing).
    - Installs Python, Git and Ollama only if missing, via winget from the
      publishers' official installers.
    - Creates the Python virtual environment D:\LocalForgeAI\.venv.
    - Pulls one small Ollama model sized for this machine's RAM.

    Before every install or download it prints the name, source and size and
    waits for you to type "yes". Anything else skips that item.

    Run:
        powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
    Optional:
        -Model llama3.2:1b      pick the model yourself
#>

[CmdletBinding()]
param(
    [string]$Root = 'D:\LocalForgeAI',
    [string]$Model = ''
)

$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Folders = 'core', 'agents', 'models', 'projects', 'database', 'logs', 'backups', 'tests', 'scripts', 'docs', 'config'

function Say([string]$Text) { Write-Host $Text }
function Step([string]$Text) { Write-Host ''; Write-Host ('=== ' + $Text) -ForegroundColor Cyan }

function Format-Size($Bytes) {
    if (-not $Bytes -or $Bytes -le 0) { return 'unknown' }
    if ($Bytes -ge 1GB) { return '{0:N2} GB' -f ($Bytes / 1GB) }
    return '{0:N0} MB' -f ($Bytes / 1MB)
}

function Confirm-Yes([string]$What, [string]$Source, [string]$Size) {
    Write-Host ''
    Write-Host '  About to download/install:' -ForegroundColor Yellow
    Write-Host ('    Name  : ' + $What)
    Write-Host ('    Source: ' + $Source)
    Write-Host ('    Size  : ' + $Size)
    $answer = Read-Host '  Type yes to continue (anything else skips)'
    return ($answer.Trim().ToLower() -eq 'yes')
}

function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

# Real python.exe, ignoring the Microsoft Store alias stub.
function Find-Python {
    foreach ($c in @(Get-Command python -CommandType Application -ErrorAction SilentlyContinue)) {
        if ($c.Source -like '*\Microsoft\WindowsApps\*') { continue }
        return $c.Source
    }
    $local = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs\Python') -Filter python.exe -Recurse -ErrorAction SilentlyContinue |
             Sort-Object FullName -Descending | Select-Object -First 1
    if ($local) { return $local.FullName }
    return $null
}

function Find-Tool([string]$Name, [string[]]$Fallbacks) {
    $c = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Source }
    foreach ($f in $Fallbacks) { if (Test-Path -LiteralPath $f) { return $f } }
    return $null
}

# Size of the installer winget would download, read from its URL (HEAD only).
function Get-WingetInstallerInfo([string]$Id) {
    $info = [pscustomobject]@{ Url = 'unknown'; Size = 'unknown' }
    try {
        $show = (& winget show --id $Id --exact --source winget --accept-source-agreements 2>$null) -join "`n"
        if ($show -match 'Installer Url:\s*(\S+)') {
            $info.Url = $Matches[1]
            try {
                $head = Invoke-WebRequest -Uri $info.Url -Method Head -UseBasicParsing -TimeoutSec 20 -ErrorAction Stop
                $len = $head.Headers['Content-Length']
                if ($len -is [array]) { $len = $len[0] }
                if ($len) { $info.Size = Format-Size ([double]$len) }
            } catch { }
        }
    } catch { }
    return $info
}

function Install-WithWinget([string]$Label, [string]$Id, [string[]]$Extra) {
    $i = Get-WingetInstallerInfo $Id
    $src = "winget package $Id (official installer: $($i.Url))"
    if (-not (Confirm-Yes $Label $src $i.Size)) { Say "  Skipped $Label."; return $false }
    $wgArgs = @('install', '--id', $Id, '--exact', '--source', 'winget',
              '--accept-package-agreements', '--accept-source-agreements') + $Extra
    # Out-Host keeps winget's output from becoming this function's return value.
    & winget @wgArgs | Out-Host
    $code = $LASTEXITCODE
    Update-SessionPath
    if ($code -ne 0) { Say "  winget exited with code $code for $Label." }
    return ($code -eq 0)
}

# Real download size of an Ollama model, from the public registry manifest.
function Get-OllamaModelSize([string]$Name) {
    try {
        $parts = $Name.Split(':')
        $repo = $parts[0]; $tag = if ($parts.Count -gt 1) { $parts[1] } else { 'latest' }
        if ($repo -notmatch '/') { $repo = "library/$repo" }
        $m = Invoke-RestMethod -Uri "https://registry.ollama.ai/v2/$repo/manifests/$tag" `
             -Headers @{ Accept = 'application/vnd.docker.distribution.manifest.v2+json' } -TimeoutSec 20 -ErrorAction Stop
        $total = ($m.layers | Measure-Object size -Sum).Sum
        return (Format-Size $total)
    } catch { return 'unknown (registry not reachable)' }
}

function Test-OllamaServer {
    try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 3 -ErrorAction Stop | Out-Null; return $true }
    catch { return $false }
}

# ---------------------------------------------------------------- 0. Pre-checks
Step 'Pre-checks'
$drive = Split-Path -Qualifier $Root
if (-not (Test-Path -LiteralPath ($drive + '\'))) {
    Say "Drive $drive not found. Stopping; nothing was changed."
    exit 1
}
$hasWinget = [bool](Get-Command winget -ErrorAction SilentlyContinue)
Say ("winget available: " + $hasWinget)
$ramGB = [Math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
Say ("Total RAM: $ramGB GB")

# ---------------------------------------------------------------- 1. Folders
Step "Folders under $Root"
foreach ($f in @('') + $Folders) {
    $p = if ($f) { Join-Path $Root $f } else { $Root }
    if (Test-Path -LiteralPath $p) { Say "  exists : $p" }
    else { New-Item -ItemType Directory -Path $p -ErrorAction Stop | Out-Null; Say "  created: $p" }
}
# Keep a copy of the Phase 1 scripts with the project.
foreach ($s in 'inspect.ps1', 'install.ps1', 'benchmark.ps1') {
    $from = Join-Path $PSScriptRoot $s
    $to = Join-Path $Root "scripts\$s"
    if ((Test-Path -LiteralPath $from) -and ($from -ne $to)) { Copy-Item -LiteralPath $from -Destination $to -Force }
}

# ---------------------------------------------------------------- 2. Tools
Step 'Base tools (install only if missing)'
$python = Find-Python
$git = Find-Tool 'git' @("$env:ProgramFiles\Git\cmd\git.exe")
$ollama = Find-Tool 'ollama' @((Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'))

$need = @()
if ($python) { Say "  Python : present ($python)" } else { Say '  Python : missing'; $need += 'python' }
if ($git)    { Say "  Git    : present ($git)" }    else { Say '  Git    : missing'; $need += 'git' }
if ($ollama) { Say "  Ollama : present ($ollama)" } else { Say '  Ollama : missing'; $need += 'ollama' }

if ($need.Count -gt 0 -and -not $hasWinget) {
    Say ''
    Say 'winget is not available, so missing tools cannot be installed automatically.'
    Say 'Install "App Installer" from the Microsoft Store, or install manually from:'
    Say '  Python: https://www.python.org/downloads/windows/'
    Say '  Git   : https://git-scm.com/download/win'
    Say '  Ollama: https://ollama.com/download/windows'
    Say 'Then run this script again.'
}

if ($hasWinget) {
    if ($need -contains 'python') {
        # Per-user install, added to PATH, with the py launcher.
        if (Install-WithWinget 'Python 3.12 (64-bit)' 'Python.Python.3.12' @('--scope', 'user', '--override', '/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1')) {
            $python = Find-Python
        }
    }
    if ($need -contains 'git') {
        if (Install-WithWinget 'Git for Windows' 'Git.Git' @('--silent')) {
            $git = Find-Tool 'git' @("$env:ProgramFiles\Git\cmd\git.exe")
        }
    }
    if ($need -contains 'ollama') {
        if (Install-WithWinget 'Ollama for Windows' 'Ollama.Ollama' @('--silent')) {
            $ollama = Find-Tool 'ollama' @((Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'))
        }
    }
}

# If python is installed but not reachable as "python", put its folder first on the user PATH.
if ($python) {
    $onPath = Get-Command python -CommandType Application -ErrorAction SilentlyContinue |
              Where-Object { $_.Source -notlike '*\Microsoft\WindowsApps\*' } | Select-Object -First 1
    if (-not $onPath) {
        $dir = Split-Path $python
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        $newPath = ($dir, (Join-Path $dir 'Scripts'), $userPath) -join ';'
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
        Update-SessionPath
        Say "  Added $dir to your user PATH so 'python' works in new windows."
    }
}

# ---------------------------------------------------------------- 3. venv
Step "Python virtual environment ($Root\.venv)"
$venvPy = Join-Path $Root '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPy) {
    Say '  exists'
} elseif ($python) {
    & $python -m venv (Join-Path $Root '.venv')
    if (Test-Path -LiteralPath $venvPy) { Say '  created' } else { Say '  FAILED to create venv' }
} else {
    Say '  skipped: Python is not installed'
}

# ---------------------------------------------------------------- 4. Model
Step 'Ollama model'
$avx2 = $true
try {
    if (-not ('LocalForge.LfCpu' -as [type])) {
        Add-Type -Namespace 'LocalForge' -Name 'LfCpu' -MemberDefinition '[DllImport("kernel32.dll")] public static extern bool IsProcessorFeaturePresent(uint feature);'
    }
    $avx2 = [LocalForge.LfCpu]::IsProcessorFeaturePresent(40)
} catch { }

# Shortlist sized by total RAM (smaller first). The first entry is the one pulled.
if ($ramGB -lt 8 -or -not $avx2) { $shortlist = 'llama3.2:1b', 'qwen2.5:1.5b', 'gemma3:1b' }
elseif ($ramGB -lt 16)            { $shortlist = 'llama3.2:3b', 'qwen2.5-coder:3b', 'phi4-mini' }
else                               { $shortlist = 'llama3.2:3b', 'qwen2.5-coder:7b', 'llama3.1:8b' }
if (-not $Model) { $Model = $shortlist[0] }
Say ('  Shortlist for this machine: ' + ($shortlist -join ', '))
Say ("  Model to pull: $Model")

$pulled = $false
if (-not $ollama) {
    Say '  skipped: Ollama is not installed'
} else {
    if (-not (Test-OllamaServer)) {
        Say '  Starting Ollama server...'
        Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden
        for ($i = 0; $i -lt 30 -and -not (Test-OllamaServer); $i++) { Start-Sleep -Seconds 1 }
    }
    $list = (& $ollama list 2>$null) -join "`n"
    if ($list -match [regex]::Escape($Model)) {
        Say '  already installed'
        $pulled = $true
    } else {
        $size = Get-OllamaModelSize $Model
        if (Confirm-Yes "Ollama model $Model" "https://ollama.com/library/$($Model.Split(':')[0])" $size) {
            & $ollama pull $Model
            $pulled = ($LASTEXITCODE -eq 0)
        } else {
            Say '  Skipped model pull.'
        }
    }
}

# Remember the choice for benchmark.ps1.
$cfg = [ordered]@{ model = $Model; shortlist = $shortlist; model_pulled = $pulled; updated = (Get-Date -Format s) }
$cfg | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Root 'config\phase1.json') -Encoding UTF8

Step 'Summary'
Say ("  Python : " + $(if ($python) { $python } else { 'missing' }))
Say ("  Git    : " + $(if ($git) { $git } else { 'missing' }))
Say ("  Ollama : " + $(if ($ollama) { $ollama } else { 'missing' }))
Say ("  venv   : " + (Test-Path -LiteralPath $venvPy))
Say ("  Model  : $Model (pulled: $pulled)")
Say ''
Say 'Install step finished. Next: benchmark.ps1'
