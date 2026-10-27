# S7 test of the token-saving orchestrator (plan section 11): routine transitions run without a brain call, each trigger causes exactly one call,
# a quiet hour causes zero calls, the context limit writes HANDOFF and a -c2 agent continues. Everything runs in pilot-scratch\s7 with its own AGENTS.json,
# dummy plans and a fake brain answer file; the live AGENTS.json and the live runtime are never touched. No pane is opened (ORCH_TEST_NOSTART).
. (Join-Path $PSScriptRoot 'common.ps1')
$tools = $ToolsRoot
$root = Join-Path ([IO.Path]::GetTempPath()) ('orch-test-s7-' + [guid]::NewGuid().ToString('N').Substring(0, 8))   # outside any folder named like planExcludeDirRegex: Get-PlanFiles ignores plans below such a folder
if (Test-Path $root) { Remove-Item $root -Recurse -Force }
New-Item -ItemType Directory -Force "$root\rt\runs", "$root\proj\dummy", "$root\proj\queue", "$root\wt" | Out-Null
[void](New-TestProject -Base $root -Repo "$root\repo" -Runtime "$root\rt")
$env:ORCH_RUNTIME_OVERRIDE = "$root\rt"; $env:ORCH_PLANS_OVERRIDE = "$root\proj"; $env:ORCH_TEST_NOSTART = '1'; $env:ORCH_QUESTIONS_FILE = "$root\QUESTIONS.md"
. (Join-Path $tools 'lib.ps1'); . (Join-Path $tools 'routine.ps1')
$cfg = Get-Cfg
if ($cfg.runtime -ne "$root\rt") { throw 'runtime override not active: refusing to run against the live runtime' }
$plan = "$root\proj\dummy\DUMMY-PLAN.md"; $state = "$root\proj\dummy\DUMMY-STATE.md"
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
# Writes the dummy plan.
function New-Plan([string]$status) { Set-Content $plan ("# Dummy plan`nStatus: $status`n`n| Package | Branch suffix | Content | Files | Depends on |`n|---|---|---|---|---|`n| P1 | one | first | a | none |`n| P2 | two | second | b | P1 |`n") -Encoding UTF8; Set-Content $state "# State`n## Open for Owner`nnone`n" -Encoding UTF8 }

