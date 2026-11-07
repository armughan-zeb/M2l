import json
import os
import shutil
import subprocess
import zipfile
import argparse
import fnmatch
import hashlib
from pathlib import Path


ROOT_DIR = Path(__file__).parent.resolve()
DIST_DIR = ROOT_DIR / "dist"
PRODUCT_DIR = DIST_DIR / "Vidnets-Studio-Product"
PRODUCT_ZIP = DIST_DIR / "Vidnets-Studio-Product.zip"
APP_DIR = DIST_DIR / "Vidnets-Studio-Product-App"
APP_ZIP = DIST_DIR / "Vidnets-Studio-Product-App.zip"

ROOT_FILES = [
    "VIDNETS_AGENT.cmd",
    "AGENT_ACCESS.md",
    "main.js",
    "preload.js",
    "main.py",
    "package.json",
    "package-lock.json",
    "gallery-dl.conf",
    ".env.defaults",
    ".env.example",
]



ROOT_DIRS = [
    "src",
    "server",
    "fonts",
    "public",
    "resources",
]

TOOL_DIRS = [
    ("tools/ffmpeg", "tools/ffmpeg"),
]

SITE_PACKAGE_PREFIXES = {
    "_cffi_backend",
    "_soundfile",
    "_soundfile_data",
    "addict",
    "aiohappyeyeballs",
    "aiohttp",
    "aiosignal",
    "annotated_types",
    "anyio",
    "async_timeout",
    "attr",
    "attrs",
    "av",
    "av.libs",
    "blinker",
    "certifi",
    "cffi",
    "charset_normalizer",
    "click",
    "colorama",
    "coloredlogs",
    "ctranslate2",
    "cv2",
    "dateutil",
    "ddgs",
    "distro",
    "docopt",
    "dotenv",
    "edge_playback",
    "edge_tts",
    "exceptiongroup",
    "faster_whisper",
    "filelock",
    "flask",
    "flask_cors",
    "flatbuffers",
    "frozenlist",
    "fsspec",
    "gallery_dl",
    "google",
    "groq",
    "h11",
    "httpcore",
    "httpx",
    "huggingface_hub",
    "humanfriendly",
    "idna",
    "itsdangerous",
    "jinja2",
    "joblib",
    "markupsafe",
    "more_itertools",
    "mpmath",
    "multidict",
    "networkx",
    "numpy",
    "numpy.libs",
    "onnxruntime",
    "onnxruntime.libs",
    "openai",
    "opencv_python_headless",
    "rapidocr_onnxruntime",
    "packaging",
    "PIL",
    "pip",
    "primp",
    "propcache",
    "protobuf",
    "psutil",
    "pycparser",
    "pyclipper",
    "pydantic",
    "pydantic_core",
    "pydub",
    "python_dateutil",
    "pyyaml",
    "regex",
    "requests",
    "safetensors",
    "scipy",
    "scipy.libs",
    "setuptools",
    "shapely",
    "shapely.libs",
    "six",
    "sniffio",
    "soundfile",
    "sympy",
    "tiktoken",
    "tokenizers",
    "tqdm",
    "typing_extensions",
    "typing_inspection",
    "urllib3",
    "werkzeug",
    "wheel",
    "yarl",
    "yaml",
    "youtube_transcript_api",
    "yt_dlp",
}

EXCLUDED_DIR_NAMES = {
    ".git",
    ".vscode",
    ".commandcode",
    ".opencode",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "test",
    "tests",
    "node_modules",
    "venv",
    "venv310",
    "admin-panel",
    "dist",
    "build",
    "cache",
    "temp",
    "tmp",
    "logs",
    "output",
    "scratch",
    "voice_tests",
    "Youtube Scraper",
    "AudiogramTTS-Consumer",
}

EXCLUDED_FILE_NAMES = {
    ".env",
    ".env.public",
    ".vidzeo_session_secret",
    "auth-session.json",
    "auth-session.enc.json",
    "secret-settings.json",
    "secret-settings.enc.json",
    "queue.json",
    "queue.json.bak",
    "queue.json.tmp",
    "VidzeoLocal-Pure-Source.zip",
    "VidzeoLocal-New-Package.zip",
    "temp_audio_test.wav",
}

EXCLUDED_FILE_GLOBS = {
    "*.bak",
    "*.enc.json",
    "auth-session*.json",
    "secret-settings*.json",
    "step_*_content.txt",
    "test_*.py",
    "test_*.txt",
    "tmp_*",
    "debug_*",
    "patch*",
    "auto*",
    "kaggle_*",
}

PUBLIC_ENV_KEYS = [
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "GOOGLE_CLIENT_ID",
]

EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
    ".tmp",
    ".wav",
    ".zip",
    ".whl",
    ".bak",
}

FORBIDDEN_PACKAGE_GLOBS = {
    "**/.env",
    "**/.env.*",
    "**/.vidzeo_session_secret",
    "**/auth-session*.json",
    "**/secret-settings*.json",
    "**/*.enc.json",
    "**/queue.json",
    "**/queue.json.bak",
    "**/queue.json.tmp",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/*.pyo",
    "**/.pytest_cache/**",
    "**/*.log",
    "**/*.tmp",
    "**/*.bak",
    "**/step_*_content.txt",
    "**/node_modules/**",
    "**/admin-panel/**",
    "**/output/**",
    "**/temp/**",
    "**/tmp/**",
    "**/cache/**",
    "**/logs/**",
    "**/build/**",
    "**/dist/**",
    "**/tests/**",
    "**/test/**",
    "**/test_*",
    "**/debug_*",
    "**/patch*",
    "**/auto*",
    "**/kaggle_*",
    "**/tools/python/Lib/site-packages/torch/**",
    "**/tools/python/Lib/site-packages/torch",
    "**/tools/python/Lib/site-packages/spacy/**",
    "**/tools/python/Lib/site-packages/spacy",
    "**/tools/python/Lib/site-packages/transformers/**",
    "**/tools/python/Lib/site-packages/transformers",
}

