$ErrorActionPreference = 'Stop'
$vendor = Join-Path $PSScriptRoot '..\vendor'
$required = @(
    'atlas_engine',
    'flutter-atlas-plugins',
    'flutter-elinux-atlas',
    'arc-0.5.0.tgz',
    'atlas-sdk-x86_64-armv8a-generic_arm64-toolchain-26.06.0-unofficial.scarthgap.s6-6-ai.sh'
)

$missing = $required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $vendor $_)) }
if ($missing) {
    throw "Atlas vendor assets are missing: $($missing -join ', '). Run import-atlas-vendor.ps1 with the extracted official Atlas package."
}

Write-Host "Atlas vendor assets are ready: $vendor"
