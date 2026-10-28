# Lock release test: a locked package (lock_package) is lifted automatically once its lock question is answered;
# an answer "erledigt" makes the package finished so that dependent packages start. Everything runs in a temporary folder with its own runtime and dummy plans; the live runtime is never touched.
. (Join-Path $PSScriptRoot 'common.ps1')
$tools = $ToolsRoot
$root = Join-Path ([IO.Path]::GetTempPath()) ('orch-test-lockrel-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
if (Test-Path $root) { Remove-Item $root -Recurse -Force }
New-Item -ItemType Directory -Force "$root\rt\runs", "$root\proj\dummy", "$root\proj\queue", "$root\wt" | Out-Null
[void](New-TestProject -Base $root -Repo "$root\repo" -Runtime "$root\rt")
$env:ORCH_RUNTIME_OVERRIDE = "$root\rt"; $env:ORCH_PLANS_OVERRIDE = "$root\proj"; $env:ORCH_TEST_NOSTART = '1'; $env:ORCH_QUESTIONS_FILE = "$root\QUESTIONS.md"; $env:ORCH_ANSWERS_FILE = "$root\ANSWERS.md"
. (Join-Path $tools 'lib.ps1'); . (Join-Path $tools 'routine.ps1')
$cfg = Get-Cfg
if ($cfg.runtime -ne "$root\rt") { throw 'runtime override not active: refusing to run against the live runtime' }
$plan = "$root\proj\dummy\DUMMY-PLAN.md"
$state = "$root\proj\dummy\DUMMY-STATE.md"
$fails = 0
Set-Content "$root\note.json" '{"actions":[{"action":"note","text":"test"}]}' -Encoding UTF8
# Records one check.
function Check([string]$name, [bool]$ok, [string]$detail = '') { if (-not $ok) { $script:fails++ }; Write-Host ('{0} {1} {2}' -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' }) }
# Runs one round in its own process (like the loop does); a fake brain answer is always given.
function Round { & pwsh -NoProfile -File (Join-Path $tools 'round.ps1') -BrainAnswerFile "$root\note.json" *> $null }
# Marks an agent as ended with a final answer.
function End-Agent([string]$n, [string]$final = '') { Update-Agent $cfg $n @{ status = 'done'; endedAt = (Get-Date).ToString('o'); exitCode = 0; lastLine = 'test' }; if ($final) { $d = Join-Path $cfg.runtime "runs\$n"; New-Item -ItemType Directory -Force $d | Out-Null; Set-Content (Join-Path $d 'final.txt') $final -Encoding UTF8 } }
# The routine decision of the dummy plan.
function Routine { $ctx = @{ Cfg = $cfg; Repo = "$root\repo"; Plans = @([pscustomobject]@{ path = $plan; status = 'IN_PROGRESS' }); Queue = @(); AllRows = @(); Questions = @(Get-Questions $cfg); CanStartIntegrator = $false; Free = @{ claude = 2; codex = 2; workers = 4; int = 0 } }; return (Get-Routine $ctx) }
# Start / lock actions of the routine for one package.
function Acts($r, [string]$act, [string]$pkg) { return @($r.Actions | Where-Object { $_.action -eq $act -and [string]$_.package -eq $pkg }) }
# Builds a plan with package $p and the dependent package $d, three verifies with open items for $p, and lets one round write the lock section.
function New-Locked([string]$p, [string]$d) {
    Remove-Item "$($cfg.runtime)\AGENTS.json", "$($cfg.runtime)\ORCH-HANDLED.json", "$root\QUESTIONS.md", "$root\ANSWERS.md" -ErrorAction SilentlyContinue; Remove-Item "$($cfg.runtime)\runs" -Recurse -Force -ErrorAction SilentlyContinue
    Update-Agent $cfg 'DUMMY-SEED-work' @{ type = 'claude'; plan = $plan; package = 'SEED'; phase = 'work'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-90).ToString('o'); endedAt = (Get-Date).AddMinutes(-80).ToString('o'); exitCode = 0; lastLine = 'seed' }
    Set-Content $plan ("# Dummy plan`nStatus: IN_PROGRESS`n`n| Package | Branch suffix | Content | Files | Depends on | Class |`n|---|---|---|---|---|---|`n| $p | a | stubborn | x | none | medium |`n| $d | b | follower | x | $p | medium |`n") -Encoding UTF8
    Set-Content $state "# State`n" -Encoding UTF8
    $min = 60
    foreach ($n in 1..3) {
        $sfx = if ($n -eq 1) { '' } else { "-$n" }
        Update-Agent $cfg "DUMMY-$p-work$sfx" @{ type = 'claude'; plan = $plan; package = $p; phase = 'work'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-$min).ToString('o'); endedAt = (Get-Date).AddMinutes(-$min + 5).ToString('o'); exitCode = 0; lastLine = 'x' }
        Update-Agent $cfg "DUMMY-$p-verify$sfx" @{ type = 'codex'; plan = $plan; package = $p; phase = 'verify'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-$min + 10).ToString('o'); endedAt = (Get-Date).AddMinutes(-$min + 15).ToString('o'); exitCode = 0; lastLine = 'x' }
        End-Agent "DUMMY-$p-verify$sfx" "Offen: A.`nOffene Anforderungen: 1"
        $min -= 20
    }
    Round
}

