"""Frame-level caption and watermark cleanup.

This is the production-safe part of the standalone Watermark Remover tool
ported into Vidnets Studio.  The standalone app used a binary mask followed by
mask dilation, Gaussian feathering, and a soft blur blend.  This module keeps
that behavior local to the packaged app and deliberately works on extracted
frames (JPEG/PNG), where a full video encode would be wasteful.

The detector is intentionally bounded: captions are searched in the lower
third and narrow title bands, while logo candidates are limited to compact
corner text-like regions.  It does not blur arbitrary central objects or use a
fallback full-frame rectangle when detection is uncertain.
"""
from __future__ import annotations

import os
import threading
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # The desktop runtime includes OpenCV, but keep imports optional.
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - exercised only on minimal runtimes
    cv2 = None  # type: ignore
    np = None  # type: ignore


DEFAULT_CONFIDENCE_THRESHOLD = 0.40
DEFAULT_BOX_EXPANSION_RATIO = 1.60
DEFAULT_MASK_DILATION_PX = 5
DEFAULT_FEATHER_PX = 6
DEFAULT_BLUR_RADIUS_PX = 53

# RapidOCR is deliberately optional at import time.  The production runtime
# can ship its ONNX files locally, but a source checkout without the package
# must still be able to clean frames with the OpenCV morphology fallback.  The
# engine is created once per worker and never downloads anything.
_RAPIDOCR_ENGINE: Any = None
_RAPIDOCR_LOCK = threading.Lock()


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def overlay_clean_output_path(frame_path: str) -> str:
    """Return a deterministic sidecar path without overwriting the context frame."""
    path = Path(str(frame_path))
    return str(path.with_name(f"{path.stem}.overlay_clean{path.suffix or '.jpg'}"))


def _region(
    kind: str,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    confidence: float,
    method: str,
    polygon: Optional[Sequence[Sequence[float]]] = None,
) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "type": str(kind),
        "bbox": [
            round(_clamp(x1), 4),
            round(_clamp(y1), 4),
            round(_clamp(x2), 4),
            round(_clamp(y2), 4),
        ],
        "confidence": round(_clamp(confidence) * 100.0, 2),
        "method": str(method),
    }
    if polygon:
        normalized_polygon = [
            [round(_clamp(float(point[0])), 5), round(_clamp(float(point[1])), 5)]
            for point in polygon
            if isinstance(point, (list, tuple)) and len(point) >= 2
        ]
        if len(normalized_polygon) >= 3:
            item["polygon"] = normalized_polygon
            item["polygons"] = [normalized_polygon]
    return item


def _contour_candidates(
    binary: Any,
    *,
    width: int,
    height: int,
    y_offset: int,
    x_offset: int = 0,
    kind: str,
    min_width_ratio: float,
    max_height_ratio: float,
    min_aspect: float,
    method: str,
    max_candidates: int = 4,
) -> List[Dict[str, Any]]:
    """Convert text-like connected components into bounded normalized boxes."""
    if cv2 is None or np is None or binary is None:
        return []
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    found: List[Tuple[float, Dict[str, Any]]] = []
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        if box_width <= 0 or box_height <= 0:
            continue
        aspect = box_width / max(1.0, float(box_height))
        area = float(box_width * box_height)
        density = cv2.countNonZero(binary[y:y + box_height, x:x + box_width]) / max(1.0, area)
        if box_width < width * min_width_ratio:
            continue
        if box_height < max(5, int(height * 0.008)) or box_height > height * max_height_ratio:
            continue
        if aspect < min_aspect or density < 0.025 or density > 0.94:
            continue
        # Compact horizontal text gets a higher score than a broad bright
        # region.  The score is only used to select candidates; it is not a
        # claim that OCR understood the text.
        confidence = 0.38
        confidence += min(0.26, (box_width / max(1.0, width)) * 0.34)
        confidence += min(0.18, max(0.0, 1.0 - abs(density - 0.28)) * 0.18)
        confidence += min(0.12, max(0.0, (aspect - min_aspect) / 24.0))
        contour_points = contour.reshape(-1, 2).astype(np.float32)
        contour_points[:, 0] += float(x_offset)
        contour_points[:, 1] += float(y_offset)
        normalized = _region(
            kind,
            (x + x_offset) / width,
            (y + y_offset) / height,
            (x + x_offset + box_width) / width,
            (y + y_offset + box_height) / height,
            confidence,
            method,
            polygon=(contour_points / np.array([width, height], dtype=np.float32)).tolist(),
        )
        found.append((confidence, normalized))
    found.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in found[:max_candidates]]


