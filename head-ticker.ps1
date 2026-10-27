# Header of one agent pane (PowerShell 7). Started by agent-run.ps1, shares the console of the pane and redraws the header every second from the agent's row in AGENTS.json:
#   line 1 "<NAME> | <Agent-ID> | <Type> <model short> | <status> <runtime>" in the agent's own color (status word in its status color)
#   line 2 the task in plain words (one line), line 3 the tokens ("Tokens: noch keine Daten" before the first token data).
# When the pane width or height changes, the whole screen is cleared first and the milestones are replayed, so no old header line stays behind.
param([Parameter(Mandatory)][string]$Name, [int]$ParentPid = 0, [string]$Project = '')
if ($Project) { $env:ORCH_PROJECT = $Project }
. (Join-Path $PSScriptRoot 'lib.ps1')
$ErrorActionPreference = 'Continue'
$cfg = Get-Cfg
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ESC = [char]27
$msFile = Join-Path $cfg.runtime "runs\$Name\milestones.txt"

# ANSI sequence for a color: 6 hex digits = true color, else a status color name.
function Sgr([string]$c) {
    if ($c -match '^[0-9A-Fa-f]{6}$') { return ("$ESC[1;38;2;{0};{1};{2}m" -f [Convert]::ToInt32($c.Substring(0, 2), 16), [Convert]::ToInt32($c.Substring(2, 2), 16), [Convert]::ToInt32($c.Substring(4, 2), 16)) }
    $n = switch ($c) { 'Yellow' { '33' } 'Green' { '32' } 'Red' { '31' } 'Gray' { '90' } 'White' { '97' } 'Cyan' { '36' } 'DarkGray' { '90' } 'Magenta' { '95' } 'Blue' { '94' } default { '0' } }
    return "$ESC[${n}m"
}
# Word-wraps a text to the width $w (a word longer than the width is split); returns the lines.
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
# Runtime text: mm:ss, or h:mm:ss from one hour on.
function Fmt-Run([timespan]$d) { if ($d.TotalHours -ge 1) { return ('{0:h\:mm\:ss}' -f $d) }; return ('{0:mm\:ss}' -f $d) }
# Builds the header lines for the width $w: a list of lines, each a list of @(text, color).
function Build-Head($row, [int]$w) {
    $res = New-Object System.Collections.Generic.List[object]
    $person = if ($row.person) { ([string]$row.person).ToUpper() } else { '-' }
    $col = if ($row.color) { [string]$row.color } else { 'FFFFFF' }
    $typ = if ($row.type -eq 'codex') { 'Codex' } else { 'Claude' }
    $st = [string]$row.status
    $start = if ($row.startedAt) { [datetime]$row.startedAt } else { Get-Date }
    $end = if ($row.endedAt) { [datetime]$row.endedAt } else { Get-Date }
    $run = Fmt-Run ($end - $start)
    $stCol = switch ($st) { 'done' { 'Green' } 'failed' { 'Red' } default { 'Yellow' } }
    $left = "$person | $Name | $typ $($row.modelShort) | "
    $right = "$st $run"
    if (($left.Length + $right.Length) -le $w) { $res.Add(@(@($left, $col), @($right, $stCol))) }
    else { foreach ($l in (Wrap-Text $left.TrimEnd(' ', '|') $w)) { $res.Add(@(, @($l, $col))) }; $res.Add(@(, @($right, $stCol))) }
    $task = ([string]$row.task -replace '\s+', ' ').Trim()
    if ($task.Length -gt $w) { $task = $task.Substring(0, [Math]::Max(0, $w - 3)) + '...' }
    $res.Add(@(, @($task, 'Gray')))
    $tl = Format-TokenLine $row.ctxTokens $row.tokensIn $row.tokensOut
    foreach ($l in (Wrap-Text $tl $w)) { $res.Add(@(, @($l, 'White'))) }
    return $res
}
# One header line as an escape string.
function Line-Str($parts) { $s = ''; foreach ($p in $parts) { $s += (Sgr $p[1]) + $p[0] + "$ESC[0m" }; return $s }

$lastW = -1; $lastH = -1; $lastN = -1; $endSeen = 0
while ($true) {
    try {
        $row = @(Read-Agents $cfg | Where-Object { $_.name -eq $Name } | Select-Object -First 1)
        if ($row.Count) {
            $r = $row[0]
            $w = [Math]::Max(30, [Console]::WindowWidth); $h = [Math]::Max(10, [Console]::WindowHeight)
            $lines = Build-Head $r $w
            $n = $lines.Count
            $sb = New-Object System.Text.StringBuilder
            if ($w -ne $lastW -or $h -ne $lastH -or $n -ne $lastN) {
                # full redraw: clear everything (also old header remnants), set the scroll region below the header, replay the milestones that fit
                [void]$sb.Append("$ESC[r$ESC[2J$ESC[H")
                for ($i = 0; $i -lt $n; $i++) { [void]$sb.Append("$ESC[$($i + 1);1H" + (Line-Str $lines[$i])) }
                [void]$sb.Append("$ESC[$($n + 1);${h}r")
                $room = $h - $n
                $rep = New-Object System.Collections.Generic.List[object]
                if (Test-Path $msFile) {
                    foreach ($ml in (Get-Content $msFile -Tail 300 -Encoding UTF8)) {
                        $i1 = $ml.IndexOf("`t"); if ($i1 -lt 0) { continue }
                        $first = $true
                        foreach ($wl in (Wrap-Text $ml.Substring($i1 + 1) ($w - 6))) { $rep.Add(@(($(if ($first) { '' } else { '      ' }) + $wl), $ml.Substring(0, $i1))); $first = $false }
                    }
                }
                $skip = [Math]::Max(0, $rep.Count - ($room - 1))
                [void]$sb.Append("$ESC[$($n + 1);1H")
                for ($i = $skip; $i -lt $rep.Count; $i++) { [void]$sb.Append((Sgr $rep[$i][1]) + $rep[$i][0] + "$ESC[0m`r`n") }
                $lastW = $w; $lastH = $h; $lastN = $n
            }
            else {
                [void]$sb.Append("${ESC}7")
                for ($i = 0; $i -lt $n; $i++) { [void]$sb.Append("$ESC[$($i + 1);1H$ESC[2K" + (Line-Str $lines[$i])) }
                [void]$sb.Append("${ESC}8")
            }
            [Console]::Write($sb.ToString())
            if ($r.endedAt -and $ParentPid -gt 0 -and -not (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) { $endSeen++; if ($endSeen -ge 2) { break } }
        }
    } catch {}
    Start-Sleep -Seconds 1
}
