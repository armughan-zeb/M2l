$ErrorActionPreference = 'Stop'

$rootPath = (Get-Location).Path
$pythonPath = Join-Path $rootPath 'tools\python\python.exe'
$backend = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq 'python.exe' -and $_.ExecutablePath -eq $pythonPath -and $_.CommandLine -like '*main.py*' } |
    Select-Object -First 1
$queue = $null
$backendHealthy = $false
try {
    $queue = Invoke-RestMethod 'http://127.0.0.1:5000/api/queue/status?compact=1' -TimeoutSec 10
    $backendHealthy = $true
} catch {
    $backendHealthy = $false
}

if ($backendHealthy) {
    if (@($queue.active_jobs).Count -gt 0 -or @($queue.items | Where-Object status -eq 'queued').Count -gt 0) {
        throw 'Refusing restart while a render is active or queued.'
    }
    if (-not $backend) {
        throw 'The queue responds, but its owning Vidzeo Python process could not be identified; refusing to risk another service on port 5000.'
    }
} else {
    $listener = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        throw 'Port 5000 is occupied but the Vidzeo queue is unavailable; refusing to stop an unidentified process.'
    }
}

$logPrefix = $env:VIDZEO_BACKEND_LOG_PREFIX
if ($logPrefix) {
    $logPrefix = [System.IO.Path]::GetFullPath($logPrefix)
    $logDir = Split-Path -Parent $logPrefix
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $stdoutPath = "$logPrefix-stdout.log"
    $stderrPath = "$logPrefix-stderr.log"
    $runtimePath = "$logPrefix-runtime.log"
    $recordPath = "$logPrefix-restart_record.json"
} else {
    $bootDir = Join-Path $rootPath 'artifacts\nfl_test_001_20261002\attempts\attempt_008_boot\logs'
    New-Item -ItemType Directory -Force -Path $bootDir | Out-Null
    $stdoutPath = Join-Path $bootDir 'server-process-stdout.log'
    $stderrPath = Join-Path $bootDir 'server-process-stderr.log'
    $runtimePath = Join-Path $bootDir 'server-runtime.log'
    $recordPath = Join-Path (Split-Path $bootDir -Parent) 'restart_record.json'
}

if ($backendHealthy) {
    Stop-Process -Id $backend.ProcessId -Force
    $releaseDeadline = (Get-Date).AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 300
        $listener = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
    } while ($listener -and (Get-Date) -lt $releaseDeadline)
    if ($listener) {
        throw 'Port 5000 did not become free after stopping the identified idle backend.'
    }
}

$env:VIDZEO_RUNTIME_LOG_PATH = $runtimePath
$directorModel = if ($env:VIDZEO_EDITORIAL_DIRECTOR_MODEL) { $env:VIDZEO_EDITORIAL_DIRECTOR_MODEL } else { 'openai/gpt-5.6-luna' }
$env:VIDZEO_EDITORIAL_DIRECTOR_MODEL = $directorModel
$started = Start-Process -FilePath $pythonPath -ArgumentList 'main.py' -WorkingDirectory $rootPath `
    -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru

$healthy = $false
$healthDeadline = (Get-Date).AddSeconds(120)
do {
    Start-Sleep -Seconds 2
    try {
        $check = Invoke-RestMethod 'http://127.0.0.1:5000/api/queue/status?compact=1' -TimeoutSec 6
        $healthy = $true
    }
    catch {
        $healthy = $false
    }
} while (-not $healthy -and (Get-Date) -lt $healthDeadline)

if (-not $healthy) {
    $tail = if (Test-Path $stderrPath) { Get-Content $stderrPath -Tail 30 } else { @() }
    throw "Backend did not become healthy within 120 seconds. STDERR: $($tail -join ' ')"
}

$previousPid = if ($backend) { $backend.ProcessId } else { $null }
$restartRecord = [pscustomobject]@{
    recorded_at   = (Get-Date).ToUniversalTime().ToString('o')
    previous_pid  = $previousPid
    new_pid       = $started.Id
    port          = 5000
    worker_running = $check.worker_running
    worker_count  = $check.worker_count
    active_jobs   = @($check.active_jobs).Count
    runtime_log   = $runtimePath
    stdout_log    = $stdoutPath
    stderr_log    = $stderrPath
    director_model = $directorModel
}
$restartRecord | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 $recordPath
$restartRecord | ConvertTo-Json -Compress
