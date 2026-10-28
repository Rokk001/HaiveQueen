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

# Name of a global mutex: 'Global\' + the project's mutexPrefix + the lock name. A run with a test runtime (ORCH_RUNTIME_OVERRIDE) adds a short hash of that runtime
# path, so a test never holds or waits for a lock of the live system (the live name stays exactly as before).
function Get-MutexName([string]$Prefix, [string]$Lock) {
    $tag = ''
    if ($env:ORCH_RUNTIME_OVERRIDE) {
        $sha = [System.Security.Cryptography.SHA1]::Create()
        $tag = 'T' + ([BitConverter]::ToString($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($env:ORCH_RUNTIME_OVERRIDE.ToLowerInvariant()))).Replace('-', '').Substring(0, 8))
    }
    return 'Global\' + $Prefix + $tag + $Lock
}

# Runs a script block while holding the agents mutex (guards the pane slot counter file) (the mutex name starts with the project's mutexPrefix).
# The wait is limited (ORCH_LOCK_TIMEOUT_SEC, default 120 s) and throws instead of waiting forever; a lock abandoned by an exited process counts as free.
function Invoke-Locked([scriptblock]$Block) {
    $mutexName = Get-MutexName ([string](Get-ProjCfg).mutexPrefix) 'AgentsJson'
    $limit = if ($env:ORCH_LOCK_TIMEOUT_SEC) { [int]$env:ORCH_LOCK_TIMEOUT_SEC } else { 120 }
    $m = New-Object System.Threading.Mutex($false, $mutexName)
    $got = $false
    try { $got = $m.WaitOne($limit * 1000) } catch [System.Threading.AbandonedMutexException] { $got = $true }
    if (-not $got) { throw "agents lock '$mutexName' not free within $limit s" }
    try { & $Block } finally { $m.ReleaseMutex() }
}
# Maps the snake_case column names of an agent row of the database to the camelCase names the scripts used in AGENTS.json.
$script:AgentColumnNames = @{ plan_path = 'plan'; started_at = 'startedAt'; ended_at = 'endedAt'; last_line = 'lastLine'; pane_id = 'paneId'; exit_code = 'exitCode'; tokens_in = 'tokensIn'; tokens_out = 'tokensOut' }
# Turns one agent row of the database into the flat row the scripts always saw (camelCase names, "bundle" as array); empty columns are left out like missing properties.
function ConvertFrom-OrchAgentRow($R) {
    $o = [ordered]@{}
    foreach ($p in $R.PSObject.Properties) {
        if ($p.Name -in @('id', 'archived', 'extra_json') -or $null -eq $p.Value) { continue }
        if ($p.Name -eq 'bundle_json') { if (-not $R.PSObject.Properties['bundle']) { try { $o['bundle'] = @(ConvertFrom-Json -InputObject ([string]$p.Value)) } catch {} }; continue }
        $n = if ($script:AgentColumnNames.ContainsKey($p.Name)) { $script:AgentColumnNames[$p.Name] } else { $p.Name }
        $o[$n] = $p.Value
    }
    return [pscustomobject]$o
}
# Turns an agent row (or hashtable) back into the field hashtable of Set-OrchAgent (row bookkeeping fields are dropped).
function ConvertTo-OrchAgentFields($Row) {
    $h = @{}
    foreach ($p in $Row.PSObject.Properties) { if ($p.Name -notin @('id', 'project', 'archived', 'extra_json', 'name')) { $h[$p.Name] = $p.Value } }
    return $h
}
# Reads the active agent rows of the project from the database as a flat array of agent rows (same shape as the old AGENTS.json rows).
function Read-Agents($Cfg) {
    return @(Get-OrchAgents $Cfg | ForEach-Object { ConvertFrom-OrchAgentRow $_ })
}
# Writes an agent list back: every row with a name is created or updated in the database (rows missing from the list are not removed; use Move-AgentsToArchive for that).
function Write-Agents($Cfg, $List) {
    foreach ($r in @($List | Where-Object { $_ -and $_.PSObject.Properties['name'] -and $_.name })) { [void](Set-OrchAgent $Cfg ([string]$r.name) (ConvertTo-OrchAgentFields $r)) }
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

# Marks agent rows as archived in the database (they leave the active list but stay in the history).
# Without -Match: every row that ended more than $Hours hours ago (running rows always stay). With -Match: every row whose plan or name matches the regex. Returns the number of moved rows.
function Move-AgentsToArchive($Cfg, [double]$Hours = 12, [string]$Match = '') {
    return [int](Move-OrchAgentsToArchive $Cfg $Hours $Match)
}

# Creates or updates the row of one agent (hashtable of properties); the database makes the change in one transaction.
function Update-Agent($Cfg, [string]$Name, [hashtable]$Props) {
    [void](Set-OrchAgent $Cfg $Name $Props)
}

# Update-Agent with up to $Attempts tries (a failing call throws). When the last try fails, the row is written once more as status 'failed' with the reason,
# so a row never stays on 'starting'/'running' because of a database problem. Returns $true when the requested properties were written, $false when the failure fallback was used.
function Update-AgentRetry($Cfg, [string]$Name, [hashtable]$Props, [int]$Attempts = 3) {
    $err = ''
    for ($i = 1; $i -le $Attempts; $i++) {
        try { Update-Agent $Cfg $Name $Props; return $true } catch { $err = $_.Exception.Message; if ($i -lt $Attempts) { Start-Sleep -Seconds 2 } }
    }
    try { Write-RoundLog $Cfg "warning $Name could not update its row after $Attempts tries, set to failed: $err" } catch {}
    $f = @{}
    foreach ($k in $Props.Keys) { $f[$k] = $Props[$k] }
    $f['status'] = 'failed'
    $f['endedAt'] = (Get-Date).ToString('o')
    $f['lastLine'] = "agents lock error after $Attempts tries (wanted status '" + [string]$Props['status'] + "'): " + $err
    Update-Agent $Cfg $Name $f
    return $false
}

# Appends one timestamped line to the history and to the ROUNDS.log text mirror.
function Write-RoundLog($Cfg, [string]$Text) {
    [void](Add-OrchEvent $Cfg $Text 'rounds')
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
# Reads the questions of the project from the database as blocks (id "Q-<number>", plan, item, status OPEN or ANSWERED, answer).
function Get-Questions($Cfg) {
    $res = @()
    foreach ($q in @(Get-OrchQuestions $Cfg)) {
        $st = if ([string]$q.status -eq 'open') { 'OPEN' } elseif ([string]$q.status -eq 'answered') { 'ANSWERED' } else { ([string]$q.status).ToUpper() }
        $res += [ordered]@{ id = ('Q-' + [string]$q.id); plan = [string]$q.plan_path; item = [string]$q.item; status = $st; answer = [string]$q.answer }
    }
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
    $mutexName = Get-MutexName ([string](Get-ProjCfg).mutexPrefix) 'WtPaneCommands'
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
            [void](Update-OrchAgentFields $Cfg ([string]$half.name) @{ paneCols = $cols; paneRows = $rows })   # estimate until the pane reports its real size
        }
        $mine = @(Read-Agents $Cfg | Where-Object { $_.name -eq $Name } | Select-Object -First 1)
        if ($mine.Count) { [void](Update-OrchAgentFields $Cfg $Name @{ paneId = $next; paneCols = $cols; paneRows = $rows; paneClosed = $false }) }
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

# Remembers the newest Claude usage windows (from a rate_limit_event of any headless claude run) in the kv key usage-claude.
function Save-ClaudeUsage($Cfg, $Event) {
    $w = $Event.rate_limit_info.unifiedWindows
    if (-not $w) { return }
    $o = [ordered]@{ saved = (Get-Date).ToString('o'); fiveHour = $w.five_hour; sevenDay = $w.seven_day }
    Set-OrchKv $Cfg 'usage-claude' ($o | ConvertTo-Json -Depth 5)
}

# Claude 5h / 7d usage: @{FiveHour=@{Pct;Reset}; SevenDay=@{Pct;Reset}}; values are $null when unknown or the window has already reset.
function Get-ClaudeUsage($Cfg) {
    $res = @{ FiveHour = $null; SevenDay = $null }
    try {
        $raw = Get-OrchKv $Cfg 'usage-claude'
        if ([string]::IsNullOrWhiteSpace($raw)) { return $res }
        $j = $raw | ConvertFrom-Json
        $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        if ($j.fiveHour) { if ([int64]$j.fiveHour.resetsAt -gt $now) { $res.FiveHour = @{ Pct = [math]::Round(100 * [double]$j.fiveHour.utilization); Reset = (Format-ResetTime $j.fiveHour.resetsAt) } } else { $res.FiveHour = @{ Pct = $null; Reset = 'neues Fenster' } } }
        if ($j.sevenDay) { if ([int64]$j.sevenDay.resetsAt -gt $now) { $res.SevenDay = @{ Pct = [math]::Round(100 * [double]$j.sevenDay.utilization); Reset = (Format-ResetTime $j.sevenDay.resetsAt); At = [int64]$j.sevenDay.resetsAt } } else { $res.SevenDay = @{ Pct = $null; Reset = 'neues Fenster' } } }
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
                $v = @{ Pct = [math]::Round([double]$w.used_percent); Reset = (Format-ResetTime $w.resets_at); At = [int64]$w.resets_at }
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
# Stores one brain call (a hashtable or object: ts, trigger, model, context, input, output, durationSec, actions, error) as a usage row of kind brain.
function Add-BrainRow($Cfg, $Row) {
    $h = @{ kind = 'brain' }
    $names = @{ ts = 'ts'; trigger = 'trigger'; model = 'model'; context = 'context'; input = 'tokensIn'; output = 'tokensOut'; durationSec = 'durationS'; actions = 'actions'; error = 'error' }
    foreach ($k in $names.Keys) {
        $v = if ($Row -is [System.Collections.IDictionary]) { $Row[$k] } else { $Row.$k }
        if ($null -ne $v) { $h[$names[$k]] = $(if ($k -eq 'ts') { ConvertTo-OrchTime $v } elseif ($k -in @('actions', 'trigger', 'model')) { [string]$v } else { $v }) }
    }
    Add-OrchUsage $Cfg $h
}
# Reads all brain usage rows of the project (oldest first) in the old row shape: ts, trigger, model, context, input, output, durationSec, actions, error.
function Read-BrainRows($Cfg) {
    $out = @()
    foreach ($u in @(Get-OrchUsage $Cfg 'brain')) {
        $o = [ordered]@{ ts = $u.ts; trigger = $u.trigger; model = $u.model; context = $u.context; input = $u.tokens_in; output = $u.tokens_out; durationSec = $u.duration_s; actions = $u.actions; error = [bool]$u.error }
        $out += [pscustomobject]$o
    }
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
# Writes the orchestrator state (status text, round number, next round time) for the dashboard and the pane header (kv key loop-state, JSON text).
function Write-OrchState($Cfg, [hashtable]$State) { Set-OrchKv $Cfg 'loop-state' (ConvertTo-Json -InputObject $State -Compress) }
# Reads the orchestrator state; $null when missing.
function Read-OrchState($Cfg) {
    try { $raw = Get-OrchKv $Cfg 'loop-state'; if ([string]::IsNullOrWhiteSpace($raw)) { return $null }; return ($raw | ConvertFrom-Json) } catch { return $null }
}

# Adds one event line (HH:mm|E or O|text) to the history and to the ORCH-EVENTS.log mirror; the orchestrator pane shows exactly these lines.
function Write-OrchEvent($Cfg, [string]$Text, [bool]$IsError = $false) {
    [void](Add-OrchEvent $Cfg $Text 'events' $IsError)
}
# All agent rows: the archived rows (history for package states) followed by the active rows.
function Read-AllAgents($Cfg) {
    return @(@(Get-OrchAgents $Cfg 'archive' | ForEach-Object { ConvertFrom-OrchAgentRow $_ }) + @(Read-Agents $Cfg))
}

# ---- Follow-up freeze, person names, colors, task text, short model names (2026-10-27) ----

# Key of a package for the freeze list: plan file name (lower case) and package id (lower case).
function Get-FreezeKey([string]$Plan, [string]$Pkg) { return (([string]$Plan).Replace('/', '\').Split('\')[-1].ToLower() + '|' + ([string]$Pkg).ToLower()) }
# Packages that must not get any follow-up task (no verify, work, release, no event for the brain): the nofollow keys of the project, e.g. "a-plan.md|a6". Set by the coordinator or on request only.
function Get-FrozenKeys($Cfg) {
    return @(Get-OrchNofollow $Cfg | ForEach-Object { ([string]$_).ToLower() })
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
        # the title of the package row in the packages table (filled from the plan header)
        if (Get-Command Get-PlanPackages -ErrorAction SilentlyContinue) {
            $k = @(Get-PlanPackages $Cfg (Resolve-PlanPath $Cfg $Plan) | Where-Object { $_.Id -eq $Package } | Select-Object -First 1)
            if ($k.Count -and $k[0].Content) { return ($Package + ': ' + $k[0].Content) }
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

# ---- Providers (plan section 15) ----
# A provider is one entry of the project file list "providers": name, protocol (claude or codex: the event format of the CLI, default = name), command, args (template), classes (light/medium/heavy: model + effort),
# capabilities (e.g. "images"), slots (how many tasks may run at once; 0 = off for everything), use (preferred | fallback | capability-only), limit.source (claude | codex | none: where the weekly usage comes from),
# endpoint (optional OpenAI-compatible server: baseUrl, id, name; runs through the codex CLI with its own model_provider). A project file without "providers" gets the two classic entries from the old keys.
function ConvertTo-StringArray($v) { $o = @(); if ($null -ne $v) { foreach ($x in @($v)) { $o += [string]$x } }; return $o }
function Get-Providers($Cfg) {
    $src = @()
    if ($Cfg.providers) { $src = @($Cfg.providers) }
    else {
        $src += [pscustomobject]@{ name = 'claude'; command = 'claude'; args = $Cfg.claudeArgs; slots = $Cfg.claudeSlots; use = 'preferred'; capabilities = @(); classes = $Cfg.classes.claude; limit = [pscustomobject]@{ source = 'claude' } }
        $src += [pscustomobject]@{ name = 'codex'; command = 'codex'; args = $Cfg.codexArgs; slots = $Cfg.codexSlots; use = 'preferred'; capabilities = @('images'); classes = $Cfg.classes.codex; limit = [pscustomobject]@{ source = 'codex' } }
    }
    $out = @()
    foreach ($p in $src) {
        $name = [string]$p.name
        if (-not $name) { throw 'a provider entry has no name' }
        $use = if ($p.use) { [string]$p.use } else { 'preferred' }
        if ($use -notin @('preferred', 'fallback', 'capability-only')) { throw "provider ${name}: use must be preferred, fallback or capability-only, not '$use'" }
        $proto = if ($p.protocol) { [string]$p.protocol } else { $name }
        if ($proto -notin @('claude', 'codex')) { throw "provider ${name}: protocol must be claude or codex, not '$proto'" }
        $out += [pscustomobject]@{
            Name = $name; Protocol = $proto; Command = $(if ($p.command) { [string]$p.command } else { $proto }); Args = (ConvertTo-StringArray $p.args)
            Slots = [int]$p.slots; Use = $use; Capabilities = (ConvertTo-StringArray $p.capabilities); Classes = $p.classes
            Limit = $(if ($p.limit -and $p.limit.source) { [string]$p.limit.source } else { '' }); Endpoint = $p.endpoint
        }
    }
    return $out
}
# One provider by name; $null when the project file has no such entry.
function Get-Provider($Cfg, [string]$Name) { return (@(Get-Providers $Cfg | Where-Object { $_.Name -eq $Name }) | Select-Object -First 1) }
# Provider name of an AGENTS.json row: the field "provider", for rows from before plan section 15 the old field "type".
function Get-RowProvider($Row) { if ($Row.PSObject.Properties['provider'] -and $Row.provider) { return [string]$Row.provider }; return [string]$Row.type }
# Free slots per provider name: its slots minus the running or starting worker agents of that provider (integrator agents use no slot); never below 0.
function Get-ProviderFree($Cfg, $Rows) {
    $free = @{}
    foreach ($p in (Get-Providers $Cfg)) {
        $used = @($Rows | Where-Object { ($_.status -eq 'running' -or $_.status -eq 'starting') -and $_.phase -ne 'integrate' -and (Get-RowProvider $_) -eq $p.Name }).Count
        $free[$p.Name] = [Math]::Max(0, $p.Slots - $used)
    }
    return $free
}
# Weekly limit of a provider as @{ FreePct; Hours }: 100 minus the used percent, and the hours until the reset. No limit source, no reading or an unknown value counts as 100 % free with a reset in 168 h
# (the 100 % for "no weekly limit" is the coordinator's value, not the owner's). Tests: ORCH_PROVIDER_LIMITS_FILE = {"<provider>":{"usedPercent":80,"resetHours":24}}; with a test runtime and no such file nothing is read from this PC.
function Get-ProviderLimit($Cfg, $P) {
    $used = $null; $hours = $null
    $lf = $env:ORCH_PROVIDER_LIMITS_FILE
    if ($lf -and (Test-Path -LiteralPath $lf)) {
        $t = Get-Content -LiteralPath $lf -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($t.PSObject.Properties[$P.Name]) { $used = $t.($P.Name).usedPercent; $hours = $t.($P.Name).resetHours }
    }
    elseif (-not $env:ORCH_RUNTIME_OVERRIDE -and $P.Limit -in @('claude', 'codex')) {
        $u = if ($P.Limit -eq 'claude') { (Get-ClaudeUsage $Cfg).SevenDay } else { (Get-CodexUsage).SevenDay }
        if ($u -and $null -ne $u.Pct) { $used = $u.Pct; if ($u.At) { $hours = ([double]$u.At - [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) / 3600 } }
    }
    if ($null -eq $used) { return @{ FreePct = 100.0; Hours = 168.0 } }
    return @{ FreePct = [Math]::Max(0.0, 100.0 - [double]$used); Hours = $(if ($null -ne $hours) { [double]$hours } else { 168.0 }) }
}
# Capabilities a package needs, from its content text, by the config list "capabilityRules" (each rule: capability + patterns that must ALL match). Without the list the graphics rule applies:
# models, textures, materials, particles, icons, clips or sounds together with Blender, assets or graphics need "images".
# A capability named in the plan header (package field "capability", comma separated) is used as it stands; only a package without one falls back to the content rules.
function Get-PackageNeeds($Cfg, [string]$Content, [string]$Capability = '') {
    $declared = @(([string]$Capability).Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($declared.Count) { return $declared }
    $rules = if ($Cfg.capabilityRules) { @($Cfg.capabilityRules) } else { @([pscustomobject]@{ capability = 'images'; patterns = @('(?i)\b(models?|textures?|materials?|particles?|icons?|clips?|sounds?)\b', '(?i)\b(Blender|assets?|graphics?)\b') }) }
    $need = @()
    foreach ($r in $rules) { $ok = $true; foreach ($pat in @($r.patterns)) { if ($Content -notmatch [string]$pat) { $ok = $false; break } }; if ($ok -and $need -notcontains [string]$r.capability) { $need += [string]$r.capability } }
    return $need
}
# True when at least one provider with ALL the capabilities has slots above 0 (slots 0 = off for everything).
function Test-CapabilityAvailable($Cfg, [string[]]$Needs) {
    return (@(Get-Providers $Cfg | Where-Object { $p = $_; $p.Slots -gt 0 -and @($Needs | Where-Object { $p.Capabilities -notcontains $_ }).Count -eq 0 }).Count -gt 0)
}
# The provider that gets a task of this class, or $null. Candidates: slots above 0 and a free slot ($Free: name -> free slots), the class in its entry, all needed capabilities (a package that needs a capability goes only to providers
# that have it, and the weekly limit does not matter then), and use "capability-only" only for packages that need a capability. Without a need, providers at their weekly limit drop out.
# Order: "preferred" and "capability-only" first; "fallback" only when no such candidate has a free slot. Among them the highest (100 - used percent) / hours to reset wins, then list order.
# -Prefer keeps a follow-up task with the provider of the earlier task when it is a candidate; -Avoid (verify) prefers another provider but accepts it when no other is left; -Protocol limits to claude or codex providers (release, integrator).
function Select-Provider($Cfg, [string]$Class, [string[]]$Needs, $Free, [string]$Avoid = '', [string]$Prefer = '', [string]$Protocol = '') {
    $c = @('light', 'medium', 'heavy')[(Get-ClassIndex $Class)]
    $Needs = @($Needs | Where-Object { $_ })   # a $null or empty need means "no need"
    $cand = @()
    $idx = 0
    foreach ($p in (Get-Providers $Cfg)) {
        $idx++
        if ($p.Slots -le 0 -or [int]$Free[$p.Name] -le 0) { continue }
        if ($Protocol -and $p.Protocol -ne $Protocol) { continue }
        if (-not ($p.Classes -and $p.Classes.$c -and $p.Classes.$c.model)) { continue }
        if (@($Needs | Where-Object { $p.Capabilities -notcontains $_ }).Count -gt 0) { continue }
        if ($p.Use -eq 'capability-only' -and @($Needs).Count -eq 0) { continue }
        $lim = Get-ProviderLimit $Cfg $p
        if (@($Needs).Count -eq 0 -and $lim.FreePct -le 0) { continue }
        $cand += [pscustomobject]@{ P = $p; Order = $idx; Score = ($lim.FreePct / [Math]::Max(0.1, $lim.Hours)) }
    }
    if ($cand.Count -eq 0) { return $null }
    $pool = @($cand | Where-Object { $_.P.Use -ne 'fallback' })
    if ($pool.Count -eq 0) { $pool = $cand }
    if ($Prefer) { $hit = @($pool | Where-Object { $_.P.Name -eq $Prefer }); if ($hit.Count) { return $hit[0].P } }
    if ($Avoid) { $other = @($pool | Where-Object { $_.P.Name -ne $Avoid }); if ($other.Count) { $pool = $other } }
    return (@($pool | Sort-Object @{ Expression = 'Score'; Descending = $true }, @{ Expression = 'Order'; Descending = $false }) | Select-Object -First 1).P
}
# Extra arguments of a provider with an endpoint: a model_provider of the codex CLI with the base URL from the project file (-c key=value; Codex 0.163 supports only wire_api "responses").
function Get-EndpointArgs($P) {
    if (-not $P.Endpoint -or -not $P.Endpoint.baseUrl) { return @() }
    $id = if ($P.Endpoint.id) { [string]$P.Endpoint.id } else { $P.Name }
    $nm = if ($P.Endpoint.name) { [string]$P.Endpoint.name } else { $P.Name }
    return @('-c', "model_provider=$id", '-c', "model_providers.$id.name=$nm", '-c', "model_providers.$id.base_url=$($P.Endpoint.baseUrl)", '-c', "model_providers.$id.wire_api=responses")
}

# Model and effort of a provider for a class, from its "classes" entry. A class the entry does not list falls back to the nearest lower class it has, else the nearest higher one.
function Get-ClassModel($Cfg, [string]$Provider, [string]$Class) {
    $p = Get-Provider $Cfg $Provider
    $i = Get-ClassIndex $Class
    $names = @('light', 'medium', 'heavy')
    foreach ($k in (@($i..0) + @(($i + 1)..2))) {
        if ($k -lt 0 -or $k -gt 2) { continue }
        $m = if ($p -and $p.Classes) { $p.Classes.($names[$k]) } else { $null }
        if ($m -and $m.model) { return [pscustomobject]@{ Model = [string]$m.model; Effort = [string]$m.effort } }
    }
    return [pscustomobject]@{ Model = ''; Effort = '' }
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
        if (Get-Command Get-PlanPackages -ErrorAction SilentlyContinue) {
            $row = @(Get-PlanPackages $Cfg (Resolve-PlanPath $Cfg $Plan) | Where-Object { $_.Id -eq $Pkg } | Select-Object -First 1)
            if ($row.Count) { $base = Get-ClassIndex $row[0].Class }   # the table class is the base: the class sent with the action may already be escalated and must not be raised twice
        }
        $failures = Get-PackageFailures $Cfg @(Read-Agents $Cfg) $Plan $Pkg
    }
    return (Resolve-AgentClass $Cfg $Phase @('light', 'medium', 'heavy')[$base] $failures)
}


# Build-job settings of the project file "buildJobs" block with the defaults filled in (plan section 16.1); $null when the project has no such block.
function Get-BuildJobsCfg($Cfg) {
    $b = $Cfg.buildJobs
    if ($null -eq $b) { return $null }
    $share = 2.0 / 3.0
    if ($null -ne $b.coreShare) { $cs = [string]$b.coreShare; if ($cs -match '^\s*(\d+)\s*/\s*(\d+)\s*$') { $share = [double]$Matches[1] / [double]$Matches[2] } else { $share = [double]$cs } }
    $per = $null
    if ($null -ne $b.perJobMB -and [string]$b.perJobMB -ne 'auto') { $per = [double]$b.perJobMB }
    return [pscustomobject]@{
        EnvVar = [string]$b.envVar; CoreShare = $share
        ReserveMB = $(if ($null -ne $b.reserveMB) { [double]$b.reserveMB } else { 1024.0 })
        PerJobMB = $per
        FirstJobs = $(if ($null -ne $b.firstJobs) { [int]$b.firstJobs } else { 2 })
        Fixed = $(if ($null -ne $b.fixed) { [int]$b.fixed } else { $null })
    }
}

# Number of parallel build jobs (plan 16.2): the smaller of (cores x coreShare, rounded up) and ((free RAM - reserve) / use per job, rounded down), at least 1.
# Without any use value (neither configured nor measured) firstJobs applies, never more than the core limit; "fixed" overrides everything. $MeasuredMB is the stored value or $null.
function Get-BuildJobCount($Bj, [int]$Cores, [double]$FreeMB, $MeasuredMB) {
    $coreLimit = [math]::Max(1, [int][math]::Ceiling($Cores * $Bj.CoreShare))
    if ($null -ne $Bj.Fixed) { return [pscustomobject]@{ Jobs = [math]::Max(1, $Bj.Fixed); Why = 'fixed'; PerJobMB = $null } }
    $per = if ($null -ne $Bj.PerJobMB) { $Bj.PerJobMB } elseif ($null -ne $MeasuredMB -and [double]$MeasuredMB -gt 0) { [double]$MeasuredMB } else { $null }
    if ($null -eq $per -or $per -le 0) { return [pscustomobject]@{ Jobs = [math]::Max(1, [math]::Min($Bj.FirstJobs, $coreLimit)); Why = 'first'; PerJobMB = $null } }
    $ramLimit = [int][math]::Floor(($FreeMB - $Bj.ReserveMB) / $per)
    $jobs = [math]::Max(1, [math]::Min($coreLimit, $ramLimit))
    return [pscustomobject]@{ Jobs = $jobs; Why = $(if ($ramLimit -lt $coreLimit) { 'ram' } else { 'cores' }); PerJobMB = $per }
}

# RAM use per job in MB: (free RAM at the start - lowest free RAM seen) / jobs set; never negative (plan 16.3).
function Get-BuildUseMB([double]$StartFreeMB, [double]$MinFreeMB, [int]$Jobs) {
    return [math]::Round([math]::Max(0.0, $StartFreeMB - $MinFreeMB) / [math]::Max(1, $Jobs), 0)
}

# Stored highest use per job (MB) of this host and project, or $null when never measured.
function Get-BuildPerJobMB($Cfg) {
    $v = Get-OrchBuildMax $Cfg
    if ($null -eq $v) { return $null }
    return [double]$v
}

# Stores the measured use per job for this host and project as a build measurement row; the stored value is the highest one ever measured. Returns the value that is stored afterwards.
# The other numbers of the measurement (jobs, free RAM at the start and lowest free RAM, duration in seconds) are optional and default to 0.
function Save-BuildPerJobMB($Cfg, [double]$UseMB, [int]$Jobs = 0, [double]$FreeStartMB = 0, [double]$FreeMinMB = 0, [double]$DurationSec = 0) {
    Add-OrchBuild $Cfg $Jobs $FreeStartMB $FreeMinMB $UseMB $DurationSec
    return [double](Get-OrchBuildMax $Cfg)
}

# Ends the measurement of one build (plan 16.3): reads the 10-second samples, takes the lowest free RAM, computes the use per job, stores the highest value per host and project
# and writes one round log line (jobs, duration, lowest free RAM, use per job). Returns an object with MinFreeMB, UseMB and StoredMB.
function Complete-BuildMeasure($Cfg, [string]$Name, [int]$Jobs, [double]$StartFreeMB, [string]$SampleFile, [datetime]$Started) {
    $min = $StartFreeMB
    if (Test-Path $SampleFile) { foreach ($l in (Get-Content $SampleFile -Encoding UTF8)) { $p = $l -split ';'; if ($p.Count -ge 2) { $v = [double]$p[1]; if ($v -lt $min) { $min = $v } } } }
    $use = Get-BuildUseMB $StartFreeMB $min $Jobs
    $dur = (Get-Date) - $Started
    $stored = Save-BuildPerJobMB $Cfg $use $Jobs $StartFreeMB $min $dur.TotalSeconds
    Write-RoundLog $Cfg ('build {0}: jobs={1}, duration={2}, lowest free RAM={3} MB, use per job={4} MB (stored maximum {5} MB)' -f $Name, $Jobs, $dur.ToString('hh\:mm\:ss'), $min, $use, $stored)
    return [pscustomobject]@{ MinFreeMB = $min; UseMB = $use; StoredMB = $stored }
}

# SYSTEM line of the dashboard (plan 16.4): the job count of the running build (else the count the next build would get now) with the logical cores, the measured use per job and, while a build runs, the cores in use
# (number of running cl.exe, $ClCount overrides the live count for tests); $null without a buildJobs block.
function Get-BuildJobsLine($Cfg, $Agents, $ClCount = $null) {
    $bj = Get-BuildJobsCfg $Cfg
    if (-not $bj) { return $null }
    $per = Get-BuildPerJobMB $Cfg
    $cores = [Environment]::ProcessorCount
    $run = @($Agents | Where-Object { $_.status -eq 'running' -and $_.buildJobs } | Select-Object -First 1)
    if ($run.Count) { $txt = 'Build-Jobs ' + $run[0].buildJobs + ' von ' + $cores + ' Kernen (laufend)' }
    else { $c = Get-BuildJobCount $bj $cores ((Get-FreeRamGB) * 1024) $per; $txt = 'Build-Jobs ' + $c.Jobs + ' von ' + $cores + ' Kernen (naechster Build)' }
    $txt += ' | Verbrauch pro Job ' + $(if ($null -ne $per) { ('{0:0} MB' -f $per) } else { 'noch nicht gemessen' })
    $cl = if ($null -ne $ClCount) { [int]$ClCount } else { @(Get-Process cl -ErrorAction SilentlyContinue).Count }
    if ($run.Count -or $cl -gt 0) { $txt += ' | aktiv: ' + $cl + ' Kerne' }
    return $txt
}

# Wraps a value in double quotes for Start-Process -ArgumentList, which joins the array unquoted (a name with a space or comma would be split into several arguments).
function Quote-Arg([string]$Value) { return ('"' + $Value.Replace('"', '\"') + '"') }

# Folder and branch name from a free text: only lower-case letters, digits and hyphens (everything else becomes one hyphen, no hyphen at the ends); "pkg" when nothing is left.
function ConvertTo-Slug([string]$Text) {
    $s = ([regex]::Replace($Text.ToLowerInvariant(), '[^a-z0-9]+', '-')).Trim('-')
    if (-not $s) { return 'pkg' }
    return $s
}

# ---- v2 runtime wrappers ----
# Thin PowerShell wrappers around the runtime commands of orch.py (see docs/RUNTIME-API.md). Every wrapper takes the project config $Cfg first (the project name
# comes from Get-OrchProject) and returns the parsed JSON; a failing command throws with its error message.

# Name of the project in the database: $env:ORCH_PROJECT_NAME, else the projectName of the config, else the file name of the project file without extension.
function Get-OrchProject($Cfg) {
    if ($env:ORCH_PROJECT_NAME) { return [string]$env:ORCH_PROJECT_NAME }
    if ($Cfg -and $Cfg.PSObject.Properties['projectName'] -and $Cfg.projectName) { return [string]$Cfg.projectName }
    return [IO.Path]::GetFileNameWithoutExtension([string]$Cfg.projectFile)
}

# Escapes backslashes in front of a quote or at the end of the text, then quotes it with Quote-Arg, so the Windows command line parser reads the argument back unchanged.
function ConvertTo-OrchArg([string]$Value) {
    $v = [regex]::Replace($Value, '(\\+)("|$)', { param($m) $m.Groups[1].Value + $m.Groups[1].Value + $m.Groups[2].Value })
    return (Quote-Arg $v)
}

# Runs "python -I orch.py <args>" (python from $env:ORCH_PYTHON, else the python on the PATH; the database folder comes from ORCH_DATA_DIR / the settings), optionally with text on stdin,
# and returns the parsed JSON of stdout. Exit code <> 0 throws with the error text of the command.
# Path of a Python >= 3.11: ORCH_PYTHON, else python on PATH, else the newest python of the py launcher; cached per process.
function Get-OrchPython {
    if ($env:ORCH_PYTHON) { return $env:ORCH_PYTHON }
    if ($script:OrchPythonPath) { return $script:OrchPythonPath }
    $cands = @()
    $c = Get-Command python -ErrorAction SilentlyContinue
    if ($c) { $cands += $c.Source }
    $l = Get-Command py -ErrorAction SilentlyContinue
    if ($l) { $exe = (& $l.Source -3 -c 'import sys;print(sys.executable)' 2>$null); if ($exe) { $cands += [string]$exe } }
    foreach ($x in $cands) {
        $ok = (& $x -I -c 'import sys;print(int(sys.version_info>=(3,11)))' 2>$null)
        if ("$ok".Trim() -eq '1') { $script:OrchPythonPath = $x; return $x }
    }
    throw 'no Python 3.11 or newer found: install it or set ORCH_PYTHON to the python executable'
}
function Invoke-Orch([string[]]$OrchArgs, [string]$InputText = $null) {
    $py = Get-OrchPython
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $py
    $psi.Arguments = (@('-I', (ConvertTo-OrchArg (Join-Path $script:ToolsDir 'orch.py'))) + @($OrchArgs | ForEach-Object { ConvertTo-OrchArg ([string]$_) })) -join ' '
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8; $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $psi.RedirectStandardInput = $true; $psi.StandardInputEncoding = New-Object System.Text.UTF8Encoding($false)
    $p = [System.Diagnostics.Process]::Start($psi)
    $outTask = $p.StandardOutput.ReadToEndAsync(); $errTask = $p.StandardError.ReadToEndAsync()
    if ($null -ne $InputText) { $p.StandardInput.Write($InputText) }
    $p.StandardInput.Close()
    $p.WaitForExit()
    $stdout = $outTask.GetAwaiter().GetResult(); $stderr = $errTask.GetAwaiter().GetResult()
    $code = $p.ExitCode; $p.Dispose()
    $parsed = $null
    if (-not [string]::IsNullOrWhiteSpace($stdout)) { try { $parsed = ConvertFrom-Json -InputObject $stdout } catch { if ($code -eq 0) { throw "orch $($OrchArgs -join ' '): output is not JSON: $stdout" } } }
    if ($code -ne 0) {
        $msg = if ($parsed -and $parsed.PSObject.Properties['error']) { [string]$parsed.error } else { ($stderr + ' ' + $stdout).Trim() }
        throw "orch $($OrchArgs[0]) $($OrchArgs[1]) failed (exit $code): $msg"
    }
    return $parsed
}

# Agent rows of the project: $Scope active (default), archive or all; optional status filter.
function Get-OrchAgents($Cfg, [string]$Scope = 'active', [string]$Status = '') {
    $a = @('agents', 'list', '--project', (Get-OrchProject $Cfg), '--scope', $Scope)
    if ($Status) { $a += @('--status', $Status) }
    return @(Invoke-Orch $a)
}
# One agent row by name ($null when unknown).
function Get-OrchAgent($Cfg, [string]$Name) { return (Invoke-Orch @('agents', 'get', '--project', (Get-OrchProject $Cfg), '--name', $Name)) }
# Turns a time value (DateTime or ISO text, with or without UTC offset) into local ISO text without offset (yyyy-MM-ddTHH:mm:ss), the one format the database compares; empty and unparsable values are returned unchanged.
function ConvertTo-OrchTime($Value) {
    if ($null -eq $Value -or "$Value" -eq '') { return $Value }
    $d = [datetime]::MinValue
    if ($Value -is [datetimeoffset]) { return $Value.LocalDateTime.ToString('yyyy-MM-ddTHH:mm:ss') }
    if ($Value -is [datetime]) { $Value = $(if ($Value.Kind -eq 'Utc') { $Value.ToLocalTime() } else { $Value }); return $Value.ToString('yyyy-MM-ddTHH:mm:ss') }
    if ([datetime]::TryParse([string]$Value, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind, [ref]$d)) { if ($d.Kind -eq 'Utc') { $d = $d.ToLocalTime() }; return $d.ToString('yyyy-MM-ddTHH:mm:ss') }
    return $Value
}
# JSON text of an agent field hashtable for the runtime command; the time fields startedAt and endedAt are normalised with ConvertTo-OrchTime.
function ConvertTo-OrchFieldsJson([hashtable]$Fields) {
    $f = @{}
    foreach ($k in $Fields.Keys) { $f[$k] = $(if ($k -in @('startedAt', 'endedAt', 'started_at', 'ended_at')) { ConvertTo-OrchTime $Fields[$k] } else { $Fields[$k] }) }
    return (ConvertTo-Json -InputObject $f -Compress -Depth 6)
}
# Creates or updates the active row of an agent from a hashtable of fields (unknown keys are kept as free fields).
function Set-OrchAgent($Cfg, [string]$Name, [hashtable]$Fields) {
    return (Invoke-Orch @('agents', 'upsert', '--project', (Get-OrchProject $Cfg), '--name', $Name, '--fields', '-') (ConvertTo-OrchFieldsJson $Fields))
}
# Like Set-OrchAgent, but throws when the agent does not exist.
function Update-OrchAgentFields($Cfg, [string]$Name, [hashtable]$Fields) {
    return (Invoke-Orch @('agents', 'update-fields', '--project', (Get-OrchProject $Cfg), '--name', $Name, '--fields', '-') (ConvertTo-OrchFieldsJson $Fields))
}
# Archives agent rows ended more than $Hours ago (or whose plan/name matches the regex $Match); returns the number of moved rows.
function Move-OrchAgentsToArchive($Cfg, [double]$Hours = 12, [string]$Match = '') {
    $a = @('agents', 'archive', '--project', (Get-OrchProject $Cfg), '--hours', ([string]$Hours))
    if ($Match) { $a += @('--match', $Match) }
    return [int](Invoke-Orch $a).moved
}

# Adds an event to the history and one line to the text log: $Mirror rounds (ROUNDS.log), events (ORCH-EVENTS.log, $IsError marks the line E), both or none.
function Add-OrchEvent($Cfg, [string]$Text, [string]$Mirror = 'rounds', [bool]$IsError = $false, [string]$Kind = '', [string]$Agent = '', $Data = $null) {
    $a = @('event', 'append', '--project', (Get-OrchProject $Cfg), '--text', $Text, '--mirror', $Mirror)
    if ($IsError) { $a += '--error' }
    if ($Kind) { $a += @('--kind', $Kind) }
    if ($Agent) { $a += @('--agent', $Agent) }
    if ($null -ne $Data) { $a += @('--data', (ConvertTo-Json -InputObject $Data -Compress -Depth 6)) }
    return (Invoke-Orch $a)
}
# Events of the project after id $SinceId (oldest first); with $Tail the newest $Tail events.
function Get-OrchEvents($Cfg, [int]$SinceId = 0, [int]$Tail = 0, [string]$Kind = '') {
    $a = if ($Tail -gt 0) { @('events', 'tail', '-n', "$Tail") } else { @('events', 'list') }
    $a += @('--project', (Get-OrchProject $Cfg), '--since-id', "$SinceId")
    if ($Kind) { $a += @('--kind', $Kind) }
    return @(Invoke-Orch $a)
}
# Paths of the text log files (rounds, events) of the project.
function Get-OrchLogPaths($Cfg) { return (Invoke-Orch @('events', 'paths', '--project', (Get-OrchProject $Cfg))) }

# Value of a kv key as text, or $null when the key does not exist.
function Get-OrchKv($Cfg, [string]$Key) { $r = Invoke-Orch @('kv', 'get', '--project', (Get-OrchProject $Cfg), '--key', $Key); if ($r.found) { return [string]$r.value }; return $null }
# Stores a text value under a kv key.
function Set-OrchKv($Cfg, [string]$Key, [string]$Value) { [void](Invoke-Orch @('kv', 'set', '--project', (Get-OrchProject $Cfg), '--key', $Key, '--value', '-') $Value) }
# Deletes a kv key.
function Remove-OrchKv($Cfg, [string]$Key) { [void](Invoke-Orch @('kv', 'delete', '--project', (Get-OrchProject $Cfg), '--key', $Key)) }

# Handled event keys: Get-OrchHandled returns the row (handled, value, ts); Add-OrchHandled remembers a key (optional value such as a retry counter); Remove-OrchHandled forgets it.
function Get-OrchHandled($Cfg, [string]$Key) { return (Invoke-Orch @('handled', 'check', '--project', (Get-OrchProject $Cfg), '--key', $Key)) }
function Add-OrchHandled($Cfg, [string]$Key, [string]$Value = '') {
    $a = @('handled', 'add', '--project', (Get-OrchProject $Cfg), '--key', $Key)
    if ($Value) { $a += @('--value', $Value) }
    [void](Invoke-Orch $a)
}
function Remove-OrchHandled($Cfg, [string]$Key) { [void](Invoke-Orch @('handled', 'remove', '--project', (Get-OrchProject $Cfg), '--key', $Key)) }

# Frozen plan|package keys (lower case): list, add, remove.
function Get-OrchNofollow($Cfg) { return @(Invoke-Orch @('nofollow', 'list', '--project', (Get-OrchProject $Cfg))) }
function Add-OrchNofollow($Cfg, [string]$Key) { [void](Invoke-Orch @('nofollow', 'add', '--project', (Get-OrchProject $Cfg), '--key', $Key)) }
function Remove-OrchNofollow($Cfg, [string]$Key) { [void](Invoke-Orch @('nofollow', 'remove', '--project', (Get-OrchProject $Cfg), '--key', $Key)) }

# Stores one build measurement; Get-OrchBuildMax returns the highest MB per job of this host and project, or $null.
function Add-OrchBuild($Cfg, [int]$Jobs, [double]$FreeStartMB, [double]$FreeMinMB, [double]$PerJobMB, [double]$DurationSec, [string]$HostName = '') {
    $a = @('build', 'add', '--project', (Get-OrchProject $Cfg), '--jobs', "$Jobs", '--free-start-mb', "$FreeStartMB", '--free-min-mb', "$FreeMinMB", '--per-job-mb', "$PerJobMB", '--duration-s', "$DurationSec")
    if ($HostName) { $a += @('--host', $HostName) }
    [void](Invoke-Orch $a)
}
function Get-OrchBuildMax($Cfg, [string]$HostName = '') {
    $a = @('build', 'max-per-job', '--project', (Get-OrchProject $Cfg))
    if ($HostName) { $a += @('--host', $HostName) }
    return (Invoke-Orch $a).per_job_mb
}

# Usage rows: Add-OrchUsage stores a row from a hashtable (keys provider, model, tokensIn, tokensOut, kind, trigger, context, durationS, actions, error, ts); Get-OrchUsage lists rows (kind, since, newest $Limit).
function Add-OrchUsage($Cfg, [hashtable]$Row) {
    $map = [ordered]@{ provider = '--provider'; model = '--model'; tokensIn = '--tokens-in'; tokensOut = '--tokens-out'; kind = '--kind'; trigger = '--trigger'; context = '--context'; durationS = '--duration-s'; actions = '--actions'; error = '--error'; ts = '--ts' }
    $a = @('usage', 'add', '--project', (Get-OrchProject $Cfg))
    foreach ($k in $map.Keys) { if ($Row.ContainsKey($k) -and $null -ne $Row[$k]) { $v = $Row[$k]; if ($v -is [bool]) { $v = [int]$v }; $a += @($map[$k], [string]$v) } }
    [void](Invoke-Orch $a)
}
function Get-OrchUsage($Cfg, [string]$Kind = '', [string]$Since = '', [int]$Limit = 0) {
    $a = @('usage', 'list', '--project', (Get-OrchProject $Cfg))
    if ($Kind) { $a += @('--kind', $Kind) }
    if ($Since) { $a += @('--since', $Since) }
    if ($Limit -gt 0) { $a += @('--limit', "$Limit") }
    return @(Invoke-Orch $a)
}

# Plans: Sync-OrchPlans reads the plan files into the database and returns @{plans; startable}; Get-OrchPlans / Get-OrchPackages read the tables; Set-OrchPlanStatus changes the status in table and file; Set-OrchPackage changes phase / lock.
function Sync-OrchPlans($Cfg, [string]$Workspace = '', [string[]]$Done = @()) {
    $a = @('plans', 'sync', '--project', (Get-OrchProject $Cfg))
    if ($Workspace) { $a += @('--workspace', $Workspace) }
    if ($Done.Count) { $a += @('--done', ($Done -join ',')) }
    return (Invoke-Orch $a)
}
function Get-OrchPlans($Cfg) { return @(Invoke-Orch @('plans', 'list', '--project', (Get-OrchProject $Cfg))) }
function Get-OrchPackages($Cfg, [string]$Plan = '') {
    $a = @('packages', 'list', '--project', (Get-OrchProject $Cfg))
    if ($Plan) { $a += @('--plan', $Plan) }
    return @(Invoke-Orch $a)
}
function Set-OrchPlanStatus($Cfg, [string]$Plan, [string]$Status, [string]$Workspace = '') {
    $a = @('plans', 'set-status', '--project', (Get-OrchProject $Cfg), '--plan', $Plan, '--status', $Status)
    if ($Workspace) { $a += @('--workspace', $Workspace) }
    return (Invoke-Orch $a)
}
function Set-OrchPackage($Cfg, [string]$Plan, [string]$Id, [string]$Phase = '', $Locked = $null, [string]$Reason = '') {
    $a = @('packages', 'set', '--project', (Get-OrchProject $Cfg), '--plan', $Plan, '--id', $Id)
    if ($Phase) { $a += @('--phase', $Phase) }
    if ($null -ne $Locked) { $a += @('--locked', $(if ($Locked) { '1' } else { '0' })) }
    if ($Reason) { $a += @('--reason', $Reason) }
    return (Invoke-Orch $a)
}

# Integration queue rows of the project; Set-OrchQueue changes or adds the row of a branch (hashtable keys tip, plan, status, note, run).
function Get-OrchQueue($Cfg) { return @(Invoke-Orch @('queue', 'list', '--project', (Get-OrchProject $Cfg))) }
function Set-OrchQueue($Cfg, [string]$Branch, [hashtable]$Fields = @{}) {
    $a = @('queue', 'upsert', '--project', (Get-OrchProject $Cfg), '--branch', $Branch)
    foreach ($k in 'tip', 'plan', 'status', 'note', 'run') { if ($Fields.ContainsKey($k) -and $null -ne $Fields[$k]) { $a += @("--$k", [string]$Fields[$k]) } }
    return (Invoke-Orch $a)
}

# Questions (filters status open/answered, plan, item), decisions (filters plan, item) and recording a decision (source owner, coordinator or auto).
function Get-OrchQuestions($Cfg, [string]$Status = '', [string]$Plan = '', [string]$Item = '') {
    $a = @('questions', '--project', (Get-OrchProject $Cfg))
    if ($Status) { $a += @('--status', $Status) }
    if ($Plan) { $a += @('--plan', $Plan) }
    if ($Item) { $a += @('--item', $Item) }
    return @(Invoke-Orch $a)
}
function Add-OrchQuestion($Cfg, [string]$Plan, [string]$Item, [string]$Question, [string]$Recommendation = '') {
    return (Invoke-Orch @('ask', '--project', (Get-OrchProject $Cfg), '--plan', $Plan, '--item', $Item, '--question', $Question, '--recommendation', $Recommendation))
}
function Get-OrchDecisions($Cfg, [string]$Plan = '', [string]$Item = '') {
    $a = @('decisions', 'list', '--project', (Get-OrchProject $Cfg))
    if ($Plan) { $a += @('--plan', $Plan) }
    if ($Item) { $a += @('--item', $Item) }
    return @(Invoke-Orch $a)
}
function Add-OrchDecision($Cfg, [string]$Source, [string]$Text, [string]$Plan = '', [string]$Item = '') {
    $a = @('decisions', 'add', '--project', (Get-OrchProject $Cfg), '--source', $Source, '--text', $Text)
    if ($Plan) { $a += @('--plan', $Plan) }
    if ($Item) { $a += @('--item', $Item) }
    return (Invoke-Orch $a)
}

# ---- v2 runtime wrappers, additions of the round scripts ----
# Key/value pairs of the project whose key starts with the prefix (rows with key, value, updated_at).
function Get-OrchKvList($Cfg, [string]$Prefix = '') {
    $a = @('kv', 'list', '--project', (Get-OrchProject $Cfg))
    if ($Prefix) { $a += @('--prefix', $Prefix) }
    return @(Invoke-Orch $a)
}
# All handled event keys of the project (rows with key, ts, value).
function Get-OrchHandledList($Cfg) { return @(Invoke-Orch @('handled', 'list', '--project', (Get-OrchProject $Cfg))) }
# ---- v2 agent row helpers (pane scripts) ----
# Adds the camelCase names the pane scripts use next to the snake_case columns of an agent row from orch.py (plan_path -> plan, started_at -> startedAt, ...); the original properties stay.
function ConvertTo-OrchAgentRow($Row) {
    if (-not $Row) { return $null }
    $alias = [ordered]@{ plan_path = 'plan'; pane_id = 'paneId'; started_at = 'startedAt'; ended_at = 'endedAt'; exit_code = 'exitCode'; tokens_in = 'tokensIn'; tokens_out = 'tokensOut'; last_line = 'lastLine' }
    foreach ($k in $alias.Keys) { if ($Row.PSObject.Properties[$k] -and -not $Row.PSObject.Properties[$alias[$k]]) { $Row | Add-Member -NotePropertyName $alias[$k] -NotePropertyValue $Row.$k } }
    return $Row
}
# The newest row of the named agent with camelCase names, or $null when the agent is unknown.
function Get-OrchAgentRow($Cfg, [string]$Name) { return (ConvertTo-OrchAgentRow (Get-OrchAgent $Cfg $Name)) }
# All agent rows of the scope (active, archive, all) with camelCase names.
function Get-OrchAgentRows($Cfg, [string]$Scope = 'active') { return @(Get-OrchAgents $Cfg $Scope | ForEach-Object { ConvertTo-OrchAgentRow $_ }) }
