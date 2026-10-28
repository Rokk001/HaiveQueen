# Test of rule 11 (only READY or IN_PROGRESS plans may get an agent): four dummy plans (DRAFT, PAUSED, READY, no header = no status) and a forced brain answer that tries to start an
# agent in every one of them. round.ps1 must reject DRAFT, PAUSED and "no status" with the rule 11 message and start only the READY plan (which then becomes IN_PROGRESS).
# Everything runs in a temporary folder with its own runtime and database; no pane is opened (ORCH_TEST_NOSTART) and no model is called (-BrainAnswerFile).
. (Join-Path $PSScriptRoot 'common.ps1')
$tools = $ToolsRoot
$root = Join-Path ([IO.Path]::GetTempPath()) ('orch-test-r11-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force "$root\rt\runs", "$root\wt" | Out-Null
[void](New-TestProject -Base $root -Repo "$root\repo" -Runtime "$root\rt")
$env:ORCH_RUNTIME_OVERRIDE = "$root\rt"; $env:ORCH_TEST_NOSTART = '1'
$plansDir = "$root\repo\docs\plans\dummy"
. (Join-Path $tools 'lib.ps1'); . (Join-Path $tools 'routine.ps1')
$cfg = Get-Cfg
if ($cfg.runtime -ne "$root\rt") { throw 'runtime override not active: refusing to run against the live runtime' }
$fails = 0
# Records one check.
function Check([string]$name, [bool]$ok, [string]$detail = '') { if (-not $ok) { $script:fails++ }; Write-Host ('{0} {1} {2}' -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' }) }
# Agent row by name (empty when there is none).
function Ag([string]$n) { return @(Read-Agents $cfg | Where-Object { $_.name -eq $n } | Select-Object -First 1) }

# Writes one dummy plan; an empty status gives a plan without a header (no status at all).
function New-DummyPlan([string]$stem, [string]$status) { Write-TestPlan "$plansDir\$stem-PLAN.md" $status @(@{ id = 'P1'; title = 'first' }) "Dummy plan $stem" }
# Status of a plan in the database (the plan files are read first).
function Get-DbPlanStatus([string]$stem) { [void](Sync-OrchPlans $cfg $cfg.repo); return [string](@(Get-OrchPlans $cfg | Where-Object { $_.path -eq "docs/plans/dummy/$stem-PLAN.md" })[0].status) }
$stems = [ordered]@{ DRAFT = 'DRAFT'; PAUSED = 'PAUSED'; READY = 'READY'; NOSTATUS = '' }
foreach ($k in $stems.Keys) { New-DummyPlan $k $stems[$k] }

# The forced brain answer proposes start_agent for all four plans.
$actions = @(); foreach ($k in $stems.Keys) { $actions += @{ action = 'start_agent'; name = "R11-$k-work"; type = 'claude'; phase = 'work'; plan = "docs/plans/dummy/$k-PLAN.md"; package = 'P1'; cwd = "$root\wt"; prompt = 'Do package P1.' } }
Set-Content "$root\forced.json" (ConvertTo-Json -InputObject @{ actions = $actions } -Depth 6) -Encoding UTF8
& pwsh -NoProfile -File (Join-Path $tools 'round.ps1') -BrainAnswerFile "$root\forced.json" *> $null

Check 'READY plan: agent started' ((Ag 'R11-READY-work').status -eq 'running')
foreach ($k in 'DRAFT', 'PAUSED', 'NOSTATUS') { Check "$k plan: no agent started" (@(Ag "R11-$k-work").Count -eq 0) }
$log = Get-Content "$root\rt\ROUNDS.log" -Raw
foreach ($k in 'DRAFT', 'PAUSED', 'NOSTATUS') { Check "$k plan: rejected with the rule 11 message" ($log -match "(?s)R11-$k-work.*?rule 11" -or $log -match "(?s)rule 11.*?R11-$k-work") }
Check 'READY plan: READY -> IN_PROGRESS (table and plan header)' ((Get-DbPlanStatus 'READY') -eq 'IN_PROGRESS' -and (Get-Content "$plansDir\READY-PLAN.md" -Raw) -match 'status = "IN_PROGRESS"')
Check 'DRAFT and PAUSED plans keep their status' ((Get-DbPlanStatus 'DRAFT') -eq 'DRAFT' -and (Get-DbPlanStatus 'PAUSED') -eq 'PAUSED')

Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
if ($fails -gt 0) { Write-Host "$fails check(s) failed" -ForegroundColor Red; exit 1 }
Write-Host 'all rule 11 checks passed' -ForegroundColor Green
