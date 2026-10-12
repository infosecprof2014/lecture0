<#
.SYNOPSIS
    LocalForge AI - Phase 7 installer: voice input, image generation and video tools.
    Safe to run again: anything already installed is skipped.

    Before each download it shows the name, the official source and the exact size, and waits for
    you to type "yes" (anything else skips that part). Model files are checked against their
    published SHA-256 checksums after downloading.

    powershell -NoProfile -ExecutionPolicy Bypass -File D:\LocalForgeAI\scripts\install-phase7.ps1
#>
[CmdletBinding()]
param([string]$Root = '')
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Py = Join-Path $Root '.venv\Scripts\python.exe'
$Curl = Join-Path $env:SystemRoot 'System32\curl.exe'
$results = [ordered]@{}

function Step([string]$t) { Write-Host ''; Write-Host "=== $t" -ForegroundColor Cyan }
function Size($b) { if (-not $b) { 'unknown' } elseif ($b -ge 1GB) { '{0:N2} GB' -f ($b / 1GB) } else { '{0:N1} MB' -f ($b / 1MB) } }
function Ask([string]$What, [string]$Source, [string]$SizeText, [string]$Note = '') {
    Write-Host ''
    Write-Host '  About to download/install:' -ForegroundColor Yellow
    Write-Host "    Name  : $What"
    Write-Host "    Source: $Source"
    Write-Host "    Size  : $SizeText"
    if ($Note) { Write-Host "    Note  : $Note" }
    return ((Read-Host '  Type yes to continue (anything else skips)').Trim().ToLower() -eq 'yes')
}
function Get-File([string]$Url, [string]$Out) {
    New-Item -ItemType Directory -Force -Path (Split-Path $Out) | Out-Null
    & $Curl -L --fail --retry 3 --retry-delay 5 -C - -o $Out $Url
    return ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $Out))
}
function Get-HfFiles([string]$Repo) {
    # Lists files with sizes and SHA-256 (for large files) from the Hugging Face API.
    try { return @(Invoke-RestMethod -Uri "https://huggingface.co/api/models/$Repo/tree/main" -TimeoutSec 30) }
    catch {
        $code = try { [int]$_.Exception.Response.StatusCode } catch { 0 }
        Write-Host "  Could not list $Repo on Hugging Face (HTTP $code). $(if ($code -in 401,403) { 'This model needs a Hugging Face login/licence acceptance.' })" -ForegroundColor Yellow
        return $null
    }
}
function Test-Sha([string]$File, $Entry) {
    if (-not $Entry.lfs -or -not $Entry.lfs.oid) { return $true }  # small files have no published SHA-256
    return ((Get-FileHash -Algorithm SHA256 -LiteralPath $File).Hash.ToLower() -eq $Entry.lfs.oid.ToLower())
}

if (-not (Test-Path -LiteralPath $Py)) { Write-Host "Missing $Py - run the Phase 1 install first."; exit 1 }
if (-not (Test-Path -LiteralPath $Curl)) { Write-Host "Windows curl.exe not found ($Curl)."; exit 1 }
$drive = (Get-Item -LiteralPath $Root).PSDrive
Write-Host ("Free space on {0}: {1}" -f $drive.Name, (Size $drive.Free))

# ---------------------------------------------------------------- 1. voice packages
Step '1/5 Voice input packages (faster-whisper)'
$have = (& $Py -c "import faster_whisper; print('ok')" 2>$null) -eq 'ok'
if ($have) { Write-Host '  already installed'; $results['Voice packages'] = 'installed (already)' }
elseif (Ask 'faster-whisper 1.2.1 and 16 supporting packages (pinned in config\requirements-media.txt)' 'PyPI (pypi.org), into D:\LocalForgeAI\.venv only' '80.9 MB (measured for Windows / Python 3.11)') {
    & $Py -m pip install -r (Join-Path $Root 'config\requirements-media.txt')
    $results['Voice packages'] = if ($LASTEXITCODE -eq 0) { 'installed' } else { "FAILED (pip exit $LASTEXITCODE)" }
} else { $results['Voice packages'] = 'skipped' }