# --- 1. no answer: the lock stays ------------------------------------------------------------------
New-Locked 'K1' 'D1'
Check 'lock section written by the round' ((Get-Content $state -Raw) -match 'Open for Owner - LOCK-K1')
$r = Routine
Check 'without an answer: lock_package action is still produced' ((Acts $r 'lock_package' 'K1').Count -eq 1)
Check 'without an answer: package is locked, dependent package does not start' ((Test-PackageLocked $cfg $plan 'K1') -and (Acts $r 'start_agent' 'D1').Count -eq 0 -and (Acts $r 'start_agent' 'K1').Count -eq 0)
Check 'an OPEN question with item LOCK-K1 is no answer' ($(Add-Content "$root\QUESTIONS.md" "`n## Q-20260101-001`n- Plan: $plan`n- Item: LOCK-K1`n- Question: how?`n- Status: OPEN`n" -Encoding UTF8; $r2 = Routine; (Acts $r2 'lock_package' 'K1').Count -eq 1))

# --- 2. answer in the state file: lock lifted, earlier failures forgiven ------------------------------------
Add-Content $state "`n### Decision by the owner on Open for Owner - LOCK-K1`nTry the other approach.`n" -Encoding UTF8
$r = Routine
Check 'answer: no lock_package action any more' ((Acts $r 'lock_package' 'K1').Count -eq 0)
Check 'answer: failures of the earlier verifies are forgiven' ((Get-PackageFailures $cfg @(Read-Agents $cfg) $plan 'K1') -eq 0) ([string](Get-PackageFailures $cfg @(Read-Agents $cfg) $plan 'K1'))
Check 'answer: release time is remembered in ORCH-HANDLED.json' ($null -ne (Get-LockReleaseTime $cfg $plan 'K1'))
Check 'answer: package is not locked for brain starts either' (-not (Test-PackageLocked $cfg $plan 'K1'))
$r = Routine
Check 'second round after the answer still does not lock' ((Acts $r 'lock_package' 'K1').Count -eq 0)
# A verify after the answer that still finds open items counts again (one failure, not locked).
Start-Sleep -Milliseconds 1100
Update-Agent $cfg 'DUMMY-K1-verify-4' @{ type = 'codex'; plan = $plan; package = 'K1'; phase = 'verify'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).ToString('o'); endedAt = (Get-Date).ToString('o'); exitCode = 0; lastLine = 'x' }
End-Agent 'DUMMY-K1-verify-4' "Offen: A.`nOffene Anforderungen: 1"
Check 'only verifies after the answer count (one failure)' ((Get-PackageFailures $cfg @(Read-Agents $cfg) $plan 'K1') -eq 1) ([string](Get-PackageFailures $cfg @(Read-Agents $cfg) $plan 'K1'))

# --- 3. answered question (status ANSWERED) lifts the lock; the answer starts work as before ----------------
New-Locked 'K2' 'D2'
Add-Content "$root\QUESTIONS.md" "`n## Q-20260101-002`n- Plan: $plan`n- Item: LOCK-K2`n- Question: how?`n- Status: ANSWERED`n- Answer: try the other approach`n" -Encoding UTF8
$r = Routine
Check 'ANSWERED question: no lock_package action' ((Acts $r 'lock_package' 'K2').Count -eq 0)
Check 'ANSWERED question: failures forgiven' ((Get-PackageFailures $cfg @(Read-Agents $cfg) $plan 'K2') -eq 0)
Check 'ANSWERED question: the answer still starts its work agent (Q-LOCK-K2)' ((Acts $r 'start_agent' 'Q-LOCK-K2').Count -eq 1)
Check 'ANSWERED question: no second work agent on the package itself' ((Acts $r 'start_agent' 'K2').Count -eq 0)

# --- 4. answer "erledigt": package finished, dependent package starts, no new work or verify ---------------------
New-Locked 'K3' 'D3'
Add-Content $state "`n### Entscheidung von Owner zu Open for Owner - LOCK-K3`nerledigt, keine weiteren Laeufe.`n" -Encoding UTF8
$r = Routine
Check 'erledigt: no lock_package action' ((Acts $r 'lock_package' 'K3').Count -eq 0)
Check 'erledigt: dependent package D3 starts' ((Acts $r 'start_agent' 'D3').Count -eq 1)
Check 'erledigt: no work or verify run on K3' ((Acts $r 'start_agent' 'K3').Count -eq 0)
New-Locked 'K4' 'D4'
Add-Content "$root\QUESTIONS.md" "`n## Q-20260101-003`n- Plan: $plan`n- Item: LOCK-K4`n- Question: how?`n- Status: ANSWERED`n- Answer: erledigt, no further runs`n" -Encoding UTF8
$r = Routine
Check 'erledigt via question: D4 starts, no run for K4 and no answer work agent' ((Acts $r 'start_agent' 'D4').Count -eq 1 -and (Acts $r 'start_agent' 'K4').Count -eq 0 -and (Acts $r 'start_agent' 'Q-LOCK-K4').Count -eq 0 -and (Acts $r 'lock_package' 'K4').Count -eq 0)

Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "failures: $fails"
exit $fails