PIPELINE_PARITY_FILES = [
    "main.py",
    "scripts/bootstrap-runtime.ps1",
    "server/app.py",
    "server/requirements.txt",
    "server/services/transcription_service.py",
    "server/services/video_service.py",
    "server/services/real_media_service.py",
    "server/services/youtube_clips_service.py",
    "server/services/openrouter_llm.py",
    "server/services/motion_templates.py",
    "server/services/native_render_policy.py",
    "server/services/editorial_brain.py",
    "server/services/editing_profile.py",
    "server/services/editorial_director/contract.py",
    "server/services/editorial_director/director.py",
    "server/services/editorial_director/script_analyzer.py",
    "server/services/editorial_director/scene_grammar.py",
    "server/services/semantic_edit_engine.py",
    "scripts/render-remotion-manifest.js",
    "remotion/entry.jsx",
    "src/remotion/DocumentaryComposition.jsx",
    "src/remotion/engine/SceneRegistry.js",
    "src/remotion/components/Chart.jsx",
    "src/remotion/components/AnimatedMap.jsx",
    "src/remotion/components/Timeline.jsx",
    "server/services/native_engine/renderer.py",
    "server/services/native_engine/pro_composer.py",
    "server/services/native_engine/smart_composer.py",
    "server/services/native_engine/timeline_compiler.py",
    "server/services/native_engine/vox_composer.py",
    "server/services/native_engine/libraries/sound_design.py",
    "server/services/web_image/query.py",
    "server/services/web_image/resolver.py",
    "server/services/web_image_service.py",
]

PRODUCT_PARITY_FILES = [
    "main.js",
    *PIPELINE_PARITY_FILES,
]

NO_BLANK_FALLBACK_POLICY_FILES = [
    "server/app.py",
    "server/services/video_service.py",
    "server/services/real_media_service.py",
]

NO_BLANK_FALLBACK_FORBIDDEN_SNIPPETS = [
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
    "solid color",
]

FASTER_WHISPER_POLICY_FILES = [
    "server/services/transcription_service.py",
    "server/services/video_service.py",
]

OPENAI_WHISPER_FORBIDDEN_SNIPPETS = [
    "import whisper",
    "openai-whisper",
    "whisper.load_model",
    "OpenAI Whisper",
    "VIDZEO_PREFER_FASTER_WHISPER",
]


def is_excluded_file(path: Path, *, apply_project_name_filters: bool = True) -> bool:
    name = path.name
    lower_name = name.lower()
    if lower_name == "python310.zip":
        return False
    if name in EXCLUDED_FILE_NAMES or lower_name in {item.lower() for item in EXCLUDED_FILE_NAMES}:
        return True
    if apply_project_name_filters:
        if any(fnmatch.fnmatch(lower_name, pattern.lower()) for pattern in EXCLUDED_FILE_GLOBS):
            return True
        if lower_name.startswith(("test_", "tmp_", "debug_", "patch")):
            return True
        if lower_name.startswith("kaggle_"):
            return True
    # Generated voiceovers/test audio must not leak into a package, but the
    # curated procedural edit library is a required runtime asset.
    if path.suffix.lower() == ".wav":
        parts = {part.lower() for part in path.parts}
        return not ({"public", "sound-effects"} <= parts)
    return path.suffix.lower() in EXCLUDED_SUFFIXES


def copy_tree_clean(src: Path, dst: Path, *, apply_project_name_filters: bool = True) -> int:
    copied = 0
    for item in src.rglob("*"):
        relative = item.relative_to(src)
        if any(part in EXCLUDED_DIR_NAMES for part in relative.parts):
            continue
        if item.is_symlink():
            continue
        target = dst / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if is_excluded_file(item, apply_project_name_filters=apply_project_name_filters):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        copied += 1
    return copied


def safe_rmtree(path: Path) -> bool:
    if not path.exists():
        return True
    resolved = path.resolve()
    if DIST_DIR.resolve() not in resolved.parents:
        raise RuntimeError(f"Refusing to delete unexpected path: {resolved}")
    try:
        shutil.rmtree(path)
        return True
    except PermissionError:
        return False


def run_checked(command: list[str], cwd: Path) -> None:
    print(f"Running: {' '.join(command)}")
    executable = shutil.which(command[0]) or command[0]
    result = subprocess.run([executable, *command[1:]], cwd=str(cwd), shell=False)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}")


