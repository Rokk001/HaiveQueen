# Runs ONE agent (Claude Code or Codex, headless) inside its own Windows Terminal pane, tracks it in AGENTS.json and ends.
# The pane shows a fixed header (redrawn on every update, never scrolls away) and below it only milestones with a time stamp; the full raw output stays in runs\<Name>\.
param(
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][ValidateSet('claude', 'codex')][string]$Type,
    [string]$Plan = '', [string]$Package = '', [string]$Phase = 'work',
    [Parameter(Mandatory)][string]$Cwd,
    [Parameter(Mandatory)][string]$PromptFile,
    [string]$Project = ''
)
if ($Project) { $env:ORCH_PROJECT = $Project }
. (Join-Path $PSScriptRoot 'lib.ps1')
$cfg = Get-Cfg
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$queueName = Split-Path ([string]$cfg.integrationStateFile) -Leaf   # a command that writes into the integration queue file is shown as the ready line
$queueNameRe = [regex]::Escape([IO.Path]::GetFileNameWithoutExtension($queueName))
$runDir = Join-Path $cfg.runtime "runs\$Name"
New-Item -ItemType Directory -Force $runDir | Out-Null
$events = Join-Path $runDir 'events.jsonl'
$errFile = Join-Path $runDir 'stderr.txt'
$finalFile = Join-Path $runDir 'final.txt'
$started = Get-Date
$ESC = [char]27
$DASH = [string][char]0x2013
$state = @{ thread = ''; tin = 0; tout = 0; ctx = 0; seen = @{}; last = ''; final = ''; ok = $false; lastWrite = [datetime]::MinValue; headAt = [datetime]::MinValue; model = ''; ended = $null; pending = @{}; lastSaid = ''; rows = 0 }
if ($Type -eq 'codex') { $state.tin = $null; $state.tout = $null }   # Codex reports its token totals only in the session log; until then the pane and the dashboard show a dash