# --- 1. new READY plan: exactly one brain call, the brain starts P1 ---------------------------
New-Plan 'READY'
Answer "$root\a1.json" @(@{ action = 'start_agent'; name = 'DUMMY-P1-work'; type = 'claude'; phase = 'work'; plan = $plan; package = 'P1'; cwd = "$root\wt"; prompt = 'Do package P1.' })
Round "$root\a1.json"
Check 'new_plan: one brain call' ((Calls) -eq 1) "calls=$(Calls)"
Check 'new_plan: brain started P1 work' ((Ag 'DUMMY-P1-work').status -eq 'running')
Check 'new_plan: plan READY -> IN_PROGRESS' ((Get-PlanStatus $plan) -eq 'IN_PROGRESS')
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
Check 'release done -> plan IN_PROGRESS -> DONE (routine)' ((Get-PlanStatus $plan) -eq 'DONE')
Check 'whole routine chain used no further brain call' ((Calls) -eq 1) "calls=$(Calls)"
# --- 3. quiet hour: 12 rounds (5 minutes each) with nothing to do -> zero calls ---------------
$before = Calls; 1..12 | ForEach-Object { Round }
Check 'quiet hour: zero brain calls' ((Calls) -eq $before) "calls=$(Calls)"
# --- 4. each trigger causes exactly one brain call ---------------------------------------------
$noteAns = "$root\note.json"; Answer $noteAns @(@{ action = 'note'; text = 'nothing to start' })
New-Plan 'IN_PROGRESS'; Remove-Item "$($cfg.runtime)\AGENTS.json"
Update-Agent $cfg 'T-P1-work' @{ type = 'claude'; plan = $plan; package = 'P1'; phase = 'work'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-20).ToString('o'); endedAt = (Get-Date).AddMinutes(-15).ToString('o') }
Update-Agent $cfg 'T-P1-verify' @{ type = 'codex'; plan = $plan; package = 'P1'; phase = 'verify'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-10).ToString('o'); endedAt = (Get-Date).AddMinutes(-5).ToString('o') }
New-Item -ItemType Directory -Force "$($cfg.runtime)\runs\T-P1-verify" | Out-Null; Set-Content "$($cfg.runtime)\runs\T-P1-verify\final.txt" "- Item A: teilweise`nOffene Anforderungen: 1" -Encoding UTF8
$b = Calls; Round $noteAns; Check 'trigger verify_open: exactly one call' ((Calls) -eq $b + 1) "delta=$((Calls) - $b)"
Round $noteAns; Check 'verify_open handled: no second call' ((Calls) -eq $b + 1)
Update-Agent $cfg 'T-P2-work' @{ type = 'claude'; plan = $plan; package = 'P2'; phase = 'work'; cwd = "$root\wt"; status = 'failed'; startedAt = (Get-Date).AddMinutes(-3).ToString('o'); endedAt = (Get-Date).AddMinutes(-2).ToString('o'); lastLine = 'test failure' }
$b = Calls; Round $noteAns; Check 'trigger agent_failed: exactly one call' ((Calls) -eq $b + 1) "delta=$((Calls) - $b)"
Set-Content "$root\proj\queue\QUEUE.md" "| Branch | Tip | Plan | Status | Order | Date |`n|---|---|---|---|---|---|`n| feature/dummy | abc123def | $plan | blockiert (test conflict) | 1 | 2026-10-27 |`n" -Encoding UTF8
$b = Calls; Round $noteAns; Check 'integration_blocked: handled by the routine (fix agent), no brain call' ((Calls) -eq $b) "delta=$((Calls) - $b)"
Remove-Item "$root\proj\queue\QUEUE.md"
Set-Content $env:ORCH_QUESTIONS_FILE "# Open questions`n`n## Q-TEST-001`n- Plan: $plan`n- Item: X`n- Status: ANSWERED`n" -Encoding UTF8
$b = Calls; Round $noteAns; Check 'question_answered: handled by the routine (work agent), no brain call' ((Calls) -eq $b) "delta=$((Calls) - $b)"
Set-Content $state "# State`n## Open for Owner`nShould the dummy do X or Y?`n" -Encoding UTF8
$b = Calls; Round $noteAns; Check 'trigger open_for_owner: exactly one call' ((Calls) -eq $b + 1) "delta=$((Calls) - $b)"
# --- 5. context limit 300k: HANDOFF flag, no brain call, -c2 continues -------------------------
New-Plan 'IN_PROGRESS'; Set-Content $state "# State`n" -Encoding UTF8; Remove-Item "$($cfg.runtime)\AGENTS.json", "$($cfg.runtime)\ORCH-HANDLED.json" -ErrorAction SilentlyContinue
Remove-Item $env:ORCH_QUESTIONS_FILE -ErrorAction SilentlyContinue
Update-Agent $cfg 'C-P1-work' @{ type = 'claude'; plan = $plan; package = 'P1'; phase = 'work'; cwd = "$root\wt"; status = 'running'; pid = 4; startedAt = (Get-Date).AddMinutes(-30).ToString('o'); ctxTokens = 310000 }
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
# --- 6. finished packages are never startable (state files and queue), also with no agent history for them ---
function Reset-Rt { New-Plan 'IN_PROGRESS'; Remove-Item "$($cfg.runtime)\AGENTS.json", "$($cfg.runtime)\ORCH-HANDLED.json" -ErrorAction SilentlyContinue; Remove-Item "$root\proj\queue\QUEUE.md" -ErrorAction SilentlyContinue
    Update-Agent $cfg 'Z-ZZ-work' @{ type = 'claude'; plan = $plan; package = 'ZZ'; phase = 'work'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-60).ToString('o'); endedAt = (Get-Date).AddMinutes(-50).ToString('o') } }   # one old row only gives the plan its worktree
