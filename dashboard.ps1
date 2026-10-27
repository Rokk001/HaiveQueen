# Dashboard pane "Übersicht" (PowerShell 7, file saved as UTF-8 with BOM).
# Sections LIMITS, SYSTEM, PLAENE, AGENTEN, FRAGEN. Agents every 5 s, usage limits / plan counts once a minute.
# Every line is cut to the pane width so nothing wraps in the middle of a line.
param([string]$Project = '')
if ($Project) { $env:ORCH_PROJECT = $Project }
. (Join-Path $PSScriptRoot 'lib.ps1')
$cfg = Get-Cfg
if (Test-Path (Join-Path $PSScriptRoot 'usage.ps1')) { . (Join-Path $PSScriptRoot 'usage.ps1') }
$usage = $null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$FULL = [string][char]0x2588; $LIGHT = [string][char]0x2591; $LINE = [string][char]0x2500

# Formats a token count (1234 -> 1.2k).
# Whole-number token count for the agent list (75k, 2k, 1M); n/a when unknown.
function Fmt-Tok0($n) { $ic = [cultureinfo]::InvariantCulture; if ($null -eq $n) { return [string][char]0x2013 }; if ($n -ge 1000000000) { return [string]::Format($ic, '{0:0.0} Mrd.', $n / 1e9) }; if ($n -ge 1000000) { return [string]::Format($ic, '{0:0.0}M', $n / 1e6) }; if ($n -ge 1000) { return [string]::Format($ic, '{0:0}k', $n / 1e3) }; return "$n" }   # invariant culture: no thousands separators
function Fmt-Tok($n) { if ($null -eq $n) { return 'n/a' }; if ($n -ge 1000000) { return ('{0:N1}M' -f ($n / 1e6)) }; if ($n -ge 1000) { return ('{0:N1}k' -f ($n / 1e3)) }; return "$n" }
# Cuts a text to the pane width.
function Cut([string]$s, [int]$w) { if ($s.Length -gt $w) { return $s.Substring(0, [Math]::Max(0, $w - 1)) + '~' }; return $s }
# Color by fill level: green under 60 %, yellow 60 to 85 %, red above.
function Level-Color([double]$p) { if ($p -lt 60) { return 'Green' }; if ($p -le 85) { return 'Yellow' }; return 'Red' }
# One colored piece of a display line.
function P([string]$t, [string]$c = 'Gray') { return [pscustomobject]@{ t = $t; c = $c } }
# Wraps the parts of one logical line to the width $w: a part that does not fit moves to a new line (indented by 2), an over-long part is split hard. Nothing is cut, no '~'. Appends the physical lines to the list $res.
function Wrap-Parts($parts, [int]$w, $res) {
    $cur = @(); $used = 0
    foreach ($p in $parts) {
        $t = $p.t
        if ($used -gt 0 -and ($used + $t.Length) -gt $w -and ($t.TrimStart().Length + 2) -le $w) { $res.Add($cur); $cur = @(); $used = 0; $t = '  ' + $t.TrimStart() }
        while (($used + $t.Length) -gt $w) {
            $room = $w - $used
            if ($room -le 0) { $res.Add($cur); $cur = @(); $used = 0; continue }
            $cur += (P $t.Substring(0, $room) $p.c); $res.Add($cur); $cur = @(); $used = 0
            $t = '  ' + $t.Substring($room).TrimStart()
        }
        if ($t.Length -gt 0) { $cur += (P $t $p.c); $used += $t.Length }
    }
    if ($cur.Count -gt 0) { $res.Add($cur) }
}
# Expands logical lines to the physical lines for the width $w.
function Get-Physical($lines, [int]$w) {
    $out = New-Object System.Collections.Generic.List[object]
    foreach ($ln in $lines) { Wrap-Parts $ln $w $out }
    return $out
}
# Running agents: two lines, always shown. Agents ended within the last hour: one line. Older ones are not shown (AGENTS.json keeps them until the 12 h archiving).
# Builds all display lines for one detail level: 0 = bars and separators, 1 = numbers only, 2 = numbers only and no separators, 3 = no headings, all limits in one line. $d holds the collected data.
function Build-Lines([int]$level, $d, [int]$w, [int]$nFin) {
    $lines = New-Object System.Collections.Generic.List[object]
    $bars = $true; $sep = $true; $compact = $false   # the limit bars are always shown (LIMITS contains only the bars); $level only shrinks the model list of VERBRAUCH
    # Adds a section heading: "-- TITLE -----" over the full width, title in its color, line grey.
    function Add-Head([string]$title, [string]$c = 'Cyan', [string]$extra = '') {
        $used = 3 + $title.Length + $extra.Length + 1
        $lines.Add(@((P ($LINE + $LINE + ' ') 'DarkGray'), (P $title $c), (P $extra $c), (P (' ' + ($LINE * [Math]::Max(1, $w - $used))) 'DarkGray')))
    }
    # Adds one limit line: label, optional 10 character bar, percent and reset time (compact level: all limits in one line).
    function Add-Limit([string]$label, $pct, [string]$sfx) {
        if ($compact) { $txt = if ($null -eq $pct) { 'n/a' } else { ('{0}% {1}' -f [int]$pct, $sfx) }; $script:limParts += (P ($label + ' ' + $txt + '  ') $(if ($null -eq $pct) { 'DarkGray' } else { Level-Color $pct })); return }
        $lab = $label.PadRight(10)
        if ($null -eq $pct) { $lines.Add(@((P $lab 'Gray'), (P 'n/a' 'DarkGray'))); return }
        $col = Level-Color $pct
        $parts = @((P $lab 'Gray'))
        if ($bars) { $f = [int][Math]::Round(10 * [Math]::Min(100, [Math]::Max(0, $pct)) / 100); $parts += (P ($FULL * $f) $col); $parts += (P ($LIGHT * (10 - $f)) 'DarkGray') }
        $parts += (P (' {0,3}% {1}' -f [int]$pct, $sfx) $col)
        $lines.Add($parts)
    }
    $agentCount = @($d.agents).Count   # running agents only; the ended ones stand under their own line below; CHIEF is shown only in the orchestrator pane
    Add-Head ('AGENTEN {0}' -f $agentCount) 'Cyan' ('  {0}  letzte Runde {1}' -f $d.now, $d.lastRound)
    # One agent (or CHIEF) in the three-line format of the pane header; no line wraps and none starts with "|": a too long first line shortens the agent id ("..."), then the end of the status.
    function Add-Agent([string]$pn, [string]$id, [string]$typ, [string]$ms, [string]$st, [string]$rt, [string]$nameCol, [string]$stCol, [string]$task, $tokParts, [string]$tokCol, [string]$phase = '') {
        # Single-width status symbol before the status word (running / starting / done / failed, anything else = waiting) and phase symbol before the task line.
        $stSym = switch ($st) { 'running' { [string][char]0x25BA } 'starting' { [string][char]0x25CC } 'done' { [string][char]0x2713 } 'failed' { [string][char]0x2717 } default { [string][char]0x2016 } }
        $phSym = switch ($phase) { 'work' { [string][char]0x270E } 'verify' { [string][char]0x26B2 } 'release' { [string][char]0x21EA } 'integrate' { [string][char]0x26C1 } default { '' } }
        if ($phSym) { $task = $phSym + ' ' + $task }
        $tail = (($stSym + ' ' + $st + ' ' + $rt).Trim())
        $idOwnLine = $false
        # Width fallback of line 1: the type word (Claude / Codex) is never shortened; the id is cut but stays at least 20 characters long, else it moves to its own line (never "G...").
        $mid = "$typ $ms"
        $room = $w - ($pn + ' | ' + ' | ' + $mid + ' | ' + $tail).Length
        $idShown = $id
        if ($id.Length -gt $room) { if ($room -ge 20) { $idShown = $id.Substring(0, $room - 3) + '...' } else { $idShown = ''; $idOwnLine = $true } }
        $left = if ($idShown) { "$pn | $idShown | $mid | " } else { "$pn | $mid | " }
        if (($left.Length + $tail.Length) -gt $w) { $tail = $tail.Substring(0, [Math]::Max(0, $w - $left.Length - 3)) + '...' }        $lines.Add(@((P $left $nameCol), (P $tail $stCol)))
        if ($idOwnLine) { $idl = $id; if ($idl.Length -gt $w) { $idl = $idl.Substring(0, $w - 3) + '...' }; $lines.Add(@((P $idl $nameCol))) }
        $t = ([string]$task -replace '\s+', ' ').Trim(); if ($t.Length -gt $w) { $t = $t.Substring(0, $w - 3) + '...' }
        $lines.Add(@((P $t 'Gray')))
        $tp = @(); $used = 0
        foreach ($x in $tokParts) { if ($used -gt 0 -and ($used + $x.Length) -gt $w) { $lines.Add($tp); $tp = @(); $used = 0 }; $y = if ($x.Length -gt $w) { $x.Substring(0, $w - 3) + '...' } else { $x }; $tp += (P $y $tokCol); $used += $y.Length }
        if ($tp.Count) { $lines.Add($tp) }
    }
    if ($d.agents.Count -eq 0 -and $d.blocked.Count -eq 0 -and @($d.fin).Count -eq 0) { $lines.Add(@(P 'keine Agenten' 'DarkGray')) }
    foreach ($b in $d.blocked) { $lines.Add(@(P ('BLOCKIERT ' + $b.Name + ': ' + $b.Error) 'Red')) }
    # Running agents always; of the ended ones of the last hour only the newest $nFin, the rest as one summary line.
    $shown = @($d.agents) + @($d.fin | Select-Object -First $nFin)
    $finHeadDone = $false
    foreach ($a in $shown) {
        if (-not $finHeadDone -and $a.status -ne 'running' -and $a.status -ne 'starting') { $lines.Add(@(P 'beendet letzte Stunde' 'DarkGray')); $finHeadDone = $true }
        $st = [string]$a.status
        $col = switch ($st) { 'done' { 'Green' } 'running' { 'Yellow' } 'starting' { 'Yellow' } 'failed' { 'Red' } default { 'DarkGray' } }
        $rt = ''
        if ($a.startedAt) { $s0 = [datetime]$a.startedAt; $e0 = if ($a.endedAt) { [datetime]$a.endedAt } else { Get-Date }; $rt = '{0:mm\:ss}' -f ($e0 - $s0); if (($e0 - $s0).TotalHours -ge 1) { $rt = '{0:h\:mm\:ss}' -f ($e0 - $s0) } }
        $typ = if ($a.type -eq 'claude') { 'Claude' } elseif ($a.type -eq 'codex') { 'Codex' } else { [string]$a.type }
        $ac = if ($a.color) { [string]$a.color } else { 'FFFFFF' }
        $pn = if ($a.person) { ([string]$a.person).ToUpper() } else { '-' }
        Add-Agent $pn ([string]$a.name) $typ ([string]$a.modelShort) $st $rt $ac $col ([string]$a.task) @((($(if ($null -ne $a.ctxTokens -and [int64]$a.ctxTokens -ge 300000) { [string][char]0x25B2 + ' ' } else { '' })) + (Format-TokenLine $a.ctxTokens $a.tokensIn $a.tokensOut))) $(if ($null -ne $a.ctxTokens -and [int64]$a.ctxTokens -ge 300000) { 'Red' } else { 'White' }) ([string]$a.phase)
    }
    if ($nFin -lt @($d.fin).Count) { $hid = @($d.fin | Select-Object -Skip $nFin); $lines.Add(@(P ('weitere beendete: {0}, davon failed: {1}' -f $hid.Count, @($hid | Where-Object { $_.status -eq 'failed' }).Count) 'DarkGray')) }
    $script:limParts = @()
    Add-Head ([string][char]0x25D4 + ' LIMITS')
    Add-Limit 'Claude 5h' $(if ($d.cu.FiveHour) { $d.cu.FiveHour.Pct } else { $null }) $(if ($d.cu.FiveHour) { $d.cu.FiveHour.Reset } else { '' })
    Add-Limit 'Claude 7d' $(if ($d.cu.SevenDay) { $d.cu.SevenDay.Pct } else { $null }) $(if ($d.cu.SevenDay) { $d.cu.SevenDay.Reset } else { '' })
    if ($d.xu.FiveHour) { Add-Limit 'Codex 5h' $d.xu.FiveHour.Pct $d.xu.FiveHour.Reset }
    Add-Limit 'Codex 7d' $(if ($d.xu.SevenDay) { $d.xu.SevenDay.Pct } else { $null }) $(if ($d.xu.SevenDay) { $d.xu.SevenDay.Reset } else { '' })
    if ($compact -and $script:limParts.Count -gt 0) { $lines.Add($script:limParts) }
    # VERBRAUCH: aligned table, one column per model found in the data, rows today / all plans / total since the oldest log. When the width is too small, every model becomes its own aligned block (same right edge for all values).
    Add-Head ([string][char]0x3A3 + ' VERBRAUCH')
    $us = $d.usage
    # Level 0 lists every model of the logs; from level 1 only models active today or in the orchestrator data; at level 3 at most two of them plus a summary line (a model is never cut in the middle).
    $umodels = @($us.Models | Where-Object { $level -eq 0 -or ($_.Today -and [int64]$_.Today.In -gt 0) -or ($_.Plans -and $null -ne $_.Plans.In) })
    $hiddenModels = 0
    if ($level -ge 3 -and $umodels.Count -gt 2) { $hiddenModels = $umodels.Count - 2; $umodels = @($umodels | Select-Object -First 2) }
    if (-not $us -or $umodels.Count -eq 0) { $lines.Add(@(P 'keine Daten' 'DarkGray')) }
    else {
        $us = [pscustomobject]@{ Models = $umodels }
        foreach ($m in $us.Models) { $m.Label = ([string]$m.Label) -replace 'codex-auto-review', 'Auto-Review' }   # the internal reviewer model of Codex (approvals_reviewer auto_review)
        $since = @($us.Models | Where-Object { $_.Since } | ForEach-Object { [datetime]$_.Since } | Sort-Object | Select-Object -First 1)
        $rowLabels = @('heute', ('alle Pl' + [char]0xE4 + 'ne'), $(if ($since.Count) { 'gesamt seit ' + $since[0].ToString('dd.MM.') } else { 'gesamt' }))
        $cell = { param($v) if ($null -eq $v -or $null -eq $v.In) { 'keine Daten' } else { 'Total Input ' + (Fmt-Tok0 $v.In) + ' / Output ' + (Fmt-Tok0 $v.Out) } }
        $cells = @{}; foreach ($m in $us.Models) { $cells[$m.Label] = @((& $cell $m.Today), (& $cell $m.Plans), (& $cell $m.Total)) }
        $lwT = ($rowLabels | Measure-Object -Property Length -Maximum).Maximum
        $colW = @{}; foreach ($m in $us.Models) { $colW[$m.Label] = [Math]::Max($m.Label.Length, ($cells[$m.Label] | Measure-Object -Property Length -Maximum).Maximum) }
        $need = $lwT + (($us.Models | ForEach-Object { 2 + $colW[$_.Label] }) | Measure-Object -Sum).Sum
        if ($need -le $w) {
            $hdr = @((P (''.PadRight($lwT)) 'Gray')); foreach ($m in $us.Models) { $hdr += (P ('  ' + $m.Label.PadLeft($colW[$m.Label])) 'Cyan') }; $lines.Add($hdr)
            for ($ri = 0; $ri -lt 3; $ri++) { $r = @((P $rowLabels[$ri].PadRight($lwT) 'Gray')); foreach ($m in $us.Models) { $r += (P ('  ' + $cells[$m.Label][$ri].PadLeft($colW[$m.Label])) 'White') }; $lines.Add($r) }
        }
        else {
            $cwAll = ($us.Models | ForEach-Object { $cells[$_.Label] } | Measure-Object -Property Length -Maximum).Maximum   # one value width for all blocks: same right edge
            $labAll = @('heute', ('alle Pl' + [char]0xE4 + 'ne'), 'seit 00.00.'); $lw = ($labAll | Measure-Object -Property Length -Maximum).Maximum
            $oneLine = ((2 + $lw + 1 + $cwAll) -le $w)   # label and value share a line when the pane is wide enough, else the value stands right-aligned under its label
            foreach ($m in $us.Models) {
                $lines.Add(@((P $m.Label 'Cyan')))
                $lab2 = @($rowLabels[0], $rowLabels[1], $(if ($m.Since) { 'seit ' + ([datetime]$m.Since).ToString('dd.MM.') } else { 'gesamt' }))   # each model block shows its own start date
                for ($ri = 0; $ri -lt 3; $ri++) {
                    if ($oneLine) { $lines.Add(@((P ('  ' + $lab2[$ri].PadRight($lw)) 'Gray'), (P (' ' + $cells[$m.Label][$ri].PadLeft($cwAll)) 'White'))) }
                    else { $lines.Add(@((P ('  ' + $lab2[$ri]) 'Gray'))); $lines.Add(@((P ($cells[$m.Label][$ri].PadLeft($w)) 'White'))) }
                }
            }
        }
        if ($hiddenModels -gt 0) { $lines.Add(@((P ('weitere Modelle: ' + $hiddenModels) 'DarkGray'))) }
    }
    Add-Head ([string][char]0x25A3 + ' SYSTEM')
    $lines.Add(@((P ($(if (($d.totGB - $d.usedGB) -lt 2) { [string][char]0x25B2 + ' ' } else { '' }) + ('RAM {0}% {1:N1}/{2:N0} GB' -f $d.ramPct, $d.usedGB, $d.totGB)) (Level-Color $d.ramPct)), (P ('  CPU {0}%' -f [int]$d.cpu) (Level-Color $d.cpu)), (P ('  ' + [char]0x2318 + ' ' + $d.bld) 'Gray')))
    if ($d.bj) { $lines.Add(@((P $d.bj 'Gray'))) }
    Add-Head ([string][char]0x2630 + ' PLAENE')
    $pl = @(); foreach ($b in $d.badges) { $pl += (P ('{0} {1}  ' -f $b[0], [int]$d.counts[$b[0]]) $b[1]) }
    if ($d.counts['DRAFT'] -gt 0) { $pl += (P ('(' + $cfg.owner + ': ' + ($d.draftNames -join ', ') + ')') 'DarkYellow') }
    $lines.Add($pl)
    if ($d.qs.Count -eq 0) { Add-Head '? FRAGEN'; $lines.Add(@(P 'keine offenen' 'DarkGray')) }
    else { Add-Head '! FRAGEN' 'Red'; $lines.Add(@(P ('offen: ' + $d.qs.Count + '  ' + (($d.qs | ForEach-Object { $_.id }) -join ', ')) 'Yellow')) }
    return $lines
}