# Shortens a text to one line for pane and dashboard.
function Short([string]$s, [int]$max = 180) { $s = ($s -replace '\s+', ' ').Trim(); if ($s.Length -gt $max) { $s = $s.Substring(0, $max) + '...' }; return $s }
# Whole-number token count for the header (75k, 2k, 1.2M); a dash when unknown.
function Fmt-Tok($n) { if ($null -eq $n) { return $DASH }; if ($n -ge 1000000) { return ('{0:N1}M' -f ($n / 1e6)) }; if ($n -ge 1000) { return ('{0:N0}k' -f ($n / 1e3)) }; return "$n" }
# Readable model name: claude-sonnet-5-5 -> Sonnet 5.5, gpt-6.1-sol -> GPT-6.1-Sol.
function Model-Label([string]$m) {
    if ($m -match '^claude-([a-z]+)-(\d+)-(\d+)') { return (Get-Culture).TextInfo.ToTitleCase($Matches[1]) + ' ' + $Matches[2] + '.' + $Matches[3] }
    $parts = $m -split '-'; $out = @()
    for ($i = 0; $i -lt $parts.Count; $i++) { $x = $parts[$i]; if ($i -eq 0) { $out += $x.ToUpper() } elseif ($x -match '^\d') { $out += $x } else { $out += (Get-Culture).TextInfo.ToTitleCase($x) } }
    return ($out -join '-')
}
# The task in one line: the first line after the TASK heading of the prompt file (else its first line).
function Get-TaskLine {
    if (-not (Test-Path $PromptFile)) { return '' }
    $pl = @(Get-Content $PromptFile -Encoding UTF8 | ForEach-Object { $_.TrimStart([char]0xFEFF) })
    $ti = [array]::IndexOf($pl, 'TASK')
    $cand = @(if ($ti -ge 0) { $pl | Select-Object -Skip ($ti + 1) | Where-Object { $_.Trim() } } else { $pl | Where-Object { $_.Trim() } })
    if ($cand.Count -eq 0) { return '' }
    return (Short $cand[0] 300)
}
$planShort = [System.IO.Path]::GetFileNameWithoutExtension($Plan) -replace '-?PLAN$', ''
$typeLabel = if ($Type -eq 'claude') { 'Claude' } else { 'Codex' }
$headColor = if ($Type -eq 'claude') { 'Magenta' } else { 'Blue' }
# Model and effort come from the class stored in the agent's row by round.ps1 (plan 12.2); no class = medium.
$myRow = @(Read-Agents $cfg | Where-Object { $_.name -eq $Name } | Select-Object -First 1)
$provName = if ($myRow.Count -and $myRow[0].provider) { [string]$myRow[0].provider } else { $Type }   # the provider entry of the project file (plan section 15); rows from before it use the type as the name
$prov = Get-Provider $cfg $provName
if (-not $prov) { throw "provider '$provName' is not in the project file" }
$classModel = Get-ClassModel $cfg $provName $(if ($myRow.Count -and $myRow[0].class) { [string]$myRow[0].class } else { 'medium' })
$modelCfg = $classModel.Model
$effortCfg = $classModel.Effort
$state.model = Model-Label $modelCfg
# The task for the header: work = package row of the plan's package table (package id, branch suffix, content); verify / release / integrate = phase plus package. Falls back to the first line of the prompt.
function Get-TaskText {
    $pkgTxt = if ($Package -and $Package -ne 'ALL' -and $Package -notmatch '^(REL|INT-)') { "Paket $Package" } else { 'ganzer Plan' }
    switch ($Phase) {
        'verify' { return "Verify $pkgTxt, Vollst" + [char]0xE4 + "ndigkeitspr" + [char]0xFC + "fung" }
        'release' { return "Release: Plan-Branch mergen, Kompiliertest, ready-Zeile ($planShort)" }
        'integrate' { return "Integrator: ready-Zeilen mergen, Build, Checks, Push" }
    }
    try {
        $abs =if ([System.IO.Path]::IsPathRooted($Plan)) { $Plan } else { Join-Path $cfg.repo $Plan }
        if (Test-Path $abs) {
            $hdr = $null; $cont = -1; $suf = -1
            foreach ($l in (Get-Content $abs -Encoding UTF8)) {
                if ($l -notmatch '^\s*\|') { $hdr = $null; continue }
                $c = @($l.Trim().Trim('|').Split('|') | ForEach-Object { $_.Trim() })
                if (-not $hdr) { if ($c[0] -match '^(Package|Paket)$') { $hdr = $c; $cont = -1; $suf = -1; for ($i = 0; $i -lt $c.Count; $i++) { if ($c[$i] -match '^(Content|Inhalt)') { $cont = $i }; if ($c[$i] -match '^Branch') { $suf = $i } } }; continue }
                if ($c[0] -eq $Package -and $cont -ge 0 -and $cont -lt $c.Count) { return ($Package + $(if ($suf -ge 0 -and $suf -lt $c.Count) { ' (' + $c[$suf] + ')' } else { '' }) + ': ' + $c[$cont]) }
            }
        }
    } catch {}
    return ''
}
$taskLine = Get-TaskText
if (-not $taskLine) { $taskLine = Get-TaskLine }

