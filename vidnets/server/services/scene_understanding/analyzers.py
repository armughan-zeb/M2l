"""LAVIP V1 analyzers — cheap, deterministic, CPU-friendly.

Every analyzer returns a plain dict so stages are independently replaceable.
Designed for 8 GB RAM: batch size 1, frames deleted after analysis, never
loads full video into memory.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Heavy imports are deferred/guarded so the module can be imported even when
# optional deps are missing.
try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore

try:
    import numpy as np  # type: ignore
except ImportError:
    np = None  # type: ignore

try:
    import imagehash  # type: ignore
    from PIL import Image as PILImage  # type: ignore
except ImportError:
    imagehash = None
    PILImage = None

try:
    from scenedetect import detect, ContentDetector  # type: ignore
except ImportError:
    detect = None  # type: ignore
    ContentDetector = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _media_bin(name: str) -> str:
    local = Path(__file__).resolve().parents[3] / "tools" / "ffmpeg" / f"{name}.exe"
    if local.exists():
        return str(local)
    return shutil.which(name) or name


def _run_ffmpeg(
    args: List[str], timeout: int = 60, cancellation: Any = None
) -> Tuple[bool, str]:
    try:
        process_kwargs = {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": timeout,
            "creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        }
        if cancellation is not None:
            from services.cancellation import run_cancellable_process
            proc = run_cancellable_process(args, cancellation=cancellation, **process_kwargs)
        else:
            proc = subprocess.run(args, **process_kwargs)
        return proc.returncode == 0, proc.stderr
    except subprocess.TimeoutExpired:
        print(f"   [lavip] ffmpeg command timed out after {timeout}s")
        return False, "timeout"
    except Exception as exc:
        print(f"   [lavip] ffmpeg error: {exc}")
        return False, str(exc)


def _cleanup(frames: List[str]) -> None:
    """Delete extracted frame files and their temp directory."""
    for f in frames:
        try:
            os.remove(f)
        except OSError:
            pass
    parent = os.path.dirname(frames[0]) if frames else ""
    if parent and parent.startswith(tempfile.gettempdir()):
        try:
            shutil.rmtree(parent, ignore_errors=True)
        except OSError:
            pass


def _video_duration(path: str) -> float:
    try:
        proc = subprocess.run(
            [_media_bin("ffprobe"), "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return float(proc.stdout.strip()) if proc.returncode == 0 else 0.0
    except Exception:
        return 0.0


def _video_meta(path: str) -> Tuple[int, int, float]:
    """Return (width, height, fps) via ffprobe."""
    w = h = 0
    fps = 0.0
    try:
        proc = subprocess.run(
            [_media_bin("ffprobe"), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            parts = proc.stdout.strip().split(",")
            if len(parts) >= 3:
                w = int(parts[0])
                h = int(parts[1])
                rate_str = parts[2].strip()
                if "/" in rate_str:
                    num, den = rate_str.split("/")
                    den_f = float(den)
                    fps = float(num) / den_f if den_f else 0.0
                else:
                    fps = float(rate_str)
    except Exception:
        pass
    return w, h, fps


# ---------------------------------------------------------------------------
# Analyzer 1 — Scene detection (PySceneDetect)
# ---------------------------------------------------------------------------

def detect_scenes(path: str, threshold: float = 27.0) -> List[Tuple[float, float]]:
    """Return [(start_sec, end_sec), ...] of detected scenes.

    Falls back to a single full-duration scene if PySceneDetect is missing.
    """
    duration = _video_duration(path)
    if duration <= 0:
        return [(0.0, 0.0)]
    if detect is None or ContentDetector is None:
        return [(0.0, duration)]
    try:
        scenes = detect(path, ContentDetector(threshold=threshold), show_progress=False)
        result: List[Tuple[float, float]] = []
        for start, end in scenes:
            result.append((float(start.get_seconds()), float(end.get_seconds())))
        if not result:
            return [(0.0, duration)]
        return result
    except Exception:
        return [(0.0, duration)]


# ---------------------------------------------------------------------------
# Analyzer 2 — Frame sampling (FFmpeg)
# ---------------------------------------------------------------------------

def sample_frames(path: str, count: int = 3, duration: Optional[float] = None,
                  out_dir: Optional[str] = None) -> List[str]:
    """Extract ``count`` evenly-spaced JPEG frames into ``out_dir``.

    Returns list of frame file paths.  Frames are small (320px wide) to keep
    memory low.
    """
    if duration is None:
        duration = _video_duration(path)
    if duration <= 0:
        return []
    n = max(1, min(count, 5))
    # Evenly space timestamps, avoiding the very first/last 0.5s
    pad = min(0.5, duration * 0.1)
    usable = max(duration - 2 * pad, 0.1)
    step = usable / max(n, 1)
    timestamps = [pad + step * (i + 0.5) for i in range(n)]
    if out_dir is None:
        out_dir = tempfile.mkdtemp(prefix="lavip_frames_")
    os.makedirs(out_dir, exist_ok=True)
    frames: List[str] = []
    for i, ts in enumerate(timestamps):
        out_path = os.path.join(out_dir, f"frame_{i:02d}.jpg")
        ok, _ = _run_ffmpeg([
            _media_bin("ffmpeg"), "-y", "-ss", f"{ts:.2f}", "-i", path,
            "-frames:v", "1", "-vf", "scale=320:-2",
            "-q:v", "3", out_path,
        ], timeout=15)
        if ok and os.path.exists(out_path):
            frames.append(out_path)
    return frames


# ---------------------------------------------------------------------------
# Analyzer 3 — Quality filter (OpenCV)
# ---------------------------------------------------------------------------

def measure_blur(frame_path: str) -> float:
    """Laplacian variance — higher = sharper.  Returns 0 if cv2 missing."""
    if cv2 is None:
        return 0.0
    try:
        img = cv2.imread(frame_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return 0.0
        return float(cv2.Laplacian(img, cv2.CV_64F).var())
    except Exception:
        return 0.0


def measure_brightness(frame_path: str) -> float:
    """Average brightness 0-255."""
    if cv2 is None:
        return 0.0
    try:
        img = cv2.imread(frame_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return 0.0
        return float(img.mean())
    except Exception:
        return 0.0


def measure_black_ratio(frame_path: str) -> float:
    """Fraction of near-black pixels (value < 15).  0-1."""
    if cv2 is None:
        return 0.0
    try:
        img = cv2.imread(frame_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return 0.0
        return float((img < 15).sum()) / float(img.size)
    except Exception:
        return 0.0


def analyze_quality(frames: List[str]) -> Dict[str, float]:
    """Return {blur, brightness, black_ratio} averaged across frames."""
    if not frames:
        return {"blur": 0.0, "brightness": 0.0, "black_ratio": 0.0}
    blurs = [measure_blur(f) for f in frames]
    brights = [measure_brightness(f) for f in frames]
    blacks = [measure_black_ratio(f) for f in frames]
    return {
        "blur": sum(blurs) / len(blurs),
        "brightness": sum(brights) / len(brights),
        "black_ratio": sum(blacks) / len(blacks),
    }


# ---------------------------------------------------------------------------
# Analyzer 4 — Duplicate detection (ImageHash)
# ---------------------------------------------------------------------------

def compute_phash(frame_path: str) -> str:
    """Return hex perceptual hash, or empty string if imagehash missing."""
    if imagehash is None or PILImage is None:
        return ""
    try:
        with PILImage.open(frame_path) as img:
            return str(imagehash.phash(img))
    except Exception:
        return ""


def find_duplicate(phash: str, seen: Dict[str, str], threshold: int = 5) -> Optional[str]:
    """Return the path of a near-duplicate if hamming distance < threshold."""
    if not phash or imagehash is None:
        return None
    try:
        h1 = imagehash.hex_to_hash(phash)
    except Exception:
        return None
    for other_hash, other_path in seen.items():
        try:
            h2 = imagehash.hex_to_hash(other_hash)
            if (h1 - h2) < threshold:
                return other_path
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Analyzer 5 — Watermark detection (OpenCV corner stability)
# ---------------------------------------------------------------------------

_CORNERS = {
    "top_left":     (0.0, 0.0, 0.32, 0.25),
    "top_right":    (0.68, 0.0, 1.0, 0.25),
    "bottom_left":  (0.0, 0.75, 0.32, 1.0),
    "bottom_right": (0.68, 0.75, 1.0, 1.0),
}


def _extract_corner(img, corner: Tuple[float, float, float, float]):
    h, w = img.shape[:2]
    x1 = int(corner[0] * w)
    y1 = int(corner[1] * h)
    x2 = int(corner[2] * w)
    y2 = int(corner[3] * h)
    return img[y1:y2, x1:x2]


def detect_watermark(frames: List[str]) -> Dict[str, float]:
    """Return {region: probability} for each corner with verified static logo pixels."""
    if cv2 is None or np is None or len(frames) < 2:
        return {}
    result: Dict[str, float] = {}
    for name in _CORNERS:
        box = detect_watermark_box(frames, name)
        result[name] = 0.90 if box is not None else 0.0
    return result


def detect_watermark_box(
    frames: List[str],
    corner: str,
) -> Optional[Tuple[float, float, float, float]]:
    """Return a tight, pixel-exact bounding box around a verified recurrent static logo.
    
    Returns None if no genuine stationary logo contour is detected. NEVER returns fallback boxes.
    """
    if cv2 is None or np is None or not frames or corner not in _CORNERS or len(frames) < 2:
        return None
    try:
        images = []
        for frame_index, frame_path in enumerate(frames):
            image = cv2.imread(frame_path, cv2.IMREAD_GRAYSCALE)
            if image is not None:
                images.append(image)
        if len(images) < 2:
            return None
        frame_height, frame_width = images[0].shape[:2]
        rx1, ry1, rx2, ry2 = _CORNERS[corner]
        x1 = int(rx1 * frame_width)
        y1 = int(ry1 * frame_height)
        x2 = int(rx2 * frame_width)
        y2 = int(ry2 * frame_height)
        crop_width = x2 - x1
        crop_height = y2 - y1
        if crop_width < 10 or crop_height < 10:
            return None

        crops = [image[y1:y2, x1:x2] for image in images]
        
        # 1. Compute frame-by-frame Canny edges
        edge_maps = [cv2.Canny(c, 35, 110) for c in crops]
        
        # 2. Count edge recurrence across frames at the exact same (x, y) pixel
        edge_stack = np.stack(edge_maps, axis=0).astype(np.float32) / 255.0
        edge_recurrence = np.sum(edge_stack, axis=0)
        
        # A genuine static logo edge must appear at the exact same pixel in ALL frames (N or N-1)
        req_recurrence = len(crops) - (1 if len(crops) >= 4 else 0)
        static_edges = (edge_recurrence >= req_recurrence).astype(np.uint8) * 255
        
        # 3. Check pixel std across all frames
        stack_gray = np.stack(crops, axis=0).astype(np.float32)
        pixel_std = np.std(stack_gray, axis=0)
        
        # Only keep static edges that are strictly stationary (std <= 4.0)
        static_edges = cv2.bitwise_and(static_edges, (pixel_std <= 4.0).astype(np.uint8) * 255)
        
        crop_area = float(crop_width * crop_height)

        # 4. Group adjacent static edges into logo glyph clusters
        kernel_logo = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        grouped = cv2.dilate(static_edges, kernel_logo, iterations=1)
        contours, _ = cv2.findContours(grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        candidates = []
        for c in contours:
            bx, by, bw, bh = cv2.boundingRect(c)
            area = bw * bh
            if bw < 14 or bh < 10 or bw > crop_width * 0.50 or bh > crop_height * 0.50:
                continue
            if area < 150 or area > crop_area * 0.15:
                continue
            # Verify that the logo contour strokes themselves are stationary
            patch_edges = static_edges[by:by+bh, bx:bx+bw]
            edge_count = cv2.countNonZero(patch_edges)
            if edge_count < 20:
                continue
            patch_std = pixel_std[by:by+bh, bx:bx+bw]
            edge_std = patch_std[patch_edges > 0]
            if edge_std.size == 0 or np.mean(edge_std) > 4.5:
                continue
            candidates.append((bx, by, bw, bh))

        if not candidates:
            # ZERO FALLBACK BOXES! Clean footage stays 100% clean!
            return None

        # Merge candidate boxes
        min_bx = min(b[0] for b in candidates)
        min_by = min(b[1] for b in candidates)
        max_bx = max(b[0] + b[2] for b in candidates)
        max_by = max(b[1] + b[3] for b in candidates)
        
        # Add minimal 2px margin
        bx1 = max(0, x1 + min_bx - 2)
        by1 = max(0, y1 + min_by - 2)
        bx2 = min(frame_width, x1 + max_bx + 2)
        by2 = min(frame_height, y1 + max_by + 2)

        return (
            round(bx1 / frame_width, 4),
            round(by1 / frame_height, 4),
            round(bx2 / frame_width, 4),
            round(by2 / frame_height, 4),
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Analyzer 6 — Motion energy (inter-frame difference)
# ---------------------------------------------------------------------------

def measure_motion(frames: List[str]) -> float:
    """Return 0-100 motion energy score based on pixel differences."""
    if cv2 is None or np is None or len(frames) < 2:
        return 0.0
    try:
        grays = []
        for fp in frames:
            img = cv2.imread(fp, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                grays.append(cv2.resize(img, (160, 90)))
        if len(grays) < 2:
            return 0.0
        diffs = []
        for i in range(len(grays) - 1):
            diff = cv2.absdiff(grays[i], grays[i + 1])
            diffs.append(float(diff.mean()))
        mean_diff = sum(diffs) / len(diffs)
        return round(min(100.0, mean_diff * 4.0), 2)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Analyzer 7 — Subtitle detection (lower text line verification)
# ---------------------------------------------------------------------------

def detect_subtitle(frame_path: str, bottom_pct: float = 0.28) -> bool:
    """Detect if frame contains verified high-contrast text lines in bottom 28%."""
    if cv2 is None:
        return False
    try:
        img = cv2.imread(frame_path)
        if img is None:
            return False
        h, w = img.shape[:2]
        y1_scan = int(0.72 * h)
        y2_scan = int(0.96 * h)
        region = img[y1_scan:y2_scan, :]
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        rh, rw = gray.shape[:2]
        if rh <= 0 or rw <= 0:
            return False

        sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        stroke_edges = (np.abs(sobel_x) > 35).astype(np.uint8) * 255
        kw = max(16, int(rw * 0.025))
        kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (kw, 2))
        dilated = cv2.dilate(stroke_edges, kernel_h, iterations=2)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for c in contours:
            x, y, bw, bh = cv2.boundingRect(c)
            aspect = bw / max(1, bh)
            x_center = (x + bw/2.0) / rw
            if bw >= rw * 0.15 and bh >= 12 and bh <= 55 and aspect >= 3.2 and 0.25 <= x_center <= 0.75:
                patch_sobel = stroke_edges[y:y+bh, x:x+bw]
                stroke_density = cv2.countNonZero(patch_sobel) / float(max(1, bw * bh))
                # Thin anti-aliased subtitle fonts can produce a dense Sobel
                # contour after the horizontal dilation (especially on small
                # 720p/thumbnail probes).  Compact geometry and recurrence
                # are the primary safeguards; the former 0.45 ceiling
                # rejected legitimate single-line captions at ~0.53 density.
                if 0.08 <= stroke_density <= 0.70:
                    return True
        return False
    except Exception:
        return False


def detect_subtitles(frames: List[str]) -> Dict[str, bool]:
    """Analyze multiple frames for subtitle presence."""
    if not frames:
        return {"has_subtitle": False, "subtitle_frames": 0, "subtitle_confidence": 0.0}
    box = detect_subtitle_box(frames)
    has_sub = box is not None
    return {
        "has_subtitle": has_sub,
        "subtitle_frames": len(frames) if has_sub else 0,
        "subtitle_confidence": 0.90 if has_sub else 0.0,
    }


def detect_subtitle_box(frames: List[str]) -> Optional[Tuple[float, float, float, float]]:
    """Return a tight, stroke-verified normalized box for subtitle lines.
    
    Returns None if no genuine subtitle line is detected. NEVER returns fallback boxes.
    """
    if cv2 is None or np is None or not frames or len(frames) < 2:
        return None
    try:
        boxes = []
        frame_width = frame_height = 0
        for fp in frames:
            img = cv2.imread(fp)
            if img is None:
                continue
            h, w = img.shape[:2]
            frame_width, frame_height = w, h
            y1_scan = int(0.72 * h)
            y2_scan = int(0.96 * h)
            region = img[y1_scan:y2_scan, :]
            gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
            rh, rw = gray.shape[:2]
            
            sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
            stroke_edges = (np.abs(sobel_x) > 35).astype(np.uint8) * 255
            kw = max(16, int(rw * 0.025))
            kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (kw, 2))
            dilated = cv2.dilate(stroke_edges, kernel_h, iterations=2)
            contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            line_candidates = []
            for c in contours:
                x, y, bw, bh = cv2.boundingRect(c)
                aspect = bw / max(1, bh)
                x_center = (x + bw/2.0) / rw
                if bw >= rw * 0.15 and bh >= 12 and bh <= 75 and aspect >= 2.8 and 0.20 <= x_center <= 0.80:
                    patch_sobel = stroke_edges[y:y+bh, x:x+bw]
                    stroke_density = cv2.countNonZero(patch_sobel) / float(max(1, bw * bh))
                    if 0.08 <= stroke_density <= 0.70:
                        line_candidates.append((x, y + y1_scan, x + bw, y + y1_scan + bh))
            
            if line_candidates:
                min_x = min(b[0] for b in line_candidates)
                min_y = min(b[1] for b in line_candidates)
                max_x = max(b[2] for b in line_candidates)
                max_y = max(b[3] for b in line_candidates)
                boxes.append((min_x, min_y, max_x, max_y))

        # Real subtitles appear consistently across frames
        if len(boxes) < (2 if len(frames) >= 3 else 1):
            return None

        x1 = min(b[0] for b in boxes)
        y1 = min(b[1] for b in boxes)
        x2 = max(b[2] for b in boxes)
        y2 = max(b[3] for b in boxes)
        
        # Minimal 2px padding
        x1 = max(0, x1 - 2)
        y1 = max(0, y1 - 2)
        x2 = min(frame_width, x2 + 2)
        y2 = min(frame_height, y2 + 2)

        area_ratio = ((x2 - x1) * (y2 - y1)) / float(frame_width * frame_height)
        if area_ratio <= 0 or area_ratio > 0.30:
            return None

        return (
            round(x1 / frame_width, 4),
            round(y1 / frame_height, 4),
            round(x2 / frame_width, 4),
            round(y2 / frame_height, 4),
        )
    except Exception:
        return None


def detect_persistent_text_overlay_box(
    frames: List[str],
    *,
    min_y: float = 0.45,
) -> Optional[Tuple[float, float, float, float]]:
    """Detect a persistent lower-third/text band beyond subtitle geometry.

    Creator captions and channel lower-thirds are often taller or higher than
    the old subtitle-only detector's fixed bottom scan. This detector uses
    lightweight stroke geometry across several frames, then requires the
    *region* (not the exact glyph pixels) to persist. It is deliberately
    conservative and returns no box when OpenCV cannot establish persistence.
    """
    if cv2 is None or np is None or not frames or len(frames) < 2:
        return None
    try:
        bands: List[Dict[str, Any]] = []
        for frame_index, frame_path in enumerate(frames):
            image = cv2.imread(str(frame_path))
            if image is None:
                continue
            height, width = image.shape[:2]
            if width <= 0 or height <= 0:
                continue
            y_start = max(0, int(float(min_y) * height))
            scan = cv2.cvtColor(image[y_start:int(0.98 * height), :], cv2.COLOR_BGR2GRAY)
            if scan.size == 0:
                continue
            # Captions may be either light-on-dark or dark-on-light. Use both
            # morphological polarities and merge their horizontal strokes.
            kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT, (max(9, int(width * 0.018)), 3)
            )
            top = cv2.morphologyEx(scan, cv2.MORPH_TOPHAT, kernel)
            black = cv2.morphologyEx(scan, cv2.MORPH_BLACKHAT, kernel)
            mask = cv2.bitwise_or(
                cv2.threshold(top, 24, 255, cv2.THRESH_BINARY)[1],
                cv2.threshold(black, 24, 255, cv2.THRESH_BINARY)[1],
            )
            mask = cv2.dilate(
                mask,
                cv2.getStructuringElement(cv2.MORPH_RECT, (max(7, int(width * 0.012)), 2)),
                iterations=1,
            )
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            frame_boxes: List[Tuple[float, float, float, float]] = []
            for contour in contours:
                x, y, box_width, box_height = cv2.boundingRect(contour)
                aspect = box_width / max(1.0, float(box_height))
                density = cv2.countNonZero(mask[y:y + box_height, x:x + box_width]) / max(
                    1.0, float(box_width * box_height)
                )
                if (
                    box_width >= width * 0.12
                    and 6 <= box_height <= height * 0.28
                    and aspect >= 2.0
                    and 0.03 <= density <= 0.90
                ):
                    frame_boxes.append((
                        x / width,
                        (y + y_start) / height,
                        min(1.0, (x + box_width) / width),
                        min(1.0, (y + y_start + box_height) / height),
                    ))
            # Merge boxes on the same horizontal band within this frame.
            for box in frame_boxes:
                bx1, by1, bx2, by2 = box
                center_y = (by1 + by2) / 2.0
                matching = next(
                    (
                        band for band in bands
                        if abs(float(band["center_y"]) - center_y) <= 0.10
                        and max(float(band["x1"]), bx1) <= min(float(band["x2"]), bx2) + 0.08
                    ),
                    None,
                )
                if matching is None:
                    bands.append({
                        "frames": {frame_index},
                        "x1": bx1, "y1": by1, "x2": bx2, "y2": by2,
                        "center_y": center_y,
                    })
                else:
                    matching["frames"].add(frame_index)
                    matching["x1"] = min(float(matching["x1"]), bx1)
                    matching["y1"] = min(float(matching["y1"]), by1)
                    matching["x2"] = max(float(matching["x2"]), bx2)
                    matching["y2"] = max(float(matching["y2"]), by2)

        # Sobel fallback: morphology is strong against boxed lower-thirds but
        # can erase thin anti-aliased glyphs. Reuse the proven subtitle stroke
        # geometry over the expanded vertical range when no persistent band
        # was found above.
        if not bands:
            for frame_index, frame_path in enumerate(frames):
                image = cv2.imread(str(frame_path))
                if image is None:
                    continue
                height, width = image.shape[:2]
                y_start = max(0, int(float(min_y) * height))
                region = cv2.cvtColor(image[y_start:int(0.98 * height), :], cv2.COLOR_BGR2GRAY)
                stroke_edges = (np.abs(cv2.Sobel(region, cv2.CV_32F, 1, 0, ksize=3)) > 35).astype(np.uint8) * 255
                kernel_h = cv2.getStructuringElement(
                    cv2.MORPH_RECT, (max(16, int(width * 0.025)), 2)
                )
                dilated = cv2.dilate(stroke_edges, kernel_h, iterations=2)
                contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                for contour in contours:
                    x, y, box_width, box_height = cv2.boundingRect(contour)
                    aspect = box_width / max(1.0, float(box_height))
                    density = cv2.countNonZero(stroke_edges[y:y + box_height, x:x + box_width]) / max(1.0, float(box_width * box_height))
                    if not (
                        box_width >= width * 0.12
                        and 8 <= box_height <= height * 0.28
                        and aspect >= 2.2
                        and 0.05 <= density <= 0.75
                    ):
                        continue
                    bx1, by1 = x / width, (y + y_start) / height
                    bx2, by2 = min(1.0, (x + box_width) / width), min(1.0, (y + y_start + box_height) / height)
                    center_y = (by1 + by2) / 2.0
                    matching = next((band for band in bands if abs(float(band["center_y"]) - center_y) <= 0.10), None)
                    if matching is None:
                        bands.append({"frames": {frame_index}, "x1": bx1, "y1": by1, "x2": bx2, "y2": by2, "center_y": center_y})
                    else:
                        matching["frames"].add(frame_index)
                        matching["x1"] = min(float(matching["x1"]), bx1)
                        matching["y1"] = min(float(matching["y1"]), by1)
                        matching["x2"] = max(float(matching["x2"]), bx2)
                        matching["y2"] = max(float(matching["y2"]), by2)

        # Last lightweight signal for thin burned-in text: aggregate edge
        # energy by scanline and retain only a wide, repeated horizontal band.
        # Unlike a full OCR dependency this works in the bundled runtime and
        # does not attempt to read the text; it only decides whether a
        # persistent overlay region needs cleanup.
        for frame_index, frame_path in enumerate(frames):
            image = cv2.imread(str(frame_path))
            if image is None:
                continue
            height, width = image.shape[:2]
            y_start = max(0, int(float(min_y) * height))
            region = cv2.cvtColor(image[y_start:int(0.98 * height), :], cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(region, 45, 135)
            # Include stable high-contrast glyph interiors. Canny alone can
            # produce too few scanline pixels for small anti-aliased captions.
            bright = cv2.threshold(region, 180, 255, cv2.THRESH_BINARY)[1]
            edges = cv2.bitwise_or(edges, bright)
            row_counts = np.count_nonzero(edges, axis=1)
            active_rows = row_counts >= max(3, int(width * 0.006))
            run_start = None
            for row_index in range(len(active_rows) + 1):
                active = row_index < len(active_rows) and bool(active_rows[row_index])
                if active and run_start is None:
                    run_start = row_index
                if (not active or row_index == len(active_rows)) and run_start is not None:
                    run_end = row_index
                    run_height = run_end - run_start
                    if 4 <= run_height <= int(height * 0.24):
                        crop = edges[run_start:run_end, :]
                        columns = np.where(np.count_nonzero(crop, axis=0) > 0)[0]
                        if len(columns):
                            bx1, bx2 = int(columns.min()), int(columns.max()) + 1
                            if bx2 - bx1 >= width * 0.12:
                                nx1, ny1 = bx1 / width, (run_start + y_start) / height
                                nx2, ny2 = bx2 / width, (run_end + y_start) / height
                                center_y = (ny1 + ny2) / 2.0
                                matching = next((band for band in bands if abs(float(band["center_y"]) - center_y) <= 0.10), None)
                                if matching is None:
                                    bands.append({"frames": {frame_index}, "x1": nx1, "y1": ny1, "x2": nx2, "y2": ny2, "center_y": center_y})
                                else:
                                    matching["frames"].add(frame_index)
                                    matching["x1"] = min(float(matching["x1"]), nx1)
                                    matching["y1"] = min(float(matching["y1"]), ny1)
                                    matching["x2"] = max(float(matching["x2"]), nx2)
                                    matching["y2"] = max(float(matching["y2"]), ny2)
                    run_start = None

        required = max(2, int((len(frames) + 1) / 2))
        persistent = [band for band in bands if len(band.get("frames") or ()) >= required]
        if not persistent:
            return None
        best = max(
            persistent,
            key=lambda band: (
                len(band.get("frames") or ()),
                (float(band.get("x2") or 0.0) - float(band.get("x1") or 0.0)),
            ),
        )
        x1, y1 = float(best["x1"]), float(best["y1"])
        x2, y2 = float(best["x2"]), float(best["y2"])
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        if area <= 0.0 or area > 0.24:
            return None
        return (round(max(0.0, x1), 4), round(max(0.0, y1), 4), round(min(1.0, x2), 4), round(min(1.0, y2), 4))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Analyzer 8 — Face detection (OpenCV Haar cascades)
# ---------------------------------------------------------------------------

_HAAR_FACE = None
_HAAR_EYE = None


def _load_haar_cascades():
    """Lazy-load Haar cascade files (open, light, no GPU needed)."""
    global _HAAR_FACE, _HAAR_EYE
    if cv2 is None or not hasattr(cv2, "CascadeClassifier"):
        return
    try:
        cascade_dir = getattr(getattr(cv2, "data", None), "haarcascades", "") or ""
        face_path = os.path.join(cascade_dir, "haarcascade_frontalface_default.xml") if cascade_dir else ""
        eye_path = os.path.join(cascade_dir, "haarcascade_eye.xml") if cascade_dir else ""

        if not face_path or not os.path.isfile(face_path):
            candidates = [
                Path(__file__).resolve().parents[3] / "tools" / "python" / "Lib" / "site-packages" / "cv2" / "data" / "haarcascade_frontalface_default.xml",
                Path(__file__).resolve().parents[3] / "dist-build-cache" / "builder-output" / "win-unpacked" / "resources" / "tools" / "python" / "Lib" / "site-packages" / "cv2" / "data" / "haarcascade_frontalface_default.xml",
            ]
            for cand in candidates:
                if cand.is_file():
                    face_path = str(cand)
                    eye_cand = cand.with_name("haarcascade_eye.xml")
                    if eye_cand.is_file():
                        eye_path = str(eye_cand)
                    break

        if _HAAR_FACE is None and face_path and os.path.isfile(face_path):
            _HAAR_FACE = cv2.CascadeClassifier(face_path)
        if _HAAR_EYE is None and eye_path and os.path.isfile(eye_path):
            _HAAR_EYE = cv2.CascadeClassifier(eye_path)
    except Exception:
        pass


def detect_faces(frame_path: str) -> Dict[str, any]:
    """Detect faces in a frame using Haar cascades.

    Returns dict with 'face_count', 'faces' list of (x,y,w,h), and
    'face_coverage' (fraction of frame area covered by faces).
    """
    _load_haar_cascades()
    if cv2 is None or _HAAR_FACE is None:
        # Fallback: if running in an environment where cv2 lacks CascadeClassifier,
        # delegate to bundled tools/python/python.exe if available.
        bundled_python = Path(__file__).resolve().parents[3] / "tools" / "python" / "python.exe"
        if bundled_python.is_file() and os.path.isfile(frame_path):
            try:
                import subprocess, json
                server_dir = str(Path(__file__).resolve().parents[2])
                code = (
                    f"import sys; sys.path.insert(0, r'{server_dir}'); "
                    f"from services.scene_understanding.analyzers import detect_faces; "
                    f"import json; print(json.dumps(detect_faces(r'{frame_path}')))"
                )
                res = subprocess.run([str(bundled_python), "-c", code], capture_output=True, text=True, timeout=5)
                if res.returncode == 0 and res.stdout.strip():
                    return json.loads(res.stdout.strip().splitlines()[-1])
            except Exception:
                pass
        return {"face_count": 0, "faces": [], "face_coverage": 0.0, "frame_width": 0, "frame_height": 0}
    try:
        img = cv2.imread(frame_path)
        if img is None:
            return {"face_count": 0, "faces": [], "face_coverage": 0.0, "frame_width": 0, "frame_height": 0}
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # Check raw grayscale first, which preserves natural contrast for small corner insets.
        raw_faces = _HAAR_FACE.detectMultiScale(
            gray,
            scaleFactor=1.05,
            minNeighbors=3,
            minSize=(16, 16),
        )
        # Low-resolution downloaded footage often has washed-out faces. A
        # modest equalization pass catches presenters without turning the
        # detector into a general object classifier.
        normalized = cv2.equalizeHist(gray)
        norm_faces = _HAAR_FACE.detectMultiScale(
            normalized,
            scaleFactor=1.05,
            minNeighbors=3,
            minSize=(18, 18),
        )
        combined = list(raw_faces)
        for nf in norm_faces:
            nx, ny, nw, nh = nf
            if not any(abs(nx - rx) < 20 and abs(ny - ry) < 20 for rx, ry, rw, rh in raw_faces):
                combined.append(nf)
        face_list = []
        total_face_area = 0
        for (x, y, fw, fh) in combined:
            face_list.append((int(x), int(y), int(fw), int(fh)))
            total_face_area += fw * fh
        frame_area = h * w
        coverage = total_face_area / frame_area if frame_area > 0 else 0.0
        return {
            "face_count": len(face_list),
            "faces": face_list,
            "face_coverage": round(coverage, 4),
            "frame_width": int(w),
            "frame_height": int(h),
        }
    except Exception:
        return {"face_count": 0, "faces": [], "face_coverage": 0.0, "frame_width": 0, "frame_height": 0}


def analyze_faces(frames: List[str]) -> Dict[str, any]:
    """Aggregate face detection across multiple frames.

    Returns dict with 'max_faces', 'avg_faces', 'face_coverage',
    'has_faces', and 'faces_stable' (same face appears in 60%+ frames).
    """
    if not frames:
        return {"max_faces": 0, "avg_faces": 0.0, "face_coverage": 0.0, "has_faces": False, "faces_stable": False}
    face_counts = []
    coverages = []
    for fp in frames:
        result = detect_faces(fp)
        face_counts.append(result["face_count"])
        coverages.append(result["face_coverage"])
    max_faces = max(face_counts) if face_counts else 0
    avg_faces = sum(face_counts) / len(face_counts) if face_counts else 0.0
    avg_coverage = sum(coverages) / len(coverages) if coverages else 0.0
    # Face is stable if present in 60%+ frames with consistent count
    frames_with_faces = sum(1 for c in face_counts if c > 0)
    has_faces = max_faces > 0
    faces_stable = has_faces and (frames_with_faces / len(frames)) >= 0.6
    return {
        "max_faces": max_faces,
        "avg_faces": round(avg_faces, 2),
        "face_coverage": round(avg_coverage, 4),
        "has_faces": has_faces,
        "faces_stable": faces_stable,
    }


def detect_talking_head(frames: List[str]) -> Dict[str, any]:
    """Detect a presenter-dominated shot without requiring a vision API.

    This intentionally targets the common talking-head composition: one
    persistent, centered, relatively large face across a mostly static shot.
    It does not reject incidental people in moving/archive footage.
    """
    result = {
        "talking_head": False,
        "talking_head_confidence": 0.0,
        "talking_head_reason": "no_stable_centered_face",
    }
    if cv2 is None or np is None or len(frames) < 2:
        result["talking_head_reason"] = "face_detector_unavailable"
        return result
    try:
        detections = [detect_faces(frame) for frame in frames]
        usable = [row for row in detections if row.get("faces")]
        if len(usable) < max(2, (len(frames) + 1) // 2):
            return result
        stable_ratio = len(usable) / float(len(frames))
        avg_faces = sum(len(row.get("faces") or []) for row in usable) / len(usable)
        centered = 0
        face_area_ratios = []
        for row in usable:
            faces = list(row.get("faces") or [])
            if not faces:
                continue
            # ``detect_faces`` samples 320px-wide frames, so normalize against
            # the largest detected face rather than the total face coverage.
            largest = max(faces, key=lambda face: int(face[2]) * int(face[3]))
            x, y, width, height = [float(value) for value in largest]
            frame_width = max(1.0, float(row.get("frame_width") or 320.0))
            frame_height = max(1.0, float(row.get("frame_height") or 180.0))
            center_x = (x + width / 2.0) / frame_width
            center_y = (y + height / 2.0) / frame_height
            if 0.24 <= center_x <= 0.76 and 0.12 <= center_y <= 0.82:
                centered += 1
            face_area_ratios.append((width * height) / (frame_width * frame_height))
        centered_ratio = centered / float(len(usable))
        avg_face_area = sum(face_area_ratios) / max(1, len(face_area_ratios))
        motion = measure_motion(frames)
        # Large stable face + centered composition is the primary signal. A
        # moderate motion allowance keeps walking/archival shots eligible.
        # When face is dominant (avg_face_area >= 0.035), allow false-positive
        # sub-face boxes (avg_faces) and shot-transition motion spikes so
        # presenters and mugshots are never missed.
        talking = (
            stable_ratio >= 0.67
            and centered_ratio >= 0.67
            and (avg_faces <= 2.0 or avg_face_area >= 0.035)
            and avg_face_area >= 0.018
            and (motion <= 48.0 or avg_face_area >= 0.035)
        )
        if talking:
            confidence = min(
                1.0,
                (stable_ratio * 0.35)
                + (centered_ratio * 0.35)
                + min(1.0, avg_face_area / 0.08) * 0.20
                + (0.10 if (motion <= 48.0 or avg_face_area >= 0.035) else 0.0),
            )
        else:
            confidence = min(
                0.35,
                (stable_ratio * 0.15)
                + (centered_ratio * 0.15)
                + min(1.0, avg_face_area / 0.08) * 0.05,
            )
        result.update({
            "talking_head": bool(talking),
            "talking_head_confidence": round(confidence, 3),
            "talking_head_reason": (
                "stable_centered_presenter" if talking else "face_composition_not_talking_head"
            ),
        })
        return result
    except Exception:
        result["talking_head_reason"] = "face_detector_error"
        return result


def talking_head_confidence_percent(value: Any) -> float:
    """Normalize detector/model confidence to the pipeline's 0..100 scale."""
    try:
        confidence = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if 0.0 < confidence <= 1.0:
        confidence *= 100.0
    return max(0.0, min(100.0, confidence))


