# PC 에서 Pi 4 자세 서비스를 전처럼 본다 (2026-10-22).
#   powershell -ExecutionPolicy Bypass -File .\view.ps1            # ssh 터널(pi4-via-pi5) + 로컬 http.server 8790 + 브라우저
#   powershell -ExecutionPolicy Bypass -File .\view.ps1 -Direct 172.20.10.3   # Pi 4 가 핫스팟에 직접 있을 때(터널 없이)
# Pi 4 는 보통 Pi 5(핫스팟) 뒤 랜선에만 있어 노트북에서 직접 안 닿는다. 그래서 ssh -L 로 :8771(자세)·:8772(원본 격자)를
# 127.0.0.1 로 끌어오고, posture_view.html 에 ?base=…&raw=… 로 그 주소를 넘긴다. 창을 닫으면 터널도 끝난다.
param(
    [string]$Direct = "",
    [string]$Jump = "pi4-via-pi5",
    [int]$HttpPort = 8790
)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

if ($Direct -ne "") {
    $base = "http://$Direct`:8771"; $raw = "http://$Direct`:8772"
} else {
    $base = "http://127.0.0.1:8771"; $raw = "http://127.0.0.1:8772"
    $existing = Get-NetTCPConnection -LocalPort 8771 -State Listen -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        Write-Host "ssh 터널 여는 중: $Jump (:8771, :8772 -> 127.0.0.1)"
        Start-Process -WindowStyle Minimized ssh -ArgumentList "-N", "-o", "ServerAliveInterval=15", "-o", "ExitOnForwardFailure=yes", "-L", "8771:127.0.0.1:8771", "-L", "8772:127.0.0.1:8772", $Jump
        Start-Sleep -Seconds 4
    } else { Write-Host "ssh 터널 이미 있음 (8771 LISTEN)" }
}

$srv = Get-NetTCPConnection -LocalPort $HttpPort -State Listen -ErrorAction SilentlyContinue
if ($null -eq $srv) {
    Write-Host "로컬 http.server :$HttpPort (file:// 로 열면 fetch 가 막혀서 필요)"
    Start-Process -WindowStyle Minimized python -ArgumentList "-m", "http.server", "$HttpPort", "--bind", "127.0.0.1"
    Start-Sleep -Seconds 2
}

$url = "http://127.0.0.1:$HttpPort/posture_view.html?base=$([uri]::EscapeDataString($base))&raw=$([uri]::EscapeDataString($raw))"
Write-Host "여는 중: $url"
Start-Process $url
try {
    $h = Invoke-RestMethod "$base/health" -TimeoutSec 5
    Write-Host ("health: ok={0} online_sensors={1}" -f $h.ok, $h.online_sensors)
} catch { Write-Host "health 응답 없음: $($_.Exception.Message)  (보드에서 sh start.sh, sh relay.sh 확인)" }
