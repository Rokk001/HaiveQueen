# usage-test.ps1 - checks usage.ps1 with small test logs and a test database in a temporary folder (no real logs and no real runtime are touched).
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$tools = $ToolsRoot
. (Join-Path $tools 'usage.ps1')

$base = Join-Path ([IO.Path]::GetTempPath()) ('orch-test-usage-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
if (Test-Path $base) { Remove-Item -Recurse -Force $base }
$claude = Join-Path $base 'claude\proj'; $codex = Join-Path $base 'codex\2026\10\27'; $cache = Join-Path $base 'cache'
New-Item -ItemType Directory -Force -Path $claude, $codex, $cache | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)
[void](New-TestProject -Base $base)
$env:ORCH_RUNTIME_OVERRIDE = "$base\rt"
. (Join-Path $tools 'lib.ps1')
$cfg = Get-Cfg
if ($cfg.runtime -ne "$base\rt") { throw 'runtime override not active: refusing to run against the live runtime' }

$script:fail = 0
# Prints PASS or FAIL for one check.
function Check([string]$Name, $Actual, $Expected) {
    $ok = ("$Actual" -eq "$Expected")
    if (-not $ok) { $script:fail++ }
    '{0}  {1}  (ist: {2}, soll: {3})' -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $Name, $Actual, $Expected
}
# Returns an ISO UTC timestamp for a local time.
function Ts([datetime]$Local) { return $Local.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ') }
# Builds one Claude assistant line.
function ClaudeLine([string]$Id, [string]$Model, [datetime]$When, [int]$In, [int]$CC, [int]$CR, [int]$Out) {
    return '{"type":"assistant","timestamp":"' + (Ts $When) + '","message":{"id":"' + $Id + '","model":"' + $Model + '","role":"assistant","usage":{"input_tokens":' + $In + ',"cache_creation_input_tokens":' + $CC + ',"cache_read_input_tokens":' + $CR + ',"output_tokens":' + $Out + ',"iterations":[{"input_tokens":999,"output_tokens":999}]}}}'
}
# Builds one Codex token_count line (cumulative).
function CodexCount([datetime]$When, [int]$In, [int]$Out) {
    return '{"timestamp":"' + (Ts $When) + '","ordinal":5,"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":' + $In + ',"cached_input_tokens":1,"output_tokens":' + $Out + ',"total_tokens":' + ($In + $Out) + '}}}}'
}
# Builds one Codex turn_context line.
function CodexCtx([datetime]$When, [string]$Model) {
    return '{"timestamp":"' + (Ts $When) + '","ordinal":2,"type":"turn_context","payload":{"model":"' + $Model + '"}}'
}

$today = (Get-Date).Date
$t12 = $today.AddHours(12); $yest = $today.AddDays(-1).AddHours(12)
$sonnet = 'claude-sonnet-5-5'

# Claude: msg_1 streams (3 lines) and is repeated in file b; msg_2 is yesterday; opus and synthetic.
$a = @(
    (ClaudeLine 'msg_1' $sonnet $t12 1 0 50 10),
    (ClaudeLine 'msg_1' $sonnet $t12 1 20 400 30),
    (ClaudeLine 'msg_1' $sonnet $t12 1 20 400 50),
    (ClaudeLine 'msg_2' $sonnet $yest 100 0 0 5),
    (ClaudeLine 'msg_3' 'claude-opus-4-1-20250805' $t12 7 0 0 3),
    (ClaudeLine 'msg_s' '<synthetic>' $t12 5 5 5 5),
    ('{"type":"user","timestamp":"' + (Ts $t12) + '","message":{"content":"hallo"}}')
)
$b = @(
    (ClaudeLine 'msg_1' $sonnet $t12 1 20 400 50),
    (ClaudeLine 'msg_4' $sonnet $t12 10 0 0 1)
)
[IO.File]::WriteAllText("$claude\a.jsonl", ($a -join "`n") + "`n", $utf8)
[IO.File]::WriteAllText("$claude\b.jsonl", ($b -join "`n") + "`n", $utf8)

# Codex: S1 crosses midnight, S2 starts today, S3 has no token events.
$s1 = @((CodexCtx $yest 'gpt-6.1-sol'), (CodexCount $today.AddHours(-2) 1000 100), (CodexCount $today.AddMinutes(-10) 2000 200),
    (CodexCount $today.AddHours(10) 3500 300), (CodexCount $today.AddHours(11) 5000 450))
$s2 = @((CodexCtx $t12 'gpt-6.1-sol'), (CodexCount $t12 400 40), (CodexCount $t12.AddMinutes(1) 900 90))
$s3 = @((CodexCtx $t12 'gpt-6.1-sol'), ('{"timestamp":"' + (Ts $t12) + '","ordinal":3,"type":"event_msg","payload":{"type":"token_count","info":null}}'))
[IO.File]::WriteAllText("$codex\rollout-s1.jsonl", ($s1 -join "`n") + "`n", $utf8)
[IO.File]::WriteAllText("$codex\rollout-s2.jsonl", ($s2 -join "`n") + "`n", $utf8)
[IO.File]::WriteAllText("$codex\rollout-s3.jsonl", ($s3 -join "`n") + "`n", $utf8)

# Agent rows (with pilot and test entries) and brain rows in the test database; A5 is an old ended row and is moved to the archive.
foreach ($ag in @(
    @{ name = 'A1'; startedAt = '2026-10-27T08:00:00+01:00'; type = 'claude'; plan = 'x\REAL-PLAN.md'; tokensIn = 1000; tokensOut = 100 },
    @{ name = 'A2'; startedAt = '2026-10-27T08:01:00+01:00'; type = 'codex'; plan = 'x\REAL-PLAN.md'; tokensIn = 2000; tokensOut = 200 },
    @{ name = 'A3'; startedAt = '2026-10-27T08:02:00+01:00'; type = 'claude'; plan = 'x\PILOT-PLAN.md'; tokensIn = 999; tokensOut = 99 },
    @{ name = 'ZZTEST-x'; startedAt = '2026-10-27T08:03:00+01:00'; type = 'codex'; plan = 'x\REAL-PLAN.md'; tokensIn = 888; tokensOut = 88 },
    @{ name = 'A5'; startedAt = '2026-10-26T08:00:00+01:00'; endedAt = '2026-10-26T09:00:00+01:00'; status = 'done'; type = 'claude'; plan = 'x\REAL-PLAN.md'; tokensIn = 500; tokensOut = 50 }
)) { [void](Set-OrchAgent $cfg ([string]$ag.name) $ag) }
if ((Move-OrchAgentsToArchive $cfg 12) -ne 1) { throw 'test setup: the old row was not archived' }
Add-OrchUsage $cfg @{ kind = 'brain'; model = 'Sonnet 5.5'; tokensIn = 300; tokensOut = 30 }
Add-OrchUsage $cfg @{ kind = 'brain'; model = 'Opus 4'; tokensIn = 777; tokensOut = 77 }

# Calls Get-UsageTable with the test log folders and the test database.
function Run-Usage {
    return Get-UsageTable -CacheDir $cache -Cfg $cfg -ClaudeRoot (Join-Path $base 'claude') -CodexRoot (Join-Path $base 'codex')
}
# Returns the model entry with the given key.
function M($u, [string]$Key) { return $u.Models | Where-Object { $_.Key -eq $Key } }

$u = Run-Usage
$s = M $u $sonnet; $o = M $u 'claude-opus-4-1'; $x = M $u 'gpt-6.1-sol'
Check 'Modellreihenfolge Claude zuerst' (($u.Models | ForEach-Object { $_.Key }) -join ',') 'claude-sonnet-5-5,claude-opus-4-1,gpt-6.1-sol'
Check 'synthetic ignoriert (3 Modelle)' $u.Models.Count 3
Check 'Label Claude' $s.Label 'Claude Son 5.5'
Check 'Label Claude Opus' $o.Label 'Claude Opu 4.1'
Check 'Label Codex' $x.Label 'Codex Sol 6.1'
Check 'Sonnet gesamt In (dedupliziert, letzter Stand)' $s.Total.In (421 + 100 + 10)
Check 'Sonnet gesamt Out' $s.Total.Out (50 + 5 + 1)
Check 'Sonnet heute In (ohne gestern)' $s.Today.In (421 + 10)
Check 'Sonnet heute Out' $s.Today.Out (50 + 1)
Check 'Opus heute In/Out' "$($o.Today.In)/$($o.Today.Out)" '7/3'
Check 'Sonnet seit = gestern' $s.Since.Date $yest.Date
Check 'Codex gesamt In (letzter Stand je Sitzung)' $x.Total.In (5000 + 900)
Check 'Codex gesamt Out' $x.Total.Out (450 + 90)
Check 'Codex heute In (Differenz zu Mitternacht, S2 komplett)' $x.Today.In (3000 + 900)
Check 'Codex heute Out' $x.Today.Out (250 + 90)
Check 'Plaene Sonnet In (Agents, Archiv, Gehirn, ohne Pilot/ZZTEST)' $s.Plans.In (1000 + 500 + 300)
Check 'Plaene Sonnet Out' $s.Plans.Out (100 + 50 + 30)
Check 'Plaene Codex In' $x.Plans.In 2000
Check 'Plaene Opus ohne Daten (null)' ($null -eq $o.Plans.In) $true
Check 'NoData false' $s.NoData $false
Check 'Kaltlauf liest 5 Dateien' $u.Stats.FilesRead 5
Check 'Retention ist Text' ($u.Retention -match 'Claude Code') $true

$cacheFile = Join-Path $cache 'usage-cache.bin'
$stamp1 = (Get-Item $cacheFile).LastWriteTimeUtc.Ticks
Start-Sleep -Milliseconds 50
$u2 = Run-Usage
Check 'Zweiter Lauf liest keine Datei' $u2.Stats.FilesRead 0
Check 'Zweiter Lauf liest keine Bytes' $u2.Stats.BytesRead 0
Check 'Zweiter Lauf schreibt Cache nicht' ((Get-Item $cacheFile).LastWriteTimeUtc.Ticks -eq $stamp1) $true
Check 'Zweiter Lauf gleiche Summe' (M $u2 $sonnet).Total.In (M $u $sonnet).Total.In

# Append: new Claude message, a later (higher) stream state of msg_4, and a new Codex count.
$add1 = (ClaudeLine 'msg_5' $sonnet $t12 1 0 0 1) + "`n" + (ClaudeLine 'msg_4' $sonnet $t12 10 0 0 4) + "`n"
[IO.File]::AppendAllText("$claude\b.jsonl", $add1, $utf8)
$add2 = (CodexCount $t12.AddMinutes(2) 1500 150) + "`n"
[IO.File]::AppendAllText("$codex\rollout-s2.jsonl", $add2, $utf8)
$u3 = Run-Usage
$s3m = M $u3 $sonnet; $x3 = M $u3 'gpt-6.1-sol'
Check 'Inkrementell: 2 Dateien neu gelesen' $u3.Stats.FilesRead 2
Check 'Inkrementell: nur angehaengte Bytes' $u3.Stats.BytesRead ($utf8.GetByteCount($add1) + $utf8.GetByteCount($add2))
Check 'Inkrementell: Sonnet In (msg_5 neu, msg_4 gleich)' $s3m.Total.In (531 + 1)
Check 'Inkrementell: Sonnet Out (msg_4 jetzt 4, msg_5 1)' $s3m.Total.Out (56 + 3 + 1)
Check 'Inkrementell: Codex heute In' $x3.Today.In (3000 + 1500)
Check 'Inkrementell: Codex gesamt Out' $x3.Total.Out (450 + 150)

# Cache damaged -> full rebuild gives the same result.
[IO.File]::WriteAllBytes($cacheFile, [byte[]](1, 2, 3))
$u4 = Run-Usage
Check 'Kaputter Cache: Neuaufbau gleiche Summe' (M $u4 $sonnet).Total.Out (M $u3 $sonnet).Total.Out
Check 'Kaputter Cache: liest wieder 5 Dateien' $u4.Stats.FilesRead 5

Remove-Item $base -Recurse -Force -ErrorAction SilentlyContinue
if ($script:fail -eq 0) { "ALLE PRUEFUNGEN PASS"; exit 0 } else { "$($script:fail) PRUEFUNG(EN) FAIL"; exit 1 }