def zip_directory(source_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zip_file:
        for path in source_dir.rglob("*"):
            arc_name = path.relative_to(source_dir.parent)
            if path.is_dir():
                zip_file.writestr(str(arc_name).replace("\\", "/") + "/", "")
            else:
                zip_file.write(path, arc_name)


def _matches_forbidden_package_path(relative: Path) -> bool:
    rel = relative.as_posix().lower()
    name = relative.name.lower()
    if name in {".env.public", ".env.defaults", ".env.example"}:
        return False
    if lower_name_is_runtime_dir(name):
        return True
    in_site_packages = (
        "/tools/python/lib/site-packages/" in rel
        or rel.startswith("tools/python/lib/site-packages/")
        or rel.startswith("resources/tools/python/lib/site-packages/")
    )
    if in_site_packages:
        return any(
            fnmatch.fnmatch(rel, pattern.lower())
            for pattern in {
                "**/*.pyc",
                "**/*.pyo",
                "**/__pycache__/**",
                "**/.pytest_cache/**",
                "**/*.log",
                "**/*.tmp",
                "**/*.bak",
                "**/*.enc.json",
                "**/tools/python/Lib/site-packages/torch/**",
                "**/tools/python/Lib/site-packages/torch",
                "**/tools/python/Lib/site-packages/spacy/**",
                "**/tools/python/Lib/site-packages/spacy",
                "**/tools/python/Lib/site-packages/transformers/**",
                "**/tools/python/Lib/site-packages/transformers",
            }
        )
    return any(fnmatch.fnmatch(rel, pattern.lower()) for pattern in FORBIDDEN_PACKAGE_GLOBS)


def lower_name_is_runtime_dir(name: str) -> bool:
    return name in {
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
        "dist",
    }


def validate_clean_tree(root: Path, label: str) -> None:
    offenders: list[str] = []
    if not root.exists():
        raise RuntimeError(f"{label} was not created: {root}")
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if _matches_forbidden_package_path(relative):
            offenders.append(relative.as_posix())
            if len(offenders) >= 40:
                break
    if offenders:
        raise RuntimeError(
            f"{label} contains forbidden dirty/runtime files:\n"
            + "\n".join(f"  - {item}" for item in offenders)
        )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_pipeline_parity(target_root: Path, label: str, *, resources_prefix: bool = False) -> None:
    mismatches: list[str] = []
    files = PIPELINE_PARITY_FILES if resources_prefix else PRODUCT_PARITY_FILES
    for relative in files:
        source = ROOT_DIR / relative
        target = target_root / ("resources" if resources_prefix else "") / relative
        if not source.exists():
            mismatches.append(f"missing source: {relative}")
            continue
        if not target.exists():
            mismatches.append(f"missing packaged file: {relative}")
            continue
        if file_sha256(source) != file_sha256(target):
            mismatches.append(relative)
    if mismatches:
        raise RuntimeError(
            f"{label} does not match main app pipeline/runtime files:\n"
            + "\n".join(f"  - {item}" for item in mismatches)
        )


def validate_no_blank_fallback_policy(target_root: Path, label: str, *, resources_prefix: bool = False) -> None:
    offenders: list[str] = []
    base = target_root / "resources" if resources_prefix else target_root
    for relative in NO_BLANK_FALLBACK_POLICY_FILES:
        path = base / relative
        if not path.exists():
            offenders.append(f"missing policy file: {relative}")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for snippet in NO_BLANK_FALLBACK_FORBIDDEN_SNIPPETS:
            if snippet in text:
                offenders.append(f"{relative} contains {snippet!r}")
    if offenders:
        raise RuntimeError(
            f"{label} violates no-blank-fallback policy:\n"
            + "\n".join(f"  - {item}" for item in offenders)
        )


def validate_faster_whisper_policy(target_root: Path, label: str, *, resources_prefix: bool = False) -> None:
    offenders: list[str] = []
    base = target_root / "resources" if resources_prefix else target_root
    for relative in FASTER_WHISPER_POLICY_FILES:
        path = base / relative
        if not path.exists():
            offenders.append(f"missing faster-whisper policy file: {relative}")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "faster_whisper" not in text:
            offenders.append(f"{relative} does not import/use faster_whisper")
        for snippet in OPENAI_WHISPER_FORBIDDEN_SNIPPETS:
            if snippet in text:
                offenders.append(f"{relative} contains forbidden OpenAI Whisper snippet {snippet!r}")
    if offenders:
        raise RuntimeError(
            f"{label} violates faster-whisper-only policy:\n"
            + "\n".join(f"  - {item}" for item in offenders)
        )


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def write_public_env(target_dir: Path) -> None:
    source_values = read_env_file(ROOT_DIR / ".env")
    lines = [
        "# Public Vidnets Studio runtime config.",
        "# Safe to ship: contains only browser/client keys, never service-role secrets.",
    ]
    for key in PUBLIC_ENV_KEYS:
        value = source_values.get(key, "")
        if value:
            lines.append(f"{key}={value}")
    lines.append("")
    (target_dir / ".env.public").write_text("\n".join(lines), encoding="utf-8")


def ensure_windows_icon(target_dir: Path) -> None:
    png_path = target_dir / "public" / "icon.png"
    ico_path = target_dir / "public" / "icon.ico"
    if not png_path.exists():
        return
    try:
        from PIL import Image

        with Image.open(png_path) as image:
            image.save(
                ico_path,
                format="ICO",
                sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
            )
    except Exception as exc:
        raise RuntimeError(f"Could not create Windows installer icon from {png_path}: {exc}") from exc


def validate_public_env_config(target_root: Path, label: str, *, resources_prefix: bool = False) -> None:
    env_path = target_root / ("resources" if resources_prefix else "") / ".env.public"
    if not env_path.exists():
        raise RuntimeError(f"{label} is missing .env.public, so Supabase auth will not be configured.")

    values = read_env_file(env_path)
    missing = [key for key in ("SUPABASE_URL", "SUPABASE_ANON_KEY") if not values.get(key)]
    if missing:
        raise RuntimeError(
            f"{label} has incomplete public Supabase config in .env.public. Missing: "
            + ", ".join(missing)
        )

    public_env = env_path.read_text(encoding="utf-8", errors="ignore")
    if "SUPABASE_SERVICE_ROLE_KEY" in public_env or "service_role" in public_env.lower():
        raise RuntimeError(f"{label} .env.public must not contain a Supabase service-role key.")


def validate_distribution_runtime_defaults(target_root: Path, label: str, *, resources_prefix: bool = False) -> None:
    """Prevent a clean build from silently dropping production behavior."""
    base = target_root / "resources" if resources_prefix else target_root
    defaults_path = base / ".env.defaults"
    if not defaults_path.is_file():
        raise RuntimeError(f"{label} is missing .env.defaults; packaged media/editing behavior would be degraded.")
    values = read_env_file(defaults_path)
    required = {
        "VIDZEO_RENDER_BACKEND": "ffmpeg",
        "VIDZEO_SEMANTIC_EDIT_ENABLE": "1",
        "VIDZEO_SEMANTIC_RENDER_REQUIRED": "1",
        "VIDZEO_QWEN_VISION_MODEL": "qwen/qwen3.5-flash-02-23",
        "VIDZEO_VISUAL_DEFAULT_ON_WITH_KEY": "1",
        "VIDZEO_WEB_IMAGE_REUSE_CAP": "1",
        "VIDZEO_YT_PREFERRED_HEIGHT": "1080",
    }
    missing = [key for key, expected in required.items() if values.get(key) != expected]
    if missing:
        raise RuntimeError(f"{label} has incomplete production runtime defaults: " + ", ".join(missing))
    if any(key in values for key in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "SUPABASE_SERVICE_ROLE_KEY")):
        raise RuntimeError(f"{label} .env.defaults contains credentials; only non-secret tuning may ship.")
    sound_root = base / "public" / "sound-effects"
    wav_count = sum(1 for path in sound_root.rglob("*.wav") if path.is_file()) if sound_root.is_dir() else 0
    if wav_count < 20:
        raise RuntimeError(f"{label} has only {wav_count} packaged sound effects; the editing library is incomplete.")


