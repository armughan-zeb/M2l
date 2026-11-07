import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from pathlib import Path
from typing import List, Dict
from urllib.parse import quote

def _project_id(item: Path, stats) -> str:
    # os.stat().st_ino is 0 on Windows, and two output folders can contain
    # identically named files. Include the resolved path so every project has
    # a stable identity even when names, sizes, and timestamps match.
    try:
        resolved_path = item.resolve()
    except OSError:
        resolved_path = item
    identity = f"{resolved_path}\0{item.name}\0{stats.st_size}\0{stats.st_mtime_ns}"
    return hashlib.md5(identity.encode('utf-8')).hexdigest()[:16]

def _thumbnail_name(video_path: Path) -> str:
    """Return a collision-safe cache name that changes when the video changes."""
    try:
        stats = video_path.stat()
        identity = f"{video_path.resolve()}\0{stats.st_size}\0{stats.st_mtime_ns}"
    except OSError:
        identity = str(video_path.resolve())
    digest = hashlib.sha1(identity.encode("utf-8", errors="replace")).hexdigest()[:20]
    return f"{video_path.stem[:48]}-{digest}.jpg"


def ensure_project_thumbnail(video_path: Path) -> str:
    """Ensure a real, cache-safe JPG poster exists and return its local URL."""
    try:
        temp_root = Path(os.environ.get("VIDZEO_TEMP_DIR") or Path(__file__).resolve().parents[2] / "temp")
        thumbs_dir = temp_root / "thumbnails"
        thumbs_dir.mkdir(parents=True, exist_ok=True)
        thumb_path = thumbs_dir / _thumbnail_name(video_path)
        if thumb_path.exists() and thumb_path.stat().st_size > 0:
            return f"/temp/thumbnails/{quote(thumb_path.name)}"

        # Fast thumbnail extraction with ffmpeg
        import subprocess
        ffmpeg_bin = os.environ.get("FFMPEG_BINARY") or os.environ.get("VIDZEO_FFMPEG_PATH") or "ffmpeg"
        cmd = [
            ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", "0.5", "-i", str(video_path),
            "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "3", str(thumb_path)
        ]
        subprocess.run(cmd, capture_output=True, timeout=8, check=False)
        if thumb_path.exists() and thumb_path.stat().st_size > 0:
            return f"/temp/thumbnails/{quote(thumb_path.name)}"
    except Exception:
        pass
    return ""


def list_projects(output_dir: Path) -> List[Dict]:
    """Scan the output directory for video files and return metadata."""
    if not output_dir.exists():
        return []
    
    projects = []
    # Support common video extensions
    extensions = {
        '.mp4', '.m4v', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv',
        '.mpg', '.mpeg', '.ts', '.3gp',
    }
    
    try:
        candidates = output_dir.rglob("*")
    except OSError:
        candidates = ()

    video_files = []
    for item in candidates:
        try:
            if item.is_file() and item.suffix.lower() in extensions:
                video_files.append(item)
        except OSError:
            # A file can disappear while a render is being finalized. It
            # should not make the entire Projects request fail.
            continue

    # Thumbnail extraction invokes ffmpeg. Doing it serially made a library
    # of many exports look empty/stuck while every poster was generated. Keep
    # the listing responsive by generating posters in a small bounded pool.
    try:
        configured_workers = int(os.environ.get("VIDZEO_PROJECT_THUMBNAIL_WORKERS", "3") or 3)
    except (TypeError, ValueError):
        configured_workers = 3
    worker_count = max(1, min(configured_workers, len(video_files) or 1))
    thumbnails = {}
    if video_files:
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="project-thumb") as executor:
            futures = {item: executor.submit(ensure_project_thumbnail, item) for item in video_files}
            for item in video_files:
                try:
                    thumbnails[item] = futures[item].result()
                except Exception:
                    thumbnails[item] = ""

    for item in video_files:
        try:
            stats = item.stat()
            relative_name = item.relative_to(output_dir).as_posix()
        except (OSError, ValueError):
            continue
        projects.append({
            "id": _project_id(item, stats),
            "name": item.name,
            "filename": relative_name,
            "path": str(item),
            "root": str(output_dir.resolve()),
            "url": f"/output/{quote(relative_name, safe='/')}",
            "thumbnail_url": thumbnails.get(item, ""),
            "size": stats.st_size,
            "created_at": stats.st_ctime,
            "modified_at": stats.st_mtime,
            "extension": item.suffix.lower()
        })
    
    # Sort by created_at descending
    projects.sort(key=lambda x: x["created_at"], reverse=True)
    return projects


def delete_project(output_dir: Path, filename: str) -> bool:
    """Delete a project file."""
    output_root = output_dir.resolve()
    requested = Path(str(filename or "").replace("/", os.sep))
    # Preserve nested project paths while still rejecting absolute paths and
    # traversal outside the registered output root.
    if requested.is_absolute():
        return False
    file_path = (output_root / requested).resolve()
    if output_root not in file_path.parents and file_path != output_root:
        return False
    if file_path.exists() and file_path.is_file():
        file_path.unlink()
        return True
    return False
