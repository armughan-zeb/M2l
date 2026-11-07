param(
    [string]$InstallRoot = "",
    [string]$SourceRoot = "",
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    $InstallRoot = Split-Path -Parent $PSScriptRoot
}
if ([string]::IsNullOrWhiteSpace($SourceRoot)) {
    $SourceRoot = Split-Path -Parent $PSScriptRoot
}

function Resolve-CleanPath([string]$Value, [string]$Fallback) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        $Value = $Fallback
    }

    $clean = $Value.Trim()
    $clean = $clean.Replace('"', '').Replace("'", '')
    $clean = [regex]::Split($clean, '\s+-[A-Za-z][A-Za-z0-9_-]*\s+')[0]
    $clean = $clean.Trim()

    while ($clean.EndsWith('"') -or $clean.EndsWith("'")) {
        $clean = $clean.Substring(0, $clean.Length - 1).Trim()
    }

    if ([string]::IsNullOrWhiteSpace($clean)) {
        $clean = $Fallback
    }

    return [System.IO.Path]::GetFullPath($clean)
}

$InstallRoot = Resolve-CleanPath $InstallRoot (Split-Path -Parent $PSScriptRoot)
$SourceRoot = Resolve-CleanPath $SourceRoot (Split-Path -Parent $PSScriptRoot)
$ToolsDir = Join-Path $InstallRoot "tools"
$PythonDir = Join-Path $ToolsDir "python"
$FfmpegDir = Join-Path $ToolsDir "ffmpeg"
$SourceToolsDir = Join-Path $SourceRoot "tools"
$SourcePythonDir = Join-Path $SourceToolsDir "python"
$SourceFfmpegDir = Join-Path $SourceToolsDir "ffmpeg"
$RuntimeStamp = Join-Path $ToolsDir ".runtime-ready"
$RuntimeStampVersion = "deps-v3-faster-whisper"
$requirementsCandidates = @(
    (Join-Path $SourceRoot "server\requirements.txt"),
    (Join-Path $SourceRoot "resources\server\requirements.txt"),
    (Join-Path (Split-Path -Parent $PSScriptRoot) "server\requirements.txt"),
    (Join-Path (Split-Path -Parent $PSScriptRoot) "resources\server\requirements.txt")
)
$Requirements = ""
foreach ($cand in $requirementsCandidates) {
    if (Test-Path $cand) {
        $Requirements = $cand
        break
    }
}
if (-not $Requirements) {
    $Requirements = Join-Path $SourceRoot "server\requirements.txt"
}

function Write-Step($Message) {
    Write-Host "[Vidnets Runtime] $Message"
}

function Invoke-Checked([string]$Exe, [string[]]$Arguments, [string]$Label) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Test-CompatiblePython([string]$Exe, [string[]]$Arguments) {
    try {
        & $Exe @Arguments -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Get-PythonVersionLabel([string]$Exe, [string[]]$Arguments) {
    try {
        return (& $Exe @Arguments -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>$null).Trim()
    } catch {
        return "unknown"
    }
}

function Find-SystemPython {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3.11"); Label = "Python 3.11 via py launcher" },
        @{ Exe = "py"; Args = @("-3.12"); Label = "Python 3.12 via py launcher" },
        @{ Exe = "python"; Args = @(); Label = "python on PATH" }
    )

    foreach ($candidate in $candidates) {
        $cmd = Get-Command $candidate.Exe -ErrorAction SilentlyContinue
        if ($cmd -and (Test-CompatiblePython $candidate.Exe $candidate.Args)) {
            return [pscustomobject]@{
                Exe = $candidate.Exe
                Args = [string[]]$candidate.Args
                Label = $candidate.Label
                Version = Get-PythonVersionLabel $candidate.Exe $candidate.Args
            }
        }
    }
    return $null
}

function Ensure-SystemPython {
    $systemPython = Find-SystemPython
    if ($systemPython) {
        return $systemPython
    }

    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python was not found. Install Python 3.10+ from python.org, then run INSTALL_RUNTIME.bat again."
    }

    Write-Step "Python not found. Installing Python 3.11 from the web..."
    Invoke-Checked "winget" @("install", "--id", "Python.Python.3.11", "--exact", "--silent", "--accept-package-agreements", "--accept-source-agreements") "Python 3.11 install"
    $systemPython = Find-SystemPython
    if (-not $systemPython) {
        throw "Python installation finished, but Python is still not available on PATH. Restart Windows or install Python manually, then run INSTALL_RUNTIME.bat again."
    }
    return $systemPython
}