# ANSI color code of a color name.
function Sgr([string]$c) { $n = switch ($c) { 'Magenta' { '1;35' } 'Blue' { '1;94' } 'Cyan' { '36' } 'Gray' { '90' } 'DarkGray' { '90' } 'White' { '97' } 'Green' { '32' } 'Red' { '31' } 'Yellow' { '33' } default { '0' } }; return "$ESC[${n}m" }
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
# Wraps a list of text parts to the width $w; a break only happens between two parts (a part longer than the width is word-wrapped).
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
# Builds the fixed header lines (text and color) for the width $w.
function Head-Lines([int]$w) {
    $e = if ($state.ended) { $state.ended } else { Get-Date }
    $d = $e - $started
    $rt = if ($d.TotalHours -ge 1) { '{0:h\:mm\:ss}' -f $d } else { '{0:mm\:ss}' -f $d }
    $res = New-Object System.Collections.Generic.List[object]
    foreach ($l in (Wrap-Text "== $Name | $typeLabel | $($state.model) $effortCfg | $planShort/$Package/$Phase ==" $w)) { $res.Add(@{ t = $l; c = $headColor }) }
    # The task takes at most two lines (a longer text ends with "...").
    $tw = @(Wrap-Text ('Auftrag: ' + $taskLine) $w)
    if ($tw.Count -gt 2) { $tw = @($tw[0], ($tw[1].Substring(0, [Math]::Max(0, [Math]::Min($tw[1].Length, $w - 3))) + '...')) }
    foreach ($l in $tw) { $res.Add(@{ t = $l; c = 'Gray' }) }
    $tail = if ($state.ended) { '  (beendet)' } else { '' }
    foreach ($l in (Wrap-Parts-Text @("Laufzeit $rt   Kontext $(Fmt-Tok $state.ctx)", " | Total Input $(Fmt-Tok $state.tin)", " / Output $(Fmt-Tok $state.tout)$tail") $w)) { $res.Add(@{ t = $l; c = 'White' }) }
    return $res
}
# Redraws the header in the top rows and keeps the scroll region below it; the cursor returns to where it was. Returns the number of header rows.
function Draw-Head {
    $w = Get-Width; $h = [Math]::Max(10, $Host.UI.RawUI.WindowSize.Height)
    $hl = @(Head-Lines $w)
    while ($hl.Count -lt $state.rows) { $hl += @{ t = ''; c = 'White' } }   # the header never shrinks, so no stale rows remain
    $n = $hl.Count
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append("${ESC}7")
    if ($state.rows -gt 0 -and $n -gt $state.rows) {   # the header grows: push the body down so no milestone line is overwritten
        $k = $n - $state.rows
        [void]$sb.Append("$ESC[$($state.rows + 1);${h}r$ESC[$($state.rows + 1);1H$ESC[${k}L${ESC}8$ESC[${k}B${ESC}7")
    }
    $state.rows = $n
    [void]$sb.Append("$ESC[$($n + 1);${h}r")
    for ($i = 0; $i -lt $n; $i++) { [void]$sb.Append("$ESC[$($i + 1);1H$ESC[2K" + (Sgr $hl[$i].c) + $hl[$i].t + "$ESC[0m") }
    [void]$sb.Append("${ESC}8")
    [Console]::Write($sb.ToString())
    $state.headAt = Get-Date
    return $n
}
# Redraws the header at most once per second.
function Maybe-Head { if (((Get-Date) - $state.headAt).TotalSeconds -ge 1) { [void](Draw-Head) } }
# Writes one milestone line with the time and wraps it instead of cutting it.
function Say([string]$text, [string]$color = 'White') {
    if ($text.Length -gt 1500) { $text = $text.Substring(0, 1500) + '...' }
    $w = Get-Width; $ts = (Get-Date -Format 'HH:mm') + ' '
    try { [System.IO.File]::AppendAllText((Join-Path $runDir 'milestones.txt'), ($color + "`t" + $ts + (($text -replace '\s+', ' ').Trim()) + "`n"), (New-Object System.Text.UTF8Encoding($false))) } catch {}   # kept so the header process can replay the lines after a resize
    $first = $true
    foreach ($l in (Wrap-Text $text ($w - 6))) { Write-Host ($(if ($first) { $ts } else { '      ' }) + $l) -ForegroundColor $color; $first = $false }
    $state.lastSaid = $text
    Maybe-Head
}
# Path relative to the working directory when it lies inside it.
function Rel-Path([string]$p) { if ($p.StartsWith($Cwd + '\', [System.StringComparison]::OrdinalIgnoreCase)) { return $p.Substring($Cwd.Length + 1) }; return $p }
# Recognizes a milestone command (commit, push, compile test, check, ready line, file write); returns @{ kind; label } or $null.
function Classify-Cmd([string]$c) {
    if ($c -match 'git\s+commit') {
        $m = ''; if ($c -match '-m\s+"([^"\r\n$]{3,})"') { $m = $Matches[1] } elseif ($c -match "-m\s+'([^'\r\n]{3,})'") { $m = $Matches[1] }
        return @{ kind = 'commit'; label = $(if ($m) { 'Commit: ' + (Short $m 100) } else { 'Commit' }) }
    }
    if ($c -match 'git\s+push') { return @{ kind = 'push'; label = 'Push' } }
    if ($c -match 'compile-check-changed') { return @{ kind = 'check'; label = 'Kompiliertest' } }
    if ($c -match '(check[\w\-]*\.(py|ps1))') { return @{ kind = 'check'; label = 'Check ' + $Matches[1] } }
    if ($c -match $queueNameRe -and $c -match '>>|Add-Content|Out-File|Set-Content') { return @{ kind = 'ready'; label = 'ready-Zeile eingetragen' } }
    if ($c -match '(Set-Content|Add-Content|Out-File|WriteAllText|sed\s+-i|apply_patch)') {
        if ($c -match "(?:-(?:Path|LiteralPath|FilePath)\s+|WriteAllText\(|sed\s+-i\S*\s+(?:'[^']*'\s+|`"[^`"]*`"\s+)?)['`"]?([^'`"\s,)]+\.[A-Za-z0-9]+)") { return @{ kind = 'edit'; label = 'geändert: ' + (Rel-Path ($Matches[1] -replace '\\\\', '\')) } }
    }
    return $null
}
# Writes the result line of a milestone command (exit code, last output line).
function Say-Result($info, [string]$text, $exit, [bool]$isErr) {
    $ex = $exit; if ($null -eq $ex -and $text -match 'EXIT=(\d+)') { $ex = [int]$Matches[1] }
    $lastLine = (($text -split "`r?`n") | Where-Object { $_.Trim() } | Select-Object -Last 1)
    $ok = if ($null -ne $ex) { ([int]$ex -eq 0) } else { -not $isErr }
    $label = $info.label
    if ($info.kind -eq 'commit' -and $text -match '\[[^\]]+ ([0-9a-f]{7,})\]') { $label += ' (' + $Matches[1] + ')' }
    $msg = $label + ' - ' + $(if ($null -ne $ex) { "Exit $ex" } elseif ($ok) { 'OK' } else { 'Fehler' }) + $(if ($lastLine) { ': ' + (Short $lastLine 140) } else { '' })
    Say $msg $(if ($ok) { 'Green' } else { 'Red' })
}
# Pushes the current counters and last line into AGENTS.json (throttled to every 2 s unless forced).
function Push-State([bool]$Force) {
    if (-not $Force -and ((Get-Date) - $state.lastWrite).TotalSeconds -lt 2) { return }
    $state.lastWrite = Get-Date
    if ($Type -eq 'codex' -and $state.thread) { $cx = Get-CodexTokens $state.thread; if ($cx) { $state.ctx = $cx.ctx; $state.tin = $cx.tin; $state.tout = $cx.tout } }
    Update-Agent $cfg $Name @{ tokensIn = $state.tin; tokensOut = $state.tout; ctxTokens = $state.ctx; lastLine = $state.last; paneCols = (Get-Width); paneRows = $Host.UI.RawUI.WindowSize.Height }   # pane size: the next pane is placed by it (Start-AgentPane)
}

$prev = @(Read-Agents $cfg | Where-Object { $_.name -eq $Name } | Select-Object -First 1)
if ($prev.Count -and $prev[0].pid -and [int]$prev[0].pid -ne $PID -and (Get-Process -Id ([int]$prev[0].pid) -ErrorAction SilentlyContinue)) { Write-Host "agent $Name already runs (pid $($prev[0].pid)), this second pane ends" -ForegroundColor Yellow; exit 0 }   # a relaunched pane must never start the task twice
if (-not (Update-AgentRetry $cfg $Name @{ type = $Type; plan = $Plan; package = $Package; phase = $Phase; cwd = $Cwd; status = 'running'; startedAt = $started.ToString('o'); endedAt = $null; pid = $PID; tokensIn = $state.tin; tokensOut = $state.tout; lastLine = 'starting'; paneCols = (Get-Width); paneRows = $Host.UI.RawUI.WindowSize.Height })) { Write-Host "agent $Name not started: the agents lock stayed busy, row set to failed" -ForegroundColor Red; exit 0 }   # the row is on 'failed' now, the model is never called
# The header is drawn by head-ticker.ps1 (own process in this pane, redraws every second from AGENTS.json, clears old header lines completely when the size changes); this script only prints milestones below it.
function Draw-Head { return 0 }
function Maybe-Head { }
$null = New-Item -ItemType File -Force (Join-Path $runDir 'milestones.txt')
Set-Content -Path (Join-Path $runDir 'milestones.txt') -Value @() -Encoding UTF8
[void](Start-Process -FilePath 'pwsh.exe' -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Quote-Arg (Join-Path $PSScriptRoot 'head-ticker.ps1')), '-Project', (Quote-Arg $env:ORCH_PROJECT), '-Name', (Quote-Arg $Name), '-ParentPid', "$PID") -NoNewWindow -PassThru)
Start-Sleep -Seconds 2

$prompt = "Read the file `"$PromptFile`" completely and carry out exactly the task described there. Do nothing else."
$prompt = $prompt.Replace('"', '')
$model = $modelCfg
$tmpl = @($prov.Args) + @(Get-EndpointArgs $prov)   # argument template of the provider entry (plus the model_provider arguments of an endpoint)
# S9 (plan 12.1): a Claude agent gets only the rule sections of its phase (file rules.md in its run folder, built from the sources named in config.json) instead of all CLAUDE.md files.
$rulesFile = Join-Path $runDir 'rules.md'
if ($Type -eq 'claude') { [void](Write-PhaseRules $cfg $Phase $rulesFile) }
$argList = @($tmpl | ForEach-Object { $_.Replace('{model}', $model).Replace('{effort}', $effortCfg).Replace('{repo}', $cfg.repo).Replace('{tools}', $cfg.tools).Replace('{cwd}', $Cwd).Replace('{claudeTools}', [string]$cfg.claudeTools).Replace('{rules}', $rulesFile) })

# Handles one parsed JSON event of either CLI.
function Handle-Event($e) {
    if ($Type -eq 'claude') {
        if ($e.type -eq 'assistant' -and $e.message) {
            if ($e.message.model) { $state.model = Model-Label ([string]$e.message.model) }
            $u = $e.message.usage
            # One API message is streamed once per content block with the same id and usage: count each message id once. ctx = size of the latest context.
            if ($u -and $e.message.id -and -not $state.seen.ContainsKey($e.message.id)) { $state.seen[$e.message.id] = 1; $c1 = [int64]($u.input_tokens + $u.cache_creation_input_tokens + $u.cache_read_input_tokens); $state.tin += $c1; $state.tout += [int64]$u.output_tokens; $state.ctx = $c1 }
            foreach ($c in @($e.message.content)) {
                if ($c.type -eq 'text' -and $c.text) { $state.last = Short $c.text; Say ([string]$c.text) 'White' }
                elseif ($c.type -eq 'tool_use') {
                    $state.last = 'tool: ' + $c.name
                    $inp = $c.input
                    if ($c.name -in 'Edit', 'Write', 'MultiEdit', 'NotebookEdit') {
                        $fp = [string]$inp.file_path; if (-not $fp) { $fp = [string]$inp.notebook_path }
                        if ($fp -match $queueNameRe) { Say ('ready-Zeile eingetragen (' + [IO.Path]::GetFileNameWithoutExtension($queueName) + ')') 'Green' } elseif ($fp) { Say ('geändert: ' + (Rel-Path $fp)) 'Cyan' }
                    }
                    elseif ($c.name -in 'Bash', 'PowerShell') {
                        $info = Classify-Cmd ([string]$inp.command)
                        if ($info) { if ($info.kind -eq 'edit') { Say $info.label 'Cyan' } else { $state.pending[[string]$c.id] = $info } }
                    }
                }
            }
        }
        elseif ($e.type -eq 'user' -and $e.message) {
            foreach ($c in @($e.message.content)) {
                if ($c.type -eq 'tool_result' -and $c.tool_use_id -and $state.pending.ContainsKey([string]$c.tool_use_id)) {
                    $info = $state.pending[[string]$c.tool_use_id]; $state.pending.Remove([string]$c.tool_use_id)
                    $txt = if ($c.content -is [string]) { [string]$c.content } else { (@($c.content) | ForEach-Object { [string]$_.text }) -join "`n" }
                    Say-Result $info $txt $null ([bool]$c.is_error)
                }
            }
        }
        elseif ($e.type -eq 'rate_limit_event') { Save-ClaudeUsage $cfg $e }
        elseif ($e.type -eq 'result') {
            if ($e.usage) { $state.tin = [int64]($e.usage.input_tokens + $e.usage.cache_creation_input_tokens + $e.usage.cache_read_input_tokens); $state.tout = [int64]$e.usage.output_tokens }
            $state.final = [string]$e.result
            $state.ok = (-not $e.is_error)
            $state.last = Short $state.final
        }
    }
    else {
        if ($e.type -eq 'thread.started' -and $e.thread_id) { $state.thread = [string]$e.thread_id }
        elseif ($e.type -eq 'item.completed' -and $e.item) {
            if ($e.item.type -eq 'agent_message') { $state.final = [string]$e.item.text; $state.last = Short $e.item.text; Say ([string]$e.item.text) 'White' }
            elseif ($e.item.type -eq 'command_execution') {
                $state.last = 'cmd: ' + (Short $e.item.command)
                $info = Classify-Cmd ([string]$e.item.command)
                if ($info) { if ($info.kind -eq 'edit') { Say $info.label 'Cyan' } else { Say-Result $info ([string]$e.item.aggregated_output) $e.item.exit_code $false } }
            }
            elseif ($e.item.type -eq 'file_change') { foreach ($ch in @($e.item.changes)) { if ($ch.path) { Say ('geändert: ' + (Rel-Path ([string]$ch.path))) 'Cyan' } } }
        }
        elseif ($e.type -eq 'turn.completed' -and $e.usage) { $state.tin = [int64]$e.usage.input_tokens; $state.tout = [int64]$e.usage.output_tokens; $cx2 = Get-CodexTokens $state.thread; if ($cx2) { $state.ctx = $cx2.ctx }; $state.ok = $true }
        elseif ($e.type -eq 'turn.failed' -or $e.type -eq 'error') { $state.last = 'error: ' + (Short ($e | ConvertTo-Json -Compress -Depth 3)); Say $state.last 'Red' }
    }
    Push-State $false
    Maybe-Head
}