def _merge_regions(regions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge overlapping detections so a caption is blurred once, not twice."""
    merged: List[Dict[str, Any]] = []
    for incoming in regions:
        bbox = incoming.get("bbox") if isinstance(incoming, dict) else None
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        x1, y1, x2, y2 = [float(value) for value in bbox]
        if x2 <= x1 or y2 <= y1:
            continue
        matched = None
        for current in merged:
            cx1, cy1, cx2, cy2 = [float(value) for value in current["bbox"]]
            ix1, iy1 = max(x1, cx1), max(y1, cy1)
            ix2, iy2 = min(x2, cx2), min(y2, cy2)
            intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
            union = (x2 - x1) * (y2 - y1) + (cx2 - cx1) * (cy2 - cy1) - intersection
            same_band = abs(((y1 + y2) / 2.0) - ((cy1 + cy2) / 2.0)) <= 0.075
            if union > 0 and (intersection / union >= 0.28 or (same_band and intersection > 0)):
                matched = current
                current["bbox"] = [
                    round(min(x1, cx1), 4), round(min(y1, cy1), 4),
                    round(max(x2, cx2), 4), round(max(y2, cy2), 4),
                ]
                current["confidence"] = max(
                    float(current.get("confidence") or 0.0),
                    float(incoming.get("confidence") or 0.0),
                )
                incoming_polygons = incoming.get("polygons") or (
                    [incoming.get("polygon")] if incoming.get("polygon") else []
                )
                if incoming_polygons:
                    current.setdefault("polygons", [])
                    current["polygons"].extend(
                        polygon for polygon in incoming_polygons
                        if isinstance(polygon, (list, tuple)) and len(polygon) >= 3
                    )
                if not incoming_polygons:
                    # Stationary-edge analyzers return a normalized bbox rather
                    # than a DBNet polygon. Preserve it separately so a later
                    # OCR polygon merge cannot silently discard the logo.
                    current.setdefault("bbox_polygons", []).append([
                        [x1, y1], [x2, y1], [x2, y2], [x1, y2],
                    ])
                break
        if matched is None:
            item = dict(incoming)
            if not item.get("polygons") and item.get("bbox"):
                bx1, by1, bx2, by2 = [float(value) for value in item["bbox"]]
                item["bbox_polygons"] = [[[bx1, by1], [bx2, by1], [bx2, by2], [bx1, by2]]]
            merged.append(item)
    return merged


def _get_rapidocr() -> Any:
    """Return the locally packaged RapidOCR engine, if available.

    No installer/network work happens here.  Import failure simply selects the
    deterministic OpenCV detector below.  ``RapidOCR`` caches the ONNX models
    in its installed package, so this remains offline at frame-processing time.
    """
    global _RAPIDOCR_ENGINE
    if _RAPIDOCR_ENGINE is not None:
        return _RAPIDOCR_ENGINE
    with _RAPIDOCR_LOCK:
        if _RAPIDOCR_ENGINE is not None:
            return _RAPIDOCR_ENGINE
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore

            _RAPIDOCR_ENGINE = RapidOCR(
                print_verbose=False,
                use_angle_cls=False,
                text_score=0.45,
                # Multiple OCR sessions defaulted to independent host-wide
                # thread pools, starving retrieval and render workers. Keep
                # the same models/resolution with bounded CPU parallelism.
                intra_op_num_threads=2,
                inter_op_num_threads=1,
            )
        except Exception:
            _RAPIDOCR_ENGINE = False
    return _RAPIDOCR_ENGINE


def _rapidocr_candidates(
    image: Any,
    *,
    width: int,
    height: int,
    confidence_threshold: float,
    max_candidates: int = 8,
) -> List[Dict[str, Any]]:
    """Detect overlay text using a local RapidOCR DBNet polygon detector.

    OCR is used for geometry, not semantic acceptance.  We only admit text in
    common overlay zones: lower-third captions, top title bands, and compact
    corners.  This prevents signs, jerseys, or scene text in the central frame
    from being blurred accidentally.
    """
    engine = _get_rapidocr()
    if not engine or image is None:
        return []
    detect_image = image
    scale_x = scale_y = 1.0
    try:
        max_side = max(256, int(os.environ.get("VIDZEO_FRAME_BLUR_DETECT_SIDE", "640") or 640))
        image_h, image_w = image.shape[:2]
        if max(image_h, image_w) > max_side:
            scale = max_side / float(max(image_h, image_w))
            detect_w = max(32, int(image_w * scale) // 32 * 32)
            detect_h = max(32, int(image_h * scale) // 32 * 32)
            detect_image = cv2.resize(image, (detect_w, detect_h), interpolation=cv2.INTER_AREA)
            scale_x = image_w / float(detect_w)
            scale_y = image_h / float(detect_h)
    except Exception:
        detect_image = image
        scale_x = scale_y = 1.0
    try:
        # Use DBNet geometry only.  Calling ``RapidOCR(image)`` would also run
        # the recognizer and is needlessly expensive for masking; Blurrr's
        # production path only needs detector polygons.
        detector = getattr(engine, "text_detector", None)
        if detector is not None:
            detector.postprocess_op.box_thresh = max(0.20, float(confidence_threshold))
            detector.postprocess_op.unclip_ratio = DEFAULT_BOX_EXPANSION_RATIO
            raw_result, _elapsed = detector(detect_image)
            raw_result = [
                (box.tolist() if hasattr(box, "tolist") else box, "", 1.0)
                for box in (raw_result if raw_result is not None else [])
            ]
        else:
            raw_result, _timing = engine(detect_image, text_score=max(0.20, float(confidence_threshold)))
    except Exception:
        return []
    if not raw_result:
        return []
    found: List[Tuple[float, Dict[str, Any]]] = []
    for entry in raw_result:
        if not isinstance(entry, (list, tuple)) or len(entry) < 1:
            continue
        raw_polygon = entry[0]
        if not isinstance(raw_polygon, (list, tuple)) or len(raw_polygon) < 4:
            continue
        try:
            points = np.asarray(raw_polygon, dtype=np.float32).reshape(-1, 2)
            if points.shape[0] < 4:
                continue
            points[:, 0] *= scale_x
            points[:, 1] *= scale_y
            score = float(entry[2]) if len(entry) > 2 else 0.0
        except Exception:
            continue
        if score < max(0.20, float(confidence_threshold)):
            continue
        x1, y1 = np.min(points, axis=0)
        x2, y2 = np.max(points, axis=0)
        if x2 <= x1 or y2 <= y1:
            continue
        nx1, ny1, nx2, ny2 = x1 / width, y1 / height, x2 / width, y2 / height
        box_w, box_h = nx2 - nx1, ny2 - ny1
        center_x, center_y = (nx1 + nx2) / 2.0, (ny1 + ny2) / 2.0
        # OCR often finds a short title in the body of footage.  Keep the
        # central 20–55% untouched; only known overlay bands are eligible.
        if center_y >= 0.55 and box_w >= 0.07 and box_h <= 0.20:
            kind = "caption"
        elif center_y <= 0.28 and box_w >= 0.10 and box_h <= 0.18:
            kind = "text_overlay"
        elif (center_x <= 0.24 or center_x >= 0.76) and box_w <= 0.30 and box_h <= 0.16:
            kind = "watermark"
        else:
            continue
        # OCR confidence is already a probability; add a small geometry prior
        # so tiny isolated glyphs do not win over a real caption line.
        confidence = _clamp(score * 0.84 + min(0.16, box_w * 0.35))
        polygon = [[float(x) / width, float(y) / height] for x, y in points]
        found.append((confidence, _region(
            kind, nx1, ny1, nx2, ny2, confidence, "rapidocr_dbnet_polygon", polygon=polygon
        )))
    found.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in found[:max_candidates]]


def detect_overlay_regions(
    frame_path: str,
    *,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    max_regions: int = 6,
    context_frames: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Detect bounded caption and watermark boxes in one extracted frame.

    ``confidence_threshold`` follows the standalone tool's 0.40 default.  A
    single frame cannot prove temporal persistence, so this detector only
    returns compact, high-contrast text-like geometry in known overlay zones.
    The parent video/vision gate remains responsible for semantic verification.
    """
    if cv2 is None or np is None or not frame_path or not os.path.isfile(str(frame_path)):
        return []
    try:
        image = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
        if image is None:
            return []
        height, width = image.shape[:2]
        if width < 160 or height < 90:
            return []
        regions: List[Dict[str, Any]] = _rapidocr_candidates(
            image,
            width=width,
            height=height,
            confidence_threshold=confidence_threshold,
        )
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # The standalone detector used top-hat/black-hat morphology to catch
        # both light-on-dark and dark-on-light glyphs.  Keep the kernel small
        # enough for 720p/1080p captions while avoiding scene-wide blobs.
        kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (max(9, int(width * 0.018)), 3)
        )
        response = cv2.max(
            cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel),
            cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel),
        )
        threshold = max(20.0, float(np.percentile(response, 91)) * 0.42)
        raw = cv2.threshold(response, threshold, 255, cv2.THRESH_BINARY)[1]
        raw = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
        raw = cv2.dilate(
            raw,
            cv2.getStructuringElement(cv2.MORPH_RECT, (max(7, int(width * 0.012)), 2)),
            iterations=1,
        )

        # Lower-third captions and subtitles.  The detector intentionally does
        # not treat text in the central 20-55% of the frame as an overlay.
        lower_y = int(height * 0.55)
        regions.extend(_contour_candidates(
            raw[lower_y:int(height * 0.99), :],
            width=width,
            height=height,
            y_offset=lower_y,
            kind="caption",
            min_width_ratio=0.10,
            max_height_ratio=0.20,
            min_aspect=2.0,
            method="ported_morphology_lower_third",
            max_candidates=4,
        ))
        # Top chapter/title bands are common in downloaded footage.  Only a
        # wide compact band is admitted, preventing small logos from becoming
        # a full-frame blur.
        top_h = max(1, int(height * 0.24))
        regions.extend(_contour_candidates(
            raw[:top_h, :],
            width=width,
            height=height,
            y_offset=0,
            kind="text_overlay",
            min_width_ratio=0.18,
            max_height_ratio=0.18,
            min_aspect=2.6,
            method="ported_morphology_top_band",
            max_candidates=2,
        ))

        # Small corner logos/watermarks.  When the parent clip's extracted
        # frames are available, use the existing multi-frame stationary-edge
        # verifier first.  This prevents a car edge, tree branch, or bright
        # ellipse in one still from being mistaken for a logo.  A single-frame
        # fallback remains available, but uses a higher confidence floor.
        corner_h, corner_w = int(height * 0.25), int(width * 0.35)
        corners = [
            (0, 0, corner_w, corner_h, "top_left"),
            (width - corner_w, 0, width, corner_h, "top_right"),
            (0, height - corner_h, corner_w, height, "bottom_left"),
            (width - corner_w, height - corner_h, width, height, "bottom_right"),
        ]
        context = [str(path) for path in (context_frames or []) if path and os.path.isfile(str(path))]
        if len(context) >= 2:
            try:
                from .analyzers import detect_watermark_box
                for corner_name in ("top_left", "top_right", "bottom_left", "bottom_right"):
                    box = detect_watermark_box(context, corner_name)
                    if box:
                        regions.append(_region(
                            "watermark", *box, 0.82,
                            f"ported_stationary_edge_{corner_name}",
                        ))
            except Exception:
                pass
        else:
            for x1, y1, x2, y2, corner_name in corners:
                crop = raw[y1:y2, x1:x2]
                corner_candidates = _contour_candidates(
                    crop,
                    width=width,
                    height=height,
                    y_offset=y1,
                    x_offset=x1,
                    kind="watermark",
                    min_width_ratio=0.025,
                    max_height_ratio=0.14,
                    min_aspect=1.15,
                    method=f"ported_corner_{corner_name}",
                    max_candidates=2,
                )
                for candidate in corner_candidates:
                    bx1, by1, bx2, by2 = [float(value) for value in candidate["bbox"]]
                    area = max(0.0, candidate["bbox"][2] - candidate["bbox"][0]) * max(
                        0.0, candidate["bbox"][3] - candidate["bbox"][1]
                    )
                    # Single-frame watermark evidence is weaker than the
                    # stationary verifier; do not blur scenery at the default
                    # 0.40 sensitivity unless its score is clearly higher.
                    if area <= 0.08 and float(candidate.get("confidence") or 0.0) >= 56.0:
                        regions.append(candidate)

        filtered = [
            item for item in _merge_regions(regions)
            if float(item.get("confidence") or 0.0) / 100.0 >= float(confidence_threshold)
        ]
        safe: List[Dict[str, Any]] = []
        for item in filtered:
            x1, y1, x2, y2 = [float(value) for value in item["bbox"]]
            area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
            kind = str(item.get("type") or "")
            limit = 0.34 if kind == "caption" else 0.12 if kind == "watermark" else 0.24
            if x2 <= x1 or y2 <= y1 or area <= 0 or area > limit:
                continue
            # Blurrr alignment: only blur genuine overlay zones (corner watermarks,
            # lower-third captions, or top bands). Reject central gameplay or scene regions.
            is_corner = (x1 <= 0.35 or x2 >= 0.65) and (y1 <= 0.35 or y2 >= 0.65)
            is_top_band = y1 <= 0.28 and (x2 - x1) >= 0.10 and (y2 - y1) <= 0.20
            is_lower_third = y1 >= 0.48 or (y2 >= 0.65 and (y2 - y1) <= 0.30)
            if not is_corner and not is_lower_third and not is_top_band:
                continue
            safe.append(item)
        safe.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
        return safe[:max(1, int(max_regions))]
    except Exception:
        return []


