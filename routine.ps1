# Routine part of an orchestrator round (plan section 11): everything that needs no language model is decided here by script.
# Dot-sourced by round.ps1. Returns deterministic actions (same format as the brain's) and the events that need the brain.

# Plan path in one comparable form (forward slashes, lower case).
function Norm-Plan([string]$p) { return ([string]$p).Replace('\', '/').ToLower() }

# Keys of events the brain already handled (ORCH-HANDLED.json): @{ key = 'yyyy-MM-dd HH:mm' }.
function Get-Handled($Cfg) {
    $p = Join-Path $Cfg.runtime 'ORCH-HANDLED.json'
    if (-not (Test-Path $p)) { return @{} }
    try { $h = (Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json -AsHashtable); if ($null -eq $h) { return @{} }; return $h } catch { return @{} }
}
# Remembers an event key as handled.
function Set-Handled($Cfg, [string]$Key) {
    $h = Get-Handled $Cfg
    $h[$Key] = (Get-Date -Format 'yyyy-MM-dd HH:mm')
    Write-FileAtomic (Join-Path $Cfg.runtime 'ORCH-HANDLED.json') (ConvertTo-Json -InputObject $h -Compress)
}

# Package table of a plan: rows (Id, Content, Depends = list of package ids). A table is recognized by a header cell "Package" or "Paket" in the first column.
function Get-PlanPackages([string]$PlanPath) {
    if (-not (Test-Path -LiteralPath $PlanPath)) { return @() }   # an agent row may name a file that is not a plan (the queue file of a fix without a plan) or is gone
    $res = @(); $hdr = $null; $depIdx = -1; $contIdx = -1; $clsIdx = -1
    $range = '([A-Za-z]+)(\d+)\s*[-–]\s*(?:[A-Za-z]+)?(\d+)'
    foreach ($l in (Get-Content $PlanPath -Encoding UTF8)) {
        if ($l -notmatch '^\s*\|') { $hdr = $null; continue }
        $c = @($l.Trim().Trim('|').Split('|') | ForEach-Object { $_.Trim() })
        if (-not $hdr) {
            if ($c[0] -match '^(Package|Paket)$' -or ($c[0] -match '^Item$' -and @($c | Where-Object { $_ -match '^(Depends on|Abh)' }).Count -gt 0)) { $hdr = $c; $depIdx = -1; $contIdx = -1; $clsIdx = -1; for ($i = 0; $i -lt $c.Count; $i++) { if ($c[$i] -match '^(Class|Klasse)$') { $clsIdx = $i }; if ($c[$i] -match '^(Depends on|Abh)') { $depIdx = $i }; if ($c[$i] -match '^(Content|Inhalt)') { $contIdx = $i } } }
            continue
        }
        if ($c[0] -match '^[-: ]+$' -or -not $c[0]) { continue }
        $dep = if ($depIdx -ge 0 -and $depIdx -lt $c.Count) { $c[$depIdx] } else { 'none' }
        $deps = @()
        foreach ($m in [regex]::Matches($dep, $range)) { $a = [int]$m.Groups[2].Value; $b = [int]$m.Groups[3].Value; for ($k = $a; $k -le $b; $k++) { $deps += ($m.Groups[1].Value + $k) } }
        $rest = [regex]::Replace($dep, $range, '')
        foreach ($m in [regex]::Matches($rest, '[A-Za-z]+\d+')) { $deps += $m.Value }
        $res += [pscustomobject]@{ Id = $c[0]; Class = $(if ($clsIdx -ge 0 -and $clsIdx -lt $c.Count -and $c[$clsIdx] -match '(?i)^(light|medium|heavy)$') { $c[$clsIdx].ToLower() } else { 'medium' }); Content = $(if ($contIdx -ge 0 -and $contIdx -lt $c.Count) { $c[$contIdx] } else { '' }); Depends = @($deps | Select-Object -Unique); DependsAll = ($dep.Trim() -match '^(?i)all(e)?$') }
    }
    # "all" in the dependency column: the package waits for every other package of the table.
    foreach ($r in $res) { if ($r.DependsAll) { $r.Depends = @($res | Where-Object { $_.Id -ne $r.Id } | ForEach-Object { $_.Id }) } }
    return $res
}

# True when the final answer of a verify agent ends with exactly "Offene Anforderungen: 0" and lists no partial item.
function Test-VerifyZero($Cfg, [string]$AgentName) {
    $ff = Join-Path $Cfg.runtime ('runs\' + $AgentName + '\final.txt')
    if (-not (Test-Path $ff)) { return $false }
    $t = Get-Content $ff -Raw -Encoding UTF8
    $m = [regex]::Matches($t, 'Offene Anforderungen[^:\r\n]*:\s*(\d+)')   # the LAST count line decides, also when the answer adds notes after it
    return ($m.Count -gt 0 -and [int]$m[$m.Count - 1].Groups[1].Value -eq 0 -and $t -notmatch '(?im)^\s*[-*|].*teilweise')
}

# Number of open requirements named at the end of a verify answer ("Offene Anforderungen: N"); -1 when the answer is missing or has no such line.
function Get-VerifyOpenCount($Cfg, [string]$AgentName) {
    $ff = Join-Path $Cfg.runtime ('runs\' + $AgentName + '\final.txt')
    if (-not (Test-Path $ff)) { return -1 }
    $m = [regex]::Matches((Get-Content $ff -Raw -Encoding UTF8), 'Offene Anforderungen[^:\r\n]*:\s*(\d+)')
    if ($m.Count -eq 0) { return -1 }
    return [int]$m[$m.Count - 1].Groups[1].Value
}

# Packages that the state files or the integration queue already show as finished (done, merged, verified, integrated, released). Returns @{ id = evidence }.
# Sources: "Alle Pakete G1-G7 sind fertig" lines and table rows whose first cell is the package id in the plan's *STATE*.md files; integration rows (any status except blockiert) whose note says "Paket ALL" or names the package.
function Get-StateDonePackages([string]$AbsPlan, [string[]]$PkgIds, $Rows) {
    $res = @{}
    $doneRe = '(?i)(fertig|erledigt|\bdone\b|gemergt|merged|verifiziert|verified|integriert|released|umgesetzt)'
    $negRe = '(?i)(\bnicht\b|noch offen|\bwartet|teilweise|partial|\bnot\b|\btodo\b|in arbeit|blockiert)'
    foreach ($sf in @(Get-PlanStateFiles $AbsPlan)) {
        foreach ($l in (Get-Content $sf.FullName -Encoding UTF8)) {
            $m = [regex]::Match($l, '(?i)alle pakete\s+([A-Za-z]+)(\d+)\s*[-\u2013]\s*(?:[A-Za-z]+)?(\d+)\s+sind\s+(fertig|erledigt)')
            if ($m.Success) { for ($k = [int]$m.Groups[2].Value; $k -le [int]$m.Groups[3].Value; $k++) { $id = $m.Groups[1].Value + $k; if ($PkgIds -contains $id) { $res[$id] = $sf.Name + ': ' + $m.Value } } }
            $t = [regex]::Match($l, '^\|\s*\**([A-Za-z]+\d+)\**\s*(?:\s[^|]*)?\|(.*)$')
            if ($t.Success -and ($PkgIds -contains $t.Groups[1].Value) -and $t.Groups[2].Value -match $doneRe -and $t.Groups[2].Value -notmatch $negRe) { $res[$t.Groups[1].Value] = $sf.Name + ': ' + $l.Substring(0, [Math]::Min(80, $l.Length)) }
        }
    }
    $relN = Norm-Plan $AbsPlan
    foreach ($r in @($Rows)) {
        if ([string]$r.status -match '^(blockiert|verworfen|ersetzt)') { continue }
        $pl = (Norm-Plan ([string]$r.plan)).TrimStart('.', '/')
        if (-not $pl -or -not $relN.EndsWith($pl)) { continue }
        $note = [string]$r.note
        if ($note -match '(?i)Pakete?\s+ALL\b') { foreach ($id in $PkgIds) { if (-not $res.ContainsKey($id)) { $res[$id] = 'queue ' + $r.branch + ': ' + $r.status } } }
        else { foreach ($id in $PkgIds) { if ($note -match ('(?i)Pakete?\s+(?:[A-Za-z]+\d+\s*[-,]\s*)*' + [regex]::Escape($id) + '\b') -and -not $res.ContainsKey($id)) { $res[$id] = 'queue ' + $r.branch + ': ' + $r.status } } }
    }
    return $res
}

# Capabilities the package of the plan needs (plan section 15; graphics packages need "images", see Get-PackageNeeds): such work goes only to providers that have them.
function Get-PackageNeedsFor($Cfg, [string]$AbsPlan, [string]$Pkg) {
    if (-not (Test-Path $AbsPlan)) { return @() }
    $k = @(Get-PlanPackages $AbsPlan | Where-Object { $_.Id -eq $Pkg } | Select-Object -First 1)
    if (-not $k.Count) { return @() }
    return @(Get-PackageNeeds $Cfg ([string]$k[0].Content))
}
# Short id of a question item ("ALL-V5 Stimmungsverfall im Bad" -> "ALL-V5", "M1" -> "M1").
function Get-ItemToken([string]$Item) { $m = [regex]::Match(([string]$Item).Trim(), '^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*'); return $m.Value.ToUpper() }
# Ids (M1, ALL-V5 ...) the owner has already decided: found in the headings of the answers file ("*|ID"); plus "plan|ID" of every known question in the questions file (any status).
function Get-DecidedItems($Cfg, $Questions) {
    $res = @{}
    $dp = Get-AnswersPath $Cfg
    if (Test-Path $dp) { foreach ($l in (Get-Content $dp -Encoding UTF8)) { if ($l -match '^##\s') { foreach ($m in [regex]::Matches($l, '(?<![A-Za-z0-9-])[A-Za-z]{1,4}(?:-[A-Za-z]+)?-?\d+[A-Za-z]?(?![A-Za-z0-9])')) { $res[('*|' + $m.Value.ToUpper())] = (Split-Path ([string]$Cfg.answersFile) -Leaf) } } } }
    foreach ($q in @($Questions)) { $res[((Norm-Plan $q.plan) + '|' + (Get-ItemToken $q.item))] = (Split-Path ([string]$Cfg.questionsFile) -Leaf) + ' ' + $q.id }
    return $res
}
# Returns the source (answers file name / questions file name and question id) when the item of this plan is already decided or asked, else $null.
function Test-ItemDecided($Decided, [string]$Plan, [string]$Item) {
    $tok = Get-ItemToken $Item
    if (-not $tok) { return $null }
    $k1 = (Norm-Plan $Plan) + '|' + $tok
    if ($Decided.ContainsKey($k1)) { return $Decided[$k1] }
    if ($Decided.ContainsKey('*|' + $tok)) { return $Decided['*|' + $tok] }
    return $null
}
# Text of the answers-file section whose heading names the item id (at most 1200 characters), so the brain needs no file reading.
function Get-AnswerText($Cfg, [string]$Token) {
    $dp = Get-AnswersPath $Cfg

    if (-not $Token -or -not (Test-Path $dp)) { return '' }
    $out = @(); $in = $false
    foreach ($l in (Get-Content $dp -Encoding UTF8)) {
        if ($l -match '^##\s') { if ($in) { break }; $in = ($l.ToUpper() -match ('(?<![A-Z0-9-])' + [regex]::Escape($Token) + '(?![A-Z0-9])')); if ($in) { $out += $l }; continue }
        if ($in -and $l.Trim()) { $out += $l.Trim() }
    }
    $t = $out -join ' '
    if ($t.Length -gt 1200) { $t = $t.Substring(0, 1200) }
    return $t
}

# Remembers that the lock of a package was lifted now (read by Get-PackageFailures, which then ignores the earlier failures).
function Set-LockRelease($Cfg, [string]$Plan, [string]$Pkg) {
    $h = Get-Handled $Cfg
    $h['lockrel:' + (Norm-Plan $Plan) + '|' + $Pkg] = (Get-Date).ToString('o')
    Write-FileAtomic (Join-Path $Cfg.runtime 'ORCH-HANDLED.json') (ConvertTo-Json -InputObject $h -Compress)
}
# Answer to the lock question of a package, or $null while it is unanswered. Counts only when the state file holds the lock section "<openMarker> - LOCK-<pkg>" with an optional title after the tag (so only a real lock can be lifted) and one of these holds:
# (a) a heading of the answers file names LOCK-<pkg> ($Decided key '*|LOCK-<PKG>'), (b) a later heading of the state file names a decision/answer and LOCK-<pkg>,
# (c) a question of this plan with item LOCK-<pkg> (or item <pkg> whose question text says the package is gesperrt/locked) has status ANSWERED. A question that is only OPEN does not count. $StateLines = line arrays of the state files of the plan.
# Returns @{ Done; Source }: Done is true when the answer text (heading and body) says erledigt / done / closed / keine weiteren.
function Get-LockDecision($Cfg, [string]$Rel, [string]$Pkg, $Decided, $Questions, $StateLines) {
    $tag = 'LOCK-' + $Pkg
    $tagRe = '(?<![A-Za-z0-9-])' + [regex]::Escape($tag) + '(?![A-Za-z0-9])'
    $secRe = '(?i)^#+\s*' + [regex]::Escape([string]$Cfg.openMarker) + '\s*-\s*' + [regex]::Escape($tag) + '(?![A-Za-z0-9-])'
    $hasLock = $false; $src = @(); $text = @()
    foreach ($sl in @($StateLines)) {
        $lines = @($sl); $at = -1
        for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match $secRe) { $at = $i; break } }
        if ($at -lt 0) { continue }
        $hasLock = $true
        for ($i = $at + 1; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match '^#' -and $lines[$i] -match '(?i)(Entscheidung|decision|Antwort|answer|erledigt|umgesetzt|implemented)' -and $lines[$i] -match $tagRe) {
                $src = @('state file'); $text = @($lines[$i])
                for ($j = $i + 1; $j -lt $lines.Count -and $lines[$j] -notmatch '^#'; $j++) { if ($lines[$j].Trim()) { $text += $lines[$j].Trim() } }
            }
        }
    }
    if (-not $hasLock) { return $null }
    if ($Decided.ContainsKey('*|' + $tag.ToUpper())) { $src += 'answers file'; $text += (Get-AnswerText $Cfg $tag.ToUpper()) }
    foreach ($q in @($Questions | Where-Object { $_.status -eq 'ANSWERED' -and (Norm-Plan $_.plan) -eq (Norm-Plan $Rel) -and ((Get-ItemToken ([string]$_.item)) -eq $tag.ToUpper() -or ((Get-ItemToken ([string]$_.item)) -eq $Pkg.ToUpper() -and [string]$_.question -match '(?i)(gesperrt|\blocked\b)')) })) { $src += ('question ' + $q.id); $text += [string]$q.answer; $text += (Get-AnswerText $Cfg $tag.ToUpper()) }
    if (-not $src.Count) { return $null }
    $t = $text -join ' '
    return [pscustomobject]@{ Done = ($t -match '(?i)(erledigt|\bdone\b|\bclosed\b|keine weiteren)' -and $t -notmatch '(?i)(nicht erledigt|not done|not closed|nicht abgeschlossen)'); Source = ($src -join ', ') }
}

# Stage of one package from the agent history: new, running, needVerify, verified, open (verify found items), failed.
function Get-PkgStage($Cfg, $Agents, [string]$Plan, [string]$Pkg) {
    # The whole-plan stage (ALL) also counts the follow-up work of the plan (Q-* = answered question, FIX-* = integration fix): it makes an earlier ALL verify outdated.
    $mine = @($Agents | Where-Object { (Norm-Plan $_.plan) -eq (Norm-Plan $Plan) -and ([string]$_.package -eq $Pkg -or @($_.bundle) -contains $Pkg -or ($Pkg -eq 'ALL' -and [string]$_.package -match '^(Q|FIX)-')) })
    if (@($mine | Where-Object { $_.status -eq 'running' -or $_.status -eq 'starting' }).Count -gt 0) { return [pscustomobject]@{ Stage = 'running'; Work = $null; Verify = $null } }
    $work = @($mine | Where-Object { $_.phase -eq 'work' } | Sort-Object { [string]$_.startedAt } | Select-Object -Last 1)
    $ver = @($mine | Where-Object { $_.phase -eq 'verify' } | Sort-Object { [string]$_.startedAt } | Select-Object -Last 1)
    $w = if ($work.Count) { $work[0] } else { $null }; $v = if ($ver.Count) { $ver[0] } else { $null }
    $latest = @($mine | Sort-Object { [string]$_.startedAt } | Select-Object -Last 1)
    if ($latest.Count -and $latest[0].status -eq 'done' -and (Test-Path (Join-Path $Cfg.runtime ('runs\' + $latest[0].name + '\HANDOFF')))) { return [pscustomobject]@{ Stage = 'handoff'; Work = $w; Verify = $v } }   # ended with a handoff: its -cN continuation is started by the routine
    if (-not $w -and -not $v) { return [pscustomobject]@{ Stage = 'new'; Work = $null; Verify = $null } }
    if ($w -and $w.status -eq 'failed' -and (-not $v -or [string]$v.startedAt -lt [string]$w.startedAt)) { return [pscustomobject]@{ Stage = 'failed'; Work = $w; Verify = $v } }
    if ($w -and (-not $v -or [string]$v.startedAt -lt [string]$w.startedAt)) { return [pscustomobject]@{ Stage = 'needVerify'; Work = $w; Verify = $v } }
    if ($v -and $v.status -eq 'failed') { return [pscustomobject]@{ Stage = 'failed'; Work = $w; Verify = $v } }
    if ($v -and $v.status -eq 'done') { return [pscustomobject]@{ Stage = $(if (Test-VerifyZero $Cfg ([string]$v.name)) { 'verified' } else { 'open' }); Work = $w; Verify = $v } }
    return [pscustomobject]@{ Stage = 'new'; Work = $w; Verify = $v }
}

# Plan short name for agent names: the project folder in upper case (alpha -> ALPHA); a stand-alone plan file (planStandaloneRegex) uses its own file stem.
function Get-PlanTag([string]$Plan) { $re = [string](Get-ProjCfg).planStandaloneRegex; if ($re -and [IO.Path]::GetFileName(([string]$Plan).Replace('/', [string][char]92)) -match $re) { return $Matches[1] }; return (Split-Path (Split-Path ([string]$Plan).Replace('/', '\') -Parent) -Leaf).ToUpper() }
# Unique agent name (adds -2, -3 ... when the name was used before, also in the archive).
function New-AgentName([string]$Base, $Agents, $Chosen) {
    $n = $Base; $k = 1
    while (@($Agents | Where-Object { $_.name -eq $n }).Count -gt 0 -or $Chosen -contains $n) { $k++; $n = "$Base-$k" }
    return $n
}
# The verify question of plan 6.2 for one package (or the whole plan with package ALL).
function Get-VerifyPrompt([string]$Plan, [string]$Pkg) {
    $what = if ($Pkg -eq 'ALL') { "(alle Pakete)" } else { "(Paket $Pkg)" }
    return "Wurden alle Tasks aus dem Plan $Plan $what umgesetzt? Gibt es Anforderungen, welche nicht umgesetzt wurden? Liste jede nicht oder nur teilweise umgesetzte Anforderung mit Beleg (Datei:Zeile oder Commit) und schliesse mit 'Offene Anforderungen: <Anzahl>'. Schreib die Antwort in die State-Datei. Aendere nichts am Code. Follow all rules in section 1 of ORCHESTRATOR.md."
}
# Builds one start_agent action.
function New-StartAction($Name, $Type, $Phase, $Plan, $Pkg, $Cwd, $Prompt, $Gfx, [string]$Class = 'medium', [string[]]$Bundle = @()) {
    return [pscustomobject]@{ action = 'start_agent'; name = $Name; type = $Type; phase = $Phase; plan = $Plan; package = $Pkg; cwd = $Cwd; prompt = $Prompt; graphics = [bool]$Gfx; class = $Class; bundle = @($Bundle) }
}
# Reads the short end of a verify answer for the brain.
function Get-FinalTail($Cfg, [string]$AgentName) {
    $ff = Join-Path $Cfg.runtime ('runs\' + $AgentName + '\final.txt')
    $tx = if (Test-Path $ff) { (Get-Content $ff -Raw -Encoding UTF8) } else { '' }
    if ($tx.Length -gt 1800) { $tx = $tx.Substring($tx.Length - 1800) }
    return $tx
}

# The routine decision of one round. $Ctx: Cfg, Repo, Plans (startable plan objects: path, status), Queue (integration rows), Questions, CanStartIntegrator, Free (free slots per provider name, plus workers and int).
# Returns @{ Actions = start_agent / plan_done actions; Events = events for the brain (type, key, plan, package, agent, text) }.
function Get-Routine($Ctx) {
    $cfg = $Ctx.Cfg
    $all = @(Read-AllAgents $cfg)
    $handled = Get-Handled $cfg
    $acts = @(); $events = @(); $chosen = @()
    $free = @{}; foreach ($pk in @($Ctx.Free.Keys)) { if ($pk -ne 'workers') { $free[[string]$pk] = [int]$Ctx.Free[$pk] } }   # free slots per provider name (and int = integrator slots)
    $workers = [Math]::Max(0, [int]$Ctx.Free.workers)
    $blocked = @(Get-BlockedTasks @($all))
    $decided = Get-DecidedItems $cfg $Ctx.Questions
    # Priority 1 and 2 (before everything else takes a slot): integration fixes for blocked queue rows, then work for really answered questions. Both are started by the routine itself (no brain call);
    # a task counts as started once an agent row with its package id exists, so a rejected start is simply tried again next round.
    foreach ($r in @($Ctx.AllRows | Where-Object { [string]$_.status -match '^blockiert' })) {
        $rp = $Ctx.Plans | Where-Object { (Norm-Plan $_.path) -eq (Norm-Plan ([string]$r.plan)) -or (Norm-Plan ([string]$r.plan)).EndsWith((Norm-Plan $_.path)) } | Select-Object -First 1
        $noPlan = (-not $rp) -and ([string]$r.plan -match '(?i)^\s*(keiner?|none|n/?a|-+|–)?\s*$')   # plan column "keiner": the row text itself is the task (plan 6.5)
        if ((-not $rp -and -not $noPlan) -or $workers -le 0) { continue }
        if ($noPlan) {
            $tip = ([string]$r.tip); $tip = $tip.Substring(0, [Math]::Min(7, $tip.Length)); $pk = "FIX-$tip"; $relP = Get-QueueRel $cfg
            if (@($all | Where-Object { (Norm-Plan $_.plan) -eq (Norm-Plan $relP) -and [string]$_.package -eq $pk -and $_.status -ne 'failed' }).Count -gt 0) { continue }
            if (@($blocked | Where-Object { (Norm-Plan $_.Plan) -eq (Norm-Plan $relP) -and $_.Package -eq $pk }).Count -gt 0) { continue }
            $pv = Select-Provider $cfg 'medium' @() $free
            if (-not $pv) { continue }
            $type = $pv.Name
            $nm = New-AgentName "$pk-work" $all $chosen; $chosen += $nm; $free[$type]--; $workers--
            $acts += New-StartAction $nm $type 'work' $relP $pk ([string]$Ctx.Repo) (Get-PromptText $cfg 'fixBlockedNoPlan' @{ branch = [string]$r.branch; tip = [string]$r.tip; status = ([string]$r.status + ' ' + [string]$r.note).Trim(); cwd = [string]$Ctx.Repo; newWorktree = ((Split-Path $Ctx.Repo -Parent) + '\' + [string]$cfg.worktreePrefix + '-fix-' + (ConvertTo-Slug ([string]$r.branch))) }) $false
            continue
        }
        $relP = [string]$rp.path
        $h2 = @($all | Where-Object { (Norm-Plan $_.plan) -eq (Norm-Plan $relP) -and $_.cwd } | Sort-Object { [string]$_.startedAt })
        if (-not $h2.Count) { continue }
        $tip = ([string]$r.tip); $tip = $tip.Substring(0, [Math]::Min(7, $tip.Length)); $pk = "FIX-$tip"
        if (@($all | Where-Object { (Norm-Plan $_.plan) -eq (Norm-Plan $relP) -and [string]$_.package -eq $pk -and $_.status -ne 'failed' }).Count -gt 0) { continue }   # a failed start is tried again (stops after two failures with the same error: blocked tasks)
        if (@($blocked | Where-Object { (Norm-Plan $_.Plan) -eq (Norm-Plan $relP) -and $_.Package -eq $pk }).Count -gt 0) { continue }
        $pv = Select-Provider $cfg 'medium' @() $free
        if (-not $pv) { continue }
        $type = $pv.Name
        $nm = New-AgentName ((Get-PlanTag $relP) + "-$pk-work") $all $chosen; $chosen += $nm; $free[$type]--; $workers--
        $acts += New-StartAction $nm $type 'work' $relP $pk ([string]$h2[-1].cwd) (Get-PromptText $cfg 'fixBlocked' @{ plan = $relP; branch = [string]$r.branch; tip = [string]$r.tip; status = [string]$r.status; cwd = [string]$h2[-1].cwd }) $false
    }
    foreach ($q in @($Ctx.Questions | Where-Object { $_.status -eq 'ANSWERED' })) {
        if ([string]$q.answer -match '(?i)duplicate') { continue }   # the coordinator marked it as a duplicate of an answered question
        $rp = $Ctx.Plans | Where-Object { (Norm-Plan $_.path) -eq (Norm-Plan ([string]$q.plan)) } | Select-Object -First 1
        if (-not $rp -or $workers -le 0) { continue }
        $relP = [string]$rp.path; $tok = Get-ItemToken ([string]$q.item); $pk = "Q-$tok"
        $absP = if ([System.IO.Path]::IsPathRooted($relP)) { $relP } else { Join-Path $Ctx.Repo $relP.Replace('/', '\') }
        if ($true) {   # answer to a lock question (item LOCK-<pkg> or "<pkg> <title>") that says "erledigt": the package is finished, no new work (the plan loop below marks it stateDone); no effect when the plan has no lock section for the package
            $lsl = @(Get-PlanStateFiles $absP | ForEach-Object { , @(Get-Content $_.FullName -Encoding UTF8) })
            $lq = Get-LockDecision $cfg $relP $(if ($tok -like 'LOCK-*') { $tok.Substring(5) } else { $tok }) $decided $Ctx.Questions $lsl
            if ($lq -and $lq.Done) { continue }
        }
        $done = $false   # the item already stands as implemented in a state file of the plan
        foreach ($sf in @(Get-PlanStateFiles $absP)) { foreach ($l in (Get-Content $sf.FullName -Encoding UTF8)) { if ($l -match '^\|' -and $l.Split('|').Count -gt 2 -and $l.Split('|')[1].ToUpper() -match ('(?<![A-Z0-9-])' + [regex]::Escape($tok) + '(?![A-Z0-9])') -and $l -match '(?i)(implementiert|umgesetzt|implemented)' -and $l -notmatch '(?i)(\bnicht\b|teilweise|\boffen\b|partial)') { $done = $true } } }
        if ($done) { continue }
        if (@($all | Where-Object { (Norm-Plan $_.plan) -eq (Norm-Plan $relP) -and [string]$_.package -eq $pk -and $_.status -ne 'failed' }).Count -gt 0) { continue }   # a failed start is tried again (stops after two failures with the same error: blocked tasks)
        $h2 = @($all | Where-Object { (Norm-Plan $_.plan) -eq (Norm-Plan $relP) -and $_.cwd } | Sort-Object { [string]$_.startedAt })
        if (-not $h2.Count) { continue }
        $pv = Select-Provider $cfg 'medium' @() $free
        if (-not $pv) { continue }
        $type = $pv.Name
        $nm = New-AgentName ((Get-PlanTag $relP) + "-$pk-work") $all $chosen; $chosen += $nm; $free[$type]--; $workers--
        $acts += New-StartAction $nm $type 'work' $relP $pk ([string]$h2[-1].cwd) (Get-PromptText $cfg 'answeredQuestion' @{ plan = $relP; item = [string]$q.item; qid = [string]$q.id; answer = (Get-AnswerText $cfg $tok); cwd = [string]$h2[-1].cwd }) $false
    }
    foreach ($pl in $Ctx.Plans) {
        $rel = [string]$pl.path
        $abs = if ([System.IO.Path]::IsPathRooted($rel)) { $rel } else { Join-Path $Ctx.Repo $rel.Replace('/', '\') }
        if (-not (Test-Path $abs)) { continue }
        $tag = Get-PlanTag $rel
        $pkgs = @(Get-PlanPackages $abs)
        $hist = @($all | Where-Object { (Norm-Plan $_.plan) -eq (Norm-Plan $rel) -and $_.cwd } | Sort-Object { [string]$_.startedAt })
        $cwd = if ($hist.Count) { [string]$hist[-1].cwd } else { '' }
        # New plan: the script cannot plan it (no package table or no known worktree) -> brain, once.
        if ($pkgs.Count -eq 0 -or -not $cwd) {
            if (-not $handled.ContainsKey("newplan:$rel")) { $events += [pscustomobject]@{ type = 'new_plan'; key = "newplan:$rel"; plan = $rel; package = ''; agent = ''; text = "Plan $rel is $($pl.status) and has " + $(if ($pkgs.Count) { 'no known worktree yet' } else { 'no readable package table' }) + ': plan its packages (worktree, branch, first work agents).' } }
            # The brain was asked once already and a package table exists: the routine starts the packages itself in the main checkout (the work prompt makes the agent create its own worktree), instead of skipping the plan forever.
            if ($pkgs.Count -eq 0 -or -not $handled.ContainsKey("newplan:$rel")) { continue }
            $cwd = [string]$Ctx.Repo
        }
        $stages = @{}
        foreach ($k in $pkgs) { $stages[$k.Id] = Get-PkgStage $cfg $all $rel $k.Id }
        # A package without agent history that the state files or the queue already show as finished is never startable (stage stateDone).
        $sdone = Get-StateDonePackages $abs @($pkgs | ForEach-Object { $_.Id }) $Ctx.AllRows
        foreach ($k in $pkgs) { if (($stages[$k.Id].Stage -eq 'new' -or $stages[$k.Id].Stage -eq 'open') -and $sdone.ContainsKey($k.Id)) { $stages[$k.Id] = [pscustomobject]@{ Stage = 'stateDone'; Work = $null; Verify = $null; Evidence = $sdone[$k.Id] } } }
        # Frozen packages (runtime\NOFOLLOW.json): no follow-up task of any kind, their results only go into the state file.
        foreach ($k in $pkgs) { if ($stages[$k.Id].Stage -ne 'running' -and (Test-Frozen $cfg $rel $k.Id)) { $stages[$k.Id] = [pscustomobject]@{ Stage = 'stateDone'; Work = $null; Verify = $null; Evidence = 'NOFOLLOW' } } }
        # Answered lock question (see Get-LockDecision): "erledigt / keine weiteren" -> the package counts as finished (stateDone); any other answer lifts the lock and forgives the earlier failures (Get-PackageFailures counts only agents started after that moment).
        $stLines = @(Get-PlanStateFiles $abs | ForEach-Object { , @(Get-Content $_.FullName -Encoding UTF8) })
        foreach ($k in (@($pkgs | ForEach-Object { $_.Id }) + 'ALL')) {
            $ld = Get-LockDecision $cfg $rel $k $decided $Ctx.Questions $stLines
            if (-not $ld) { continue }
            if (-not (Get-LockReleaseTime $cfg $rel $k)) { Set-LockRelease $cfg $rel $k }
            if ($ld.Done -and $k -ne 'ALL' -and $stages[$k].Stage -notin @('running', 'handoff', 'verified', 'stateDone')) { $stages[$k] = [pscustomobject]@{ Stage = 'stateDone'; Work = $null; Verify = $null; Evidence = ('lock answered: ' + $ld.Source) } }
        }
        # Lock (plan 12.4): a package whose verifications reported open items more often than the escalation allows (escalateAfter + 1 times: two attempts of the class, then one attempt one class higher) gets no further work agent, no event for the brain and no follow-up task; it is reported once under the open-question marker with the open items.
        $locked = @{}
        foreach ($k in (@($pkgs | ForEach-Object { $_.Id }) + 'ALL')) {
            $lst = if ($k -eq 'ALL') { Get-PkgStage $cfg $all $rel 'ALL' } else { $stages[$k] }
            if ($lst.Stage -eq 'open' -and $lst.Verify -and (Get-PackageFailures $cfg $all $rel $k) -gt [int]$cfg.escalateAfter) {
                $locked[$k] = $true
                $acts += [pscustomobject]@{ action = 'lock_package'; plan = $rel; package = $k; agent = [string]$lst.Verify.name; text = (Get-FinalTail $cfg ([string]$lst.Verify.name)) }
            }
        }
        # Events for the brain: failed agents, verify answers with open items.
        foreach ($k in $pkgs) {
            $st = $stages[$k.Id]
            $isBlocked = @($blocked | Where-Object { (Norm-Plan $_.Plan) -eq (Norm-Plan $rel) -and $_.Package -eq $k.Id }).Count -gt 0
            if ($st.Stage -eq 'failed' -and -not $isBlocked) {
                $ag = if ($st.Verify -and $st.Verify.status -eq 'failed') { $st.Verify } else { $st.Work }
                if (-not $handled.ContainsKey('failed:' + $ag.name)) { $events += [pscustomobject]@{ type = 'agent_failed'; key = 'failed:' + $ag.name; plan = $rel; package = $k.Id; agent = [string]$ag.name; text = "Agent $($ag.name) ($($ag.phase)) failed: $($ag.lastLine)" } }
            }
            # A verify answer with open items for a package that the state file already shows as finished creates no work task (mechanical rule; the open items are only noted).
            if ($st.Stage -eq 'open' -and -not $locked.ContainsKey($k.Id) -and -not $sdone.ContainsKey($k.Id) -and -not $handled.ContainsKey('verify:' + $st.Verify.name)) { $events += [pscustomobject]@{ type = 'verify_open'; key = 'verify:' + $st.Verify.name; plan = $rel; package = $k.Id; agent = [string]$st.Verify.name; text = (Get-FinalTail $cfg ([string]$st.Verify.name)) } }
        }
        # Verify after work.
        foreach ($k in $pkgs) {
            $st = $stages[$k.Id]
            $retry = ($st.Stage -eq 'failed' -and $st.Verify -and $st.Verify.status -eq 'failed' -and $st.Work -and [string]$st.Work.startedAt -lt [string]$st.Verify.startedAt -and @($blocked | Where-Object { (Norm-Plan $_.Plan) -eq (Norm-Plan $rel) -and $_.Package -eq $k.Id }).Count -eq 0)   # a verify that crashed is run again (stops after two crashes with the same error: blocked task)
            # A verify answer without the closing line "Offene Anforderungen: <n>" can be neither followed up nor locked: the verify is run again (at most 3 verify agents per package).
            $noLine = ($st.Stage -eq 'open' -and $st.Verify -and (Get-VerifyOpenCount $cfg ([string]$st.Verify.name)) -lt 0 -and @($all | Where-Object { (Norm-Plan $_.plan) -eq (Norm-Plan $rel) -and [string]$_.package -eq $k.Id -and $_.phase -eq 'verify' }).Count -lt 3 -and @($blocked | Where-Object { (Norm-Plan $_.Plan) -eq (Norm-Plan $rel) -and $_.Package -eq $k.Id }).Count -eq 0)
            if (($st.Stage -ne 'needVerify' -and -not $retry -and -not $noLine) -or $workers -le 0) { continue }
            $pv = Select-Provider $cfg 'medium' @() $free -Avoid (Get-RowProvider $st.Work)   # verify never below medium; another provider than the work agent when one is free
            if (-not $pv) { continue }
            $type = $pv.Name
            $nm = New-AgentName ("$tag-$($k.Id)-verify") $all $chosen; $chosen += $nm; $free[$type]--; $workers--
            $acts += New-StartAction $nm $type 'verify' $rel $k.Id ([string]$st.Work.cwd) (Get-VerifyPrompt $rel $k.Id) $false
        }
        # Follow-up tasks derived from the STATE (so they are found again after every restart of the loop, independent of handled events):
        # (1) a verify that answered with open requirements and no work after it -> a work agent for exactly those items (at most 3 rounds per package); (2) a work agent that failed technically ("pane never started") -> started again (at most 3 times).
        foreach ($k in (@($pkgs | ForEach-Object { $_.Id }) + 'ALL')) {
            if ($workers -le 0) { break }
            $st = if ($k -eq 'ALL') { Get-PkgStage $cfg $all $rel 'ALL' } else { $stages[$k] }
            $isBlockedK = @($blocked | Where-Object { (Norm-Plan $_.Plan) -eq (Norm-Plan $rel) -and $_.Package -eq $k }).Count -gt 0
            if ($isBlockedK -or $locked.ContainsKey($k) -or (Test-Frozen $cfg $rel $k)) { continue }
            if (@($acts | Where-Object { $_.action -eq 'start_agent' -and (Norm-Plan $_.plan) -eq (Norm-Plan $rel) -and @("Q-LOCK-$k", "Q-$k") -contains [string]$_.package }).Count -gt 0 -or @($all | Where-Object { (Norm-Plan $_.plan) -eq (Norm-Plan $rel) -and @("Q-LOCK-$k", "Q-$k") -contains [string]$_.package -and ($_.status -eq 'running' -or $_.status -eq 'starting') }).Count -gt 0) { continue }   # the work for the answered lock question is already starting or running: no second work agent on the same package
            $kmine = @($all | Where-Object { (Norm-Plan $_.plan) -eq (Norm-Plan $rel) -and [string]$_.package -eq $k })
            $wcwd = if ($st.Work -and $st.Work.cwd) { [string]$st.Work.cwd } else { $cwd }
            $wtype = if ($st.Work) { Get-RowProvider $st.Work } else { '' }
            $why = ''; $prompt = ''
            if ($st.Stage -eq 'open' -and $st.Verify -and (Get-VerifyOpenCount $cfg ([string]$st.Verify.name)) -gt 0 -and @($kmine | Where-Object { $_.phase -eq 'verify' -and $_.status -eq 'done' }).Count -lt 4) {
                $ans = (Get-RuntimeRel $cfg) + '/runs/' + $st.Verify.name + '/final.txt'
                $prompt = Get-PromptText $cfg 'verifyOpenWork' @{ plan = $rel; pkg = $k; verifier = [string]$st.Verify.name; answerPath = $ans; cwd = $wcwd }
            }
            elseif ($st.Stage -eq 'failed' -and $st.Work -and $st.Work.status -eq 'failed' -and [string]$st.Work.lastLine -match '^pane never started' -and (-not $st.Verify -or [string]$st.Verify.startedAt -lt [string]$st.Work.startedAt) -and @($kmine | Where-Object { $_.phase -eq 'work' -and $_.status -eq 'failed' -and [string]$_.lastLine -match '^pane never started' }).Count -lt 3) {
                $prompt = Get-PromptText $cfg 'restartWork' @{ plan = $rel; pkg = $k; previous = [string]$st.Work.name; cwd = $wcwd }
            }
            if (-not $prompt) { continue }
            $kc = @($pkgs | Where-Object { $_.Id -eq $k } | Select-Object -First 1)
            $kcls = Resolve-AgentClass $cfg 'work' $(if ($kc.Count) { $kc[0].Class } else { 'medium' }) (Get-PackageFailures $cfg $all $rel $k)
            $kneeds = @(if ($kc.Count) { Get-PackageNeeds $cfg ([string]$kc[0].Content) })
            $pv = Select-Provider $cfg $kcls $kneeds $free -Prefer $wtype   # the follow-up stays with the provider of the earlier task when that one is a candidate
            if (-not $pv) { continue }
            $type = $pv.Name
            $nm = New-AgentName ("$tag-$k-work") $all $chosen; $chosen += $nm; $free[$type]--; $workers--
            $acts += New-StartAction $nm $type 'work' $rel $k $wcwd $prompt ($kneeds.Count -gt 0) $kcls
        }
        # Whole-plan verify, release, plan done.
        $allVerified = (@($pkgs | Where-Object { $stages[$_.Id].Stage -ne 'verified' -and $stages[$_.Id].Stage -ne 'stateDone' }).Count -eq 0)
        $allSt = Get-PkgStage $cfg $all $rel 'ALL'
        $allRetry = ($allSt.Stage -eq 'failed' -and $allSt.Verify -and $allSt.Verify.status -eq 'failed' -and @($blocked | Where-Object { (Norm-Plan $_.Plan) -eq (Norm-Plan $rel) -and $_.Package -eq 'ALL' }).Count -eq 0)   # a crashed whole-plan verify is run again (stops after two crashes with the same error; 'pane never started' never blocks)
        if ($allVerified -and ($allSt.Stage -eq 'new' -or $allSt.Stage -eq 'needVerify' -or $allRetry) -and $workers -gt 0) {
            $lastW = @($all | Where-Object { (Norm-Plan $_.plan) -eq (Norm-Plan $rel) -and $_.phase -eq 'work' } | Sort-Object { [string]$_.startedAt } | Select-Object -Last 1)
            $pv = Select-Provider $cfg 'medium' @() $free -Avoid $(if ($lastW.Count) { Get-RowProvider $lastW[0] } else { '' })
            if ($pv) {
                $type = $pv.Name
                $nm = New-AgentName ("$tag-ALL-verify") $all $chosen; $chosen += $nm; $free[$type]--; $workers--
                $acts += New-StartAction $nm $type 'verify' $rel 'ALL' $cwd (Get-VerifyPrompt $rel 'ALL') $false
            }
        }
        if ($allSt.Stage -eq 'open' -and -not $locked.ContainsKey('ALL') -and -not $handled.ContainsKey('verify:' + $allSt.Verify.name)) { $events += [pscustomobject]@{ type = 'verify_open'; key = 'verify:' + $allSt.Verify.name; plan = $rel; package = 'ALL'; agent = [string]$allSt.Verify.name; text = (Get-FinalTail $cfg ([string]$allSt.Verify.name)) } }
        $rels = @($all | Where-Object { (Norm-Plan $_.plan) -eq (Norm-Plan $rel) -and $_.phase -eq 'release' })
        $relPv = if ($allSt.Stage -eq 'verified' -and $rels.Count -eq 0 -and $workers -gt 0) { Select-Provider $cfg 'medium' @() $free -Protocol 'claude' } else { $null }   # release and integrator agents are Claude Code agents
        if ($relPv) {
            $nm = New-AgentName ("$tag-release") $all $chosen; $chosen += $nm; $free[$relPv.Name]--; $workers--
            $acts += New-StartAction $nm $relPv.Name 'release' $rel 'REL' $cwd (Get-PromptText $cfg 'release' @{ plan = $rel; cwd = $cwd }) $false
        }
        $openRows = @($Ctx.Queue | Where-Object { [string]$_.plan -like ('*' + $rel) })
        if ($allSt.Stage -eq 'verified' -and $pl.status -eq 'IN_PROGRESS' -and @($rels | Where-Object { $_.status -eq 'done' }).Count -gt 0 -and $openRows.Count -eq 0) { $acts += [pscustomobject]@{ action = 'plan_done'; plan = $rel } }
        # New work: fill the remaining slots with startable packages (dependencies verified, nothing running).
        $bundled = @()
        $isStartable = { param($p) $stages[$p.Id].Stage -eq 'new' -and @($p.Depends | Where-Object { -not $stages.ContainsKey($_) -or ($stages[$_].Stage -ne 'verified' -and $stages[$_].Stage -ne 'stateDone') }).Count -eq 0 -and @($blocked | Where-Object { (Norm-Plan $_.Plan) -eq (Norm-Plan $rel) -and $_.Package -eq $p.Id }).Count -eq 0 }
        foreach ($k in $pkgs) {
            if ($stages[$k.Id].Stage -ne 'new' -or $workers -le 0 -or $bundled -contains $k.Id) { continue }
            if (@($k.Depends | Where-Object { -not $stages.ContainsKey($_) -or ($stages[$_].Stage -ne 'verified' -and $stages[$_].Stage -ne 'stateDone') }).Count -gt 0) { continue }
            if (@($blocked | Where-Object { (Norm-Plan $_.Plan) -eq (Norm-Plan $rel) -and $_.Package -eq $k.Id }).Count -gt 0) { continue }
            $needs = @(Get-PackageNeeds $cfg ([string]$k.Content)); $nk = ($needs -join ',')
            if ($needs.Count -gt 0 -and -not (Test-CapabilityAvailable $cfg $needs)) {
                # No provider with the needed capability has slots above 0: the package stays where it is and is reported once (round.ps1 does not repeat a known question).
                $acts += [pscustomobject]@{ action = 'ask_owner'; plan = $rel; item = "CAP-$($k.Id)"; question = "Package $($k.Id) needs the capability '$($needs -join ', ')' but no provider with it has slots above 0, so the package is not started."; recommendation = "Set slots above 0 for a provider with this capability in the project file, or tell me how this package should run." }
                continue
            }
            $kcls = Resolve-AgentClass $cfg 'work' $k.Class 0
            $pv = Select-Provider $cfg $kcls $needs $free
            if (-not $pv) { continue }
            $type = $pv.Name
            # Bundling (plan 12.3): small (light) packages of the same plan with the same agent type run in ONE agent, because the start context costs more than a small task; at most config bundle.maxPackages per agent.
            $members = @($k)
            if ($k.Class -eq 'light') {
                foreach ($o in $pkgs) {
                    if ($members.Count -ge [int]$cfg.bundle.maxPackages) { break }
                    if ($o.Id -eq $k.Id -or $bundled -contains $o.Id -or $o.Class -ne 'light' -or ((@(Get-PackageNeeds $cfg ([string]$o.Content)) -join ',') -ne $nk)) { continue }
                    if (& $isStartable $o) { $members += $o }
                }
            }
            $bundled += @($members | ForEach-Object { $_.Id })
            $nm = New-AgentName ("$tag-$($k.Id)-work") $all $chosen; $chosen += $nm; $free[$type]--; $workers--
            $texts = @()
            foreach ($m in $members) {
                $wtNote = if ([IO.Path]::GetFullPath($cwd).TrimEnd('') -ieq [IO.Path]::GetFullPath([string]$Ctx.Repo).TrimEnd('')) { Get-PromptText $cfg 'workInMainCheckout' @{ cwd = $cwd; newWorktree = ((Split-Path $Ctx.Repo -Parent) + '\' + [string]$cfg.worktreePrefix + '-' + (ConvertTo-Slug $tag) + '-' + (ConvertTo-Slug $m.Id)); subBranch = ((ConvertTo-Slug $tag) + '-' + (ConvertTo-Slug $m.Id)) } } else { Get-PromptText $cfg 'workInWorktree' @{ cwd = $cwd } }
                $texts += "Plan: $rel, package $($m.Id). $wtNote Names of worktree folders and branches you create: only lower-case letters, digits and hyphens (for this package: $((ConvertTo-Slug $tag) + '-' + (ConvertTo-Slug $m.Id))). Do the package exactly as the plan describes it."
            }
            $tail = " Follow all rules in section 1 of ORCHESTRATOR.md, update the plan's state file, end when the package is finished."
            $prompt = if ($members.Count -gt 1) { "This agent works $($members.Count) small packages of plan $rel one after the other (bundled so that the start context is paid once). Finish each package and record it in the state file before you start the next. Packages in this order: $(($members | ForEach-Object { $_.Id }) -join ', ').`n`n" + ($texts -join "`n`n") + $tail } else { $texts[0] + $tail }
            $acts += New-StartAction $nm $type 'work' $rel $k.Id $cwd $prompt ($needs.Count -gt 0) $kcls @($members | ForEach-Object { $_.Id })
        }
        # New open-question entries (marker openMarker) of the state files of this plan -> brain (needs plain words and a recommendation).
        foreach ($sf in @(Get-PlanStateFiles $abs)) {
            # Sections with the marker openMarker (also "### <marker> - M3") of the state file of THIS plan; a section is done when a later heading of the same file names a decision or answer for its tag (e.g. "Entscheidung zu <marker> - M3").
            $slines = @(Get-Content $sf.FullName -Encoding UTF8)
            $secs = @(); $cur = $null
            for ($li = 0; $li -lt $slines.Count; $li++) {
                $l = $slines[$li]
                if ($l -match ('^#+\s*' + [regex]::Escape([string]$cfg.openMarker) + '\s*(?:-\s*(.+))?$')) { $cur = [pscustomobject]@{ Tag = [string]$Matches[1]; Line = $li; Text = @() }; $secs += $cur; continue }
                if ($cur -and $l -match '^#') { $cur = $null }
                if ($cur -and $l.Trim()) { $cur.Text += $l.Trim() }
            }
            $sec = @()
            foreach ($sc in $secs) {
                $tg = $sc.Tag.Trim()
                $resolved = $false
                # Items of the section: its tag, else the item ids named in its text (all of them must be decided later).
                $items = if ($tg) { @($tg) } else { @([regex]::Matches(($sc.Text -join ' '), '(?<![A-Za-z0-9.-])[A-Z]{1,4}(?:-[A-Z]+)?-?\d+[A-Za-z]?(?![A-Za-z0-9])') | ForEach-Object { $_.Value } | Select-Object -Unique) }
                if ($items.Count) {
                    $resolved = $true
                    foreach ($it in $items) {
                        $hit = $false
                        for ($li = $sc.Line + 1; $li -lt $slines.Count; $li++) { if ($slines[$li] -match '^#' -and $slines[$li] -match '(?i)(Entscheidung|decision|Antwort|answer|erledigt|umgesetzt|implemented)' -and $slines[$li] -match ('(?<![A-Za-z0-9-])' + [regex]::Escape($it) + '(?![A-Za-z0-9])')) { $hit = $true; break } }
                        if (-not $hit) { $resolved = $false; break }
                    }
                }
                if (-not $resolved -and (($sc.Text -join ' ') -match '\?')) { $sec += $sc.Text }   # a section without a question mark ("leer", "keine Frage") asks nothing
            }
            $txt = ($sec -join "`n")
            $ids = @([regex]::Matches($txt, '(?<![A-Za-z0-9-])[A-Za-z]{1,4}(?:-[A-Za-z]+)?-?\d+[A-Za-z]?(?![A-Za-z0-9])') | ForEach-Object { $_.Value })
            $allDecided = ($ids.Count -gt 0 -and @($ids | Where-Object { -not (Test-ItemDecided $decided $rel $_) }).Count -eq 0)   # every item named in the section is already decided or asked
            if ($txt -and $txt -notmatch '^(?i)(none|keine)\.?$' -and -not $allDecided) {
                $h = [BitConverter]::ToString([System.Security.Cryptography.SHA1]::Create().ComputeHash([System.Text.Encoding]::UTF8.GetBytes($txt))).Replace('-', '').Substring(0, 10)
                $key = "open:${rel}:$h"
                if (-not $handled.ContainsKey($key)) { $events += [pscustomobject]@{ type = 'open_for_owner'; key = $key; plan = $rel; package = ''; agent = ''; text = $(if ($txt.Length -gt 1500) { $txt.Substring(0, 1500) } else { $txt }) } }
            }
        }
    }
    # Context limit (rule 13): agents that ended with a handoff get a fresh continuation agent.
    $wref = [ref]$workers
    $cont = @(Get-HandoffContinuations $cfg $all $free $wref $chosen)
    $workers = $wref.Value
    foreach ($c in $cont) { $chosen += $c.name }
    $acts += $cont
    # Integration: blocked rows go to the brain, a ready row without a running integrator starts the integrator.
    # Blocked rows are handled at the top of this function (fix agent started by the routine itself, no brain event).
    $intPv = if ($Ctx.CanStartIntegrator -and [int]$Ctx.Free.int -gt 0) { @(Get-Providers $cfg | Where-Object { $_.Protocol -eq 'claude' -and $_.Slots -gt 0 -and $_.Classes.medium.model } | Select-Object -First 1) } else { @() }   # the integrator is a Claude Code agent (class medium); it uses no worker slot, but a provider that is off (slots 0) does not run it
    if ($intPv.Count) {
        $nm = New-AgentName ('INTEGRATOR-' + (Get-Date -Format 'MMdd-HHmm')) $all $chosen
        $acts += New-StartAction $nm $intPv[0].Name 'integrate' (Get-QueueRel $cfg) ('INT-' + (Get-Date -Format 'MMddHHmm')) $Ctx.Repo (Get-PromptText $cfg 'integrator' @{}) $false
    }
    # Answered questions -> brain, once each.
    # Answered questions are handled at the top of this function (work agent started by the routine itself, no brain event).
    foreach ($q in @()) {
        if (-not $handled.ContainsKey('q:' + $q.id)) { $events += [pscustomobject]@{ type = 'question_answered'; key = 'q:' + $q.id; plan = [string]$q.plan; package = ''; agent = ''; text = "Question $($q.id) ($($q.plan) / $($q.item)) is answered in the answers file: start a work agent for that item with the answer. Answer: " + (Get-AnswerText $cfg (Get-ItemToken $q.item)) } }
    }
    return @{ Actions = $acts; Events = $events }
}

# Context limit (rule 13): writes the flag file runs\<name>\HANDOFF for every running agent whose current context reached contextLimitTokens (the agent is never killed). Returns @(@{ Name; Ctx }) of the newly flagged agents.
function Set-HandoffFlags($Cfg, $Agents) {
    $limit = if ($Cfg.contextLimitTokens) { [int64]$Cfg.contextLimitTokens } else { 300000 }
    $res = @()
    foreach ($a in @($Agents | Where-Object { ($_.status -eq 'running' -or $_.status -eq 'starting') -and $null -ne $_.ctxTokens -and [int64]$_.ctxTokens -ge $limit })) {
        $f = Join-Path $Cfg.runtime ('runs' + [char]92 + $a.name + '\HANDOFF')
        if (-not (Test-Path $f)) { Set-Content -Path $f -Value ('context ' + $a.ctxTokens + ' reached ' + (Get-Date).ToString('o')) -Encoding UTF8; $res += @{ Name = [string]$a.name; Ctx = [int64]$a.ctxTokens } }
    }
    return $res
}
# Fresh continuation agents (-c2, -c3 ...) for agents that ended with a handoff (rule 13); the task text is taken from the prompt file of the ended agent.
function Get-HandoffContinuations($Cfg, $All, $Free, [ref]$Workers, $Taken) {
    $acts = @()
    foreach ($a in @($All | Where-Object { $_.status -eq 'done' })) {
        if (-not (Test-Path (Join-Path $Cfg.runtime ('runs' + [char]92 + $a.name + '\HANDOFF')))) { continue }
        $base = [string]$a.name -replace '-c\d+$', ''
        $succ = @($All | Where-Object { $_.name -match ('^' + [regex]::Escape($base) + '-c\d+$') -and [string]$_.startedAt -gt [string]$a.startedAt })
        if ($succ.Count -gt 0) { continue }
        $isInt = ([string]$a.phase -eq 'integrate')
        if ($isInt) { if ([int]$Free.int -le 0) { continue } } else { if ($Workers.Value -le 0 -or $Free[(Get-RowProvider $a)] -le 0) { continue } }
        $pf = Join-Path $Cfg.runtime ('runs' + [char]92 + $a.name + '\prompt.md')
        $body = ''
        if (Test-Path $pf) { $t = (Get-Content $pf -Raw -Encoding UTF8).TrimStart([char]0xFEFF); $i = $t.IndexOf('TASK'); if ($i -ge 0) { $t = $t.Substring($i + 4).TrimStart() }; $j = $t.LastIndexOf('Pr' + [char]0xFC + 'fe nach jedem'); if ($j -gt 0) { $t = $t.Substring(0, $j).TrimEnd() }; $body = $t }
        $k = 2; while (@($All | Where-Object { $_.name -eq "$base-c$k" }).Count -gt 0 -or $Taken -contains "$base-c$k") { $k++ }
        $nm = "$base-c$k"
        if ($isInt) { $Free.int = [int]$Free.int - 1 } else { $Free[(Get-RowProvider $a)] = $Free[(Get-RowProvider $a)] - 1; $Workers.Value-- }
        $acts += New-StartAction $nm (Get-RowProvider $a) ([string]$a.phase) ([string]$a.plan) ([string]$a.package) ([string]$a.cwd) ($body + "`n`nLies zuerst 'Handoff " + $a.name + "' in der State-Datei des Plans und mach von dort weiter.") $false $(if ($a.class) { [string]$a.class } else { 'medium' }) @($a.bundle)
    }
    return $acts
}


# Removes an event key from ORCH-HANDLED.json (the event is asked again in the next round).
function Remove-Handled($Cfg, [string]$Key) {
    $h = Get-Handled $Cfg
    if ($h.ContainsKey($Key)) { [void]$h.Remove($Key); Write-FileAtomic (Join-Path $Cfg.runtime 'ORCH-HANDLED.json') (ConvertTo-Json -InputObject $h -Compress) }
}