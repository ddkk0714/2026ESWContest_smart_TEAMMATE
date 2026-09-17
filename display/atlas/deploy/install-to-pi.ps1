<#
빌드된 .ipk 를 Pi 5(ATLAS)에 설치한다. 개발 환경이 필요 없다 — ssh · scp 만 있으면 된다.
WSL 없이 Windows PowerShell 에서 바로 돌아간다. (install-to-pi.sh 의 PowerShell 판)

    .\install-to-pi.ps1 -Ip 192.168.0.100
    .\install-to-pi.ps1 -Ip 192.168.0.100 -Ipk com.atlas.app.deskmate_display_ui_test.ipk
    .\install-to-pi.ps1 -Ip 192.168.0.100 -NoStart

-Ipk 를 생략하면 스크립트와 같은 폴더에서 찾는다.
앱 ID 는 파일 이름에서 가져온다(flutter-atlas 가 "<앱ID>.ipk" 로 굽는다).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Ip,
    [string]$Ipk,
    [string]$BoardUser = 'root',
    [int]$Port = 22,
    [string]$SshKey,
    [string]$AppId,
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
function Die($m)  { Write-Host "X $m" -ForegroundColor Red; exit 1 }
function Ok($m)   { Write-Host "O $m" -ForegroundColor Green }
function Step($m) { Write-Host ''; Write-Host $m -ForegroundColor Cyan }

if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    Die "ssh 가 없습니다. '설정 > 시스템 > 선택적 기능' 에서 'OpenSSH 클라이언트' 를 설치하세요."
}
if (-not (Get-Command scp -ErrorAction SilentlyContinue)) {
    Die "scp 가 없습니다. OpenSSH 클라이언트를 설치하세요."
}

# ---- .ipk 찾기 -------------------------------------------------------------
$here = Split-Path -Parent $PSCommandPath
if (-not $Ipk) {
    $found = @(Get-ChildItem -Path $here -Filter '*.ipk' -File | Sort-Object Name)
    if ($found.Count -eq 0) {
        Die "이 폴더에 .ipk 가 없습니다: $here`n    릴리스에서 받은 .ipk 를 이 스크립트 옆에 두거나 -Ipk 로 경로를 주세요."
    }
    if ($found.Count -gt 1) {
        Write-Host '여러 개를 찾았습니다. -Ipk 로 하나를 지정하세요:' -ForegroundColor Yellow
        $found | ForEach-Object { Write-Host "  $($_.Name)" }
        exit 1
    }
    $Ipk = $found[0].FullName
}
if (-not (Test-Path $Ipk)) { Die ".ipk 를 찾을 수 없습니다: $Ipk" }
$ipkItem = Get-Item $Ipk
$base = $ipkItem.Name
if (-not $AppId) { $AppId = [System.IO.Path]::GetFileNameWithoutExtension($base) }
if ($AppId -notlike 'com.atlas.app.*') {
    Die "앱 ID 를 파일 이름에서 못 읽었습니다: $base`n    'com.atlas.app.<이름>.ipk' 형식이어야 합니다. 다르면 -AppId 로 직접 주세요."
}

$sshOpts = @('-o','BatchMode=yes','-o','StrictHostKeyChecking=no','-o','ConnectTimeout=10','-p',"$Port")
$scpOpts = @('-o','BatchMode=yes','-o','StrictHostKeyChecking=no','-o','ConnectTimeout=10','-P',"$Port")
if ($SshKey) {
    if (-not (Test-Path $SshKey)) { Die "SSH 키가 없습니다: $SshKey" }
    $sshOpts += @('-i', $SshKey); $scpOpts += @('-i', $SshKey)
}
$target = "$BoardUser@$Ip"

# 원격 명령 실행. 네이티브 exe 라 2>&1 리다이렉트를 쓰지 않는다(5.1 에서 ErrorRecord 로 감싸짐).
function Invoke-Board([string]$Cmd) { & ssh @sshOpts $target $Cmd }
function Board-Ok([string]$Cmd) { Invoke-Board $Cmd | Out-Null; return $LASTEXITCODE -eq 0 }

Write-Host "앱   : $AppId"
Write-Host "보드 : ${target}:$Port"
Write-Host "파일 : $base ($($ipkItem.Length) bytes)"

