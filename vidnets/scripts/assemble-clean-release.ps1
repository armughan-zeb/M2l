$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
function Assert-WorkspacePath([string] $taskPath) {
    $absolute = [IO.Path]::GetFullPath($taskPath)
    if (-not $absolute.StartsWith($taskRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Release path is outside the workspace: $absolute"
    }
    return $absolute
}
$taskDist = Assert-WorkspacePath (Join-Path $taskRoot 'dist')
$taskStaging = Assert-WorkspacePath (Join-Path $taskRoot 'dist-build-cache\builder-output\win-unpacked')
$taskInstaller = Assert-WorkspacePath (Join-Path $taskRoot 'dist-build-cache\final-installer\Vidnets v1.exe')
$taskArchive = Assert-WorkspacePath (Join-Path $taskRoot ('artifacts\launch_20261022\previous-dist-' + (Get-Date -Format 'yyyyMMdd-HHmmss')))
$taskOutput = Assert-WorkspacePath (Join-Path $taskDist 'Vidnets')
$taskIntegrity = Get-Content -LiteralPath (Join-Path $taskDist 'distribution-integrity.json') -Raw | ConvertFrom-Json
$taskStamp = Get-Content -LiteralPath (Join-Path $taskRoot 'public\update-build.json') -Raw | ConvertFrom-Json
if ($taskIntegrity.buildId -ne $taskStamp.buildId) { throw 'Installer build does not match current source.' }
$taskHash = (Get-FileHash -LiteralPath $taskInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
if ($taskHash -ne ($taskIntegrity.files | Where-Object name -eq 'Vidnets v1.exe').sha256) {
    throw 'Final installer does not match verified distribution.'
}
foreach ($taskPath in @($taskDist, $taskStaging)) {
    if ((Get-Item -LiteralPath $taskPath).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Release directory must not be a junction: $taskPath"
    }
}
$taskRunning = Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith($taskDist + '\', [StringComparison]::OrdinalIgnoreCase)
}
if ($taskRunning) { throw 'Close the old dist app before assembling the clean folder.' }
if (Test-Path -LiteralPath $taskArchive) { throw 'Rollback archive already exists.' }
New-Item -ItemType Directory -Path (Split-Path $taskArchive -Parent) -Force | Out-Null
# Both fully resolved targets are within this workspace. Preserve the old
# distribution and any old runtime state rather than deleting user material.
Move-Item -LiteralPath $taskDist -Destination $taskArchive
New-Item -ItemType Directory -Path $taskOutput -Force | Out-Null
$taskApp = Assert-WorkspacePath (Join-Path $taskOutput 'App')
# Both absolute paths were checked above; a same-volume move avoids a second
# multi-gigabyte runtime copy. Future builds can seed a fresh staging copy.
Move-Item -LiteralPath $taskStaging -Destination $taskApp
Copy-Item -LiteralPath $taskInstaller -Destination (Join-Path $taskOutput 'Vidnets Setup.exe')
Copy-Item -LiteralPath (Join-Path $taskRoot 'docs\GENERATION_API.md') -Destination (Join-Path $taskOutput 'API.md')
@'
@echo off
start "" "%~dp0App\Vidnets Studio v1.exe"
'@ | Set-Content -LiteralPath (Join-Path $taskOutput 'Start Vidnets.cmd') -Encoding ascii
@'
VIDNETS STUDIO

Start Vidnets.cmd opens the bundled app. Keep the App folder beside this launcher.
Vidnets Setup.exe installs the same build on Windows 10+ x64.
Use either the portable app or the installation; avoid opening both together.

Sign in, configure your keys in Settings, and choose Efficient for Qwen 3.5 Flash.
API.md explains the single-call generator and safe retries.

Python, Node, FFmpeg and the Remotion browser are included. Internet and working
provider accounts are required for new generation. Account data and generated
projects are stored in the app's user data locations, not in this distribution.

The installer is unsigned. See checksums.json for build identity and hashes.
This package passed backend startup checks and a short bundled Remotion render.
Clean-machine installation and every niche/HD long-film scenario are not certified.
'@ | Set-Content -LiteralPath (Join-Path $taskOutput 'README.txt') -Encoding utf8
$taskFiles = @('Vidnets Setup.exe','Start Vidnets.cmd','API.md','README.txt','App\Vidnets Studio v1.exe','App\resources\app.asar')
$taskChecks = @($taskFiles | ForEach-Object {
    $taskFile = Join-Path $taskOutput $_
    [ordered]@{ path = $_; bytes = (Get-Item -LiteralPath $taskFile).Length; sha256 = (Get-FileHash -LiteralPath $taskFile -Algorithm SHA256).Hash.ToLowerInvariant() }
})
[ordered]@{ version = $taskStamp.version; buildId = $taskStamp.buildId; assembledAt = (Get-Date).ToUniversalTime().ToString('o'); files = $taskChecks } |
    ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $taskOutput 'checksums.json') -Encoding utf8
[ordered]@{ folder = $taskOutput; preservedOldDistribution = $taskArchive; buildId = $taskStamp.buildId } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskRoot 'artifacts\launch_20261022\clean-folder.json') -Encoding utf8
Write-Output "Clean release folder: $taskOutput"
