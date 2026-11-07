"""Create a clean, secret-checked GitHub source repository for Vidzeo."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT.parent / "Vidzeo-App-Production"
MARKER = ".vidzeo-production-export"

ROOT_FILES = {
    ".dockerignore",
    ".editorconfig",
    ".env.example",
    ".gitattributes",
    ".gitignore",
    "BUILD.bat",
    "Dockerfile.backend",
    "Dockerfile.backend.dockerignore",
    "Dockerfile.package",
    "INSTALL.bat",
    "README.md",
    "SECURITY.md",
    "START.bat",
    "SUPABASE_SETUP.md",
    "Vidnets Studio.vbs",
    "docker-compose.backend.yml",
    "docker-compose.package.yml",
    "gallery-dl.conf",
    "main.js",
    "main.py",
    "package-lock.json",
    "package.json",
    "preload.js",
}

ROOT_DIRS = {
    ".github",
    "docs",
    "fonts",
    "public",
    "server",
    "src",
    "supabase",
    "tests",
}

SCRIPT_FILES = {
    "audit-utils.js",
    "bootstrap-runtime.ps1",
    "create-github-source.py",
    "docker-production-check.ps1",
    "package-scan.js",
    "security-scan.js",
}

EXCLUDED_DIRS = {
    ".git", ".vscode", ".idea", ".commandcode", ".opencode", ".pytest_cache", ".temp",
    ".mypy_cache", ".ruff_cache", "__pycache__", "admin-panel", "build", "cache",
    "dist", "dist-build-cache", "logs", "node_modules", "output", "scratch", "temp",
    "temp_test", "tmp", "tools", "venv", "venv310", ".venv", "voice_tests",
}

EXCLUDED_NAMES = {
    ".env", ".env.public", ".vidzeo_session_secret", "auth-session.enc.json",
    "memory_bank.json", "queue.json", "queue.json.bak", "queue.json.tmp",
    "secret-settings.json", "secret-settings.enc.json", "START_ADMIN_PANEL.bat",
}

EXCLUDED_SUFFIXES = {
    ".bak", ".db", ".enc.json", ".exe", ".log", ".mp3", ".mp4", ".msi",
    ".mov", ".mkv", ".p12", ".pem", ".pfx", ".pyc", ".pyo", ".sqlite",
    ".sqlite3", ".tmp", ".wav", ".webm", ".zip",
}

SECRET_PATTERNS = {
    "JWT": re.compile(r"eyJhbGci[A-Za-z0-9._-]{40,}"),
    "OpenAI-style key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "Groq key": re.compile(r"gsk_[A-Za-z0-9_-]{20,}"),
    "Google API key": re.compile(r"AIza[A-Za-z0-9_-]{25,}"),
    "GitHub token": re.compile(r"gh[oprsu]_[A-Za-z0-9_]{20,}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"),
}

TEXT_SUFFIXES = {
    "", ".bat", ".css", ".env", ".html", ".ini", ".js", ".json", ".md",
    ".ps1", ".py", ".sql", ".toml", ".txt", ".yaml", ".yml",
}


def is_excluded(path: Path, source_root: Path) -> bool:
    relative = path.relative_to(source_root)
    if any(part.lower() in {name.lower() for name in EXCLUDED_DIRS} for part in relative.parts):
        return True
    name = path.name.lower()
    if name in {item.lower() for item in EXCLUDED_NAMES}:
        return True
    if any(name.endswith(suffix.lower()) for suffix in EXCLUDED_SUFFIXES):
        return True
    if name.startswith(("debug_", "patch_", "tmp_")):
        return True
    return False


def copy_tree(source: Path, destination: Path) -> int:
    copied = 0
    for path in source.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"Refusing to export symbolic link: {path}")
        if is_excluded(path, source):
            continue
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1
    return copied


def load_local_secrets() -> dict[str, str]:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if len(value) < 12:
            continue
        if re.search(r"(?:KEY|SECRET|TOKEN|PASSWORD|SERVICE_ROLE)", key, flags=re.IGNORECASE):
            values[key] = value
    return values


def sanitize_package_json(output: Path) -> None:
    package_path = output / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    scripts = package.get("scripts") or {}
    scripts.pop("audit:admin", None)
    package["scripts"] = scripts

    build = package.get("build") or {}
    resources = build.get("extraResources") or []
    build["extraResources"] = [
        item for item in resources
        if "admin-panel" not in json.dumps(item).lower()
    ]
    files = build.get("files") or []
    build["files"] = [item for item in files if "admin-panel" not in str(item).lower()]
    package["build"] = build
    package_path.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")


def validate_export(output: Path) -> None:
    forbidden = []
    findings = []
    local_secrets = load_local_secrets()
    allowed_pattern_files = {"SECURITY.md", "scripts/security-scan.js", "scripts/create-github-source.py"}

    for path in output.rglob("*"):
        relative = path.relative_to(output).as_posix()
        lower = relative.lower()
        if path.is_symlink():
            forbidden.append(f"{relative} (symbolic link)")
            continue
        if any(part in {name.lower() for name in EXCLUDED_DIRS} for part in Path(lower).parts):
            forbidden.append(relative)
            continue
        if path.name.lower() in {item.lower() for item in EXCLUDED_NAMES}:
            forbidden.append(relative)
            continue
        if "admin-panel" in lower or lower == "scripts/admin-api-smoke.js":
            forbidden.append(relative)
            continue
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if relative not in allowed_pattern_files:
            for label, pattern in SECRET_PATTERNS.items():
                if pattern.search(content):
                    findings.append(f"{relative}: {label}")
        for key, value in local_secrets.items():
            if value and value in content:
                findings.append(f"{relative}: exact local value for {key}")

    required = [
        "package.json", ".gitignore", ".env.example", "server/app.py",
        "server/requirements.txt", "src/index.html", "tests/test_pipeline_api_contracts.py",
        "supabase/migrations", "scripts/security-scan.js",
    ]
    missing = [item for item in required if not (output / item).exists()]
    if missing:
        raise RuntimeError("Production export is incomplete: " + ", ".join(missing))
    if forbidden:
        raise RuntimeError("Forbidden paths in production export:\n" + "\n".join(forbidden[:50]))
    if findings:
        raise RuntimeError("Potential credentials in production export:\n" + "\n".join(findings[:50]))


def write_export_readme(output: Path) -> None:
    (output / "PRODUCTION_EXPORT.md").write_text(
        """# Vidzeo Production Source

