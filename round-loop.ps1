# Round loop and orchestrator pane: runs round.ps1 every roundMinutes until the STOP flag exists; then waits for running agents and ends.
# The pane has the same layout as the agent panes: a fixed header (never scrolls away) and below it exactly one line per event (time, what was done; errors red).
# Raw output of round.ps1 (prompts, JSON, debug) is discarded here; it lives only in ROUNDS.log and runs\.
param([string]$Project = '', [string]$PlanFilesCsv = '', [int]$RoundMinutes = 0, [switch]$DryRun, [string]$BrainAnswerFile = '')   # DryRun and BrainAnswerFile only for tests
if ($Project) { $env:ORCH_PROJECT = $Project }
. (Join-Path $PSScriptRoot 'lib.ps1')
$cfg = Get-Cfg
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$stop = Join-Path $cfg.runtime 'STOP'
$rm = if ($RoundMinutes -gt 0) { $RoundMinutes } else { $cfg.roundMinutes }
$ESC = [char]27
$ui = @{ busy = $false; since = (Get-Date); nextAt = $null; headAt = [datetime]::MinValue; rows = 0; evPos = 0 }
# Only one loop at a time: when another round-loop process is alive (by lock file or by process list), this one ends at once and touches nothing (running agents are never affected).
$otherLoops = @(Get-CimInstance Win32_Process -Filter "Name='pwsh.exe'" | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match ('round-loop\.ps1.*' + [regex]::Escape([string]$env:ORCH_PROJECT)) })
if ($otherLoops.Count -gt 0) { Write-Host ('Another orchestrator loop runs (pid ' + (($otherLoops | ForEach-Object { $_.ProcessId }) -join ', ') + '): this one ends.') -ForegroundColor Yellow; Start-Sleep -Seconds 5; exit 0 }
Set-Content -Path (Join-Path $cfg.runtime 'loop.lock') -Value $PID
$evFile = [string](Get-OrchLogPaths $cfg).events   # text mirror of the orchestrator events (lines HH:MM|O or E|text)
if (Test-Path $evFile) { $ui.evPos = @(Get-Content $evFile -Encoding UTF8).Count }

