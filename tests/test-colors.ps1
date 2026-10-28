# Agent color test: every running agent gets a color that no other running agent has (also when many agents ended within the last hour).
# Everything runs in a temporary folder with its own runtime; the live runtime is never touched.
. (Join-Path $PSScriptRoot 'common.ps1')
$tools = $ToolsRoot
$root = Join-Path ([IO.Path]::GetTempPath()) ('orch-test-colors-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
if (Test-Path $root) { Remove-Item $root -Recurse -Force }
New-Item -ItemType Directory -Force "$root\rt\runs", "$root\proj", "$root\wt" | Out-Null
[void](New-TestProject -Base $root -Repo "$root\repo" -Runtime "$root\rt")
$env:ORCH_RUNTIME_OVERRIDE = "$root\rt"; $env:ORCH_PLANS_OVERRIDE = "$root\proj"; $env:ORCH_TEST_NOSTART = '1'
. (Join-Path $tools 'lib.ps1')
$cfg = Get-Cfg
if ($cfg.runtime -ne "$root\rt") { throw 'runtime override not active: refusing to run against the live runtime' }
$fails = 0
# Records one check.
function Check([string]$name, [bool]$ok, [string]$detail = '') { if (-not $ok) { $script:fails++ }; Write-Host ('{0} {1} {2}' -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' }) }
# Starts $n running agents one after the other like round.ps1 does (identity, then the row) and returns their colors.
function Start-Agents([int]$n, [string]$prefix) {
    $res = @()
    foreach ($i in 1..$n) {
        $id = Get-AgentIdentity $cfg
        Update-Agent $cfg "$prefix$i" @{ type = 'claude'; status = 'running'; startedAt = (Get-Date).ToString('o'); person = $id.Person; color = $id.Color; pid = 4 }
        $res += $id.Color
    }
    return $res
}
# Hue of an RRGGBB color in degrees.
function Get-Hue([string]$hex) {
    $r = [Convert]::ToInt32($hex.Substring(0, 2), 16) / 255.0; $g = [Convert]::ToInt32($hex.Substring(2, 2), 16) / 255.0; $b = [Convert]::ToInt32($hex.Substring(4, 2), 16) / 255.0
    $mx = [Math]::Max($r, [Math]::Max($g, $b)); $mn = [Math]::Min($r, [Math]::Min($g, $b)); $d = $mx - $mn
    if ($d -eq 0) { return 0 }
    $h = if ($mx -eq $r) { 60 * ((($g - $b) / $d) % 6) } elseif ($mx -eq $g) { 60 * ((($b - $r) / $d) + 2) } else { 60 * ((($r - $g) / $d) + 4) }
    if ($h -lt 0) { $h += 360 }
    return $h
}
# The palette must be 12 colors in 6 hue families (light and dark level each); the families are at least 25 degrees apart.
$hues = @($script:AgentColors | ForEach-Object { Get-Hue $_ } | Sort-Object)
$fam = @(); foreach ($h in $hues) { if ($fam.Count -and ($h - $fam[-1][-1]) -le 10) { $fam[-1] += $h } else { $fam += , @($h) } }
if ($fam.Count -gt 1 -and (($fam[0][0] + 360) - $fam[-1][-1]) -le 10) { $fam[0] += $fam[-1]; $fam = $fam[0..($fam.Count - 2)] }
$gaps = @(); for ($i = 0; $i -lt $fam.Count; $i++) { $a = ($fam[$i] | Measure-Object -Average).Average; $b = ($fam[($i + 1) % $fam.Count] | Measure-Object -Average).Average; $d = [Math]::Abs($a - $b); $gaps += [Math]::Min($d, 360 - $d) }
Check 'palette has 12 colors in 6 hue families with two levels each' ($script:AgentColors.Count -eq 12 -and $fam.Count -eq 6 -and @($fam | Where-Object { $_.Count -ne 2 }).Count -eq 0) ("$($script:AgentColors.Count) colors, $($fam.Count) families")
Check 'hue families are at least 25 degrees apart' ((($gaps | Measure-Object -Minimum).Minimum) -ge 25) ([string][int](($gaps | Measure-Object -Minimum).Minimum))
Check 'palette has no duplicate entry' ((@($script:AgentColors | Select-Object -Unique)).Count -eq $script:AgentColors.Count)
foreach ($n in 2, 4, 7, 12) {
    Remove-Item "$($cfg.runtime)\AGENTS.json" -ErrorAction SilentlyContinue
    $c = Start-Agents $n "R$n-"
    Check "$n running agents: all colors differ" ((@($c | Select-Object -Unique)).Count -eq $n) ($c -join ',')
}
# Agents that ended within the last hour hold the first colors; the two new running agents must still differ from each other.
Remove-Item "$($cfg.runtime)\AGENTS.json" -ErrorAction SilentlyContinue
foreach ($i in 1..($script:AgentColors.Count - 1)) { Update-Agent $cfg "E$i" @{ type = 'claude'; status = 'done'; endedAt = (Get-Date).AddMinutes(-5).ToString('o'); person = "P$i"; color = $script:AgentColors[$i - 1] } }
$c = Start-Agents 2 'N'
Check 'two new agents after many recently ended ones: colors differ' ($c[0] -ne $c[1]) ($c -join ',')
Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "failures: $fails"
exit $fails
