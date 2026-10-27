# Samples free RAM and the number of running cl.exe every 10 s (ORCH_SAMPLE_SEC overrides, tests only) into a file while a building agent runs (plan 16.3).
# Started by agent-run.ps1; ends when the parent process is gone. Line format: ISO time;free MB;cl count.
param([int]$ParentPid = 0, [Parameter(Mandatory)][string]$Out)
$ErrorActionPreference = 'Continue'
$sec = if ($env:ORCH_SAMPLE_SEC) { [int]$env:ORCH_SAMPLE_SEC } else { 10 }
while ($true) {
    $free = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1KB, 0)
    $cl = @(Get-Process cl -ErrorAction SilentlyContinue).Count
    Add-Content -Path $Out -Value ('{0};{1};{2}' -f (Get-Date).ToString('o'), $free, $cl) -Encoding UTF8
    if ($ParentPid -and -not (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Seconds $sec
}