# ANSI color code of a color name.
function Sgr([string]$c) { $n = switch ($c) { 'Cyan' { '1;36' } 'Gray' { '90' } 'White' { '97' } 'Green' { '32' } 'Red' { '31' } 'Yellow' { '33' } default { '0' } }; return "$ESC[${n}m" }
# Pane width in columns.
function Get-Width { return [Math]::Max(30, $Host.UI.RawUI.WindowSize.Width) }
# Word-wraps a text to the width $w (long words are split); returns the lines.
function Wrap-Text([string]$s, [int]$w) {
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($para in ($s -split "`r?`n")) {
        $cur = ''
        foreach ($word in ($para -split ' ')) {
            while ($word.Length -gt $w) { if ($cur) { $out.Add($cur); $cur = '' }; $out.Add($word.Substring(0, $w)); $word = $word.Substring($w) }
            if ($cur.Length -eq 0) { $cur = $word } elseif (($cur.Length + 1 + $word.Length) -le $w) { $cur += ' ' + $word } else { $out.Add($cur); $cur = $word }
        }
        $out.Add($cur)
    }
    return $out
}
# Wraps a list of text parts to the width $w; a break only happens between two parts.
function Wrap-Parts-Text($parts, [int]$w) {
    $out = New-Object System.Collections.Generic.List[string]
    $cur = ''
    foreach ($p in $parts) {
        if ($cur.Length -gt 0 -and ($cur.Length + $p.Length) -gt $w) { $out.Add($cur); $cur = '' }
        if ($p.Length -gt $w) { foreach ($l in (Wrap-Text $p.Trim() $w)) { $out.Add($l) }; continue }
        $cur += $p
    }
    if ($cur.Length -gt 0) { $out.Add($cur) }
    return $out
}
# Builds the fixed header lines from the brain usage and the loop state.
function Head-Lines([int]$w, $tot) {
    $model = if ($tot.Last -and $tot.Last.model) { [string]$tot.Last.model } else { Get-ModelLabel $cfg.brainModel }
    $res = New-Object System.Collections.Generic.List[object]
    # All CHIEF information stands here (it is no longer an entry of the dashboard's agent list). Four lines; a wrapped line never starts with "|".
    $os = Read-OrchState $cfg
    $brain = ($os -and $os.status -eq 'brain')
    $stopSet = Test-Path $stop
    $state = if ($stopSet) { [string][char]0x2016 + ' STOP gesetzt, keine neuen Agents' } elseif ($brain) { 'Gehirnaufruf läuft' } elseif ($ui.busy) { 'Runde läuft' } elseif ($ui.nextAt) { 'idle seit ' + $ui.since.ToString('HH:mm') + ', ' + [char]0x25D4 + ' nächste Runde ' + $ui.nextAt.ToString('HH:mm') } else { 'startet' }
    foreach ($l in (Wrap-Text ([string][char]0x25C6 + " CHIEF | ORCHESTRATOR | Claude " + (Get-ModelShort 'claude' ([string]$cfg.brainModel) ([string]$cfg.brainEffort)) + " | $state") $w)) { $res.Add(@{ t = $l; c = 'White' }) }
    $res.Add(@{ t = 'Entscheidet nur bei Ereignissen, die Routine läuft'; c = 'Gray' })
    $lc = $tot.LastCall
    $last = if ($lc) { @("Letzter Aufruf: Kontext $(Format-Tokens $lc.context) | ", "Total Input $(Format-Tokens $lc.input) / Output $(Format-Tokens $lc.output)") } else { @('Letzter Aufruf: noch keiner') }
    foreach ($l in (Wrap-Parts-Text $last $w)) { $res.Add(@{ t = $l.TrimEnd(' ', '|'); c = 'Gray' }) }
    foreach ($l in (Wrap-Parts-Text @("Heute: Total Input $(Format-Tokens $tot.Input) / Output $(Format-Tokens $tot.Output) | ", "Gehirnaufrufe heute $($tot.Calls)") $w)) { $res.Add(@{ t = $l.TrimEnd(' ', '|'); c = 'Gray' }) }
    return $res
}
# Redraws the header in the top rows and keeps the scroll region below it; the cursor returns to where it was. The height is reserved for the worst case, so it never grows.
function Draw-Head {
    $w = Get-Width; $h = [Math]::Max(10, $Host.UI.RawUI.WindowSize.Height)
    $hl = @(Head-Lines $w (Get-BrainTotals $cfg))
    if ($ui.rows -eq 0) {
        $big = [pscustomobject]@{ Calls = 99; Input = 99999999; Output = 9999999; Last = [pscustomobject]@{ model = 'Sonnet 5.5' }; LastCall = [pscustomobject]@{ context = 999999; input = 99999999; output = 9999999 } }
        $ui.rows = @(Head-Lines $w $big).Count
    }
    while ($hl.Count -lt $ui.rows) { $hl += @{ t = ''; c = 'White' } }
    $n = $hl.Count
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append("${ESC}7")
    [void]$sb.Append("$ESC[$($n + 1);${h}r")
    for ($i = 0; $i -lt $n; $i++) { [void]$sb.Append("$ESC[$($i + 1);1H$ESC[2K" + (Sgr $hl[$i].c) + $hl[$i].t + "$ESC[0m") }
    [void]$sb.Append("${ESC}8")
    [Console]::Write($sb.ToString())
    $ui.headAt = Get-Date
    return $n
}
# Redraws the header at most every 5 seconds.
function Maybe-Head { if (((Get-Date) - $ui.headAt).TotalSeconds -ge 5) { [void](Draw-Head) } }
# Prints the new event lines of ORCH-EVENTS.log: one line each (time, text), wrapped instead of cut, errors red.
function Show-NewEvents {
    if (-not (Test-Path $evFile)) { return }
    $all = @(Get-Content $evFile -Encoding UTF8)
    for ($i = $ui.evPos; $i -lt $all.Count; $i++) {
        $p = $all[$i].Split('|', 3)
        if ($p.Count -lt 3) { continue }
        $w = Get-Width; $first = $true
        foreach ($l in (Wrap-Text $p[2] ($w - 7))) { Write-Host ($(if ($first) { $p[0] + '  ' } else { '       ' }) + $l) -ForegroundColor $(if ($p[1] -eq 'E') { 'Red' } else { 'White' }); $first = $false }
    }
    $ui.evPos = $all.Count
}
# Writes the loop state for the dashboard (idle since / next round).
function Save-State([bool]$Busy) {
    $ui.busy = $Busy; if (-not $Busy) { $ui.since = Get-Date }
    Write-OrchState $cfg @{ status = $(if ($Busy) { 'round' } else { 'idle' }); since = $ui.since.ToString('o'); nextAt = $(if ($ui.nextAt) { $ui.nextAt.ToString('o') } else { $null }) }
}

[Console]::Write("$ESC[2J$ESC[H")
$headRows = Draw-Head
[Console]::Write("$ESC[$($headRows + 1);1H")
Write-RoundLog $cfg "loop started (roundMinutes=$rm)"
Write-OrchEvent $cfg ("Orchestrator gestartet (Runde alle $rm Minuten)")
$round = @{}
if ($PlanFilesCsv) { $round['PlanFiles'] = ($PlanFilesCsv -split ',') }
if ($DryRun) { $round['DryRun'] = $true }
if ($BrainAnswerFile) { $round['BrainAnswerFile'] = $BrainAnswerFile }
while ($true) {
    if (Test-Path $stop) { break }
    Save-State $true; [void](Draw-Head)
    try { & (Join-Path $PSScriptRoot 'round.ps1') @round *> $null }
    catch { Write-RoundLog $cfg ('ROUND ERROR ' + $_.Exception.Message); Write-OrchEvent $cfg ('Rundenfehler: ' + $_.Exception.Message) $true }
    $ui.nextAt = (Get-Date).AddMinutes($rm)
    Save-State $false; Show-NewEvents; [void](Draw-Head)
    while ((Get-Date) -lt $ui.nextAt -and -not (Test-Path $stop)) { Start-Sleep -Seconds 2; Show-NewEvents; Maybe-Head }
}
Write-RoundLog $cfg 'STOP found, loop ending'
Write-OrchEvent $cfg 'STOP-Datei gefunden: keine neuen Agenten, warte auf laufende Agenten'
$ui.nextAt = $null; Save-State $false; Show-NewEvents
while (@(Get-OrchAgents $cfg | Where-Object { $_.status -eq 'running' -or $_.status -eq 'starting' }).Count -gt 0) { Start-Sleep -Seconds 5; Show-NewEvents; Maybe-Head }
Write-RoundLog $cfg 'loop ended'
Write-OrchEvent $cfg 'Schleife beendet: alle Agenten fertig'
Show-NewEvents; [void](Draw-Head)
Remove-Item (Join-Path $cfg.runtime 'loop.lock') -ErrorAction SilentlyContinue
