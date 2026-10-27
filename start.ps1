# Opens the orchestrator window (name and terminal profile from the project file): Orchestrator log (left column = 40 % of the width, top 20 %), Uebersicht (left, below it, 80 % of the height; closes by itself via dash-wrap.ps1), agent area (right). Usage: start.ps1 -Project <path to the project json>
param([Parameter(Mandatory)][string]$Project, [string]$PlanFilesCsv = '', [int]$RoundMinutes = 0)   # PlanFilesCsv and RoundMinutes only for the pilot
$Project = (Resolve-Path -LiteralPath $Project).Path
$env:ORCH_PROJECT = $Project
. (Join-Path $PSScriptRoot 'lib.ps1')
$cfg = Get-Cfg
New-Item -ItemType Directory -Force (Join-Path $cfg.runtime 'runs') | Out-Null
$lock = Join-Path $cfg.runtime 'loop.lock'
if (Test-Path $lock) {
    $old = Get-Content $lock -ErrorAction SilentlyContinue
    if ($old -and (Get-Process -Id ([int]$old) -ErrorAction SilentlyContinue)) { Write-Host 'The orchestrator is already running.' -ForegroundColor Yellow; Start-Sleep 5; exit 0 }
}
# Also refuse when a loop process runs although the lock file is missing or stale (a second loop would start agents twice).
if (@(Get-CimInstance Win32_Process -Filter "Name='pwsh.exe'" | Where-Object { $_.CommandLine -match ('round-loop\.ps1.*' + [regex]::Escape($Project)) }).Count -gt 0) { Write-Host 'The orchestrator is already running (loop process found).' -ForegroundColor Yellow; Start-Sleep 5; exit 0 }
Remove-Item (Join-Path $cfg.runtime 'STOP') -ErrorAction SilentlyContinue
if (-not (Test-Path (Join-Path $cfg.runtime 'AGENTS.json'))) { Write-Agents $cfg @() }
# New window: all panes of the old window are gone, pane numbers start again (0 Orchestrator, 1 idle area, 2 Uebersicht, agents from 3).
foreach ($r in @(Read-Agents $cfg | Where-Object { $null -ne $_.paneId -and -not $_.paneClosed })) { Update-Agent $cfg ([string]$r.name) @{ paneClosed = $true } }
Write-FileAtomic (Join-Path $cfg.runtime 'panes.json') '{"next":3}'
$ps = 'pwsh -NoProfile -ExecutionPolicy Bypass'
$t = $script:ToolsDir
$loopArgs = " -Project `"$Project`""
if ($PlanFilesCsv) { $loopArgs += " -PlanFilesCsv `"$PlanFilesCsv`"" }
if ($RoundMinutes -gt 0) { $loopArgs += " -RoundMinutes $RoundMinutes" }
# A leftover old orchestrator window (e.g. a dead tab) would keep its pane numbers and shift the new layout: close it first (only the window remembered in window.txt, never another terminal).
. (Join-Path $PSScriptRoot 'winutil.ps1')
$hwndFile = Join-Path $cfg.runtime 'window.txt'
if (Test-Path $hwndFile) {
    $oldH = (Get-Content $hwndFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    # Closing the old window by message makes Windows Terminal ask "Alle Registerkarten schliessen?" (a dialog nobody answers): no automatic close, a leftover old window is only reported.
    if ($oldH -and @([WinUtil]::List('WindowsTerminal') | Where-Object { $_ -match ('^' + [regex]::Escape($oldH) + '\|') }).Count -gt 0) { Write-Host 'Note: the old orchestrator window is still open; close its dead tabs by hand if the pane layout looks shifted.' -ForegroundColor Yellow }
}
$pre = Get-PaneTitlePrefix
$chiefTitle = $pre + ' | CHIEF | job: Orchestrator | model: ' + (Get-ModelLong 'claude' ([string]$cfg.brainModel) ([string]$cfg.brainEffort))
$wtArgs = "-M -w $($cfg.window) new-tab -p $($cfg.terminalProfile.name) --title `"$chiefTitle`" --suppressApplicationTitle $ps -NoExit -File `"$t\round-loop.ps1`"$loopArgs ; " +
          "split-pane -p $($cfg.terminalProfile.name) -V --size 0.6 --title `"$pre | Agenten`" --suppressApplicationTitle $ps -File `"$t\agents-idle.ps1`" -Project `"$Project`" ; " +
          "focus-pane -t 0 ; split-pane -p $($cfg.terminalProfile.name) -H --size 0.7 --title `"$pre | Übersicht`" --suppressApplicationTitle $ps -File `"$t\dash-wrap.ps1`" -Project `"$Project`""
Start-Process -FilePath 'wt.exe' -ArgumentList $wtArgs
# Maximize the new orchestrator window and bring it to the front (wt -M is ignored when Windows Terminal already runs).
. (Join-Path $PSScriptRoot 'winutil.ps1')
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    $orchWin = [WinUtil]::List('WindowsTerminal') | Where-Object { $_ -match '\|(Agent-Orchestrator.*|PILOT-.*)$' } | Select-Object -First 1
    if ($orchWin) { [WinUtil]::Front([long]$orchWin.Split('|')[0]); Set-Content -Path $hwndFile -Value $orchWin.Split('|')[0] -Encoding ASCII; break }
}
