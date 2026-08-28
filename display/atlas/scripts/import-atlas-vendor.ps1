param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Container })]
    [string]$SourcePath
)

$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $SourcePath).Path
$destination = Join-Path $PSScriptRoot '..\vendor'
$required = @(
    'atlas_engine',
    'flutter-atlas-plugins',
    'flutter-elinux-atlas',
    'arc-0.5.0.tgz',
    'atlas-sdk-x86_64-armv8a-generic_arm64-toolchain-26.06.0-unofficial.scarthgap.s6-6-ai.sh'
)

$missing = $required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $source $_)) }
if ($missing) {
    throw "Provided Atlas package is incomplete: $($missing -join ', ')"
}

New-Item -ItemType Directory -Force -Path $destination | Out-Null
foreach ($item in $required) {
    Copy-Item -LiteralPath (Join-Path $source $item) -Destination (Join-Path $destination $item) -Recurse -Force
}

Write-Host "Atlas vendor assets prepared at $destination"
