# PC(Windows)에서 Pi 4(ATLAS)로 ToF 자세 서비스 묶음을 올리고 자가 시험 뒤 띄운다.
#   .\deploy-pi4.ps1 -Ip 172.16.34.146               # 기본: 올리기 + selftest + start
#   .\deploy-pi4.ps1 -Ip 172.16.34.146 -NoStart      # 올리기 + selftest 만
#   .\deploy-pi4.ps1 -Ip 172.16.34.146 -Bundle ..\..\..\rpi4_tof_deploy\pi4
# 필요한 것: Windows 기본 OpenSSH(ssh, scp)·tar. 보드는 root 키 인증(비밀번호 없음).
param(
    [Parameter(Mandatory = $true)][string]$Ip,
    [string]$Bundle = "",
    [string]$User = "root",
    [string]$Dest = "/mnt/rawdata/deskmate_tof",
    [switch]$NoStart
)
$ErrorActionPreference = "Stop"
if (-not $Bundle) {   # 묶음 폴더 안에서 실행하면 .\pi4, 레포 hub/atlas-tof/tools 에서 실행하면 sw경진대회\rpi4_tof_deploy\pi4
    $Bundle = Join-Path $PSScriptRoot "pi4"
    if (-not (Test-Path $Bundle)) { $Bundle = Join-Path $PSScriptRoot "..\..\..\..\rpi4_tof_deploy\pi4" }
}
$Bundle = (Resolve-Path $Bundle).Path
if (-not (Test-Path (Join-Path $Bundle "deskmate_tofposture"))) { throw "묶음이 아니다: $Bundle (deskmate_tofposture 없음)" }
$Target = "$User@$Ip"
$SshOpts = @("-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new")

Write-Host "== 1. 보드 확인 ($Target)"
& ssh @SshOpts $Target "tr -d '\0' < /proc/device-tree/model; echo; grep PRETTY /etc/os-release; for p in `$(pidof deskmate_tofposture); do kill `$p; done; mkdir -p $Dest"
if ($LASTEXITCODE -ne 0) { throw "SSH 실패. 보드 IP·전원·네트워크 확인 (README '내일 순서' 0번: DHCP 라 IP 가 바뀌면 노션 「ATLAS 보드 SSH 접속」 3번 포트 스캔)" }

Write-Host "== 2. 복사 → $Dest"
# tar 를 ssh 로 흘려 보낸다(바이너리 안전: PowerShell 파이프가 아니라 cmd 파이프 사용). 실패하면 scp -r.
cmd /c "tar -cf - -C `"$Bundle`" . | ssh -o ConnectTimeout=8 $Target `"tar -xf - -C $Dest`""
if ($LASTEXITCODE -ne 0) {
    Write-Host "   tar 경로 실패 → scp -r 로 재시도"
    & scp @SshOpts -r "$Bundle\*" "${Target}:$Dest/"
    if ($LASTEXITCODE -ne 0) { & scp -O @SshOpts -r "$Bundle\*" "${Target}:$Dest/" }
    if ($LASTEXITCODE -ne 0) { throw "복사 실패" }
}
& ssh @SshOpts $Target "chmod +x $Dest/deskmate_tofposture $Dest/tofposture_selftest $Dest/*.sh; ls -l $Dest $Dest/models"

Write-Host "== 3. 보드 자가 시험 (모델·파서·융합)"
& ssh @SshOpts $Target "sh $Dest/selftest.sh"
if ($LASTEXITCODE -ne 0) { throw "selftest 실패 — 출력 확인" }

if (-not $NoStart) {
    Write-Host "== 4. 서비스 시작"
    & ssh @SshOpts $Target "sh $Dest/start.sh"
    Write-Host ""
    Write-Host "PC 에서 확인:  curl http://${Ip}:8771/api/state"
    Write-Host "보드 상태:     ssh $Target sh $Dest/status.sh"
}
