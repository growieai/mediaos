param([switch]$Stop)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$localDirectory = Join-Path $projectRoot '.local'
$processFile = Join-Path $localDirectory 'local-processes.json'
$pythonPath = Join-Path $projectRoot 'backend/.venv/Scripts/python.exe'
$nextPath = Join-Path $projectRoot 'apps/console/node_modules/next/dist/bin/next'
$records = @()
if (Test-Path -LiteralPath $processFile) {
    $records = Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json
}

function Get-OwnedProcess($entry) {
    $processId = [int]$entry.id
    $found = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
    $marker = if ($entry.name -eq 'api') { $pythonPath } else { $nextPath }
    if ($found -and $found.CommandLine -and $found.CommandLine.IndexOf($marker, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $found }
    return $null
}

if ($Stop) {
    foreach ($entry in $records) {
        if (Get-OwnedProcess $entry) {
            # Stop only the recorded process tree whose command still points to this checkout.
            & taskkill.exe /PID ([int]$entry.id) /T /F | Out-Null
        }
    }
    Write-Host 'Local Media OS API and console stopped. PostgreSQL was left running.'
    exit 0
}

foreach ($required in @($pythonPath, $nextPath, (Join-Path $projectRoot 'apps/console/.next/BUILD_ID'), (Join-Path $localDirectory 'credentials.json'))) {
    if (-not (Test-Path -LiteralPath $required)) { throw 'Run setup, migrate, seed and check first; see docs/TESTING.md.' }
}
$nodePath = (Get-Command node.exe).Source
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
$services = @(
    @{ name='api'; port=8000; executable=$pythonPath; directory=(Join-Path $projectRoot 'backend'); arguments=@('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000','--no-access-log') },
    @{ name='console'; port=3000; executable=$nodePath; directory=(Join-Path $projectRoot 'apps/console'); arguments=@(('"' + $nextPath + '"'),'start','--hostname','127.0.0.1','--port','3000') }
)
$started = @($records | Where-Object { Get-OwnedProcess $_ })
foreach ($service in $services) {
    $existing = @($records | Where-Object { $_.name -eq $service.name })
    if ($existing.Count -gt 0 -and (Get-OwnedProcess $existing[0])) {
        continue
    }
    $socket = New-Object System.Net.Sockets.TcpClient
    try {
        $connected = $socket.ConnectAsync('127.0.0.1', $service.port)
        if ($connected.Wait(500) -and $socket.Connected) { throw "Port $($service.port) is occupied by an unmanaged service." }
    } catch [System.AggregateException] {
        # Connection refused means the loopback port is available.
    } finally { $socket.Dispose() }
    $child = Start-Process -FilePath $service.executable -ArgumentList $service.arguments -WorkingDirectory $service.directory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $localDirectory ($service.name + '.stdout.log')) -RedirectStandardError (Join-Path $localDirectory ($service.name + '.stderr.log'))
    $started += @{ name=$service.name; id=$child.Id }
    ConvertTo-Json -InputObject @($started) | Set-Content -LiteralPath $processFile -Encoding utf8
}

$credentials = Get-Content -LiteralPath (Join-Path $localDirectory 'credentials.json') -Raw | ConvertFrom-Json
$authHeaders = @{ Authorization=('Bearer ' + $credentials.tokens.OPERATOR); 'X-Tenant-ID'=$credentials.tenant_id }
$ready = $false
for ($attempt=0; $attempt -lt 30; $attempt++) {
    try {
        $api = Invoke-RestMethod 'http://127.0.0.1:8000/v1/readiness' -Headers $authHeaders -TimeoutSec 2
        $console = Invoke-WebRequest 'http://127.0.0.1:3000' -UseBasicParsing -TimeoutSec 2
        if ($api.status -eq 'ready' -and $console.StatusCode -eq 200) { $ready=$true; break }
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $ready) { throw 'Services did not become ready. Check .local/*.stderr.log and the standalone PostgreSQL service.' }
Write-Host 'Media OS is ready at http://127.0.0.1:3000'
Write-Host 'Use .local/credentials.json for the tenant and OPERATOR/APPROVER credentials.'
Write-Host 'Stop with: powershell -File scripts/start-local.ps1 -Stop'
