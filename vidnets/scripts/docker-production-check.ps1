param(
    [string]$Root = "",
    [string]$ImageName = "vidzeoapp-backend:latest",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "[Vidzeo Docker QA] $Message"
}

function Resolve-Root {
    param([string]$RequestedRoot)
    if ([string]::IsNullOrWhiteSpace($RequestedRoot)) {
        return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
    }
    return (Resolve-Path -LiteralPath $RequestedRoot.Trim('"', "'", " ", "`t", "`r", "`n")).Path
}

function Find-DockerExe {
    $cmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    $candidates = @(
        "C:\Program Files\Docker\Docker\resources\bin\docker.exe",
        "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    throw "Docker CLI was not found. Install/start Docker Desktop or add Docker to PATH."
}

function Invoke-Docker {
    param([string[]]$Arguments)
    & $script:DockerExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Invoke-DockerInput {
    param(
        [string[]]$Arguments,
        [string]$InputText
    )
    $InputText | & $script:DockerExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

$ProjectRoot = Resolve-Root $Root
Set-Location $ProjectRoot

$script:DockerExe = Find-DockerExe
$dockerBin = Split-Path -Parent $script:DockerExe
if ($env:PATH -notlike "*$dockerBin*") {
    $env:PATH = "$dockerBin;$env:PATH"
}

Write-Step "Checking Docker engine"
Invoke-Docker @("info", "--format", "Docker {{.ServerVersion}} {{.OSType}} {{.Architecture}}")

Write-Step "Validating docker-compose backend config"
Invoke-Docker @("compose", "-f", "docker-compose.backend.yml", "config", "--quiet")

if (-not $SkipBuild) {
    Write-Step "Building backend image from current source"
    Invoke-Docker @("build", "-f", "Dockerfile.backend", "-t", $ImageName, ".")
}

$contractCheck = @'
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/server")

import server.app as app
from services.pipeline_contracts import MediaContractError

forbidden = [
    "Generated Image Rescue",
    "Created cinematic text card",
    "_render_video_still_fallback",
    "video render fallback",
    "Minimal placeholder generated",
    "source='placeholder'",
    'source="placeholder"',
    "using placeholder",
    "_fallback.jpg",
    "download_url=''",
    'download_url=""',
]
for rel in [
    "server/app.py",
    "server/services/video_service.py",
    "server/services/real_media_service.py",
]:
    text = Path("/app", rel).read_text(encoding="utf-8", errors="ignore")
    hits = [needle for needle in forbidden if needle in text]
    if hits:
        raise SystemExit(f"forbidden fallback snippets in {rel}: {hits}")

transcription_text = Path("/app/server/services/transcription_service.py").read_text(encoding="utf-8", errors="ignore")
video_text = Path("/app/server/services/video_service.py").read_text(encoding="utf-8", errors="ignore")
forbidden_whisper = [
    "import whisper",
    "openai-whisper",
    "whisper.load_model",
    "OpenAI Whisper",
    "VIDZEO_PREFER_FASTER_WHISPER",
]
hits = [needle for needle in forbidden_whisper if needle in transcription_text or needle in video_text]
if hits:
    raise SystemExit(f"forbidden OpenAI Whisper fallback snippets remain: {hits}")

import faster_whisper

app._fill_unresolved_with_general_web_fallback = lambda **kwargs: None
app._run_youtube_render_repair = lambda **kwargs: None
try:
    app._repair_media_plan_for_render(
        media_plan={"media_paths": [None], "source_labels": [""], "prompt_bundle": {"beat_texts": ["scene"]}},
        audio_plan={"scene_texts": ["scene"]},
        job_config={"image_source": "Mixed Media"},
        render_resolution=(1280, 720),
        title="Docker Production QA",
        script="Docker Production QA scene",
        target_scene_count=1,
    )
except MediaContractError as exc:
    if exc.detail.get("fallback_policy") != "no_blank_or_text_card_fallbacks":
        raise
else:
    raise SystemExit("expected MediaContractError from no-blank media contract")

print("docker-production-contracts-ok")
'@

Write-Step "Running containerized media/no-blank contract checks"
Invoke-DockerInput @(
    "run", "--rm", "-i",
    "-e", "PYTHONPATH=/app:/app/server",
    "-e", "PYTHONIOENCODING=utf-8",
    "-e", "VIDZEO_WEB_FAST_MODE=1",
    "-e", "VIDZEO_FASTER_WHISPER_COMPUTE_TYPE=int8",
    "-e", "VIDZEO_FORCE_BURN_SUBTITLES=0",
    "-e", "VIDZEO_YT_BATCH_SIZE=8",
    "-e", "VIDZEO_YT_DOWNLOAD_CONCURRENCY=6",
    "-e", "VIDZEO_YT_BATCH_DOWNLOAD_TIMEOUT=300",
    $ImageName,
    "python", "-"
) $contractCheck

Write-Step "Booting backend container and checking /api/status"
$containerId = (& $script:DockerExe run -d --rm `
    -e PYTHONPATH=/app:/app/server `
    -e PYTHONIOENCODING=utf-8 `
    -e VIDZEO_BIND_HOST=0.0.0.0 `
    -e VIDZEO_WEB_FAST_MODE=1 `
    -e VIDZEO_FASTER_WHISPER_COMPUTE_TYPE=int8 `
    -e VIDZEO_FORCE_BURN_SUBTITLES=0 `
    -e VIDZEO_YT_BATCH_SIZE=8 `
    -e VIDZEO_YT_DOWNLOAD_CONCURRENCY=6 `
    -e VIDZEO_YT_BATCH_DOWNLOAD_TIMEOUT=300 `
    $ImageName)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($containerId)) {
    throw "Could not start backend container."
}

try {
    $ready = $false
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        $output = ""
        $exitCode = 1
        try {
            $output = (& $script:DockerExe exec $containerId python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5000/api/status', timeout=2).read().decode())" 2>&1) -join "`n"
            $exitCode = $LASTEXITCODE
        } catch {
            $output = $_.Exception.Message
            $exitCode = 1
        }
        if ($exitCode -eq 0 -and $output -match '"ready"\s*:\s*true') {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        & $script:DockerExe logs --tail 120 $containerId
        throw "Backend container did not report ready /api/status."
    }
    Write-Host "docker-backend-status-ok"
} finally {
    & $script:DockerExe rm -f $containerId *> $null
}

Write-Step "Docker production checks passed"
