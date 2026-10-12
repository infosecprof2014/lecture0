<#
.SYNOPSIS
    LocalForge AI - Phase 1, Step 1: read-only environment inspection.

.DESCRIPTION
    Reports OS, CPU, RAM, disk, tool versions, Ollama models and port status.
    This script changes nothing: it creates no files or folders, installs
    nothing, starts no services and edits no settings. Results print to the
    console only.

    Run from a normal (non-admin is fine) PowerShell window:
        powershell -NoProfile -ExecutionPolicy Bypass -File .\inspect.ps1

    -ExecutionPolicy Bypass applies to this one process only; it does not
    change the machine's execution policy.

    Note: on Windows PowerShell 5.1 the AVX2 check compiles a tiny in-memory
    helper with Add-Type, which uses %TEMP% briefly and cleans up after itself.
    On PowerShell 7+ no compile is needed.
#>

[CmdletBinding()]
param(
    [string]$TargetDrive = 'D',
    [string]$TargetFolder = 'D:\LocalForgeAI',
    [int[]]$Ports = @(8000, 11434)
)

$ErrorActionPreference = 'Continue'
Set-StrictMode -Off

function Write-Section([string]$Title) {
    Write-Output ''
    Write-Output ('=== ' + $Title + ' ' + ('=' * [Math]::Max(0, 60 - $Title.Length)))
}

function Write-Item([string]$Name, $Value) {
    Write-Output ('{0,-28}: {1}' -f $Name, $Value)
}

function Format-GB([double]$Bytes) {
    '{0:N1} GB' -f ($Bytes / 1GB)
}

# Run an external command with a timeout so a hung tool cannot stall the report.
function Invoke-Quiet([string]$Exe, [string[]]$Arguments, [int]$TimeoutSec = 20) {
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $Exe
        $psi.Arguments = ($Arguments -join ' ')
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $p = [System.Diagnostics.Process]::Start($psi)
        $outTask = $p.StandardOutput.ReadToEndAsync()
        $errTask = $p.StandardError.ReadToEndAsync()
        if (-not $p.WaitForExit($TimeoutSec * 1000)) {
            try { $p.Kill() } catch { }
            return [pscustomobject]@{ ExitCode = -1; Text = "timed out after ${TimeoutSec}s" }
        }
        $text = ($outTask.Result + "`n" + $errTask.Result).Trim()
        return [pscustomobject]@{ ExitCode = $p.ExitCode; Text = $text }
    } catch {
        return [pscustomobject]@{ ExitCode = -1; Text = $_.Exception.Message }
    }
}

