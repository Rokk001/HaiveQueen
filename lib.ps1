# Shared helpers for the agent orchestrator (PowerShell 7, pwsh).
$ErrorActionPreference = 'Stop'
$script:ToolsDir = $PSScriptRoot
# pwsh 7 switches to plain text when NO_COLOR is set (inherited e.g. from a Claude Code session): the panes must always show colors.
Remove-Item Env:NO_COLOR -ErrorAction SilentlyContinue
if ($PSStyle) { $PSStyle.OutputRendering = 'Ansi' }

# Returns the parsed project file (path in $env:ORCH_PROJECT, set by start.ps1 -Project / the -Project parameter of every entry script). The tools folder is always the folder of these scripts.
function Get-Cfg {
    if (-not $env:ORCH_PROJECT) { throw 'no project file: pass -Project <path to the project json> (or set ORCH_PROJECT)' }
    $c = (Get-Content $env:ORCH_PROJECT -Raw -Encoding UTF8 | ConvertFrom-Json)
    $c | Add-Member -NotePropertyName tools -NotePropertyValue $script:ToolsDir -Force
    $c | Add-Member -NotePropertyName projectFile -NotePropertyValue ([string]$env:ORCH_PROJECT) -Force
    $c | Add-Member -NotePropertyName projectDir -NotePropertyValue (Split-Path ([string]$env:ORCH_PROJECT) -Parent) -Force
    if ($env:ORCH_RUNTIME_OVERRIDE) { $c.runtime = $env:ORCH_RUNTIME_OVERRIDE }   # tests only: separate runtime folder (own AGENTS.json, logs), never the live one
    $plansRoot = if ($env:ORCH_PLANS_OVERRIDE) { $env:ORCH_PLANS_OVERRIDE } else { Join-Path ([string]$c.repo) ([string]$c.plansRoot) }   # ORCH_PLANS_OVERRIDE tests only: dummy plans
    $c | Add-Member -NotePropertyName projectsRoot -NotePropertyValue $plansRoot -Force
    $script:ProjCfg = $c
    return $c
}

# Returns the project config already loaded in this process (loads it when no helper has done so yet); for helpers that get no config parameter.
function Get-ProjCfg {
    if ($script:ProjCfg) { return $script:ProjCfg }
    return (Get-Cfg)
}

# Replaces every {name} in a template by the value of the same key in the hashtable.
function Expand-Template([string]$Template, [hashtable]$Vars) {
    # One pass over the template, so a value that itself contains {name} is never expanded again.
    return [regex]::Replace($Template, '\{(\w+)\}', [System.Text.RegularExpressions.MatchEvaluator]{ param($m) if ($Vars.ContainsKey($m.Groups[1].Value)) { [string]$Vars[$m.Groups[1].Value] } else { $m.Value } })
}

# Returns the prompt template with that name from the project file "prompts" section, expanded with the variables; a missing template throws.
function Get-PromptText($Cfg, [string]$Name, [hashtable]$Vars) {
    $tpl = $Cfg.prompts.$Name
    if ($null -eq $tpl) { throw "prompt template '$Name' is missing in the project file" }
    $v = @{ owner = [string]$Cfg.owner; integrationBranch = [string]$Cfg.integrationBranch; compileCheck = [string]$Cfg.compileCheck; queueFile = (Get-QueueRel $Cfg); queueName = (Split-Path ([string]$Cfg.integrationStateFile) -Leaf); integratorInstructions = [string]$Cfg.integratorInstructions; buildFlowDoc = [string]$Cfg.buildFlowDoc; runsDir = (Get-RuntimeRel $Cfg) + '/runs/' }
    foreach ($k in $Vars.Keys) { $v[$k] = $Vars[$k] }
    return (Expand-Template ([string]$tpl) $v)
}