def validate_queue_ui_policy(target_root: Path, label: str, *, resources_prefix: bool = False) -> None:
    renderer_path = target_root / ("resources" if resources_prefix else "") / "src" / "renderer.js"
    if not renderer_path.exists():
        raise RuntimeError(f"{label} is missing src/renderer.js, so queue UI validation cannot run.")

    renderer = renderer_path.read_text(encoding="utf-8", errors="ignore")
    required_snippets = [
        "queueRunObservedActiveWork",
        "queueRunNotifyOnComplete",
        "startPolling({ notifyOnComplete: true })",
        "startPolling({ notifyOnComplete: false })",
    ]
    missing = [snippet for snippet in required_snippets if snippet not in renderer]
    if missing:
        raise RuntimeError(
            f"{label} has stale queue polling UI code; repeated completion toasts may return. Missing:\n"
            + "\n".join(f"  - {item}" for item in missing)
        )


def copy_prebuilt_tools() -> int:
    copied = 0
    copied += copy_python_runtime_clean()
    for source_name, target_name in TOOL_DIRS:
        source = ROOT_DIR / source_name
        target = PRODUCT_DIR / target_name
        if source.exists():
            copied += copy_tree_clean(source, target)
    stamp = ROOT_DIR / "tools" / ".runtime-ready"
    if stamp.exists():
        target_stamp = PRODUCT_DIR / "tools" / ".runtime-ready"
        target_stamp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stamp, target_stamp)
        copied += 1
    return copied


def is_selected_site_package(path: Path) -> bool:
    name = path.name
    lower = name.lower()
    stem = path.stem.lower() if path.is_file() else lower
    for marker in (".dist-info", ".data", ".libs"):
        if marker in stem:
            stem = stem.split(marker, 1)[0]
    stem = stem.replace("-", "_")
    return any(
        stem == prefix.lower().replace("-", "_")
        or stem.startswith(prefix.lower().replace("-", "_") + "_")
        or stem.startswith(prefix.lower().replace("-", "_") + ".")
        for prefix in SITE_PACKAGE_PREFIXES
    )


def copy_python_runtime_clean() -> int:
    source = ROOT_DIR / "tools" / "python"
    target = PRODUCT_DIR / "tools" / "python"
    if not source.exists():
        return 0

    copied = 0
    site_packages = source / "Lib" / "site-packages"
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        if site_packages in item.parents or item == site_packages:
            continue
        if any(part in EXCLUDED_DIR_NAMES for part in relative.parts):
            continue
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        if is_excluded_file(item):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        copied += 1

    if site_packages.exists():
        destination_site_packages = target / "Lib" / "site-packages"
        destination_site_packages.mkdir(parents=True, exist_ok=True)
        for item in site_packages.iterdir():
            if not is_selected_site_package(item):
                continue
            destination = destination_site_packages / item.name
            if item.is_dir():
                copied += copy_tree_clean(item, destination, apply_project_name_filters=False)
            elif not is_excluded_file(item, apply_project_name_filters=False):
                shutil.copy2(item, destination)
                copied += 1

    return copied


