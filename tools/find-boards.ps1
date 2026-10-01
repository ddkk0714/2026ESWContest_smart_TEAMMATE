<#
.SYNOPSIS
  지금 붙어 있는 Wi-Fi(휴대폰 핫스팟 등)에서 DESKMATE Pi 4 / Pi 5 를 찾아 SSH 명령을 알려 준다.

.DESCRIPTION
  1) Wi-Fi 의 IPv4 /24 대역을 병렬 ping 으로 훑고, IPv6 전체 노드(ff02::1)에도 한 번 쏴서 이웃 표를 채운다.
     (보드가 ping 에 답하지 않아도 ARP/ND 표에는 MAC 이 남는다 — 2026-10-01 MHS 에서 확인)
  2) 이웃 표에서 보드 MAC(wlan0)을 찾는다. 모르는 라즈베리파이 MAC 은 후보로만 보여 준다.
  3) 22 번 포트가 열렸는지 보고, 바로 붙여 넣을 ssh 명령을 출력한다.
  -UpdateSshConfig 를 주면 ~/.ssh/config 의 `atlas-hs`(Pi 4)·`pi5-hs`(Pi 5) 항목을 이 주소로 만들거나 고친다.
  Pi 4 는 예전 교내 LAN 주소(172.16.34.146)로 저장해 둔 호스트 키로 검증한다(HostKeyAlias) — 주소가 바뀌어도
  키 경고 없이 같은 보드인지 확인된다.

  CloudflareWARP 같은 VPN 이 켜져 있으면 핫스팟 사설 주소 접속을 가로챌 수 있다. 안 잡히면 잠시 끈다.
  핫스팟 SSID·비밀번호는 이 스크립트와 저장소에 적지 않는다.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\find-boards.ps1
  powershell -ExecutionPolicy Bypass -File tools\find-boards.ps1 -UpdateSshConfig    # 이후 ssh atlas-hs / ssh pi5-hs
#>
[CmdletBinding()]
param(
    [string]$Interface = 'Wi-Fi',
    [string]$Pi4Mac = 'dc-a6-32-85-f3-73',         # Pi 4 wlan0 (IPv6 링크로컬 fe80::dea6:32ff:fe85:f373)
    [string]$Pi5Mac = '88-a2-9e-3c-cc-b4',         # Pi 5 wlan0
    [string]$Pi4KeyAlias = '172.16.34.146',        # known_hosts 에 Pi 4 키가 저장된 이름
    [string]$Pi5KeyAlias = '172.16.34.197',        # known_hosts 에 Pi 5 키가 저장된 이름
    [switch]$UpdateSshConfig
)
$ErrorActionPreference = 'Stop'
# 라즈베리파이 OUI(후보 표시용)
$rpiOui = @('b8-27-eb', 'dc-a6-32', 'e4-5f-01', 'd8-3a-dd', '2c-cf-67', '88-a2-9e')

$adapter = Get-NetAdapter -Name $Interface -ErrorAction SilentlyContinue
if (-not $adapter -or $adapter.Status -ne 'Up') { throw "$Interface 어댑터가 연결돼 있지 않다. 핫스팟에 먼저 붙는다." }
$ssid = ((netsh wlan show interfaces) -match '^\s*SSID\s*:' | Select-Object -First 1) -replace '^\s*SSID\s*:\s*', ''
$addr = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '169.254*' } | Select-Object -First 1
if (-not $addr) { throw "$Interface 에 IPv4 주소가 없다." }
$prefix = ($addr.IPAddress -split '\.')[0..2] -join '.'
Write-Host "Wi-Fi '$ssid' — PC $($addr.IPAddress)/$($addr.PrefixLength), $prefix.0/24 를 훑는다..."

# 1) 병렬 ping (응답 여부보다 이웃 표 채우기가 목적)
$tasks = foreach ($i in 1..254) {
    $p = New-Object System.Net.NetworkInformation.Ping
    $p.SendPingAsync("$prefix.$i", 500)
}
[void][System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]$tasks, 4000)
& ping.exe -6 -n 1 -w 500 "ff02::1%$($adapter.ifIndex)" | Out-Null
Start-Sleep -Milliseconds 500

# 2) 이웃 표에서 보드 찾기
$neighbors = Get-NetNeighbor -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 |
    Where-Object { $_.State -notin 'Unreachable', 'Permanent', 'Incomplete' -and $_.LinkLayerAddress }
function Find-Board([string]$mac) {
    $neighbors | Where-Object { $_.LinkLayerAddress.ToLower() -eq $mac.ToLower() } | Select-Object -First 1
}
function Test-Ssh([string]$ip) {
    $c = New-Object Net.Sockets.TcpClient
    try { return $c.ConnectAsync($ip, 22).Wait(1500) } catch { return $false } finally { $c.Close() }
}

$found = @{}
foreach ($b in @(@{Name = 'Pi 4'; Mac = $Pi4Mac; Alias = $Pi4KeyAlias; Host = 'atlas-hs'},
                 @{Name = 'Pi 5'; Mac = $Pi5Mac; Alias = $Pi5KeyAlias; Host = 'pi5-hs'})) {
    $n = Find-Board $b.Mac
    if ($n) {
        $ssh = Test-Ssh $n.IPAddress
        $found[$b.Host] = @{Ip = $n.IPAddress; Alias = $b.Alias}
        Write-Host ("{0,-5} {1,-15} ssh22={2}" -f $b.Name, $n.IPAddress, $ssh) -ForegroundColor Green
        Write-Host ("      ssh -o HostName={0} -o HostKeyAlias={1} root@{0}" -f $n.IPAddress, $b.Alias)
    } else {
        Write-Host ("{0,-5} 못 찾음 — 보드가 이 핫스팟에 붙어 있는지, 재부팅 뒤 핫스팟 연결이 풀리지 않았는지 확인" -f $b.Name) -ForegroundColor Yellow
    }
}
$others = $neighbors | Where-Object {
    $m = $_.LinkLayerAddress.ToLower()
    ($rpiOui -contains $m.Substring(0, 8)) -and $m -ne $Pi4Mac.ToLower() -and $m -ne $Pi5Mac.ToLower()
}
foreach ($o in $others) { Write-Host ("후보  {0,-15} {1} (다른 라즈베리파이)" -f $o.IPAddress, $o.LinkLayerAddress) }

# 3) ssh config 갱신(선택)
if ($UpdateSshConfig -and $found.Count -gt 0) {
    $cfgPath = Join-Path $env:USERPROFILE '.ssh\config'
    $text = if (Test-Path $cfgPath) { Get-Content $cfgPath -Raw -Encoding UTF8 } else { '' }
    foreach ($h in $found.Keys) {
        $block = "Host $h`r`n    HostName $($found[$h].Ip)`r`n    HostKeyAlias $($found[$h].Alias)`r`n    User root`r`n    LogLevel ERROR`r`n    ServerAliveInterval 30`r`n"
        $pattern = "(?ms)^Host $([regex]::Escape($h))\r?\n(?:[ \t]+.*\r?\n?)*"
        if ($text -match $pattern) { $text = [regex]::Replace($text, $pattern, $block) }
        else { $text = $text.TrimEnd() + "`r`n`r`n" + $block }
    }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($cfgPath, $text, $utf8)
    Write-Host "~/.ssh/config 갱신: $(($found.Keys | Sort-Object) -join ', ') → 이제 'ssh atlas-hs' / 'ssh pi5-hs'"
}