def hard_talking_head_signal(
    talking_head: Any,
    confidence: Any = 0.0,
    reason: Any = "",
    *,
    enabled: bool = True,
) -> bool:
    """Return true only for a confident presenter, not an incidental face.

    Earlier builds treated every boolean ``talking_head`` result as a hard
    reject. That turned sports close-ups and archive footage into false
    webcam/reaction rejections. The hard lane now requires both a stable
    presenter composition (when a reason is supplied) and a configurable
    confidence threshold. Corner facecams are evaluated by the same
    confidence-aware hard-veto policy, so ambiguous edge faces remain eligible.
    """
    if not enabled or not bool(talking_head):
        return False
    normalized_reason = str(reason or "").strip().lower()
    # The detector may emit a high numeric score for a large incidental face
    # while explicitly classifying the composition as non-presenter. That
    # categorical result must remain authoritative for the hard reject lane.
    if normalized_reason in {"face_composition_not_talking_head", "face composition not talking head"}:
        return False
    if normalized_reason:
        presenter_keywords = (
            "stable_centered_presenter", "talking_head", "presenter", "anchor",
            "host", "interview", "commentator", "commentary", "speaker", "vlogger",
            "vlog", "reaction", "facecam", "talking head", "talking to camera"
        )
        if not any(kw in normalized_reason for kw in presenter_keywords):
            if any(inc in normalized_reason for inc in ("incidental", "crowd", "audience", "background", "b-roll", "broll")):
                return False
    try:
        threshold = float(
            os.environ.get(
                "VIDZEO_YT_TALKING_HEAD_HARD_CONFIDENCE",
                os.environ.get("VIDZEO_VISUAL_TALKING_HEAD_HARD_CONFIDENCE", "75"),
            ) or 75
        )
    except (TypeError, ValueError):
        threshold = 75.0
    threshold = max(0.0, min(100.0, threshold))
    return talking_head_confidence_percent(confidence) >= threshold


