# Test of the onEvent hook (events.ps1): one call per round with all events of that round, no call without events, a failed call is repeated in the next round
# and logged in ORCH-EVENTS.log, a broken command never raises an error. Also tests the sync example against a throw-away git repository (local bare remote).
# Everything runs in a temporary folder with its own runtime and a recorder script as the command; no brain call, no pane, no real project.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$tools = $ToolsRoot
$root = Join-Path ([IO.Path]::GetTempPath()) ('orch-test-events-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$pf = New-TestProject -Base $root -Repo "$root\repo" -Runtime "$root\rt"
$env:ORCH_RUNTIME_OVERRIDE = "$root\rt"; $env:ORCH_TEST_NOSTART = '1'
# The command of the hook is a recorder: it logs its arguments and fails while the flag file exists.
$rec = "$root\recorder.ps1"
Set-Content $rec 'param([Parameter(ValueFromRemainingArguments = $true)][string[]]$E)
Add-Content (Join-Path $PSScriptRoot "calls.log") ($E -join "|")
if (Test-Path (Join-Path $PSScriptRoot "fail.flag")) { Write-Error "recorder fails on purpose"; exit 1 }' -Encoding UTF8
$j = Get-Content $pf -Raw | ConvertFrom-Json -AsHashtable
$j.onEvent = [ordered]@{ command = @('pwsh', '-NoProfile', '-File', $rec); workDir = $root; timeoutSeconds = 60 }
[System.IO.File]::WriteAllText($pf, ($j | ConvertTo-Json -Depth 10), (New-Object System.Text.UTF8Encoding($false)))
. (Join-Path $tools 'lib.ps1'); . (Join-Path $tools 'routine.ps1'); . (Join-Path $tools 'events.ps1')
$cfg = Get-Cfg
if ($cfg.runtime -ne "$root\rt") { throw 'runtime override not active: refusing to run' }
$plan = "$root\repo\docs\plans\dummy\DUMMY-PLAN.md"; $rel = 'docs/plans/dummy/DUMMY-PLAN.md'
$calls = "$root\calls.log"
$fails = 0
# Records one check.
function Check([string]$name, [bool]$ok, [string]$detail = '') { if (-not $ok) { $script:fails++ }; Write-Host ('{0} {1} {2}' -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' }) }
# Lines of the recorder log (one line per call).
function Calls { if (Test-Path $calls) { return @(Get-Content $calls) } else { return @() } }
# Runs one round in its own process (like the loop does) with a fake brain answer.
function Round { & pwsh -NoProfile -File (Join-Path $tools 'round.ps1') -BrainAnswerFile "$root\note.json" *> $null }
Set-Content "$root\note.json" '{"actions":[{"action":"note","text":"test"}]}' -Encoding UTF8
Write-TestPlan $plan 'IN_PROGRESS' @(@{ id = 'P1'; title = 'first' })

Round
Check 'first round only records the baseline: no call' ((Calls).Count -eq 0)
Round
Check 'quiet round: no call' ((Calls).Count -eq 0)
[void](Set-OrchPlanStatus $cfg $rel 'PAUSED' $cfg.repo); Round
Check 'plan status change: exactly one call with the event' ((@(Calls)).Count -eq 1 -and (@(Calls))[0] -match 'plan DUMMY-PLAN PAUSED') ((Calls) -join ' / ')
Round
Check 'no new event: no second call' ((Calls).Count -eq 1)
[void](Set-OrchQueue $cfg 'feature/dummy' @{ tip = 'abc123def'; plan = $rel; status = 'ready'; note = 'x' })
$qid = (Add-OrchQuestion $cfg $rel 'X1' 'Which one?' 'A').id
Round
Check 'two events in one round: one call that lists both' ((Calls).Count -eq 2 -and (Calls)[1] -match 'ready feature/dummy' -and (Calls)[1] -match "question Q-$qid asked") ((Calls) -join ' / ')
# A failing command: logged in ORCH-EVENTS.log, repeated in the next round with the same events, never stops anything.
Set-Content "$root\fail.flag" 'x'
[void](Invoke-Orch @('answer', "$qid", 'A', '--by', 'owner'))
Round
$ev = if (Test-Path "$root\rt\ORCH-EVENTS.log") { Get-Content "$root\rt\ORCH-EVENTS.log" -Raw } else { '' }
Check 'failed call is logged as an error line in ORCH-EVENTS.log' ((Calls).Count -eq 3 -and $ev -match '\|E\|Ereignis-Befehl fehlgeschlagen') ((Calls) -join ' / ')
Remove-Item "$root\fail.flag"
Round
Check 'next round repeats the failed events and succeeds' ((Calls).Count -eq 4 -and (Calls)[3] -eq (Calls)[2] -and (Calls)[3] -match "question Q-$qid answered") ((Calls) -join ' / ')
Round
Check 'after success nothing is repeated' ((Calls).Count -eq 4)

# A command that does not exist must not raise an error (the loop must never stop) and is retried.
$j.onEvent.command = @('pwsh-does-not-exist.exe', '-x')
[System.IO.File]::WriteAllText($pf, ($j | ConvertTo-Json -Depth 10), (New-Object System.Text.UTF8Encoding($false)))
$cfg = Get-Cfg
[void](Set-OrchPlanStatus $cfg $rel 'READY' $cfg.repo)
$threw = $false
try { Invoke-EventHook $cfg @([pscustomobject]@{ path = $rel; status = 'READY' }) @() } catch { $threw = $true }
$sync = (Get-OrchKv $cfg 'event-sync') | ConvertFrom-Json
Check 'missing command: no exception, the event stays pending' (-not $threw -and @($sync.pending).Count -eq 1) ("threw=$threw pending=" + (@($sync.pending) -join ';') + ' evlog=' + (Get-Content "$root\rt\ORCH-EVENTS.log" -Tail 1))

# The sync example against a throw-away repository with a local bare remote.
$bare = "$root\remote.git"; $work = "$root\work"
& git init --bare -b main $bare *> $null
& git init -b main $work *> $null
& git -C $work config user.name 'Test'; & git -C $work config user.email 'tester'
& git -C $work remote add origin $bare
Set-Content "$work\.gitignore" "ignored.txt`n"; Set-Content "$work\a.md" 'one'
& git -C $work add -A *> $null; & git -C $work commit -m 'start' *> $null; & git -C $work push -u origin main *> $null
$env:SYNC_REPO = $work
$sync1 = Join-Path $tools 'examples\sync.example.ps1'
& pwsh -NoProfile -File $sync1 'nothing' *> $null
Check 'sync: nothing changed, no commit' ((@(& git -C $work log --oneline).Count) -eq 1 -and $LASTEXITCODE -eq 0)
Set-Content "$work\a.md" 'two'; Set-Content "$work\ignored.txt" 'x'; Set-Content "$work\b.md" 'new'
& pwsh -NoProfile -File $sync1 'plan A READY' 'question Q-1 asked' *> $null
$msg = (& git -C $work log -1 --format=%s)
Check 'sync: commit message is Auto-sync with the events' ($msg -eq 'Auto-sync: plan A READY; question Q-1 asked') $msg
Check 'sync: ignored file is not committed' ((& git -C $work ls-files) -notcontains 'ignored.txt')
Check 'sync: pushed to the remote' ((& git -C $bare log -1 --format=%s main) -eq $msg)
# A rejected push (the remote has a newer commit) fails and is never forced.
$other = "$root\other"; & git clone $bare $other *> $null
& git -C $other config user.name 'Test'; & git -C $other config user.email 'tester'
Set-Content "$other\c.md" 'remote'; & git -C $other add -A *> $null; & git -C $other commit -m 'remote change' *> $null; & git -C $other push origin main *> $null
Set-Content "$work\a.md" 'three'
& pwsh -NoProfile -File $sync1 'plan A DONE' *> $null
$code = $LASTEXITCODE
Check 'sync: rejected push exits non-zero and is not forced' ($code -ne 0 -and (& git -C $bare log -1 --format=%s main) -eq 'remote change') "exit=$code"
Remove-Item Env:SYNC_REPO

Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "failures: $fails"
exit $fails
