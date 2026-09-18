# Pi 5 를 atlas-dev 컨테이너의 배포 대상으로 등록한다. 저장소 루트에서 실행한다.
#   .\display\atlas\scripts\pi-setup.ps1
$ErrorActionPreference = 'Stop'
$compose = Join-Path $PSScriptRoot '..\compose.yaml'
$deviceEnv = Join-Path $PSScriptRoot '..\deploy\device.env'

if (-not (Test-Path -LiteralPath $deviceEnv)) {
    throw "device.env 가 없다. display\atlas\deploy\device.env.example 을 device.env 로 복사해 값을 채운다."
}

docker compose -f $compose exec atlas-dev bash /workspace/display/atlas/scripts/setup-device.sh
if ($LASTEXITCODE -ne 0) { throw "장치 등록 실패 (exit $LASTEXITCODE)" }
