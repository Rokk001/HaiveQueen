# Runs one test script as a child process with a time limit and ends the WHOLE process tree when the limit is hit
# (a plain "timeout" only ends the direct child; grandchildren such as round.ps1 or pane-wrap.ps1 would stay behind and keep pipes open).
# Usage: pwsh -NoProfile -File tests\run-test.ps1 -Test test-s10.ps1 [-TimeoutSec 600]
# Exit code: the test's own exit code, or 124 when the limit was hit.
param([Parameter(Mandatory = $true)][string]$Test, [int]$TimeoutSec = 600)
$path = if (Test-Path $Test) { (Resolve-Path $Test).Path } else { Join-Path $PSScriptRoot $Test }
$proc = Start-Process -FilePath 'pwsh' -ArgumentList @('-NoProfile', '-File', "`"$path`"") -NoNewWindow -PassThru
if ($proc.WaitForExit($TimeoutSec * 1000)) { exit $proc.ExitCode }
Write-Host "TIMEOUT: $Test did not finish within $TimeoutSec s, ending its process tree" -ForegroundColor Red
& taskkill.exe /PID $proc.Id /T /F | Out-Null   # /T ends every child and grandchild of the test
exit 124