# Build jobs (plan section 16): an agent that builds (integrate, release) gets the job count of this machine as environment variable and its RAM use is measured while it runs.
$bjCfg = if ($Phase -in @('integrate', 'release')) { Get-BuildJobsCfg $cfg } else { $null }
$bjJobs = 0; $bjStartMB = 0.0; $bjSamples = Join-Path $runDir 'build-samples.txt'; $bjSampler = $null
if ($bjCfg -and $bjCfg.EnvVar) {
    $bjStartMB = [math]::Round((Get-FreeRamGB) * 1024, 0)
    $bjCalc = Get-BuildJobCount $bjCfg ([Environment]::ProcessorCount) $bjStartMB (Get-BuildPerJobMB $cfg)
    $bjJobs = $bjCalc.Jobs
    Set-Item -Path ('Env:' + $bjCfg.EnvVar) -Value ([string]$bjJobs)
    Remove-Item $bjSamples -ErrorAction SilentlyContinue
    $bjSampler = Start-Process -FilePath 'pwsh.exe' -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Quote-Arg (Join-Path $PSScriptRoot 'build-sampler.ps1')), '-ParentPid', "$PID", '-Out', (Quote-Arg $bjSamples)) -WindowStyle Hidden -PassThru
    Update-Agent $cfg $Name @{ buildJobs = $bjJobs }
    Write-RoundLog $cfg ('build {0}: starting with {1} jobs ({2}, free RAM {3} MB)' -f $Name, $bjJobs, $bjCalc.Why, $bjStartMB)
}
Push-Location $Cwd
$ErrorActionPreference = 'Continue'
try {
    if ($env:ORCH_FAKE_AGENT) {   # tests only: no model call, a few milestones and token numbers so the pane layout can be checked
        foreach ($i in 1..8) { Say "Testmeilenstein ${i}: das ist eine lange Zeile, die bei schmalen Panes umbrechen soll, damit der Zeilenumbruch sichtbar wird." 'White'; $state.tin += 4000; $state.tout += 120; $state.ctx = 4000 * $i; Push-State $true; Start-Sleep -Seconds 3 }
        $state.ok = $true; $state.final = 'Offene Anforderungen: 0'; $state.last = 'test finished'; $code = 0
    }
    elseif ($Type -eq 'claude') {
        & $prov.Command -p $prompt @argList 2> $errFile | ForEach-Object {
            Add-Content -Path $events -Value $_ -Encoding UTF8
            try { $e = $_ | ConvertFrom-Json } catch { return }
            Handle-Event $e
        }
    }
    else {
        '' | & $prov.Command @argList $prompt 2> $errFile | ForEach-Object {
            Add-Content -Path $events -Value $_ -Encoding UTF8
            try { $e = $_ | ConvertFrom-Json } catch { return }
            Handle-Event $e
        }
    }
    $code = $LASTEXITCODE
}
catch { $code = 99; $state.last = 'runner error: ' + $_.Exception.Message; Say $state.last 'Red' }
finally { Pop-Location }
if ($bjJobs -gt 0) {
    if ($bjSampler -and -not $bjSampler.HasExited) { Stop-Process -Id $bjSampler.Id -Force -ErrorAction SilentlyContinue }
    $bjRes = Complete-BuildMeasure $cfg $Name $bjJobs $bjStartMB $bjSamples $started
    Update-Agent $cfg $Name @{ buildUseMB = $bjRes.UseMB }
}