# ---------------------------------------------------------------- 2. Whisper model
Step '2/5 Whisper speech model (base, multilingual)'
$wDir = Join-Path $Root 'models\whisper-base'
if (Test-Path -LiteralPath (Join-Path $wDir 'model.bin')) { Write-Host '  already installed'; $results['Whisper model'] = 'installed (already)' }
else {
    $files = Get-HfFiles 'Systran/faster-whisper-base'
    if ($files) {
        $files = @($files | Where-Object { $_.type -eq 'file' -and $_.path -notmatch '^\.|README' })
        $total = ($files | Measure-Object -Property size -Sum).Sum
        if (Ask 'Whisper "base" speech-recognition model (CTranslate2 format)' 'https://huggingface.co/Systran/faster-whisper-base (MIT licence)' "$(Size $total) ($($files.Count) files)") {
            $ok = $true
            foreach ($f in $files) {
                $out = Join-Path $wDir $f.path
                if (-not (Get-File "https://huggingface.co/Systran/faster-whisper-base/resolve/main/$($f.path)" $out)) { $ok = $false; break }
                if (-not (Test-Sha $out $f)) { Write-Host "  Checksum mismatch: $($f.path)" -ForegroundColor Red; Remove-Item -LiteralPath $out; $ok = $false; break }
            }
            $results['Whisper model'] = if ($ok) { "installed, $(Size $total), checksums ok" } else { 'FAILED - run this script again to resume' }
        } else { $results['Whisper model'] = 'skipped' }
    } else { $results['Whisper model'] = 'FAILED - Hugging Face not reachable' }
}

# ---------------------------------------------------------------- 3. stable-diffusion.cpp
Step '3/5 Image program (stable-diffusion.cpp, CPU build with AVX2)'
$sdDir = Join-Path $Root 'tools\sd'
$sdExe = @(Get-ChildItem -LiteralPath $sdDir -Recurse -Include 'sd-cli.exe', 'sd.exe' -ErrorAction SilentlyContinue) | Select-Object -First 1
if ($sdExe) { Write-Host "  already installed: $($sdExe.FullName)"; $results['Image program'] = 'installed (already)' }
else {
    $rel = $null
    try { $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/leejet/stable-diffusion.cpp/releases/latest' -TimeoutSec 30 } catch { }
    $asset = if ($rel) { @($rel.assets | Where-Object { $_.name -match 'win' -and $_.name -match 'avx2' -and $_.name -match '\.zip$' -and $_.name -notmatch 'cuda|cudart|vulkan|rocm|sycl|avx512|noavx' }) | Select-Object -First 1 } else { $null }
    if ($asset) {
        if (Ask "stable-diffusion.cpp $($rel.tag_name) ($($asset.name))" "https://github.com/leejet/stable-diffusion.cpp/releases (MIT licence)" (Size $asset.size)) {
            $zip = Join-Path $env:TEMP $asset.name
            if (Get-File $asset.browser_download_url $zip) {
                New-Item -ItemType Directory -Force -Path $sdDir | Out-Null
                Expand-Archive -LiteralPath $zip -DestinationPath $sdDir -Force
                Remove-Item -LiteralPath $zip
                $sdExe = @(Get-ChildItem -LiteralPath $sdDir -Recurse -Include 'sd-cli.exe', 'sd.exe') | Select-Object -First 1
            }
            $results['Image program'] = if ($sdExe) { "installed: $($sdExe.Name) $($rel.tag_name)" } else { 'FAILED' }
        } else { $results['Image program'] = 'skipped' }
    } else { $results['Image program'] = 'FAILED - no Windows AVX2 build found in the latest release' }
}

# ---------------------------------------------------------------- 4. SD-Turbo model
Step '4/5 Image model (SD-Turbo)'
$mDir = Join-Path $Root 'models\sd'
$gguf = Join-Path $mDir 'sd_turbo-q8_0.gguf'
$st = Join-Path $mDir 'sd_turbo.safetensors'
if (Test-Path -LiteralPath $gguf) { Write-Host '  already installed'; $results['Image model'] = 'installed (already)' }
else {
    if (-not (Test-Path -LiteralPath $st)) {
        $files = Get-HfFiles 'stabilityai/sd-turbo'
        $entry = if ($files) { $files | Where-Object { $_.path -eq 'sd_turbo.safetensors' } | Select-Object -First 1 } else { $null }
        if ($entry) {
            if (Ask 'SD-Turbo image model (sd_turbo.safetensors)' 'https://huggingface.co/stabilityai/sd-turbo' (Size $entry.size) 'Stability AI licence: check the terms on that page (free for personal use; limits apply to commercial use).') {
                if ((Get-File 'https://huggingface.co/stabilityai/sd-turbo/resolve/main/sd_turbo.safetensors' $st) -and (Test-Sha $st $entry)) { Write-Host '  downloaded, checksum ok' }
                else { Write-Host '  Download failed or checksum mismatch.' -ForegroundColor Red; Remove-Item -LiteralPath $st -ErrorAction SilentlyContinue }
            }
        }
    }
    if ((Test-Path -LiteralPath $st) -and $sdExe) {
        # One-time conversion to an 8-bit file: about half the memory, faster to load each time.
        Write-Host '  Converting to an 8-bit model file (one time, a few minutes)...'
        & $sdExe.FullName -M convert -m $st -o $gguf --type q8_0
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $gguf)) {
            $results['Image model'] = "installed: sd_turbo-q8_0.gguf ($(Size (Get-Item $gguf).Length))"
            Remove-Item -LiteralPath $st  # the converted file replaces it
        } else {
            $results['Image model'] = 'installed: sd_turbo.safetensors (8-bit conversion failed; using the original, which needs more memory)'
        }
    } elseif (Test-Path -LiteralPath $st) { $results['Image model'] = 'downloaded (convert after installing the image program: run this script again)' }
    else { $results['Image model'] = 'skipped or failed' }
}