def build_product_package_json() -> dict:
    package_json = json.loads((ROOT_DIR / "package.json").read_text(encoding="utf-8"))
    build_config = package_json.setdefault("build", {})
    win_config = build_config.setdefault("win", {})
    nsis_config = build_config.setdefault("nsis", {})
    win_config["icon"] = "public/icon.ico"
    nsis_config["installerIcon"] = "public/icon.ico"
    nsis_config["uninstallerIcon"] = "public/icon.ico"
    nsis_config["installerHeaderIcon"] = "public/icon.ico"
    package_json["scripts"] = {
        "start": "electron .",
        "build": "electron-builder --win --x64",
        "build:dir": "electron-builder --win --x64 --dir",
        "build:portable": "electron-builder --win portable --x64",
    }
    build_config["extraResources"] = [
        {"from": "resources/template-library/", "to": "resources/template-library/", "filter": ["builtin/**/*"]},
        {"from": "src/", "to": "src/", "filter": ["**/*"]},
        {"from": "remotion/", "to": "remotion/", "filter": ["**/*"]},
        {"from": "scripts/render-remotion-manifest.js", "to": "scripts/render-remotion-manifest.js"},
        {"from": "scripts/bootstrap-runtime.ps1", "to": "scripts/bootstrap-runtime.ps1"},
        {
            "from": "tools/python/",
            "to": "tools/python/",
            "filter": [
                "**/*",
                "!**/__pycache__/**",
                "!**/*.pyc",
                "!**/*.pyo",
                "!**/*.log",
                "!**/.pytest_cache/**",
                "!**/tests/**",
                "!**/test/**"
            ],
        },
        {
            "from": "tools/ffmpeg/",
            "to": "tools/ffmpeg/",
            "filter": [
                "ffmpeg.exe",
                "ffprobe.exe"
            ],
        },
        {"from": "tools/.runtime-ready", "to": "tools/.runtime-ready"},
        {
            "from": "server/",
            "to": "server/",
            "filter": [
                "**/*",
                "!**/__pycache__/**",
                "!**/*.pyc",
                "!**/*.pyo",
                "!**/*.log",
                "!**/.env",
                "!**/.env.*",
                "!**/*.enc.json",
                "!**/*.bak",
                "!**/*.tmp",
                "!**/auth-session*.json",
                "!**/secret-settings*.json",
                "!**/tests/**",
                "!**/test_*",
                "!**/debug_*",
                "!**/patch*",
                "!**/auto*",
                "!**/kaggle_*",
                "!**/.pytest_cache/**",
                "!queue.json",
                "!queue.json.bak",
                "!queue.json.tmp",
                "!cache/**",
            ],
        },
        {"from": "fonts/", "to": "fonts/", "filter": ["**/*"]},
        {
            "from": "public/",
            "to": "public/",
            "filter": [
                "map-assets/**/*",
                "font-library/**/*",
                "sound-effects/**/*",
                "template-animations/**/*",
                "icon.ico",
                "icon.png",
            ],
        },
        {"from": "main.py", "to": "main.py"},
        {"from": ".env.defaults", "to": ".env.defaults"},
        {"from": ".env.public", "to": ".env.public"},
        {"from": ".env.example", "to": ".env.example"},
        {"from": "public/icon.png", "to": "public/icon.png"},
        {"from": "public/icon.ico", "to": "public/icon.ico"},
    ]
    package_json["build"]["extraFiles"] = [
        {"from": "VIDNETS_AGENT.cmd", "to": "VIDNETS_AGENT.cmd"},
        {"from": "AGENT_ACCESS.md", "to": "AGENT_ACCESS.md"},
        {"from": "START_VIDNETS_STUDIO.bat", "to": "START_VIDNETS_STUDIO.bat"},
        {"from": "README_PRODUCT_APP.txt", "to": "README_PRODUCT_APP.txt"},
    ]
    package_json["build"]["files"] = [
        "main.js",
        "preload.js",
        "src/**/*",
        "remotion/**/*",
        "scripts/render-remotion-manifest.js",
        "scripts/bootstrap-runtime.ps1",
        ".env.defaults",
        ".env.public",
        "!.env",
        "!.env.local",
        "!.env.development",
        "!.env.production",
        "!.env.test",
        "!*.enc.json",
        "!*.bak",
        "!*.tmp",
        "!auth-session*.json",
        "!secret-settings*.json",
        "!step_*_content.txt",
        "!node_modules/**",
        "!admin-panel/**",
        "!output/**",
        "!temp/**",
        "!cache/**",
        "!logs/**",
        "!tests/**",
        "!test_*",
        "!tmp_*",
        "!debug_*",
        "!patch*",
        "!auto*",
        "!kaggle_*",
        "!dist/**",
        "!build/**",
    ]
    return package_json


def write_product_scripts() -> None:
    scripts_dir = PRODUCT_DIR / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT_DIR / "scripts" / "bootstrap-runtime.ps1", scripts_dir / "bootstrap-runtime.ps1")

    (PRODUCT_DIR / "INSTALL_RUNTIME.bat").write_text(
        """@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
cd /d "%~dp0"
set "APP_DIR=%CD%"
set PYTHONIOENCODING=utf-8
if not exist "%APP_DIR%\\scripts\\bootstrap-runtime.ps1" (
    echo Missing runtime installer: %APP_DIR%\\scripts\\bootstrap-runtime.ps1
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%APP_DIR%\\scripts\\bootstrap-runtime.ps1" -InstallRoot "%APP_DIR%" -SourceRoot "%APP_DIR%"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Runtime installation failed. Check your internet connection and Python installation.
    pause
    exit /b 1
)
echo.
echo Vidnets runtime is ready.
""",
        encoding="utf-8",
    )

    (PRODUCT_DIR / "START_VIDNETS_STUDIO.bat").write_text(
        """@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
cd /d "%~dp0"
set "APP_DIR=%CD%"
set PYTHONIOENCODING=utf-8
call "%APP_DIR%\\INSTALL_RUNTIME.bat"
if %ERRORLEVEL% NEQ 0 exit /b 1
where node >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Node.js was not found. Install Node.js LTS, then run this again.
    pause
    exit /b 1
)
if not exist "%APP_DIR%\\node_modules\\electron" (
    echo Installing desktop shell dependencies...
    call npm install --no-audit --no-fund
    if %ERRORLEVEL% NEQ 0 (
        echo npm install failed. Install Node.js LTS, then run this again.
        pause
        exit /b 1
    )
)
call npm start
""",
        encoding="utf-8",
    )

    (PRODUCT_DIR / "BUILD_PRODUCT_EXE.bat").write_text(
        """@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
cd /d "%~dp0"
set "APP_DIR=%CD%"
where node >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Node.js was not found. Install Node.js LTS before building the EXE.
    pause
    exit /b 1
)
if not exist "%APP_DIR%\\node_modules\\electron-builder" call npm install
if %ERRORLEVEL% NEQ 0 exit /b 1
call npm run build
pause
""",
        encoding="utf-8",
    )

    (PRODUCT_DIR / "README_PRODUCT.txt").write_text(
        """Vidnets Studio - Consumer Product Folder

This folder is intentionally clean and lightweight.

Included:
- Electron app source
- Flask backend source
- UI files
- fonts
- prebuilt Python runtime and normal Python dependencies
- prebuilt FFmpeg
- .env.public with Supabase/Google public client config only
- first-run runtime validator
- build scripts for the later EXE package

Not included:
- .env or service-role secrets
- admin panel
- node_modules
- Faster Whisper model cache
- logs, cache, temp files, generated videos, tests, debug scripts

First source-folder test:
1. Install Node.js LTS.
2. Run START_VIDNETS_STUDIO.bat.
3. Vidnets uses the bundled Python + FFmpeg runtime.
4. Faster Whisper/model assets may download from the web on first transcription.

Later EXE packaging:
Run BUILD_PRODUCT_EXE.bat from this folder.
The packaged app will also install runtime dependencies into its writable app data folder on first run.

Production security:
Do not ship a Supabase service-role key to consumers. Keep service-role/admin actions on a trusted server.
""",
        encoding="utf-8",
    )
    (PRODUCT_DIR / "README_PRODUCT_APP.txt").write_text(
        """Vidnets Studio - Installed App

Run:
- Vidnets Studio v1.exe
- or START_VIDNETS_STUDIO.bat


Included:
- Packaged Electron app
- Vidzeo UI and Flask backend resources
- Prebuilt Python runtime and selected Python dependencies
- Prebuilt FFmpeg
- Public Supabase/Google client config only

Not included:
- .env secrets
- Supabase service-role key
- admin panel
- generated videos/logs/cache

First transcription may download Faster Whisper model assets from the web.
""",
        encoding="utf-8",
    )