Reset-Rt; Set-Content $state "# State`nAlle Pakete P1-P2 sind fertig und gemergt (Spitze abc).`n" -Encoding UTF8; $b = Calls; Round
Check 'state line "Alle Pakete P1-P2 sind fertig": no work or verify agent for P1/P2' (-not (Ag 'DUMMY-P1-work') -and -not (Ag 'DUMMY-P2-work') -and -not (Ag 'DUMMY-P1-verify') -and -not (Ag 'DUMMY-P2-verify'))
Check 'state line all done: no brain call' ((Calls) -eq $b)
Reset-Rt; Set-Content $state "# State`n| Package | Status |`n|---|---|`n| P1 | fertig, gemergt |`n| P2 | wartet auf P1 |`n" -Encoding UTF8; Round
Check 'state table P1 fertig: P1 not started' (-not (Ag 'DUMMY-P1-work') -and -not (Ag 'DUMMY-P1-verify'))
Check 'state table P1 fertig: dependent P2 is startable' ((Ag 'DUMMY-P2-work').status -eq 'running')
Reset-Rt; Set-Content $state "# State`n| Package | Status |`n|---|---|`n| P1 | nicht fertig, Code fehlt |`n" -Encoding UTF8; Round
Check 'state table "nicht fertig": P1 stays startable' ((Ag 'DUMMY-P1-work').status -eq 'running')
Reset-Rt; Set-Content "$root\proj\queue\QUEUE.md" "| Branch | Tip | Plan | Status | Notes | Date |`n|---|---|---|---|---|---|`n| feature/dummy | abc123def | $plan | integriert (Lauf 1) | Paket ALL (Aggregat-Branch) | 2026-10-27 |`n" -Encoding UTF8; Round
Check 'queue row integriert "Paket ALL": nothing of P1/P2 startable' (-not (Ag 'DUMMY-P1-work') -and -not (Ag 'DUMMY-P2-work'))
Reset-Rt; Set-Content "$root\proj\queue\QUEUE.md" "| Branch | Tip | Plan | Status | Notes | Date |`n|---|---|---|---|---|---|`n| feature/dummy | abc123def | $plan | blockiert (x) | Paket ALL | 2026-10-27 |`n" -Encoding UTF8; Round
Check 'queue row blockiert does not count as done' ((Ag 'DUMMY-P1-work').status -eq 'running')
Remove-Item "$root\proj\queue\QUEUE.md" -ErrorAction SilentlyContinue
# --- 7. questions already decided in ANSWERS.md or known in QUESTIONS.md are not asked again ---
$env:ORCH_ANSWERS_FILE = "$root\ANSWERS.md"; Set-Content $env:ORCH_ANSWERS_FILE "# Answers`n`n## 2026-10-27 Dummy room: crystal bowl (M7, Owner)`nAnswer: yes.`n" -Encoding UTF8
Reset-Rt; Set-Content $state "# State`n" -Encoding UTF8; Set-Content $env:ORCH_QUESTIONS_FILE "# Open questions`n`n## Q-TEST-009`n- Plan: $plan`n- Item: V9 Mood decay in the bath`n- Status: ANSWERED`n" -Encoding UTF8
$askAns = "$root\ask.json"; Answer $askAns @(@{ action = 'ask_owner'; plan = $plan; item = 'M7'; question = 'q?'; recommendation = 'r' }, @{ action = 'ask_owner'; plan = $plan; item = 'V9'; question = 'q?'; recommendation = 'r' }, @{ action = 'ask_owner'; plan = $plan; item = 'M8 new item'; question = 'q?'; recommendation = 'r' })
Update-Agent $cfg 'Z-ZZ-verify' @{ type = 'codex'; plan = $plan; package = 'P1'; phase = 'verify'; cwd = "$root\wt"; status = 'failed'; startedAt = (Get-Date).AddMinutes(-3).ToString('o'); endedAt = (Get-Date).AddMinutes(-2).ToString('o'); lastLine = 'x' }   # an event so that the brain (fake answer) runs
Round $askAns
$qt = Get-Content $env:ORCH_QUESTIONS_FILE -Raw -Encoding UTF8
Check 'ask_owner for an item decided in ANSWERS.md is skipped' ($qt -notmatch 'Item: M7')
Check 'ask_owner for an item known in QUESTIONS.md (id only) is skipped' ($qt -notmatch 'Item: V9\s*\r?\n- Question')
Check 'ask_owner for a new item is written to the test question file' ($qt -match 'Item: M8 new item')
Reset-Rt; Set-Content $env:ORCH_QUESTIONS_FILE "# Open questions`n" -Encoding UTF8; Set-Content $state "# State`n## Open for Owner`nM7 crystal bowl: where does it stand?`n" -Encoding UTF8; $b = Calls; Round $noteAns
Check 'Open for Owner section naming only decided items: no brain call' ((Calls) -eq $b) "delta=$((Calls) - $b)"
Remove-Item Env:ORCH_ANSWERS_FILE -ErrorAction SilentlyContinue
# --- 8. mechanical rules of 2026-10-27: freeze list, duplicate / implemented answers, fixes first, verify_open on finished packages, rule 5 ---
Reset-Rt; Set-Content $state "# State`n" -Encoding UTF8; Set-Content "$($cfg.runtime)\NOFOLLOW.json" '{"keys":["dummy-plan.md|p1"]}' -Encoding UTF8; Round
Check 'frozen package P1 is not started' (-not (Ag 'DUMMY-P1-work'))
Remove-Item "$($cfg.runtime)\NOFOLLOW.json"
Reset-Rt; Set-Content $state "# State`n| Package | Status |`n|---|---|`n| D2 | Implementiert mit abc123 |`n" -Encoding UTF8
Set-Content $env:ORCH_QUESTIONS_FILE "# Open questions`n`n## Q-D1`n- Plan: $plan`n- Item: D1`n- Answer: already answered (ANSWERS.md). Duplicate question.`n- Status: ANSWERED`n`n## Q-D2`n- Plan: $plan`n- Item: D2`n- Answer: yes`n- Status: ANSWERED`n`n## Q-D3`n- Plan: $plan`n- Item: D3`n- Answer: yes, do it`n- Status: ANSWERED`n" -Encoding UTF8
$b = Calls; Round
Check 'answer "Duplicate question": no work agent' (-not (Ag 'DUMMY-Q-D1-work'))
Check 'item already implemented in the state file: no work agent' (-not (Ag 'DUMMY-Q-D2-work'))
Check 'really answered question: work agent started by the routine, no brain call' ((Ag 'DUMMY-Q-D3-work').status -eq 'running' -and (Calls) -eq $b)
Reset-Rt; Remove-Item $env:ORCH_QUESTIONS_FILE -ErrorAction SilentlyContinue; Set-Content $state "# State`n" -Encoding UTF8
Set-Content "$root\proj\queue\QUEUE.md" "| Branch | Tip | Plan | Status | Notes | Date |`n|---|---|---|---|---|---|`n| feature/dummy | abc123def | $plan | blockiert (2 checks red) | x | 2026-10-27 |`n" -Encoding UTF8; $b = Calls; Round
Check 'blocked queue row: fix agent started by the routine, no brain call' ((Ag 'DUMMY-FIX-abc123d-work').status -eq 'running' -and (Calls) -eq $b)
Remove-Item "$root\proj\queue\QUEUE.md" -ErrorAction SilentlyContinue
Reset-Rt; Set-Content $state "# State`n| Package | Status |`n|---|---|`n| P1 | fertig |`n" -Encoding UTF8
Update-Agent $cfg 'V-P1-work' @{ type = 'claude'; plan = $plan; package = 'P1'; phase = 'work'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-20).ToString('o'); endedAt = (Get-Date).AddMinutes(-15).ToString('o') }
Update-Agent $cfg 'V-P1-verify' @{ type = 'codex'; plan = $plan; package = 'P1'; phase = 'verify'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-10).ToString('o'); endedAt = (Get-Date).AddMinutes(-5).ToString('o') }
New-Item -ItemType Directory -Force "$($cfg.runtime)\runs\V-P1-verify" | Out-Null; Set-Content "$($cfg.runtime)\runs\V-P1-verify\final.txt" "- Item A: teilweise`nOffene Anforderungen: 1" -Encoding UTF8
$b = Calls; Round $noteAns
Check 'verify_open for a package the state shows as finished: no brain call, no work agent' ((Calls) -eq $b -and -not (Ag 'DUMMY-P1-work-2'))
Reset-Rt; Set-Content $state "# State`n" -Encoding UTF8
Set-Content $plan ("# Dummy plan`nStatus: IN_PROGRESS`n`n| Package | Branch suffix | Content | Files | Depends on |`n|---|---|---|---|---|`n| P1 | one | first | a | none |`n| P3 | gfx | Blender models, textures assets | c | P9 |`n") -Encoding UTF8
Update-Agent $cfg 'Z-ZZ-verify' @{ type = 'codex'; plan = $plan; package = 'P1'; phase = 'verify'; cwd = "$root\wt"; status = 'failed'; startedAt = (Get-Date).AddMinutes(-3).ToString('o'); endedAt = (Get-Date).AddMinutes(-2).ToString('o'); lastLine = 'x' }
$gAns = "$root\gfx.json"; Answer $gAns @(@{ action = 'start_agent'; name = 'DUMMY-P3-work'; type = 'claude'; phase = 'work'; plan = $plan; package = 'P3'; cwd = "$root\wt"; prompt = 'Do P3.'; graphics = $false }); Round $gAns
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
