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

# Azure Container Apps rejects images whose config `created` timestamp has a
# non-UTC offset (Docker 29+/containerd store and podman record local time).
# SOURCE_DATE_EPOCH makes BuildKit emit the timestamp in UTC.
$env:SOURCE_DATE_EPOCH = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
docker build -t $Tag $buildDir
exit $LASTEXITCODE
