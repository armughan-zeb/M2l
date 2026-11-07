param(
    [string]$Root = ""
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "[Vidzeo Package] $Message"
}

function Resolve-Root {
    param([string]$RequestedRoot)
    $cleanRoot = ""
    if (-not [string]::IsNullOrWhiteSpace($RequestedRoot)) {
        $cleanRoot = $RequestedRoot.Trim()
        $cleanRoot = $cleanRoot.Trim([char[]]@('"', "'", " ", "`t", "`r", "`n"))
    }

    if ([string]::IsNullOrWhiteSpace($cleanRoot)) {
        return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
    }

    try {
        return (Resolve-Path -LiteralPath $cleanRoot).Path
    } catch {
        $fallbackRoot = $cleanRoot.TrimEnd('\', '/')
        $fallbackRoot = $fallbackRoot.Trim([char[]]@('"', "'", " ", "`t", "`r", "`n"))
        if (-not [string]::IsNullOrWhiteSpace($fallbackRoot)) {
            return (Resolve-Path -LiteralPath $fallbackRoot).Path
        }
        throw
    }
}

function Assert-InDist {
    param(
        [string]$Path,
        [string]$DistRoot
    )
    $full = [System.IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($DistRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify path outside dist: $full"
    }
    return $full
}

function Remove-PathRobust {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    for ($attempt = 1; $attempt -le 4; $attempt++) {
        try {
            Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue |
                ForEach-Object {
                    try {
                        $_.Attributes = [System.IO.FileAttributes]::Normal
                    } catch {
                    }
                }
            try {
                (Get-Item -LiteralPath $Path -Force).Attributes = [System.IO.FileAttributes]::Normal
            } catch {
            }

            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return
        } catch {
            try {
                if (Test-Path -LiteralPath $Path) {
                    [System.IO.Directory]::Delete([System.IO.Path]::GetFullPath($Path), $true)
                    return
                }
            } catch {
            }
            try {
                if (Test-Path -LiteralPath $Path) {
                    Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction SilentlyContinue |
                        Sort-Object FullName -Descending |
                        ForEach-Object {
                            try {
                                $_.Attributes = [System.IO.FileAttributes]::Normal
                                Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
                            } catch {
                            }
                        }
                    Get-ChildItem -LiteralPath $Path -Recurse -Force -Directory -ErrorAction SilentlyContinue |
                        Sort-Object FullName -Descending |
                        ForEach-Object {
                            try {
                                $_.Attributes = [System.IO.FileAttributes]::Normal
                                Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
                            } catch {
                            }
                        }
                    Remove-Item -LiteralPath $Path -Force -ErrorAction Stop
                    return
                }
            } catch {
            }
            if ($attempt -eq 4) {
                throw
            }
            Start-Sleep -Seconds ($attempt * 2)
        }
    }
}

function Stop-Product-Processes {
    param([string]$ProjectRoot)

    $distNeedle = (Join-Path $ProjectRoot "dist\Vidnets-Studio-Product")
    $electronNeedle = (Join-Path $ProjectRoot "node_modules\electron")
    $nodeElectronNeedle = (Join-Path $ProjectRoot "node_modules\.bin")
    $currentPid = $PID

    $targets = Get-CimInstance Win32_Process | Where-Object {
        $_.ProcessId -ne $currentPid -and
        $_.CommandLine -and (
            $_.CommandLine.IndexOf($distNeedle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            $_.CommandLine.IndexOf($electronNeedle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            ($_.CommandLine.IndexOf($nodeElectronNeedle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and $_.CommandLine.IndexOf("electron", [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
        )
    }

    if (-not $targets) {
        Write-Host "No old product app processes found."
        return
    }

    foreach ($target in $targets) {
        Write-Host ("Stopping old product process: {0} {1}" -f $target.ProcessId, $target.Name)
        Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
    }

    Start-Sleep -Seconds 2
}

function Remove-Product-Outputs {
    param(
        [string]$ProjectRoot,
        [string]$DistRoot
    )

    $targets = @(
        "Vidnets-Studio-Product",
        "Vidnets-Studio-Product.zip",
        "Vidnets-Studio-Product-Ready",
        "Vidnets-Studio-Product-Ready.zip",
        "Vidnets-Studio-Product-App",
        "Vidnets-Studio-Product-App.zip",
        "Vidnets-Studio-Product-App-Ready",
        "Vidnets-Studio-Product-App-Ready.zip"
    )

    foreach ($name in $targets) {
        $target = Assert-InDist (Join-Path $DistRoot $name) $DistRoot
        if (Test-Path -LiteralPath $target) {
            Write-Host "Removing old output: dist\$name"
            Remove-PathRobust $target
        }
    }
}

function Invoke-ProductBuilder {
    param([string]$ProjectRoot)

    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python create_product_folder.py
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 create_product_folder.py
    } else {
        throw "Python was not found. Install Python 3.10+ first."
    }

    if ($LASTEXITCODE -ne 0) {
        throw "create_product_folder.py failed with exit code $LASTEXITCODE"
    }
}

function Test-CurrentPythonSyntax {
    param([string]$ProjectRoot)

    $pythonExe = Join-Path $ProjectRoot "tools\python\python.exe"
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        if (Get-Command python -ErrorAction SilentlyContinue) {
            $pythonExe = "python"
        } elseif (Get-Command py -ErrorAction SilentlyContinue) {
            $pythonExe = "py"
        } else {
            throw "Python was not found. Cannot validate backend syntax before packaging."
        }
    }

    $files = @(
        "server\app.py",
        "server\services\youtube_clips_service.py",
        "server\services\web_image\query.py",
        "server\services\openrouter_llm.py",
        "server\services\motion_templates.py",
        "server\services\video_service.py"
    )

    if ([System.IO.Path]::GetFileName($pythonExe).Equals("py", [System.StringComparison]::OrdinalIgnoreCase)) {
        & $pythonExe -3 -m py_compile @files
    } else {
        & $pythonExe -m py_compile @files
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Current backend Python syntax validation failed. Refusing to package a broken build."
    }
}

function Test-RequiredPath {
    param(
        [string]$Base,
        [string]$Relative
    )
    $path = Join-Path $Base $Relative
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Final app folder is incomplete. Missing: $Relative"
    }
}

function Get-RelativePathCompat {
    param(
        [string]$Base,
        [string]$FullPath
    )

    $baseFull = [System.IO.Path]::GetFullPath($Base).TrimEnd('\', '/')
    $pathFull = [System.IO.Path]::GetFullPath($FullPath)
    if ($pathFull.StartsWith($baseFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        $relative = $pathFull.Substring($baseFull.Length).TrimStart('\', '/')
        if ([string]::IsNullOrWhiteSpace($relative)) {
            return "."
        }
        return $relative
    }
    return $pathFull
}

function Test-CleanPackageTree {
    param(
        [string]$Base,
        [string]$Label
    )

    $allowedNames = @(".env.public", ".env.example")
    $forbiddenNamePatterns = @(
        "*.enc.json",
        "*.bak",
        "*.tmp",
        "*.log",
        "*.pyc",
        "*.pyo",
        "auth-session*.json",
        "secret-settings*.json",
        "step_*_content.txt",
        "queue.json",
        "queue.json.bak",
        "queue.json.tmp",
        "test_*",
        "tmp_*",
        "debug_*",
        "patch*",
        "auto*",
        "kaggle_*"
    )
    $forbiddenDirNames = @(
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "admin-panel",
        "output",
        "temp",
        "tmp",
        "cache",
        "logs",
        "build",
        "dist"
    )
    $forbiddenRelativePrefixes = @(
        "resources\tools\python\Lib\site-packages\torch",
        "resources\tools\python\Lib\site-packages\spacy",
        "resources\tools\python\Lib\site-packages\transformers"
    )

    $offenders = New-Object System.Collections.Generic.List[string]
    Get-ChildItem -LiteralPath $Base -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {
        if ($offenders.Count -ge 40) {
            return
        }

        $relative = Get-RelativePathCompat $Base $_.FullName
        $relativeForMatch = $relative.Replace("/", "\")
        $name = $_.Name
        $lowerName = $name.ToLowerInvariant()
        $inSitePackages = $relativeForMatch.IndexOf("resources\tools\python\Lib\site-packages\", [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            $relativeForMatch.IndexOf("tools\python\Lib\site-packages\", [System.StringComparison]::OrdinalIgnoreCase) -ge 0

        if ($allowedNames -contains $lowerName) {
            return
        }

        foreach ($prefix in $forbiddenRelativePrefixes) {
            if ($relative.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                $offenders.Add($relative)
                return
            }
        }

        if ($_.PSIsContainer -and ($forbiddenDirNames -contains $lowerName)) {
            $offenders.Add($relative)
            return
        }

        if ($inSitePackages) {
            foreach ($pattern in @("*.enc.json", "*.bak", "*.tmp", "*.log", "*.pyc", "*.pyo")) {
                if ($name -like $pattern) {
                    $offenders.Add($relative)
                    return
                }
            }
        } else {
            foreach ($pattern in $forbiddenNamePatterns) {
                if ($name -like $pattern) {
                    $offenders.Add($relative)
                    return
                }
            }
        }
    }

    if ($offenders.Count -gt 0) {
        throw "$Label contains forbidden dirty/runtime files:`n  - $($offenders -join "`n  - ")"
    }
}

function Test-PipelineParity {
    param(
        [string]$ProjectRoot,
        [string]$PackagedRoot,
        [string]$Label,
        [switch]$ResourcesPrefix
    )

    $files = @(
        "main.py",
        "scripts\bootstrap-runtime.ps1",
        "server\app.py",
        "server\requirements.txt",
        "server\services\transcription_service.py",
        "server\services\video_service.py",
        "server\services\real_media_service.py",
        "server\services\youtube_clips_service.py",
        "server\services\openrouter_llm.py",
        "server\services\motion_templates.py",
        "server\services\web_image\query.py",
        "server\services\web_image\resolver.py",
        "server\services\web_image_service.py"
    )

    if (-not $ResourcesPrefix) {
        $files = @("main.js") + $files
    }

    $mismatches = New-Object System.Collections.Generic.List[string]
    foreach ($relative in $files) {
        $source = Join-Path $ProjectRoot $relative
        $targetRelative = $relative
        if ($ResourcesPrefix) {
            $targetRelative = Join-Path "resources" $relative
        }
        $target = Join-Path $PackagedRoot $targetRelative
        if (-not (Test-Path -LiteralPath $source)) {
            $mismatches.Add("missing source: $relative")
            continue
        }
        if (-not (Test-Path -LiteralPath $target)) {
            $mismatches.Add("missing packaged file: $targetRelative")
            continue
        }
        $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
        $targetHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
        if ($sourceHash -ne $targetHash) {
            $mismatches.Add($targetRelative)
        }
    }

    if ($mismatches.Count -gt 0) {
        throw "$Label does not match main app pipeline/runtime files:`n  - $($mismatches -join "`n  - ")"
    }
}

function Test-NoBlankFallbackPolicy {
    param(
        [string]$Base,
        [string]$Label,
        [switch]$ResourcesPrefix
    )

    $prefix = ""
    if ($ResourcesPrefix) {
        $prefix = "resources\"
    }

    $files = @(
        "${prefix}server\app.py",
        "${prefix}server\services\video_service.py",
        "${prefix}server\services\real_media_service.py"
    )
    $forbiddenSnippets = @(
        "Generated Image Rescue",
        "Created cinematic text card",
        "_render_video_still_fallback",
        "video render fallback",
        "Minimal placeholder generated",
        "source='placeholder'",
        'source="placeholder"',
        "using placeholder",
        "download_url=''",
        'download_url=""',
        "solid color"
    )

    $hits = New-Object System.Collections.Generic.List[string]
    foreach ($relative in $files) {
        $path = Join-Path $Base $relative
        if (-not (Test-Path -LiteralPath $path)) {
            $hits.Add("missing policy file: $relative")
            continue
        }
        foreach ($snippet in $forbiddenSnippets) {
            $match = Select-String -LiteralPath $path -SimpleMatch $snippet -List -ErrorAction SilentlyContinue
            if ($match) {
                $hits.Add("$relative contains '$snippet'")
            }
        }
    }

    if ($hits.Count -gt 0) {
        throw "$Label violates no-blank-fallback policy:`n  - $($hits -join "`n  - ")"
    }
}

function Test-PublicEnvConfig {
    param(
        [string]$Base,
        [string]$Label,
        [switch]$ResourcesPrefix
    )

    $relative = ".env.public"
    if ($ResourcesPrefix) {
        $relative = "resources\.env.public"
    }
    $path = Join-Path $Base $relative
    if (-not (Test-Path -LiteralPath $path)) {
        throw "$Label is missing .env.public, so Supabase auth will not be configured."
    }

    $values = @{}
    Get-Content -LiteralPath $path -ErrorAction Stop | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or $line.IndexOf("=") -le 0) {
            return
        }
        $key, $value = $line.Split("=", 2)
        $values[$key.Trim()] = $value.Trim().Trim('"').Trim("'")
    }

    $missing = @()
    foreach ($key in @("SUPABASE_URL", "SUPABASE_ANON_KEY")) {
        if (-not $values.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($values[$key])) {
            $missing += $key
        }
    }
    if ($missing.Count -gt 0) {
        throw "$Label has incomplete public Supabase config in .env.public. Missing: $($missing -join ', ')"
    }

    $publicEnv = Get-Content -LiteralPath $path -Raw -ErrorAction Stop
    if ($publicEnv.Contains("SUPABASE_SERVICE_ROLE_KEY") -or $publicEnv.ToLowerInvariant().Contains("service_role")) {
        throw "$Label .env.public must not contain a Supabase service-role key."
    }
}

function Test-QueueUiPolicy {
    param(
        [string]$Base,
        [string]$Label,
        [switch]$ResourcesPrefix
    )

    $relative = "src\renderer.js"
    if ($ResourcesPrefix) {
        $relative = "resources\src\renderer.js"
    }
    $path = Join-Path $Base $relative
    if (-not (Test-Path -LiteralPath $path)) {
        throw "$Label is missing $relative, so queue UI validation cannot run."
    }

    $renderer = Get-Content -LiteralPath $path -Raw -ErrorAction Stop
    $requiredSnippets = @(
        "queueRunObservedActiveWork",
        "queueRunNotifyOnComplete",
        "startPolling({ notifyOnComplete: true })",
        "startPolling({ notifyOnComplete: false })"
    )
    $missing = @()
    foreach ($snippet in $requiredSnippets) {
        if (-not $renderer.Contains($snippet)) {
            $missing += $snippet
        }
    }
    if ($missing.Count -gt 0) {
        throw "$Label has stale queue polling UI code; repeated completion toasts may return. Missing:`n  - $($missing -join "`n  - ")"
    }
}

function Remove-BytecodeArtifacts {
    param([string]$Base)

    Get-ChildItem -LiteralPath $Base -Recurse -Force -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq "__pycache__" } |
        Sort-Object FullName -Descending |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
    Get-ChildItem -LiteralPath $Base -Recurse -Force -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in @(".pyc", ".pyo") } |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
        }
}

function Remove-PackageRuntimeArtifacts {
    param([string]$Base)

    Remove-BytecodeArtifacts $Base
    Get-ChildItem -LiteralPath $Base -Recurse -Force -File -Filter ".vidzeo_session_secret" -ErrorAction SilentlyContinue |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
        }
    foreach ($relative in @("resources\temp", "resources\cache", "resources\logs", "resources\output", "temp", "cache", "logs", "output")) {
        $target = Join-Path $Base $relative
        if (Test-Path -LiteralPath $target) {
            Remove-PathRobust $target
        }
    }
}

function Test-ConsumerApp {
    param([string]$ProjectRoot)

    $appDir = Join-Path $ProjectRoot "dist\Vidnets-Studio-Product-App"
    $zipPath = Join-Path $ProjectRoot "dist\Vidnets-Studio-Product-App.zip"

    if (-not (Test-Path -LiteralPath $appDir)) {
        throw "Final app folder was not created: dist\Vidnets-Studio-Product-App"
    }
    if (-not (Test-Path -LiteralPath $zipPath)) {
        throw "Final app zip was not created: dist\Vidnets-Studio-Product-App.zip"
    }

    $exeName = if (Test-Path -LiteralPath (Join-Path $appDir "Vidnets Studio v1.exe")) { "Vidnets Studio v1.exe" } else { "Vidnets Studio.exe" }
    $required = @(
        $exeName,
        "START_VIDNETS_STUDIO.bat",

        "resources\app.asar",
        "resources\main.py",
        "resources\src\index.html",
        "resources\src\renderer.js",
        "resources\server\app.py",
        "resources\server\services\auth_service.py",
        "resources\server\services\transcription_service.py",
        "resources\server\services\video_service.py",
        "resources\server\services\real_media_service.py",
        "resources\server\requirements.txt",
        "resources\scripts\bootstrap-runtime.ps1",
        "resources\tools\python\python.exe",
        "resources\tools\python\Lib\site-packages\faster_whisper",
        "resources\tools\python\Lib\site-packages\onnxruntime",
        "resources\tools\ffmpeg\ffmpeg.exe",
        "resources\.env.public"
    )

    foreach ($relative in $required) {
        Test-RequiredPath $appDir $relative
    }

    $forbidden = @(
        "resources\.env",
        "resources\admin-panel",
        "resources\server\queue.json",
        "resources\tools\python\Lib\site-packages\torch",
        "resources\tools\python\Lib\site-packages\spacy",
        "resources\tools\python\Lib\site-packages\transformers"
    )

    foreach ($relative in $forbidden) {
        if (Test-Path -LiteralPath (Join-Path $appDir $relative)) {
            throw "Final app folder contains forbidden dev/heavy file: $relative"
        }
    }

    Test-CleanPackageTree $appDir "Final app folder"
    Test-PipelineParity $ProjectRoot $appDir "Final app folder" -ResourcesPrefix
    Test-NoBlankFallbackPolicy $appDir "Final app folder" -ResourcesPrefix
    Test-PublicEnvConfig $appDir "Final app folder" -ResourcesPrefix
    Test-QueueUiPolicy $appDir "Final app folder" -ResourcesPrefix

    $zipInfo = Get-Item -LiteralPath $zipPath
    if ($zipInfo.Length -lt 200MB) {
        throw "Final app zip looks too small for the full product folder: $([math]::Round($zipInfo.Length / 1MB, 2)) MB"
    }

    $pythonExe = Join-Path $appDir "resources\tools\python\python.exe"
    $oldDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
    $oldPythonIoEncoding = $env:PYTHONIOENCODING
    $oldSessionSecretFile = $env:VIDZEO_SESSION_SECRET_FILE
    $env:PYTHONDONTWRITEBYTECODE = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $env:VIDZEO_SESSION_SECRET_FILE = Join-Path $env:TEMP ("vidzeo-validation-session-" + [System.Guid]::NewGuid().ToString("N") + ".txt")
    & $pythonExe -c "import flask, requests, numpy, scipy, PIL, soundfile, _cffi_backend, edge_tts, ddgs, gallery_dl, psutil, faster_whisper, onnxruntime, cv2, rapidocr_onnxruntime, pyclipper, shapely; print('packaged-python-ok')"
    $env:PYTHONDONTWRITEBYTECODE = $oldDontWriteBytecode
    $env:PYTHONIOENCODING = $oldPythonIoEncoding
    $env:VIDZEO_SESSION_SECRET_FILE = $oldSessionSecretFile
    if ($LASTEXITCODE -ne 0) {
        throw "Packaged Python runtime import validation failed."
    }

    $env:PYTHONDONTWRITEBYTECODE = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $env:VIDZEO_SESSION_SECRET_FILE = Join-Path $env:TEMP ("vidzeo-validation-session-" + [System.Guid]::NewGuid().ToString("N") + ".txt")
    & $pythonExe -c "import os, sys, importlib.util; sys.path.insert(0, os.getcwd()); spec=importlib.util.spec_from_file_location('main', os.path.join(os.getcwd(), 'main.py')); main=importlib.util.module_from_spec(spec); spec.loader.exec_module(main); main.load_local_env(); from server.services.auth_service import auth_is_configured; raise SystemExit(0 if auth_is_configured() else 1)"
    $env:PYTHONDONTWRITEBYTECODE = $oldDontWriteBytecode
    $env:PYTHONIOENCODING = $oldPythonIoEncoding
    $env:VIDZEO_SESSION_SECRET_FILE = $oldSessionSecretFile
    if ($LASTEXITCODE -ne 0) {
        throw "Packaged backend does not see Supabase public config from .env.public."
    }
    Remove-PackageRuntimeArtifacts $appDir

    $bootstrap = Join-Path $appDir "resources\scripts\bootstrap-runtime.ps1"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrap -InstallRoot (Join-Path $appDir "resources") -SourceRoot (Join-Path $appDir "resources") -ValidateOnly
    if ($LASTEXITCODE -ne 0) {
        throw "Packaged runtime bootstrap validation failed."
    }

    Remove-PackageRuntimeArtifacts $appDir
    Test-CleanPackageTree $appDir "Final app folder"

    Write-Host ""
    Write-Host "Validated final app folder:"
    Write-Host "  $appDir"
    Write-Host ("  ZIP size: {0} MB" -f [math]::Round($zipInfo.Length / 1MB, 2))
}

$projectRoot = Resolve-Root $Root
$distRoot = Join-Path $projectRoot "dist"
$distRoot = [System.IO.Path]::GetFullPath($distRoot)

Set-Location $projectRoot

Write-Step "Stopping stale app/build processes"
Stop-Product-Processes $projectRoot

Write-Step "Removing old partial product outputs"
New-Item -ItemType Directory -Force -Path $distRoot | Out-Null
Remove-Product-Outputs $projectRoot $distRoot

Write-Step "Validating current backend syntax"
Test-CurrentPythonSyntax $projectRoot

Write-Step "Building clean full product folder and app zip"
Invoke-ProductBuilder $projectRoot

Write-Step "Validating final customer app folder"
Test-ConsumerApp $projectRoot

Write-Step "Clean full product package is ready"
