# Atlas Flutter 앱을 ipk 로 빌드해 Pi 5 에 올린다. 저장소 루트에서 실행한다.
#   .\display\atlas\scripts\pi-deploy.ps1
#   .\display\atlas\scripts\pi-deploy.ps1 -Action install -Mode debug -NoTest
#   .\display\atlas\scripts\pi-deploy.ps1 -AppDir display/atlas/app
param(
    [ValidateSet('run', 'install', 'build', 'uninstall')]
    [string]$Action = 'run',

    [ValidateSet('release', 'debug', 'profile')]
    [string]$Mode,

    [string]$AppDir,

    [switch]$NoTest,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$compose = Join-Path $PSScriptRoot '..\compose.yaml'
$deviceEnv = Join-Path $PSScriptRoot '..\deploy\device.env'

if (-not (Test-Path -LiteralPath $deviceEnv)) {
    throw "device.env 가 없다. display\atlas\deploy\device.env.example 을 device.env 로 복사해 값을 채운다."
}

$scriptArgs = @("--$Action")
if ($Mode) { $scriptArgs += @('--mode', $Mode) }
if ($AppDir) { $scriptArgs += @('--app-dir', $AppDir) }
if ($NoTest) { $scriptArgs += '--no-test' }
if ($Clean) { $scriptArgs += '--clean' }

docker compose -f $compose exec atlas-dev bash /workspace/display/atlas/scripts/deploy-app.sh @scriptArgs
if ($LASTEXITCODE -ne 0) { throw "배포 실패 (exit $LASTEXITCODE)" }
