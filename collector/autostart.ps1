# DESKMATE 키스트로크 수집기를 Windows 로그인 때 자동으로 켠다(창 없이, Pi 4 브로커는 --broker auto 로 찾음).
#
#   powershell -ExecutionPolicy Bypass -File collector\autostart.ps1 -Python <venv>\Scripts\pythonw.exe          # 등록 + 지금 켜기
#   powershell -ExecutionPolicy Bypass -File collector\autostart.ps1 -Uninstall                                  # 등록 해제 + 끄기
#
# 작업 이름: "DESKMATE keystroke collector"(작업 스케줄러에서 보이고 지울 수 있다). 현재 사용자 권한으로만 돈다.
# 키 값은 수집하지 않는다(타이밍·종류만, collector/README.md). 로컬 기록은 <저장소>\logs\keystroke.jsonl.
param(
    [string]$Python = "",
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$TaskName = "DESKMATE keystroke collector"
$Repo = Split-Path -Parent $PSScriptRoot

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "해제됨: $TaskName"
    } else {
        Write-Output "등록된 작업 없음: $TaskName"
    }
    exit 0
}

if (-not $Python) { $Python = Join-Path $Repo ".venv\Scripts\pythonw.exe" }
if (-not (Test-Path $Python)) {
    throw "pythonw.exe 를 찾지 못함: $Python — -Python 으로 pynput·paho-mqtt 가 설치된 venv 의 pythonw.exe 를 주세요"
}

$action = New-ScheduledTaskAction -Execute $Python -Argument "-m collector --broker auto" -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "DESKMATE 키스트로크 타이밍 수집기(키 값 미수집) — Pi 4 hub 브로커 자동 찾기" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output "등록·시작됨: $TaskName ($Python, 작업 폴더 $Repo)"
