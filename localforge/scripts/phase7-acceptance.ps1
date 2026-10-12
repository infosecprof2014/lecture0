<#
.SYNOPSIS
    LocalForge AI - Phase 7 acceptance tests 1-10 (voice, files, images, videos); writes docs\phase7-report.md.
    Makes one real file, two images and one short video with your models: expect 10-25 minutes.

    powershell -NoProfile -ExecutionPolicy Bypass -File D:\LocalForgeAI\scripts\phase7-acceptance.ps1
#>
[CmdletBinding()]
param([string]$Root = '')
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$ErrorActionPreference = 'Continue'
. (Join-Path (Join-Path $Root 'scripts') 'lib-acceptance.ps1')
$Report = Join-Path $Root 'docs\phase7-report.md'

Write-Host "LocalForge Phase 7 acceptance tests ($Root)"
Test-Prerequisites 'core\media_api.py'
$core = Start-Core
if (-not (Get-Health)) { Write-Host "The core did not start. See $ErrLog"; exit 1 }

# Runs a creation to the end, printing progress. Returns @{ Events; Done; End; Status }.
function Invoke-Create([string]$Kind, [hashtable]$Body, [scriptblock]$OnEvent = $null) {
    $events = @()
    $s = Open-Stream "/create/$Kind" $Body
    if ($s.Status -ne 200) {
        $text = $s.Reader.ReadToEnd(); Close-Stream $s
        Write-Host "  refused: HTTP $($s.Status) $text"
        return [pscustomobject]@{ Events = @(); Done = $null; End = $null; Status = $s.Status; Body = $text }
    }
    while ($null -ne ($e = Read-Event $s)) {
        $events += $e
        $t = '{0,6:N1}s' -f ($e.at_ms / 1000)
        switch ($e.type) {
            'progress' { Write-Host "  $t  ... $($e.stage) $($e.elapsed_s) s" }
            'stage'    { Write-Host "  $t  $($e.stage)" }
            'agent_done' { Write-Host "  $t  $($e.agent): $($e.tokens) tokens" }
            'storyboard' { Write-Host "  $t  storyboard '$($e.title)': $(@($e.scenes).Count) scene(s)" }
            'done'     { Write-Host "  $t  done: $($e.name)" }
            'end'      { Write-Host "  $t  $($e.status) $($e.error)" }
        }
        if ($OnEvent) { & $OnEvent $e }
    }
    Close-Stream $s
    return [pscustomobject]@{
        Events = $events; Status = 200
        Done = ($events | Where-Object { $_.type -eq 'done' } | Select-Object -First 1)
        End = ($events | Where-Object { $_.type -in 'done', 'end' } | Select-Object -Last 1)
    }
}
$ffprobe = @(Get-ChildItem -LiteralPath (Join-Path $Root 'tools\ffmpeg') -Recurse -Filter 'ffprobe.exe' -ErrorAction SilentlyContinue) | Select-Object -First 1

# ---------------------------------------------------------------- 2. installed
$caps = Invoke-RestMethod -Uri "$Base/media/status" -TimeoutSec 10
Add-Result '2' 'Voice input, images, video and narration are installed' ([bool]($caps.voice_input -and $caps.images -and $caps.video -and $caps.narration)) `
    "voice input=$($caps.voice_input), images=$($caps.images), video=$($caps.video), narration=$($caps.narration)"

# ---------------------------------------------------------------- 3. voice: Windows speaks, Whisper listens
$wav = Join-Path $env:TEMP 'localforge-voice-test.wav'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SetOutputToWaveFile($wav); $synth.Speak('What is two plus two? Please answer in one word.'); $synth.Dispose()
$heard = $null
try {
    $heard = Invoke-RestMethod -Uri "$Base/speech/transcribe" -Method Post -ContentType 'audio/wav' -InFile $wav -TimeoutSec 300
} catch { $heard = [pscustomobject]@{ text = ''; error = $_.ErrorDetails.Message } }
Remove-Item -LiteralPath $wav -ErrorAction SilentlyContinue
$ok3 = $heard.text -match 'two' -and $heard.text -match 'plus'
Add-Result '3' 'Speech is understood on this PC (Windows voice -> Whisper -> text)' ([bool]$ok3) `
    $(if ($heard.text) { "heard: '$($heard.text)' ($($heard.audio_seconds) s of audio in $($heard.seconds) s, first use includes loading the model)" } else { "failed: $($heard.error)" })

