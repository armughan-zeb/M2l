[CmdletBinding()]
param([string]$DestinationName = 'Vidnets Clean Working Folder')
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($DestinationName) -or $DestinationName -match '[\\/:*?"<>|]' -or $DestinationName -in @('.', '..')) { throw 'DestinationName must be a single folder name' }
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$Destination = Join-Path (Join-Path $Root 'dist') $DestinationName
$LogDirectory = Join-Path $Root 'artifacts\source-working-folder-release'
$DistRoot = [IO.Path]::GetFullPath((Join-Path $Root 'dist'))
$ResolvedDestination = [IO.Path]::GetFullPath($Destination)
if (-not $ResolvedDestination.StartsWith($DistRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Destination must stay inside dist' }
foreach ($Boundary in @($DistRoot, $Destination)) {
    if ((Test-Path -LiteralPath $Boundary) -and ((Get-Item -LiteralPath $Boundary).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Refusing a redirected distribution path: $Boundary" }
}
New-Item -ItemType Directory -Force -Path $Destination, $LogDirectory | Out-Null
# Copies only original source and required assets. Never mirrors/deletes the source or destination.
function Copy-SourceTree([string]$Relative, [string[]]$ExtraExclude = @()) {
    $Source = Join-Path $Root $Relative
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) { throw "Missing source directory: $Relative" }
    $Target = Join-Path $Destination $Relative
    $Exclude = @('__pycache__', '.pytest_cache', '.mypy_cache', '.cache', '.temp', '.git', 'cache', 'temp', 'tmp', 'logs') + $ExtraExclude
    $CopyArgs = @($Source, $Target, '/E', '/XJ', '/R:1', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/XD') + $Exclude + @('/XF', '*.pyc', '*.pyo', '*.log', '*.tmp', '.env', '.env.*', '*cookies*.txt', 'cookies.json', 'auth-session*', '*.dpapi', 'secret-settings*', 'agent-key*', '.vidzeo_session_secret', 'queue.json*', 'harness_*.txt')
    & robocopy @CopyArgs | Add-Content -LiteralPath (Join-Path $LogDirectory 'copy.log')
    if ($LASTEXITCODE -ge 8) { throw "Failed to copy $Relative ($LASTEXITCODE)" }
    Write-Host "Copied $Relative"
}
$Files = @('VIDNETS_AGENT.cmd','AGENT_ACCESS.md','main.js','main.py','preload.js','package.json','package-lock.json','START.bat','INSTALL.bat','BUILD.bat','CREATE_PACKAGE.bat','CREATE_PURE_PACKAGE.bat','create_product_folder.py','create_new_package.py','gallery-dl.conf','.env.defaults','.env.public','.env.example','.gitignore','.gitattributes','.editorconfig','.dockerignore','deno.lock','Dockerfile.backend','Dockerfile.backend.docker','Dockerfile.package','docker-compose.backend.yml','docker-compose.package.yml','AGENTS.md','README.md','ARCHITECTURE.md','EDITING_RULES.md','SECURITY.md','SUPABASE_SETUP.md','SYSTEM_ARCHITECTURE_AND_EDITING_PLAN.md','conftest.py','pytest.ini','Vidnets Studio.vbs','Vidnets Studio.spec')
foreach ($Relative in $Files) {
    $Source = Join-Path $Root $Relative
    if (Test-Path -LiteralPath $Source -PathType Leaf) { Copy-Item -LiteralPath $Source -Destination (Join-Path $Destination $Relative) -Force }
}
Copy-SourceTree 'server' @('characters')
foreach ($Relative in @('src','remotion','fonts','chrome-connector','docs','scripts','supabase','tests','build','.github')) { Copy-SourceTree $Relative }
Copy-SourceTree 'admin-panel' @('node_modules','dist','build')
Copy-SourceTree 'public' @('remotion-manifest-media','remotion-renders','remotion-video-cache','template-animations')
Copy-SourceTree 'resources\template-library\builtin'
foreach ($Relative in @('tools\python','tools\node','tools\ffmpeg','tools\models','tools\youtube-pot-provider')) { Copy-SourceTree $Relative }
Copy-SourceTree 'node_modules'
Copy-Item -LiteralPath (Join-Path $Root 'tools\.runtime-ready') -Destination (Join-Path $Destination 'tools\.runtime-ready') -Force
@'
@echo off
cd /d "%~dp0"
call START.bat
'@ | Set-Content -LiteralPath (Join-Path $Destination 'Start Vidnets.cmd') -Encoding ASCII
@'
VIDNETS — ORIGINAL, EDITABLE WORKING APP

Double-click Start Vidnets.cmd to run the original app through bundled Electron.
No npm install is needed. Python, Node, FFmpeg, Electron, Remotion and its browser
are included. Lifetime Pro purchase is required for public generation access.
Private trials are granted only by the administrator. Sign in with your purchase
account, then configure your provider API keys in Settings.
Manual generation uses Qwen 3.5 Flash through OpenRouter. For an external agent,
read AGENT_ACCESS.md and pair once in Settings, then use VIDNETS_AGENT.cmd.
The agent service can run with the desktop closed; credentials are never bundled.

The original main.js/preload.js, server Python, src UI, Remotion components,
scripts, docs, tests and built-in template library are editable here.
This is independent of the installed/portable app in the adjacent Vidnets folder.
Private keys, auth sessions, cookies, past projects, logs and generated media
are excluded. Original repository history remains in the development workspace.

After changing Remotion player sources, use bundled Node to run:
  tools\node\node.exe scripts\build-remotion-player.js
Then start the app again. See docs\GENERATION_API.md for the single-call API.
For rebuilding the installer use BUILD.bat (requires the build prerequisites
described by that script). Installing/building is not necessary to run this copy.

source-files.json contains hashes of the source files at delivery time.
'@ | Set-Content -LiteralPath (Join-Path $Destination 'READ ME FIRST.txt') -Encoding UTF8
$Critical = @('VIDNETS_AGENT.cmd','AGENT_ACCESS.md','server\headless_agent.py','server\services\agent_bridge.py','server\services\agent_access.py','src\remotion\engine\RecoveryContent.js','src\remotion\components\MissingSceneMotionGraphic.jsx','main.js','preload.js','server\app.py','server\config.py','server\services\generation_cost_policy.py','server\services\research_search_worker.py','src\renderer.js','src\remotion\DocumentaryComposition.jsx','public\update-build.json','node_modules\electron\dist\electron.exe','node_modules\.remotion\chrome-headless-shell\win64\chrome-headless-shell-win64\chrome-headless-shell.exe','tools\python\python.exe','tools\node\node.exe','tools\ffmpeg\ffmpeg.exe')
$Critical += @('.env.defaults', 'server\services\youtube_pipeline_v2.py', 'server\services\pipeline_v2_media_adapter.py', 'server\services\visual_intelligence\semantic_guard.py', 'server\services\visual_requirement.py', 'server\services\native_render_policy.py', 'server\services\editorial_director\pixel_revision.py', 'server\services\editorial_director\release_review.py', 'server\services\editorial_director\review_context.py', 'server\services\semantic_edit_engine.py')
foreach ($Relative in $Critical) {
    $SourceHash = (Get-FileHash -LiteralPath (Join-Path $Root $Relative) -Algorithm SHA256).Hash
    $TargetHash = (Get-FileHash -LiteralPath (Join-Path $Destination $Relative) -Algorithm SHA256).Hash
    if ($SourceHash -ne $TargetHash) { throw "Source copy mismatch: $Relative" }
}
$SourceRecords = foreach ($Directory in @('server','src','remotion','scripts','docs','resources\template-library\builtin')) {
    Get-ChildItem -LiteralPath (Join-Path $Destination $Directory) -Recurse -File | ForEach-Object {
        @{ path = $_.FullName.Substring($Destination.Length + 1); sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    }
}
@{ createdAt = [DateTime]::UtcNow.ToString('o'); source = $Root; files = @($SourceRecords); criticalFilesVerified = $Critical } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Destination 'source-files.json') -Encoding UTF8
Write-Host "Original working app ready: $Destination"
