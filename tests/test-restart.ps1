# test-restart.ps1 - follow-up tasks are found from the STATE after a restart of the loop (own test runtime and test plan, never live):
# (a) a work agent that failed with "pane never started" is started again exactly once, (b) a finished verify with open requirements gets exactly one work agent for those items.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$tools = $ToolsRoot
$base = Join-Path ([IO.Path]::GetTempPath()) ('orch-restarttest-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$repo = Join-Path $base 'repo'; $rt = Join-Path $base 'rt'
New-Item -ItemType Directory -Force (Join-Path $repo 'docs\plans\t'), (Join-Path $rt 'runs\TT-A1-verify') | Out-Null
[void](New-TestProject -Base $base -Repo $repo -Runtime $rt)
$env:ORCH_RUNTIME_OVERRIDE = $rt
Set-Content (Join-Path $repo 'docs\plans\t\T-PLAN.md') "# Plan: test`nStatus: IN_PROGRESS`n`n| Package | Content | Depends on |`n|---|---|---|`n| A1 | first | none |`n| A2 | second | none |`n" -Encoding UTF8
Set-Content (Join-Path $rt 'runs\TT-A1-verify\final.txt') "Nein, teilweise.`nOffene Anforderungen: 3" -Encoding UTF8
. (Join-Path $tools 'lib.ps1'); . (Join-Path $tools 'routine.ps1')
$cfg = Get-Cfg
$wt = Join-Path $base 'wt'; New-Item -ItemType Directory $wt | Out-Null
function Ag($n, $pk, $ph, $st, $last, $t0) { [pscustomobject]@{ name = $n; type = 'claude'; plan = 'docs/plans/t/T-PLAN.md'; package = $pk; phase = $ph; status = $st; cwd = $wt; startedAt = (Get-Date).AddMinutes($t0).ToString('o'); endedAt = (Get-Date).AddMinutes($t0 + 1).ToString('o'); lastLine = $last } }
$fail = 0
function Check([string]$what, [bool]$ok) { if ($ok) { Write-Host "PASS $what" } else { Write-Host "FAIL $what" -ForegroundColor Red; $script:fail++ } }
function Routine { $ctx = @{ Cfg = $cfg; Repo = $repo; Plans = @([pscustomobject]@{ path = 'docs\plans\t\T-PLAN.md'; status = 'IN_PROGRESS' }); Queue = @(); AllRows = @{}; Questions = @(); CanStartIntegrator = $false; Free = @{ claude = 2; codex = 2; workers = 4; int = 0 } }; return (Get-Routine $ctx) }
# state: A1 work done, A1 verify done with 3 open items; A2 work failed technically
Write-Agents $cfg @((Ag 'TT-A1-work' 'A1' 'work' 'done' 'ok' -30), (Ag 'TT-A1-verify' 'A1' 'verify' 'done' 'ok' -20), (Ag 'TT-A2-work' 'A2' 'work' 'failed' 'pane never started (no runner process)' -10))
foreach ($restart in 1, 2) {   # two "restarts" of the loop: the same state gives the same result
    $r = Routine
    $st = @($r.Actions | Where-Object { $_.action -eq 'start_agent' })
    Check "restart ${restart}: exactly one work start for A1 (open items)" (@($st | Where-Object { $_.package -eq 'A1' -and $_.phase -eq 'work' }).Count -eq 1)
    Check "restart ${restart}: exactly one work start for A2 (technical failure)" (@($st | Where-Object { $_.package -eq 'A2' -and $_.phase -eq 'work' }).Count -eq 1)
    Check "restart ${restart}: no other start" ($st.Count -eq 2)
}
# once the two agents run, nothing is started twice
$rows = @(Read-Agents $cfg) + (Ag 'TT-A1-work-2' 'A1' 'work' 'running' 'x' -1) + (Ag 'TT-A2-work-2' 'A2' 'work' 'running' 'x' -1)
Write-Agents $cfg $rows
$r = Routine
Check 'running follow-ups are not started again' (@($r.Actions | Where-Object { $_.action -eq 'start_agent' }).Count -eq 0)
Write-Host ("`n{0} failed" -f $fail)
Remove-Item -Recurse -Force $base -ErrorAction SilentlyContinue
exit $(if ($fail) { 1 } else { 0 })