def create_product_folder(source_only: bool = False, skip_source_zip: bool = False) -> None:
    global PRODUCT_DIR, PRODUCT_ZIP

    if PRODUCT_DIR.exists():
        if not safe_rmtree(PRODUCT_DIR):
            PRODUCT_DIR = DIST_DIR / "Vidnets-Studio-Product-Ready"
            PRODUCT_ZIP = DIST_DIR / "Vidnets-Studio-Product-Ready.zip"
            if not safe_rmtree(PRODUCT_DIR):
                raise RuntimeError(f"Close any windows using {PRODUCT_DIR}, then run again.")

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    PRODUCT_DIR.mkdir(parents=True, exist_ok=True)

    copied = 0
    for file_name in ROOT_FILES:
        src = ROOT_DIR / file_name
        if src.exists():
            shutil.copy2(src, PRODUCT_DIR / file_name)
            copied += 1

    for dir_name in ROOT_DIRS:
        src = ROOT_DIR / dir_name
        if src.exists():
            copied += copy_tree_clean(src, PRODUCT_DIR / dir_name)
    copied += copy_prebuilt_tools()
    write_public_env(PRODUCT_DIR)
    ensure_windows_icon(PRODUCT_DIR)

    (PRODUCT_DIR / "package.json").write_text(
        json.dumps(build_product_package_json(), indent=4) + "\n",
        encoding="utf-8",
    )
    write_product_scripts()

    for scaffold in ["tools"]:
        (PRODUCT_DIR / scaffold).mkdir(exist_ok=True)

    size_mb = sum(path.stat().st_size for path in PRODUCT_DIR.rglob("*") if path.is_file()) / (1024 * 1024)
    validate_clean_tree(PRODUCT_DIR, "Product source folder")
    validate_pipeline_parity(PRODUCT_DIR, "Product source folder")
    validate_no_blank_fallback_policy(PRODUCT_DIR, "Product source folder")
    validate_faster_whisper_policy(PRODUCT_DIR, "Product source folder")
    validate_public_env_config(PRODUCT_DIR, "Product source folder")
    validate_distribution_runtime_defaults(PRODUCT_DIR, "Product source folder")
    validate_queue_ui_policy(PRODUCT_DIR, "Product source folder")
    should_zip_source = source_only and not skip_source_zip
    if should_zip_source:
        zip_directory(PRODUCT_DIR, PRODUCT_ZIP)
        zip_mb = PRODUCT_ZIP.stat().st_size / (1024 * 1024)
    else:
        zip_mb = 0.0
    print("============================================================")
    print("Vidnets Studio product folder created")
    print(f"Folder: {PRODUCT_DIR}")
    if should_zip_source:
        print(f"Zip: {PRODUCT_ZIP}")
    print(f"Files copied: {copied}")
    print(f"Size: {size_mb:.2f} MB")
    if should_zip_source:
        print(f"Zip size: {zip_mb:.2f} MB")
    else:
        print("Zip: skipped for source-only build" if source_only else "Zip: deferred until after Electron app build")
    print("Included: prebuilt tools/python and tools/ffmpeg")
    print("Excluded: admin-panel, node_modules, .env, output, temp, cache, logs")
    print("============================================================")

    if source_only:
        return

    create_distributable_app_folder()