def detect_facecam_inset(frames: List[str]) -> Dict[str, any]:
    """Detect a persistent corner face/PiP camera in sampled video frames.

    A presenter detector intentionally looks for a large, centered face. That
    misses the common ``gameplay + streamer`` layout where the unrelated
    person is rendered as a small rectangle in a corner.  This detector is
    deliberately conservative: the same corner must contain a detectable
    face in at least half of the samples, and the face must be large enough to
    be a camera inset rather than a distant background pedestrian. The raw
    signal is retained for auditing; only a confidence-above-threshold signal
    is exposed as the hard ``facecam_inset`` veto.
    """
    result = {
        "facecam_inset": False,
        "facecam_detected": False,
        "facecam_confidence": 0.0,
        "facecam_reason": "no_persistent_corner_face",
        "facecam_corner": "",
    }
    if cv2 is None or np is None or len(frames) < 2:
        result["facecam_reason"] = "face_detector_unavailable"
        return result
    try:
        _load_haar_cascades()
        corner_candidates = {
            "top_left": [(0.02, 0.06, 0.26, 0.38), (0.06, 0.04, 0.30, 0.34)],
            "mid_left": [(0.02, 0.31, 0.26, 0.69), (0.06, 0.34, 0.30, 0.66)],
            "top_right": [(0.74, 0.06, 0.98, 0.38), (0.70, 0.04, 0.94, 0.34)],
            "mid_right": [(0.74, 0.31, 0.98, 0.69), (0.70, 0.34, 0.94, 0.66)],
            "bottom_left": [(0.02, 0.62, 0.26, 0.94), (0.06, 0.66, 0.30, 0.96)],
            "bottom_right": [(0.74, 0.62, 0.98, 0.94), (0.70, 0.66, 0.94, 0.96)],
        }
        corner_hits = {key: [] for key in corner_candidates}
        rect_hits = {key: [] for key in corner_candidates}
        # A circular facecam has no four-sided border for the rectangular
        # fallback below to lock onto. Keep a separate Hough-circle signal so
        # a presenter embedded in a round lower-corner inset cannot leak
        # through just because Haar missed the small face. It is only used
        # when the same circle persists across multiple samples.
        frame_corner_hits = []
        circle_hits = {key: [] for key in corner_candidates}
        multi_face_frames = 0
        for frame in frames:
            detection = detect_faces(frame)
            detected_faces = list(detection.get("faces") or [])
            if len(detected_faces) >= 2:
                multi_face_frames += 1
            frame_width = max(1.0, float(detection.get("frame_width") or 320.0))
            frame_height = max(1.0, float(detection.get("frame_height") or 180.0))
            frame_hits = {key: [] for key in corner_candidates}
            for face in detected_faces:
                x, y, width, height = [float(value) for value in face]
                cx = (x + width / 2.0) / frame_width
                cy = (y + height / 2.0) / frame_height
                area = (width * height) / (frame_width * frame_height)
                # A facecam face is visible but small and confined to true corners.
                # Ignore tiny noise and larger scene subjects / documentary interviewees.
                if area < 0.0018 or area > 0.055:
                    continue
                c = ""
                if cx <= 0.28:
                    if cy <= 0.30:
                        c = "top_left"
                    elif cy >= 0.70:
                        c = "bottom_left"
                    else:
                        c = "mid_left"
                elif cx >= 0.72:
                    if cy <= 0.30:
                        c = "top_right"
                    elif cy >= 0.70:
                        c = "bottom_right"
                    else:
                        c = "mid_right"
                if c:
                    frame_hits[c].append((area, cx, cy))
            frame_corner_hits.append(frame_hits)
            image = cv2.imread(str(frame))
            if image is not None:
                image = cv2.resize(image, (320, 180), interpolation=cv2.INTER_AREA)
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(float)
                for corner, candidates in corner_candidates.items():
                    best_rect_score = 0.0
                    for x1n, y1n, x2n, y2n in candidates:
                        x1, y1 = int(x1n * 320), int(y1n * 180)
                        x2, y2 = int(x2n * 320), int(y2n * 180)
                        border = 2
                        if x2 - x1 < 40 or y2 - y1 < 30:
                            continue
                        strips = (
                            gray[y1:y1 + border, x1:x2],
                            gray[y2 - border:y2, x1:x2],
                            gray[y1:y2, x1:x1 + border],
                            gray[y1:y2, x2 - border:x2],
                        )
                        neighbours = (
                            gray[max(0, y1 - border):y1, x1:x2],
                            gray[y2:min(180, y2 + border), x1:x2],
                            gray[y1:y2, max(0, x1 - border):x1],
                            gray[y1:y2, x2:min(320, x2 + border)],
                        )
                        edge_values = [
                            float(np.abs(a - b).mean())
                            for a, b in zip(strips, neighbours)
                            if a.size and b.size
                        ]
                        inner = gray[y1 + border:y2 - border, x1 + border:x2 - border]
                        if len(edge_values) != 4 or not inner.size:
                            continue
                        min_edge = min(edge_values)
                        mean_edge = sum(edge_values) / 4.0
                        edge_ratio = max(edge_values) / max(1.0, min_edge)
                        # Rectangular PiP boundaries are balanced on all four
                        # sides; a natural scene edge usually spikes one side.
                        if min_edge < 11.0 or mean_edge < 17.0 or edge_ratio > 3.6 or float(inner.std()) < 42.0:
                            continue
                        score = min(1.0, (min_edge / 22.0) * 0.55 + (mean_edge / 36.0) * 0.25 + (float(inner.std()) / 80.0) * 0.20)
                        best_rect_score = max(best_rect_score, score)
                    rect_hits[corner].append(best_rect_score)

                # Detect only medium/large circles anchored in a corner. The
                # radius and corner constraints avoid treating wheels, lamps,
                # and circular car badges in the main subject as PiP.
                try:
                    gray_u8 = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                    gray_u8 = cv2.medianBlur(gray_u8, 5)
                    min_radius = max(18, int(min(image.shape[:2]) * 0.075))
                    max_radius = max(min_radius + 4, int(min(image.shape[:2]) * 0.22))
                    circles = cv2.HoughCircles(
                        gray_u8,
                        cv2.HOUGH_GRADIENT,
                        dp=1.2,
                        minDist=max(18, int(min(image.shape[:2]) * 0.16)),
                        param1=100,
                        param2=42,
                        minRadius=min_radius,
                        maxRadius=max_radius,
                    )
                    circle_scores = {key: 0.0 for key in corner_candidates}
                    if circles is not None:
                        for cx, cy, radius in circles[0]:
                            nx, ny = float(cx) / 320.0, float(cy) / 180.0
                            radius_ratio = float(radius) / max(1.0, float(min(image.shape[:2])))
                            if radius_ratio < 0.075 or radius_ratio > 0.22:
                                continue
                            corner = (
                                "top_left" if nx <= 0.36 and ny <= 0.36 else
                                "bottom_left" if nx <= 0.36 and ny >= 0.64 else
                                "mid_left" if nx <= 0.36 else
                                "top_right" if nx >= 0.64 and ny <= 0.36 else
                                "bottom_right" if nx >= 0.64 and ny >= 0.64 else
                                "mid_right" if nx >= 0.64 else
                                ""
                            )
                            if corner:
                                circle_scores[corner] = max(
                                    circle_scores[corner],
                                    min(1.0, 0.72 + (radius_ratio - 0.075) * 1.5),
                                )
                    for corner in circle_hits:
                        circle_hits[corner].append(circle_scores[corner])
                except Exception:
                    for corner in circle_hits:
                        circle_hits[corner].append(0.0)
        sample_count = float(len(frames))
        best_corner = ""
        best_score = 0.0
        for corner in corner_candidates:
            corner_frame_pts = [frame_corner_hits[f_idx].get(corner, []) for f_idx in range(len(frames))]
            best_track_hits = 0
            best_track_area = 0.0
            for ref_f_idx in range(len(frames)):
                for ref_area, ref_cx, ref_cy in corner_frame_pts[ref_f_idx]:
                    track_hits = 1
                    track_areas = [ref_area]
                    for other_f_idx in range(len(frames)):
                        if other_f_idx == ref_f_idx:
                            continue
                        matching = [
                            a for a, ocx, ocy in corner_frame_pts[other_f_idx]
                            if abs(ocx - ref_cx) <= 0.065 and abs(ocy - ref_cy) <= 0.065
                        ]
                        if matching:
                            track_hits += 1
                            track_areas.append(matching[0])
                    if track_hits > best_track_hits:
                        best_track_hits = track_hits
                        best_track_area = sum(track_areas) / len(track_areas)

            presence = best_track_hits / sample_count
            if presence < 0.5:
                continue
            mean_area = best_track_area
            # Smaller, stable corner/docked faces are characteristic of insets
            if 0.002 <= mean_area <= 0.08:
                size_score = 1.0
            elif mean_area < 0.002:
                size_score = max(0.4, mean_area / 0.002)
            else:
                size_score = max(0.4, 1.0 - (mean_area - 0.08) / 0.08)
            score = min(1.0, presence * 0.70 + size_score * 0.30)
            if score > best_score:
                best_corner, best_score = corner, score
        if best_corner:
            # A corner face is useful evidence, but the old boolean gate made
            # every low-confidence corner face fatal. That caught ordinary
            # crowd shots, archival portraits, and people near the edge of a
            # frame as streamer PiP. Keep the raw signal for the audit while
            # making only a confident, persistent inset a hard veto.
            has_pip_border = bool(
                any(any(v >= 0.62 for v in vals) for vals in rect_hits.values())
                or any(any(v >= 0.70 for v in vals) for vals in circle_hits.values())
            )
            is_group_shot = multi_face_frames >= 2 and not has_pip_border
            try:
                hard_threshold = float(
                    os.environ.get(
                        "VIDZEO_YT_FACECAM_HARD_CONFIDENCE",
                        os.environ.get("VIDZEO_VISUAL_FACECAM_HARD_CONFIDENCE", "0.78"),
                    ) or 0.78
                )
            except (TypeError, ValueError):
                hard_threshold = 0.78
            if hard_threshold > 1.0:
                hard_threshold /= 100.0
            hard_threshold = max(0.50, min(0.98, hard_threshold))
            is_facecam = bool(best_score >= hard_threshold and not is_group_shot)
            result.update({
                "facecam_inset": is_facecam,
                "facecam_detected": True,
                "facecam_confidence": round(best_score, 3),
                "facecam_reason": "persistent_corner_face" if is_facecam else "group_shot_or_unconfirmed_corner_face",
                "facecam_corner": best_corner,
            })
        # OpenCV 5's slim builds do not expose Haar cascades. A persistent,
        # balanced corner rectangle is still strong evidence of a facecam/PiP
        # overlay and must be rejected before a clip reaches compositing.
        if not best_corner or best_score < 0.60:
            for corner, values in rect_hits.items():
                present = [value for value in values if value >= 0.62]
                presence = len(present) / sample_count
                if presence < 0.5:
                    continue
                confidence = min(1.0, (presence * 0.65) + (sum(present) / len(present)) * 0.35)
                if confidence > best_score:
                    best_corner, best_score = corner, confidence
            if best_corner:
                try:
                    hard_threshold = float(
                        os.environ.get(
                            "VIDZEO_YT_FACECAM_HARD_CONFIDENCE",
                            os.environ.get("VIDZEO_VISUAL_FACECAM_HARD_CONFIDENCE", "0.60"),
                        ) or 0.60
                    )
                except (TypeError, ValueError):
                    hard_threshold = 0.60
                if hard_threshold > 1.0:
                    hard_threshold /= 100.0
                hard_threshold = max(0.50, min(0.98, hard_threshold))
                result.update({
                    "facecam_inset": bool(best_score >= hard_threshold),
                    "facecam_detected": True,
                    "facecam_confidence": round(best_score, 3),
                    "facecam_reason": "persistent_corner_picture_in_picture",
                    "facecam_corner": best_corner,
                })
        # Circular PiP fallback. Require persistence, then apply the same
        # confidence threshold as the rectangular detector. This is a hard
        # contamination signal for extracted frames as well as full clips.
        if not best_corner or best_score < 0.60:
            for corner, values in circle_hits.items():
                present = [value for value in values if value >= 0.72]
                presence = len(present) / sample_count
                if presence < 0.5:
                    continue
                confidence = min(1.0, (presence * 0.65) + (sum(present) / len(present)) * 0.35)
                if confidence > best_score:
                    best_corner, best_score = corner, confidence
            if best_corner:
                try:
                    hard_threshold = float(
                        os.environ.get(
                            "VIDZEO_YT_FACECAM_HARD_CONFIDENCE",
                            os.environ.get("VIDZEO_VISUAL_FACECAM_HARD_CONFIDENCE", "0.60"),
                        ) or 0.60
                    )
                except (TypeError, ValueError):
                    hard_threshold = 0.60
                if hard_threshold > 1.0:
                    hard_threshold /= 100.0
                hard_threshold = max(0.50, min(0.98, hard_threshold))
                result.update({
                    "facecam_inset": bool(best_score >= hard_threshold),
                    "facecam_detected": True,
                    "facecam_confidence": round(best_score, 3),
                    "facecam_reason": "persistent_circular_picture_in_picture",
                    "facecam_corner": best_corner,
                })
        # Prefer an explicit circular-PiP label when both the rectangular and
        # circle heuristics fire. The frame-level screen uses this distinction
        # to avoid rejecting hard-edged engine bays while still vetoing the
        # round presenter inset seen in creator footage.
        circle_corner = ""
        circle_best_score = 0.0
        for corner, values in circle_hits.items():
            present = [value for value in values if value >= 0.72]
            presence = len(present) / sample_count
            if presence < 0.5:
                continue
            confidence = min(1.0, (presence * 0.65) + (sum(present) / len(present)) * 0.35)
            if confidence > circle_best_score:
                circle_corner, circle_best_score = corner, confidence
        if circle_corner and circle_best_score >= 0.72:
            result.update({
                "facecam_inset": True,
                "facecam_detected": True,
                "facecam_confidence": round(circle_best_score, 3),
                "facecam_reason": "persistent_circular_picture_in_picture",
                "facecam_corner": circle_corner,
            })
        return result
    except Exception:
        result["facecam_reason"] = "face_detector_error"
        return result


