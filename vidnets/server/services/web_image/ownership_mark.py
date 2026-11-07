"""Offline OCR check for visible ownership marks in web stills."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
import threading
import hashlib


_OCR_CALL_LOCK = threading.Lock()
_CONTENT_RESULTS = {}
_DOMAIN = re.compile(r"\b(?:www\.)?[a-z0-9][a-z0-9-]{2,}\.(?:com|net|org|co|io|photo|photography)\b", re.I)
_OWNERSHIP = re.compile(r"(?:copyright|watermark|shutterstock|istock|alamy|dreamstime|gettyimages?|depositphotos)", re.I)


def _is_ownership_mark(text: str, polygon: object, width: int, height: int) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if _OWNERSHIP.search(value) or "©" in value:
        return True
    try:
        points = list(polygon or [])
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        center_x = (min(xs) + max(xs)) / 2.0 / max(1, width)
        center_y = (min(ys) + max(ys)) / 2.0 / max(1, height)
    except (TypeError, ValueError, IndexError):
        return False
    if not _DOMAIN.search(value):
        # Publishers also stamp plain brand names without a URL. Restrict this
        # to thin, multiword uppercase footer overlays; ordinary signs and
        # inscriptions elsewhere in the photograph remain scene content.
        words = re.findall(r"[A-Za-z]+", value)
        return (
            len(words) >= 3 and value.upper() == value
            and center_y >= 0.94
            and (max(xs)-min(xs))/max(1,width) >= 0.15
            and (max(ys)-min(ys))/max(1,height) <= 0.035
        )
    # A URL on a sign in the middle of a documentary photograph is scene
    # content. A URL stamped into an edge or corner is an ownership overlay.
    return center_y <= 0.18 or center_y >= 0.82 or center_x <= 0.12 or center_x >= 0.88


@lru_cache(maxsize=512)
def _inspect_cached(path: str, size: int, modified_ns: int, changed_ns: int = 0) -> dict:
    try:
        import numpy as np
        from PIL import Image
        from ..scene_understanding.frame_overlay_blur import _get_rapidocr

        engine = _get_rapidocr()
        if not engine:
            return {"checked": False, "watermark": False, "reason": "ocr_unavailable"}
        digest = hashlib.sha256()
        with open(path, 'rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(block)
        content_key = digest.hexdigest()
        with _OCR_CALL_LOCK:
            if content_key in _CONTENT_RESULTS:
                return dict(_CONTENT_RESULTS[content_key])
        with Image.open(path) as source:
            image = source.convert("RGB")
            image.thumbnail((1600, 1600))
            width, height = image.size
            pixels = np.asarray(image)
        with _OCR_CALL_LOCK:
            if content_key in _CONTENT_RESULTS:
                return dict(_CONTENT_RESULTS[content_key])
            rows, _timing = engine(pixels)
            result = {"checked": True, "watermark": False, "reason": "no_ownership_text"}
            for row in rows or []:
                if not isinstance(row, (list, tuple)) or len(row) < 3:
                    continue
                confidence = float(row[2] or 0.0)
                if confidence < 0.58:
                    continue
                if _is_ownership_mark(str(row[1]), row[0], width, height):
                    result = {
                        "checked": True, "watermark": True, "reason": "ownership_text_ocr",
                        "text": str(row[1])[:100], "confidence": round(confidence, 3),
                    }
                    break
            if len(_CONTENT_RESULTS) >= 128:
                _CONTENT_RESULTS.clear()
            _CONTENT_RESULTS[content_key] = dict(result)
            return result
    except Exception as exc:
        return {"checked": False, "watermark": False, "reason": f"ocr_error:{type(exc).__name__}"}


def inspect_ownership_mark(path: str) -> dict:
    try:
        target = Path(path)
        stat = target.stat()
        return dict(_inspect_cached(str(target.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    except (OSError, TypeError, ValueError):
        return {"checked": False, "watermark": False, "reason": "missing_image"}
