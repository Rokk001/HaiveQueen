# Pane wrapper (PowerShell 7): runs agent-run.ps1 as a CHILD process, then keeps the pane 30 s and closes it with exit 0 --
# also when the agent-run process failed, crashed or was ended by the executor (a killed pane process would stay open otherwise).
# The pane closes after the delay. When it was the last open agent pane, an idle pane (agents-idle.ps1, shows the last finished agent) takes its place first, so the agent area never collapses and the next pane can split it.
# A failure while loading lib.ps1 or config.json (e.g. invalid JSON) must not leave the pane open: show the error, wait 30 s, exit 0 (the pane profile only closes a pane on exit code 0).
$pi = [array]::IndexOf($args, '-Project'); if ($pi -ge 0) { $env:ORCH_PROJECT = [string]$args[$pi + 1] }   # the project file travels in the pane command line
try { . (Join-Path $PSScriptRoot 'lib.ps1'); $cfg = Get-Cfg }
catch {
    Write-Host ('pane start error: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'pane closes in 30 s' -ForegroundColor DarkGray
    Start-Sleep -Seconds 30
    exit 0
}
$ni = [array]::IndexOf($args, '-Name'); $agentName = if ($ni -ge 0) { [string]$args[$ni + 1] } else { '' }
try { & pwsh.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'agent-run.ps1') @args }
finally {
    Write-Host ("pane closes in {0} s" -f $cfg.paneCloseDelaySeconds) -ForegroundColor DarkGray
    Start-Sleep -Seconds $cfg.paneCloseDelaySeconds
    try {
        # The pane always closes after the delay. When no other agent pane runs and no idle pane exists, an idle pane takes its place first (Windows Terminal can only split an existing pane, and the agent area must not collapse). Everything happens under the one pane-command lock and is confirmed (the idle pane writes idle.pid) before this pane exits; the lock is held until this process has exited.
        $mutexName = Get-MutexName ([string]$cfg.mutexPrefix) 'WtPaneCommands'
        $global:wtPaneMutex = New-Object System.Threading.Mutex($false, $mutexName)
        $got = $false
        try { $got = $global:wtPaneMutex.WaitOne(180000) } catch [System.Threading.AbandonedMutexException] { $got = $true }
        if ($got) {
            $rows = @(Get-OrchAgentRows $cfg)
            $me = $rows | Where-Object { $_.name -eq $agentName } | Select-Object -First 1
            $other = @($rows | Where-Object { $_.name -ne $agentName -and (Test-AgentPaneAlive $_) })
            $idlePidFile = Join-Path $cfg.runtime 'idle.pid'
            if ($agentName -and $me -and $null -ne $me.paneId -and $other.Count -eq 0 -and -not (Test-PidFileAlive $idlePidFile)) {
                $idleId = Invoke-Locked {
                    $pp = Join-Path $cfg.runtime 'panes.json'
                    $next = 3
                    if (Test-Path $pp) { try { $next = [int](Get-Content $pp -Raw -Encoding UTF8 | ConvertFrom-Json).next } catch {} }
                    Write-FileAtomic $pp (ConvertTo-Json -InputObject @{ next = ($next + 1); idle = $next } -Compress)
                    return $next
                }
                $t0 = Get-Date
                $idleCmd = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $PSScriptRoot 'agents-idle.ps1') + '" -Project "' + $cfg.projectFile + '"'
                $wtExe = if ($env:ORCH_WT_EXE) { $env:ORCH_WT_EXE } else { 'wt.exe' }
                Start-Process -FilePath $wtExe -ArgumentList ("-w $($cfg.window) focus-pane -t $($me.paneId) ; split-pane -p $($cfg.terminalProfile.name) -V -s 0.5 --suppressApplicationTitle --title `"$((Get-PaneTitlePrefix) + ' | Agenten')`" pwsh " + $idleCmd)
                # wait until the idle pane runs (idle.pid written after the launch), then a settle time; only then this pane may close
                for ($i = 0; $i -lt 30; $i++) { Start-Sleep -Seconds 1; if ((Test-PidFileAlive $idlePidFile) -and (Get-Item -LiteralPath $idlePidFile).LastWriteTime -gt $t0) { break } }
                Start-Sleep -Seconds 2
            }
        }
    } catch {}
    if ($agentName) { try { [void](Set-OrchAgent $cfg $agentName @{ paneClosed = $true }) } catch {} }
}
exit 0