Write-Output 'LocalForge AI - Phase 1 inspection (read-only)'
Write-Item 'Generated' (Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')
Write-Item 'PowerShell' $PSVersionTable.PSVersion.ToString()
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Item 'Running elevated' $isAdmin

# ---------------------------------------------------------------- Windows
Write-Section 'Windows'
try {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    $cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction SilentlyContinue
    Write-Item 'Edition' $os.Caption
    if ($cv -and $cv.DisplayVersion) { Write-Item 'Release' $cv.DisplayVersion }
    $build = $os.BuildNumber
    if ($cv -and $null -ne $cv.UBR) { $build = '{0}.{1}' -f $os.BuildNumber, $cv.UBR }
    Write-Item 'Version' $os.Version
    Write-Item 'Build' $build
    Write-Item 'OS architecture' $os.OSArchitecture
    Write-Item '64-bit OS' ([Environment]::Is64BitOperatingSystem)
    Write-Item '64-bit PowerShell process' ([Environment]::Is64BitProcess)
} catch {
    Write-Item 'Windows info' ("could not read: " + $_.Exception.Message)
}

# ---------------------------------------------------------------- CPU
Write-Section 'CPU'
try {
    $cpus = @(Get-CimInstance Win32_Processor -ErrorAction Stop)
    Write-Item 'Model' (($cpus | ForEach-Object { $_.Name.Trim() }) -join ' | ')
    Write-Item 'Sockets' $cpus.Count
    Write-Item 'Physical cores' (($cpus | Measure-Object NumberOfCores -Sum).Sum)
    Write-Item 'Logical threads' (($cpus | Measure-Object NumberOfLogicalProcessors -Sum).Sum)
    Write-Item 'Max clock' ('{0} MHz' -f $cpus[0].MaxClockSpeed)
} catch {
    Write-Item 'CPU info' ("could not read: " + $_.Exception.Message)
}

# AVX2: ask the OS/CPU directly. A "False" from the Windows API on an old
# build can mean "not reported" rather than "not supported", so say so.
$avx2 = 'UNKNOWN - could not be checked reliably'
try {
    $avxType = [type]'System.Runtime.Intrinsics.X86.Avx2'
    if ($avxType) {
        if ($avxType::IsSupported) { $avx2 = 'YES (.NET Avx2.IsSupported)' }
        else { $avx2 = 'NO (.NET Avx2.IsSupported = False)' }
    }
} catch {
    try {
        if (-not ('LocalForge.LfCpu' -as [type])) {
            Add-Type -Namespace 'LocalForge' -Name 'LfCpu' -MemberDefinition @'
[DllImport("kernel32.dll")]
public static extern bool IsProcessorFeaturePresent(uint feature);
'@ -ErrorAction Stop
        }
        # 40 = PF_AVX2_INSTRUCTIONS_AVAILABLE
        if ([LocalForge.LfCpu]::IsProcessorFeaturePresent(40)) { $avx2 = 'YES (Windows IsProcessorFeaturePresent)' }
        else { $avx2 = 'NOT REPORTED by Windows (CPU may lack AVX2, or this Windows build does not report it)' }
    } catch {
        $avx2 = 'UNKNOWN - could not be checked reliably (' + $_.Exception.Message + ')'
    }
}
Write-Item 'AVX2' $avx2

# ---------------------------------------------------------------- GPU (extra, helps model sizing)
Write-Section 'GPU (informational)'
try {
    foreach ($g in @(Get-CimInstance Win32_VideoController -ErrorAction Stop)) {
        $vram = 'unknown'
        if ($g.AdapterRAM -gt 0) { $vram = (Format-GB $g.AdapterRAM) + ' (WMI caps at 4 GB; may be understated)' }
        Write-Item 'Adapter' ('{0} | VRAM {1} | driver {2}' -f $g.Name, $vram, $g.DriverVersion)
    }
} catch {
    Write-Item 'GPU' ("could not read: " + $_.Exception.Message)
}
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    $r = Invoke-Quiet $smi.Source @('--query-gpu=name,memory.total,memory.free', '--format=csv,noheader')
    Write-Item 'nvidia-smi' $r.Text
}

# ---------------------------------------------------------------- RAM
Write-Section 'Memory'
try {
    $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
    $os2 = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    Write-Item 'Total RAM' (Format-GB $cs.TotalPhysicalMemory)
    Write-Item 'Free RAM (now)' (Format-GB ($os2.FreePhysicalMemory * 1KB))
} catch {
    Write-Item 'Memory' ("could not read: " + $_.Exception.Message)
}

# ---------------------------------------------------------------- Disk
Write-Section ("Disk ${TargetDrive}:")
$driveOk = $false
try {
    $ld = Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='{0}:'" -f $TargetDrive) -ErrorAction Stop
    if (-not $ld) {
        Write-Item "${TargetDrive}: drive" 'NOT FOUND'
    } else {
        $types = @{ 0 = 'Unknown'; 1 = 'No root'; 2 = 'Removable'; 3 = 'Fixed'; 4 = 'Network'; 5 = 'CD/DVD'; 6 = 'RAM disk' }
        Write-Item 'Drive type' $types[[int]$ld.DriveType]
        Write-Item 'File system' $ld.FileSystem
        Write-Item 'Size' (Format-GB $ld.Size)
        Write-Item 'Free space' (Format-GB $ld.FreeSpace)
        $driveOk = $true
    }
} catch {
    Write-Item 'Disk info' ("could not read: " + $_.Exception.Message)
}

