[CmdletBinding()]
param(
    [switch]$Connect,
    [switch]$NoUpdate,
    [string[]]$KnownSubnets = @('172.16.34.0/24')
)

$ErrorActionPreference = 'Stop'
$sshConfigPath = Join-Path $env:USERPROFILE '.ssh\config'
$candidateAddresses = [System.Collections.Generic.HashSet[string]]::new()

function Add-SubnetCandidates {
    param([Parameter(Mandatory)][string]$Subnet)

    if ($Subnet -notmatch '^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.0/24$') {
        throw "Only IPv4 /24 subnets are supported: $Subnet"
    }
    $prefix = "$($Matches[1]).$($Matches[2]).$($Matches[3])"
    1..254 | ForEach-Object { [void]$candidateAddresses.Add("$prefix.$_") }
}

foreach ($subnet in $KnownSubnets) {
    Add-SubnetCandidates -Subnet $subnet
}

Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
        $adapter = Get-NetAdapter -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue
        $_.AddressState -eq 'Preferred' -and
        $_.PrefixLength -le 24 -and
        $adapter.HardwareInterface -and
        $adapter.Status -eq 'Up' -and
        $_.IPAddress -match '^(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.)'
    } |
    ForEach-Object {
        $octets = $_.IPAddress.Split('.')
        Add-SubnetCandidates -Subnet "$($octets[0]).$($octets[1]).$($octets[2]).0/24"
    }

if (Test-Path -LiteralPath $sshConfigPath) {
    $sshConfig = Get-Content -Raw -LiteralPath $sshConfigPath
    $hostBlock = [regex]::Match(
        $sshConfig,
        '(?ms)^Host\s+[^\r\n]*(?:\brpi4\b|\bdeskmate-pi4\b)[^\r\n]*\r?\n(?<body>(?:(?!^Host\s).)*)'
    )
    if ($hostBlock.Success -and $hostBlock.Groups['body'].Value -match '(?m)^\s*HostName\s+(\d{1,3}(?:\.\d{1,3}){3})\s*$') {
        [void]$candidateAddresses.Add($Matches[1])
    }
}

Write-Host "DESKMATE Pi 4 검색 중 ($($candidateAddresses.Count)개 주소)..."
$probes = foreach ($address in $candidateAddresses) {
    $client = [System.Net.Sockets.TcpClient]::new()
    [pscustomobject]@{
        Address = $address
        Client = $client
        Task = $client.ConnectAsync($address, 22)
    }
}

Start-Sleep -Milliseconds 1800
$sshAddresses = @(
    $probes | Where-Object { $_.Client.Connected } | ForEach-Object { $_.Address }
)
$probes | ForEach-Object { $_.Client.Dispose() }

$piAddress = $null
foreach ($address in $sshAddresses) {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $model = & ssh.exe -F NUL -o BatchMode=yes -o ConnectTimeout=3 `
            -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL `
            "root@$address" "tr -d '\000' </proc/device-tree/model" 2>$null
        $sshExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($sshExitCode -eq 0 -and $model -match '^Raspberry Pi 4 Model B') {
        $piAddress = $address
        break
    }
}

if (-not $piAddress) {
    throw 'SSH가 활성화된 DESKMATE Raspberry Pi 4를 찾지 못했습니다.'
}

Write-Host "DESKMATE Pi 4 발견: $piAddress" -ForegroundColor Green

if (-not $NoUpdate) {
    $sshDirectory = Split-Path -Parent $sshConfigPath
    if (-not (Test-Path -LiteralPath $sshDirectory)) {
        New-Item -ItemType Directory -Path $sshDirectory | Out-Null
    }
    $sshConfig = if (Test-Path -LiteralPath $sshConfigPath) {
        Get-Content -Raw -LiteralPath $sshConfigPath
    } else {
        ''
    }
    $hostPattern = '(?ms)(^Host\s+[^\r\n]*(?:\brpi4\b|\bdeskmate-pi4\b)[^\r\n]*\r?\n(?:(?!^Host\s).)*?^\s*HostName\s+)\S+'
    if ([regex]::IsMatch($sshConfig, $hostPattern)) {
        $sshConfig = [regex]::Replace(
            $sshConfig,
            $hostPattern,
            { param($match) $match.Groups[1].Value + $piAddress },
            1
        )
    } else {
        $sshConfig = $sshConfig.TrimEnd() + "`r`n`r`nHost atlas rpi4 deskmate-pi4`r`n    HostName $piAddress`r`n    User root`r`n    Port 22`r`n    StrictHostKeyChecking accept-new`r`n    ServerAliveInterval 30`r`n    ServerAliveCountMax 3`r`n"
    }
    [System.IO.File]::WriteAllText(
        $sshConfigPath,
        $sshConfig,
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Host 'SSH 별칭 갱신 완료: ssh rpi4 / ssh deskmate-pi4'
}

if ($Connect) {
    & ssh.exe rpi4
}
