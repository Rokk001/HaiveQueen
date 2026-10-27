# Tests the three time limits: the agents lock gives up instead of waiting forever, a test ends itself with an error when it blocks, and run-test.ps1 ends the whole process tree.
# Uses its own throw-away project and unique mutex name; nothing of a real project is touched.
. (Join-Path $PSScriptRoot 'common.ps1')
$fails = 0
function Check([string]$Name, [bool]$Ok, [string]$Info = '') { if ($Ok) { Write-Host "PASS $Name $Info" } else { Write-Host "FAIL $Name $Info" -ForegroundColor Red; $script:fails++ } }
$root = Join-Path ([IO.Path]::GetTempPath()) ('orch-test-limits-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $root | Out-Null
[void](New-TestProject -Base $root -Repo "$root\repo" -Runtime "$root\rt")
$env:ORCH_RUNTIME_OVERRIDE = "$root\rt"

# --- 1. the agents lock gives up: a second process holds the mutex, Invoke-Locked must throw after the limit ----
. (Join-Path $ToolsRoot 'lib.ps1')
$mutexName = Get-MutexName ([string](Get-ProjCfg).mutexPrefix) 'AgentsJson'   # with the test runtime active this is the test's own lock name
$holder = Start-Process pwsh -ArgumentList @('-NoProfile', '-Command', "`$m = New-Object System.Threading.Mutex(`$false, '$mutexName'); [void]`$m.WaitOne(); Start-Sleep 20") -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 3   # the holder owns the lock now
$env:ORCH_LOCK_TIMEOUT_SEC = '2'
$t0 = Get-Date; $threw = $false
try { Invoke-Locked { } } catch { $threw = $_.Exception.Message -match 'not free within 2 s' }
$sec = ((Get-Date) - $t0).TotalSeconds
Check 'locked agents mutex: Invoke-Locked throws after the limit instead of waiting' ($threw -and $sec -lt 10) ("after {0:N1} s" -f $sec)
Remove-Item Env:ORCH_LOCK_TIMEOUT_SEC
if (-not $holder.HasExited) { & taskkill.exe /PID $holder.Id /T /F | Out-Null }   # only the holder started above

# --- 2. a test that blocks ends itself with exit 124 ----
$blocker = "$root\blocker.ps1"
Set-Content $blocker ('. "' + $PSScriptRoot + '\common.ps1"' + "`nStart-Sleep -Seconds 60") -Encoding UTF8
$env:ORCH_TEST_LIMIT_SEC = '2'
$t0 = Get-Date
$p = Start-Process pwsh -ArgumentList @('-NoProfile', '-File', "`"$blocker`"") -PassThru -NoNewWindow -RedirectStandardError "$root\blocker.err"
$done = $p.WaitForExit(30000)
Remove-Item Env:ORCH_TEST_LIMIT_SEC
Check 'blocked test ends itself with exit 124' ($done -and $p.ExitCode -eq 124) ("exit=$($p.ExitCode) after {0:N1} s" -f ((Get-Date) - $t0).TotalSeconds)
Check 'blocked test says why' ((Get-Content "$root\blocker.err" -Raw) -match 'TEST TIME LIMIT HIT')
if (-not $done) { & taskkill.exe /PID $p.Id /T /F | Out-Null }

# --- 3. run-test.ps1 ends the whole tree, also a grandchild ----
$marker = 'limits-grandchild-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
$tree = "$root\tree.ps1"
Set-Content $tree ("Start-Process pwsh -ArgumentList @('-NoProfile','-Command','# $marker ; Start-Sleep 120') -WindowStyle Hidden`nStart-Sleep -Seconds 120") -Encoding UTF8
$r = Start-Process pwsh -ArgumentList @('-NoProfile', '-File', "`"$PSScriptRoot\run-test.ps1`"", '-Test', "`"$tree`"", '-TimeoutSec', '4') -PassThru -NoNewWindow -RedirectStandardOutput "$root\runner.out"
$null = $r.WaitForExit(40000)
Start-Sleep -Seconds 2
$left = @(Get-CimInstance Win32_Process -Filter "Name='pwsh.exe'" | Where-Object { $_.CommandLine -match [regex]::Escape($marker) })
Check 'run-test.ps1 exits 124 on timeout' ($r.ExitCode -eq 124) "exit=$($r.ExitCode)"
Check 'run-test.ps1 left no grandchild behind' ($left.Count -eq 0) "left=$($left.Count)"
foreach ($l in $left) { & taskkill.exe /PID $l.ProcessId /T /F | Out-Null }   # clean up only the processes carrying this test's marker

# --- 4. a held lock of the live system (same project, no test runtime) never blocks a test run ----
$saved = $env:ORCH_RUNTIME_OVERRIDE; $env:ORCH_RUNTIME_OVERRIDE = $null
$liveName = Get-MutexName ([string](Get-ProjCfg).mutexPrefix) 'AgentsJson'   # the name the live system uses
$env:ORCH_RUNTIME_OVERRIDE = $saved
$liveHolder = Start-Process pwsh -ArgumentList @('-NoProfile', '-Command', "`$m = New-Object System.Threading.Mutex(`$false, '$liveName'); [void]`$m.WaitOne(); Start-Sleep 20") -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 3   # the holder owns the live-named lock now
$t0 = Get-Date; $ran = $false; $err = ''
try { Invoke-Locked { $script:ran = $true } } catch { $err = $_.Exception.Message }   # default limit (120 s): it must not wait at all
$sec = ((Get-Date) - $t0).TotalSeconds
Check 'test name differs from the live name' ($mutexName -ne $liveName) "test=$mutexName live=$liveName"
Check 'live lock held: the test run still goes through without waiting' ($ran -and $sec -lt 5) ("after {0:N1} s {1}" -f $sec, $err)
if (-not $liveHolder.HasExited) { & taskkill.exe /PID $liveHolder.Id /T /F | Out-Null }   # only the holder started above

Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "failures: $fails"
exit $fails
