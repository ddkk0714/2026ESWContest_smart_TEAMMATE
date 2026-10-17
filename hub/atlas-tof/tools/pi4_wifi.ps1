# Pi 4(ATLAS) 를 휴대폰 핫스팟에 붙인다. 비밀번호는 여기서만 읽어 ssh 인자로 보드에 넘긴다(저장 안 함).
$ssid = Read-Host "SSID (Enter = So)"
if (-not $ssid) { $ssid = "So" }
$sec = Read-Host "hotspot password (not shown)" -AsSecureString
$p = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
$p = $p -replace "'", "'\''"
$ssid = $ssid -replace "'", "'\''"
ssh pi4-via-pi5 "sh /tmp/board_wifi_connect.sh '$ssid' '$p'"
Write-Host "exit: $LASTEXITCODE"