# ---- 1. 연결 ---------------------------------------------------------------
Step '1/5 보드 연결'
if (-not (Board-Ok 'true')) {
    Die "SSH 로 못 붙습니다: ${target}:$Port`n    · 보드 전원과 IP 를 확인하세요 (DHCP 주소는 바뀝니다)`n    · 키 인증이 필요하면 -SshKey <키경로> 를 주세요"
}
if (-not (Board-Ok 'command -v abusctl >/dev/null')) {
    Die '보드에 abusctl 이 없습니다. ATLAS 이미지가 맞는지 확인하세요.'
}
Ok "연결됨 ($(Invoke-Board 'uname -r'))"

# ---- 2. 업로드 -------------------------------------------------------------
Step '2/5 업로드'
if (-not (Board-Ok 'mkdir -p /tmp/download')) { Die '보드에 /tmp/download 를 만들지 못했습니다.' }
& scp @scpOpts $ipkItem.FullName "${target}:/tmp/download/"
if ($LASTEXITCODE -ne 0) { Die '전송 실패. 용량과 네트워크를 확인하세요.' }
$remote = "/tmp/download/$base"
$remoteSize = (Invoke-Board "wc -c < '$remote'").Trim()
if ("$remoteSize" -ne "$($ipkItem.Length)") {
    Die "전송이 깨졌습니다 (로컬 $($ipkItem.Length) / 보드 $remoteSize). 다시 실행하세요."
}
Ok '전송 완료 · 크기 일치'

# ---- 3. 기존 설치 제거 -----------------------------------------------------
Step '3/5 기존 설치 정리'
# 같은 ID·같은 버전이 이미 있으면 설치가 거부된다. 먼저 멈추고 지운다.
if (Board-Ok "abusctl call com.atlas.AppManager1 ListAllApps 2>/dev/null | grep -q '$AppId'") {
    Board-Ok "abusctl call com.atlas.AppManager1 Stop `"$AppId`"" | Out-Null
    Board-Ok "abusctl call com.atlas.PackageManager1 Remove `"$AppId`"" | Out-Null
    Ok '이전 버전 제거'
} else {
    Ok '처음 설치'
}

# ---- 4. 설치 ---------------------------------------------------------------
Step '4/5 설치'
if (-not (Board-Ok "abusctl call com.atlas.PackageManager1 Install `"$AppId`" `"$remote`"")) {
    Die "설치 실패. 보드에서 직접 확인하세요:`n    ssh $target 'abusctl call com.atlas.PackageManager1 Install `"$AppId`" `"$remote`"'"
}
if (-not (Board-Ok "abusctl call com.atlas.AppManager1 ListAllApps 2>/dev/null | grep -q '$AppId'")) {
    Die '설치는 끝났는데 앱 목록에 없습니다. 위 명령을 직접 실행해 출력을 보세요.'
}
Ok '설치됨'

# ---- 5. 실행 ---------------------------------------------------------------
if ($NoStart) {
    Step '5/5 실행 생략 (-NoStart)'
    Ok "끝났습니다. 실행: ssh $target 'abusctl call com.atlas.AppManager1 Start `"$AppId`"'"
    exit 0
}
Step '5/5 실행'
if (-not (Board-Ok "abusctl call com.atlas.AppManager1 Start `"$AppId`"")) { Die '실행 명령이 실패했습니다.' }
Start-Sleep -Seconds 2
if (Board-Ok "abusctl call com.atlas.AppManager1 ListRunningApps 2>/dev/null | grep -q '$AppId'") {
    Ok '실행 중입니다. 보드 화면을 보세요.'
} else {
    Write-Host '! 설치는 됐는데 실행 상태가 확인되지 않습니다. 로그를 보세요:' -ForegroundColor Yellow
    Write-Host "    ssh $target 'journalctl -u am.$AppId.service -n 50 --no-pager'"
    exit 1
}

Write-Host ''
Write-Host "멈추기 : ssh $target 'abusctl call com.atlas.AppManager1 Stop `"$AppId`"'"
Write-Host "지우기 : ssh $target 'abusctl call com.atlas.PackageManager1 Remove `"$AppId`"'"
