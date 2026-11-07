"""Bounded chapter metadata for the few masters selected for transfer."""
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path


_LOCK = threading.Lock()
_FLIGHTS = {}


def _cache_path(video_id):
    root = Path(os.environ.get('VIDZEO_YT_CHAPTER_CACHE_DIR') or
                Path(__file__).resolve().parents[2] / 'temp' / 'youtube' / 'chapter_cache')
    return root / f'{video_id}.json'


def _cached_chapters(path):
    try:
        entry = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(entry, dict):
            return None
        if (entry.get('schema') == 1 and time.time() < float(entry['expires_at'])
                and isinstance(entry.get('chapters'), list)):
            return [row for row in entry['chapters'] if isinstance(row, dict)][:100]
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def _store_chapters(path, chapters, ttl):
    temp = path.with_name(f'.{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(json.dumps({'schema': 1, 'expires_at': time.time() + ttl,
                                   'chapters': chapters}), encoding='utf-8')
        os.replace(temp, path)
    except OSError:
        pass
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def source_chapters(video_id, timeout_seconds=10):
    """Share optional chapter metadata; never cache scene/visual approval."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', str(video_id or '')):
        return []
    path = _cache_path(video_id)
    cached = _cached_chapters(path)
    if cached is not None:
        return cached
    with _LOCK:
        lock = _FLIGHTS.setdefault(str(video_id), threading.Lock())
    # A sibling can use ordinary middle-of-source timing instead of waiting
    # beyond the optional metadata budget. One owner performs the lookup.
    wait_started = time.monotonic()
    budget = max(1, min(12, float(timeout_seconds)))
    if not lock.acquire(timeout=budget):
        return []
    try:
        cached = _cached_chapters(path)
        if cached is not None:
            return cached
        waited = time.monotonic() - wait_started
        if waited >= budget:
            return []
        lookup_timeout = max(0.1, budget - waited) if waited > 0.05 else timeout_seconds
        chapters, completed = _fetch_source_chapters(video_id, lookup_timeout)
        # Useful metadata lasts six hours. Missing chapters last 15 minutes;
        # failed optional probes only cool down for two minutes.
        _store_chapters(path, chapters, 21600 if chapters else (900 if completed else 120))
        return chapters
    finally:
        lock.release()


def _fetch_source_chapters(video_id, timeout_seconds=10):
    if getattr(sys, 'frozen', False):
        # A packaged backend is not a Python interpreter: passing -c could
        # accidentally start another server. Existing cut/vision selection
        # remains the fallback when no source interpreter is available.
        return [], False
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', str(video_id or '')):
        return [], False
    # A separate process gives metadata extraction a real wall deadline;
    # a socket timeout alone cannot bound yt-dlp's extractor retries.
    script = (
        "import json,sys;sys.path.insert(0,sys.argv[1]);"
        "from services.yt_dlp_runtime import load_yt_dlp;"
        "from services.youtube_pipeline_v2 import _youtube_js_runtime_options;"
        "opts=dict(quiet=True,no_warnings=True,skip_download=True,"
        "socket_timeout=4,retries=0,extractor_retries=0,ignore_no_formats_error=True);"
        "opts.update(_youtube_js_runtime_options());"
        "ydl=load_yt_dlp().YoutubeDL(opts);"
        "info=ydl.extract_info('https://www.youtube.com/watch?v='+sys.argv[2],download=False);"
        "print('VIDNETS_CHAPTERS='+json.dumps((info or {}).get('chapters') or []))"
    )
    try:
        result = subprocess.run(
            [sys.executable, '-c', script, str(Path(__file__).resolve().parents[1]), str(video_id)],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=max(1, min(12, float(timeout_seconds))),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        for line in reversed(result.stdout.splitlines()):
            if line.startswith('VIDNETS_CHAPTERS='):
                rows = json.loads(line.partition('=')[2])
                return ([row for row in rows if isinstance(row, dict)][:100], True) if isinstance(rows, list) else ([], False)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return [], False