# Success is judged by the agent's own result event (claude: type result without error; codex: completed turn), not by the exit code: a failing plugin hook after the end must not fail the task.
$status = if ($state.ok) { 'done' } else { 'failed' }
if ($state.ok -and $code -ne 0) { Say ("Hinweis: Exit-Code $code, obwohl der Agent fertig ist (siehe stderr.txt)") 'Yellow'; Write-RoundLog $cfg "warning $Name exit code $code after a finished task (hook/stderr), kept as done" }
if (-not $state.ok) { $errTxt = if (Test-Path $errFile) { (Get-Content $errFile -Tail 3 -ErrorAction SilentlyContinue) -join ' ' } else { '' }; if ($state.last -notmatch '^(error|runner error)') { $state.last = ('no result event, exit ' + $code + ' ' + $errTxt) }; $state.last = Short $state.last }
[System.IO.File]::WriteAllText($finalFile, [string]$state.final, (New-Object System.Text.UTF8Encoding($false)))
[void](Update-AgentRetry $cfg $Name @{ status = $status; endedAt = (Get-Date).ToString('o'); exitCode = $code; tokensIn = $state.tin; tokensOut = $state.tout; lastLine = $state.last })
$state.ended = Get-Date
[void](Draw-Head)
if ($state.final -and (Short $state.final 1500) -ne (Short $state.lastSaid 1500)) { Say ('Schlussmeldung: ' + $state.final) $(if ($status -eq 'done') { 'Green' } else { 'Red' }) }
Say "Ende: $status (Exit $code), Total Input $(Fmt-Tok $state.tin) / Output $(Fmt-Tok $state.tout). Abschnitt schließt 30 s nach dem Ende (pane-wrap.ps1)." $(if ($status -eq 'done') { 'Green' } else { 'Red' })
exit 0
