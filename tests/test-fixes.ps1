# Tests of three tool fixes, all with a test runtime and invented names (no pane is opened: ORCH_TEST_NOSTART; no model is called):
#  1. Arguments of helper processes are quoted: a job name with spaces and commas reaches the helper as ONE argument (Quote-Arg), and the call sites use it.
#  2. Folder and branch names of a package are slugs: only lower-case letters, digits and hyphens (ConvertTo-Slug); the prompt of the routine carries the slug names.
#  3. A blocked queue row without a plan (plan column "keiner") starts a fix agent with the row text as its task (plan 6.5).
. (Join-Path $PSScriptRoot 'common.ps1')
$tools = $ToolsRoot
$root = Join-Path ([IO.Path]::GetTempPath()) ('orch-test-fix-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force "$root\rt\runs", "$root\proj\dummy", "$root\proj\queue", "$root\wt" | Out-Null
[void](New-TestProject -Base $root -Repo "$root\repo" -Runtime "$root\rt")
$env:ORCH_RUNTIME_OVERRIDE = "$root\rt"; $env:ORCH_PLANS_OVERRIDE = "$root\proj"; $env:ORCH_TEST_NOSTART = '1'; $env:ORCH_QUESTIONS_FILE = "$root\QUESTIONS.md"
. (Join-Path $tools 'lib.ps1'); . (Join-Path $tools 'routine.ps1')
$cfg = Get-Cfg
if ($cfg.runtime -ne "$root\rt") { throw 'runtime override not active: refusing to run against the live runtime' }
$fails = 0
# Records one check.
function Check([string]$name, [bool]$ok, [string]$detail = '') { if (-not $ok) { $script:fails++ }; Write-Host ('{0} {1} {2}' -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' }) }
# Agent rows of the test runtime.
function Agents { return @(Read-Agents $cfg) }
# Runs one round in its own process. A fake brain answer (one note) is always given, so no model is ever called; the output is kept in round.out.
Set-Content "$root\note.json" (ConvertTo-Json -InputObject @{ actions = @(@{ action = 'note'; text = 'nothing to start' }) } -Depth 4) -Encoding UTF8
function Round { & pwsh -NoProfile -File (Join-Path $tools 'round.ps1') -BrainAnswerFile "$root\note.json" *>> "$root\round.out" }

# --- 1. quoted arguments --------------------------------------------------------------------------
$name = 'DUMMY-R4 experience, arena-work'
Set-Content "$root\echo-args.ps1" 'param([string]$Name, [int]$ParentPid = 0, [string]$Out = "")' -Encoding UTF8
Add-Content "$root\echo-args.ps1" 'Set-Content -Path $env:ECHO_OUT -Value @($Name, $ParentPid, $Out)' -Encoding UTF8
$env:ECHO_OUT = "$root\echo1.txt"
$p = Start-Process -FilePath 'pwsh.exe' -ArgumentList @('-NoProfile', '-File', (Quote-Arg "$root\echo-args.ps1"), '-Name', (Quote-Arg $name), '-ParentPid', '7', '-Out', (Quote-Arg "$root\a folder, with comma\x.txt")) -NoNewWindow -PassThru
[void]$p.WaitForExit(30000)
$got = @(Get-Content "$root\echo1.txt" -ErrorAction SilentlyContinue)
Check 'quoted: a name with spaces and a comma arrives as one argument' ($got.Count -eq 3 -and $got[0] -eq $name -and $got[1] -eq '7') ($got -join ' | ')
Check 'quoted: a path with spaces arrives as one argument' ($got.Count -eq 3 -and $got[2] -eq "$root\a folder, with comma\x.txt")
$env:ECHO_OUT = "$root\echo2.txt"
$p = Start-Process -FilePath 'pwsh.exe' -ArgumentList @('-NoProfile', '-File', "$root\echo-args.ps1", '-Name', $name) -NoNewWindow -PassThru
[void]$p.WaitForExit(30000)
$bad = @(Get-Content "$root\echo2.txt" -ErrorAction SilentlyContinue)
Check 'unquoted (the old call) does NOT deliver the name as one argument' (-not ($bad.Count -ge 1 -and $bad[0] -eq $name)) ($bad -join ' | ')
$runSrc = Get-Content (Join-Path $tools 'agent-run.ps1') -Raw
Check 'agent-run.ps1: head-ticker and sampler are started with quoted arguments' ($runSrc.Contains("'-Name', (Quote-Arg `$Name)") -and $runSrc.Contains("'-Out', (Quote-Arg `$bjSamples)"))

# --- 2. slug names --------------------------------------------------------------------------------
Check 'slug: letters, digits and hyphens only' ((ConvertTo-Slug 'R4 experience, arena, prison, torture, undead') -eq 'r4-experience-arena-prison-torture-undead')
Check 'slug: slashes and repeated separators collapse to one hyphen' ((ConvertTo-Slug '  A/B -- x__y ') -eq 'a-b-x-y') (ConvertTo-Slug '  A/B -- x__y ')
Check 'slug: nothing left gives pkg' ((ConvertTo-Slug ',,, ') -eq 'pkg')
Check 'slug: no other character, no hyphen at the ends' ((ConvertTo-Slug 'Feature/Mood: Test #1!') -match '^[a-z0-9]+(-[a-z0-9]+)*$')
# The prompt template for a package in the main checkout carries the slug folder and sub-branch names; the routine builds them with ConvertTo-Slug (static check of its source).
$wtText = Get-PromptText $cfg 'workInMainCheckout' @{ cwd = "$root\repo"; newWorktree = "$root\proj-dummy-" + (ConvertTo-Slug 'R4 experience, arena, prison'); subBranch = ((ConvertTo-Slug 'dummy') + '-' + (ConvertTo-Slug 'R4 experience, arena, prison')) }
Check 'prompt template: worktree folder and sub-branch are slugs' ($wtText.Contains('proj-dummy-r4-experience-arena-prison') -and $wtText.Contains('dummy-r4-experience-arena-prison') -and -not $wtText.Contains('experience,')) $wtText
$rSrc = Get-Content (Join-Path $tools 'routine.ps1') -Raw
Check 'routine: worktree folder, sub-branch and the naming rule use ConvertTo-Slug' ($rSrc.Contains('newWorktree = ((Split-Path $Ctx.Repo -Parent) + ''\'' + [string]$cfg.worktreePrefix + ''-'' + (ConvertTo-Slug $tag)') -and $rSrc.Contains('subBranch = ((ConvertTo-Slug $tag)') -and $rSrc.Contains('only lower-case letters, digits and hyphens'))
$plan = "$root\proj\dummy\DUMMY-PLAN.md"

# --- 3. blocked row without a plan ----------------------------------------------------------------
Remove-Item "$($cfg.runtime)\AGENTS.json", "$($cfg.runtime)\ORCH-HANDLED.json" -ErrorAction SilentlyContinue
Set-Content $plan ("# Dummy plan`nStatus: DONE`n`n| Package | Branch suffix | Content | Files | Depends on |`n|---|---|---|---|---|`n| P1 | one | first | a | none |`n") -Encoding UTF8
Set-Content "$root\proj\queue\QUEUE.md" "| Branch | Tip | Plan | Status | Notes | Date |`n|---|---|---|---|---|---|`n| chore/no-plan-fix | feed1234abcd | keiner | blockiert (two checks red) | fix the two checks | 2026-10-28 |`n" -Encoding UTF8
Round
$fx = @(Agents | Where-Object { $_.package -eq 'FIX-feed123' } | Select-Object -First 1)
Check 'row without a plan: a fix agent was started' ($fx.Count -eq 1 -and $fx[0].phase -eq 'work') $(if ($fx.Count) { $fx[0].name } else { 'no agent: ' + ((Get-Content "$root\round.out" -Tail 8 -ErrorAction SilentlyContinue) -join ' // ') })
$fp = if ($fx.Count) { Get-Content (Join-Path $cfg.runtime ('runs\' + $fx[0].name + '\prompt.md')) -Raw } else { '' }
Check 'row without a plan: the row text is the task' ($fp -match 'blockiert \(two checks red\)' -and $fp -match 'fix the two checks' -and $fp -match 'chore/no-plan-fix')
Check 'row without a plan: own worktree with a slug name' ($fp -match 'fix-chore-no-plan-fix')
Round
Check 'a second round does not start the same fix again' (@(Agents | Where-Object { $_.package -eq 'FIX-feed123' }).Count -eq 1)
Remove-Item "$($cfg.runtime)\AGENTS.json" -ErrorAction SilentlyContinue
Set-Content "$root\proj\queue\QUEUE.md" "| Branch | Tip | Plan | Status | Notes | Date |`n|---|---|---|---|---|---|`n| chore/other | bad99999 | some\unknown\PLAN.md | blockiert (x) | y | 2026-10-28 |`n" -Encoding UTF8
Round
Check 'row naming a plan that is not startable is still skipped (old behaviour)' (@(Agents | Where-Object { $_.package -like 'FIX-*' }).Count -eq 0)

Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
if ($fails -gt 0) { Write-Host "$fails check(s) failed" -ForegroundColor Red; exit 1 }
Write-Host 'all fix checks passed' -ForegroundColor Green
