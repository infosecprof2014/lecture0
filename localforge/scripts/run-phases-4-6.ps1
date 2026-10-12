<#
.SYNOPSIS
    Runs the Phase 4, 5 and 6 acceptance scripts one after the other and prints a summary.
    Expect 15-30 minutes on a slow CPU (Phase 4 runs a full agent task on the real model).

    powershell -NoProfile -ExecutionPolicy Bypass -File D:\LocalForgeAI\scripts\run-phases-4-6.ps1
#>
[CmdletBinding()]
param([string]$Root = '')
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$summary = @()
foreach ($n in 4, 5, 6) {
    Write-Host ''
    Write-Host "==================== Phase $n ====================" -ForegroundColor Cyan
    $out = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\phase$n-acceptance.ps1") 2>&1 |
           ForEach-Object { $_.ToString() } | Tee-Object -Variable lines
    $last = ($lines | Where-Object { $_ -match '^Acceptance:' } | Select-Object -Last 1)
    $summary += "Phase ${n}: " + $(if ($last) { $last } else { 'did not finish - see the messages above' })
}
Write-Host ''
Write-Host '==================== Summary ====================' -ForegroundColor Cyan
$summary | ForEach-Object { Write-Host $_ }
Write-Host "Reports: $Root\docs\phase4-report.md, phase5-report.md, phase6-report.md"
