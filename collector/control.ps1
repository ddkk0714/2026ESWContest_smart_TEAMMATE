param([ValidateSet('Status','Set')][string]$Action='Status', [ValidateSet('true','false')][string]$Enabled='false')
$ErrorActionPreference='Stop'
$dir=Join-Path $env:LOCALAPPDATA 'DESKMATE\capture-control'

function Read-State {
    try {
        $s=Get-Content -LiteralPath (Join-Path $dir 'state.json') -Raw | ConvertFrom-Json
        $age=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0 - [double]$s.updated_at
        $p=Get-Process -Id ([int]$s.pid) -ErrorAction Stop
        if($age -lt 0 -or $age -gt 5 -or !$s.running -or $p.ProcessName -notmatch '^python(w)?$') {throw 'unavailable'}
        return [PSCustomObject]@{connected=$true;keyboard_enabled=$(if($s.keyboard_enabled -is [bool]){[bool]$s.keyboard_enabled}else{$null});mouse_enabled=[bool]$s.mouse_enabled;request_id=$s.request_id;mqtt_publish_enabled=[bool]$s.mqtt_publish_enabled}
    } catch {
        return [PSCustomObject]@{connected=$false;keyboard_enabled=$null;mouse_enabled=$null;request_id=$null;mqtt_publish_enabled=$null}
    }
}

if($Action -eq 'Status') {Read-State | ConvertTo-Json -Compress; exit 0}
$state=Read-State
if(!$state.connected) {throw 'collector_not_connected'}
$id=[Guid]::NewGuid().ToString('N')
$desired=$Enabled -eq 'true'
$temporary=Join-Path $dir ('request-'+$id+'.new')
try {
    @{enabled=$desired;request_id=$id} | ConvertTo-Json -Compress | Set-Content -LiteralPath $temporary -Encoding Ascii
    $destination=Join-Path $dir 'request.json'
    if(Test-Path -LiteralPath $destination) {[IO.File]::Replace($temporary,$destination,[NullString]::Value)}
    else {[IO.File]::Move($temporary,$destination)}
    $deadline=[DateTime]::UtcNow.AddSeconds(6)
    do {
        Start-Sleep -Milliseconds 150
        $state=Read-State
        if($state.connected -and $state.request_id -eq $id -and $state.keyboard_enabled -eq $desired) {
            $state | ConvertTo-Json -Compress; exit 0
        }
    } while([DateTime]::UtcNow -lt $deadline)
    throw 'capture_switch_not_acknowledged'
} finally {if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary}}
