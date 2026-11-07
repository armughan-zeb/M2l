"""Bounded local portrait matting for Remotion person-profile scenes.

The model runs in a child process so an unavailable model download or an
out-of-memory inference can be killed without taking the video renderer down.
Only script-directed PersonProfile scenes are processed; other scene media is
left untouched.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


LOGGER = logging.getLogger(__name__)
DEFAULT_MODEL = "u2net_human_seg"
ALLOWED_MODELS = {"u2net_human_seg", "u2netp", "isnet-general-use", "birefnet-portrait"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wmv"}


def _local_source(value: Any, base_dir: Path) -> Path | None:
    raw = str(value or "").strip()
    if not raw or "://" in raw or raw.startswith("data:"):
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = base_dir / path
    try:
        path = path.resolve()
        if path.suffix.lower() in VIDEO_SUFFIXES or not path.is_file() or path.stat().st_size > 40_000_000:
            return None
    except OSError:
        return None
    return path


def _profile_source(scene: Mapping[str, Any], base_dir: Path) -> Path | None:
    primary = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), Mapping) else {}
    asset = primary.get("asset") if isinstance(primary.get("asset"), Mapping) else {}
    media = scene.get("media") if isinstance(scene.get("media"), Mapping) else {}
    candidates = [
        asset.get("src"), asset.get("path"),
        media.get("src"), media.get("path"),
        (scene.get("background") or {}).get("src") if isinstance(scene.get("background"), Mapping) else None,
        (scene.get("background") or {}).get("path") if isinstance(scene.get("background"), Mapping) else None,
    ]
    for shot in media.get("shots") or []:
        if isinstance(shot, Mapping):
            candidates.extend((shot.get("src"), shot.get("path"), shot.get("asset_path")))
    for candidate in candidates:
        path = _local_source(candidate, base_dir)
        if path:
            return path
    return None


def _model_cache_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    root = Path(local) if local else Path.home() / ".local" / "share"
    return root / "Vidzeo" / "VidzeoData" / "models" / "rembg"


def _model_name() -> str:
    requested = str(os.environ.get("VIDZEO_PERSON_CUTOUT_MODEL") or DEFAULT_MODEL).strip()
    return requested if requested in ALLOWED_MODELS else DEFAULT_MODEL


def _worker_remove(items: list[dict[str, str]], default_model: str) -> dict[str, str]:
    """Run in the isolated worker after its parent enforces the wall timeout."""
    from PIL import Image
    from rembg import new_session, remove

    sessions: dict[str, Any] = {}
    outputs: dict[str, str] = {}
    for item in items:
        source = Path(item["source"])
        target = Path(item["target"])
        item_model = str(item.get("model") or default_model).strip()
        if item_model not in sessions:
            sessions[item_model] = new_session(item_model)
        session = sessions[item_model]
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as opened:
                foreground = remove(opened.convert("RGB"), session=session, post_process_mask=True)
            if foreground.mode != "RGBA" or foreground.getchannel("A").getextrema() == (255, 255):
                LOGGER.warning("Person cutout model returned no usable alpha mask for %s", source.name)
                continue
            fd, temporary_name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".png", dir=target.parent)
            os.close(fd)
            temporary = Path(temporary_name)
            try:
                foreground.save(temporary, format="PNG", optimize=True)
                if temporary.stat().st_size < 64:
                    continue
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            outputs[str(source)] = str(target)
        except Exception as exc:
            LOGGER.warning("Portrait cutout failed for %s: %s", source.name, exc)
    return outputs


def _worker_entry(manifest_path: Path) -> int:
    try:
        request = json.loads(manifest_path.read_text(encoding="utf-8"))
        result = _worker_remove(list(request.get("items") or []), str(request.get("model") or DEFAULT_MODEL))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


def hydrate_person_cutouts(
    manifest: Mapping[str, Any],
    output_dir: Path,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Attach generated local alpha PNGs to selected portrait scenes.

    The first request may need to fetch model weights. That work has a strict
    timeout and is isolated from Remotion; timeout or inference failure keeps
    the verified original photo as the scene fallback.
    """
    result = dict(manifest)
    base_dir = Path(output_dir).resolve()
    scenes = [dict(scene) if isinstance(scene, Mapping) else scene for scene in (manifest.get("scenes") or [])]
    result["scenes"] = scenes
    candidates: dict[str, Path] = {}
    scene_sources: dict[int, str] = {}
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        primary = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), Mapping) else {}
        primary_type = str(primary.get("type") or "")
        media_type = str((scene.get("media") or {}).get("type") or "")
        eligible = primary_type in {"PersonProfile", "SmartImage", "DocumentaryStill", "HistoricalStill", "image"} or media_type == "image"
        if not eligible:
            continue
        props = primary.get("props") if isinstance(primary.get("props"), Mapping) else {}
        # A verified group frame belongs intact on screen. Single-person
        # matting can discard members and needlessly download a cold model.
        if primary_type == "PersonProfile":
            people_contract = props.get('people_copy_contract') or {}
            if people_contract.get('approved') is True and len(props.get('people') or []) > 1:
                continue
        asset = primary.get("asset") if isinstance(primary.get("asset"), Mapping) else {}
        if props.get("cutout_src") or props.get("foreground_cutout") or asset.get("cutout_src") or asset.get("foreground_cutout"):
            continue
        source = _profile_source(scene, base_dir)
        if not source:
            continue
        model_for_scene = _model_name() if primary_type == "PersonProfile" else ("isnet-general-use" if "isnet-general-use" in ALLOWED_MODELS else _model_name())
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:24]
        target = base_dir / "media" / "person-cutouts" / f"{digest}-{model_for_scene}.png"
        if target.is_file() and target.stat().st_size >= 64:
            scene_sources[index] = str(target)
        else:
            candidates[str(source)] = {"target": target, "model": model_for_scene}
            scene_sources[index] = str(target)

    missing = [
        {"source": source, "target": str(info["target"]), "model": info["model"]}
        for source, info in candidates.items()
        if not info["target"].is_file()
    ]
    if missing:
        cache_dir = _model_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        previous_cache = os.environ.get("REMBG_HOME")
        os.environ["REMBG_HOME"] = str(cache_dir)
        request_path = None
        (base_dir / "media" / "person-cutouts").mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", prefix="cutout-request-",
            dir=base_dir / "media" / "person-cutouts", delete=False, encoding="utf-8",
        ) as request_file:
            request_file.write(json.dumps({"model": _model_name(), "items": missing}))
            request_path = Path(request_file.name)
        timeout = timeout_seconds
        if timeout is None:
            try:
                # Portrait matting is an optional enhancement. A cold model
                # download must not stall the film; keep the original verified
                # portrait if local inference is not warm and fast.
                timeout = float(os.environ.get("VIDZEO_PERSON_CUTOUT_TIMEOUT_SECONDS", "12"))
            except (TypeError, ValueError):
                timeout = 12
        timeout = max(3.0, min(90.0, float(timeout)))
        try:
            completed = subprocess.run(
                # Use the file entry point instead of `python -m services...`:
                # the bundled Windows Python may run isolated and omit cwd
                # from sys.path, so the package-style invocation fails even
                # when cwd is the server directory.
                [sys.executable, str(Path(__file__).resolve()), "--worker", str(request_path)],
                cwd=str(Path(__file__).resolve().parents[1]),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env={**os.environ, "REMBG_HOME": str(cache_dir), "OMP_NUM_THREADS": "2"},
            )
            if completed.returncode != 0:
                LOGGER.warning("Person cutout worker failed (exit %s): %s", completed.returncode, completed.stderr[-500:])
        except subprocess.TimeoutExpired:
            LOGGER.info("Person cutout worker reached its %.0f second limit; using original portrait media", timeout)
        except OSError as exc:
            LOGGER.warning("Could not start the person cutout worker: %s", exc)
        finally:
            if previous_cache is None:
                os.environ.pop("REMBG_HOME", None)
            else:
                os.environ["REMBG_HOME"] = previous_cache
            if request_path:
                request_path.unlink(missing_ok=True)

    for index, cutout in scene_sources.items():
        if index < 0:
            continue
        target = Path(cutout)
        if not target.is_file() or target.stat().st_size < 64:
            continue
        scene = scenes[index]
        primary = dict(scene.get("primary_visual") or {})
        props = dict(primary.get("props") or {})
        props["cutout_src"] = str(target)
        primary["props"] = props
        if isinstance(primary.get("asset"), Mapping):
            primary["asset"] = {**dict(primary["asset"]), "cutout_src": str(target)}
        scene["primary_visual"] = primary
        if isinstance(scene.get("media"), dict):
            scene["media"]["cutout_src"] = str(target)
    return result


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--worker":
    raise SystemExit(_worker_entry(Path(sys.argv[2])))
