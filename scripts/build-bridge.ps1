param(
    [Parameter(Mandatory = $true)]
    [string]$Bridge,

    [Parameter(Mandatory = $true)]
    [string]$Tag
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

& (Join-Path $PSScriptRoot "package-bridge.ps1") -Bridge $Bridge
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$buildDir = Join-Path $root "bridges" $Bridge ".build"
if (-not (Test-Path $buildDir)) {
    throw "Build context not found: $buildDir"
}

docker build -t $Tag $buildDir
exit $LASTEXITCODE
