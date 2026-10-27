# Shared helpers of the test scripts. Every test builds its own throw-away project (project file, repository folder, runtime folder, rule fixtures) from
# examples\project.example.json; nothing here ever touches a real project or its runtime data.
$script:ToolsRoot = Split-Path $PSScriptRoot -Parent
$ToolsRoot = $script:ToolsRoot

# Builds the test project below $Base and points $env:ORCH_PROJECT to its project file. Returns the path of the project file.
# The rule fixtures give the phases different rule sets: "Coding style" only for work, "Process rules" for every phase.
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
    return $pf
}