# ---------------------------------------------------------------- 5. FFmpeg
Step '5/5 Video tool (FFmpeg)'
$ffDir = Join-Path $Root 'tools\ffmpeg'
$ffExe = @(Get-ChildItem -LiteralPath $ffDir -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue) | Select-Object -First 1
if ($ffExe) { Write-Host "  already installed: $($ffExe.FullName)"; $results['FFmpeg'] = 'installed (already)' }
else {
    $url = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
    $len = $null
    try { $len = [long](Invoke-WebRequest -UseBasicParsing -Method Head -Uri $url -TimeoutSec 30).Headers['Content-Length'] } catch { }
    if (Ask 'FFmpeg release "essentials" build for Windows' "$url (linked from ffmpeg.org; GPL)" (Size $len)) {
        $zip = Join-Path $env:TEMP 'ffmpeg-release-essentials.zip'
        if (Get-File $url $zip) {
            New-Item -ItemType Directory -Force -Path $ffDir | Out-Null
            Expand-Archive -LiteralPath $zip -DestinationPath $ffDir -Force
            Remove-Item -LiteralPath $zip
            $ffExe = @(Get-ChildItem -LiteralPath $ffDir -Recurse -Filter 'ffmpeg.exe') | Select-Object -First 1
        }
        $results['FFmpeg'] = if ($ffExe) { 'installed: ' + ((& $ffExe.FullName -version | Select-Object -First 1) -replace ' Copyright.*', '') } else { 'FAILED' }
    } else { $results['FFmpeg'] = 'skipped' }
}

# ---------------------------------------------------------------- summary
Push-Location -LiteralPath $Root
& $Py -m core.maintenance merge-settings | Out-Null
$status = (& $Py -c "import json; from core.config import load_settings; from core.media import capabilities; print(json.dumps(capabilities(load_settings())))" 2>&1) -join ''
Pop-Location
Step 'Summary'
foreach ($k in $results.Keys) { Write-Host ('  {0,-15} {1}' -f $k, $results[$k]) }
try {
    $c = $status | ConvertFrom-Json
    Write-Host ''
    Write-Host ("  Ready: voice input={0}, images={1}, video={2}, narration={3}" -f $c.voice_input, $c.images, $c.video, $c.narration)
} catch { Write-Host "  Could not read the capabilities: $status" }
Write-Host ''
Write-Host 'Restart the core (scripts\start-core.ps1) and open http://127.0.0.1:8000 - Chat has a microphone button and there is a new Create tab.'