# ---------------------------------------------------------------- 4. the page has the new controls (Edge)
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if ($edge) {
    $profileDir = Join-Path $env:TEMP 'localforge-edge-check'
    $dom = (& $edge --headless=new --disable-gpu --no-first-run "--user-data-dir=$profileDir" --virtual-time-budget=8000 --dump-dom 'http://127.0.0.1:8000/' 2>$null) -join "`n"
    Remove-Item -LiteralPath $profileDir -Recurse -Force -ErrorAction SilentlyContinue
    $ok4 = ($dom -match 'data-ready="1"') -and ($dom -match 'id="mic"') -and ($dom -match 'id="speak-answers"') -and ($dom -match 'id="view-create"') -and ($dom -match 'title="Speak your message"')
    Add-Result '4' 'Edge shows the microphone, Speak answers and the Create tab' $ok4 `
        $(if ($ok4) { 'page ready; voice is reported as available to the page' } else { 'missing controls or voice not available in the page' })
} else { Add-Result '4' 'Edge shows the microphone, Speak answers and the Create tab' $false 'Edge not found' }

# ---------------------------------------------------------------- 5. create a file
Write-Host ''
Write-Host 'Creating a file with the real model...'
$f = Invoke-Create 'file' @{ request = 'A CSV file named planets.csv listing the 8 planets of the solar system with columns name, order_from_sun and has_rings (yes/no).' }
$fileOk = $false; $fileDetail = "no file: $($f.End.error)$($f.Body)"
if ($f.Done) {
    $resp = Invoke-WebRequest -UseBasicParsing -Uri "$Base$($f.Done.url)" -TimeoutSec 30
    $csv = $null; try { $csv = [Text.Encoding]::UTF8.GetString($resp.Content) | ConvertFrom-Csv } catch { }
    $attach = "$($resp.Headers['Content-Disposition'])" -match 'attachment'
    $fileOk = $csv -and @($csv).Count -ge 3 -and $attach
    $fileDetail = "$($f.Done.name): $(@($csv).Count) rows, columns: $((@($csv)[0].PSObject.Properties.Name) -join ', '); served as download: $attach; $([math]::Round($f.Done.duration_ms / 1000)) s"
}
Add-Result '5' 'A file is created from a request, saved, valid and downloadable' ([bool]$fileOk) $fileDetail

# ---------------------------------------------------------------- 6. create an image
Write-Host ''
Write-Host 'Creating an image (about 30 s to 2 min; the first one also loads the model)...'
$img = Invoke-Create 'image' @{ prompt = 'a lighthouse on a rocky cliff at sunset, dramatic clouds, cinematic, highly detailed' }
$imgOk = $false; $imgDetail = "no image: $($img.End.error)$($img.Body)"
if ($img.Done) {
    $path = Join-Path $Root "projects\images\$($img.Done.name)"
    Add-Type -AssemblyName System.Drawing
    $bmp = [System.Drawing.Image]::FromFile($path)
    $imgOk = $bmp.Width -eq $img.Done.meta.size -and $bmp.Height -eq $img.Done.meta.size
    $imgDetail = "$($img.Done.name): $($bmp.Width)x$($bmp.Height), drawn in $($img.Done.meta.seconds) s ($($img.Done.meta.steps) step, model $($img.Done.meta.model))"
    $bmp.Dispose()
}
Add-Result '6' 'An image is generated on this PC' ([bool]$imgOk) $imgDetail

# ---------------------------------------------------------------- 7. create a video
Write-Host ''
Write-Host 'Creating a short video (one picture per scene + narration; several minutes)...' -ForegroundColor Yellow
$vid = Invoke-Create 'video' @{ request = 'A two-scene video about a fox waking up in a snowy forest at dawn.' }
$vidOk = $false; $vidDetail = "no video: $($vid.End.error)$($vid.Body)"
if ($vid.Done) {
    $vpath = Join-Path $Root "projects\videos\video-$($vid.Done.artifact_id)\video.mp4"
    $streams = if ($ffprobe) { (& $ffprobe.FullName -v error -show_entries stream=codec_type -of csv=p=0 $vpath) -join ',' } else { 'ffprobe missing' }
    $vidOk = (Test-Path -LiteralPath $vpath) -and $streams -match 'video' -and $streams -match 'audio' -and $vid.Done.meta.narrated
    $vidDetail = "$($vid.Done.meta.scenes) scene(s), $($vid.Done.meta.seconds_long) s long, streams: $streams, narrated: $($vid.Done.meta.narrated), AI pictures: $($vid.Done.meta.ai_images); made in $([math]::Round($vid.Done.duration_ms / 1000)) s"
}
Add-Result '7' 'A narrated slideshow video is made (pictures, voice, captions)' ([bool]$vidOk) $vidDetail

# ---------------------------------------------------------------- 8. cancel
Write-Host ''
Write-Host 'Starting an image and cancelling it...'
$cancelResp = $null
$c = Invoke-Create 'image' @{ prompt = 'a test picture that will be cancelled' } {
    param($e)
    if ($e.type -eq 'stage' -and -not $script:cancelResp) {
        Start-Sleep -Seconds 3
        $script:cancelResp = Invoke-RestMethod -Uri "$Base/create/cancel" -Method Post -TimeoutSec 30
    }
}
Add-Result '8' 'A creation can be cancelled within 10 s' ([bool]($cancelResp -and $cancelResp.status -eq 'cancelled' -and $cancelResp.stopped_in_ms -lt 10000)) `
    $(if ($cancelResp) { "status $($cancelResp.status) after $($cancelResp.stopped_in_ms) ms" } else { 'cancel was not reached' })

# ---------------------------------------------------------------- 9. protections
$client = New-Object System.Net.Http.HttpClient
$req = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Post, "$Base/create/file")
[void]$req.Headers.TryAddWithoutValidation('Origin', 'https://evil.example')
$req.Content = New-Object System.Net.Http.StringContent('{"request":"x"}', [Text.Encoding]::UTF8, 'application/json')
$evil = [int]$client.SendAsync($req).GetAwaiter().GetResult().StatusCode
$client.Dispose()
$outside = try { (Invoke-WebRequest -UseBasicParsing -Uri "$Base/artifacts/999999/content" -TimeoutSec 10).StatusCode } catch { [int]$_.Exception.Response.StatusCode }
Add-Result '9' 'Other websites cannot create things; unknown downloads refused' ($evil -eq 403 -and $outside -eq 404) "cross-site create: HTTP $evil; unknown download: HTTP $outside"
Stop-Core $core

