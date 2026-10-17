@echo off
rem Usage (from cmd or PowerShell, in this folder):  deploy-pi4.bat 172.16.34.146
rem Extra options are passed through, e.g.:          deploy-pi4.bat 172.16.34.146 -NoStart
if "%~1"=="" (
  echo usage: deploy-pi4.bat BOARD_IP [-NoStart]
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy-pi4.ps1" -Ip %*