def detect_outro_endcard(frames: List[str]) -> Dict[str, Any]:
    """Detect channel outro slates, 'watch more' cards, and studio end cards.
    
    Outro cards frequently feature solid white (>38% white pixels) or solid black
    (>48% black pixels) backdrops with static rectangular video recommendation
    boxes or studio hosts. If the final sampled frames of a cut contain an outro
    slate, this detector flags it for rejection.
    """
    result = {
        "outro_detected": False,
        "outro_reason": "",
        "outro_confidence": 0.0,
    }
    if cv2 is None or np is None or not frames:
        return result
    try:
        # Check the tail frames (where outro cards live)
        tail_frames = frames[-2:] if len(frames) >= 2 else frames
        for f_path in tail_frames:
            img = cv2.imread(str(f_path))
            if img is None:
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            h, w = gray.shape[:2]
            # Exclude widescreen letterbox bands (top/bottom 12.5%) so cinematic 2.35:1
            # content at night isn't falsely flagged as an outro slate.
            core_top = int(h * 0.125) if h >= 64 else 0
            core_bottom = int(h * 0.875) if h >= 64 else h
            core_gray = gray[core_top:core_bottom, :] if (core_bottom > core_top) else gray
            white_ratio = float((gray > 230).mean())
            black_ratio = float((core_gray < 20).mean())
            if white_ratio >= 0.42:
                result["outro_detected"] = True
                result["outro_reason"] = f"high_white_outro_slate_ratio_{white_ratio:.2f}"
                result["outro_confidence"] = min(99.0, white_ratio * 180.0)
                return result
            if black_ratio >= 0.65:
                result["outro_detected"] = True
                result["outro_reason"] = f"high_black_outro_slate_ratio_{black_ratio:.2f}"
                result["outro_confidence"] = min(99.0, black_ratio * 180.0)
                return result
    except Exception as exc:
        result["outro_reason"] = f"outro_detector_error:{exc}"
    return result