$usageAt = [datetime]::MinValue; $cu = $null; $xu = $null; $counts = @{}; $draftNames = @()
while ($true) {
    try {
        if (((Get-Date) - $usageAt).TotalSeconds -ge 60) {
            $usageAt = Get-Date; $cu = Get-ClaudeUsage $cfg; $xu = Get-CodexUsage
            # VERBRAUCH data (all Claude Code and Codex sessions of this PC); incremental with a cache, so the once-a-minute call stays cheap.
            $usage = $null
            if (Get-Command Get-UsageTable -ErrorAction SilentlyContinue) { try { $usage = Get-UsageTable -PythonExe ([string]$cfg.pythonExe) -CacheDir (Join-Path $cfg.runtime 'usage-cache') -AgentsFile (Join-Path $cfg.runtime 'AGENTS.json') -ArchiveFile (Join-Path $cfg.runtime 'AGENTS-ARCHIVE.json') -BrainFile (Join-Path $cfg.runtime 'BRAIN-USAGE.jsonl') } catch { $usage = $null } }
            $counts = @{ DRAFT = 0; READY = 0; IN_PROGRESS = 0; DONE = 0; PAUSED = 0 }; $draftNames = @()
            foreach ($pf in (Get-PlanFiles $cfg.projectsRoot)) { $s = Get-PlanStatus $pf.FullName; if ($counts.ContainsKey($s)) { $counts[$s]++ } else { $counts[$s] = 1 }; if ($s -eq 'DRAFT') { $draftNames += ($pf.BaseName -replace '-?PLAN$', '') } }
        }
        $w = if ($env:ORCH_DASH_WIDTH) { [int]$env:ORCH_DASH_WIDTH } else { [Math]::Max(20, $Host.UI.RawUI.WindowSize.Width - 1) }   # ORCH_DASH_WIDTH only for tests
        $h = if ($env:ORCH_DASH_HEIGHT) { [int]$env:ORCH_DASH_HEIGHT } else { [Math]::Max(5, $Host.UI.RawUI.WindowSize.Height - 1) }   # ORCH_DASH_HEIGHT only for tests
        $allAgents = @(Read-Agents $cfg)
        $agents = @($allAgents | Where-Object { $_.status -eq 'running' -or $_.status -eq 'starting' } | Sort-Object { [string]$_.startedAt })
        $fin = @($allAgents | Where-Object { $_.status -ne 'running' -and $_.status -ne 'starting' -and $_.endedAt -and ((Get-Date) - [datetime]$_.endedAt).TotalHours -le 1 } | Sort-Object { [datetime]$_.endedAt } -Descending)   # newest first
        $os = Get-CimInstance Win32_OperatingSystem
        $totGB = $os.TotalVisibleMemorySize / 1MB; $usedGB = $totGB - $os.FreePhysicalMemory / 1MB
        $bn = @(Get-BuildProcessNames)
        $rl = Join-Path $cfg.runtime 'ROUNDS.log'
        $bt = Get-BrainTotals $cfg; $os2 = Read-OrchState $cfg
        $orch = @{ modelShort = (Get-ModelShort 'claude' ([string]$cfg.brainModel) ([string]$cfg.brainEffort)); model =$(if ($bt.Last -and $bt.Last.model) { [string]$bt.Last.model } else { Get-ModelLabel $cfg.brainModel }); effort = [string]$cfg.brainEffort; calls = $bt.Calls; ctx = $(if ($bt.LastCall) { $bt.LastCall.context } else { $null }); lastIn = $(if ($bt.LastCall) { $bt.LastCall.input } else { $null }); lastOut = $(if ($bt.LastCall) { $bt.LastCall.output } else { $null }); todayIn = $bt.Input; todayOut = $bt.Output; status = $(if ($os2 -and $os2.status -eq 'brain') { 'Gehirnaufruf l' + [char]0xE4 + 'uft' } elseif ($os2 -and $os2.since) { 'idle seit ' + ([datetime]$os2.since).ToString('HH:mm') } else { 'idle' }) }
        $today = @{ cIn = [int64]0; cOut = [int64]0; xIn = [int64]0; xOut = [int64]0; noData = 0 }
        foreach ($ta in @(Read-AllAgents $cfg | Where-Object { $_.status -eq 'running' -or $_.status -eq 'starting' -or ($_.startedAt -and [datetime]$_.startedAt -ge (Get-Date).Date) })) {
            if ($null -eq $ta.tokensIn -or $null -eq $ta.tokensOut) { $today.noData++; continue }
            if ($ta.type -eq 'codex') { $today.xIn += [int64]$ta.tokensIn; $today.xOut += [int64]$ta.tokensOut } else { $today.cIn += [int64]$ta.tokensIn; $today.cOut += [int64]$ta.tokensOut }
        }
        if ($bt.Input) { $today.cIn += [int64]$bt.Input }; if ($bt.Output) { $today.cOut += [int64]$bt.Output }
        # Total of all time: every agent ever recorded (AGENTS.json + archive, without pilot and test runs) plus every brain call.
        $total = @{ cIn = [int64]0; cOut = [int64]0; xIn = [int64]0; xOut = [int64]0; noData = 0; plans = @{} }
        foreach ($ta in @(Read-AllAgents $cfg | Where-Object { [string]$_.plan -notmatch [regex]::Escape([IO.Path]::GetFileNameWithoutExtension([string]$cfg.pilotPlan)) -and [string]$_.name -notmatch '^ZZTEST' })) {
            if ([string]$ta.plan -match '[^\\/]+$' -and $ta.phase -ne 'integrate') { $total.plans[$Matches[0]] = 1 }
            if ($null -eq $ta.tokensIn -or $null -eq $ta.tokensOut) { $total.noData++; continue }
            if ($ta.type -eq 'codex') { $total.xIn += [int64]$ta.tokensIn; $total.xOut += [int64]$ta.tokensOut } else { $total.cIn += [int64]$ta.tokensIn; $total.cOut += [int64]$ta.tokensOut }
        }
        foreach ($br in @(Read-BrainRows $cfg | Where-Object { -not $_.error })) { if ($null -ne $br.input) { $total.cIn += [int64]$br.input }; if ($null -ne $br.output) { $total.cOut += [int64]$br.output } }
        $d = @{
            usage = $usage; orch = $orch; agents = $agents; fin = $fin; blocked = @(Get-BlockedTasks $allAgents); cu = $cu; xu = $xu; counts = $counts; draftNames = $draftNames
            qs = @(Get-Questions $cfg | Where-Object { $_.status -eq 'OPEN' })
            badges = @(@('DRAFT', 'Gray'), @('READY', 'Cyan'), @('IN_PROGRESS', 'Yellow'), @('DONE', 'Green'), @('PAUSED', 'DarkGray'))
            totGB = $totGB; usedGB = $usedGB; ramPct = [int][Math]::Round(100 * $usedGB / $totGB)
            cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
            bj = $(Get-BuildJobsLine $cfg @($allAgents))
            bld = $(if ($bn.Count) { 'Build: ja (' + ($bn -join ', ') + ')' } else { 'Build: nein' })
            now = (Get-Date -Format 'HH:mm:ss'); lastRound = $(if (Test-Path $rl) { (Get-Item $rl).LastWriteTime.ToString('HH:mm:ss') } else { '-' })
        }
        # First detail level whose line count fits the height: bars drop first, then separators, then the headings. Never more lines than the height (no scrolling); the sections below the agents are clipped last.
        # No section is ever hidden: when the height is short, the list of ended agents shrinks first (newest stay), then bars and the limit lines get more compact.
        $lines = $null; $fits = $false
        foreach ($lv in 0, 1, 2, 3) {
            for ($nf = $fin.Count; $nf -ge 0; $nf--) { $lines = Get-Physical (Build-Lines $lv $d $w $nf) $w; if ($lines.Count -le $h) { $fits = $true; break } }
            if ($fits) { break }
        }
        if (-not $env:ORCH_DASH_WIDTH) { Clear-Host }   # ORCH_DASH_WIDTH only for tests (redirected output has no cursor)
        $n = 0
        foreach ($ln in $lines) {
            if ($n -ge $h) { break }
            foreach ($p in $ln) {
                if ($p.c -match '^[0-9A-Fa-f]{6}$') { Write-Host ([char]27 + ('[1;38;2;{0};{1};{2}m' -f [Convert]::ToInt32($p.c.Substring(0, 2), 16), [Convert]::ToInt32($p.c.Substring(2, 4).Substring(0, 2), 16), [Convert]::ToInt32($p.c.Substring(4, 2), 16)) + $p.t + [char]27 + '[0m') -NoNewline }   # agent colors are RGB hex
                else { Write-Host $p.t -NoNewline -ForegroundColor $p.c }
            }
            Write-Host ''; $n++
        }
    }
    catch { Write-Host ('dashboard error: ' + $_.Exception.Message) -ForegroundColor Red }
    Start-Sleep -Seconds 5
}
