"""Atomic, job-scoped media promotion and ffprobe validation.

Provider transports are allowed to create incomplete ``.part``/``.raw``
files.  A path becomes renderable only after ffprobe proves that it contains
the required stream and the file is atomically moved to its final name.

The helpers in this module deliberately do not maintain a process-global
"latest download" fallback.  Callers must pass the exact job-owned source and
destination paths, which prevents one job from adopting another job's media.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from typing import Any, Dict, Optional, Tuple


_SMOKE_LOCK = threading.Lock()
_SMOKE_RESULTS: Dict[Tuple[str, str], Dict[str, Any]] = {}


@dataclass(frozen=True)
class MediaProbeResult:
    path: str
    valid: bool
    reason: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_streams: int = 0
    audio_streams: int = 0
    size_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def resolve_ffmpeg_pair(workspace_root: Optional[str | Path] = None) -> Tuple[str, str]:
    """Resolve one matching ffmpeg/ffprobe pair for the current process."""
    configured_ffmpeg = str(os.environ.get("VIDZEO_FFMPEG_PATH") or "").strip()
    configured_ffprobe = str(os.environ.get("VIDZEO_FFPROBE_PATH") or "").strip()
    if configured_ffmpeg and configured_ffprobe:
        return configured_ffmpeg, configured_ffprobe
    root = Path(workspace_root) if workspace_root else Path(__file__).resolve().parents[2]
    tools_dir = root / "tools" / "ffmpeg"
    suffix = ".exe" if os.name == "nt" else ""
    local_ffmpeg = tools_dir / f"ffmpeg{suffix}"
    local_ffprobe = tools_dir / f"ffprobe{suffix}"
    if local_ffmpeg.is_file() and local_ffprobe.is_file():
        return str(local_ffmpeg.resolve()), str(local_ffprobe.resolve())
    return shutil.which("ffmpeg") or "ffmpeg", shutil.which("ffprobe") or "ffprobe"


def smoke_test_ffmpeg_pair(
    ffmpeg_path: str = "",
    ffprobe_path: str = "",
    *,
    work_dir: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Run one real encode/probe and verify the filters required by V2."""

    resolved_ffmpeg, resolved_ffprobe = resolve_ffmpeg_pair()
    ffmpeg = str(ffmpeg_path or resolved_ffmpeg)
    ffprobe = str(ffprobe_path or resolved_ffprobe)
    cache_key = (str(Path(ffmpeg).resolve()), str(Path(ffprobe).resolve()))
    with _SMOKE_LOCK:
        cached = _SMOKE_RESULTS.get(cache_key)
        if cached is not None:
            return dict(cached)
        owns_root = work_dir is None
        root = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="vidzeo_ffmpeg_smoke_"))
        root.mkdir(parents=True, exist_ok=True)
        staged = root / "ffmpeg_smoke.mp4.part"
        encoded = root / "ffmpeg_smoke.mp4"
        try:
            filters = subprocess.run(
                [ffmpeg, "-hide_banner", "-filters"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            filter_text = f"{filters.stdout}\n{filters.stderr}".lower()
            required = ("drawtext", "overlay", "trim", "setpts")
            missing = [name for name in required if name not in filter_text]
            if filters.returncode != 0 or missing:
                result = {
                    "ok": False,
                    "reason": "missing_required_filters" if missing else "filter_probe_failed",
                    "missing_filters": missing,
                    "ffmpeg": ffmpeg,
                    "ffprobe": ffprobe,
                }
                _SMOKE_RESULTS[cache_key] = result
                return dict(result)
            command = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:r=30:d=0.2",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", "-f", "mp4", str(staged),
            ]
            encoded_result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=25,
                check=False,
            )
            if encoded_result.returncode != 0 or not staged.is_file():
                result = {
                    "ok": False,
                    "reason": "encode_failed",
                    "ffmpeg": ffmpeg,
                    "ffprobe": ffprobe,
                    "stderr": (encoded_result.stderr or "")[-400:],
                }
                _SMOKE_RESULTS[cache_key] = result
                return dict(result)
            os.replace(staged, encoded)
            probe = probe_media(encoded, ffprobe_path=ffprobe, min_bytes=256)
            result = {
                "ok": bool(probe.valid),
                "reason": "ok" if probe.valid else probe.reason,
                "ffmpeg": ffmpeg,
                "ffprobe": ffprobe,
                "probe": probe.to_dict(),
            }
            _SMOKE_RESULTS[cache_key] = result
            return dict(result)
        finally:
            for path in (staged, encoded):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            if owns_root:
                try:
                    shutil.rmtree(root, ignore_errors=True)
                except OSError:
                    pass