def screen_extracted_frame(frame_path: str) -> Dict[str, Any]:
    """Screen one inherited YouTube frame for hard visual contamination.

    A parent clip can pass because its sampled moments are clean while one
    later timestamp contains a creator title card, presenter PiP, or a large
    burned-in graphic. Extracted frames therefore need their own inexpensive
    hard screen. This intentionally rejects only deterministic contamination;
    semantic uncertainty remains the responsibility of the parent clip gate.
    """
    result: Dict[str, Any] = {
        "checked": False,
        "accepted": True,
        "rejection_reason": "",
        "facecam_inset": False,
        "overlay_box": None,
    }
    if not frame_path or not os.path.isfile(str(frame_path)):
        return result
    try:
        # Repeating the still lets persistence-based analyzers work without
        # pretending that one observation is temporal evidence.
        frames = [str(frame_path)] * 4
        facecam = detect_facecam_inset(frames) or {}
        result["facecam_inset"] = bool(facecam.get("facecam_inset"))
        facecam_reason = str(facecam.get("facecam_reason") or "")
        # The rectangular fallback is intentionally conservative for full
        # clips but can mistake hard-edged engine bays, dashboards, or framed
        # archive shots for PiP when applied to one still. Trust the frame
        # screen only for a detected face or the new circular-PiP signal.
        hard_frame_facecam = facecam_reason in {
            "persistent_corner_face",
            "persistent_circular_picture_in_picture",
        }
        if result["facecam_inset"] and hard_frame_facecam:
            result.update({
                "checked": True,
                "accepted": False,
                "rejection_reason": "frame_facecam_inset_rejected",
                "facecam_confidence": float(facecam.get("facecam_confidence") or 0.0),
                "facecam_corner": str(facecam.get("facecam_corner") or ""),
            })
            return result

        # Catch oversized creator title cards and lower-thirds missed by the
        # narrow subtitle detector. Small logos and normal archival labels
        # remain eligible.
        top_text_box = _detect_top_creator_text_box(str(frame_path))
        if top_text_box and (
            (float(top_text_box[2]) - float(top_text_box[0]))
            * (float(top_text_box[3]) - float(top_text_box[1])) > 0.12
        ):
            result.update({
                "checked": True,
                "accepted": False,
                "rejection_reason": "frame_large_text_overlay_rejected",
                "overlay_box": list(top_text_box),
            })
            return result
        overlay_box = detect_persistent_text_overlay_box(frames, min_y=0.20)
        full_overlay_box = detect_persistent_text_overlay_box(frames, min_y=0.0)
        if full_overlay_box:
            if not overlay_box:
                overlay_box = full_overlay_box
            else:
                overlay_area = max(0.0, float(overlay_box[2]) - float(overlay_box[0])) * max(
                    0.0, float(overlay_box[3]) - float(overlay_box[1])
                )
                full_area = max(0.0, float(full_overlay_box[2]) - float(full_overlay_box[0])) * max(
                    0.0, float(full_overlay_box[3]) - float(full_overlay_box[1])
                )
                if full_area > overlay_area:
                    overlay_box = full_overlay_box
        result["overlay_box"] = list(overlay_box) if overlay_box else None
        if overlay_box and len(overlay_box) == 4:
            x1, y1, x2, y2 = [float(value) for value in overlay_box]
            area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
            width = max(0.0, x2 - x1)
            # Keep small source captions/labels; reject text that materially
            # obscures the subject, with the same thresholds as the clip gate.
            if (
                (area > 0.12 and y1 < 0.45 and y2 > 0.35)
                or area > 0.24
            ):
                result.update({
                    "checked": True,
                    "accepted": False,
                    "rejection_reason": "frame_large_text_overlay_rejected",
                })
                return result
        result["checked"] = True
        return result
    except Exception as exc:
        # A detector failure must not make relaxed YouTube recall fail. The
        # parent clip verification remains the source of semantic evidence.
        result["checked"] = False
        result["detector_error"] = type(exc).__name__
        return result