function Get-VenvPython {
    $candidates = @(
        (Join-Path $PythonDir "Scripts\python.exe"),
        (Join-Path $PythonDir "python.exe")
    )
    foreach ($candidate in $candidates) {
        if ((Test-Path $candidate) -and (Test-CompatiblePython $candidate @())) {
            return $candidate
        }
    }
    return ""
}

function Download-FileSafe([string]$Url, [string]$Destination, [string]$Label) {
    Write-Step "$Label (downloading from internet)..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $wc = New-Object System.Net.WebClient
    try {
        $wc.Headers.Add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Vidnets-Studio/1.0")
        $wc.DownloadFile($Url, $Destination)
        if (-not (Test-Path $Destination) -or (Get-Item $Destination).Length -lt 1024) {
            throw "Downloaded file is incomplete or missing."
        }
    } finally {
        $wc.Dispose()
    }
}

function Ensure-PythonRuntime {
    # Prefer the packaged portable runtime whenever it is present.  A prior
    # install can contain a venv at Scripts\python.exe with a readiness stamp
    # but no dependencies; selecting it first causes the backend to crash.
    $sourcePython = Join-Path $SourcePythonDir "python.exe"
    if (Test-Path $sourcePython) {
        $sourceFull = [System.IO.Path]::GetFullPath($SourcePythonDir).TrimEnd('\\')
        $targetFull = [System.IO.Path]::GetFullPath($PythonDir).TrimEnd('\\')
        if ($sourceFull -ne $targetFull) {
            Write-Step "Copying bundled Python runtime from source to install directory..."
            $parentDir = Split-Path -Parent $PythonDir
            New-Item -ItemType Directory -Force -Path $parentDir | Out-Null
            Remove-Item -LiteralPath $PythonDir -Recurse -Force -ErrorAction SilentlyContinue
            Copy-Item -Path $SourcePythonDir -Destination $parentDir -Recurse -Force
        }
        foreach ($candidate in @((Join-Path $PythonDir "python.exe"), (Join-Path $PythonDir "Scripts\python.exe"))) {
            if ((Test-Path $candidate) -and (Test-RequiredPythonImports $candidate)) {
                Write-Step "Bundled Python runtime ready at $InstallRoot\tools\python"
                return $candidate
            }
        }
        Write-Step "Bundled Python runtime is incomplete; continuing with runtime installation."
    }

    # 1. Reuse an existing runtime only after validating its imports.
    $venvPython = Get-VenvPython
    if ($venvPython -and (Test-RequiredPythonImports $venvPython)) {
        Write-Step "Python runtime ready at $InstallRoot\tools\python"
        return $venvPython
    }

    # 2. Check if prebuilt tools bundle is available online
    $toolsZipUrl = $env:VIDZEO_RUNTIME_TOOLS_URL
    if ([string]::IsNullOrWhiteSpace($toolsZipUrl)) {
        $toolsZipUrl = "https://github.com/armughan-zeb/Vidnets-Studio-Releases/releases/download/v1.0.0/vidnets-tools.zip"
    }

    $tempZip = Join-Path $env:TEMP ("vidnets-tools-" + [System.Guid]::NewGuid().ToString("N") + ".zip")
    $downloadedTools = $false
    try {
        Download-FileSafe $toolsZipUrl $tempZip "Vidnets prebuilt runtime tools"
        Write-Step "Extracting prebuilt tools bundle..."
        Expand-Archive -LiteralPath $tempZip -DestinationPath $ToolsDir -Force
        $downloadedTools = $true
    } catch {
        Write-Step "Prebuilt tools archive not reachable online ($($_.Exception.Message)). Falling back to component install..."
    } finally {
        Remove-Item -LiteralPath $tempZip -Force -ErrorAction SilentlyContinue
    }

    if ($downloadedTools) {
        $venvPython = Get-VenvPython
        if ($venvPython) {
            Write-Step "Prebuilt Python runtime installed and ready."
            return $venvPython
        }
    }

    # 3. Check system Python for virtual environment creation
    $sysPython = Find-SystemPython
    if ($sysPython) {
        Write-Step "Using $($sysPython.Label) to initialize virtual environment..."
        New-Item -ItemType Directory -Force -Path $PythonDir | Out-Null
        & $sysPython.Exe @($sysPython.Args + @("-m", "venv", $PythonDir))
        $venvPython = Get-VenvPython
        if ($venvPython) {
            Write-Step "Virtual environment created at $PythonDir"
            return $venvPython
        }
    }

    # 4. Download Python 3.10 embedded package from python.org
    Write-Step "Downloading standalone Python 3.10 from python.org..."
    New-Item -ItemType Directory -Force -Path $PythonDir | Out-Null
    $pyEmbedZip = Join-Path $env:TEMP ("python-embed-" + [System.Guid]::NewGuid().ToString("N") + ".zip")
    try {
        Download-FileSafe "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip" $pyEmbedZip "Python 3.10 embed"
        Expand-Archive -LiteralPath $pyEmbedZip -DestinationPath $PythonDir -Force
        $pthFile = Join-Path $PythonDir "python310._pth"
        $pthContent = "python310.zip`r`n.`r`nLib\site-packages`r`nimport site`r`n"
        Set-Content -LiteralPath $pthFile -Value $pthContent -Encoding ASCII
        Ensure-Pip (Join-Path $PythonDir "python.exe")
        $venvPython = Get-VenvPython
        if ($venvPython) {
            Write-Step "Standalone Python 3.10 installed successfully."
            return $venvPython
        }
    } catch {
        Write-Step "Embedded Python download failed: $($_.Exception.Message)"
    } finally {
        Remove-Item -LiteralPath $pyEmbedZip -Force -ErrorAction SilentlyContinue
    }

    # 5. Last resort: winget
    try {
        $sysPython = Ensure-SystemPython
        if ($sysPython) {
            New-Item -ItemType Directory -Force -Path $PythonDir | Out-Null
            & $sysPython.Exe @($sysPython.Args + @("-m", "venv", $PythonDir))
            $venvPython = Get-VenvPython
            if ($venvPython) {
                return $venvPython
            }
        }
    } catch {}

    throw "Could not install Python runtime. Please ensure an active internet connection is available or install Python 3.10+ from python.org."
}