# Path of a file below the repository as forward-slash text for prompts ("a\b" -> "a/b"); a path outside the repository is returned unchanged.
function ConvertTo-RepoRel($Cfg, [string]$Path) {
    $repo = ([string]$Cfg.repo).TrimEnd('\')
    if ($Path.StartsWith($repo + '\', [System.StringComparison]::OrdinalIgnoreCase)) { $Path = $Path.Substring($repo.Length + 1) }
    return $Path.Replace('\', '/')
}
# Absolute path of the project rules file (ruleSources.project).
function Get-RulesFilePath($Cfg) { return (Resolve-RuleSourcePath $Cfg 'project') }
# Absolute path of one rule source of the project file (ruleSources.<name>) with {repo}, {tools}, {home} and {projectDir} replaced.
function Resolve-RuleSourcePath($Cfg, [string]$Name) { return ([string]$Cfg.ruleSources.$Name).Replace('{repo}', $Cfg.repo).Replace('{tools}', $Cfg.tools).Replace('{home}', $env:USERPROFILE).Replace('{projectDir}', $Cfg.projectDir) }
# Runtime folder as repo-relative forward-slash text.
function Get-RuntimeRel($Cfg) { return (ConvertTo-RepoRel $Cfg ([string]$Cfg.runtime)) }
# Integration queue file as repo-relative forward-slash text (plans root + integrationStateFile).
function Get-QueueRel($Cfg) { return ((ConvertTo-RepoRel $Cfg ([string]$Cfg.plansRoot)) + '/' + ([string]$Cfg.integrationStateFile).Replace('\', '/')) }
# Absolute path of the integration queue file (below the plans root, so a test plans root carries its own queue).
function Get-QueuePath($Cfg) { return (Join-Path ([string]$Cfg.projectsRoot) ([string]$Cfg.integrationStateFile)) }
# Absolute path of the questions file (ORCH_QUESTIONS_FILE only in tests).
function Get-QuestionsPath($Cfg) { if ($env:ORCH_QUESTIONS_FILE) { return $env:ORCH_QUESTIONS_FILE }; return (Join-Path ([string]$Cfg.repo) ([string]$Cfg.questionsFile)) }
# Absolute path of the answers file (ORCH_ANSWERS_FILE only in tests).
function Get-AnswersPath($Cfg) { if ($env:ORCH_ANSWERS_FILE) { return $env:ORCH_ANSWERS_FILE }; return (Join-Path ([string]$Cfg.repo) ([string]$Cfg.answersFile)) }

# Runs a script block while holding the AGENTS.json mutex (the mutex name starts with the project's mutexPrefix).
# The wait is limited (ORCH_LOCK_TIMEOUT_SEC, default 120 s) and throws instead of waiting forever; a lock abandoned by an exited process counts as free.
function Invoke-Locked([scriptblock]$Block) {
    $mutexName = 'Global\' + [string](Get-ProjCfg).mutexPrefix + 'AgentsJson'
    $limit = if ($env:ORCH_LOCK_TIMEOUT_SEC) { [int]$env:ORCH_LOCK_TIMEOUT_SEC } else { 120 }
    $m = New-Object System.Threading.Mutex($false, $mutexName)
    $got = $false
    try { $got = $m.WaitOne($limit * 1000) } catch [System.Threading.AbandonedMutexException] { $got = $true }
    if (-not $got) { throw "agents lock '$mutexName' not free within $limit s" }
    try { & $Block } finally { $m.ReleaseMutex() }
}
# Reads AGENTS.json as a flat array of agent rows (rows without a name, empty arrays and old nested wrappers are dropped).
function Read-Agents($Cfg) {
    $p = Join-Path $Cfg.runtime 'AGENTS.json'
    if (-not (Test-Path $p)) { return @() }
    # Open with full sharing (incl. delete) so that a reader never blocks the atomic rename of a writer.
    $raw = $null
    for ($try = 1; $try -le 20 -and $null -eq $raw; $try++) {
        try { $fs = [System.IO.File]::Open($p, 'Open', 'Read', 'ReadWrite, Delete'); try { $sr = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8); $raw = $sr.ReadToEnd() } finally { $fs.Dispose() } } catch { Start-Sleep -Milliseconds 50 }
    }
    if ([string]::IsNullOrWhiteSpace($raw)) { return @() }
    $parsed = ConvertFrom-Json -InputObject $raw
    $out = New-Object System.Collections.ArrayList
    $walk = $null
    $walk = { param($x) foreach ($y in @($x)) { if ($null -eq $y) { continue }; if ($y.PSObject.Properties['name'] -and $y.name) { [void]$out.Add($y) } elseif ($y.PSObject.Properties['value']) { & $walk $y.value } elseif ($y -is [System.Array]) { & $walk $y } } }
    & $walk $parsed
    return $out.ToArray()
}
# Writes the agent list back to AGENTS.json.
function Write-Agents($Cfg, $List) {
    $p = Join-Path $Cfg.runtime 'AGENTS.json'
    $flat = @($List | Where-Object { $_ -and $_.PSObject.Properties['name'] -and $_.name })
    $json = if ($flat.Count -eq 0) { '[]' } else { ConvertTo-Json -InputObject $flat -Depth 6 }
    Write-FileAtomic $p $json
}

# Writes a text file atomically: into a temporary file next to it first, then renamed over the target, so readers never see a half file.
function Write-FileAtomic([string]$Path, [string]$Text) {
    # Readers (dashboard, one header process per pane) may hold the target open for a moment: retry the rename instead of failing the whole agent (seen 2026-10-27 12:28, "Access to the path is denied").
    $tmp = $Path + '.' + [System.Diagnostics.Process]::GetCurrentProcess().Id + '.tmp'
    [System.IO.File]::WriteAllText($tmp, $Text, (New-Object System.Text.UTF8Encoding($false)))
    for ($try = 1; $try -le 40; $try++) {
        try { [System.IO.File]::Move($tmp, $Path, $true); return } catch { if ($try -eq 40) { Remove-Item $tmp -ErrorAction SilentlyContinue; throw }; Start-Sleep -Milliseconds (50 + 25 * $try) }
    }
}

# Moves agent rows out of AGENTS.json into AGENTS-ARCHIVE.json (both written atomically, under the lock).
# Without -Match: every row that ended more than $Hours hours ago (running rows always stay). With -Match: every row whose plan or name matches the regex. Returns the number of moved rows.
function Move-AgentsToArchive($Cfg, [double]$Hours = 12, [string]$Match = '') {
    $moved = Invoke-Locked {
        $list = @(Read-Agents $Cfg)
        $old = @($list | Where-Object {
            if ($Match) { return ([string]$_.plan -match $Match -or [string]$_.name -match $Match) }
            return ($_.endedAt -and $_.status -ne 'running' -and $_.status -ne 'starting' -and ((Get-Date) - [datetime]$_.endedAt).TotalHours -gt $Hours)
        })
        if ($old.Count -eq 0) { return 0 }
        $ap = Join-Path $Cfg.runtime 'AGENTS-ARCHIVE.json'
        $arch = @()
        if (Test-Path $ap) { $raw = Get-Content $ap -Raw -Encoding UTF8; if (-not [string]::IsNullOrWhiteSpace($raw)) { $arch = @(ConvertFrom-Json -InputObject $raw) } }
        Write-FileAtomic $ap (ConvertTo-Json -InputObject @($arch + $old) -Depth 6)
        $keep = @($list | Where-Object { $old -notcontains $_ })
        Write-Agents $Cfg $keep
        return $old.Count
    }
    return [int]$moved
}

# Creates or updates the row of one agent (hashtable of properties) under the lock.
function Update-Agent($Cfg, [string]$Name, [hashtable]$Props) {
    Invoke-Locked {
        $list = @(Read-Agents $Cfg)
        $row = $list | Where-Object { $_.name -eq $Name } | Select-Object -First 1
        if (-not $row) {
            $row = [pscustomobject]@{ name = $Name }
            $list = @($list) + @($row)
        }
        foreach ($k in $Props.Keys) { $row | Add-Member -NotePropertyName $k -NotePropertyValue $Props[$k] -Force }
        Write-Agents $Cfg $list
    }
}

# Appends one timestamped line to ROUNDS.log.
function Write-RoundLog($Cfg, [string]$Text) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Text
    Add-Content -Path (Join-Path $Cfg.runtime 'ROUNDS.log') -Value $line -Encoding UTF8
}

# Free physical RAM in GB.
function Get-FreeRamGB {
    return [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 2)
}

# Names of running compiler/build processes (cl, link, msbuild, cmake). ONE detection used by dashboard, round snapshot and start gate.
function Get-BuildProcessNames {
    return @(Get-Process cl, link, msbuild, cmake -ErrorAction SilentlyContinue | ForEach-Object { $_.Name } | Sort-Object -Unique)
}

# True while any build process runs (same detection as Get-BuildProcessNames).
function Test-FullBuildRunning { return ((Get-BuildProcessNames).Count -gt 0) }
# Parses OPEN-QUESTIONS.md into blocks (id, plan, item, status).
function Get-Questions($Cfg) {
    $p = Get-QuestionsPath $Cfg
    $res = @()
    if (-not (Test-Path $p)) { return @() }
    $cur = $null
    foreach ($l in (Get-Content $p -Encoding UTF8)) {
        if ($l -match '^##\s+(Q-\S+)') { if ($cur) { $res += $cur }; $cur = [ordered]@{ id = $Matches[1]; plan = ''; item = ''; status = ''; answer = '' } }
        elseif ($cur -and $l -match '^- Plan:\s*(.*)$') { $cur.plan = $Matches[1].Trim() }
        elseif ($cur -and $l -match '^- Item:\s*(.*)$') { $cur.item = $Matches[1].Trim() }
        elseif ($cur -and $l -match '^- Status:\s*(\S+)') { $cur.status = $Matches[1] }
        elseif ($cur -and $l -match '^- Answer[^:]*:\s*(.*)$') { $cur.answer = $Matches[1].Trim() }
    }
    if ($cur) { $res += $cur }
    return @($res)
}

# Quotes one argument for a Windows command line.
function ConvertTo-ArgQuoted([string]$A) {
    if ($A -eq '') { return '""' }   # an empty argument (e.g. --tools "") must survive the command line
    if ($A -notmatch '[\s"]') { return $A }
    return '"' + ($A -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

# Runs a native program with UTF-8 stdin/stdout and a timeout; returns @{ExitCode; StdOut; StdErr; TimedOut}.
function Invoke-Native([string]$File, [string[]]$ArgList, [string]$StdIn, [string]$WorkDir, [int]$TimeoutSec) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $File
    $psi.Arguments = ($ArgList | ForEach-Object { ConvertTo-ArgQuoted $_ }) -join ' '
    $psi.WorkingDirectory = $WorkDir
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $psi.CreateNoWindow = $true
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($StdIn)
    $proc.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
    $proc.StandardInput.Close()
    $outTask = $proc.StandardOutput.ReadToEndAsync()
    $errTask = $proc.StandardError.ReadToEndAsync()
    $timedOut = -not $proc.WaitForExit($TimeoutSec * 1000)
    if ($timedOut) { try { $proc.Kill() } catch {} }
    return @{ ExitCode = $(if ($timedOut) { -1 } else { $proc.ExitCode }); StdOut = $outTask.Result; StdErr = $errTask.Result; TimedOut = $timedOut }
}

# State files that belong to one plan: <plan>-<stateToken>.md (also without the plan suffix of the plan name). Only when there is none, the *<stateToken>*.md files of the folder that belong to no other plan file of the folder (several plans may share a folder, e.g. numbered stand-alone plans).
function Get-PlanStateFiles([string]$PlanPath) {
    $pc = Get-ProjCfg; $tok = [string]$pc.stateToken
    $dir = Split-Path $PlanPath -Parent; $base = [IO.Path]::GetFileNameWithoutExtension($PlanPath)
    $names = @("$base-$tok.md", (($base -replace [string]$pc.planSuffixRegex, '') + "-$tok.md")) | Select-Object -Unique
    $own = @($names | ForEach-Object { Join-Path $dir $_ } | Where-Object { Test-Path -LiteralPath $_ } | ForEach-Object { Get-Item -LiteralPath $_ })
    if ($own.Count) { return $own }
    $stems = @(Get-ChildItem $dir -Filter '*.md' -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch $tok -and $_.FullName -ne $PlanPath -and (Test-PlanFileName $_.Name 'planStemRegexes') } | ForEach-Object { ($_.BaseName -replace [string]$pc.planSuffixRegex, '') })
    return @(Get-ChildItem $dir -Filter "*$tok*.md" -ErrorAction SilentlyContinue | Where-Object { $n = $_.Name; @($stems | Where-Object { $n.StartsWith($_ + '-') }).Count -eq 0 })
}

# Runs a block while holding the one global lock for all commands that create or close panes in the orchestrator window (wt.exe commands and pane exits never overlap; a lock abandoned by an exiting pane process counts as released), then waits a short settle time.
function Invoke-WtLocked([scriptblock]$Block, [int]$TimeoutSec = 180) {
    $mutexName = 'Global\' + [string](Get-ProjCfg).mutexPrefix + 'WtPaneCommands'
    $m = New-Object System.Threading.Mutex($false, $mutexName)
    $got = $false
    try { $got = $m.WaitOne($TimeoutSec * 1000) } catch [System.Threading.AbandonedMutexException] { $got = $true }
    if (-not $got) { throw 'wt command lock not free' }
    try { Start-Sleep -Seconds 2; & $Block } finally { try { $m.ReleaseMutex() } catch {} }
}
# True when the pid in a pid file belongs to a running process.
function Test-PidFileAlive([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $v = Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue | Select-Object -First 1
    return [bool]($v -and (Get-Process -Id ([int]$v) -ErrorAction SilentlyContinue))
}
# True when an agent row has a pane that certainly exists now: the runner process is alive and the agent is running (a pane of an ended agent is about to close and is never a target).
function Test-AgentPaneAlive($Row) {
    return [bool]($Row -and $null -ne $Row.paneId -and -not $Row.paneClosed -and $Row.status -eq 'running' -and $Row.pid -and (Get-Process -Id ([int]$Row.pid) -ErrorAction SilentlyContinue))
}

# Chooses where the next agent pane goes and reserves its pane id. Panes of the one Windows Terminal tab are numbered in creation order (0 Orchestrator, 1 idle area, 2 Uebersicht, then the agents from panes.json "next").
# Rule: split the widest open agent pane side by side while both halves stay at least 40 columns wide, else the tallest one top/bottom; the first agent splits the idle area (which closes itself). Returns @{ Target; Dir; Id }.
function Get-PaneSlot($Cfg, [string]$Name, [int]$MinCols = 40, [int]$MinRows = 24) {
    return (Invoke-Locked {
        $pp = Join-Path $Cfg.runtime 'panes.json'
        $next = 3
        if (Test-Path $pp) { try { $next = [int](Get-Content $pp -Raw -Encoding UTF8 | ConvertFrom-Json).next } catch {} }
        $open = @(Read-Agents $Cfg | Where-Object { $_.name -ne $Name -and (Test-AgentPaneAlive $_) })   # only panes whose existence is confirmed right now
        $target = 1; $dir = '-V'; $half = $null
        # An idle pane opened by pane-wrap.ps1 (when the last agent pane closed) is the split target of the next agent.
        if ($open.Count -eq 0) {
            # no agent pane: the idle pane (agents-idle.ps1, pid file) is the only target, and only while its process is alive; without it the caller opens a new tab (no command on a pane that may not exist).
            if (Test-PidFileAlive (Join-Path $Cfg.runtime 'idle.pid')) { $target = 1; if (Test-Path $pp) { try { $ip = (Get-Content $pp -Raw -Encoding UTF8 | ConvertFrom-Json).idle; if ($null -ne $ip) { $target = [int]$ip } } catch {} } } else { $target = $null }
        }
        if ($open.Count -gt 0) {
            $wide = @($open | Where-Object { [int]$_.paneCols -ge (2 * $MinCols + 1) } | Sort-Object { [int]$_.paneCols } -Descending)
            $tall = @($open | Where-Object { [int]$_.paneRows -ge $MinRows } | Sort-Object { [int]$_.paneRows } -Descending)
            if ($wide.Count) { $half = $wide[0]; $dir = '-V' } elseif ($tall.Count) { $half = $tall[0]; $dir = '-H' } else { $half = @($open | Sort-Object { [int]$_.paneCols } -Descending)[0]; $dir = '-V' }   # nothing fits: the widest is split anyway, the header wraps and stays readable
            $target = [int]$half.paneId
        }
        $cols = 140; $rows = 50
        if ($half) {
            $cols = [int]$half.paneCols; $rows = [int]$half.paneRows
            if ($dir -eq '-V') { $cols = [int][Math]::Floor($cols / 2) } else { $rows = [int][Math]::Floor($rows / 2) }
            $old = $half.name
            $list = @(Read-Agents $Cfg); $row = $list | Where-Object { $_.name -eq $old } | Select-Object -First 1
            $row | Add-Member -NotePropertyName paneCols -NotePropertyValue $cols -Force; $row | Add-Member -NotePropertyName paneRows -NotePropertyValue $rows -Force   # estimate until the pane reports its real size
            Write-Agents $Cfg $list
        }
        $list = @(Read-Agents $Cfg); $mine = $list | Where-Object { $_.name -eq $Name } | Select-Object -First 1
        if ($mine) { $mine | Add-Member -NotePropertyName paneId -NotePropertyValue $next -Force; $mine | Add-Member -NotePropertyName paneCols -NotePropertyValue $cols -Force; $mine | Add-Member -NotePropertyName paneRows -NotePropertyValue $rows -Force; $mine | Add-Member -NotePropertyName paneClosed -NotePropertyValue $false -Force; Write-Agents $Cfg $list }
        Write-FileAtomic $pp (ConvertTo-Json -InputObject @{ next = ($next + 1) } -Compress)
        return @{ Target = $target; Dir = $dir; Id = $next }
    })
}

# Opens one agent pane in the orchestrator window: the area right of Orchestrator and Uebersicht is divided between the agents so that every pane is at least 40 columns wide (Get-PaneSlot).
function Start-AgentPane($Cfg, [hashtable]$A) {
    $ps = "pwsh -NoProfile -ExecutionPolicy Bypass -File `"$($script:ToolsDir)\pane-wrap.ps1`" -Project `"$($Cfg.projectFile)`" -Name `"$($A.name)`" -Type $($A.type) -Plan `"$($A.plan)`" -Package `"$($A.package)`" -Phase $($A.phase) -Cwd `"$($A.cwd)`" -PromptFile `"$($A.promptFile)`""
    # One pane command at a time (also against panes that close or are created by pane-wrap.ps1); the slot is chosen inside the lock so its targets are still alive when the command runs.
    Invoke-WtLocked {
        $slot = Get-PaneSlot $Cfg ([string]$A.name)
        $title = Get-AgentPaneTitle $Cfg $A
        $wtExe = if ($env:ORCH_WT_EXE) { $env:ORCH_WT_EXE } else { 'wt.exe' }   # ORCH_WT_EXE only for tests (a stub that logs the commands)
        $win = [string]$Cfg.window; $prof = [string]$Cfg.terminalProfile.name
        if ($null -eq $slot.Target) {
            $wtArgs = "-w $win new-tab -p $prof --suppressApplicationTitle --title `"$title`" $ps"
            Write-RoundLog $Cfg ("pane of $($A.name): id $($slot.Id), no live pane to split, opens a new tab")
        }
        else {
            $wtArgs = "-w $win focus-pane -t $($slot.Target) ; split-pane -p $prof $($slot.Dir) -s 0.5 --suppressApplicationTitle --title `"$title`" $ps"
            Write-RoundLog $Cfg ("pane of $($A.name): id $($slot.Id), splits pane $($slot.Target) ($($slot.Dir))")
        }
        Start-Process -FilePath $wtExe -ArgumentList $wtArgs
        # The pane may not come up: wait for the runner pid (agent-run sets it right at its start) and launch the pane once more when it stays away.
        for ($try = 1; $try -le 2; $try++) {
            for ($i = 0; $i -lt 20; $i++) {
                Start-Sleep -Seconds 2
                $row = @(Read-Agents $Cfg | Where-Object { $_.name -eq $A.name } | Select-Object -First 1)
                if ($row.Count -and $row[0].pid) { Start-Sleep -Seconds 3; return }   # settle time: the idle pane may close itself now
            }
            if ($try -eq 1 -and -not (Get-Process WindowsTerminal -ErrorAction SilentlyContinue)) { Write-RoundLog $Cfg ('pane of ' + $A.name + ': Windows Terminal is not running, no second launch'); return }
            if ($try -eq 1) { Write-RoundLog $Cfg ('pane of ' + $A.name + ' did not start within 40 s, launching it again'); Start-Process -FilePath $wtExe -ArgumentList $wtArgs }
        }
    } 300
}

# Converts an epoch-seconds value to a local HH:mm / dd.MM. HH:mm text.
function Format-ResetTime($Epoch) {
    if (-not $Epoch) { return '' }
    $d = [DateTimeOffset]::FromUnixTimeSeconds([int64]$Epoch).LocalDateTime
    if ($d.Date -eq (Get-Date).Date) { return $d.ToString('HH:mm') }
    return $d.ToString('dd.MM. HH:mm')
}

# Remembers the newest Claude usage windows (from a rate_limit_event of any headless claude run) in usage-claude.json.
function Save-ClaudeUsage($Cfg, $Event) {
    $w = $Event.rate_limit_info.unifiedWindows
    if (-not $w) { return }
    $o = [ordered]@{ saved = (Get-Date).ToString('o'); fiveHour = $w.five_hour; sevenDay = $w.seven_day }
    [System.IO.File]::WriteAllText((Join-Path $Cfg.runtime 'usage-claude.json'), ($o | ConvertTo-Json -Depth 5), (New-Object System.Text.UTF8Encoding($false)))
}

# Claude 5h / 7d usage: @{FiveHour=@{Pct;Reset}; SevenDay=@{Pct;Reset}}; values are $null when unknown or the window has already reset.
function Get-ClaudeUsage($Cfg) {
    $res = @{ FiveHour = $null; SevenDay = $null }
    $p = Join-Path $Cfg.runtime 'usage-claude.json'
    if (-not (Test-Path $p)) { return $res }
    try {
        $j = Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json
        $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        if ($j.fiveHour) { if ([int64]$j.fiveHour.resetsAt -gt $now) { $res.FiveHour = @{ Pct = [math]::Round(100 * [double]$j.fiveHour.utilization); Reset = (Format-ResetTime $j.fiveHour.resetsAt) } } else { $res.FiveHour = @{ Pct = $null; Reset = 'neues Fenster' } } }
        if ($j.sevenDay) { if ([int64]$j.sevenDay.resetsAt -gt $now) { $res.SevenDay = @{ Pct = [math]::Round(100 * [double]$j.sevenDay.utilization); Reset = (Format-ResetTime $j.sevenDay.resetsAt) } } else { $res.SevenDay = @{ Pct = $null; Reset = 'neues Fenster' } } }
    } catch {}
    return $res
}

# Codex 5h / weekly usage from the newest session log (last rate_limits block); windows are told apart by window_minutes (300 = 5h, 10080 = week).
function Get-CodexUsage {
    $res = @{ FiveHour = $null; SevenDay = $null }
    try {
        $f = Get-ChildItem (Join-Path $env:USERPROFILE '.codex\sessions') -Recurse -Filter '*.jsonl' | Sort-Object LastWriteTime -Descending | Select-Object -First 3
        foreach ($file in $f) {
            $fs = [System.IO.File]::Open($file.FullName, 'Open', 'Read', 'ReadWrite')
            try {
                $len = [Math]::Min($fs.Length, 400KB); [void]$fs.Seek(-$len, 'End')
                $buf = New-Object byte[] $len; [void]$fs.Read($buf, 0, $len)
            } finally { $fs.Close() }
            $lines = @([System.Text.Encoding]::UTF8.GetString($buf).Split("`n") | Where-Object { $_ -like '*"rate_limits":{*' })
            if ($lines.Count -eq 0) { continue }
            $e = $lines[-1] | ConvertFrom-Json
            $rl = $e.payload.rate_limits
            if (-not $rl) { continue }
            $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
            foreach ($w in @($rl.primary, $rl.secondary)) {
                if (-not $w -or [int64]$w.resets_at -le $now) { continue }
                $v = @{ Pct = [math]::Round([double]$w.used_percent); Reset = (Format-ResetTime $w.resets_at) }
                if ([int]$w.window_minutes -le 400) { $res.FiveHour = $v } else { $res.SevenDay = $v }
            }
            break
        }
    } catch {}
    return $res
}

# One display text for a usage value ("27% (resets 12:30)" or "n/a").
function Format-Usage($U) { if ($null -eq $U -or $null -eq $U.Pct) { return 'n/a' }; return ('{0}% (bis {1})' -f $U.Pct, $U.Reset) }


# Returns the plan status from the second line ("Status: X"); a missing line counts as DRAFT.
function Get-PlanStatus([string]$PlanPath) {
    $l = @(Get-Content $PlanPath -TotalCount 2 -Encoding UTF8)
    if ($l.Count -ge 2 -and $l[1] -match '^Status:\s*([A-Za-z_]+)\s*$') { return $Matches[1].ToUpper() }
    return 'DRAFT'
}

# Rewrites ONLY the status word of line 2 (rule 11); returns $false when line 2 is not a plain status line.
function Set-PlanStatus([string]$PlanPath, [string]$New) {
    $all = @(Get-Content $PlanPath -Encoding UTF8)
    if ($all.Count -lt 2 -or $all[1] -notmatch '^Status:\s*[A-Za-z_]+\s*$') { return $false }
    $all[1] = "Status: $New"
    [System.IO.File]::WriteAllLines($PlanPath, $all, (New-Object System.Text.UTF8Encoding($true)))
    return $true
}



# True when line 2 of the file is a plain status line ("Status: X").
function Test-HasStatusLine([string]$Path) {
    $l = @(Get-Content $Path -TotalCount 2 -Encoding UTF8)
    return ($l.Count -ge 2 -and $l[1] -match '^Status:\s*[A-Za-z_]+\s*$')
}

# True when a file name is a plan file name by one of the project's regexes (property planFileRegexes, or planStemRegexes for the files whose names give the stems of the state-file rule).
function Test-PlanFileName([string]$Name, [string]$Property = 'planFileRegexes') {
    foreach ($re in @((Get-ProjCfg).$Property)) { if ($Name -match [string]$re) { return $true } }
    return $false
}
# Lists plan files: file names matching planFileRegexes, or any .md with a "Status:" line in line 2; files matching planExcludeNameRegex are ignored, and (unless disabled) so are folders matching planExcludeDirRegex.
function Get-PlanFiles([string]$ProjectsRoot, [bool]$ExcludeToolDirs = $true) {
    $pc = Get-ProjCfg
    $files = Get-ChildItem $ProjectsRoot -Recurse -Filter '*.md' | Where-Object { $_.Name -notmatch [string]$pc.planExcludeNameRegex }
    if ($ExcludeToolDirs) { $files = $files | Where-Object { $_.FullName -notmatch [string]$pc.planExcludeDirRegex } }
    return @($files | Where-Object { (Test-PlanFileName $_.Name) -or (Test-HasStatusLine $_.FullName) })
}
# Tasks (plan|package|phase) whose last 2 starts failed with the same error: they are blocked and never restarted. Returns objects {Key; Name; Error; Plan; Package; Phase}.
function Get-BlockedTasks($Agents) {
    $res = @()
    $groups = @($Agents | Where-Object { $_.status -eq 'failed' } | Group-Object { "$($_.plan)|$($_.package)|$($_.phase)" })
    foreach ($g in $groups) {
        $rows = @($g.Group | Sort-Object { [string]$_.startedAt })
        if ($rows.Count -lt 2) { continue }
        $a = $rows[-1]; $b = $rows[-2]
        # a pane that never came up says nothing about the task, so it never blocks it
        if ([string]$a.lastLine -eq [string]$b.lastLine -and [string]$a.lastLine -notmatch '^pane never started') { $res +=[pscustomobject]@{ Key = $g.Name; Name = [string]$a.name; Error = [string]$a.lastLine; Plan = [string]$a.plan; Package = [string]$a.package; Phase = [string]$a.phase } }
    }
    return $res
}



# Marks running/starting agents whose runner process is gone (killed, crashed) as failed so slots and the dashboard stay correct.
function Repair-StaleAgents($Cfg) {
    foreach ($a in @(Read-Agents $Cfg | Where-Object { $_.status -eq 'running' -or $_.status -eq 'starting' })) {
        $dead = $false
        if ($a.pid) { if (-not (Get-Process -Id ([int]$a.pid) -ErrorAction SilentlyContinue)) { $dead = $true } }
        elseif ($a.startedAt -and ((Get-Date) - [datetime]$a.startedAt).TotalMinutes -gt 2) { $dead = $true; $noPane = $true }
        if ($dead) {
            $why = if ($noPane) { 'pane never started (no runner process)' } else { 'runner process vanished (ended by executor or crashed)' }
            Update-Agent $Cfg ([string]$a.name) @{ status = 'failed'; endedAt = (Get-Date).ToString('o'); lastLine = $why; paneClosed = $true }; Write-RoundLog $Cfg ('TECHNICAL NOTE: ' + $a.name + ': ' + $why + ', marked failed')
        }
        $noPane = $false
    }
}


# True when any compiler/build process (cl, link, msbuild, cmake) runs on this PC (live check).
function Test-AnyBuildProcess { return ((Get-BuildProcessNames).Count -gt 0) }


# Current context size of a Codex run: input_tokens of the LAST model call (last_token_usage of the newest token_count event in the session log of the thread). Returns $null when unreadable.
function Get-CodexContext([string]$ThreadId) {
    if (-not $ThreadId) { return $null }
    try {
        $f = Get-ChildItem (Join-Path $env:USERPROFILE '.codex\sessions') -Recurse -Filter "*$ThreadId.jsonl" -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $f) { return $null }
        $fs = [System.IO.File]::Open($f.FullName, 'Open', 'Read', 'ReadWrite')
        try { $len = [Math]::Min($fs.Length, 300KB); [void]$fs.Seek(-$len, 'End'); $buf = New-Object byte[] $len; [void]$fs.Read($buf, 0, $len) } finally { $fs.Close() }
        $lines = @([System.Text.Encoding]::UTF8.GetString($buf).Split("`n") | Where-Object { $_ -like '*"type":"token_count"*last_token_usage*' })
        if ($lines.Count -eq 0) { return $null }
        $e = $lines[-1] | ConvertFrom-Json
        return [int64]$e.payload.info.last_token_usage.input_tokens
    } catch { return $null }
}

# Live token numbers of a running Codex agent from its session log: context size (last request) and total input / output so far; $null when not available yet.
function Get-CodexTokens([string]$ThreadId) {
    if (-not $ThreadId) { return $null }
    try {
        $f = Get-ChildItem (Join-Path $env:USERPROFILE '.codex\sessions') -Recurse -Filter "*$ThreadId.jsonl" -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $f) { return $null }
        $fs = [System.IO.File]::Open($f.FullName, 'Open', 'Read', 'ReadWrite')
        try { $len = [Math]::Min($fs.Length, 300KB); [void]$fs.Seek(-$len, 'End'); $buf = New-Object byte[] $len; [void]$fs.Read($buf, 0, $len) } finally { $fs.Close() }
        $lines = @([System.Text.Encoding]::UTF8.GetString($buf).Split("`n") | Where-Object { $_ -like '*"type":"token_count"*last_token_usage*' })
        if ($lines.Count -eq 0) { return $null }
        $e = $lines[-1] | ConvertFrom-Json
        $t = $e.payload.info.total_token_usage; return [pscustomobject]@{ ctx = [int64]$e.payload.info.last_token_usage.input_tokens; tin = [int64]$t.input_tokens; tout = [int64]$t.output_tokens }
    } catch { return $null }
}

# Readable model name: claude-sonnet-5-5 -> Sonnet 5.5, sonnet -> Sonnet, gpt-6.1-sol -> GPT-6.1-Sol.
function Get-ModelLabel([string]$m) {
    if ($m -match '^claude-([a-z]+)-(\d+)-(\d+)') { return (Get-Culture).TextInfo.ToTitleCase($Matches[1]) + ' ' + $Matches[2] + '.' + $Matches[3] }
    $parts = $m -split '-'; $out = @()
    for ($i = 0; $i -lt $parts.Count; $i++) { $x = $parts[$i]; if ($i -eq 0 -and $parts.Count -gt 1) { $out += $x.ToUpper() } elseif ($x -match '^\d') { $out += $x } else { $out += (Get-Culture).TextInfo.ToTitleCase($x) } }
    return ($out -join '-')
}
# Token count in short form (51k, 1.2M); a dash when unknown.
function Format-Tokens($n) {
    if ($null -eq $n) { return [string][char]0x2013 }
    if ($n -ge 1000000) { return ('{0:N1}M' -f ($n / 1e6)) }
    if ($n -ge 1000) { return ('{0:N0}k' -f ($n / 1e3)) }
    return "$n"
}
# Appends one brain-call line (a JSON object: ts, trigger, model, context, input, output, durationSec, actions, error) to BRAIN-USAGE.jsonl.
function Add-BrainRow($Cfg, $Row) {
    $p = Join-Path $Cfg.runtime 'BRAIN-USAGE.jsonl'
    Add-Content -Path $p -Value (ConvertTo-Json -InputObject $Row -Compress -Depth 5) -Encoding UTF8
}
# Reads all lines of BRAIN-USAGE.jsonl (damaged lines are skipped).
function Read-BrainRows($Cfg) {
    $p = Join-Path $Cfg.runtime 'BRAIN-USAGE.jsonl'
    if (-not (Test-Path $p)) { return @() }
    $out = @()
    foreach ($l in (Get-Content $p -Encoding UTF8)) { if ([string]::IsNullOrWhiteSpace($l)) { continue }; try { $out += ($l | ConvertFrom-Json) } catch { } }
    return $out
}
# Summary of the brain usage: the last call, and number of calls / input / output of today.
function Get-BrainTotals($Cfg) {
    $rows = @(Read-BrainRows $Cfg)
    $today = @($rows | Where-Object { ([datetime]$_.ts).Date -eq (Get-Date).Date })
    $in = 0; $out = 0
    foreach ($r in $today) { if ($null -ne $r.input) { $in += [int64]$r.input }; if ($null -ne $r.output) { $out += [int64]$r.output } }
    $lastCall = @($rows | Where-Object { $null -ne $_.input } | Select-Object -Last 1)
    return [pscustomobject]@{ Calls = $today.Count; Input = $in; Output = $out; Last = $(if ($rows.Count) { $rows[-1] } else { $null }); LastCall = $(if ($lastCall.Count) { $lastCall[0] } else { $null }) }
}
# Writes the orchestrator state (status text, round number, next round time) for the dashboard and the pane header.
function Write-OrchState($Cfg, [hashtable]$State) { Write-FileAtomic (Join-Path $Cfg.runtime 'ORCH-STATE.json') (ConvertTo-Json -InputObject $State -Compress) }
# Reads the orchestrator state; $null when missing.
function Read-OrchState($Cfg) {
    $p = Join-Path $Cfg.runtime 'ORCH-STATE.json'
    if (-not (Test-Path $p)) { return $null }
    try { return (Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
}

# Appends one event line (HH:mm|E or O|text) to ORCH-EVENTS.log; the orchestrator pane shows exactly these lines.
function Write-OrchEvent($Cfg, [string]$Text, [bool]$IsError = $false) {
    $line = '{0}|{1}|{2}' -f (Get-Date -Format 'HH:mm'), $(if ($IsError) { 'E' } else { 'O' }), ($Text -replace '\s+', ' ').Trim()
    Add-Content -Path (Join-Path $Cfg.runtime 'ORCH-EVENTS.log') -Value $line -Encoding UTF8
}
# All agent rows: the live AGENTS.json plus the archive AGENTS-ARCHIVE.json (history for package states).
function Read-AllAgents($Cfg) {
    $rows = @(Read-Agents $Cfg)
    $ap = Join-Path $Cfg.runtime 'AGENTS-ARCHIVE.json'
    if (Test-Path $ap) { try { $rows = @(ConvertFrom-Json -InputObject (Get-Content $ap -Raw -Encoding UTF8)) + $rows } catch { } }
    return $rows
}


# ---- Follow-up freeze, person names, colors, task text, short model names (2026-10-27) ----

# Key of a package for the freeze list: plan file name (lower case) and package id (lower case).
function Get-FreezeKey([string]$Plan, [string]$Pkg) { return (([string]$Plan).Replace('/', '\').Split('\')[-1].ToLower() + '|' + ([string]$Pkg).ToLower()) }
# Packages that must not get any follow-up task (no verify, work, release, no event for the brain): runtime\NOFOLLOW.json = {"keys":["a-plan.md|a6", ...]}. Written by the coordinator or on request only.
function Get-FrozenKeys($Cfg) {
    $p = Join-Path $Cfg.runtime 'NOFOLLOW.json'
    if (-not (Test-Path $p)) { return @() }
    try { return @((Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json).keys | ForEach-Object { ([string]$_).ToLower() }) } catch { return @() }
}
# True when the package of this plan is frozen (see Get-FrozenKeys).
function Test-Frozen($Cfg, [string]$Plan, [string]$Pkg) { return ((Get-FrozenKeys $Cfg) -contains (Get-FreezeKey $Plan $Pkg)) }

# Fixed list of short, non-technical first names for agents (the orchestrator itself is always CHIEF).
$script:AgentNames = @('Max', 'Fritz', 'Lena', 'Anna', 'Jonas', 'Mia', 'Paul', 'Emma', 'Finn', 'Hanna', 'Ben', 'Clara', 'Leo', 'Nora', 'Tim', 'Ida', 'Karl', 'Rosa', 'Otto', 'Vera', 'Emil', 'Greta', 'Jan', 'Lotte')
# Fixed palette of clearly distinct colors (RGB hex) that are not used for status (green, yellow, red, grey) and not for CHIEF.
$script:AgentColors = @('4FA8FF', 'C77DFF', 'FF6FB5', '3DDCDC', 'B39DDB', 'D7A86E', '8EA2C8', 'E040FB', '6FA8DC', 'F4A6D7')
$script:ChiefColor = 'FFFFFF'
# First name and color for a new agent: the first free entry of the lists; free = not used by a running agent or one that ended within the last hour.
function Get-AgentIdentity($Cfg) {
    $rows = @(Read-Agents $Cfg | Where-Object { $_.status -eq 'running' -or $_.status -eq 'starting' -or ($_.endedAt -and ((Get-Date) - [datetime]$_.endedAt).TotalHours -le 1) })
    $usedN = @($rows | ForEach-Object { [string]$_.person }); $usedC = @($rows | ForEach-Object { [string]$_.color })
    $n = @($script:AgentNames | Where-Object { $usedN -notcontains $_ } | Select-Object -First 1)
    $c = @($script:AgentColors | Where-Object { $usedC -notcontains $_ } | Select-Object -First 1)
    return @{ Person = $(if ($n.Count) { $n[0] } else { $script:AgentNames[$rows.Count % $script:AgentNames.Count] }); Color = $(if ($c.Count) { $c[0] } else { $script:AgentColors[$rows.Count % $script:AgentColors.Count] }) }
}
# Short model text for the first display line: "Son 5.5 med." / "Sol 6.1 med.".
function Get-ModelShort([string]$Type, [string]$Model, [string]$Effort) {
    $eff = switch -Regex ($Effort) { '^medium$' { 'med.' } default { $Effort } }
    if ($Type -eq 'codex') { if ($Model -match '^gpt-(\d+(?:\.\d+)?)-([a-z]+)') { return ((Get-Culture).TextInfo.ToTitleCase($Matches[2]) + ' ' + $Matches[1] + ' ' + $eff) }; return ($Model + ' ' + $eff) }
    $v = ''
    if ($Model -match 'sonnet') { $nm = 'Son' } elseif ($Model -match 'opus') { $nm = 'Opus' } elseif ($Model -match 'haiku') { $nm = 'Hai' } else { $nm = $Model }
    $ver = @{ sonnet = '5.5'; opus = '5.5'; haiku = '5.5' }
    foreach ($k in $ver.Keys) { if ($Model -match $k) { $v = $ver[$k] } }
    if ($Model -match '(\d+)[-.](\d+)') { $v = $Matches[1] + '.' + $Matches[2] }
    return (($nm + ' ' + $v).Trim() + ' ' + $eff)
}
# Long model text for pane titles: "Claude Sonnet 5.5 medium" / "Codex GPT-6.1-Sol medium" (type, model and effort written out).
function Get-ModelLong([string]$Type, [string]$Model, [string]$Effort) {
    if ($Type -eq 'codex') {
        if ($Model -match '^gpt-(.+)$') { $parts = @($Matches[1] -split '-' | ForEach-Object { (Get-Culture).TextInfo.ToTitleCase($_) }); return ('Codex GPT-' + ($parts -join '-') + ' ' + $Effort) }
        return ('Codex ' + $Model + ' ' + $Effort)
    }
    $nm = $Model; $v = ''
    if ($Model -match 'sonnet') { $nm = 'Sonnet'; $v = '5.5' } elseif ($Model -match 'opus') { $nm = 'Opus'; $v = '5.5' } elseif ($Model -match 'haiku') { $nm = 'Haiku'; $v = '5.5' }
    if ($Model -match '(\d+)[-.](\d+)') { $v = $Matches[1] + '.' + $Matches[2] }
    return (('Claude ' + $nm + ' ' + $v).Replace('  ', ' ').Trim() + ' ' + $Effort)
}
# Common start of every pane title of the orchestrator window; test windows (own test runtime) say TEST so they are never mixed up with the live one.
function Get-PaneTitlePrefix {
    if ($env:ORCH_RUNTIME_OVERRIDE) { return 'Agent-Orchestrator TEST' }
    return 'Agent-Orchestrator'
}
# Pane title of one agent: "<prefix> | Agent <NAME> | job: <agent id> | model: <type model effort>" (the tab shows the title of the focused pane, so each pane carries the full text).
function Get-AgentPaneTitle($Cfg, [hashtable]$A) {
    $row = @(Read-Agents $Cfg | Where-Object { $_.name -eq $A.name } | Select-Object -First 1)
    $person = if ($row.Count -and $row[0].person) { ([string]$row[0].person).ToUpper() } else { '?' }
    $cls = if ($row.Count -and $row[0].class) { [string]$row[0].class } else { 'medium' }
    $cm = Get-ClassModel $Cfg ([string]$A.type) $cls
    return ((Get-PaneTitlePrefix) + ' | Agent ' + $person + ' | job: ' + $A.name + ' | model: ' + (Get-ModelLong ([string]$A.type) $cm.Model $cm.Effort))
}
# The task in plain words (one line): work = package row of the plan's package table; verify / release / integrate = phase plus package; else the first line of the prompt file.
function Get-AgentTask($Cfg, [string]$Plan, [string]$Package, [string]$Phase, [string]$PromptFile) {
    $planShort = [System.IO.Path]::GetFileNameWithoutExtension($Plan) -replace '-?PLAN$', ''
    $pkgTxt = if ($Package -and $Package -ne 'ALL' -and $Package -notmatch '^(REL|INT-)') { "Paket $Package" } else { 'ganzer Plan' }
    if ($Phase -eq 'work' -and $Package -match '^Q-(.+)$') { return ('Beantwortete Frage ' + $Matches[1] + ': ' + [string]$Cfg.owner + 's Antwort umsetzen') }
    if ($Phase -eq 'work' -and $Package -match '^FIX-(.+)$') { return ('Blockierte Integration beheben (Spitze ' + $Matches[1] + ')') }
    switch ($Phase) {
        'verify' { return "Verify $pkgTxt, Vollst" + [char]0xE4 + 'ndigkeitspr' + [char]0xFC + 'fung' }
        'release' { return "Release: Plan-Branch mergen, Kompiliertest, ready-Zeile ($planShort)" }
        'integrate' { return 'Integrator: ready-Zeilen mergen, Build, Checks, Push' }
    }
    try {
        $abs = if ([System.IO.Path]::IsPathRooted($Plan)) { $Plan } else { Join-Path $Cfg.repo $Plan }
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
    if ($PromptFile -and (Test-Path $PromptFile)) {
        $pl = @(Get-Content $PromptFile -Encoding UTF8 | ForEach-Object { $_.TrimStart([char]0xFEFF) })
        $ti = [array]::IndexOf($pl, 'TASK')
        $cand = @(if ($ti -ge 0) { $pl | Select-Object -Skip ($ti + 1) | Where-Object { $_.Trim() } } else { $pl | Where-Object { $_.Trim() } })
        if ($cand.Count) { return ("Plan $planShort, Paket ${Package}: " + (($cand[0] -replace '\s+', ' ').Trim())) }
    }
    return "Plan $planShort, Paket $Package"
}
# Third display line: "Kontext 44k | Total Input 44k / Output 95", or "Tokens: noch keine Daten" while no token data exists yet (null or all zero).
function Format-TokenLine($Ctx, $In, $Out) {
    $has = ($null -ne $In -and [int64]$In -gt 0) -or ($null -ne $Out -and [int64]$Out -gt 0) -or ($null -ne $Ctx -and [int64]$Ctx -gt 0)
    if (-not $has) { return 'Tokens: noch keine Daten' }
    function T($n) { if ($null -eq $n) { return [string][char]0x2013 }; if ($n -ge 1000000) { return ('{0:N1}M' -f ($n / 1e6)) }; if ($n -ge 1000) { return ('{0:N0}k' -f ($n / 1e3)) }; return "$n" }
    return ('Kontext ' + (T $Ctx) + ' | Total Input ' + (T $In) + ' / Output ' + (T $Out))
}
# Returns the text of the first markdown block whose heading line starts with $Prefix (e.g. '## Builds'); the block runs up to the next heading of the same or a higher level. Headings inside code fences are ignored. Empty string when no heading matches.
function Get-MarkdownBlock([string[]]$Lines, [string]$Prefix) {
    $level = ($Prefix -replace '^(#+).*$', '$1').Length
    $start = -1; $fence = $false
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match '^\s*```') { $fence = -not $fence; continue }
        if (-not $fence -and $Lines[$i].StartsWith($Prefix)) { $start = $i; break }
    }
    if ($start -lt 0) { return '' }
    $end = $Lines.Count; $fence = $false
    for ($j = $start + 1; $j -lt $Lines.Count; $j++) {
        if ($Lines[$j] -match '^\s*```') { $fence = -not $fence; continue }
        if (-not $fence -and $Lines[$j] -match '^(#+) ' -and $Matches[1].Length -le $level) { $end = $j; break }
    }
    return (($Lines[$start..($end - 1)] -join "`n").TrimEnd())
}

# Writes the rule file of one agent phase (S9, plan 12.1): only the sections listed for the phase in config.json "phaseRules", copied verbatim from the rule sources "ruleSources" (global CLAUDE.md, repository CLAUDE.md, ORCHESTRATOR.md). Returns the number of sections written; a listed section that does not exist throws, so a renamed heading never silently drops a rule.
function Write-PhaseRules($Cfg, [string]$Phase, [string]$OutFile) {
    $sel = $Cfg.phaseRules.$Phase
    if ($null -eq $sel) { throw "no phaseRules for phase '$Phase' in config.json" }
    $parts = @(); $n = 0
    foreach ($srcName in @($sel.PSObject.Properties.Name)) {
        $path = Resolve-RuleSourcePath $Cfg $srcName
        $lines = [System.IO.File]::ReadAllLines($path, [System.Text.Encoding]::UTF8)
        foreach ($pre in @($sel.$srcName)) {
            $b = Get-MarkdownBlock $lines ([string]$pre)
            if (-not $b) { throw "rule section '$pre' not found in $path" }
            $parts += $b; $n++
        }
    }
    [System.IO.File]::WriteAllText($OutFile, ("# Rules for this agent phase ($Phase)`n`n" + ($parts -join "`n`n") + "`n"), (New-Object System.Text.UTF8Encoding($true)))
    return $n
}

# Effort classes (plan 12.2): index of a class name in light < medium < heavy; empty or unknown = medium, so plans without a Class column run unchanged.
function Get-ClassIndex([string]$Class) {
    $i = [array]::IndexOf(@('light', 'medium', 'heavy'), ([string]$Class).Trim().ToLower())
    if ($i -lt 0) { return 1 }
    return $i
}

# Final class of one agent start (plan 12.4): the requested class is raised by one class for every config "escalateAfter" failures of that package (technical failure or verify with open items), but never above heavy, and then raised to the phase minimum of config "minClass" (verify and integrator never below medium).
function Resolve-AgentClass($Cfg, [string]$Phase, [string]$Requested, [int]$Failures = 0) {
    $i = Get-ClassIndex $Requested
    $every = [Math]::Max(1, [int]$Cfg.escalateAfter)
    $i = [Math]::Min(2, $i + [int][Math]::Floor($Failures / $every))
    $min = $Cfg.minClass.$Phase
    if ($min) { $i = [Math]::Max($i, (Get-ClassIndex ([string]$min))) }
    return @('light', 'medium', 'heavy')[$i]
}

# Model and effort of an agent type for a class, from config "classes" (an unknown class counts as medium).
function Get-ClassModel($Cfg, [string]$Type, [string]$Class) {
    $c = @('light', 'medium', 'heavy')[(Get-ClassIndex $Class)]
    $m = $Cfg.classes.$Type.$c
    return [pscustomobject]@{ Model = [string]$m.model; Effort = [string]$m.effort }
}

# True when the text is valid JSON (the check every write of config.json or a project config must pass first).
function Test-JsonText([string]$Text) {
    try { $null = $Text | ConvertFrom-Json -ErrorAction Stop; return $true } catch { return $false }
}

# Writes a JSON file only when the new text is valid JSON; invalid text throws and leaves the old file untouched (a broken config.json stops every script and every agent start).
function Save-JsonFile([string]$Path, [string]$Text) {
    if (-not (Test-JsonText $Text)) { throw "refusing to save $Path : the text is not valid JSON" }
    Write-FileAtomic $Path $Text
}

# Failures of one package (technical failure of a work agent except "pane never started", or a verify with open items) counted over all agents; used by the escalation and the lock (plan 12.4).
function Get-PackageFailures($Cfg, $Agents, [string]$Plan, [string]$Pkg) {
    $norm = { param($p) ([string]$p).Replace('\', '/').ToLower() }
    $mine = @($Agents | Where-Object { (& $norm $_.plan) -eq (& $norm $Plan) -and ([string]$_.package -eq $Pkg -or @($_.bundle) -contains $Pkg) })
    $f = @($mine | Where-Object { $_.phase -eq 'work' -and $_.status -eq 'failed' -and [string]$_.lastLine -notmatch '^pane never started' }).Count
    $o = 0
    foreach ($v in @($mine | Where-Object { $_.phase -eq 'verify' -and $_.status -eq 'done' })) {
        $ff = Join-Path $Cfg.runtime ('runs\' + $v.name + '\final.txt')
        $txt = if (Test-Path $ff) { Get-Content $ff -Raw -Encoding UTF8 } else { '' }
        if ($txt -match 'Offene Anforderungen:\s*(\d+)' -and [int]$Matches[1] -gt 0) { $o++ }
    }
    return ($f + $o)
}

# True when the last verify of the package reported open items more often than escalateAfter (so work on it is locked and reported as a question).
function Test-PackageLocked($Cfg, [string]$Plan, [string]$Pkg) {
    if ($Pkg -match '^(Q|FIX)-') { return $false }
    $all = @(Read-Agents $Cfg)
    $mine = @($all | Where-Object { ([string]$_.plan).Replace('\', '/').ToLower() -eq ([string]$Plan).Replace('\', '/').ToLower() -and ([string]$_.package -eq $Pkg -or @($_.bundle) -contains $Pkg) } | Sort-Object { [string]$_.startedAt })
    if ($mine.Count -eq 0) { return $false }
    $last = $mine[-1]
    if ($last.phase -ne 'verify') { return $false }
    return ((Get-PackageFailures $Cfg $all $Plan $Pkg) -gt [int]$Cfg.escalateAfter)
}

# Class of a work start, decided centrally for routine and brain starts: the class of the package row in the plan table (or the class sent with the action), raised by the escalation of its failures (plan 12.4), then the phase minimum.
function Get-StartClass($Cfg, [string]$Phase, [string]$Plan, [string]$Pkg, [string]$Sent) {
    $base = Get-ClassIndex $Sent
    $failures = 0
    if ($Phase -eq 'work') {
        $planAbs = if ([IO.Path]::IsPathRooted($Plan)) { $Plan } else { Join-Path $Cfg.repo $Plan }
        if (Get-Command Get-PlanPackages -ErrorAction SilentlyContinue) {
            $row = @(Get-PlanPackages $planAbs | Where-Object { $_.Id -eq $Pkg } | Select-Object -First 1)
            if ($row.Count) { $base = Get-ClassIndex $row[0].Class }   # the table class is the base: the class sent with the action may already be escalated and must not be raised twice
        }
        $failures = Get-PackageFailures $Cfg @(Read-Agents $Cfg) $Plan $Pkg
    }
    return (Resolve-AgentClass $Cfg $Phase @('light', 'medium', 'heavy')[$base] $failures)
}