# Write permission is judged from the ACL only; nothing is created or written.
if ($driveOk) {
    $exists = Test-Path -LiteralPath $TargetFolder
    Write-Item "$TargetFolder exists" $exists
    $checkPath = if ($exists) { $TargetFolder } else { "${TargetDrive}:\" }
    $need = if ($exists) { 'WriteData/CreateFiles' } else { 'CreateDirectories' }
    try {
        $item = Get-Item -LiteralPath $checkPath -Force -ErrorAction Stop
        if ($exists -and ($item.Attributes -band [IO.FileAttributes]::ReadOnly) -and -not $item.PSIsContainer) {
            Write-Item 'Read-only attribute' 'set'
        }
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $sids = @($id.User.Value) + @($id.Groups | ForEach-Object { $_.Value })
        $acl = Get-Acl -LiteralPath $checkPath -ErrorAction Stop
        $rules = $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])
        $mask = if ($exists) { [Security.AccessControl.FileSystemRights]::WriteData } else { [Security.AccessControl.FileSystemRights]::AppendData }
        $allowVia = @(); $denied = $false
        foreach ($rule in $rules) {
            if ($sids -notcontains $rule.IdentityReference.Value) { continue }
            # Inherit-only entries apply to children, not to this folder itself.
            if ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) { continue }
            if (($rule.FileSystemRights -band $mask) -eq 0) { continue }
            if ($rule.AccessControlType -eq 'Deny') { $denied = $true }
            else { $allowVia += $rule.IdentityReference.Value }
        }
        $adminOnly = ($allowVia.Count -gt 0) -and (@($allowVia | Where-Object { $_ -ne 'S-1-5-32-544' }).Count -eq 0)
        $verdict =
            if ($denied) { 'NO - an explicit Deny entry applies to this user' }
            elseif ($allowVia.Count -eq 0) { 'NO - no Allow entry grants this user ' + $need }
            elseif ($adminOnly -and -not $isAdmin) { 'ONLY WHEN ELEVATED - granted via Administrators group' }
            else { 'YES - ACL grants ' + $need + ' (inferred from ACL; no test write made)' }
        $label = if ($exists) { "Can write to $TargetFolder" } else { "Can create $TargetFolder" }
        Write-Item $label $verdict
    } catch {
        Write-Item 'Write check' ("UNKNOWN - could not read ACL: " + $_.Exception.Message)
    }
}

# ---------------------------------------------------------------- Tools
Write-Section 'Tools (check only)'

# Prints its result line and leaves the tool path in $script:ToolPath. It must not
# return a value: anything a function outputs, including Write-Item lines, would be
# captured by an assignment like $x = Get-ToolVersion ... instead of printed.
function Get-ToolVersion([string]$Label, [string]$Cmd, [string[]]$VersionArgs) {
    $script:ToolPath = $null
    $c = Get-Command $Cmd -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $c) { Write-Item $Label 'missing'; return }
    # The Microsoft Store "python" stub lives in WindowsApps and is not a real install.
    if ($c.Source -like '*\Microsoft\WindowsApps\*' -and (Get-Item $c.Source -ErrorAction SilentlyContinue).Length -eq 0) {
        $r = Invoke-Quiet $c.Source $VersionArgs
        if ($r.ExitCode -ne 0 -or $r.Text -match 'was not found|Microsoft Store') {
            Write-Item $Label ('missing (only the Microsoft Store alias at ' + $c.Source + ')')
            return
        }
    }
    $r = Invoke-Quiet $c.Source $VersionArgs
    $first = ($r.Text -split "`r?`n" | Where-Object { $_ -match '\S' } | Select-Object -First 1)
    if ($r.ExitCode -ne 0 -and -not $first) { $first = "error (exit $($r.ExitCode))" }
    Write-Item $Label ('{0}   [{1}]' -f $first, $c.Source)
    $script:ToolPath = $c.Source
}