function Ensure-Ffmpeg {
    $ffmpegExe = Join-Path $FfmpegDir "ffmpeg.exe"
    $ffprobeExe = Join-Path $FfmpegDir "ffprobe.exe"
    if ((Test-Path $ffmpegExe) -and (Test-Path $ffprobeExe)) {
        Write-Step "Bundled FFmpeg ready at $InstallRoot\tools\ffmpeg"
        return
    }

    # 1. Check if FFmpeg exists at SourceRoot
    $sourceFfmpeg = Join-Path $SourceFfmpegDir "ffmpeg.exe"
    $sourceFfprobe = Join-Path $SourceFfmpegDir "ffprobe.exe"
    if ((Test-Path $sourceFfmpeg) -and (Test-Path $sourceFfprobe)) {
        Write-Step "Copying bundled FFmpeg from source to install directory..."
        $parentDir = Split-Path -Parent $FfmpegDir
        New-Item -ItemType Directory -Force -Path $parentDir | Out-Null
        Copy-Item -Path $SourceFfmpegDir -Destination $parentDir -Recurse -Force
        if ((Test-Path $ffmpegExe) -and (Test-Path $ffprobeExe)) {
            Write-Step "Bundled FFmpeg copied and ready."
            return
        }
    }

    # 2. Check if system PATH has ffmpeg and ffprobe
    $sysFfmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $sysFfprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
    if ($sysFfmpeg -and $sysFfprobe) {
        Write-Step "Using system FFmpeg from PATH..."
        New-Item -ItemType Directory -Force -Path $FfmpegDir | Out-Null
        Copy-Item -LiteralPath $sysFfmpeg.Source -Destination $ffmpegExe -Force
        Copy-Item -LiteralPath $sysFfprobe.Source -Destination $ffprobeExe -Force
        if ((Test-Path $ffmpegExe) -and (Test-Path $ffprobeExe)) {
            Write-Step "System FFmpeg copied and ready."
            return
        }
    }

    # 3. Download official FFmpeg essentials from web
    Write-Step "Downloading FFmpeg from the internet..."
    New-Item -ItemType Directory -Force -Path $FfmpegDir | Out-Null
    $ffmpegZip = Join-Path $env:TEMP ("vidnets-ffmpeg-" + [System.Guid]::NewGuid().ToString("N") + ".zip")
    $ffmpegTmp = Join-Path $env:TEMP ("vidnets-ffmpeg-extract-" + [System.Guid]::NewGuid().ToString("N"))
    try {
        $urls = @(
            "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
            "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        )
        $downloadSuccess = $false
        foreach ($url in $urls) {
            try {
                Download-FileSafe $url $ffmpegZip "FFmpeg release package"
                $downloadSuccess = $true
                break
            } catch {
                Write-Step "Mirror $url failed, trying next..."
            }
        }
        if (-not $downloadSuccess) {
            throw "All FFmpeg download mirrors failed."
        }

        Write-Step "Extracting FFmpeg binaries..."
        New-Item -ItemType Directory -Force -Path $ffmpegTmp | Out-Null
        Expand-Archive -LiteralPath $ffmpegZip -DestinationPath $ffmpegTmp -Force
        $foundFfmpeg = Get-ChildItem -LiteralPath $ffmpegTmp -Filter "ffmpeg.exe" -Recurse -File | Select-Object -First 1
        $foundFfprobe = Get-ChildItem -LiteralPath $ffmpegTmp -Filter "ffprobe.exe" -Recurse -File | Select-Object -First 1
        if ($foundFfmpeg -and $foundFfprobe) {
            Copy-Item -LiteralPath $foundFfmpeg.FullName -Destination $ffmpegExe -Force
            Copy-Item -LiteralPath $foundFfprobe.FullName -Destination $ffprobeExe -Force
            Write-Step "FFmpeg installed successfully."
            return
        }
        throw "ffmpeg.exe / ffprobe.exe not found inside extracted archive."
    } finally {
        Remove-Item -LiteralPath $ffmpegZip -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $ffmpegTmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Test-PythonImport([string]$PythonExe, [string]$ModuleName) {
    & $PythonExe -I -c "import $ModuleName" *> $null
    return $LASTEXITCODE -eq 0
}

function Test-RequiredPythonImports([string]$PythonExe) {
    $checkCode = @'
import importlib
modules = [
    'flask',
    'flask_cors',
    'requests',
    'numpy',
    'scipy',
    'PIL',
    'rembg',
    'soundfile',
    '_cffi_backend',
    'pydub',
    'edge_tts',
    'ddgs',
    'gallery_dl',
    'psutil',
    'faster_whisper',
    'onnxruntime',
    'cv2',
    'rapidocr_onnxruntime',
    'pyclipper',
    'shapely',
]
missing = []
for module in modules:
    try:
        importlib.import_module(module)
    except Exception as exc:
        missing.append('{}: {}'.format(module, exc))
if missing:
    print('MISSING_RUNTIME_IMPORTS')
    print('\n'.join(missing))
    raise SystemExit(1)
print('runtime-imports-ok')
'@
    $checkPath = Join-Path $env:TEMP ("vidzeo-runtime-check-" + [System.Guid]::NewGuid().ToString("N") + ".py")
    $oldDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
    try {
        Set-Content -LiteralPath $checkPath -Value $checkCode -Encoding UTF8
        $env:PYTHONDONTWRITEBYTECODE = "1"
        # Probe the bundled runtime only. User site-packages can otherwise
        # falsely satisfy imports that the isolated Electron backend cannot use.
        & $PythonExe -I $checkPath
        $exitCode = $LASTEXITCODE
        $env:PYTHONDONTWRITEBYTECODE = $oldDontWriteBytecode
        return $exitCode -eq 0
    } finally {
        $env:PYTHONDONTWRITEBYTECODE = $oldDontWriteBytecode
        Remove-Item -LiteralPath $checkPath -Force -ErrorAction SilentlyContinue
    }
}

function Ensure-Pip([string]$PythonExe) {
    & $PythonExe -I -m pip --version *> $null
    if ($LASTEXITCODE -eq 0) {
        return
    }

    Write-Step "pip is missing. Trying bundled ensurepip..."
    & $PythonExe -I -m ensurepip --upgrade
    if ($LASTEXITCODE -eq 0) {
        & $PythonExe -I -m pip --version *> $null
        if ($LASTEXITCODE -eq 0) {
            return
        }
    }

    Write-Step "ensurepip failed. Downloading get-pip.py..."
    $getPipPath = Join-Path $env:TEMP "get-pip.py"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $web = New-Object System.Net.WebClient
        $web.DownloadFile("https://bootstrap.pypa.io/get-pip.py", $getPipPath)
        & $PythonExe -I $getPipPath --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "get-pip.py execution failed"
        }
        & $PythonExe -I -m pip --version *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "pip still unavailable after get-pip.py install"
        }
        Write-Step "pip installed via get-pip.py."
    } catch {
        throw "Could not install pip. Ensure the Python runtime includes pip or that this machine has internet access."
    } finally {
        Remove-Item -LiteralPath $getPipPath -Force -ErrorAction SilentlyContinue
    }
}