def create_distributable_app_folder() -> None:
    global APP_DIR, APP_ZIP

    print("============================================================")
    print("Building consumer app folder with Electron...")
    print("============================================================")

    run_checked(["npm", "install", "--no-audit", "--no-fund"], PRODUCT_DIR)
    run_checked(["npm", "run", "build:dir"], PRODUCT_DIR)

    unpacked_dir = PRODUCT_DIR / "dist" / "win-unpacked"
    if not unpacked_dir.exists():
        raise RuntimeError(f"Electron build output not found: {unpacked_dir}")

    if APP_DIR.exists() and not safe_rmtree(APP_DIR):
        APP_DIR = DIST_DIR / "Vidnets-Studio-Product-App-Ready"
        APP_ZIP = DIST_DIR / "Vidnets-Studio-Product-App-Ready.zip"
        if not safe_rmtree(APP_DIR):
            raise RuntimeError(f"Close any windows using {APP_DIR}, then run again.")

    shutil.copytree(unpacked_dir, APP_DIR)
    write_app_folder_helpers()
    validate_consumer_app_folder()
    cleanup_consumer_app_runtime_artifacts()
    cleanup_product_build_artifacts()
    validate_clean_tree(APP_DIR, "Consumer app folder")
    validate_clean_tree(PRODUCT_DIR, "Product source folder")
    validate_pipeline_parity(APP_DIR, "Consumer app folder", resources_prefix=True)
    validate_pipeline_parity(PRODUCT_DIR, "Product source folder")
    validate_no_blank_fallback_policy(APP_DIR, "Consumer app folder", resources_prefix=True)
    validate_no_blank_fallback_policy(PRODUCT_DIR, "Product source folder")
    validate_faster_whisper_policy(APP_DIR, "Consumer app folder", resources_prefix=True)
    validate_faster_whisper_policy(PRODUCT_DIR, "Product source folder")
    validate_public_env_config(APP_DIR, "Consumer app folder", resources_prefix=True)
    validate_distribution_runtime_defaults(APP_DIR, "Consumer app folder", resources_prefix=True)
    validate_public_env_config(PRODUCT_DIR, "Product source folder")
    validate_distribution_runtime_defaults(PRODUCT_DIR, "Product source folder")
    validate_queue_ui_policy(APP_DIR, "Consumer app folder", resources_prefix=True)
    validate_queue_ui_policy(PRODUCT_DIR, "Product source folder")
    zip_directory(APP_DIR, APP_ZIP)
    zip_directory(PRODUCT_DIR, PRODUCT_ZIP)

    app_size_mb = sum(path.stat().st_size for path in APP_DIR.rglob("*") if path.is_file()) / (1024 * 1024)
    app_zip_mb = APP_ZIP.stat().st_size / (1024 * 1024)
    print("============================================================")
    print("Consumer app folder ready")
    print(f"Folder: {APP_DIR}")
    print(f"Zip: {APP_ZIP}")
    print(f"Size: {app_size_mb:.2f} MB")
    print(f"Zip size: {app_zip_mb:.2f} MB")
    print("Run: START_VIDNETS_STUDIO.bat or Vidnets Studio v1.exe")
    print("============================================================")


def cleanup_product_build_artifacts() -> None:
    for relative in ["node_modules", "dist"]:
        target = PRODUCT_DIR / relative
        if target.exists():
            if not safe_rmtree(target):
                raise RuntimeError(f"Close any windows using {target}, then run again.")


def cleanup_consumer_app_runtime_artifacts() -> None:
    runtime_dirs = [
        APP_DIR / "resources" / "cache",
        APP_DIR / "resources" / "logs",
        APP_DIR / "resources" / "output",
        APP_DIR / "resources" / "temp",
        APP_DIR / "resources" / "tmp",
        APP_DIR / "cache",
        APP_DIR / "logs",
        APP_DIR / "output",
        APP_DIR / "temp",
        APP_DIR / "tmp",
    ]
    for target in runtime_dirs:
        if target.exists() and not safe_rmtree(target):
            raise RuntimeError(f"Close any windows using {target}, then run again.")

    for pycache in list(APP_DIR.rglob("__pycache__")):
        if pycache.exists() and not safe_rmtree(pycache):
            raise RuntimeError(f"Close any windows using {pycache}, then run again.")

    for secret_file in list(APP_DIR.rglob(".vidzeo_session_secret")):
        try:
            secret_file.unlink()
        except FileNotFoundError:
            pass


