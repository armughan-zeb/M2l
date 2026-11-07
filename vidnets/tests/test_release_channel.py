import hashlib
import json
import re
from pathlib import Path

import pytest


APP_ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = APP_ROOT.parent.parent / "Vidnets Releases"


def _manifest():
    path = RELEASE_ROOT / "latest.json"
    if not path.exists():
        pytest.skip("local release checkout is not present")
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_release_checkout_is_linked_to_expected_github_repo():
    git_config = (RELEASE_ROOT / ".git" / "config").read_text(encoding="utf-8")
    assert "armughan-zeb/Vidnets-Studio-Releases.git" in git_config


def test_latest_manifest_is_safe_and_matches_staged_artifact():
    path, manifest = _manifest()
    assert manifest["schemaVersion"] == 1
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["platform"] == "win32"
    assert manifest["arch"] == "x64"
    artifact = manifest["artifact"]
    assert re.fullmatch(r"Vidzeo-\d+\.\d+\.\d+-Setup-x64\.exe", artifact["name"])
    assert artifact["url"].startswith("https://github.com/armughan-zeb/Vidnets-Studio-Releases/releases/download/")
    assert re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"])
    assert isinstance(artifact["size"], int) and artifact["size"] >= 10 * 1024 * 1024
    assert not re.search(r"API_KEY|SERVICE_ROLE|PASSWORD|TOKEN", path.read_text(encoding="utf-8"), re.I)
    staged = RELEASE_ROOT / "artifacts" / artifact["name"]
    if staged.exists():
        assert staged.stat().st_size == artifact["size"]
        with staged.open('rb') as stream:
            hasher = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                hasher.update(block)
            digest = hasher.hexdigest()
        assert digest == artifact["sha256"]


def test_updater_surface_is_packaged_and_wired():
    main = (APP_ROOT / "main.js").read_text(encoding="utf-8")
    preload = (APP_ROOT / "preload.js").read_text(encoding="utf-8")
    renderer = (APP_ROOT / "src" / "renderer.js").read_text(encoding="utf-8")
    html = (APP_ROOT / "src" / "index.html").read_text(encoding="utf-8")
    assert "app-update-check" in main and "sha256File" in main
    assert "checkForAppUpdate" in preload and "installAppUpdate" in preload
    assert "checkForAppUpdateFromUi" in renderer
    assert "checkAppUpdateBtn" in html and "installAppUpdateBtn" in html
