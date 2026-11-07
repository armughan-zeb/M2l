"""Cheap render-boundary checks for supplied still media.

Custom media is trusted for ownership, not blindly trusted for editorial
quality. A failed download or an exported background plate can be a valid JPG
with almost no information; letting it reach Remotion creates a black slate.
This module detects near-flat stills and baked social-media blur/letterbox
bands, then repairs a slot from the same closed media pool. It deliberately
does not apply a brightness-only rejection to video clips.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def inspect_still(path: Any) -> Dict[str, Any]:
    """Return lightweight luminance/edge metrics for an image file."""
    raw = str(path or "").strip()
    suffix = Path(raw).suffix.casefold()
    if not raw or suffix not in IMAGE_SUFFIXES:
        return {"checked": False, "low_information": False, "reason": "not_a_still"}
    file_path = Path(raw)
    if not file_path.is_file():
        return {"checked": True, "low_information": True, "reason": "missing_file"}
    try:
        import cv2

        image = cv2.imread(str(file_path), cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            return {"checked": True, "low_information": True, "reason": "decode_failed"}
        mean = float(image.mean())
        std = float(image.std())
        edge_density = float(cv2.Canny(image, 80, 160).mean() / 255.0)
        height, width = image.shape[:2]

        def band_edges(y1: float, y2: float, x1: float = 0.0, x2: float = 1.0) -> float:
            crop = image[
                max(0, int(height * y1)):max(1, int(height * y2)),
                max(0, int(width * x1)):max(1, int(width * x2)),
            ]
            return float(cv2.Canny(crop, 80, 160).mean() / 255.0)

        top_edge = band_edges(0.0, 0.18, 0.1, 0.9)
        middle_edge = band_edges(0.22, 0.78, 0.1, 0.9)
        bottom_edge = band_edges(0.82, 1.0, 0.1, 0.9)
        left_edge = band_edges(0.1, 0.9, 0.0, 0.18)
        center_edge = band_edges(0.1, 0.9, 0.22, 0.78)
        right_edge = band_edges(0.1, 0.9, 0.82, 1.0)
        upper_inner_edge = band_edges(0.35, 0.60, 0.1, 0.9)
        lower_inner_edge = band_edges(0.64, 0.84, 0.1, 0.9)
        def band_std(y1: float, y2: float, x1: float = 0.0, x2: float = 1.0) -> float:
            crop = image[
                max(0, int(height * y1)):max(1, int(height * y2)),
                max(0, int(width * x1)):max(1, int(width * x2)),
            ]
            return float(crop.std())
        middle_std = band_std(0.22, 0.78, 0.1, 0.9)
        top_std = band_std(0.0, 0.18, 0.1, 0.9)
        bottom_std = band_std(0.82, 1.0, 0.1, 0.9)
        left_std = band_std(0.1, 0.9, 0.0, 0.18)
        right_std = band_std(0.1, 0.9, 0.82, 1.0)
        center_std = band_std(0.1, 0.9, 0.22, 0.78)
        near_black_rows = (image.mean(axis=1) < 8.0) & (image.std(axis=1) < 13.0)

        def longest_run(values) -> int:
            best = current = 0
            for value in values:
                current = current + 1 if bool(value) else 0
                best = max(best, current)
            return best

        top_matte_run = longest_run(near_black_rows[:max(1, int(height * 0.15))])
        bottom_matte_run = longest_run(near_black_rows[max(0, int(height * 0.85)):][::-1])
    except Exception:
        # A missing optional CV dependency must not turn a valid custom asset
        # into a rejected scene.
        return {"checked": False, "low_information": False, "reason": "analyzer_unavailable"}

    # Intentionally conservative: this catches the near-flat dark plates that
    # were appearing as full-frame slates, while preserving dark but textured
    # footage/stills such as night driving or red-lit interiors.
    low_information = bool(mean < 22.0 and std < 20.0 and edge_density < 0.0014)
    # These are not ordinary shadows. They are the soft duplicate strips
    # exported by vertical/social assets and the blurred lower-third baked
    # into several stills. Reject them before Remotion can mistake them for a
    # deliberate documentary treatment.
    bottom_blur_band = bool(
        middle_edge > 0.008
        and bottom_edge < max(0.00035, middle_edge * 0.16)
        and bottom_std < max(8.0, middle_std * 0.72)
    )
    # Some exported stills contain a very large, low-detail lower duplicate
    # strip whose luminance variance is still high because it is a gradient.
    # Edge collapse is the reliable signal in that case; keep the threshold
    # conservative so ordinary dark or shallow foregrounds remain valid.
    bottom_soft_duplicate = bool(
        middle_edge > 0.02
        and bottom_edge < 0.0015
        and bottom_std < middle_std * 0.95
    )
    # A blurred social-media strip can retain luminance variance, so its
    # standard deviation is not always lower than the sharp image above it.
    # The more stable signal is a collapse of high-frequency edges in the
    # lower band. Keep both an absolute and relative threshold so an ordinary
    # dark foreground is not rejected merely for being dark.
    bottom_edge_collapse = bool(
        middle_edge > 0.0028
        and bottom_edge < 0.0012
        and bottom_edge / max(middle_edge, 1e-6) < 0.22
    )
    # Some wide source frames keep a sharp-looking foreground edge while the
    # large inner lower strip is still a baked blur. Compare adjacent inner
    # bands so detection does not depend on the absolute bottom edge.
    lower_inner_edge_collapse = bool(
        upper_inner_edge > 0.0025
        and lower_inner_edge < 0.0035
        and lower_inner_edge / max(upper_inner_edge, 1e-6) < 0.35
    )
    # Reject baked letterbox/matte bars. They reduce usable documentary media
    # area and are especially harmful when a still is promoted to a full shot.
    hard_matte_bars = bool(
        max(top_matte_run, bottom_matte_run) >= max(12, int(height * 0.012))
    )
    top_blur_band = bool(
        middle_edge > 0.008
        and top_edge < max(0.00035, middle_edge * 0.16)
        and top_std < max(8.0, middle_std * 0.72)
    )
    side_blur_bands = bool(
        center_edge > 0.008
        and left_edge < center_edge * 0.18
        and right_edge < center_edge * 0.18
        and left_std < max(8.0, center_std * 0.55)
        and right_std < max(8.0, center_std * 0.55)
    )
    baked_blur_band = bool(
        bottom_blur_band
        or bottom_soft_duplicate
        or bottom_edge_collapse
        or lower_inner_edge_collapse
        or hard_matte_bars
        or top_blur_band
        or side_blur_bands
    )
    return {
        "checked": True,
        "low_information": low_information,
        "mean_luma": round(mean, 3),
        "luma_std": round(std, 3),
        "edge_density": round(edge_density, 6),
        "top_edge_density": round(top_edge, 6),
        "middle_edge_density": round(middle_edge, 6),
        "bottom_edge_density": round(bottom_edge, 6),
        "bottom_edge_collapse": bottom_edge_collapse,
        "lower_inner_edge_collapse": lower_inner_edge_collapse,
        "hard_matte_bars": hard_matte_bars,
        "left_edge_density": round(left_edge, 6),
        "center_edge_density": round(center_edge, 6),
        "right_edge_density": round(right_edge, 6),
        "middle_band_std": round(middle_std, 3),
        "bottom_band_std": round(bottom_std, 3),
        "left_band_std": round(left_std, 3),
        "right_band_std": round(right_std, 3),
        "baked_blur_band": baked_blur_band,
        "reason": "near_flat_dark_plate" if low_information else ("baked_blur_band" if baked_blur_band else "pass"),
        "needs_repair": bool(low_information or baked_blur_band),
    }


def repair_media_sequence(
    media_items: Sequence[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Replace near-flat still slots with nearby healthy media from the pool."""
    items = [dict(item) for item in (media_items or []) if isinstance(item, Mapping)]
    inspections = [inspect_still(item.get("path") or item.get("src")) for item in items]
    healthy_indices = [
        index
        for index, (item, quality) in enumerate(zip(items, inspections))
        if not quality.get("needs_repair")
        and str(item.get("path") or item.get("src") or "").strip()
    ]
    # If multiple healthy candidates are available, prefer an actual clip over
    # another still: a clip is less likely to carry an exported blur/letterbox
    # treatment and gives the edit a more natural visual rhythm.
    healthy_indices.sort(key=lambda index: (str(items[index].get("type") or "").lower() != "video", index))
    repairs: List[Dict[str, Any]] = []
    recent_replacements: List[str] = []

    for index, (item, quality) in enumerate(zip(items, inspections)):
        if not quality.get("needs_repair") or not healthy_indices:
            continue
        ranked = sorted(healthy_indices, key=lambda candidate: (abs(candidate - index), candidate))
        replacement_index = next(
            (
                candidate for candidate in ranked
                if str(items[candidate].get("path") or items[candidate].get("src") or "")
                not in recent_replacements[-4:]
            ),
            ranked[0],
        )
        replacement = items[replacement_index]
        old_path = str(item.get("path") or item.get("src") or "")
        new_path = str(replacement.get("path") or replacement.get("src") or "")
        if not new_path or new_path == old_path:
            continue
        for key in ("path", "src", "type", "safe_framing", "position", "focal_bbox", "subject_bbox"):
            if key in replacement:
                item[key] = replacement[key]
        item["media_quality_repair"] = {
            "status": "replaced_low_quality_still",
            "original_path": old_path,
            "replacement_path": new_path,
            "original_metrics": quality,
        }
        recent_replacements.append(new_path)
        repairs.append({
            "scene_index": index,
            "original_path": old_path,
            "replacement_path": new_path,
            "reason": quality.get("reason"),
        })
    return items, repairs
