# S7 test of the token-saving orchestrator (plan section 11): routine transitions run without a brain call, each trigger causes exactly one call,
# a quiet hour causes zero calls, the context limit writes HANDOFF and a -c2 agent continues. Everything runs in a temporary folder with its own test database,
# dummy plans (plan header format) and a fake brain answer file; the live database and the live runtime are never touched. No pane is opened (ORCH_TEST_NOSTART).
. (Join-Path $PSScriptRoot 'common.ps1')
$tools = $ToolsRoot
$root = Join-Path ([IO.Path]::GetTempPath()) ('orch-test-s7-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
if (Test-Path $root) { Remove-Item $root -Recurse -Force }
New-Item -ItemType Directory -Force "$root\rt\runs", "$root\wt" | Out-Null
[void](New-TestProject -Base $root -Repo "$root\repo" -Runtime "$root\rt")
$env:ORCH_RUNTIME_OVERRIDE = "$root\rt"; $env:ORCH_TEST_NOSTART = '1'
. (Join-Path $tools 'lib.ps1'); . (Join-Path $tools 'routine.ps1')
$cfg = Get-Cfg
if ($cfg.runtime -ne "$root\rt") { throw 'runtime override not active: refusing to run against the live runtime' }
$plan = "$root\repo\docs\plans\dummy\DUMMY-PLAN.md"; $rel = 'docs/plans/dummy/DUMMY-PLAN.md'
$results = New-Object System.Collections.Generic.List[string]
# Records one check.
function Check([string]$name, [bool]$ok, [string]$detail = '') { $results.Add(('{0} {1} {2}' -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail)); Write-Host $results[-1] -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' }) }
# Number of brain calls so far.
function Calls { return @(Read-BrainRows $cfg).Count }
# Agent row by name.
function Ag([string]$n) { return @(Read-Agents $cfg | Where-Object { $_.name -eq $n } | Select-Object -First 1) }
# Runs one round in its own process (like the loop does).
function Round([string]$answer = '') { $a = @('-NoProfile', '-File', (Join-Path $tools 'round.ps1')); if ($answer) { $a += @('-BrainAnswerFile', $answer) }; & pwsh @a *> $null }
# Fake brain answer file.
function Answer([string]$file, $actions) { Set-Content $file (ConvertTo-Json -InputObject @{ actions = @($actions) } -Depth 6) -Encoding UTF8 }
# Marks an agent as ended with an optional final answer.
function End-Agent([string]$n, [string]$final = '', [string]$status = 'done') { Update-Agent $cfg $n @{ status = $status; endedAt = (Get-Date).ToString('o'); exitCode = 0; lastLine = 'test' }; if ($final) { New-Item -ItemType Directory -Force "$($cfg.runtime)\runs\$n" | Out-Null; Set-Content "$($cfg.runtime)\runs\$n\final.txt" $final -Encoding UTF8 } }
# Writes the dummy plan (header with status and the packages P1 and P2, P2 depends on P1; $extra: more package hashtables) and reads it into the database.
function New-Plan([string]$status, $extra = @()) {
    Write-TestPlan $plan $status (@(@{ id = 'P1'; title = 'first' }, @{ id = 'P2'; title = 'second'; depends = @('P1') }) + @($extra))
    [void](Sync-OrchPlans $cfg $cfg.repo)
}
# Status of the dummy plan in the database (the plan file is read first).
function Get-DbPlanStatus { [void](Sync-OrchPlans $cfg $cfg.repo); return [string](@(Get-OrchPlans $cfg | Where-Object { $_.path -eq $rel })[0].status) }

# --- 1. new READY plan: exactly one brain call, the brain starts P1 ---------------------------
New-Plan 'READY'
Answer "$root\a1.json" @(@{ action = 'start_agent'; name = 'DUMMY-P1-work'; type = 'claude'; phase = 'work'; plan = $rel; package = 'P1'; cwd = "$root\wt"; prompt = 'Do package P1.' })
Round "$root\a1.json"
Check 'new_plan: one brain call' ((Calls) -eq 1) "calls=$(Calls)"
Check 'new_plan: brain started P1 work' ((Ag 'DUMMY-P1-work').status -eq 'running')
Check 'new_plan: plan READY -> IN_PROGRESS' ((Get-DbPlanStatus) -eq 'IN_PROGRESS')
Round "$root\a1.json"
Check 'handled event is not asked twice' ((Calls) -eq 1) "calls=$(Calls)"
# --- 2. routine transitions without the brain -------------------------------------------------
End-Agent 'DUMMY-P1-work'; Round
Check 'work done -> verify started (routine)' ((Ag 'DUMMY-P1-verify').status -eq 'running') "type=$((Ag 'DUMMY-P1-verify').type)"
Check 'verify agent has the other type' ((Ag 'DUMMY-P1-verify').type -eq 'codex')
End-Agent 'DUMMY-P1-verify' "Alles geprueft.`nOffene Anforderungen: 0"; Round
Check 'verify 0 -> dependent package P2 work started (routine)' ((Ag 'DUMMY-P2-work').status -eq 'running')
End-Agent 'DUMMY-P2-work'; Round
Check 'P2 verify started' ((Ag 'DUMMY-P2-verify').status -eq 'running')
End-Agent 'DUMMY-P2-verify' "Offene Anforderungen: 0"; Round
Check 'all packages verified -> whole-plan verify started' ((Ag 'DUMMY-ALL-verify').status -eq 'running')
End-Agent 'DUMMY-ALL-verify' "Offene Anforderungen: 0"; Round
Check 'whole-plan verify 0 -> release agent started' ((Ag 'DUMMY-release').status -eq 'running')
End-Agent 'DUMMY-release'; Round
Check 'release done -> plan IN_PROGRESS -> DONE (routine)' ((Get-DbPlanStatus) -eq 'DONE')
Check 'whole routine chain used no further brain call' ((Calls) -eq 1) "calls=$(Calls)"
# --- 3. quiet hour: 12 rounds (5 minutes each) with nothing to do -> zero calls ---------------
$before = Calls; 1..12 | ForEach-Object { Round }
Check 'quiet hour: zero brain calls' ((Calls) -eq $before) "calls=$(Calls)"
# --- 4. each trigger causes exactly one brain call ---------------------------------------------
$noteAns = "$root\note.json"; Answer $noteAns @(@{ action = 'note'; text = 'nothing to start' })
Reset-TestDb; New-Plan 'IN_PROGRESS'
Update-Agent $cfg 'T-P1-work' @{ type = 'claude'; plan = $rel; package = 'P1'; phase = 'work'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-20).ToString('o'); endedAt = (Get-Date).AddMinutes(-15).ToString('o') }
Update-Agent $cfg 'T-P1-verify' @{ type = 'codex'; plan = $rel; package = 'P1'; phase = 'verify'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-10).ToString('o'); endedAt = (Get-Date).AddMinutes(-5).ToString('o') }
New-Item -ItemType Directory -Force "$($cfg.runtime)\runs\T-P1-verify" | Out-Null; Set-Content "$($cfg.runtime)\runs\T-P1-verify\final.txt" "- Item A: teilweise`nOffene Anforderungen: 1" -Encoding UTF8
$b = Calls; Round $noteAns; Check 'trigger verify_open: exactly one call' ((Calls) -eq $b + 1) "delta=$((Calls) - $b)"
Round $noteAns; Check 'verify_open handled: no second call' ((Calls) -eq $b + 1)
Update-Agent $cfg 'T-P2-work' @{ type = 'claude'; plan = $rel; package = 'P2'; phase = 'work'; cwd = "$root\wt"; status = 'failed'; startedAt = (Get-Date).AddMinutes(-3).ToString('o'); endedAt = (Get-Date).AddMinutes(-2).ToString('o'); lastLine = 'test failure' }
$b = Calls; Round $noteAns; Check 'trigger agent_failed: exactly one call' ((Calls) -eq $b + 1) "delta=$((Calls) - $b)"
[void](Set-OrchQueue $cfg 'feature/dummy' @{ tip = 'abc123def'; plan = $rel; status = 'blocked'; note = 'test conflict' })
$b = Calls; Round $noteAns; Check 'integration_blocked: handled by the routine (fix agent), no brain call' ((Calls) -eq $b) "delta=$((Calls) - $b)"
[void](Set-OrchQueue $cfg 'feature/dummy' @{ status = 'integrated' })   # the queue row is closed (the database keeps rows; an integrated row is no open work)
$qid = (Add-OrchQuestion $cfg $rel 'X' 'Which one?' 'A').id
[void](Invoke-Orch @('answer', "$qid", 'Take A.', '--by', 'owner'))
$b = Calls; Round $noteAns; Check 'question_answered: handled by the routine (work agent), no brain call' ((Calls) -eq $b) "delta=$((Calls) - $b)"
Set-OrchKv $cfg "lock:$rel|P1" 'Should the dummy do X or Y?'   # an open point for the owner waiting for the brain (the place where a locked package puts it)
$b = Calls; Round $noteAns; Check 'trigger open_for_owner: exactly one call' ((Calls) -eq $b + 1) "delta=$((Calls) - $b)"
# --- 5. context limit 300k: HANDOFF flag, no brain call, -c2 continues -------------------------
Reset-TestDb; New-Plan 'IN_PROGRESS'
Update-Agent $cfg 'C-P1-work' @{ type = 'claude'; plan = $rel; package = 'P1'; phase = 'work'; cwd = "$root\wt"; status = 'running'; pid = 4; startedAt = (Get-Date).AddMinutes(-30).ToString('o'); ctxTokens = 310000 }
New-Item -ItemType Directory -Force "$($cfg.runtime)\runs\C-P1-work" | Out-Null
[System.IO.File]::WriteAllText("$($cfg.runtime)\runs\C-P1-work\prompt.md", "Header`n`nTASK`n`nDo package P1 of the dummy plan.`n`nPr" + [char]0xFC + "fe nach jedem abgeschlossenen Arbeitsschritt, ob die Datei X existiert.", (New-Object System.Text.UTF8Encoding($true)))
$b = Calls; Round
Check 'context 310k: HANDOFF flag written' (Test-Path "$($cfg.runtime)\runs\C-P1-work\HANDOFF")
Check 'context limit: no brain call' ((Calls) -eq $b)
Check 'running agent is not killed' ((Ag 'C-P1-work').status -eq 'running')
End-Agent 'C-P1-work'; Round
$c2 = Ag 'C-P1-work-c2'
Check 'handoff agent ended -> fresh -c2 agent started (routine)' ($c2 -and $c2.status -eq 'running')
$pt = if (Test-Path "$($cfg.runtime)\runs\C-P1-work-c2\prompt.md") { Get-Content "$($cfg.runtime)\runs\C-P1-work-c2\prompt.md" -Raw -Encoding UTF8 } else { '' }
Check '-c2 prompt: same task and read-handoff sentence' ($pt -match 'Do package P1' -and $pt -match "Handoff C-P1-work")
Check 'every agent prompt contains the handoff check sentence' ($pt -match 'HANDOFF')
# --- 6. finished packages are never startable (packages table and queue), also with no agent history for them ---
# Empties the database, writes the plan and one old row that only gives the plan its worktree.
function Reset-Rt { Reset-TestDb; New-Plan 'IN_PROGRESS'
    Update-Agent $cfg 'Z-ZZ-work' @{ type = 'claude'; plan = $rel; package = 'ZZ'; phase = 'work'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-60).ToString('o'); endedAt = (Get-Date).AddMinutes(-50).ToString('o') } }