def validate_consumer_app_folder() -> None:
    exe_name = "Vidnets Studio v1.exe" if (APP_DIR / "Vidnets Studio v1.exe").exists() else "Vidnets Studio.exe"
    required = [
        exe_name,
        "resources/app.asar",

        "resources/main.py",
        "resources/src/index.html",
        "resources/src/renderer.js",
        "resources/server/app.py",
        "resources/server/services/auth_service.py",
        "resources/server/services/transcription_service.py",
        "resources/server/services/video_service.py",
        "resources/server/services/real_media_service.py",
        "resources/server/services/youtube_clips_service.py",
        "resources/server/services/web_image/query.py",
        "resources/server/services/web_image/resolver.py",
        "resources/server/requirements.txt",
        "resources/scripts/bootstrap-runtime.ps1",
        "resources/tools/python/python.exe",
        "resources/tools/python/Lib/site-packages/faster_whisper",
        "resources/tools/python/Lib/site-packages/onnxruntime",
        "resources/tools/python/Lib/site-packages/cv2",
        "resources/tools/python/Lib/site-packages/rapidocr_onnxruntime",
        "resources/tools/python/Lib/site-packages/pyclipper",
        "resources/tools/python/Lib/site-packages/shapely",
        "resources/tools/python/Lib/site-packages/pip",
        "resources/tools/ffmpeg/ffmpeg.exe",
        "resources/tools/ffmpeg/ffprobe.exe",
        "resources/tools/.runtime-ready",
        "resources/fonts/Montserrat-Bold.ttf",
        "resources/public/font-library/Montserrat-Bold.ttf",
        "resources/public/sound-effects/vidzeo/impact.wav",
        "resources/public/template-animations",
        "resources/.env.public",
        "resources/.env.defaults",
        "START_VIDNETS_STUDIO.bat",
    ]
    forbidden = [
        "resources/.env",
        "resources/admin-panel",
        "resources/server/queue.json",
        "resources/tools/python/Lib/site-packages/torch",
        "resources/tools/python/Lib/site-packages/spacy",
        "resources/tools/python/Lib/site-packages/transformers",
    ]

    missing = [item for item in required if not (APP_DIR / item).exists()]
    if missing:
        raise RuntimeError("Consumer app folder is incomplete. Missing: " + ", ".join(missing))

    present = [item for item in forbidden if (APP_DIR / item).exists()]
    if present:
        raise RuntimeError("Consumer app folder contains forbidden dev/secret files: " + ", ".join(present))

    validate_clean_tree(APP_DIR, "Consumer app folder")

    validate_public_env_config(APP_DIR, "Consumer app folder", resources_prefix=True)
    validate_distribution_runtime_defaults(APP_DIR, "Consumer app folder", resources_prefix=True)
    validate_queue_ui_policy(APP_DIR, "Consumer app folder", resources_prefix=True)
    python_exe = APP_DIR / "resources" / "tools" / "python" / "python.exe"

    auth_config_check = subprocess.run(
        [
            str(python_exe),
            "-c",
            (
                "import os, sys, importlib.util; sys.path.insert(0, os.getcwd()); "
                "spec=importlib.util.spec_from_file_location('main', os.path.join(os.getcwd(), 'main.py')); "
                "main=importlib.util.module_from_spec(spec); spec.loader.exec_module(main); main.load_local_env(); "
                "from server.services.auth_service import auth_is_configured; "
                "raise SystemExit(0 if auth_is_configured() else 1)"
            ),
        ],
        cwd=str(APP_DIR / "resources"),
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "VIDZEO_SESSION_SECRET_FILE": str(APP_DIR / "resources" / "temp" / "validation-session-secret.txt"),
        },
    )
    if auth_config_check.returncode != 0:
        raise RuntimeError(
            "Packaged backend does not see Supabase public config from .env.public:\n"
            + (auth_config_check.stderr or auth_config_check.stdout or "auth_is_configured returned false")
        )

    media_contract_check_code = r"""
import os
import sys
sys.path.insert(0, os.getcwd())
import server.app as app
from services.pipeline_contracts import MediaContractError

def no_repair(**kwargs):
    return None

app._fill_unresolved_with_general_web_fallback = no_repair
app._run_youtube_render_repair = no_repair
try:
    app._repair_media_plan_for_render(
        media_plan={'media_paths': [None], 'source_labels': [''], 'prompt_bundle': {'beat_texts': ['packaged scene']}},
        audio_plan={'scene_texts': ['packaged scene']},
        job_config={'image_source': 'Mixed Media'},
        render_resolution=(1280, 720),
        title='Packaged Media Contract QA',
        script='Packaged Media Contract QA scene',
        target_scene_count=1,
    )
except MediaContractError as exc:
    raise SystemExit(0 if exc.detail.get('fallback_policy') == 'no_blank_or_text_card_fallbacks' else 2)
except NameError as exc:
    print(exc)
    raise SystemExit(3)
raise SystemExit(4)
"""
    media_contract_check = subprocess.run(
        [str(python_exe), "-c", media_contract_check_code],
        cwd=str(APP_DIR / "resources"),
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "VIDZEO_SESSION_SECRET_FILE": str(APP_DIR / "resources" / "temp" / "validation-session-secret.txt"),
        },
    )
    if media_contract_check.returncode != 0:
        raise RuntimeError(
            "Packaged media contract validation failed; no-blank render QA may crash instead of returning MediaContractError:\n"
            + (media_contract_check.stderr or media_contract_check.stdout or f"exit code {media_contract_check.returncode}")
        )
    cleanup_consumer_app_runtime_artifacts()

    import_check = subprocess.run(
        [
            str(python_exe),
            "-c",
            "import flask, requests, numpy, scipy, PIL, soundfile, _cffi_backend, edge_tts, ddgs, gallery_dl, psutil, faster_whisper, onnxruntime, cv2, rapidocr_onnxruntime, pyclipper, shapely; print('ok')",
        ],
        cwd=str(APP_DIR / "resources"),
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if import_check.returncode != 0:
        raise RuntimeError(
            "Bundled Python runtime is incomplete:\n"
            + (import_check.stderr or import_check.stdout or "unknown import error")
        )

    bootstrap_script = APP_DIR / "resources" / "scripts" / "bootstrap-runtime.ps1"
    bootstrap_check = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(bootstrap_script),
            "-InstallRoot",
            str(APP_DIR / "resources"),
            "-SourceRoot",
            str(APP_DIR / "resources"),
            "-ValidateOnly",
        ],
        cwd=str(APP_DIR / "resources"),
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if bootstrap_check.returncode != 0:
        raise RuntimeError(
            "Bundled runtime bootstrap validation failed:\n"
            + (bootstrap_check.stderr or bootstrap_check.stdout or "unknown bootstrap error")
        )


def write_app_folder_helpers() -> None:
    (APP_DIR / "START_VIDNETS_STUDIO.bat").write_text(
        """@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
start "" "%~dp0Vidnets Studio v1.exe"
""",
        encoding="utf-8",
    )

    (APP_DIR / "README_PRODUCT_APP.txt").write_text(
        """Vidnets Studio - Consumer App Folder

This is the folder to ship/test with users.

Run:
- Vidnets Studio v1.exe
- or START_VIDNETS_STUDIO.bat


Included:
- Packaged Electron app
- Vidzeo UI and Flask backend resources
- Prebuilt Python runtime and selected Python dependencies
- Prebuilt FFmpeg
- Public Supabase/Google client config only

Not included:
- .env secrets
- Supabase service-role key
- admin panel
- generated videos/logs/cache
- Faster Whisper model cache

First transcription may download Faster Whisper model assets from the web.
""",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Create clean Vidzeo product folders.")
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="Create and validate only dist/Vidnets-Studio-Product; skip Electron app-folder build.",
    )
    parser.add_argument(
        "--skip-source-zip",
        action="store_true",
        help="With --source-only, validate the clean source folder without creating dist/Vidnets-Studio-Product.zip.",
    )
    args = parser.parse_args()
    create_product_folder(source_only=args.source_only, skip_source_zip=args.skip_source_zip)


if __name__ == "__main__":
    main()
