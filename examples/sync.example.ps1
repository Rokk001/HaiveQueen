# Example onEvent command (see "onEvent" in project.example.json): commits every changed file of a repository that .gitignore allows
# and pushes it to main with a normal push (never forced). Called once at the end of a round that had events; the events are the arguments.
# Exit 0 = nothing to do or pushed; any other exit code = failed (the orchestrator logs it and calls again in the next round).
# Set SYNC_REPO to the repository to sync; without it the parent folder of this script is used.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Events)
$ErrorActionPreference = 'Stop'
$repo = if ($env:SYNC_REPO) { $env:SYNC_REPO } else { Split-Path $PSScriptRoot -Parent }

# Runs git in the repository; throws with the output when git fails.
function Invoke-Git([string[]]$GitArgs) {
    $out = & git -C $repo @GitArgs 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw ('git ' + ($GitArgs -join ' ') + ' failed (exit ' + $LASTEXITCODE + '): ' + $out.Trim()) }
    return $out
}

$branch = (Invoke-Git @('rev-parse', '--abbrev-ref', 'HEAD')).Trim()
if ($branch -ne 'main') { throw "the sync only works on main, the checkout is on '$branch'" }
[void](Invoke-Git @('add', '-A'))   # .gitignore decides what is added
& git -C $repo diff --cached --quiet
if ($LASTEXITCODE -eq 0) { Write-Host 'nothing to commit'; exit 0 }
$list = (@($Events) -join '; ')
if ($list.Length -gt 180) { $list = $list.Substring(0, 177) + '...' }
if (-not $list) { $list = 'changes' }
[void](Invoke-Git @('commit', '-m', ('Auto-sync: ' + $list)))
[void](Invoke-Git @('push', 'origin', 'main'))
Write-Host ('synced: ' + $list)
exit 0
