# Test of the provider list (plan section 15): selection by weekly limit, needed capability at a full limit, provider without limit, slots 0, a busy slot forwards at once,
# "capability-only" gets no normal packages, "fallback" only when every "preferred" provider is full or at its limit, and a dummy provider that exists only in the project file.
# A package that names its capability in the plan header (field "capability") needs it whatever its title says. All limit values are invented (ORCH_PROVIDER_LIMITS_FILE);
# everything runs in a temporary folder with its own runtime and database; no pane is opened (ORCH_TEST_NOSTART) and no model is called.
. (Join-Path $PSScriptRoot 'common.ps1')
$tools = $ToolsRoot
$root = Join-Path ([IO.Path]::GetTempPath()) ('orch-test-prov-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force "$root\rt\runs", "$root\wt" | Out-Null
$pf = New-TestProject -Base $root -Repo "$root\repo" -Runtime "$root\rt"
$env:ORCH_RUNTIME_OVERRIDE = "$root\rt"; $env:ORCH_TEST_NOSTART = '1'
. (Join-Path $tools 'lib.ps1'); . (Join-Path $tools 'routine.ps1')
$fails = 0
function Check([string]$name, [bool]$ok, [string]$detail = '') { if (-not $ok) { $script:fails++ }; Write-Host ('{0} {1} {2}' -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' }) }

# One provider entry as the project file has it.
function Prov([string]$name, [int]$slots, [string]$use = 'preferred', [string[]]$caps = @(), [string]$limit = '', [string[]]$classes = @('light', 'medium', 'heavy'), [string]$proto = 'claude') {
    $cl = [ordered]@{}; foreach ($c in $classes) { $cl[$c] = [ordered]@{ model = "$name-$c"; effort = 'medium' } }
    $e = [ordered]@{ name = $name; protocol = $proto; command = $proto; args = @('--model', '{model}'); slots = $slots; use = $use; capabilities = @($caps); classes = $cl }
    if ($limit) { $e.limit = [ordered]@{ source = $limit } }
    return [pscustomobject]$e
}
# A minimal config object with the given providers.
function Cfg2($providers) { return [pscustomobject]@{ providers = @($providers) } }
# Writes the invented weekly limits: name -> @(usedPercent, resetHours).
function Set-Limits($h) {
    $o = [ordered]@{}; foreach ($k in $h.Keys) { $o[$k] = [ordered]@{ usedPercent = $h[$k][0]; resetHours = $h[$k][1] } }
    Set-Content "$root\limits.json" (ConvertTo-Json -InputObject $o) -Encoding UTF8; $env:ORCH_PROVIDER_LIMITS_FILE = "$root\limits.json"
}
# Name of the provider that gets a task (or 'none').
function Pick($cfg2, [string]$class, [string[]]$needs, $free, [string]$avoid = '', [string]$prefer = '') { $p = Select-Provider $cfg2 $class $needs $free -Avoid $avoid -Prefer $prefer; if ($p) { return $p.Name } else { return 'none' } }

# --- 1. selection by weekly limit: (100 - used) / hours to reset --------------------------------------
$c1 = Cfg2 @((Prov 'A' 2 'preferred' @() 'x'), (Prov 'B' 2 'preferred' @() 'x'))
$free = @{ A = 2; B = 2 }
Set-Limits @{ A = @(80, 24); B = @(20, 48) }   # A: 20/24 = 0.83, B: 80/48 = 1.67
Check 'higher (100 - used) / hours wins' ((Pick $c1 'medium' @() $free) -eq 'B')
Set-Limits @{ A = @(10, 24); B = @(20, 48) }   # A: 3.75, B: 1.67
Check 'the winner changes with the limit values' ((Pick $c1 'medium' @() $free) -eq 'A')
Set-Limits @{ A = @(50, 100); B = @(50, 10) }   # an early reset is worth more: B 5.0, A 0.5
Check 'the sooner reset wins at equal usage' ((Pick $c1 'medium' @() $free) -eq 'B')
Set-Limits @{}
Check 'equal values: list order decides' ((Pick (Cfg2 @((Prov 'A' 1), (Prov 'B' 1))) 'medium' @() @{ A = 1; B = 1 }) -eq 'A')

# --- 2. needed capability: only providers with it, the weekly limit does not matter ------------------------
$c2 = Cfg2 @((Prov 'A' 2 'preferred' @() 'x'), (Prov 'G' 2 'preferred' @('images') 'x'))
$free = @{ A = 2; G = 2 }
Set-Limits @{ A = @(0, 10); G = @(100, 10) }   # G is at its weekly limit
Check 'without a need, a provider at its weekly limit drops out' ((Pick $c2 'medium' @() $free) -eq 'A')
Check 'a needed capability goes to the provider with it, even at a full limit' ((Pick $c2 'medium' @('images') $free) -eq 'G')
Check 'a need no provider covers: nobody' ((Pick $c2 'medium' @('voice') $free) -eq 'none')

# --- 3. provider without weekly limit counts as 100 % free ------------------------------------------------
$c3 = Cfg2 @((Prov 'A' 2 'preferred' @() 'x'), (Prov 'L' 1 'preferred' @() ''))
$free = @{ A = 2; L = 1 }
Set-Limits @{ A = @(95, 10) }   # A: 0.5, L (no limit): 100 / 168 = 0.595
Check 'no limit = 100 % free beats a nearly used provider' ((Pick $c3 'medium' @() $free) -eq 'L')
Set-Limits @{ A = @(10, 100) }   # A: 0.9
Check 'no limit does not beat a fresh provider with a reset in sight' ((Pick $c3 'medium' @() $free) -eq 'A')
$lim = Get-ProviderLimit $c3 (Get-Providers $c3 | Where-Object { $_.Name -eq 'L' })
Check 'no limit source: 100 % free' ($lim.FreePct -eq 100) "free=$($lim.FreePct)"

# --- 4. slots 0 = off for everything ------------------------------------------------------------------------
Set-Limits @{}
$c4 = Cfg2 @((Prov 'A' 0), (Prov 'G' 0 'preferred' @('images')), (Prov 'B' 1))
$free = @{ A = 0; G = 0; B = 1 }
Check 'slots 0: never chosen (normal package)' ((Pick $c4 'medium' @() $free) -eq 'B')
Check 'slots 0: never chosen, also not for a needed capability' ((Pick $c4 'medium' @('images') @{ A = 0; G = 5; B = 1 }) -eq 'none')
Check 'capability is not available when its provider has slots 0' (-not (Test-CapabilityAvailable $c4 @('images')))
Check 'capability is available when a provider with it has slots above 0' (Test-CapabilityAvailable (Cfg2 @((Prov 'G' 1 'preferred' @('images')))) @('images'))

# --- 5. a busy slot forwards at once -----------------------------------------------------------------------
$c5 = Cfg2 @((Prov 'A' 2), (Prov 'B' 2))
Check 'busy slot of the first: the next provider takes it' ((Pick $c5 'medium' @() @{ A = 0; B = 2 }) -eq 'B')
Check 'all slots busy: nobody (the package waits for the next round)' ((Pick $c5 'medium' @() @{ A = 0; B = 0 }) -eq 'none')
Check 'follow-up task stays with its earlier provider when that one is free' ((Pick $c5 'medium' @() @{ A = 2; B = 2 } -prefer 'B') -eq 'B')
Check 'follow-up task moves on when the earlier provider is busy' ((Pick $c5 'medium' @() @{ A = 2; B = 0 } -prefer 'B') -eq 'A')
Check 'verify prefers another provider than the work agent' ((Pick $c5 'medium' @() @{ A = 2; B = 2 } -avoid 'A') -eq 'B')
Check 'verify takes the same provider when no other is free' ((Pick $c5 'medium' @() @{ A = 2; B = 0 } -avoid 'A') -eq 'A')
$rows = @([pscustomobject]@{ status = 'running'; phase = 'work'; type = 'claude'; provider = 'B' }, [pscustomobject]@{ status = 'starting'; phase = 'work'; type = 'claude' }, [pscustomobject]@{ status = 'running'; phase = 'integrate'; type = 'claude'; provider = 'A' }, [pscustomobject]@{ status = 'done'; phase = 'work'; provider = 'A' })
$f = Get-ProviderFree (Cfg2 @((Prov 'A' 2), (Prov 'B' 2), (Prov 'claude' 2))) $rows
Check 'free slots count running and starting workers per provider, not the integrator, not ended ones, a row without provider uses its type' (($f.A -eq 2) -and ($f.B -eq 1) -and ($f.claude -eq 1)) "A=$($f.A) B=$($f.B) claude=$($f.claude)"

# --- 6. capability-only gets no normal packages --------------------------------------------------------------
Set-Limits @{ A = @(100, 1) }   # the only normal provider is at its limit, so the best score belongs to the capability-only provider
$c6 = Cfg2 @((Prov 'A' 1 'preferred' @() 'x'), (Prov 'G' 2 'capability-only' @('images')))
$free = @{ A = 1; G = 2 }
Check 'capability-only: a normal package never goes there' ((Pick $c6 'medium' @() $free) -eq 'none')
Check 'capability-only: a package that needs the capability does' ((Pick $c6 'medium' @('images') $free) -eq 'G')

# --- 7. fallback only when every preferred provider is full or at its limit ------------------------------------
Set-Limits @{ P = @(60, 100); F = @(0, 1) }   # the fallback provider has the far better score
$c7 = Cfg2 @((Prov 'P' 1), (Prov 'F' 2 'fallback' @() 'x'))
Check 'fallback is not used while a preferred provider has a free slot' ((Pick $c7 'medium' @() @{ P = 1; F = 2 }) -eq 'P')
Check 'fallback takes over when the preferred slots are full' ((Pick $c7 'medium' @() @{ P = 0; F = 2 }) -eq 'F')
Set-Limits @{ P = @(100, 100); F = @(0, 1) }
$c7b = Cfg2 @((Prov 'P' 1 'preferred' @() 'x'), (Prov 'F' 2 'fallback' @() 'x'))
Check 'fallback takes over when the preferred provider is at its weekly limit' ((Pick $c7b 'medium' @() @{ P = 1; F = 2 }) -eq 'F')
Check 'a preferred provider without the class does not block the fallback' ((Pick (Cfg2 @((Prov 'P' 1 'preferred' @() '' @('medium')), (Prov 'F' 1 'fallback'))) 'light' @() @{ P = 1; F = 1 }) -eq 'F')

# --- 8. only the classes of the entry; a dummy provider that exists only in the project file ----------------------
Set-Limits @{}
$dummy = Prov 'dummy' 1 'preferred' @() '' @('medium')
$c8 = Cfg2 @((Prov 'A' 1), $dummy)
Check 'dummy provider is found by name' ((Get-Provider $c8 'dummy').Slots -eq 1)
Check 'a provider gets only the classes of its entry (light is not in the dummy entry)' ((Pick (Cfg2 @($dummy)) 'light' @() @{ dummy = 1 }) -eq 'none')
Check 'the dummy takes a medium package when the others are busy' ((Pick $c8 'medium' @() @{ A = 0; dummy = 1 }) -eq 'dummy')
Check 'class model of the dummy: its own model' ((Get-ClassModel $c8 'dummy' 'medium').Model -eq 'dummy-medium')
Check 'class model of an unlisted class falls back to the nearest listed class' ((Get-ClassModel $c8 'dummy' 'heavy').Model -eq 'dummy-medium')
$threw = $false; try { [void](Get-Providers (Cfg2 @((Prov 'X' 1 'sometimes')))) } catch { $threw = $_.Exception.Message -match 'use must be' }
Check 'an unknown use value is refused' $threw

# --- 9. the graphics rule comes from the configuration; endpoint arguments ------------------------------------
Check 'default rule: Blender models need images' ((@(Get-PackageNeeds ([pscustomobject]@{}) 'Blender models and textures')) -contains 'images')
Check 'default rule: a code package needs nothing' (@(Get-PackageNeeds ([pscustomobject]@{}) 'Change the parser').Count -eq 0)
$rules = [pscustomobject]@{ capabilityRules = @([pscustomobject]@{ capability = 'voice'; patterns = @('(?i)\bnarration\b') }) }
Check 'rule from the configuration' ((@(Get-PackageNeeds $rules 'Record the narration') -contains 'voice') -and @(Get-PackageNeeds $rules 'Blender models').Count -eq 0)
$lm = [pscustomobject]@{ name = 'lmstudio'; protocol = 'codex'; slots = 1; use = 'preferred'; classes = [pscustomobject]@{ light = [pscustomobject]@{ model = 'qwen-coder'; effort = 'medium' } }; endpoint = [pscustomobject]@{ baseUrl = 'http://192.0.2.10:1234/v1' } }
$ea = @(Get-EndpointArgs (Get-Providers (Cfg2 @($lm)) | Select-Object -First 1))
# A package that names its capability in the plan header keeps it (not only the title rules decide): declared capability first, title rules only as the fallback.
Check 'declared capability is used although the title says nothing' ((@(Get-PackageNeeds ([pscustomobject]@{}) 'Draw the logo' 'images')) -contains 'images')
Check 'declared capability replaces the title rules' ((@(Get-PackageNeeds ([pscustomobject]@{}) 'Blender models and textures' 'voice')) -join ',' -eq 'voice')
Check 'several declared capabilities (comma separated)' ((@(Get-PackageNeeds ([pscustomobject]@{}) 'Draw the logo' 'images, voice')) -join ',' -eq 'images,voice')
Check 'no declared capability: the title rules are the fallback' ((@(Get-PackageNeeds ([pscustomobject]@{}) 'Blender models and textures' '')) -contains 'images')
Check 'endpoint: model_provider arguments for the codex CLI' (($ea -contains 'model_provider=lmstudio') -and ($ea -contains 'model_providers.lmstudio.base_url=http://192.0.2.10:1234/v1') -and ($ea -contains 'model_providers.lmstudio.wire_api=responses')) ($ea -join ' ')

# --- 10. whole rounds with a provider list that exists only in the project file ----------------------------------
$proj = Get-Content $pf -Raw | ConvertFrom-Json -AsHashtable
# Writes the project file with the given providers.
function Set-ProjectProviders($list) { $proj.providers = @($list); [IO.File]::WriteAllText($pf, ($proj | ConvertTo-Json -Depth 10), (New-Object System.Text.UTF8Encoding($false))) }
$cfg = Get-Cfg
$plan = "$root\repo\docs\plans\dummy\DUMMY-PLAN.md"; $rel = 'docs/plans/dummy/DUMMY-PLAN.md'
# Writes the dummy plan from package hashtables (id, title, optional capability).
function New-Plan($packages) { Write-TestPlan $plan 'IN_PROGRESS' $packages }
function Ag([string]$n) { return @(Read-Agents $cfg | Where-Object { $_.name -eq $n } | Select-Object -First 1) }
function Round { Set-Content "$root\note.json" '{"actions":[{"action":"note","text":"test"}]}' -Encoding UTF8; & pwsh -NoProfile -File (Join-Path $tools 'round.ps1') -BrainAnswerFile "$root\note.json" *> $null }
function Reset-Agents {
    Reset-TestDb; Remove-Item "$($cfg.runtime)\runs" -Recurse -Force -ErrorAction SilentlyContinue
    Update-Agent $cfg 'DUMMY-SEED-work' @{ type = 'claude'; plan = $rel; package = 'SEED'; phase = 'work'; cwd = "$root\wt"; status = 'done'; startedAt = (Get-Date).AddMinutes(-30).ToString('o'); endedAt = (Get-Date).AddMinutes(-20).ToString('o'); exitCode = 0; lastLine = 'seed' }   # gives the plan a known worktree
}
Set-Limits @{}
Reset-Agents
Set-ProjectProviders @((Prov 'dummy' 1 'preferred' @() '' @('light', 'medium', 'heavy')))
New-Plan @(@{ id = 'W1'; title = 'change the parser' }, @{ id = 'W2'; title = 'change the lexer' })
Round
Check 'round: the dummy provider from the project file starts the package' (((Ag 'DUMMY-W1-work').status -eq 'running') -and ((Ag 'DUMMY-W1-work').provider -eq 'dummy')) "provider=$((Ag 'DUMMY-W1-work').provider) type=$((Ag 'DUMMY-W1-work').type)"
Check 'round: its single slot is used up, the second package waits' ((Ag 'DUMMY-W2-work').Count -eq 0)
Reset-Agents
Set-ProjectProviders @((Prov 'dummy' 0), (Prov 'other' 1))
Round
Check 'round: slots 0 = off, the other provider takes the package' (((Ag 'DUMMY-W1-work').provider -eq 'other') -and ((Ag 'DUMMY-W2-work').Count -eq 0)) "provider=$((Ag 'DUMMY-W1-work').provider)"
Reset-Agents
Set-ProjectProviders @((Prov 'dummy' 1), (Prov 'other' 1))
Round
Check 'round: a busy slot forwards at once (two packages, two providers in one round)' ((@(Read-Agents $cfg | Where-Object { $_.phase -eq 'work' -and $_.status -eq 'running' } | ForEach-Object { Get-RowProvider $_ } | Sort-Object) -join ',') -eq 'dummy,other')
Reset-Agents
Set-ProjectProviders @((Prov 'plain' 2), (Prov 'gfx' 1 'capability-only' @('images') '' @('light', 'medium', 'heavy') 'codex'))
New-Plan @(@{ id = 'G1'; title = 'Blender models and textures' }, @{ id = 'W1'; title = 'change the parser' })
Round
Check 'round: a graphics package goes to the provider with images' (((Ag 'DUMMY-G1-work').provider -eq 'gfx') -and ((Ag 'DUMMY-G1-work').type -eq 'codex')) "provider=$((Ag 'DUMMY-G1-work').provider)"
Check 'round: a normal package never goes to the capability-only provider' ((Ag 'DUMMY-W1-work').provider -eq 'plain')
Reset-Agents
New-Plan @(@{ id = 'G2'; title = 'Draw the logo'; capability = 'images' }, @{ id = 'W1'; title = 'change the parser' })
Round
Check 'round: a package that declares the capability in the plan header goes to the provider with images, whatever its title says' (((Ag 'DUMMY-G2-work').provider -eq 'gfx') -and ((Ag 'DUMMY-W1-work').provider -eq 'plain')) "provider=$((Ag 'DUMMY-G2-work').provider)"
Reset-Agents
Set-ProjectProviders @((Prov 'plain' 2), (Prov 'gfx' 0 'capability-only' @('images') '' @('light', 'medium', 'heavy') 'codex'))
New-Plan @(@{ id = 'G1'; title = 'Blender models and textures' }, @{ id = 'W1'; title = 'change the parser' })
Round; Round
$capQ = @(Get-OrchQuestions $cfg | Where-Object { $_.item -eq 'CAP-G1' })
Check 'round: no provider with images has slots above 0: the package stays and is reported' ((Ag 'DUMMY-G1-work').Count -eq 0)
Check 'the question in the database exists exactly once after two rounds' ($capQ.Count -eq 1) "count=$($capQ.Count)"

Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "failures: $fails"
exit $fails
