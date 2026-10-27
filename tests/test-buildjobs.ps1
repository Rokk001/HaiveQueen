# Test of the build-job calculation and measurement (plan section 16): first build without a measured value, RAM limits, cores limit, a higher measured value replaces the old one,
# "fixed" overrides everything, reserve 1024 from the configuration, one ROUNDS.log line per build. All numbers are invented; everything runs in a temporary folder with its own runtime.
. (Join-Path $PSScriptRoot 'common.ps1')
$tools = $ToolsRoot
$root = Join-Path ([IO.Path]::GetTempPath()) ('orch-test-bj-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force "$root\rt\runs", "$root\proj", "$root\wt" | Out-Null
$pf = New-TestProject -Base $root -Repo "$root\repo" -Runtime "$root\rt"
$env:ORCH_RUNTIME_OVERRIDE = "$root\rt"
. (Join-Path $tools 'lib.ps1')
$cfg = Get-Cfg
if ($cfg.runtime -ne "$root\rt") { throw 'runtime override not active: refusing to run against the live runtime' }
$fails = 0
# Records one check.
function Check([string]$name, [bool]$ok, [string]$detail = '') { if (-not $ok) { $script:fails++ }; Write-Host ('{0} {1} {2}' -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' }) }
# Build-job settings with the standard values; $extra overrides single keys.
function Bj([hashtable]$extra = @{}) {
    $b = [ordered]@{ envVar = 'BUILD_JOBS'; coreShare = '2/3'; reserveMB = 1024; perJobMB = 'auto'; firstJobs = 2 }
    foreach ($k in $extra.Keys) { $b[$k] = $extra[$k] }
    return (Get-BuildJobsCfg ([pscustomobject]@{ buildJobs = [pscustomobject]$b }))
}

# Defaults when the block only names the variable.
$d = Get-BuildJobsCfg ([pscustomobject]@{ buildJobs = [pscustomobject]@{ envVar = 'X' } })
Check 'defaults: share 2/3, reserve 1024, first 2, per job auto' (([math]::Abs($d.CoreShare - 2.0 / 3.0) -lt 1e-9) -and $d.ReserveMB -eq 1024 -and $d.FirstJobs -eq 2 -and $null -eq $d.PerJobMB -and $null -eq $d.Fixed)
Check 'no block = no build jobs' ($null -eq (Get-BuildJobsCfg ([pscustomobject]@{})))

# 1. first build without a measured value: firstJobs, never above the core limit.
$r = Get-BuildJobCount (Bj) 12 32000 $null
Check 'first build: firstJobs 2' ($r.Jobs -eq 2 -and $r.Why -eq 'first') "jobs=$($r.Jobs)"
$r = Get-BuildJobCount (Bj @{ firstJobs = 10 }) 6 32000 $null
Check 'first build: capped by the core limit (6 cores x 2/3 = 4)' ($r.Jobs -eq 4) "jobs=$($r.Jobs)"

# 2. RAM limits: 12 cores -> core limit 8; free 4096 MB, reserve 1024, 1000 MB per job -> floor(3072 / 1000) = 3.
$r = Get-BuildJobCount (Bj) 12 4096 1000
Check 'RAM limits the jobs (3)' ($r.Jobs -eq 3 -and $r.Why -eq 'ram') "jobs=$($r.Jobs)"
$r = Get-BuildJobCount (Bj) 12 1500 1000
Check 'at least 1 job when RAM is tiny' ($r.Jobs -eq 1) "jobs=$($r.Jobs)"

# 3. cores limit: plenty of RAM; 10 cores x 2/3 = 6.67 -> 7.
$r = Get-BuildJobCount (Bj) 10 64000 500
Check 'cores limit the jobs (7)' ($r.Jobs -eq 7 -and $r.Why -eq 'cores') "jobs=$($r.Jobs)"

# 4. a higher measured value replaces the old one, a lower one does not.
Check 'nothing stored at first' ($null -eq (Get-BuildPerJobMB $cfg))
Check 'first value stored' ((Save-BuildPerJobMB $cfg 600) -eq 600)
Check 'higher value replaces' ((Save-BuildPerJobMB $cfg 900) -eq 900 -and (Get-BuildPerJobMB $cfg) -eq 900)
Check 'lower value does not replace' ((Save-BuildPerJobMB $cfg 400) -eq 900 -and (Get-BuildPerJobMB $cfg) -eq 900)
$r = Get-BuildJobCount (Bj) 12 4096 (Get-BuildPerJobMB $cfg)
Check 'the stored value is used for the next count (floor(3072 / 900) = 3)' ($r.Jobs -eq 3) "jobs=$($r.Jobs)"
Check 'use per job = (start - lowest) / jobs' ((Get-BuildUseMB 8000 5000 3) -eq 1000 -and (Get-BuildUseMB 3000 4000 2) -eq 0)

# 5. fixed overrides everything; a number in perJobMB overrides the measurement.
$r = Get-BuildJobCount (Bj @{ fixed = 5 }) 4 1200 2000
Check 'fixed overrides cores, RAM and measurement' ($r.Jobs -eq 5 -and $r.Why -eq 'fixed') "jobs=$($r.Jobs)"
$r = Get-BuildJobCount (Bj @{ perJobMB = 2000 }) 12 6144 100
Check 'a configured perJobMB overrides the measurement (floor(5120 / 2000) = 2)' ($r.Jobs -eq 2) "jobs=$($r.Jobs)"

# 6. reserve 1024 from the configuration: free 3072 MB, 1000 per job -> floor(2048 / 1000) = 2; with reserve 2048 -> 1.
$r1 = Get-BuildJobCount (Bj) 12 3072 1000
$r2 = Get-BuildJobCount (Bj @{ reserveMB = 2048 }) 12 3072 1000
Check 'reserve 1024 from the configuration' ($r1.Jobs -eq 2 -and $r2.Jobs -eq 1 -and (Bj).ReserveMB -eq 1024) "r1=$($r1.Jobs) r2=$($r2.Jobs)"

# 7. end of a build: samples -> lowest free RAM, use per job, one ROUNDS.log line, stored maximum (invented samples).
Set-Content "$root\samples.txt" @('2026-01-01T00:00:00;8000;0', '2026-01-01T00:00:10;6500;4', '2026-01-01T00:00:20;5000;4', '2026-01-01T00:00:30;7000;1') -Encoding UTF8
$m = Complete-BuildMeasure $cfg 'TEST-build' 3 8000 "$root\samples.txt" ((Get-Date).AddSeconds(-42))
Check 'measure: lowest 5000, use per job 1000, stored 1000' ($m.MinFreeMB -eq 5000 -and $m.UseMB -eq 1000 -and $m.StoredMB -eq 1000) "min=$($m.MinFreeMB) use=$($m.UseMB) stored=$($m.StoredMB)"
$line = Get-Content "$root\rt\ROUNDS.log" | Where-Object { $_ -match 'build TEST-build: jobs=3' } | Select-Object -Last 1
Check 'one ROUNDS.log line with jobs, duration, lowest free RAM, use per job' ($line -match 'jobs=3, duration=00:00:4\d, lowest free RAM=5000 MB, use per job=1000 MB') $line

# 8. dashboard line.
$cfg | Add-Member -NotePropertyName buildJobs -NotePropertyValue ([pscustomobject]@{ envVar = 'BUILD_JOBS'; fixed = 4 }) -Force
$txt = Get-BuildJobsLine $cfg @([pscustomobject]@{ status = 'running'; buildJobs = 4 })
Check 'dashboard line: running jobs and use per job' ($txt -match '^Build-Jobs 4 \(laufend\) \| Verbrauch pro Job 1000 MB$') $txt

Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
if ($fails -gt 0) { Write-Host "$fails check(s) failed" -ForegroundColor Red; exit 1 }
Write-Host 'all build-job checks passed' -ForegroundColor Green