function Ensure-PythonDependencies([string]$PythonExe) {
    if (Test-RequiredPythonImports $PythonExe) {
        Write-Step "Python runtime dependencies ready."
        return
    }

    Write-Step "Installing missing Python runtime dependencies..."
    Ensure-Pip $PythonExe
    Invoke-Checked $PythonExe @("-I", "-m", "pip", "install", "--retries", "3", "--timeout", "120", "-r", $Requirements) "Python dependency install"

    if (-not (Test-RequiredPythonImports $PythonExe)) {
        throw "Python runtime dependency validation failed after install."
    }
    Write-Step "Python runtime dependencies ready."
}

if (-not (Test-Path $Requirements)) {
    throw "Missing requirements file: $Requirements"
}

if ($ValidateOnly) {
    Write-Step "Bootstrap path validation passed."
    Write-Host "InstallRoot: $InstallRoot"
    Write-Host "SourceRoot: $SourceRoot"
    exit 0
}

New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallRoot "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallRoot "cache") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallRoot "temp") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallRoot "output") | Out-Null

$python = Ensure-PythonRuntime
Ensure-Ffmpeg
Ensure-PythonDependencies $python

Set-Content -LiteralPath $RuntimeStamp -Value ("ready $RuntimeStampVersion " + (Get-Date).ToString("s")) -Encoding UTF8
Write-Step "Runtime ready."
Write-Host "Python: $python"
Write-Host "FFmpeg: $(Join-Path $FfmpegDir 'ffmpeg.exe')"
