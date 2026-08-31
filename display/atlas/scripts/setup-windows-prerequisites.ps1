param(
    [Parameter(Mandatory = $true)]
    [string]$ToolRoot
)

$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated Administrator PowerShell.'
}

$toolRootPath = [IO.Path]::GetFullPath($ToolRoot)
if (-not $toolRootPath.StartsWith('D:\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'ToolRoot must be on D: for this development host.'
}

$paths = @(
    $toolRootPath,
    (Join-Path $toolRootPath 'DockerDesktop'),
    (Join-Path $toolRootPath 'DockerData'),
    (Join-Path $toolRootPath 'Installers')
)
foreach ($path in $paths) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
}

$featureNames = @(
    'Microsoft-Windows-Subsystem-Linux',
    'VirtualMachinePlatform'
)

$restartNeeded = $false
foreach ($featureName in $featureNames) {
    $feature = Get-WindowsOptionalFeature -Online -FeatureName $featureName
    if ($feature.State -ne 'Enabled') {
        Write-Host "Enabling Windows feature: $featureName"
        $result = Enable-WindowsOptionalFeature `
            -Online `
            -FeatureName $featureName `
            -All `
            -NoRestart
        $restartNeeded = $restartNeeded -or $result.RestartNeeded
    } else {
        Write-Host "Windows feature already enabled: $featureName"
    }
}

$state = [ordered]@{
    completed_at = (Get-Date).ToString('o')
    tool_root = $toolRootPath
    restart_needed = $restartNeeded
    features = $featureNames
    next_step = 'Restart Windows, then install Docker Desktop with its installation and WSL data roots on D:.'
}
$state | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $toolRootPath 'wsl-prerequisites.json') -Encoding utf8

Write-Host "WSL prerequisites configured. Tool root: $toolRootPath"
Write-Host "Restart needed: $restartNeeded"