def _detect_top_creator_text_box(frame_path: str) -> Optional[Tuple[float, float, float, float]]:
    """Find a large, persistent-style chapter/title banner in one frame.

    This is deliberately limited to the top 18% and requires many compact
    high-contrast glyph components spanning a substantial horizontal run. It
    therefore avoids treating a bright sky or a small station/car label as a
    creator overlay while catching the common all-caps chapter bars found in
    repackaged YouTube footage.
    """
    if cv2 is None or np is None or not frame_path:
        return None
    try:
        image = cv2.imread(str(frame_path))
        if image is None:
            return None
        height, width = image.shape[:2]
        if width < 160 or height < 90:
            return None
        top_height = max(1, int(height * 0.18))
        gray = cv2.cvtColor(image[:top_height, :], cv2.COLOR_BGR2GRAY)
        mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)[1]
        _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask)
        components: list[tuple[int, int, int, int, int]] = []
        for x, y, box_width, box_height, area in stats[1:]:
            if not (
                4 <= box_height <= max(8, int(height * 0.08))
                and 2 <= box_width <= max(16, int(width * 0.20))
                and 8 <= area <= max(64, int(width * height * 0.02))
                and y <= int(height * 0.12)
            ):
                continue
            components.append((int(x), int(y), int(box_width), int(box_height), int(area)))
        if len(components) < 14:
            return None
        x1 = min(item[0] for item in components)
        y1 = min(item[1] for item in components)
        x2 = max(item[0] + item[2] for item in components)
        y2 = max(item[1] + item[3] for item in components)
        span = x2 - x1
        ink_area = sum(item[4] for item in components)
        if span < width * 0.55 or ink_area < width * height * 0.006:
            return None
        return (
            round(x1 / width, 4),
            round(y1 / height, 4),
            round(x2 / width, 4),
            round(y2 / height, 4),
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Analyzer 9 — Object detection (YOLOv8 Nano, optional)
# ---------------------------------------------------------------------------

_YOLO_MODEL = None
_YOLO_AVAILABLE = False


def _load_yolo():
    """Lazy-load YOLOv8 Nano model. Returns True if available."""
    global _YOLO_MODEL, _YOLO_AVAILABLE
    if _YOLO_MODEL is not None or _YOLO_AVAILABLE:
        return _YOLO_MODEL is not None
    try:
        from ultralytics import YOLO
        import torch
        # Force CPU, small model
        _YOLO_MODEL = YOLO("yolov8n.pt")
        _YOLO_AVAILABLE = True
        return True
    except Exception:
        _YOLO_AVAILABLE = False
        return False


def detect_objects(frame_path: str, confidence: float = 0.3) -> Dict[str, any]:
    """Detect objects using YOLOv8 Nano.

    Returns dict with 'objects' list of {class, confidence, bbox} and
    'object_count', 'unique_classes'.
    """
    if not _load_yolo() or _YOLO_MODEL is None:
        return {"objects": [], "object_count": 0, "unique_classes": [], "available": False}
    try:
        results = _YOLO_MODEL(frame_path, conf=confidence, device="cpu", verbose=False)
        objects = []
        classes_seen = set()
        for r in results:
            if r.boxes is not None:
                for box in r.boxes:
                    cls_id = int(box.cls[0])
                    cls_name = _YOLO_MODEL.names.get(cls_id, f"cls_{cls_id}")
                    conf = float(box.conf[0])
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    objects.append({
                        "class": cls_name,
                        "confidence": round(conf, 3),
                        "bbox": [round(x1), round(y1), round(x2), round(y2)],
                    })
                    classes_seen.add(cls_name)
        return {
            "objects": objects,
            "object_count": len(objects),
            "unique_classes": list(classes_seen),
            "available": True,
        }
    except Exception:
        return {"objects": [], "object_count": 0, "unique_classes": [], "available": True}


def analyze_objects(frames: List[str]) -> Dict[str, any]:
    """Aggregate object detection across multiple frames.

    Returns dict with 'total_objects', 'avg_objects', 'dominant_classes',
    'scene_type' guess, 'available'.
    """
    if not frames:
        return {"total_objects": 0, "avg_objects": 0.0, "dominant_classes": [], "scene_type": "unknown", "available": False}
    all_objects = []
    all_classes = []
    for fp in frames:
        result = detect_objects(fp)
        if not result.get("available", False):
            return {"total_objects": 0, "avg_objects": 0.0, "dominant_classes": [], "scene_type": "unknown", "available": False}
        all_objects.extend(result["objects"])
        all_classes.extend(result["unique_classes"])
    # Count class frequency
    class_freq = {}
    for cls in all_classes:
        class_freq[cls] = class_freq.get(cls, 0) + 1
    dominant = sorted(class_freq.keys(), key=lambda c: class_freq[c], reverse=True)[:5]
    # Guess scene type from dominant classes
    scene_type = "general"
    if any(c in dominant for c in ["person", "people"]):
        scene_type = "people"
    elif any(c in dominant for c in ["car", "truck", "bus", "motorcycle", "bicycle"]):
        scene_type = "traffic"
    elif any(c in dominant for c in ["dog", "cat", "bird", "horse"]):
        scene_type = "animals"
    elif any(c in dominant for c in ["tv", "laptop", "monitor", "cell phone"]):
        scene_type = "indoor"
    avg_per_frame = len(all_objects) / len(frames) if frames else 0.0
    return {
        "total_objects": len(all_objects),
        "avg_objects": round(avg_per_frame, 2),
        "dominant_classes": dominant,
        "scene_type": scene_type,
        "available": True,
    }


# ---------------------------------------------------------------------------
# Analyzer 10 — FFmpeg blur cleaning (watermarks + subtitles)
# ---------------------------------------------------------------------------

def apply_overlay_blur(
    input_path: str,
    output_path: str,
    regions: List[Dict],
    *,
    max_logo_area: float = 0.12,
    max_caption_area: float = 0.42,
    cancellation: Any = None,
) -> bool:
    """Blur verified overlay boxes in one encode with feathered boundaries.

    This cleanup is intentionally opt-in at the call site and only accepts
    bounded, confidence-backed regions. Keeping the compositor live matters:
    a rejected overlay must be surfaced to the resolver rather than silently
    pretending that the source was cleaned.
    """
    try:
        vw, vh, fps = _video_meta(input_path)
        if vw <= 0 or vh <= 0:
            return False
        boxes = []
        for raw in list(regions or [])[:6]:
            if not isinstance(raw, dict):
                continue
            bbox = raw.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            try:
                x1, y1, x2, y2 = [max(0.0, min(1.0, float(v))) for v in bbox]
            except Exception:
                continue
            area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
            kind = str(raw.get("type") or "other").lower()
            is_caption = any(k in kind for k in ("caption", "subtitle", "subtitles", "text_overlay"))
            limit = max_caption_area if is_caption else max_logo_area
            confidence = float(raw.get("confidence") or 0.0)
            if x2 <= x1 or y2 <= y1 or area <= 0 or area > limit or (confidence > 0 and confidence < 15.0):
                continue
            # A full-width lower-third is usually a baked news ticker or a
            # large presenter caption. Blurring that band destroys faces and
            # readable context, so reject the cut and let retrieval choose a
            # clean alternate instead of running an expensive encode.
            if is_caption and (x2 - x1) >= 0.90 and area >= 0.30:
                continue
            # Blurrr alignment: only blur genuine overlay zones (corner watermarks,
            # top chapter bands, or lower-third captions). Never blur central gameplay
            # HUD, vehicles, or scene graphics.
            is_corner = (x1 <= 0.35 or x2 >= 0.65) and (y1 <= 0.35 or y2 >= 0.65)
            is_top_band = y1 <= 0.28 and (x2 - x1) >= 0.10 and (y2 - y1) <= 0.25
            is_lower_third = y1 >= 0.45 or (y2 >= 0.60 and (y2 - y1) <= 0.38)
            if not is_caption and not is_corner and not is_top_band:
                continue
            if is_caption and not is_lower_third and not is_top_band:
                continue
            # Expand the detected box before masking. Generous proportional
            # margin gives the feathered mask room to fall off naturally and
            # completely obscure the caption glyphs and outlines.
            if is_caption:
                pad_x = max(12, int(vw * 0.025))
                pad_y = max(10, int(vh * 0.025))
            else:
                pad_x = max(8, int(vw * 0.012))
                pad_y = max(7, int(vh * 0.016))
            px1 = max(0, int(x1 * vw) - pad_x)
            py1 = max(0, int(y1 * vh) - pad_y)
            px2 = min(vw, int(x2 * vw) + pad_x)
            py2 = min(vh, int(y2 * vh) + pad_y)
            px1 -= px1 % 2
            py1 -= py1 % 2
            width = max(2, (px2 - px1) - ((px2 - px1) % 2))
            height = max(2, (py2 - py1) - ((py2 - py1) % 2))
            boxes.append((px1, py1, min(vw - px1, width), min(vh - py1, height), is_caption))
        if not boxes:
            return False
        # Build one feathered mask for all accepted regions and merge a single
        # full-frame blur underneath it. Using planes=1 restricts the blend to
        # luma and prevents FFmpeg's format=gray->yuv420p neutral chroma (128)
        # from smearing chroma across the entire frame.
        blur_radius = 28
        feather = max(8, min(24, int(min(vw, vh) * 0.025)))
        duration = max(0.1, _video_duration(input_path))
        mask_fps = max(1.0, float(fps or 30.0))
        mask_draws = []
        for x, y, width, height, _is_caption in boxes:
            mask_draws.append(
                f"drawbox=x={x}:y={y}:w={width}:h={height}:"
                "color=white@1:t=fill"
            )
        # These drawboxes operate on the generated mask stream and therefore
        # must be chained with commas.  Semicolons create a new filtergraph
        # with an unlabeled input, which FFmpeg rejects at runtime (the old
        # graph passed unit tests that only inspected its text).
        mask_chain = ",".join(mask_draws)
        filters = [
            f"[0:v]split=2[base][blurbase]",
            f"[blurbase]boxblur=luma_radius={blur_radius}:luma_power=3:"
            f"chroma_radius={blur_radius}:chroma_power=3[blurred]",
            f"color=c=black:s={vw}x{vh}:r={mask_fps:.3f}:d={duration:.6f},format=gray,{mask_chain},"
            f"boxblur={feather}:1[mask]",
            "[base][blurred][mask]maskedmerge=planes=1[outv]",
        ]
        cmd = [
            _media_bin("ffmpeg"), "-y", "-i", input_path,
            "-filter_complex", ";".join(filters),
            "-map", "[outv]", "-map", "0:a?",
            # LAVIP is a short cleanup pass on already-verified clips.  The
            # previous ``fast`` encode could spend the full 45s watchdog on a
            # 5–7s 1080p cut, starving the YouTube lane.  Ultrafast keeps the
            # blur deterministic and bounded; the final master is re-encoded
            # by the production renderer anyway.
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-c:a", "copy",
            "-movflags", "+faststart", output_path,
        ]
        # Overlay cleanup is optional; it must not hold a YouTube worker for
        # three minutes while the main render is waiting.
        try:
            lavip_timeout = max(
                8.0,
                min(60.0, float(os.environ.get("VIDZEO_LAVIP_TIMEOUT_SECONDS", "30") or 30)),
            )
        except (TypeError, ValueError):
            lavip_timeout = 30.0
        if cancellation is None:
            # Preserve the small test/integration doubles that model the
            # legacy two-argument helper; production cancellation still uses
            # the explicit keyword below.
            ok, _ = _run_ffmpeg(cmd, timeout=lavip_timeout)
        else:
            ok, _ = _run_ffmpeg(cmd, timeout=lavip_timeout, cancellation=cancellation)
        return bool(ok and os.path.exists(output_path) and os.path.getsize(output_path) > 0)
    except Exception as exc:
        print(f"   [lavip] overlay blur failed: {exc}")
        return False


def apply_watermark_blur(input_path: str, output_path: str,
                         watermark_region: str, frames: List[str]) -> bool:
    """Apply the feathered overlay compositor to a detected watermark."""
    if not watermark_region or watermark_region not in _CORNERS:
        return False
    if not frames:
        return False
    box = detect_watermark_box(frames, watermark_region)
    if not box:
        return False
    return apply_overlay_blur(
        input_path,
        output_path,
        [{"type": "watermark", "bbox": list(box), "confidence": 100.0}],
    )


def apply_subtitle_blur(
    input_path: str,
    output_path: str,
    region: Optional[Tuple[float, float, float, float]] = None,
) -> bool:
    """Apply a feathered blur only to a verified, tight subtitle box."""
    if not region:
        return False
    return apply_overlay_blur(
        input_path,
        output_path,
        [{"type": "subtitle", "bbox": list(region), "confidence": 100.0}],
    )


def clean_clip(input_path: str, has_watermark: bool, watermark_region: str,
               has_subtitle: bool, frames: List[str], overlay_regions: Optional[List[Dict]] = None) -> Optional[str]:
    """Clean a clip by applying watermark and/or subtitle blurs.

    Returns path to cleaned file, or None if cleaning failed.
    Original file is NOT modified.
    """
    if not has_watermark and not has_subtitle:
        return input_path
    temp_dir = None
    try:
        # Create temp output path
        base, ext = os.path.splitext(input_path)
        cleaned_path = f"{base}_cleaned{ext}"
        temp_dir = tempfile.mkdtemp(prefix="lavip_clean_")
        temp_output = os.path.join(temp_dir, f"cleaned{ext}")
        if overlay_regions and apply_overlay_blur(input_path, temp_output, overlay_regions):
            shutil.move(temp_output, cleaned_path)
            return cleaned_path
        subtitle_box = detect_subtitle_box(frames) if has_subtitle else None
        # Apply watermarks first
        if has_watermark and watermark_region:
            if apply_watermark_blur(input_path, temp_output, watermark_region, frames):
                # Then apply subtitle blur if needed
                if has_subtitle:
                    final_path = os.path.join(temp_dir, f"final{ext}")
                    if apply_subtitle_blur(temp_output, final_path, subtitle_box):
                        # Move to permanent location
                        shutil.move(final_path, cleaned_path)
                        os.remove(temp_output)
                        return cleaned_path
                else:
                    shutil.move(temp_output, cleaned_path)
                    return cleaned_path
        elif has_subtitle:
            if subtitle_box and apply_subtitle_blur(input_path, temp_output, subtitle_box):
                shutil.move(temp_output, cleaned_path)
                return cleaned_path
        return None
    except Exception as e:
        print(f"   [lavip] cleaning failed: {e}")
        return None
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
