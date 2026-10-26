param(
  [string]$Pi5 = 'fe80::8aa2:9eff:fe3c:ceca%21',
  [string]$Pi4 = '',
  [string]$KnownHosts = (Join-Path $env:TEMP 'deskmate-pi-discovery-known-hosts'),
  [int]$Port = 8791,
  [string]$AppId = 'com.atlas.app.deskmate_ui_v27'
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$dockerPath = (Get-Command docker -ErrorAction Stop).Source
$buildPath = Join-Path $PSScriptRoot 'build'
New-Item -ItemType Directory -Force -Path $buildPath | Out-Null
if (!(Test-Path -LiteralPath $KnownHosts)) { throw '먼저 신뢰할 수 있는 SSH 연결로 Pi5 호스트 키를 확인해 주세요.' }
# Build only the transient input helper, never alter the installed Pi5 app.
& $dockerPath run --rm --entrypoint bash -v "${repoRoot}:/workspace" deskmate-atlas-dev:local -lc 'source "$ATLAS_FLUTTER_NDK_ENV" && $CC /workspace/tools/laptop-control/ui-input.c -o /workspace/tools/laptop-control/build/ui-input'
if ($LASTEXITCODE -ne 0) { throw 'Pi5 입력 도구 빌드 실패' }
& $dockerPath build -f (Join-Path $PSScriptRoot 'Dockerfile.sensors') -t deskmate-laptop-sensors:local $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw '센서 화면 이미지 빌드 실패' }
$nodeArgs = @((Join-Path $PSScriptRoot 'server.mjs'), '--pi5', $Pi5, '--known-hosts', $KnownHosts,
  '--port', $Port, '--docker', $dockerPath, '--app', $AppId)
if ($Pi4) { $nodeArgs += @('--pi4', $Pi4) }
& node @nodeArgs
