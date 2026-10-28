# S9 test (plan 12.1): phase rule files and start context. Uses its own runtime folder (ORCH_RUNTIME_OVERRIDE) and test database, never the live runtime.
# Usage: pwsh -File test-s9.ps1 [-Live]   (-Live additionally starts one real Claude agent per phase with the task "Reply OK" and prints its first context)
param([switch]$Live)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$tools = $ToolsRoot
$rt = Join-Path ([IO.Path]::GetTempPath()) ('orch-test-s9-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $rt | Out-Null
[void](New-TestProject -Base $rt -Repo "$rt\repo" -Runtime "$rt\rt")
. (Join-Path $tools 'lib.ps1')
$env:ORCH_RUNTIME_OVERRIDE = $rt
$cfg = Get-Cfg
$fail = 0
function Check([bool]$ok, [string]$what) { if ($ok) { Write-Host "PASS $what" -ForegroundColor Green } else { Write-Host "FAIL $what" -ForegroundColor Red; $script:fail++ } }

# 1. Every phase builds its rule file; each section is a verbatim part of its source.
$sizes = @{}
foreach ($ph in 'work', 'verify', 'release', 'integrate') {
    $f = Join-Path $rt "rules-$ph.md"
    $n = Write-PhaseRules $cfg $ph $f
    $txt = [System.IO.File]::ReadAllText($f)
    $sizes[$ph] = $txt.Length
    Check ($n -gt 0) "$ph rule file written ($n sections, $($txt.Length) chars)"
    $all = 0; $ok = 0
    foreach ($src in @($cfg.phaseRules.$ph.PSObject.Properties.Name)) {
        $path = Resolve-RuleSourcePath $cfg $src
        $srcTxt = ([System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)) -replace "`r`n", "`n"
        foreach ($pre in @($cfg.phaseRules.$ph.$src)) {
            $all++
            $b = (Get-MarkdownBlock ([System.IO.File]::ReadAllLines($path, [System.Text.Encoding]::UTF8)) $pre) -replace "`r`n", "`n"
            if ($b -and $srcTxt.Contains($b) -and $txt.Contains($b)) { $ok++ }
        }
    }
    Check ($all -eq $ok) "$ph sections verbatim in source and in file ($ok/$all)"
}
Check ($sizes.verify -lt $sizes.work) 'verify gets fewer rule characters than work'
$v = [System.IO.File]::ReadAllText((Join-Path $rt 'rules-verify.md'))
$w = [System.IO.File]::ReadAllText((Join-Path $rt 'rules-work.md'))
Check ($v -notmatch 'Coding style') 'verify rules contain no coding-style section'
Check ($w -match 'Coding style') 'work rules contain the coding-style section'
Check ($v -match 'Process rules') 'verify rules keep the process rules'
Check ($v -match 'Hard rules \(tool\)' -and $v -match 'Hard rules \(project\)') 'verify rules contain the tool rules and the project rules'

# 2. A renamed heading must fail loudly, never drop a rule silently.
$bad = $cfg | ConvertTo-Json -Depth 8 | ConvertFrom-Json
$bad.phaseRules.verify.repo = @('## No such heading')
$threw = $false
try { [void](Write-PhaseRules $bad 'verify' (Join-Path $rt 'rules-bad.md')) } catch { $threw = $true }
Check $threw 'missing section throws'

# 3. Argument templates carry the reduced start context options.
$cl = ((Get-Provider $cfg 'claude').Args -join ' ')
Check ($cl -match '--tools \{claudeTools\}' -and $cl -match '--append-system-prompt-file \{rules\}' -and $cl -match '--setting-sources') 'claudeArgs: tools, setting sources, rules file'
Check (((Get-Provider $cfg 'codex').Args -join ' ') -match '--ignore-user-config') 'codexArgs: --ignore-user-config'

if ($Live) {
    $prompt = Join-Path $rt 'prompt-ok.md'
    Set-Content $prompt 'Reply with the single word OK. Do nothing else.' -Encoding UTF8
    foreach ($ph in 'work', 'verify', 'release', 'integrate') {
        $nm = "ZZS9-$ph"
        $p = Start-Process pwsh -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $tools 'agent-run.ps1'), '-Project', $env:ORCH_PROJECT, '-Name', $nm, '-Type', 'claude', '-Plan', 'zz', '-Package', 'zz', '-Phase', $ph, '-Cwd', $cfg.repo, '-PromptFile', $prompt) -WindowStyle Hidden -PassThru
        if (-not $p.WaitForExit(240000)) { Write-Host "FAIL $nm timeout" -ForegroundColor Red; $fail++; continue }
        $ev = Join-Path $rt "runs\$nm\events.jsonl"
        $first = $null
        foreach ($l in (Get-Content $ev -ErrorAction SilentlyContinue)) { try { $j = $l | ConvertFrom-Json } catch { continue }; if ($j.type -eq 'assistant' -and $j.message.usage -and $null -eq $first) { $u = $j.message.usage; $first = [int]($u.input_tokens + $u.cache_creation_input_tokens + $u.cache_read_input_tokens) } }
        Check ($null -ne $first -and $first -lt 40000) "$nm first context $first (was 55200-56100)"
    }
}
Remove-Item $rt -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "failures: $fail"
exit $fail
