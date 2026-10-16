# Keeps train.py running for a fixed number of hours, resuming after crashes.
#
#   .\run_forever.ps1                       # run "l8_2weeks" for 336 h (two weeks)
#   .\run_forever.ps1 -Run l8_v2 -Hours 48
#
# Ctrl+C stops training cleanly (train.py saves first) and ends the loop.
# Turn off Windows sleep for the duration, or training pauses with the PC.
param(
    [string]$Run = "l8_2weeks",
    [double]$Hours = 336,
    [string]$Sensor = "vl53l8cx"
)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $here ".venv\Scripts\python.exe"
$train = Join-Path $here "train.py"
$runDir = Join-Path $here "runs\$Run"
New-Item -ItemType Directory -Force $runDir | Out-Null
$deadline = (Get-Date).AddHours($Hours)

while ((Get-Date) -lt $deadline) {
    if (Test-Path (Join-Path $runDir "STOP")) {
        Write-Host "runs\$Run\STOP 이 있어서 멈춤 (다시 돌리려면 이 파일을 지우고 실행)"
        break
    }
    $left = [math]::Round(($deadline - (Get-Date)).TotalHours, 3)
    & $py $train --run $Run --sensor $Sensor --resume --hours $left
    if ($LASTEXITCODE -eq 0) { break }   # finished, or stopped with Ctrl+C
    "$(Get-Date -Format s) train.py exited with $LASTEXITCODE, restarting in 30 s" |
        Add-Content (Join-Path $runDir "restarts.log")
    Start-Sleep -Seconds 30
}