Get-ToolVersion 'Python' 'python' @('--version')
$pyPath = $script:ToolPath
Get-ToolVersion 'Python launcher (py)' 'py' @('--version')
if ($pyPath) {
    $r = Invoke-Quiet $pyPath @('-m', 'pip', '--version')
    if ($r.ExitCode -eq 0) { Write-Item 'pip' (($r.Text -split "`r?`n")[0]) }
    else { Write-Item 'pip' 'missing (python -m pip failed)' }
} elseif (Get-Command pip -CommandType Application -ErrorAction SilentlyContinue) {
    Get-ToolVersion 'pip' 'pip' @('--version')
} else {
    Write-Item 'pip' 'missing'
}
Get-ToolVersion 'Git' 'git' @('--version')
Get-ToolVersion 'Ollama' 'ollama' @('--version')
$ollamaPath = $script:ToolPath
if (-not $ollamaPath -and $env:LOCALAPPDATA) {
    $guess = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    if (Test-Path -LiteralPath $guess) { Write-Item 'Ollama (not on PATH)' $guess; $ollamaPath = $guess }
}
Get-ToolVersion 'Node.js' 'node' @('--version')

# ---------------------------------------------------------------- Ollama models
Write-Section 'Ollama models'
$listed = $false
try {
    # Read-only GET; does not start the server if it is not already running.
    $tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -Method Get -TimeoutSec 3 -ErrorAction Stop
    Write-Item 'Ollama server' 'running on 127.0.0.1:11434'
    if (@($tags.models).Count -eq 0) { Write-Item 'Installed models' 'none' }
    foreach ($m in $tags.models) {
        $q = if ($m.details) { "$($m.details.parameter_size) $($m.details.quantization_level)" } else { '' }
        Write-Item '  model' ('{0}  {1}  {2}' -f $m.name, (Format-GB $m.size), $q)
    }
    $listed = $true
} catch {
    Write-Item 'Ollama server' 'not reachable on 127.0.0.1:11434 (not running, or not installed)'
}
if (-not $listed) {
    # Fall back to reading the manifest folder on disk.
    $modelsRoot = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path $env:USERPROFILE '.ollama\models' }
    $manifests = Join-Path $modelsRoot 'manifests'
    if (Test-Path -LiteralPath $manifests) {
        $found = @(Get-ChildItem -LiteralPath $manifests -Recurse -File -ErrorAction SilentlyContinue)
        if ($found.Count -eq 0) { Write-Item 'Models on disk' "none (in $modelsRoot)" }
        foreach ($f in $found) {
            $rel = $f.FullName.Substring($manifests.Length + 1) -split '\\'
            # layout: registry\namespace\model\tag
            $name = if ($rel.Count -ge 4) { '{0}:{1}' -f $rel[-2], $rel[-1] } else { $rel -join '/' }
            if ($rel.Count -ge 4 -and $rel[-3] -ne 'library') { $name = $rel[-3] + '/' + $name }
            Write-Item '  model (from disk)' $name
        }
    } else {
        Write-Item 'Models on disk' "none found (no $manifests)"
    }
}

# ---------------------------------------------------------------- Ports
Write-Section 'Ports'
foreach ($port in $Ports) {
    $conns = @()
    try { $conns = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop) } catch { $conns = @() }
    if ($conns.Count -eq 0) {
        Write-Item "Port $port" 'FREE'
    } else {
        $owners = $conns | ForEach-Object {
            $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
            '{0} (PID {1}) on {2}' -f $(if ($p) { $p.ProcessName } else { '?' }), $_.OwningProcess, $_.LocalAddress
        } | Select-Object -Unique
        Write-Item "Port $port" ('IN USE by ' + ($owners -join '; '))
    }
}

Write-Output ''
Write-Output 'Inspection complete. Nothing on this machine was changed.'
