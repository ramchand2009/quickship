param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^https://')]
    [string]$SiteUrl
)

$ErrorActionPreference = 'Stop'
$mobileRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$manifestUrl = $SiteUrl.TrimEnd('/') + '/manifest.webmanifest'

Push-Location $mobileRoot
try {
    if (-not (Test-Path (Join-Path $mobileRoot 'node_modules'))) {
        npm install
    }
    npx bubblewrap init --manifest $manifestUrl --directory $mobileRoot
}
finally {
    Pop-Location
}