Reset-Rt; foreach ($id in 'P1', 'P2') { [void](Set-OrchPackage $cfg $rel $id 'done') }; $b = Calls; Round
Check 'packages table: P1 and P2 done: no work or verify agent for P1/P2' (-not (Ag 'DUMMY-P1-work') -and -not (Ag 'DUMMY-P2-work') -and -not (Ag 'DUMMY-P1-verify') -and -not (Ag 'DUMMY-P2-verify'))
Check 'packages table all done: no brain call' ((Calls) -eq $b)
Reset-Rt; [void](Set-OrchPackage $cfg $rel 'P1' 'done'); Round
Check 'packages table P1 done: P1 not started' (-not (Ag 'DUMMY-P1-work') -and -not (Ag 'DUMMY-P1-verify'))
Check 'packages table P1 done: dependent P2 is startable' ((Ag 'DUMMY-P2-work').status -eq 'running')
Reset-Rt; [void](Set-OrchPackage $cfg $rel 'P1' 'work'); Round
Check 'packages table P1 not done (phase work): P1 stays startable' ((Ag 'DUMMY-P1-work').status -eq 'running')
Reset-Rt; [void](Set-OrchQueue $cfg 'feature/dummy' @{ tip = 'abc123def'; plan = $rel; status = 'integrated'; note = 'Lauf 1: Paket ALL (Aggregat-Branch)' }); Round
Check 'queue row integriert "Paket ALL": nothing of P1/P2 startable' (-not (Ag 'DUMMY-P1-work') -and -not (Ag 'DUMMY-P2-work'))
Reset-Rt; [void](Set-OrchQueue $cfg 'feature/dummy' @{ tip = 'abc123def'; plan = $rel; status = 'blocked'; note = '2 checks red: Paket ALL' }); Round
Check 'queue row blockiert does not count as done' ((Ag 'DUMMY-P1-work').status -eq 'running')
# --- 7. questions already decided (decisions table) or known (questions table) are not asked again ---
# Records the decision of the item M7 (the place where an answered question of the owner ends up).
function Add-M7Decision { [void](Add-OrchDecision $cfg 'owner' 'Dummy room: crystal bowl (M7): yes.' $rel 'M7') }
Reset-Rt; Add-M7Decision
$v9 = (Add-OrchQuestion $cfg $rel 'V9 Mood decay in the bath' 'Decay?' 'A').id
[void](Invoke-Orch @('answer', "$v9", 'Yes.', '--by', 'owner'))
$askAns = "$root\ask.json"; Answer $askAns @(@{ action = 'ask_owner'; plan = $rel; item = 'M7'; question = 'q?'; recommendation = 'r' }, @{ action = 'ask_owner'; plan = $rel; item = 'V9'; question = 'q?'; recommendation = 'r' }, @{ action = 'ask_owner'; plan = $rel; item = 'M8 new item'; question = 'q?'; recommendation = 'r' })
Update-Agent $cfg 'Z-ZZ-verify' @{ type = 'codex'; plan = $rel; package = 'P1'; phase = 'verify'; cwd = "$root\wt"; status = 'failed'; startedAt = (Get-Date).AddMinutes(-3).ToString('o'); endedAt = (Get-Date).AddMinutes(-2).ToString('o'); lastLine = 'x' }   # an event so that the brain (fake answer) runs
Round $askAns
$qs7 = @(Get-OrchQuestions $cfg)
Check 'ask_owner for an item decided in the decisions table is skipped' (@($qs7 | Where-Object { $_.item -match '^M7' }).Count -eq 0)
Check 'ask_owner for an item known in the questions table (id only) is skipped' (@($qs7 | Where-Object { $_.item -match '^V9' }).Count -eq 1 -and @($qs7 | Where-Object { $_.item -match '^V9' })[0].question -eq 'Decay?')
Check 'ask_owner for a new item is written to the test database' (@($qs7 | Where-Object { $_.item -eq 'M8 new item' }).Count -eq 1)
Reset-Rt; Add-M7Decision; Set-OrchKv $cfg "lock:$rel|P1" 'M7 crystal bowl: where does it stand?'; $b = Calls; Round $noteAns
Check 'Open for Owner point naming only decided items: no brain call' ((Calls) -eq $b) "delta=$((Calls) - $b)"
# --- 8. mechanical rules of 2026-10-27: freeze list, duplicate / implemented answers, fixes first, verify_open on finished packages, rule 5 ---
Reset-Rt; Add-OrchNofollow $cfg 'dummy-plan.md|p1'; Round
Check 'frozen package P1 is not started' (-not (Ag 'DUMMY-P1-work'))
Remove-OrchNofollow $cfg 'dummy-plan.md|p1'
Reset-TestDb; New-Plan 'IN_PROGRESS' @(@{ id = 'D2'; title = 'implemented item' })
Update-Agent $cfg 'Z-ZZ-work' @{ type = 'claude'; plan = $rel; package = 'ZZ'; phase = 'work'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-60).ToString('o'); endedAt = (Get-Date).AddMinutes(-50).ToString('o') }
[void](Set-OrchPackage $cfg $rel 'D2' 'done')   # the packages table shows D2 as implemented
foreach ($qa in @(@('D1', 'already answered (ANSWERS.md). Duplicate question.'), @('D2', 'yes'), @('D3', 'yes, do it'))) {
    $qi = (Add-OrchQuestion $cfg $rel $qa[0] 'Which one?' 'A').id
    [void](Invoke-Orch @('answer', "$qi", $qa[1], '--by', 'owner'))
}
$b = Calls; Round
Check 'answer "Duplicate question": no work agent' (-not (Ag 'DUMMY-Q-D1-work'))
Check 'item already implemented in the packages table: no work agent' (-not (Ag 'DUMMY-Q-D2-work'))
Check 'really answered question: work agent started by the routine, no brain call' ((Ag 'DUMMY-Q-D3-work').status -eq 'running' -and (Calls) -eq $b)
Reset-Rt
[void](Set-OrchQueue $cfg 'feature/dummy' @{ tip = 'abc123def'; plan = $rel; status = 'blocked'; note = '2 checks red: x' }); $b = Calls; Round
Check 'blocked queue row: fix agent started by the routine, no brain call' ((Ag 'DUMMY-FIX-abc123d-work').status -eq 'running' -and (Calls) -eq $b)
Reset-Rt; [void](Set-OrchPackage $cfg $rel 'P1' 'done')
Update-Agent $cfg 'V-P1-work' @{ type = 'claude'; plan = $rel; package = 'P1'; phase = 'work'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-20).ToString('o'); endedAt = (Get-Date).AddMinutes(-15).ToString('o') }
Update-Agent $cfg 'V-P1-verify' @{ type = 'codex'; plan = $rel; package = 'P1'; phase = 'verify'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-10).ToString('o'); endedAt = (Get-Date).AddMinutes(-5).ToString('o') }
New-Item -ItemType Directory -Force "$($cfg.runtime)\runs\V-P1-verify" | Out-Null; Set-Content "$($cfg.runtime)\runs\V-P1-verify\final.txt" "- Item A: teilweise`nOffene Anforderungen: 1" -Encoding UTF8
$b = Calls; Round $noteAns
Check 'verify_open for a package the packages table shows as finished: no brain call, no work agent' ((Calls) -eq $b -and -not (Ag 'DUMMY-P1-work-2'))
Reset-Rt
Write-TestPlan $plan 'IN_PROGRESS' @(@{ id = 'P1'; title = 'first' }, @{ id = 'P3'; title = 'Blender models, textures assets'; depends = @('P9') }); [void](Sync-OrchPlans $cfg $cfg.repo)
Update-Agent $cfg 'Z-ZZ-verify' @{ type = 'codex'; plan = $rel; package = 'P1'; phase = 'verify'; cwd = "$root\wt"; status = 'failed'; startedAt = (Get-Date).AddMinutes(-3).ToString('o'); endedAt = (Get-Date).AddMinutes(-2).ToString('o'); lastLine = 'x' }
$gAns = "$root\gfx.json"; Answer $gAns @(@{ action = 'start_agent'; name = 'DUMMY-P3-work'; type = 'claude'; phase = 'work'; plan = $rel; package = 'P3'; cwd = "$root\wt"; prompt = 'Do P3.'; graphics = $false }); Round $gAns
Check 'rule 5: graphics package sent as Claude by the brain starts as Codex' ((Ag 'DUMMY-P3-work').type -eq 'codex') "type=$((Ag 'DUMMY-P3-work').type)"
Check 'test ran against its own runtime only' ($cfg.runtime -eq "$root\rt")
# the project file must be valid JSON (a bad escape such as a single backslash before U once stopped every script and agent start); a write of invalid JSON must be refused and leave the file as it was.
Check 'project file is valid JSON' (Test-JsonText (Get-Content $env:ORCH_PROJECT -Raw -Encoding UTF8))
$badJson = '{ "p": "C:' + [char]92 + 'Users' + [char]92 + 'x" }'
Check 'validator rejects a bad escape sequence' (-not (Test-JsonText $badJson))
$jf = "$root\json-test.json"; Set-Content $jf '{"a":1}' -Encoding UTF8; $refused = $false
try { Save-JsonFile $jf $badJson } catch { $refused = $true }
Check 'Save-JsonFile refuses invalid JSON and keeps the old file' ($refused -and ((Get-Content $jf -Raw) -match '"a":1'))
$results | Set-Content "$root\RESULT.txt" -Encoding UTF8
$failed = @($results | Where-Object { $_ -like 'FAIL*' }).Count
Write-Host ("`n{0} checks, {1} failed" -f $results.Count, @($results | Where-Object { $_ -like 'FAIL*' }).Count)
Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
exit $failed
