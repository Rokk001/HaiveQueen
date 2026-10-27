# Pane wrapper for the Uebersicht (PowerShell 7): runs dashboard.ps1 as a CHILD process. When the dashboard ends (e.g. it was stopped to load new code), it is started again, so a dashboard restart no longer closes the pane;
# the pane only closes when the file DASH-STOP exists in the runtime folder (or the loop lock is gone for good).
param([string]$Project = '')
if ($Project) { $env:ORCH_PROJECT = $Project }
. (Join-Path $PSScriptRoot 'lib.ps1')
$cfg = Get-Cfg
while ($true) {
    & pwsh.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'dashboard.ps1') -Project $env:ORCH_PROJECT
    if (Test-Path (Join-Path $cfg.runtime 'DASH-STOP')) { break }
    Start-Sleep -Seconds 1
}
exit 0