def _parse_fraction(value: Any) -> float:
    raw = str(value or "").strip()
    if not raw or raw in {"0/0", "N/A"}:
        return 0.0
    try:
        if "/" in raw:
            numerator, denominator = raw.split("/", 1)
            return float(numerator) / float(denominator)
        return float(raw)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def probe_media(
    path: str | Path,
    *,
    ffprobe_path: str = "",
    require_video: bool = True,
    min_bytes: int = 4096,
    max_bytes: Optional[int] = None,
    min_duration: float = 0.05,
    timeout_seconds: float = 20.0,
) -> MediaProbeResult:
    """Return a structured integrity verdict for one exact local file."""
    source = Path(path)
    try:
        size_bytes = source.stat().st_size
    except OSError:
        return MediaProbeResult(str(source), False, "missing_file")
    if size_bytes < max(0, int(min_bytes)):
        return MediaProbeResult(str(source), False, "file_too_small", size_bytes=size_bytes)
    if max_bytes is not None and size_bytes > int(max_bytes):
        return MediaProbeResult(str(source), False, "file_too_large", size_bytes=size_bytes)

    _ffmpeg, resolved_probe = resolve_ffmpeg_pair()
    probe = ffprobe_path or resolved_probe
    command = [
        probe,
        "-v", "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,width,height,avg_frame_rate",
        "-of", "json",
        str(source),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout_seconds)),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return MediaProbeResult(
            str(source), False, f"ffprobe_error:{type(exc).__name__}", size_bytes=size_bytes,
        )
    if completed is None or not hasattr(completed, "returncode"):
        # Real subprocess.run always returns CompletedProcess. This explicit
        # result keeps test doubles distinguishable from a genuine ffprobe
        # rejection without ever treating the file as valid here.
        return MediaProbeResult(str(source), False, "ffprobe_invalid_result", size_bytes=size_bytes)
    if completed.returncode != 0:
        return MediaProbeResult(str(source), False, "ffprobe_failed", size_bytes=size_bytes)
    try:
        payload = json.loads(completed.stdout or "{}")
    except (TypeError, json.JSONDecodeError):
        return MediaProbeResult(str(source), False, "ffprobe_invalid_json", size_bytes=size_bytes)

    streams = payload.get("streams") if isinstance(payload, dict) else []
    streams = streams if isinstance(streams, list) else []
    video_rows = [row for row in streams if isinstance(row, dict) and row.get("codec_type") == "video"]
    audio_rows = [row for row in streams if isinstance(row, dict) and row.get("codec_type") == "audio"]
    if require_video and not video_rows:
        return MediaProbeResult(
            str(source), False, "missing_video_stream",
            video_streams=0, audio_streams=len(audio_rows), size_bytes=size_bytes,
        )
    format_row = payload.get("format") if isinstance(payload, dict) else {}
    try:
        duration = max(0.0, float((format_row or {}).get("duration") or 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    if duration < max(0.0, float(min_duration)):
        return MediaProbeResult(
            str(source), False, "invalid_duration", duration=duration,
            video_streams=len(video_rows), audio_streams=len(audio_rows), size_bytes=size_bytes,
        )
    video = video_rows[0] if video_rows else {}
    return MediaProbeResult(
        path=str(source),
        valid=True,
        duration=duration,
        width=max(0, int(video.get("width") or 0)),
        height=max(0, int(video.get("height") or 0)),
        fps=max(0.0, _parse_fraction(video.get("avg_frame_rate"))),
        video_streams=len(video_rows),
        audio_streams=len(audio_rows),
        size_bytes=size_bytes,
    )


def promote_verified_media(
    staged_path: str | Path,
    final_path: str | Path,
    *,
    ffprobe_path: str = "",
    require_video: bool = True,
    min_bytes: int = 4096,
    max_bytes: Optional[int] = None,
    min_duration: float = 0.05,
    expected_duration: Optional[float] = None,
    duration_tolerance: Optional[float] = None,
    expected_width: Optional[int] = None,
    expected_height: Optional[int] = None,
    require_decode: bool = False,
    ffmpeg_path: str = "",
) -> MediaProbeResult:
    """Validate a job-owned staged file and atomically promote it.

    ``staged_path`` and ``final_path`` must share a parent directory so the
    rename is atomic on Windows and POSIX.  A failed validation leaves no final
    path and reports the exact reason; cleanup of the staged file remains the
    caller's explicit responsibility.
    """
    staged = Path(staged_path).resolve()
    final = Path(final_path).resolve()
    if staged == final:
        return MediaProbeResult(str(staged), False, "staged_equals_final")
    if staged.parent != final.parent:
        return MediaProbeResult(str(staged), False, "cross_directory_promotion_forbidden")
    probe = probe_media(
        staged,
        ffprobe_path=ffprobe_path,
        require_video=require_video,
        min_bytes=min_bytes,
        max_bytes=max_bytes,
        min_duration=min_duration,
    )
    if not probe.valid:
        return probe
    if require_decode and require_video:
        ffmpeg = ffmpeg_path or resolve_ffmpeg_pair()[0]
        try:
            decoded = subprocess.run(
                [ffmpeg, '-hide_banner', '-loglevel', 'error', '-xerror',
                 '-err_detect', 'explode', '-threads', '1', '-i', str(staged),
                 '-map', '0:v:0', '-an', '-threads', '1', '-f', 'null', '-'],
                capture_output=True, text=True, check=False,
                timeout=max(10.0, min(45.0, probe.duration * 2 + 5)))
            if decoded.returncode != 0:
                return replace(probe, valid=False, reason='video_decode_failed')
        except subprocess.TimeoutExpired:
            return replace(probe, valid=False, reason='video_decode_timeout')
        except (OSError, AttributeError):
            return replace(probe, valid=False, reason='video_decode_unavailable')
    if expected_duration is not None:
        tolerance = max(0.0, float(duration_tolerance or 0.0))
        if abs(probe.duration - float(expected_duration)) > tolerance:
            return MediaProbeResult(
                str(staged), False, "duration_mismatch",
                duration=probe.duration, width=probe.width, height=probe.height,
                fps=probe.fps, video_streams=probe.video_streams,
                audio_streams=probe.audio_streams, size_bytes=probe.size_bytes,
            )
    if expected_width is not None and probe.width != int(expected_width):
        return MediaProbeResult(
            str(staged), False, "width_mismatch",
            duration=probe.duration, width=probe.width, height=probe.height,
            fps=probe.fps, video_streams=probe.video_streams,
            audio_streams=probe.audio_streams, size_bytes=probe.size_bytes,
        )
    if expected_height is not None and probe.height != int(expected_height):
        return MediaProbeResult(
            str(staged), False, "height_mismatch",
            duration=probe.duration, width=probe.width, height=probe.height,
            fps=probe.fps, video_streams=probe.video_streams,
            audio_streams=probe.audio_streams, size_bytes=probe.size_bytes,
        )
    final.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(staged, final)
    except OSError as exc:
        return MediaProbeResult(
            str(staged), False, f"atomic_replace_failed:{type(exc).__name__}",
            duration=probe.duration, width=probe.width, height=probe.height,
            fps=probe.fps, video_streams=probe.video_streams,
            audio_streams=probe.audio_streams, size_bytes=probe.size_bytes,
        )
    return MediaProbeResult(
        path=str(final), valid=True, duration=probe.duration,
        width=probe.width, height=probe.height, fps=probe.fps,
        video_streams=probe.video_streams, audio_streams=probe.audio_streams,
        size_bytes=probe.size_bytes,
    )
