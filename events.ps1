# Event hook (generic): collects the events of one round (plan status changes, finished work / passed verify / released packages, ready and integrated queue rows,
# questions asked or answered) and calls the command "onEvent" of the project file ONCE at the end of the round with the list of events. No event, no call.
# A failed call is logged in ORCH-EVENTS.log and repeated in the next round with all events collected so far; it never stops the loop and costs no tokens.
# Dot-sourced by round.ps1 (needs lib.ps1 and routine.ps1).

# Builds the current event candidates of the round as an ordered hashtable key -> text (the state, not the change: new keys since the last round are the events).
function Get-CurrentEventKeys($Cfg, [string[]]$PlanPaths, $QueueRows) {
    $cur = [ordered]@{}
    foreach ($pp in $PlanPaths) {
        if (-not (Test-Path -LiteralPath $pp)) { continue }
        $st = Get-PlanStatus $pp
        $cur["plan|$pp|$st"] = ('plan ' + [IO.Path]::GetFileNameWithoutExtension($pp) + ' ' + $st)
    }
    foreach ($a in @(Read-AllAgents $Cfg | Where-Object { $_.status -eq 'done' -and $_.name })) {
        $n = [string]$a.name
        if ($a.phase -eq 'work') { $cur["work|$n"] = ($n + ' finished') }
        elseif ($a.phase -eq 'verify' -and (Test-VerifyZero $Cfg $n)) { $cur["verified|$n"] = ($n + ' passed') }
        elseif ($a.phase -eq 'release') { $cur["release|$n"] = ($n + ' finished') }
    }
    foreach ($r in @($QueueRows)) {
        $word = ([string]$r.status).Trim().Split(' ')[0]
        if ($word -eq 'ready') { $cur["queue|$($r.branch)|$($r.tip)|ready"] = ('ready ' + $r.branch) }
        elseif ($word -eq 'integriert') { $cur["queue|$($r.branch)|$($r.tip)|integrated"] = ('integrated ' + $r.branch) }
    }
    foreach ($q in @(Get-Questions $Cfg)) {
        if ($q.status -eq 'OPEN') { $cur["q|$($q.id)|OPEN"] = ('question ' + $q.id + ' asked') }
        elseif ($q.status -eq 'ANSWERED') { $cur["q|$($q.id)|ANSWERED"] = ('question ' + $q.id + ' answered') }
    }
    return $cur
}

# Runs the onEvent command of the project file with the pending events (see the file header). The first call in a runtime only records the current state as the baseline.
function Invoke-EventHook($Cfg, [string[]]$PlanPaths, $QueueRows) {
    if (-not $Cfg.onEvent -or -not $Cfg.onEvent.command) { return }
    try {
        $sp = Join-Path $Cfg.runtime 'ORCH-SYNC.json'
        $baseline = -not (Test-Path -LiteralPath $sp)
        $seen = @{}; $pending = @()
        if (-not $baseline) {
            $j = Get-Content $sp -Raw -Encoding UTF8 | ConvertFrom-Json -AsHashtable
            if ($j.seen) { $seen = $j.seen }
            if ($j.pending) { $pending = @($j.pending | ForEach-Object { [string]$_ }) }
        }
        $cur = Get-CurrentEventKeys $Cfg $PlanPaths $QueueRows
        foreach ($k in $cur.Keys) {
            if (-not $seen.ContainsKey($k)) {
                $seen[$k] = (Get-Date).ToString('o')
                if (-not $baseline) { $pending += [string]$cur[$k] }
            }
        }
        if ($pending.Count -eq 0) { Write-FileAtomic $sp (ConvertTo-Json -InputObject @{ seen = $seen; pending = @() } -Compress); return }
        Write-FileAtomic $sp (ConvertTo-Json -InputObject @{ seen = $seen; pending = @($pending) } -Compress)   # the events are kept before the command runs, so a crash or an error never loses them
        $cmd = @($Cfg.onEvent.command | ForEach-Object { ([string]$_).Replace('{projectDir}', [string]$Cfg.projectDir).Replace('{tools}', [string]$Cfg.tools) })
        $workDir = if ($Cfg.onEvent.workDir) { ([string]$Cfg.onEvent.workDir).Replace('{projectDir}', [string]$Cfg.projectDir) } else { [string]$Cfg.projectDir }
        $timeout = if ($Cfg.onEvent.timeoutSeconds) { [int]$Cfg.onEvent.timeoutSeconds } else { 180 }
        $res = Invoke-Native $cmd[0] (@($cmd | Select-Object -Skip 1) + $pending) '' $workDir $timeout
        if ($res.ExitCode -eq 0) {
            Write-RoundLog $Cfg ('event hook ok: ' + $pending.Count + ' event(s)')
            $pending = @()
        }
        else {
            $err = (($res.StdErr + ' ' + $res.StdOut) -replace '\s+', ' ').Trim()
            if ($res.TimedOut) { $err = 'timeout after ' + $timeout + ' s' }
            Write-RoundLog $Cfg ('event hook failed (exit ' + $res.ExitCode + '): ' + $err)
            Write-OrchEvent $Cfg ('Ereignis-Befehl fehlgeschlagen (naechste Runde wieder): ' + $err.Substring(0, [Math]::Min(120, $err.Length))) $true
        }
        Write-FileAtomic $sp (ConvertTo-Json -InputObject @{ seen = $seen; pending = @($pending) } -Compress)
    }
    catch {
        try { Write-RoundLog $Cfg ('event hook error: ' + $_.Exception.Message); Write-OrchEvent $Cfg ('Ereignis-Befehl Fehler: ' + $_.Exception.Message) $true } catch { }
    }
}
