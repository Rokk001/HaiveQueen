# One orchestrator round: snapshot -> fresh Claude "brain" -> validated actions -> execution (or just printing with -DryRun).
param(
    [switch]$DryRun,
    [string[]]$PlanFiles,      # optional: only these plan files (pilot); default = all plans under the plans root of the project file
    [string]$OutFile,          # optional: where -DryRun writes the brain answer + enforcement result
    [string]$BrainAnswerFile,  # test hook: use this JSON file as the brain answer (no claude call)
    [string]$Project = ''      # project file (default: $env:ORCH_PROJECT)
)
if ($Project) { $env:ORCH_PROJECT = $Project }
. (Join-Path $PSScriptRoot 'lib.ps1')
. (Join-Path $PSScriptRoot 'routine.ps1')
. (Join-Path $PSScriptRoot 'events.ps1')
$cfg = Get-Cfg
$repo = $cfg.repo
$brainTrigger = ''; $brainCtx = $null; $brainIn = $null; $brainOut = $null; $brainModel = ''; $brainSec = $null
# Pilot mode (config.json pilot=true): ONLY packages of PILOT-PLAN.md, never an integrator, never packages of real plans. Enforced here, not only in the brain prompt.
$pilotMode = ($cfg.pilot -eq $true)
$pilotPlan = [string]$cfg.pilotPlan
if ($pilotMode) { $PlanFiles = @($pilotPlan) }
$proj = $cfg.projectsRoot   # the plans root of the project file (ORCH_PLANS_OVERRIDE only in tests: dummy plans)
New-Item -ItemType Directory -Force (Join-Path $cfg.runtime 'runs') | Out-Null
$stopFlag = Join-Path $cfg.runtime 'STOP'
$tag = if ($DryRun) { 'DRYRUN ' } else { '' }
# Relative path helper.
function Rel([string]$p) { if ($p.StartsWith($repo + '\')) { return $p.Substring($repo.Length + 1) }; return $p }
# Absolute path of a plan given relative to the repo or already absolute.
function Abs([string]$p) { if ([System.IO.Path]::IsPathRooted($p)) { return $p }; return (Join-Path $repo $p) }
# Appends the brain call of this round (only called when the brain ran) to BRAIN-USAGE.jsonl: ts, trigger, model, context, input, output, durationSec, actions, error.
function Save-Brain([string]$Actions, [bool]$IsErr = $false) {
    if ($DryRun -and -not $env:ORCH_RUNTIME_OVERRIDE) { return }
    Add-BrainRow $cfg ([ordered]@{ ts = (Get-Date).ToString('o'); trigger = $brainTrigger; model = $(if ($brainModel) { $brainModel } else { Get-ModelLabel $cfg.brainModel }); context = $brainCtx; input = $brainIn; output = $brainOut; durationSec = $brainSec; actions = $Actions; error = $IsErr })
}

Write-Host ("[{0}] {1}round start" -f (Get-Date -Format 'HH:mm:ss'), $tag) -ForegroundColor Cyan

# ---- snapshot -------------------------------------------------------------------------------
$planObjs = @()
$files = if ($PlanFiles) { $PlanFiles | ForEach-Object { Get-Item (Abs $_) } }
         else { Get-PlanFiles $proj }
foreach ($f in $files) {
    $states = @(Get-PlanStateFiles $f.FullName | ForEach-Object { [ordered]@{ path = (Rel $_.FullName); sizeKB = [int]($_.Length / 1KB); modified = $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm') } })
    $st = Get-PlanStatus $f.FullName
    $planObjs += [ordered]@{ path = (Rel $f.FullName); status = $st; sizeKB = [int]($f.Length / 1KB); modified = $f.LastWriteTime.ToString('yyyy-MM-dd HH:mm'); stateFiles = $states }
}
$planObjs = @($planObjs | Sort-Object { $_.modified } -Descending)
# Rule 11: only READY / IN_PROGRESS plans are startable; all others are only counted (DRAFT is shown as 'in Arbeit bei <owner>').
$startablePlans = @($planObjs | Where-Object { $_.status -eq 'READY' -or $_.status -eq 'IN_PROGRESS' })
$otherPlans = @($planObjs | Where-Object { $_.status -ne 'READY' -and $_.status -ne 'IN_PROGRESS' } | ForEach-Object { [ordered]@{ path = $_.path; status = $_.status } })

# Integration queue: last row per branch; only ready / in Lauf / blockiert are reported.
$rows = [ordered]@{}
$isPath = Get-QueuePath $cfg
if (Test-Path $isPath) {
    foreach ($l in (Get-Content $isPath -Encoding UTF8)) {
        if ($l -notmatch '^\|') { continue }
        $c = $l.Split('|')
        if ($c.Count -lt 7) { continue }
        $br = $c[1].Trim()
        if ($br -eq 'Branch' -or $br -match '^-+$') { continue }
        $rows[$br] = [ordered]@{ branch = $br; tip = $c[2].Trim().Substring(0, [Math]::Min(9, $c[2].Trim().Length)); plan = $c[3].Trim(); status = $c[4].Trim(); note = $c[5].Trim(); date = $c[6].Trim() }
    }
}
$allRows = @($rows.Values | ForEach-Object { [pscustomobject]$_ })   # every queue row incl. integriert (package-done detection of the routine)
$queue = @($rows.Values | Where-Object { $_.status -match '^(ready|in Lauf|blockiert)' } | ForEach-Object { $_.Remove('note'); $_.status = $_.status.Substring(0, [Math]::Min(140, $_.status.Length)); $_ })
$anyReady = @($queue | Where-Object { $_.status -match '^ready' }).Count -gt 0
$anyInRun = @($queue | Where-Object { $_.status -match '^in Lauf' }).Count -gt 0

Repair-StaleAgents $cfg
if (-not $DryRun) { [void](Move-AgentsToArchive $cfg 12) }   # rows that ended more than 12 h ago go to AGENTS-ARCHIVE.json
$agents = @(Read-Agents $cfg)
$active = @($agents | Where-Object { $_.status -eq 'running' -or $_.status -eq 'starting' })
$freeP = Get-ProviderFree $cfg $active   # free slots per provider name (plan section 15)
$freeTotal = [int](($freeP.Values | Measure-Object -Sum).Sum)
$slotTotal = [int]((Get-Providers $cfg | Measure-Object Slots -Sum).Sum)
$usedInt = @($active | Where-Object { $_.phase -eq 'integrate' }).Count
$freeRam = Get-FreeRamGB
$build = Test-FullBuildRunning
$qs = @(Get-Questions $cfg)
$blocked = @(Get-BlockedTasks $agents)
$snapAgents = @($agents | ForEach-Object { [ordered]@{ name = $_.name; type = $_.type; plan = $_.plan; package = $_.package; phase = $_.phase; status = $_.status; finalFile = ((Get-RuntimeRel $cfg) + '/runs/' + $_.name + '/final.txt') } })
$snapshot = [ordered]@{
    now = (Get-Date -Format 'yyyy-MM-dd HH:mm'); repo = $repo; dryRun = [bool]$DryRun
    plans = $startablePlans
    notStartablePlans = $otherPlans
    integration = [ordered]@{ rows = $queue; canStart = (-not $pilotMode -and -not $PlanFiles -and $anyReady -and -not $anyInRun -and $usedInt -lt $cfg.integratorSlots -and -not $build) }
    blockedTasks = @($blocked | ForEach-Object { [ordered]@{ plan = $_.Plan; package = $_.Package; phase = $_.Phase; error = $_.Error } })
    agents = $snapAgents
    slots = [ordered]@{ freeByProvider = $freeP; integratorFree = [Math]::Max(0, $cfg.integratorSlots - $usedInt); workersMax = $slotTotal }
    freeRamGB = $freeRam; minFreeRamGB = $cfg.minFreeRamGB; fullBuildRunning = $build
    questions = [ordered]@{ open = @($qs | Where-Object { $_.status -eq 'OPEN' }); answered = @($qs | Where-Object { $_.status -eq 'ANSWERED' }) }
}
if ($pilotMode) { $snapshot.Remove('integration'); $snapshot['pilotMode'] = 'true: only the packages of the pilot plan may be started; there is no integrator in this mode' }
$snapJson = $snapshot | ConvertTo-Json -Depth 8

# Calls the project's onEvent command once with the events collected since the last call (see events.ps1); never in a dry run.
function Invoke-RoundEvents { if (-not $DryRun) { Invoke-EventHook $cfg @($files | ForEach-Object { $_.FullName }) $allRows } }
if (-not $DryRun -and (Test-Path $stopFlag)) { Write-Host 'STOP flag set: no new agents.' -ForegroundColor Yellow; Write-RoundLog $cfg 'round skipped (STOP)'; Invoke-RoundEvents; return }
$minRam = if ($pilotMode) { $cfg.pilotMinFreeRamGB } else { $cfg.minFreeRamGB }
$buildNames = @(Get-BuildProcessNames)
$lowRam = ($freeRam -lt $minRam)
$buildBlock = ($pilotMode -and $buildNames.Count -gt 0)
if (-not $DryRun -and ($lowRam -or $buildBlock)) {
    # Name the real reason(s): RAM below the minimum and/or the running build processes (same detection as the dashboard).
    $reasons = @()
    if ($lowRam) { $reasons += ('free RAM {0} GB is below the minimum {1} GB' -f $freeRam, $minRam) }
    if ($buildBlock) { $reasons += ('build process running: ' + ($buildNames -join ', ')) }
    $msg = 'waiting for the next round: ' + ($reasons -join '; ')
    Write-Host $msg -ForegroundColor Yellow
    Write-RoundLog $cfg ('round skipped (' + ($reasons -join '; ') + ')'); Invoke-RoundEvents; return
}
# ---- routine (no language model, plan section 11.1) -----------------------------------------
$routineFree = @{ workers = $freeTotal; int = $snapshot.slots.integratorFree }   # the routine gets its own copy of the free slots per provider (it counts them down while it plans)
foreach ($fk in @($freeP.Keys)) { $routineFree[[string]$fk] = $freeP[$fk] }
$routineCtx = @{
    Cfg = $cfg; Repo = $repo; Proj = $(if ($cfg.projectsRoot) { $cfg.projectsRoot } else { $null }); Plans = $startablePlans; Queue = $queue; AllRows = $allRows; Questions = $qs
    CanStartIntegrator = ($snapshot.Contains('integration') -and $snapshot.integration.canStart)
    Free = $routineFree
}
$routine = Get-Routine $routineCtx
if (-not $DryRun) {
    foreach ($fl in @(Set-HandoffFlags $cfg $agents)) { Write-OrchEvent $cfg ("{0}: Kontext {1} erreicht, HANDOFF angefordert" -f $fl.Name, (Format-Tokens $fl.Ctx)); Write-RoundLog $cfg ('handoff flag written for ' + $fl.Name + ' (context ' + $fl.Ctx + ')') }
}
# ---- brain: only when an event needs judgement (plan section 11.2) --------------------------
$answer = $null; $envl = $null
if ($routine.Events.Count -gt 0) {
    $brainTrigger = (@($routine.Events | ForEach-Object { $_.type } | Select-Object -Unique) -join ',')
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    if ($BrainAnswerFile) {
        $answer = Get-Content $BrainAnswerFile -Raw -Encoding UTF8 | ConvertFrom-Json   # test hook: no claude call, no tokens
    }
    else {
        $delta = [ordered]@{ now = (Get-Date -Format 'yyyy-MM-dd HH:mm'); slots = $snapshot.slots; freeRamGB = $freeRam; events = @($routine.Events | ForEach-Object { [ordered]@{ type = $_.type; plan = $_.plan; package = $_.package; agent = $_.agent; text = $_.text } }) }
        $projRules = $(if ($cfg.ruleSources.project) { "`n`n" + (Get-Content (Get-RulesFilePath $cfg) -Raw -Encoding UTF8) } else { '' })   # the project rules file is part of the brain instructions
        $brainPrompt = (Get-Content (Join-Path $script:ToolsDir 'ORCHESTRATOR.md') -Raw -Encoding UTF8) + $projRules + "`n`n# EVENTS (JSON)`n`n" + ($delta | ConvertTo-Json -Depth 6) + "`n`nAnswer now with the JSON object only."
        $claudeExe = (Get-Command claude.exe -ErrorAction Stop).Source
        $bargs = @('-p') + @($cfg.brainArgs | ForEach-Object { $_.Replace('{brainModel}', $cfg.brainModel).Replace('{brainEffort}', $cfg.brainEffort).Replace('{repo}', $repo) })
        # Rule 12: without a new plan the events already carry all data, so the brain gets no tools and answers in exactly one turn (no re-reading of the context per tool call).
        $ti = [array]::IndexOf($bargs, '--tools')
        if ($ti -ge 0 -and @($routine.Events | Where-Object { $_.type -eq 'new_plan' }).Count -eq 0) { $bargs[$ti + 1] = '' }
        Write-Host 'asking the brain ...' -ForegroundColor DarkGray
        Write-OrchState $cfg @{ status = 'brain'; since = (Get-Date).ToString('o'); nextAt = $null }
        $res = Invoke-Native $claudeExe $bargs $brainPrompt $repo 1500
        $lastUse = $null
        if (-not ($res.TimedOut -or $res.ExitCode -ne 0)) {
            foreach ($ln in ($res.StdOut -split "`n")) {
                if ([string]::IsNullOrWhiteSpace($ln)) { continue }
                try { $ev = $ln | ConvertFrom-Json } catch { continue }
                if ($ev.type -eq 'rate_limit_event') { Save-ClaudeUsage $cfg $ev }
                elseif ($ev.type -eq 'assistant' -and $ev.message.usage) { $lastUse = $ev.message.usage }
                elseif ($ev.type -eq 'result') { $envl = $ev }
            }
        }
        # Token numbers come only from the stream-json result / assistant usage; missing fields stay $null (never invented).
        if ($envl -and $envl.usage) { $u = $envl.usage; $brainIn = [int64]($u.input_tokens + $u.cache_creation_input_tokens + $u.cache_read_input_tokens); $brainOut = [int64]$u.output_tokens }
        if ($lastUse) { $brainCtx = [int64]($lastUse.input_tokens + $lastUse.cache_creation_input_tokens + $lastUse.cache_read_input_tokens) } elseif ($null -ne $brainIn) { $brainCtx = $brainIn }
        if ($envl -and $envl.modelUsage) { $mn = @($envl.modelUsage.PSObject.Properties.Name | Select-Object -First 1); if ($mn.Count) { $brainModel = Get-ModelLabel $mn[0] } }
        $failMsg = $null
        if ($res.TimedOut -or $res.ExitCode -ne 0) { $failMsg = "brain failed (exit $($res.ExitCode), timeout $($res.TimedOut)): " + $res.StdErr.Substring(0, [Math]::Min(300, $res.StdErr.Length)) }
        elseif (-not $envl) { $failMsg = 'brain run had no result event' }
        else {
            $text = [string]$envl.result
            $a0 = $text.IndexOf('{'); $a1 = $text.LastIndexOf('}')
            if ($a0 -lt 0 -or $a1 -le $a0) { $failMsg = 'brain answer has no JSON' }
            else { try { $answer = $text.Substring($a0, $a1 - $a0 + 1) | ConvertFrom-Json } catch { $failMsg = 'brain JSON invalid: ' + $_.Exception.Message } }
            if (-not $failMsg -and $null -eq $answer.actions) { $failMsg = 'brain answer has no "actions" list' }
        }
        if ($failMsg) {
            $brainSec = [int]$sw.Elapsed.TotalSeconds
            Write-Host $failMsg -ForegroundColor Red; Write-RoundLog $cfg $failMsg
            Write-OrchEvent $cfg ('Gehirn (' + $brainTrigger + '): Fehler - ' + $failMsg.Substring(0, [Math]::Min(120, $failMsg.Length))) $true
            Save-Brain ('Fehler: ' + $failMsg) $true
            foreach ($ev in $routine.Events) { $n = 1; $t = Get-Handled $cfg; if ($t.ContainsKey('try:' + $ev.key)) { $n = [int]$t['try:' + $ev.key] + 1 }; Set-Handled $cfg ('try:' + $ev.key) ([string]$n); if ($n -ge 2) { Set-Handled $cfg $ev.key } }
            $answer = $null; $brainFailed = $true
        }
    }
    $brainSec = [int]$sw.Elapsed.TotalSeconds
    if ($answer) { foreach ($ev in $routine.Events) { Set-Handled $cfg $ev.key } }
}
$acts = @($routine.Actions) + $(if ($answer) { @($answer.actions) } else { @() })
# ---- validate + enforce + execute -----------------------------------------------------------
$report = @()
$freeI = $snapshot.slots.integratorFree
$workersFree = $freeTotal
$names = @($agents | ForEach-Object { [string]$_.name })
$busyPkgs = @($active | ForEach-Object { "$($_.plan)|$($_.package)" })
$started = 0
foreach ($a in $acts) {
    switch ([string]$a.action) {
        'note' { $report += "NOTE: $($a.text)"; if (-not $DryRun) { Write-RoundLog $cfg ('NOTE ' + $a.text) } }
        'ask_owner' {
            if (-not $a.question -or -not $a.plan) { $report += 'REJECT ask_owner: missing plan/question'; continue }
            $dup = @($qs | Where-Object { $_.plan -eq $a.plan -and $_.item -eq $a.item -and ($_.status -eq 'OPEN' -or $_.status -eq 'ANSWERED') }).Count -gt 0
            if ($dup) { $report += "SKIP ask_owner (already known): $($a.plan) / $($a.item)"; continue }
            $src = Test-ItemDecided (Get-DecidedItems $cfg $qs) ([string]$a.plan) ([string]$a.item)
            if ($src) { $report += "SKIP ask_owner (already decided in $src): $($a.plan) / $($a.item)"; if (-not $DryRun) { Write-RoundLog $cfg ("skip ask_owner $($a.plan) / $($a.item): already decided in $src") }; continue }
            $report += "ASK_OWNER: $($a.plan) / $($a.item): $($a.question)"
            if (-not $DryRun) {
                $qp = Get-QuestionsPath $cfg
                if (-not (Test-Path $qp)) { Set-Content $qp -Value "# Open questions for $($cfg.owner)`n" -Encoding UTF8 }
                $n = @($qs | Where-Object { $_.id -like ('Q-' + (Get-Date -Format 'yyyyMMdd') + '-*') }).Count + 1
                $id = 'Q-{0}-{1:000}' -f (Get-Date -Format 'yyyyMMdd'), $n
                $blk = "`n## $id`n- Plan: $($a.plan)`n- Item: $($a.item)`n- Question: $($a.question)`n- Recommendation: $($a.recommendation)`n- Status: OPEN`n" + $(if ($pilotMode) { "- Pilot: JA (Testeintrag, bei Pilotende entfernen)`n" } else { "" })
                Add-Content -Path $qp -Value $blk -Encoding UTF8
                $qs += [ordered]@{ id = $id; plan = [string]$a.plan; item = [string]$a.item; status = 'OPEN' }
                Write-RoundLog $cfg "ask_owner $id $($a.plan) / $($a.item)"; Write-OrchEvent $cfg "Frage an $($cfg.owner): $id ($($a.item))"
            }
        }
        'start_agent' {
            $why = $null; $pick = $null; $type = $null; $provName = $null
            if (-not $a.name -or -not $a.prompt -or -not $a.cwd -or -not $a.type -or -not $a.phase) { $why = 'missing field' }
            elseif (-not (Get-Provider $cfg ([string]$a.type))) { $why = "unknown provider $($a.type)" }
            elseif ($a.phase -notin @('work', 'verify', 'release', 'integrate')) { $why = "bad phase $($a.phase)" }
            elseif (-not (Test-Path $a.cwd -PathType Container)) { $why = "cwd missing: $($a.cwd)" }
            elseif ((Test-Path $stopFlag) -and -not $DryRun) { $why = 'STOP flag' }
            elseif (@($blocked | Where-Object { $_.Key -eq "$($a.plan)|$($a.package)|$($a.phase)" }).Count -gt 0) { $bk = $blocked | Where-Object { $_.Key -eq "$($a.plan)|$($a.package)|$($a.phase)" } | Select-Object -First 1; $why = 'blockiert: ' + $bk.Error; if (-not $DryRun) { Write-RoundLog $cfg ('TECHNICAL NOTE: task ' + $a.name + ' failed 2 times with the same error and is not started again: ' + $bk.Error) } }
            elseif ($busyPkgs -contains "$($a.plan)|$($a.package)" -or @(@($a.bundle) | Where-Object { $busyPkgs -contains "$($a.plan)|$_" }).Count -gt 0) { $why = 'an agent already works on this package' }
            elseif ($a.phase -eq 'work' -and (Test-PackageLocked $cfg ([string]$a.plan) ([string]$a.package))) { $why = 'package is locked: verifications reported open items too often (open question)' }
            elseif (Test-Frozen $cfg ([string]$a.plan) ([string]$a.package)) { $why = 'package is frozen (NOFOLLOW.json): no follow-up task' }
            elseif ($a.phase -ne 'integrate' -and -not ($a.phase -eq 'work' -and [string]$a.package -like 'FIX-*' -and (Norm-Plan ([string]$a.plan)) -eq (Norm-Plan (Get-QueueRel $cfg))) -and -not ($startablePlans | Where-Object { $_.path -eq ([string]$a.plan).Replace('\','/') -or $_.path -eq [string]$a.plan })) { $why = 'rule 11: plan is not READY or IN_PROGRESS' }
            if (-not $why -and $pilotMode -and $a.phase -eq 'integrate') { $why = 'pilot mode: no integrator' }
            if (-not $why -and $pilotMode -and (([string]$a.plan).Replace('/','\') -notlike ('*' + $pilotPlan))) { $why = 'pilot mode: only packages of PILOT-PLAN.md' }
            if (-not $why -and $a.phase -eq 'integrate' -and $PlanFiles) { $why = 'pilot/test mode (-PlanFiles): no integrator' }
            if (-not $why -and $a.phase -eq 'integrate') {
                if (-not $snapshot.integration.canStart) { $why = 'integrator.canStart is false' }
                elseif ($freeI -le 0) { $why = 'no integrator slot' }
                else {
                    $pick = @(Get-Providers $cfg | Where-Object { $_.Protocol -eq 'claude' -and $_.Slots -gt 0 -and $_.Classes.medium.model } | Select-Object -First 1)   # the integrator is a Claude Code agent and uses no worker slot
                    if ($pick.Count) { $pick = $pick[0] } else { $pick = $null; $why = 'no free slot: no Claude Code provider is on (slots 0)' }
                }
            }
            elseif (-not $why) {
                if ($workersFree -le 0) { $why = 'no worker slot' }
                else {
                    # The package table decides what a package needs (plan section 15), not the "graphics" flag the brain happened to send; the flag still counts as "images".
                    $need = @(); if ($a.phase -eq 'work') { $need = @(Get-PackageNeedsFor $cfg (Abs ([string]$a.plan)) ([string]$a.package)) }
                    if ($a.graphics -eq $true -and $need -notcontains 'images') { $need += 'images' }
                    if ($need.Count -and -not (Test-CapabilityAvailable $cfg $need)) { $why = "no provider with the capability $($need -join ', ') has slots above 0" }
                    else {
                        $cls0 = Get-StartClass $cfg ([string]$a.phase) ([string]$a.plan) ([string]$a.package) ([string]$a.class)
                        $pick = Select-Provider $cfg $cls0 $need $freeP -Prefer ([string]$a.type)   # a busy slot never waits: the next fitting provider with a free slot takes the task at once
                        if (-not $pick) { $why = 'no free slot' + $(if ($need.Count) { " for the capability $($need -join ', ')" } else { " in a fitting provider (class $cls0)" }) }
                    }
                }
            }
            if (-not $why -and $pick) { $type = $pick.Protocol; $provName = $pick.Name }
            # Live check before EACH start (not from the round snapshot); in pilot mode minFreeRamGB (2 GB) and no cl/link/msbuild/cmake process.
            if (-not $why -and -not $DryRun -and (Get-FreeRamGB) -lt $minRam) { $why = ('free RAM ' + (Get-FreeRamGB) + ' GB is below ' + $minRam + ' GB') }
            if (-not $why -and -not $DryRun -and $pilotMode -and (Test-AnyBuildProcess)) { $why = ('a build process runs (pilot waits): ' + ((Get-BuildProcessNames) -join ', ')) }
            if ($why) {
                $report += "REJECT start_agent $($a.name): $why"; if (-not $DryRun) { Write-RoundLog $cfg "reject $($a.name): $why" }
                # A start that failed only for a temporary reason (slot, RAM, STOP) must not use up the brain event: the event is asked again next round.
                if ($why -match '^(no worker slot|no Codex slot|no free slot|no integrator slot|free RAM|STOP flag)' -and -not $DryRun) { foreach ($ev in $routine.Events) { if ((Norm-Plan $ev.plan) -eq (Norm-Plan ([string]$a.plan))) { Remove-Handled $cfg $ev.key } } }
                continue
            }
            $nm = [string]$a.name; $k = 1
            while ($names -contains $nm) { $k++; $nm = "$($a.name)-$k" }
            $names += $nm
            if ($a.phase -eq 'integrate') { $freeI-- } else { $workersFree--; $freeP[$provName]-- }
            $busyPkgs += "$($a.plan)|$($a.package)"
            foreach ($bp in @($a.bundle)) { $busyPkgs += "$($a.plan)|$bp" }
            $report += "START $nm [$provName/$($a.phase)] plan=$($a.plan) package=$($a.package) cwd=$($a.cwd)"
            if ($DryRun) { continue }
            $runDir = Join-Path $cfg.runtime "runs\$nm"
            New-Item -ItemType Directory -Force $runDir | Out-Null
            $hdr = "You are the agent $nm (one task, then you end). Follow all rules in section 1 of $($script:ToolsDir)\ORCHESTRATOR.md and every rule of the repository (CLAUDE.md and the files it names). Update the plan's state file. End when your task is finished.`n`nTASK`n`n"
            if ($type -eq 'claude') { $hdr = "You are the agent $nm (one task, then you end). The rules for your phase (section 1 of ORCHESTRATOR.md and the CLAUDE.md sections you need) are already in your system prompt: do not read ORCHESTRATOR.md or CLAUDE.md again; read other files they name (e.g. $($cfg.buildFlowDoc)) only when your task needs them. Update the plan's state file. End when your task is finished.`n`nTASK`n`n" }   # S9: the rule files are not read a second time
            $pf = Join-Path $runDir 'prompt.md'
            $hf = Join-Path $runDir 'HANDOFF'
            $handoffText = "Prüfe nach jedem abgeschlossenen Arbeitsschritt, ob die Datei $hf existiert. Wenn ja: schreib den Stand (erledigt, offen, nächster Schritt, Branch, Worktree) in die State-Datei des Plans unter 'Handoff $nm' und beende dich."
            [System.IO.File]::WriteAllText($pf, $hdr + [string]$a.prompt + "`n`n" + $handoffText, (New-Object System.Text.UTF8Encoding($true)))
            $idn = Get-AgentIdentity $cfg   # short first name and own color (unique among running and last-hour agents), stored in AGENTS.json
            $cls = Get-StartClass $cfg ([string]$a.phase) ([string]$a.plan) ([string]$a.package) ([string]$a.class)   # plan 12.4: verify and integrator never run below medium, for routine and brain starts alike
            $cm = Get-ClassModel $cfg $provName $cls
            Update-Agent $cfg $nm @{ type = $type; provider = $provName; plan = [string]$a.plan; package = [string]$a.package; phase = [string]$a.phase; cwd = [string]$a.cwd; status = 'starting'; startedAt = (Get-Date).ToString('o'); tokensIn = $null; tokensOut = $null; lastLine = 'queued'; person = $idn.Person; color = $idn.Color; modelShort = (Get-ModelShort $type $cm.Model $cm.Effort); class = $cls; bundle = @($a.bundle); task = (Get-AgentTask $cfg ([string]$a.plan) ([string]$a.package) ([string]$a.phase) $pf) }
            if ($env:ORCH_TEST_NOSTART) { Update-Agent $cfg $nm @{ status = 'running'; pid = 4 } }   # tests only: no pane, the fake agent is marked running (pid 4 = System, always alive)
            else { Start-AgentPane $cfg @{ name = $nm; type = $type; plan = [string]$a.plan; package = [string]$a.package; phase = [string]$a.phase; cwd = [string]$a.cwd; promptFile = $pf } }
            Write-RoundLog $cfg "start $nm ($provName, $($a.phase))"
            Write-OrchEvent $cfg ("$nm gestartet (" + $(if ($provName -eq 'codex') { 'Codex' } elseif ($provName -eq 'claude') { 'Claude' } else { $provName }) + ')')
            if ($a.phase -ne 'integrate') {
                $pp = Abs ([string]$a.plan)
                if ((Test-Path $pp) -and (Get-PlanStatus $pp) -eq 'READY') { if (Set-PlanStatus $pp 'IN_PROGRESS') { Write-RoundLog $cfg "plan $($a.plan): READY -> IN_PROGRESS" } }
            }
            $started++
            if (-not $env:ORCH_TEST_NOSTART) { Start-Sleep -Seconds 3 }
        }
        'lock_package' {
            # Plan 12.4: report the open items of a locked package once under the open-question marker in the state file of the plan (the next round turns it into a question).
            $pp = Abs ([string]$a.plan)
            $sf = @(Get-PlanStateFiles $pp | Select-Object -First 1)
            $tag = 'LOCK-' + [string]$a.package
            if ($sf.Count -and -not $DryRun -and -not (Select-String -Path $sf[0].FullName -Pattern ($cfg.openMarker + ' - ' + $tag) -SimpleMatch -Quiet)) {
                $q = Get-PromptText $cfg 'lockQuestion' @{ marker = [string]$cfg.openMarker; tag = $tag; pkg = [string]$a.package; agent = [string]$a.agent; text = [string]$a.text }
                Add-Content -Path $sf[0].FullName -Value ("`n### " + $q + "`n") -Encoding UTF8
                Write-RoundLog $cfg "package $($a.package) of $($a.plan) locked, open question written"
                Write-OrchEvent $cfg ("Paket " + $a.package + " gesperrt (" + $cfg.openMarker + ")") $true
            }
            $report += "LOCK $($a.plan) $($a.package)"
        }
        'plan_done' {
            # Rule 11: IN_PROGRESS -> DONE only after every branch of the plan is integrated and the whole-plan verify answered exactly "Offene Anforderungen: 0".
            $pp = Abs ([string]$a.plan)
            $why = $null
            if (-not (Test-Path $pp)) { $why = 'plan file missing' }
            elseif ((Get-PlanStatus $pp) -ne 'IN_PROGRESS') { $why = 'plan is not IN_PROGRESS' }
            else {
                $open = @($queue | Where-Object { $_.plan -like ('*' + ([string]$a.plan).Replace('\', '/')) })
                $ver = @($agents | Where-Object { $_.plan -eq $a.plan -and $_.package -eq 'ALL' -and $_.phase -eq 'verify' -and $_.status -eq 'done' } | Select-Object -Last 1)
                if ($open.Count -gt 0) { $why = 'branches of this plan are still ready / in Lauf / blockiert in the integration queue' }
                elseif ($ver.Count -eq 0) { $why = 'no finished whole-plan verify agent (package ALL, phase verify)' }
                else {
                    $ff = Join-Path $cfg.runtime ('runs\' + $ver[0].name + '\final.txt')
                    $txt = if (Test-Path $ff) { Get-Content $ff -Raw -Encoding UTF8 } else { '' }
                    if ($txt -notmatch 'Offene Anforderungen:\s*0\s*$') { $why = 'whole-plan verify did not end with "Offene Anforderungen: 0"' }
                }
            }
            if ($why) { $report += "REJECT plan_done $($a.plan): $why"; continue }
            $report += "PLAN_DONE $($a.plan): IN_PROGRESS -> DONE"
            if (-not $DryRun) { if (Set-PlanStatus $pp 'DONE') { Write-RoundLog $cfg "plan $($a.plan): IN_PROGRESS -> DONE"; Write-OrchEvent $cfg ("Plan " + (Split-Path ([string]$a.plan) -Leaf) + " fertig (DONE)") } }
        }        default { $report += "REJECT unknown action '$($a.action)'" }
    }
}
Write-Host ("brain actions: {0}" -f $acts.Count)
$report | ForEach-Object { Write-Host ('  ' + $_) }
if (-not $DryRun) { Write-RoundLog $cfg ("round done: {0} action(s), {1} agent(s) started" -f $acts.Count, $started) }
Invoke-RoundEvents
if ($routine.Events.Count -gt 0 -and $answer -and ($DryRun -eq $false -or $env:ORCH_RUNTIME_OVERRIDE)) {
    $sumParts = @()
    foreach ($x in @($answer.actions)) { switch ([string]$x.action) { 'start_agent' { if (@($report | Where-Object { $_ -like ('START ' + $x.name + '*') -or $_ -like ('START ' + $x.name + '-*') }).Count) { $sumParts += "$($x.name) gestartet" } else { $sumParts += "$($x.name) abgelehnt" } } 'ask_owner' { $sumParts += ('Frage an ' + $cfg.owner) } 'plan_done' { $sumParts += 'Plan fertig' } } }
    $note = @(@($answer.actions) | Where-Object { $_.action -eq 'note' } | Select-Object -First 1)
    $sum = if ($sumParts.Count) { $sumParts -join '; ' } elseif ($note.Count) { 'keine Aktion: ' + ((([string]$note[0].text) -replace '\s+', ' ').Trim() | ForEach-Object { if ($_.Length -gt 110) { $_.Substring(0, 110) } else { $_ } }) } else { 'keine Aktion' }
    Save-Brain $sum $false
    Write-OrchEvent $cfg ('Gehirn (' + $brainTrigger + '): ' + $sum)
}
if ($DryRun -and $OutFile) {
    New-Item -ItemType Directory -Force (Split-Path $OutFile) | Out-Null
    $out = [ordered]@{ time = (Get-Date -Format 'o'); brainCostUSD = $envl.total_cost_usd; snapshotSlots = $snapshot.slots; freeRamGB = $freeRam; integrationCanStart = $(if ($snapshot.Contains('integration')) { $snapshot.integration.canStart } else { $null }); brainAnswer = $answer; enforcement = $report }
    $out | ConvertTo-Json -Depth 10 | Set-Content $OutFile -Encoding UTF8
    $snapJson | Set-Content ([System.IO.Path]::ChangeExtension($OutFile, '.snapshot.json')) -Encoding UTF8
}
if (-not $DryRun -or $env:ORCH_RUNTIME_OVERRIDE) { Write-OrchState $cfg @{ status = 'idle'; since = (Get-Date).ToString('o'); nextAt = $null } }   # dashboard: idle since the end of this round
