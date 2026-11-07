param(
    [string]$InstallRoot = "",
    [string]$ProviderVersion = "1.3.1",
    [string]$NodeVersion = "24.15.0",
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    $InstallRoot = Split-Path -Parent $PSScriptRoot
}
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$ToolsDir = Join-Path $InstallRoot "tools"
$ProviderDir = Join-Path $ToolsDir "youtube-pot-provider"
$ProviderEntry = Join-Path $ProviderDir "server\build\main.js"
$ProviderModules = Join-Path $ProviderDir "server\node_modules"
$ProviderStamp = Join-Path $ProviderDir ".vidzeo-provider-ready"
$NodeDir = Join-Path $ToolsDir "node"
$NodeExe = Join-Path $NodeDir "node.exe"
$ExpectedStamp = "bgutil-$ProviderVersion node-$NodeVersion loopback-only"

function Write-Step([string]$Message) {
    Write-Host "[Vidzeo YouTube] $Message"
}

function Test-Ready {
    if (-not (Test-Path -LiteralPath $ProviderEntry)) { return $false }
    if (-not (Test-Path -LiteralPath $ProviderModules)) { return $false }
    if (-not (Test-Path -LiteralPath $NodeExe)) { return $false }
    if (-not (Test-Path -LiteralPath $ProviderStamp)) { return $false }
    return (Get-Content -Raw -LiteralPath $ProviderStamp).Contains($ExpectedStamp)
}

if (Test-Ready) {
    Write-Step "Token provider runtime is ready."
    exit 0
}
if ($ValidateOnly) {
    throw "YouTube token provider runtime is missing or stale. Run this script without -ValidateOnly before packaging."
}

$TempRoot = Join-Path $env:TEMP ("vidzeo-youtube-provider-" + [System.Guid]::NewGuid().ToString("N"))
$ProviderZip = Join-Path $TempRoot "provider.zip"
$NodeZip = Join-Path $TempRoot "node.zip"
$ExtractedProvider = Join-Path $TempRoot "provider"
$ExtractedNode = Join-Path $TempRoot "node"

try {
    New-Item -ItemType Directory -Force -Path $TempRoot, $ExtractedProvider, $ExtractedNode | Out-Null

    Write-Step "Downloading token provider $ProviderVersion..."
    Invoke-WebRequest -UseBasicParsing `
        -Uri "https://github.com/Brainicism/bgutil-ytdlp-pot-provider/archive/refs/tags/$ProviderVersion.zip" `
        -OutFile $ProviderZip
    Expand-Archive -LiteralPath $ProviderZip -DestinationPath $ExtractedProvider -Force
    $ProviderSource = Get-ChildItem -LiteralPath $ExtractedProvider -Directory | Select-Object -First 1
    if (-not $ProviderSource) { throw "Downloaded provider archive is empty." }

    $ServerDir = Join-Path $ProviderSource.FullName "server"
    $MainSource = Join-Path $ServerDir "src\main.ts"
    $SourceText = Get-Content -Raw -LiteralPath $MainSource
    $SourceText = $SourceText.Replace('host: "::"', 'host: "127.0.0.1"')
    $SourceText = $SourceText.Replace('host: "0.0.0.0"', 'host: "127.0.0.1"')
    $SourceText = $SourceText.Replace('on address [::]', 'on address 127.0.0.1')
    $SourceText = $SourceText.Replace('on address 0.0.0.0', 'on address 127.0.0.1')
    Set-Content -LiteralPath $MainSource -Value $SourceText -Encoding UTF8

    $Npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
    if (-not $Npm) { throw "Node.js/npm 20+ is required on the build machine." }
    Write-Step "Building loopback-only token provider..."
    Push-Location $ServerDir
    try {
        & $Npm ci --no-fund --no-audit
        if ($LASTEXITCODE -ne 0) { throw "Provider npm install failed." }
        & $Npm exec -- tsc
        if ($LASTEXITCODE -ne 0) { throw "Provider TypeScript build failed." }
        & $Npm prune --omit=dev --no-fund --no-audit
        if ($LASTEXITCODE -ne 0) { throw "Provider dependency pruning failed." }
        & $Npm audit fix --omit=dev --no-fund
        if ($LASTEXITCODE -ne 0) { throw "Provider production dependency audit failed." }
        & $Npm audit --omit=dev --audit-level=high
        if ($LASTEXITCODE -ne 0) { throw "Provider still has high-severity production dependencies." }
    } finally {
        Pop-Location
    }

    Write-Step "Downloading private Node runtime $NodeVersion..."
    Invoke-WebRequest -UseBasicParsing `
        -Uri "https://nodejs.org/dist/v$NodeVersion/node-v$NodeVersion-win-x64.zip" `
        -OutFile $NodeZip
    Expand-Archive -LiteralPath $NodeZip -DestinationPath $ExtractedNode -Force
    $NodeSource = Get-ChildItem -LiteralPath $ExtractedNode -Directory | Select-Object -First 1
    if (-not $NodeSource -or -not (Test-Path -LiteralPath (Join-Path $NodeSource.FullName "node.exe"))) {
        throw "Downloaded Node runtime is incomplete."
    }

    if (Test-Path -LiteralPath $ProviderDir) {
        $resolvedProvider = [System.IO.Path]::GetFullPath($ProviderDir)
        if (-not $resolvedProvider.StartsWith($ToolsDir, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to replace provider outside the tools directory."
        }
        Remove-Item -LiteralPath $resolvedProvider -Recurse -Force
    }
    if (Test-Path -LiteralPath $NodeDir) {
        $resolvedNode = [System.IO.Path]::GetFullPath($NodeDir)
        if (-not $resolvedNode.StartsWith($ToolsDir, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to replace Node outside the tools directory."
        }
        Remove-Item -LiteralPath $resolvedNode -Recurse -Force
    }

    New-Item -ItemType Directory -Force -Path $ProviderDir, $NodeDir | Out-Null
    Copy-Item -Path (Join-Path $ProviderSource.FullName "*") -Destination $ProviderDir -Recurse -Force
    Copy-Item -Path (Join-Path $NodeSource.FullName "node.exe") -Destination $NodeExe -Force
    Copy-Item -Path (Join-Path $NodeSource.FullName "LICENSE") -Destination (Join-Path $NodeDir "LICENSE") -Force
    Set-Content -LiteralPath $ProviderStamp -Value $ExpectedStamp -Encoding UTF8
    Write-Step "Token provider and Node runtime are ready."
} finally {
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
