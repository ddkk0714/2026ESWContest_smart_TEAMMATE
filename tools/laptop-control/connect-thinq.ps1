$ErrorActionPreference = 'Stop'
$dockerPath = (Get-Command docker -ErrorAction Stop).Source
& $dockerPath build -t deskmate-thinq-host:local -f (Join-Path $PSScriptRoot 'Dockerfile.thinq') $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'ThinQ 실행 환경 준비 실패' }
# PAT is entered directly through Docker's terminal, never via argv/env/files/browser.
& $dockerPath run --rm -it --name deskmate-thinq-host -p '127.0.0.1:8792:8792' deskmate-thinq-host:local
