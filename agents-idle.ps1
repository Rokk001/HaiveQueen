# Right-hand area at the window start (before the first agent): Windows Terminal can only split an existing pane, so the area needs one pane from the beginning.
# It is no placeholder text: it shows the last finished agent (if any) and closes itself as soon as the first agent pane is open, so the agent panes get the whole area.
param([string]$Project = '')
if ($Project) { $env:ORCH_PROJECT = $Project }
. (Join-Path $PSScriptRoot 'lib.ps1')
$cfg = Get-Cfg
$until = (Get-Date).AddHours(12)   # time limit
$idlePid = Join-Path $cfg.runtime 'idle.pid'
Set-Content -LiteralPath $idlePid -Value $PID -Encoding ASCII   # tells the others that this pane exists (a split target is only used while this process is alive)
$shown = ''
while ((Get-Date) -lt $until) {
    try {
        $rows = @(Read-Agents $cfg)
        if (@($rows | Where-Object { Test-AgentPaneAlive $_ }).Count -gt 0) { Start-Sleep -Seconds 3; break }   # an agent pane runs (confirmed by its live runner process): this pane makes room, after a settle time so the split that created it is finished
        $last = $rows | Where-Object { $_.endedAt } | Sort-Object { [datetime]$_.endedAt } -Descending | Select-Object -First 1
        $txt = if ($last) { 'Letzter Agent: {0} ({1}), beendet {2}: {3}' -f $last.name, $last.status, ([datetime]$last.endedAt).ToString('HH:mm'), (([string]$last.lastLine -replace '\s+', ' ').Trim() | ForEach-Object { if ($_.Length -gt 300) { $_.Substring(0, 300) } else { $_ } }) } else { '' }
        if ($txt -ne $shown) { Clear-Host; if ($txt) { Write-Host $txt -ForegroundColor DarkGray }; $shown = $txt }
    } catch {}
    Start-Sleep -Seconds 2
}
Remove-Item -LiteralPath $idlePid -ErrorAction SilentlyContinue
exit 0
