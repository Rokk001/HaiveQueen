# S10 test (plan sections 12.2-12.4): effort classes from the package table, bundling of small packages, escalation, minimum class of verify and integrator.
# Everything runs in a temporary folder with its own AGENTS.json and dummy plans; the live runtime is never touched. No pane is opened (ORCH_TEST_NOSTART), no model is called.
. (Join-Path $PSScriptRoot 'common.ps1')
$tools = $ToolsRoot
$root = Join-Path ([IO.Path]::GetTempPath()) ('orch-test-s10-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
if (Test-Path $root) { Remove-Item $root -Recurse -Force }
New-Item -ItemType Directory -Force "$root\rt\runs", "$root\proj\dummy", "$root\proj\queue", "$root\wt" | Out-Null
[void](New-TestProject -Base $root -Repo "$root\repo" -Runtime "$root\rt")
$env:ORCH_RUNTIME_OVERRIDE = "$root\rt"; $env:ORCH_PLANS_OVERRIDE = "$root\proj"; $env:ORCH_TEST_NOSTART = '1'; $env:ORCH_QUESTIONS_FILE = "$root\QUESTIONS.md"
. (Join-Path $tools 'lib.ps1'); . (Join-Path $tools 'routine.ps1')
$cfg = Get-Cfg
if ($cfg.runtime -ne "$root\rt") { throw 'runtime override not active: refusing to run against the live runtime' }
$plan = "$root\proj\dummy\DUMMY-PLAN.md"
$fails = 0
Set-Content "$root\note.json" '{"actions":[{"action":"note","text":"test"}]}' -Encoding UTF8
# Records one check.
function Check([string]$name, [bool]$ok, [string]$detail = '') { if (-not $ok) { $script:fails++ }; Write-Host ('{0} {1} {2}' -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' }) }
# Agent row by name.
function Ag([string]$n) { return @(Read-Agents $cfg | Where-Object { $_.name -eq $n } | Select-Object -First 1) }
# Runs one round in its own process (like the loop does).
function Round { & pwsh -NoProfile -File (Join-Path $tools 'round.ps1') -BrainAnswerFile "$root\note.json" *> $null }   # a fake brain answer is always given: no real brain call
# Marks an agent as ended with an optional final answer.
function End-Agent([string]$n, [string]$final = '', [string]$status = 'done') { Update-Agent $cfg $n @{ status = $status; endedAt = (Get-Date).ToString('o'); exitCode = 0; lastLine = 'test' }; if ($final) { $d = Join-Path $cfg.runtime "runs\$n"; New-Item -ItemType Directory -Force $d | Out-Null; Set-Content (Join-Path $d 'final.txt') $final -Encoding UTF8 } }
# Writes the dummy plan from package rows (Id, Content, Depends, Class).
function New-Plan($rows) {
    $t = "# Dummy plan`nStatus: IN_PROGRESS`n`n| Package | Branch suffix | Content | Files | Depends on | Class |`n|---|---|---|---|---|---|`n"
    foreach ($r in $rows) { $t += ('| {0} | {1} | {2} | x | {3} | {4} |' -f $r[0], $r[0].ToLower(), $r[1], $r[2], $r[3]) + "`n" }
    Set-Content $plan $t -Encoding UTF8
}
# Empties the agent list and the run folders of the test runtime.
function Reset-Agents {
    Remove-Item "$($cfg.runtime)\AGENTS.json", "$($cfg.runtime)\ORCH-HANDLED.json" -ErrorAction SilentlyContinue; Remove-Item "$($cfg.runtime)\runs" -Recurse -Force -ErrorAction SilentlyContinue
    # The seed row gives the plan a known worktree, so the routine plans it without the brain.
    Update-Agent $cfg 'DUMMY-SEED-work' @{ type = 'claude'; plan = $plan; package = 'SEED'; phase = 'work'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-30).ToString('o'); endedAt = (Get-Date).AddMinutes(-20).ToString('o'); exitCode = 0; lastLine = 'seed' }
}

# --- 1. units: class column, class resolution, class models -------------------------------------
New-Plan @(@('A1', 'one', 'none', 'light'), @('A2', 'two', 'none', 'Heavy'), @('A3', 'three', 'none', ''), @('A4', 'four', 'none', 'bogus'))
$pk = @(Get-PlanPackages $plan)
Check 'Class column: light / heavy / empty / bogus' (($pk[0].Class -eq 'light') -and ($pk[1].Class -eq 'heavy') -and ($pk[2].Class -eq 'medium') -and ($pk[3].Class -eq 'medium')) (($pk | ForEach-Object { $_.Class }) -join ',')
Set-Content $plan "# No class column`nStatus: IN_PROGRESS`n`n| Package | Content | Depends on |`n|---|---|---|`n| B1 | x | none |`n" -Encoding UTF8
Check 'plan without Class column = medium' ((@(Get-PlanPackages $plan))[0].Class -eq 'medium')
Check 'verify never below medium' ((Resolve-AgentClass $cfg 'verify' 'light' 0) -eq 'medium')
Check 'integrator never below medium' ((Resolve-AgentClass $cfg 'integrate' 'light' 0) -eq 'medium')
Check 'verify keeps heavy' ((Resolve-AgentClass $cfg 'verify' 'heavy' 0) -eq 'heavy')
Check 'work light stays light without failures' ((Resolve-AgentClass $cfg 'work' 'light' 1) -eq 'light')
Check 'light after 2 failures -> medium' ((Resolve-AgentClass $cfg 'work' 'light' 2) -eq 'medium')
Check 'light after 4 failures -> heavy' ((Resolve-AgentClass $cfg 'work' 'light' 4) -eq 'heavy')
Check 'medium after 2 failures -> heavy, heavy stays' (((Resolve-AgentClass $cfg 'work' 'medium' 2) -eq 'heavy') -and ((Resolve-AgentClass $cfg 'work' 'heavy' 9) -eq 'heavy'))
Check 'unknown class = medium' ((Resolve-AgentClass $cfg 'work' 'bogus' 0) -eq 'medium')
$cl = Get-ClassModel $cfg 'claude' 'light'; $cm = Get-ClassModel $cfg 'claude' 'medium'; $ch = Get-ClassModel $cfg 'claude' 'heavy'
Check 'Claude classes: haiku / sonnet / opus' (($cl.Model -eq 'claude-haiku-5-5') -and ($cm.Model -eq 'claude-sonnet-5-5') -and ($ch.Model -eq 'claude-opus-5-5')) "$($cl.Model)/$($cm.Model)/$($ch.Model)"
Check 'medium class: Sonnet / Terra, effort medium' (($cm.Model -eq 'claude-sonnet-5-5') -and ($cm.Effort -eq 'medium') -and ((Get-ClassModel $cfg 'codex' 'medium').Model -eq 'gpt-6.1-terra'))
$cxl = Get-ClassModel $cfg 'codex' 'light'
$cache = Get-Content (Join-Path $env:USERPROFILE '.codex\models_cache.json') -Raw -ErrorAction SilentlyContinue
if ($cache) { Check 'Codex light model is listed by the installed Codex CLI' ($cache.Contains('"' + $cxl.Model + '"')) $cxl.Model } else { Write-Host 'SKIP Codex light model check: no Codex model cache on this PC' }

# --- 2. bundling: light packages of one plan share one agent ----------------------------------------
Reset-Agents
New-Plan @(@('L1', 'first small', 'none', 'light'), @('L2', 'second small', 'none', 'light'), @('L3', 'third small', 'none', 'light'), @('L4', 'fourth small', 'none', 'light'), @('M1', 'medium task', 'none', 'medium'), @('L5', 'needs L1', 'L1', 'light'))
Round
$rows = @(Read-Agents $cfg)
$b1 = @($rows | Where-Object { $_.phase -eq 'work' -and @($_.bundle).Count -gt 1 } | Select-Object -First 1)
Check 'bundle: up to maxPackages light packages in one agent' ($b1.Count -eq 1 -and @($b1[0].bundle).Count -eq [int]$cfg.bundle.maxPackages) ($(if ($b1.Count) { @($b1[0].bundle) -join '+' } else { 'no bundle' }))
Check 'bundle agent has class light and the light model' ($b1.Count -eq 1 -and $b1[0].class -eq 'light' -and [string]$b1[0].modelShort -match 'Hai') ($(if ($b1.Count) { "$($b1[0].class) $($b1[0].modelShort)" } else { '' }))
$pr = if ($b1.Count) { Get-Content (Join-Path $cfg.runtime "runs\$($b1[0].name)\prompt.md") -Raw -Encoding UTF8 } else { '' }
Check 'bundle prompt names every package' (($pr -match 'package L1') -and ($pr -match 'package L2') -and ($pr -match 'package L3'))
Check 'bundled packages count as running (no second agent for L2/L3)' (@($rows | Where-Object { $_.phase -eq 'work' -and $_.package -in @('L2', 'L3') }).Count -eq 0)
Check 'dependent light package L5 does not start' (@($rows | Where-Object { $_.package -eq 'L5' }).Count -eq 0)
$others = @($rows | Where-Object { $_.phase -eq 'work' -and @($_.bundle).Count -le 1 })
Check 'second agent: M1 (medium) or the remaining light package L4' (@($others | Where-Object { ($_.package -eq 'M1' -and $_.class -eq 'medium') -or ($_.package -eq 'L4' -and $_.class -eq 'light') }).Count -ge 1) (($others | ForEach-Object { "$($_.package):$($_.class)" }) -join ',')
Check 'at most 2 Claude + 2 Codex workers (slots)' (@($rows | Where-Object { $_.phase -eq 'work' }).Count -le 4)

# --- 3. a bundled package is verified separately, verify runs at least at medium ----------------------
if ($b1.Count) {
    End-Agent $b1[0].name; Round
    $vrows = @(Read-Agents $cfg | Where-Object { $_.phase -eq 'verify' })
    Check 'bundle ended -> verify agents for the bundled packages' ($vrows.Count -ge 1) "verify rows=$($vrows.Count)"
    Check 'verify rows run at least at medium' (@($vrows | Where-Object { $_.class -eq 'light' }).Count -eq 0 -and @($vrows | Where-Object { $_.class -eq 'medium' }).Count -eq $vrows.Count) (($vrows | ForEach-Object { "$($_.package):$($_.class)" }) -join ',')
}

# --- 4. escalation: light stays light after one open verify, medium after two ---------------------------
Reset-Agents
New-Plan @(, @('E1', 'single light task', 'none', 'light'))
Round
$e1 = Ag 'DUMMY-E1-work'
Check 'single light package starts as light' ($e1.Count -eq 1 -and $e1[0].class -eq 'light' -and @($e1[0].bundle).Count -le 1)
End-Agent 'DUMMY-E1-work'; Round
End-Agent 'DUMMY-E1-verify' "Offene Anforderungen: 2"; Round
$f1 = Ag 'DUMMY-E1-work-2'
Check 'after one open verify the repeat is still light' ($f1.Count -eq 1 -and $f1[0].class -eq 'light') ($f1 | ForEach-Object { $_.class })
End-Agent 'DUMMY-E1-work-2'; Round
End-Agent 'DUMMY-E1-verify-2' "Offene Anforderungen: 1"; Round
$f2 = Ag 'DUMMY-E1-work-3'
Check 'after two open verifies the repeat is one class higher (medium)' ($f2.Count -eq 1 -and $f2[0].class -eq 'medium' -and [string]$f2[0].modelShort -match 'Son') ($f2 | ForEach-Object { "$($_.class) $($_.modelShort)" })

# --- 5. lock: repeated open verifies stop the work agents and report the package as a question ------------
Reset-Agents
New-Plan @(, @('K1', 'stubborn task', 'none', 'medium'))
Set-Content "$root\proj\dummy\DUMMY-STATE.md" "# State`n" -Encoding UTF8
Update-Agent $cfg 'DUMMY-K1-work' @{ type = 'claude'; plan = $plan; package = 'K1'; phase = 'work'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-60).ToString('o'); endedAt = (Get-Date).AddMinutes(-55).ToString('o'); exitCode = 0; lastLine = 'x' }
Update-Agent $cfg 'DUMMY-K1-verify' @{ type = 'codex'; plan = $plan; package = 'K1'; phase = 'verify'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-50).ToString('o'); endedAt = (Get-Date).AddMinutes(-45).ToString('o'); exitCode = 0; lastLine = 'x' }
End-Agent 'DUMMY-K1-verify' "Offen: A.`nOffene Anforderungen: 2"
Update-Agent $cfg 'DUMMY-K1-work-2' @{ type = 'claude'; plan = $plan; package = 'K1'; phase = 'work'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-40).ToString('o'); endedAt = (Get-Date).AddMinutes(-35).ToString('o'); exitCode = 0; lastLine = 'x' }
Update-Agent $cfg 'DUMMY-K1-verify-2' @{ type = 'codex'; plan = $plan; package = 'K1'; phase = 'verify'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-30).ToString('o'); endedAt = (Get-Date).AddMinutes(-25).ToString('o'); exitCode = 0; lastLine = 'x' }
End-Agent 'DUMMY-K1-verify-2' "Offen: A.`nOffene Anforderungen: 1"
Check 'two open verifies: any work start (also from the brain) is one class higher' ((Get-StartClass $cfg 'work' $plan 'K1' '') -eq 'heavy') (Get-StartClass $cfg 'work' $plan 'K1' '')
Check 'two open verifies: package is not locked yet' (-not (Test-PackageLocked $cfg $plan 'K1'))
Update-Agent $cfg 'DUMMY-K1-work-3' @{ type = 'claude'; plan = $plan; package = 'K1'; phase = 'work'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-20).ToString('o'); endedAt = (Get-Date).AddMinutes(-15).ToString('o'); exitCode = 0; lastLine = 'x' }
Update-Agent $cfg 'DUMMY-K1-verify-3' @{ type = 'codex'; plan = $plan; package = 'K1'; phase = 'verify'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-10).ToString('o'); endedAt = (Get-Date).AddMinutes(-5).ToString('o'); exitCode = 0; lastLine = 'x' }
End-Agent 'DUMMY-K1-verify-3' "Offen: A.`nOffene Anforderungen: 1"
Check 'third open verify: package is locked' (Test-PackageLocked $cfg $plan 'K1')
Round
$before = @(Read-Agents $cfg).Count
Check 'locked package: no new agent' (@(Read-Agents $cfg | Where-Object { $_.name -like 'DUMMY-K1-work-4*' -or $_.name -like 'DUMMY-K1-verify-4*' }).Count -eq 0)
Check 'locked package: reported once as Open for Owner in the state file' ((Get-Content "$root\proj\dummy\DUMMY-STATE.md" -Raw) -match 'Open for Owner - LOCK-K1')
Round
Check 'second round does not write the question twice' (([regex]::Matches((Get-Content "$root\proj\dummy\DUMMY-STATE.md" -Raw), 'Open for Owner - LOCK-K1')).Count -eq 1)

# --- 6. a pane wrapper that fails at start (invalid project file) closes its pane after the delay with exit 0 ----
$pw = "$root\panewrap"; New-Item -ItemType Directory -Force $pw | Out-Null
Copy-Item (Join-Path $tools 'lib.ps1'), (Join-Path $tools 'pane-wrap.ps1') $pw
Set-Content "$pw\bad.json" ('{ "x": "C:' + [char]92 + 'Users" }') -Encoding UTF8
$savedProject = $env:ORCH_PROJECT; $env:ORCH_PROJECT = "$pw\bad.json"
$t0 = Get-Date
$out = & pwsh -NoProfile -File "$pw\pane-wrap.ps1" -Name ZZ 2>&1 | Out-String
$env:ORCH_PROJECT = $savedProject
Check 'pane wrapper with invalid config: error shown, exit 0' (($LASTEXITCODE -eq 0) -and ($out -match 'pane start error')) "exit=$LASTEXITCODE"
Check 'pane wrapper waited about 30 s before closing' (((Get-Date) - $t0).TotalSeconds -ge 29)

Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "failures: $fails"
exit $fails
