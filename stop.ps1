# Creates the STOP flag: running agents finish their task, no new agent is started, the loop ends.
param([Parameter(Mandatory)][string]$Project)
$env:ORCH_PROJECT = (Resolve-Path -LiteralPath $Project).Path
. (Join-Path $PSScriptRoot 'lib.ps1')
$cfg = Get-Cfg
New-Item -ItemType Directory -Force $cfg.runtime | Out-Null
Set-Content -Path (Join-Path $cfg.runtime 'STOP') -Value ('stop requested ' + (Get-Date -Format 'o')) -Encoding UTF8
$run = @(Read-Agents $cfg | Where-Object { $_.status -eq 'running' -or $_.status -eq 'starting' }).Count
Write-Host "STOP set. $run agent(s) still running will finish their task. No new agent starts." -ForegroundColor Yellow
Start-Sleep -Seconds 5