# ---------------------------------------------------------------- 1. automated tests
Write-Host ''
Write-Host 'Running automated tests (fast, then real model)...'
$py = Invoke-Pytest
Add-Result '1' 'All automated tests pass' $py.Pass $py.Detail

# ---------------------------------------------------------------- 10. report
New-Report 'LocalForge AI - Phase 7 report (voice, files, images, video)'
L '## What was built'
L ''
L '- Voice: microphone button in Chat; speech is turned into text on this PC by Whisper (base model); "Speak answers" reads replies aloud with Windows voices'
L '- Create tab: files (`.md .txt .csv .json .html .py ...`, only into `projects\files`, never overwriting), images (SD-Turbo via stable-diffusion.cpp, 512x512), videos (storyboard -> one picture per scene -> narration -> slideshow MP4 with zoom, fades and captions)'
L '- Every creation streams progress, can be cancelled, and shares the one-job-at-a-time rule with chat and tasks; the chat model is unloaded before drawing to free memory'
L '- Created HTML and other documents are always downloaded, never opened inside LocalForge'
L ''
L '## Acceptance tests'
L ''
Add-ResultTable '10' $Report
L '## Timings on this PC'
L ''
if ($heard.seconds) { L "- Speech to text: $($heard.audio_seconds) s of speech understood in $($heard.seconds) s" }
if ($f.Done) { L "- File: $([math]::Round($f.Done.duration_ms / 1000)) s" }
if ($img.Done) { L "- Image (512x512, $($img.Done.meta.steps) step): $($img.Done.meta.seconds) s drawing, $([math]::Round($img.Done.duration_ms / 1000)) s in total" }
if ($vid.Done) { L "- Video ($($vid.Done.meta.scenes) scenes, $($vid.Done.meta.seconds_long) s long): $([math]::Round($vid.Done.duration_ms / 1000)) s to make" }
L ''
if ($vid.Done) {
    L '### Video storyboard'
    L ''
    foreach ($sc in $vid.Done.meta.storyboard) { L "- **$($sc.caption)** - $($sc.narration)  _(picture: $($sc.image_prompt))_" }
    L ''
}
L '## Known limits'
L ''
L '- Videos are narrated slideshows of still pictures (slow zoom, fades, captions). Realistic AI-generated motion video needs a graphics card with 12-24 GB of memory; it is not possible on this laptop.'
L '- Images are 512x512 from a small, fast model: good for ideas and illustrations, not photo-perfect. Faces and text in pictures are often wrong.'
L '- Drawing needs about 2.5 GB of free memory; the chat model is unloaded first, so the next chat takes about 30 s to load it again.'
L '- Whisper "base" understands clear speech well; strong accents or noisy rooms reduce accuracy.'
L '- Spoken answers use the voices installed in Windows (Settings > Time & language > Speech).'
L ''
L '## Repeat each test'
L ''
L '```powershell'
L "powershell -NoProfile -ExecutionPolicy Bypass -File $Root\scripts\phase7-acceptance.ps1"
L '# or use it: start the service and open http://127.0.0.1:8000 (Chat > Speak, or the Create tab)'
L "powershell -NoProfile -ExecutionPolicy Bypass -File $Root\scripts\start-core.ps1"
L '```'
Save-Report $Report '10'
