# Test of the v2 runtime wrappers in lib.ps1 (Invoke-Orch and the Get-/Set-/Add-Orch* functions): agents, events with text mirror, kv, handled, nofollow, builds, usage, plans, queue,
# questions/decisions, argument quoting (quotes, trailing backslash, umlauts), error handling and several processes writing at once. Everything runs in a temporary folder with its own database.
. (Join-Path $PSScriptRoot 'common.ps1')
$tools = $ToolsRoot
$root = Join-Path ([IO.Path]::GetTempPath()) ('orch-test-wr-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force "$root\rt", "$root\data", "$root\logs", "$root\ws\docs\plans" | Out-Null
$pf = New-TestProject -Base $root -Repo "$root\repo" -Runtime "$root\rt"
$env:ORCH_RUNTIME_OVERRIDE = "$root\rt"
$env:ORCH_DATA_DIR = "$root\data"; $env:ORCH_CONFIG_DIR = "$root\cfg"; $env:ORCH_LOG_DIR = "$root\logs"
$env:ORCH_PROJECT_NAME = 'wtest'
if (-not $env:ORCH_PYTHON) { $env:ORCH_PYTHON = (Get-Command python).Source }
. (Join-Path $tools 'lib.ps1')
$cfg = Get-Cfg
if ($cfg.runtime -ne "$root\rt") { throw 'runtime override not active: refusing to run against the live runtime' }
$fails = 0
# Records one check.
function Check([string]$name, [bool]$ok, [string]$detail = '') { if (-not $ok) { $script:fails++ }; Write-Host ('{0} {1} {2}' -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' }) }

try {
    Check 'project name from the environment' ((Get-OrchProject $cfg) -eq 'wtest')
    Check 'test mutex name differs from the live name' ((Get-MutexName 'X' 'AgentsJson') -ne ('Global\XAgentsJson'))

    # agents
    $a = Set-OrchAgent $cfg 'Max' @{ status = 'running'; plan = 'a.md'; startedAt = '2020-01-01T00:00:00'; buildJobs = 3; lastLine = 'say "hi" \' }
    Check 'agent created, free field kept, quotes and trailing backslash survive' ($a.status -eq 'running' -and $a.plan_path -eq 'a.md' -and $a.buildJobs -eq 3 -and $a.last_line -eq 'say "hi" \') $a.last_line
    [void](Update-OrchAgentFields $cfg 'Max' @{ status = 'done'; endedAt = '2020-01-01T01:00:00' })
    $g = Get-OrchAgent $cfg 'Max'
    Check 'agent fields merged' ($g.status -eq 'done' -and $g.buildJobs -eq 3 -and $g.ended_at -eq '2020-01-01T01:00:00')
    Check 'unknown agent returns null' ($null -eq (Get-OrchAgent $cfg 'Nobody'))
    $threw = $false; try { [void](Update-OrchAgentFields $cfg 'Nobody' @{ status = 'x' }) } catch { $threw = $true; $msg = $_.Exception.Message }
    Check 'update of an unknown agent throws with the message' ($threw -and $msg -match 'unknown agent') $msg
    [void](Set-OrchAgent $cfg 'Fritz' @{ status = 'running' })
    Check 'two active agents' ((Get-OrchAgents $cfg).Count -eq 2)
    Check 'archive moves the old ended row' ((Move-OrchAgentsToArchive $cfg 12) -eq 1 -and (Get-OrchAgents $cfg).Count -eq 1 -and (Get-OrchAgents $cfg 'all').Count -eq 2 -and (Get-OrchAgents $cfg 'archive')[0].name -eq 'Max')

    # events and text mirror
    [void](Add-OrchEvent $cfg 'round started')
    [void](Add-OrchEvent $cfg 'Fehler  beim Start: ä ö ü' 'events' $true)
    $paths = Get-OrchLogPaths $cfg
    $r = @(Get-Content $paths.rounds -Encoding UTF8)
    $e = @(Get-Content $paths.events -Encoding UTF8)
    Check 'rounds mirror line has the timestamp format' ($r.Count -eq 1 -and $r[0] -match '^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d round started$') ($r -join '|')
    Check 'events mirror line has HH:mm|E|text, umlauts intact' ($e.Count -eq 1 -and $e[0] -match '^\d\d:\d\d\|E\|Fehler beim Start: ä ö ü$') ($e -join '|')
    Check 'events listed and tailed' ((Get-OrchEvents $cfg).Count -eq 2 -and (Get-OrchEvents $cfg -Tail 1)[0].text -match 'Fehler' -and (Get-OrchEvents $cfg -SinceId 1).Count -eq 1)

    # kv, handled, nofollow
    Check 'missing kv is null' ($null -eq (Get-OrchKv $cfg 'state'))
    Set-OrchKv $cfg 'state' '{"brain":"idle","text":"ä \"q\""}'
    Check 'kv round trip' ((Get-OrchKv $cfg 'state') -eq '{"brain":"idle","text":"ä \"q\""}')
    Remove-OrchKv $cfg 'state'
    Check 'kv deleted' ($null -eq (Get-OrchKv $cfg 'state'))
    Check 'key not handled at first' (-not (Get-OrchHandled $cfg 'k1').handled)
    Add-OrchHandled $cfg 'try:k1' '2'
    $h = Get-OrchHandled $cfg 'try:k1'
    Check 'handled key with counter' ($h.handled -and $h.value -eq '2')
    Remove-OrchHandled $cfg 'try:k1'
    Check 'handled key removed' (-not (Get-OrchHandled $cfg 'try:k1').handled)
    Add-OrchNofollow $cfg 'A-Plan.md|A6'
    Check 'nofollow key stored lower case' ((Get-OrchNofollow $cfg) -contains 'a-plan.md|a6')
    Remove-OrchNofollow $cfg 'a-plan.md|a6'
    Check 'nofollow key removed' ((Get-OrchNofollow $cfg).Count -eq 0)

    # builds and usage
    Check 'no build measured yet' ($null -eq (Get-OrchBuildMax $cfg 'h1'))
    foreach ($v in 600, 900, 400) { Add-OrchBuild $cfg 2 8000 6000 $v 60 'h1' }
    Check 'build maximum per job' ((Get-OrchBuildMax $cfg 'h1') -eq 900)
    Add-OrchUsage $cfg @{ kind = 'brain'; model = 'm'; tokensIn = 10; tokensOut = 5; trigger = 'event'; context = 1234; durationS = 3.5; actions = 'a;b'; error = $false }
    Add-OrchUsage $cfg @{ kind = 'agent'; provider = 'x'; tokensIn = 1 }
    $u = @(Get-OrchUsage $cfg -Kind 'brain')
    Check 'brain usage row keeps the extra fields' ($u.Count -eq 1 -and $u[0].trigger -eq 'event' -and $u[0].context -eq 1234 -and $u[0].actions -eq 'a;b' -and $u[0].error -eq 0)
    Check 'usage list all' ((Get-OrchUsage $cfg).Count -eq 2)

    # plans
    $plan = "$root\ws\docs\plans\one.md"
    [IO.File]::WriteAllText($plan, "+++`nstatus = `"READY`"`n[[packages]]`nid = `"P1`"`ntitle = `"First`"`n[[packages]]`nid = `"P2`"`ndepends_on = [`"P1`"]`n+++`n# One`n", (New-Object System.Text.UTF8Encoding($false)))
    $s = Sync-OrchPlans $cfg "$root\ws"
    Check 'plan synced, only P1 startable' ($s.plans.Count -eq 1 -and @($s.startable).Count -eq 1 -and $s.startable[0].id -eq 'P1')
    [void](Set-OrchPackage $cfg 'docs/plans/one.md' 'P1' -Phase 'done')
    Check 'after P1 is done P2 is startable' ((Sync-OrchPlans $cfg "$root\ws").startable[0].id -eq 'P2')
    [void](Set-OrchPackage $cfg 'docs/plans/one.md' 'P2' -Locked $true -Reason 'needs input')
    Check 'locked package is not startable' (@((Sync-OrchPlans $cfg "$root\ws").startable).Count -eq 0 -and (Get-OrchPackages $cfg).Count -eq 2)
    [void](Set-OrchPlanStatus $cfg 'docs/plans/one.md' 'IN_PROGRESS' "$root\ws")
    Check 'plan status written to table and file' ((Get-OrchPlans $cfg)[0].status -eq 'IN_PROGRESS' -and (Get-Content $plan -Raw) -match 'status = "IN_PROGRESS"')

    # queue, questions, decisions
    [void](Set-OrchQueue $cfg 'b1' @{ tip = 't1'; plan = 'x.md' })
    $q = Set-OrchQueue $cfg 'b1' @{ status = 'in_run'; run = 'r1' }
    Check 'queue row changed in place' ($q.status -eq 'in_run' -and $q.tip -eq 't1' -and (Get-OrchQueue $cfg).Count -eq 1)
    $qid = (Add-OrchQuestion $cfg 'x.md' 'I1' 'Which?' 'A').id
    Check 'open question found by item' ((Get-OrchQuestions $cfg -Status 'open' -Item 'I1').Count -eq 1)
    [void](Invoke-Orch @('answer', "$qid", 'A', '--by', 'owner'))
    Check 'answer is a decision with the item' ((Get-OrchDecisions $cfg -Item 'I1').Count -eq 1 -and (Get-OrchQuestions $cfg -Status 'open').Count -eq 0)
    [void](Add-OrchDecision $cfg 'auto' 'tech choice' 'x.md' 'I2')
    Check 'decision added' ((Get-OrchDecisions $cfg).Count -eq 2)

    # errors
    $threw = $false; try { [void](Invoke-Orch @('kv', 'get', '--project', 'wtest')) } catch { $threw = $true }
    Check 'bad arguments throw' $threw

    # several processes at once
    $procs = foreach ($n in 1..4) {
        Start-Process -FilePath 'pwsh' -ArgumentList @('-NoProfile', '-Command', "`$env:ORCH_PROJECT = '$pf'; . '$tools\lib.ps1'; `$c = Get-Cfg; foreach (`$i in 1..5) { [void](Add-OrchEvent `$c 'p$n-`$i' 'both'); [void](Set-OrchAgent `$c 'Shared' @{ ('f${n}_' + `$i) = `$i }); Set-OrchKv `$c ('k${n}_' + `$i) 'v' }") -NoNewWindow -PassThru
    }
    foreach ($p in $procs) { if (-not $p.WaitForExit(120000)) { $p.Kill($true) } }
    Check 'parallel processes all exit 0' (@($procs | Where-Object { $_.ExitCode -ne 0 }).Count -eq 0)
    $shared = Get-OrchAgent $cfg 'Shared'
    $fields = @($shared.PSObject.Properties.Name | Where-Object { $_ -match '^f\d_\d$' })
    Check 'parallel agent updates lost nothing (20 fields)' ($fields.Count -eq 20) "$($fields.Count)"
    Check 'parallel events all stored (20)' (@(Get-OrchEvents $cfg | Where-Object { $_.text -match '^p\d-' }).Count -eq 20)
    Check 'parallel kv writes all stored' ((Invoke-Orch @('kv', 'list', '--project', 'wtest', '--prefix', 'k')).Count -eq 20)
    Check 'parallel mirror lines all written' (@(Get-Content $paths.rounds).Count -eq 21)
}
finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
if ($fails -gt 0) { Write-Host "$fails check(s) failed" -ForegroundColor Red; exit 1 }
Write-Host 'all checks passed' -ForegroundColor Green
