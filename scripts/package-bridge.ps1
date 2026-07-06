param(
    [Parameter(Mandatory = $true)]
    [string]$Project,

    [string]$Manifest = "competency.manifest.json",
    [string]$Documentation = "DOCUMENTATION.md",
    [string]$Handlers = "bridge/competency_handlers.py",
    [string]$BridgeDir = "bridge",
    [switch]$SkipHandlers
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Resolve-FromRoot([string]$path) {
    if ([System.IO.Path]::IsPathRooted($path)) { return $path }
    return Join-Path $root $path
}

$projectPath = Resolve-FromRoot $Project
$manifestPath = Resolve-FromRoot $Manifest
$documentationPath = Resolve-FromRoot $Documentation
$handlersPath = Resolve-FromRoot $Handlers
$bridgeDirPath = Resolve-FromRoot $BridgeDir

if (-not (Test-Path $projectPath)) { throw "Project config not found: $projectPath" }
if (-not (Test-Path $manifestPath)) { throw "Manifest not found: $manifestPath — run generate-manifest first." }

if (-not (Test-Path $documentationPath)) {
    $template = Join-Path $bridgeDirPath "DOCUMENTATION.md.template"
    if (Test-Path $template) {
        Write-Host "DOCUMENTATION.md missing — copying template."
        Copy-Item $template $documentationPath
    } else {
        throw "DOCUMENTATION.md not found: $documentationPath"
    }
}

Copy-Item $manifestPath (Join-Path $bridgeDirPath "competency.manifest.json") -Force
Copy-Item $documentationPath (Join-Path $bridgeDirPath "DOCUMENTATION.md") -Force

if (-not $SkipHandlers) {
    if (Test-Path $handlersPath) {
        Copy-Item $handlersPath (Join-Path $bridgeDirPath "competency_handlers.py") -Force
        Write-Host "  competency_handlers.py"
    } else {
        Write-Host "No handlers file at $handlersPath — tier-1 only image."
    }
}

Write-Host "Packaged into $bridgeDirPath :"
Write-Host "  competency.manifest.json"
Write-Host "  DOCUMENTATION.md"
Write-Host ""
Write-Host "Next: docker build -t <registry>/dorg-mcp-bridge:<tag> $BridgeDir"