def apply_gaussian_overlay_blur(
    frame_path: str,
    regions: Sequence[Dict[str, Any]],
    *,
    output_path: Optional[str] = None,
    box_expansion_ratio: float = DEFAULT_BOX_EXPANSION_RATIO,
    mask_dilation_px: int = DEFAULT_MASK_DILATION_PX,
    feather_px: int = DEFAULT_FEATHER_PX,
    blur_radius_px: int = DEFAULT_BLUR_RADIUS_PX,
) -> Optional[str]:
    """Apply a localized, feathered Gaussian blur to overlay polygons.

    The original Blurrr compositor is intentionally kept here: polygons are
    filled first, an elliptical dilation expands anti-aliased glyph edges, and
    Gaussian feathering produces a float alpha mask.  Only the active mask
    bounding region is blurred, so the rest of the extracted frame remains
    byte-for-byte unchanged.  Rectangular bboxes are accepted as a safe
    compatibility fallback for older overlay records.
    """
    if cv2 is None or np is None or not frame_path or not os.path.isfile(str(frame_path)):
        return None
    if not regions:
        return None
    try:
        image = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
        if image is None:
            return None
        height, width = image.shape[:2]
        mask = np.zeros((height, width), dtype=np.uint8)
        expansion = max(1.0, min(2.4, float(box_expansion_ratio)))
        for item in regions:
            bbox = item.get("bbox") if isinstance(item, dict) else None
            polygons = item.get("polygons") if isinstance(item, dict) else None
            bbox_polygons = item.get("bbox_polygons") if isinstance(item, dict) else None
            if not polygons and isinstance(item, dict) and item.get("polygon"):
                polygons = [item.get("polygon")]
            # Merged OCR/morphology regions retain each source polygon.  This
            # keeps gaps between words sharp instead of blurring a union box.
            if isinstance(polygons, (list, tuple)) and polygons:
                for raw_polygon in polygons:
                    if not isinstance(raw_polygon, (list, tuple)) or len(raw_polygon) < 3:
                        continue
                    points = np.asarray(raw_polygon, dtype=np.float32).reshape(-1, 2)
                    # Stored polygons are normalized.  Accept pixel polygons
                    # too so callers can pass the standalone tool's records.
                    if float(np.max(np.abs(points))) > 1.5:
                        points[:, 0] /= max(1.0, float(width))
                        points[:, 1] /= max(1.0, float(height))
                    center = np.mean(points, axis=0)
                    expanded_points = center + (points - center) * expansion
                    expanded_points[:, 0] = np.clip(expanded_points[:, 0], 0.0, 1.0)
                    expanded_points[:, 1] = np.clip(expanded_points[:, 1], 0.0, 1.0)
                    pixel_points = np.round(
                        expanded_points * np.array([width - 1, height - 1], dtype=np.float32)
                    ).astype(np.int32)
                    if len(pixel_points) >= 3:
                        cv2.fillPoly(mask, [pixel_points.reshape(-1, 1, 2)], 255)
                for raw_polygon in bbox_polygons or []:
                    points = np.asarray(raw_polygon, dtype=np.float32).reshape(-1, 2)
                    if len(points) >= 3:
                        pixel_points = np.round(
                            np.clip(points, 0.0, 1.0)
                            * np.array([width - 1, height - 1], dtype=np.float32)
                        ).astype(np.int32)
                        cv2.fillPoly(mask, [pixel_points.reshape(-1, 1, 2)], 255)
                continue
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            x1, y1, x2, y2 = [_clamp(float(value)) for value in bbox]
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            half_w = (x2 - x1) * expansion / 2.0
            half_h = (y2 - y1) * expansion / 2.0
            # Compatibility fallback for legacy records without polygons.
            x1, x2 = max(0.0, cx - half_w), min(1.0, cx + half_w)
            y1, y2 = max(0.0, cy - half_h), min(1.0, cy + half_h)
            if x2 <= x1 or y2 <= y1:
                continue
            cv2.rectangle(
                mask,
                (max(0, int(round(x1 * width))), max(0, int(round(y1 * height)))),
                (min(width - 1, int(round(x2 * width))), min(height - 1, int(round(y2 * height)))),
                255,
                thickness=-1,
            )
        if not np.any(mask):
            return None
        dilation = max(0, min(32, int(mask_dilation_px)))
        if dilation:
            kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (dilation * 2 + 1, dilation * 2 + 1)
            )
            mask = cv2.dilate(mask, kernel, iterations=1)
        feather = max(1.0, min(32.0, float(feather_px)))
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=feather, sigmaY=feather)
        # Keep the same visual intent as the standalone tool's 53px radius,
        # but cap sigma for CPU speed and to avoid obliterating nearby detail.
        sigma = max(3.0, min(24.0, float(blur_radius_px) * 0.42))
        active = cv2.findNonZero((mask > 0).astype(np.uint8))
        if active is None:
            return None
        bx, by, bw, bh = cv2.boundingRect(active)
        pad = int(max(4, round(sigma * 1.5)))
        x_start, y_start = max(0, bx - pad), max(0, by - pad)
        x_end, y_end = min(width, bx + bw + pad), min(height, by + bh + pad)
        crop = image[y_start:y_end, x_start:x_end]
        blurred_crop = cv2.GaussianBlur(crop, (0, 0), sigmaX=sigma, sigmaY=sigma)
        alpha = (mask.astype(np.float32) / 255.0)[..., None]
        result = image.copy()
        crop_alpha = alpha[y_start:y_end, x_start:x_end]
        result[y_start:y_end, x_start:x_end] = np.clip(
            blurred_crop.astype(np.float32) * crop_alpha
            + crop.astype(np.float32) * (1.0 - crop_alpha),
            0,
            255,
        ).astype(np.uint8)
        destination = str(output_path or frame_path)
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        suffix = Path(destination).suffix.lower()
        write_params = (
            [int(cv2.IMWRITE_JPEG_QUALITY), 95]
            if suffix in {".jpg", ".jpeg"}
            else [int(cv2.IMWRITE_PNG_COMPRESSION), 3]
            if suffix == ".png"
            else []
        )
        if not cv2.imwrite(destination, result, write_params):
            return None
        return destination
    except Exception:
        return None


