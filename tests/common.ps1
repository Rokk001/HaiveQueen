# Shared helpers of the test scripts. Every test builds its own throw-away project (project file, repository folder, runtime folder, rule fixtures) from
# examples\project.example.json and its own test runtime (own database, settings and log folders below the test folder); nothing here ever touches a real project or its runtime data.
$script:ToolsRoot = Split-Path $PSScriptRoot -Parent
$ToolsRoot = $script:ToolsRoot

# Time limit of the test itself: when the test is still running after ORCH_TEST_LIMIT_SEC (default 600) it ends with an error instead of waiting
# (the timer runs on its own thread, so it also fires while the test is blocked in a wait; tests\run-test.ps1 adds a limit from outside that ends the whole process tree).
$limitSec = if ($env:ORCH_TEST_LIMIT_SEC) { [int]$env:ORCH_TEST_LIMIT_SEC } else { 600 }
# (a plain .NET class: a script block cannot run on a timer thread because that thread has no PowerShell runspace)
if (-not ('OrchTestWatchdog' -as [type])) {
    Add-Type -TypeDefinition @'
public static class OrchTestWatchdog {
    private static System.Threading.Timer timer;
    // Starts the timer; when it fires the process writes the reason to stderr and exits with 124.
    public static void Start(int seconds) {
        timer = new System.Threading.Timer(delegate(object s) { System.Console.Error.WriteLine("TEST TIME LIMIT HIT: the test was still running, ending it with an error"); System.Environment.Exit(124); }, null, seconds * 1000, System.Threading.Timeout.Infinite);
    }
}
'@
}
[OrchTestWatchdog]::Start($limitSec)

# Builds the test project below $Base and points $env:ORCH_PROJECT to its project file. Returns the path of the project file.
# The rule fixtures give the phases different rule sets: "Coding style" only for work, "Process rules" for every phase.
# It also creates the test runtime: ORCH_DATA_DIR (database), ORCH_CONFIG_DIR (settings) and ORCH_LOG_DIR (text mirrors, the runtime folder) point below $Base and the project name in the
# database is "testproj"; the plans of the test project are the Markdown files below <repo>\docs\plans (any depth).
function New-TestProject([string]$Base, [string]$Repo = '', [string]$Runtime = '') {
    if (-not $Repo) { $Repo = Join-Path $Base 'repo' }
    if (-not $Runtime) { $Runtime = Join-Path $Base 'rt' }
    New-Item -ItemType Directory -Force $Base, $Repo, $Runtime | Out-Null
    $j = Get-Content (Join-Path $script:ToolsRoot 'examples\project.example.json') -Raw -Encoding UTF8 | ConvertFrom-Json -AsHashtable
    $j.repo = $Repo; $j.runtime = $Runtime
    Copy-Item (Join-Path $script:ToolsRoot 'examples\project-rules.example.md') (Join-Path $Base 'project-rules.md') -Force
    [System.IO.File]::WriteAllText((Join-Path $Repo 'CLAUDE.md'), "# Repo rules`n`n## Coding style`nUse four spaces.`n`n## Process rules`nNever bypass the checks.`n", (New-Object System.Text.UTF8Encoding($false)))
    $j.ruleSources = [ordered]@{ repo = '{repo}/CLAUDE.md'; orch = '{tools}/ORCHESTRATOR.md'; project = '{projectDir}\project-rules.md' }
    $orch = @('## 1. Hard rules'); $proj = @('## 1. Hard rules (project)')
    $j.phaseRules = [ordered]@{
        work = [ordered]@{ repo = @('## Coding style', '## Process rules'); orch = $orch; project = $proj }
        verify = [ordered]@{ repo = @('## Process rules'); orch = $orch; project = $proj }
        release = [ordered]@{ repo = @('## Process rules'); orch = $orch; project = $proj }
        integrate = [ordered]@{ repo = @('## Process rules'); orch = $orch; project = $proj }
    }
    $json = $j | ConvertTo-Json -Depth 10
    $null = $json | ConvertFrom-Json   # the project file must parse before it is saved
    $pf = Join-Path $Base 'project.json'
    [System.IO.File]::WriteAllText($pf, $json, (New-Object System.Text.UTF8Encoding($false)))
    $env:ORCH_PROJECT = $pf
    $env:ORCH_DATA_DIR = Join-Path $Base 'orch-data'; $env:ORCH_CONFIG_DIR = Join-Path $Base 'orch-config'; $env:ORCH_LOG_DIR = $Runtime; $env:ORCH_PROJECT_NAME = 'testproj'
    New-Item -ItemType Directory -Force $env:ORCH_DATA_DIR, $env:ORCH_CONFIG_DIR, (Join-Path $Repo 'docs\plans'), (Join-Path $Repo '.orchestrator') | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $Repo '.orchestrator\project.toml'), "name = `"testproj`"`nplanGlobs = [`"docs/plans/**/*.md`"]`n", (New-Object System.Text.UTF8Encoding($false)))
    return $pf
}

# Empties the database of the test runtime (agents, events, queue, questions, kv, ...): the next runtime call creates a fresh one. Only the test database is touched.
function Reset-TestDb {
    if (-not $env:ORCH_DATA_DIR -or -not $env:ORCH_RUNTIME_OVERRIDE) { throw 'Reset-TestDb: no test runtime active, refusing to delete anything' }
    Remove-Item -LiteralPath (Join-Path $env:ORCH_DATA_DIR 'orchestrator.db'), (Join-Path $env:ORCH_DATA_DIR 'orchestrator.db-wal'), (Join-Path $env:ORCH_DATA_DIR 'orchestrator.db-shm') -ErrorAction SilentlyContinue
}

# Writes a plan file in the v2 format (TOML header between +++ lines, then Markdown); an empty $Status writes a plan without any header. $Packages: array of hashtables with id, title and optional class, agent, capability, depends (array of ids).
function Write-TestPlan([string]$Path, [string]$Status, $Packages = @(), [string]$Title = 'Dummy plan') {
    New-Item -ItemType Directory -Force (Split-Path $Path -Parent) | Out-Null
    if (-not $Status) { [System.IO.File]::WriteAllText($Path, "# $Title`nNo header here.`n", (New-Object System.Text.UTF8Encoding($false))); return }
    $t = "+++`nstatus = `"$Status`"`n"
    foreach ($p in @($Packages)) {
        $t += "[[packages]]`nid = `"$($p.id)`"`ntitle = `"$($p.title)`"`n"
        if ($p.class) { $t += "class = `"$($p.class)`"`n" }
        if ($p.agent) { $t += "agent = `"$($p.agent)`"`n" }
        if ($p.capability) { $t += "capability = `"$($p.capability)`"`n" }
        $t += 'depends_on = [' + ((@($p.depends) | Where-Object { $_ } | ForEach-Object { "`"$_`"" }) -join ', ') + "]`n"
    }
    $t += "+++`n# $Title`n"
    [System.IO.File]::WriteAllText($Path, $t, (New-Object System.Text.UTF8Encoding($false)))
}
