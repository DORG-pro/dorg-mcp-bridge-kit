param(
    [Parameter(Mandatory = $true)]
    [string]$Name,

    [ValidateSet("default", "passthrough")]
    [string]$Template = "default"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    python -m cli init-bridge $Name --template $Template
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