def sanitize_extracted_frame(
    frame_path: str,
    *,
    output_path: Optional[str] = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    context_frames: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Detect and clean one extracted frame without ever failing the pipeline."""
    result: Dict[str, Any] = {
        "path": str(frame_path or ""),
        "blurred": False,
        "regions": [],
        "detector": "opencv_blurrr_port",
        "detection_time_ms": 0.0,
        "blur_time_ms": 0.0,
        "mask_area_ratio": 0.0,
    }
    # Never degrade media quality by blurring frames when unblurred pristine footage is required
    if os.environ.get("VIDZEO_DISABLE_FRAME_BLUR", "1") == "1":
        return result
    try:
        started = time.perf_counter()
        effective_confidence = float(confidence_threshold)
        if abs(effective_confidence - DEFAULT_CONFIDENCE_THRESHOLD) < 1e-6:
            try:
                effective_confidence = max(
                    0.20,
                    min(0.90, float(os.environ.get("VIDZEO_FRAME_BLUR_CONFIDENCE", effective_confidence))),
                )
            except (TypeError, ValueError):
                pass
        regions = detect_overlay_regions(
            str(frame_path or ""),
            confidence_threshold=effective_confidence,
            context_frames=context_frames,
        )
        result["detection_time_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        result["regions"] = regions
        if not regions:
            return result
        destination = output_path
        temporary = None
        if not destination or os.path.normcase(os.path.normpath(str(destination))) == os.path.normcase(os.path.normpath(str(frame_path))):
            suffix = Path(str(frame_path)).suffix or ".jpg"
            temporary = tempfile.NamedTemporaryFile(
                prefix="vidnets_overlay_", suffix=suffix, delete=False,
                dir=str(Path(str(frame_path)).parent),
            )
            temporary.close()
            destination = temporary.name
        def _env_int(name: str, fallback: int, low: int, high: int) -> int:
            try:
                return max(low, min(high, int(os.environ.get(name, fallback))))
            except (TypeError, ValueError):
                return fallback

        blur_started = time.perf_counter()
        cleaned = apply_gaussian_overlay_blur(
            str(frame_path),
            regions,
            output_path=str(destination),
            mask_dilation_px=_env_int("VIDZEO_FRAME_BLUR_DILATION_PX", DEFAULT_MASK_DILATION_PX, 0, 32),
            feather_px=_env_int("VIDZEO_FRAME_BLUR_FEATHER_PX", DEFAULT_FEATHER_PX, 1, 32),
            blur_radius_px=_env_int("VIDZEO_FRAME_BLUR_RADIUS_PX", DEFAULT_BLUR_RADIUS_PX, 3, 121),
        )
        result["blur_time_ms"] = round((time.perf_counter() - blur_started) * 1000.0, 3)
        if not cleaned:
            return result
        if temporary is not None:
            os.replace(str(cleaned), str(frame_path))
            cleaned = str(frame_path)
        result.update({
            "path": str(cleaned),
            "blurred": True,
            "mask_area_ratio": round(sum(
                max(0.0, float(region.get("bbox", [0, 0, 0, 0])[2]) - float(region.get("bbox", [0, 0, 0, 0])[0]))
                * max(0.0, float(region.get("bbox", [0, 0, 0, 0])[3]) - float(region.get("bbox", [0, 0, 0, 0])[1]))
                for region in regions
                if isinstance(region, dict) and isinstance(region.get("bbox"), (list, tuple)) and len(region.get("bbox")) == 4
            ), 6),
        })
        return result
    except Exception:
        # Overlay cleanup is a quality enhancement, never a reason to lose a
        # verified frame.  Callers can still apply their normal vision gate.
        return result


__all__ = [
    "detect_overlay_regions",
    "apply_gaussian_overlay_blur",
    "sanitize_extracted_frame",
    "overlay_clean_output_path",
]
