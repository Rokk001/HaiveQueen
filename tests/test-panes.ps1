# test-panes.ps1 - checks the pane command sequence of the Orchestrator window against a stub for wt.exe (no Windows Terminal involved, own test runtime, never live).
# Checks: targets only for confirmed live panes, new tab when none exists, no overlap of two pane commands, idle pane protocol.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$tools = $ToolsRoot
$rt = Join-Path ([IO.Path]::GetTempPath()) ('orch-panetest-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $rt | Out-Null
[void](New-TestProject -Base $rt -Runtime $rt)
$env:ORCH_RUNTIME_OVERRIDE = $rt
$stubLog = Join-Path $rt 'wt-calls.log'
$stub = Join-Path $rt 'wt-stub.cmd'
Set-Content -LiteralPath $stub -Value ('@echo off' + "`r`n" + 'echo %* >> "' + $stubLog + '"') -Encoding ASCII
$env:ORCH_WT_EXE = $stub
. (Join-Path $tools 'lib.ps1')
$cfg = Get-Cfg
$fail = 0
function Check([string]$what, [bool]$ok) { if ($ok) { Write-Host "PASS $what" } else { Write-Host "FAIL $what" -ForegroundColor Red; $script:fail++ } }
function Row($name, $status, $pane, $pid2, $closed) { return [pscustomobject]@{ name = $name; type = 'claude'; plan = 'x/X-PLAN.md'; package = 'S1'; phase = 'work'; status = $status; paneId = $pane; paneClosed = $closed; pid = $pid2; paneCols = 140; paneRows = 50 } }
function NewA($name) { return @{ name = $name; type = 'claude'; plan = 'x'; package = 'S1'; phase = 'work'; cwd = $rt; promptFile = (Join-Path $rt 'p.md') } }
# Simulates the runner of a new pane: sets the pid of the agent row after 3 s (like agent-run.ps1).
function Runner($name) { Start-Job -ScriptBlock { param($n, $pidv, $rtp, $tools) $env:ORCH_RUNTIME_OVERRIDE = $rtp; . (Join-Path $tools 'lib.ps1'); $c = Get-Cfg; Start-Sleep -Seconds 3; Update-Agent $c $n @{ pid = $pidv; status = 'running' } } -ArgumentList $name, $PID, $rt, $tools | Out-Null }

# A: no pane, no idle -> new tab, never focus-pane / split-pane
Write-Agents $cfg @((Row 'A1' 'starting' $null $null $false))
Set-Content (Join-Path $rt 'panes.json') '{"next":3}'
Runner 'A1'; Start-AgentPane $cfg (NewA 'A1')
$log = if (Test-Path $stubLog) { Get-Content $stubLog -Raw } else { '' }
Check 'A: no live pane -> new-tab' ($log -match 'new-tab')
Check 'A: no focus-pane / split-pane on a missing pane' ($log -notmatch 'focus-pane|split-pane')

# B: idle pane alive (pid file with a live pid) -> split of the idle pane id
Remove-Item $stubLog -ErrorAction SilentlyContinue
Write-Agents $cfg @((Row 'B1' 'starting' $null $null $false))
Set-Content (Join-Path $rt 'panes.json') '{"next":8,"idle":7}'; Set-Content (Join-Path $rt 'idle.pid') $PID
Runner 'B1'; Start-AgentPane $cfg (NewA 'B1')
$log = Get-Content $stubLog -Raw
Check 'B: live idle pane is the split target' ($log -match 'focus-pane -t 7 ; split-pane')

# C: running agent pane (live runner) is the target, an ended agent whose pane is closing is not
Remove-Item $stubLog -ErrorAction SilentlyContinue; Remove-Item (Join-Path $rt 'idle.pid')
Write-Agents $cfg @((Row 'C-run' 'running' 5 $PID $false), (Row 'C-done' 'done' 6 $PID $false), (Row 'C1' 'starting' $null $null $false))
Runner 'C1'; Start-AgentPane $cfg (NewA 'C1')
$log = Get-Content $stubLog -Raw
Check 'C: split target is the running pane 5' ($log -match 'focus-pane -t 5 ;')
Check 'C: the closing pane 6 is never targeted' ($log -notmatch '-t 6')

# D: dead idle pid file + no agent pane -> new tab again (stale pid file is not trusted)
Remove-Item $stubLog -ErrorAction SilentlyContinue
Write-Agents $cfg @((Row 'D1' 'starting' $null $null $false)); Set-Content (Join-Path $rt 'idle.pid') 999999
Runner 'D1'; Start-AgentPane $cfg (NewA 'D1')
$log = Get-Content $stubLog -Raw
Check 'D: dead idle pid -> new-tab' ($log -match 'new-tab' -and $log -notmatch 'focus-pane')

# E: the pane-command lock never overlaps between two processes
$script2 = '. "' + (Join-Path $tools 'lib.ps1') + '"; $env:ORCH_RUNTIME_OVERRIDE="' + $rt + '"; Invoke-WtLocked { Add-Content "' + (Join-Path $rt 'lock.log') + '" ("in " + [DateTime]::Now.Ticks); Start-Sleep -Seconds 3; Add-Content "' + (Join-Path $rt 'lock.log') + '" ("out " + [DateTime]::Now.Ticks) }'
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script2))
$p1 = Start-Process pwsh -ArgumentList @('-NoProfile', '-EncodedCommand', $enc) -PassThru -WindowStyle Hidden
$p2 = Start-Process pwsh -ArgumentList @('-NoProfile', '-EncodedCommand', $enc) -PassThru -WindowStyle Hidden
$p1.WaitForExit(60000) | Out-Null; $p2.WaitForExit(60000) | Out-Null
$ll = @(Get-Content (Join-Path $rt 'lock.log')) | ForEach-Object { $_.Split(' ')[0] }
Check 'E: two pane commands run one after the other' (($ll -join ',') -eq 'in,out,in,out')

Get-Job | Remove-Job -Force -ErrorAction SilentlyContinue
Write-Host ("`n{0} failed" -f $fail)
Remove-Item -Recurse -Force $rt -ErrorAction SilentlyContinue
exit $(if ($fail) { 1 } else { 0 })