This directory is an allowlisted source export created for a fresh GitHub repository.

## Excluded by design

- `.env`, API keys, auth sessions, encrypted settings, and queue/user state
- `admin-panel/` and local service-role operator scripts
- bundled Python/FFmpeg runtimes, virtual environments, and `node_modules`
- generated media, logs, caches, test scratch data, and build outputs

## Before deployment

1. Configure secrets in the deployment platform, never in Git.
2. Run `npm ci`, `npm run security:scan`, and the Python tests.
3. Keep Supabase service-role operations on trusted infrastructure only.
4. Enable GitHub secret scanning, Dependabot, and branch protection.
""",
        encoding="utf-8",
    )


def initialize_git(output: Path) -> None:
    try:
        subprocess.run(["git", "init", "-b", "main"], cwd=output, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        subprocess.run(["git", "init"], cwd=output, check=True, capture_output=True, text=True)
        subprocess.run(["git", "branch", "-M", "main"], cwd=output, check=True, capture_output=True, text=True)


def prepare_output(output: Path) -> None:
    output = output.resolve()
    if output == ROOT or ROOT in output.parents:
        raise RuntimeError("Production export must be outside the development checkout.")
    if output.exists():
        marker = output / MARKER
        if not marker.exists():
            raise RuntimeError(f"Refusing to replace unmarked directory: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    (output / MARKER).write_text("Generated by scripts/create-github-source.py\n", encoding="ascii")


def create_export(output: Path, initialize_repository: bool = True) -> None:
    prepare_output(output)
    copied = 0
    for name in sorted(ROOT_FILES):
        source = ROOT / name
        if source.is_file():
            shutil.copy2(source, output / name)
            copied += 1
    for name in sorted(ROOT_DIRS):
        source = ROOT / name
        if source.is_dir():
            copied += copy_tree(source, output / name)

    scripts_output = output / "scripts"
    scripts_output.mkdir(exist_ok=True)
    for name in sorted(SCRIPT_FILES):
        source = ROOT / "scripts" / name
        if source.is_file():
            shutil.copy2(source, scripts_output / name)
            copied += 1

    sanitize_package_json(output)
    write_export_readme(output)
    validate_export(output)
    if initialize_repository:
        initialize_git(output)

    print(f"Production source created: {output}")
    print(f"Files copied: {copied}")
    print("Security validation: passed")
    print(f"Git repository: {'initialized on main' if initialize_repository else 'not initialized'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a GitHub-ready Vidzeo production source directory.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-git", action="store_true", help="Do not initialize a fresh Git repository.")
    args = parser.parse_args()
    create_export(args.output, initialize_repository=not args.no_git)


if __name__ == "__main__":
    main()
