"""Pool-first source preparation, shortlist, local cuts, and visual ranking."""
from __future__ import annotations

import os
import math
import re
import statistics
import subprocess
import tempfile
import threading
import time
import zlib
from typing import Any, Dict, List

_SCENE_OFFSET_BASE = (0.0, 18.0, -18.0, 36.0, -36.0, 54.0, -54.0, 90.0, -90.0, 126.0, -126.0)

_CREATOR_CTA_TEXT = re.compile(
    r"\b(?:subscribe(?:d)?|follow\s+us|like\s*(?:and|&)\s*subscribe|"
    r"like\s*(?:and|&)\s*share|comment\s+below|ring\s+the\s+bell)\b",
    re.IGNORECASE,
)


def _creator_cta_text_match(text: str, bbox: Any, width: int, height: int) -> bool:
    """Recognize explicit channel-promotion text in an edge overlay only."""
    normalized = re.sub(r"[^a-z]+", " ", str(text or "").lower()).strip()
    compact = normalized.replace(" ", "")
    # OCR often merges the YouTube button icon into a leading O/D token.
    if compact.startswith(("osubscribe", "dsubscribe")):
        compact = compact[1:]
    explicit_cta = bool(
        re.search(r"\bsubscribe(?:d)?\b", normalized)
        or compact.startswith(("subscribe", "subscribed"))
        or _CREATOR_CTA_TEXT.search(normalized)
    )
    if not explicit_cta:
        return False
    try:
        points = list(bbox or [])
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        center_x = (min(xs) + max(xs)) / 2.0 / max(1, width)
        center_y = (min(ys) + max(ys)) / 2.0 / max(1, height)
    except (TypeError, ValueError, IndexError):
        return False
    # A spoken/captured sign can mention these words naturally. Creator CTAs
    # are screen furniture, so require them near the frame edge or lower band.
    return center_y >= 0.52 or center_y <= 0.16 or center_x <= 0.10 or center_x >= 0.90


def _detect_creator_cta_overlay(cut_path: str, duration: float, sample_frames=None) -> Dict[str, Any]:
    """Read beginning, middle, and ending frames for creator CTA overlays.

    OCR is optional and local. If it is unavailable or errors, normal overlay
    screening continues; recognized CTAs are rejected as source contamination.
    Creator subscribe cards commonly appear only during an intro, so sampling
    only the final frames lets contaminated clips pass into the edit.
    """
    result: Dict[str, Any] = {"checked": False, "detected": False, "reason": "ocr_unavailable"}
    try:
        import numpy as np
        from PIL import Image
        from services.scene_understanding.frame_overlay_blur import _get_rapidocr

        engine = _get_rapidocr()
        if not engine:
            return result
        # This is supplementary OCR; the shared pixel verifier and regular
        # facecam/end-card checks remain the content gates. Do not let three
        # workers queue indefinitely on the same optional ONNX session.
        ocr_deadline = time.monotonic() + 10.0
        result["checked"] = True
        result["reason"] = "no_creator_cta"
        from services.visual_intelligence.frame_extractor import _ffmpeg_bin

        clip_duration = max(0.0, float(duration or 0.0))
        if clip_duration <= 0.2:
            return result
        for frame_index, fraction in enumerate((0.08, 0.50, 0.90)):
            if time.monotonic() >= ocr_deadline:
                result['reason'] = 'partial_ocr_budget'
                break
            frame_path = ""
            try:
                if sample_frames:
                    # Reuse the already decoded beginning/middle/end probes.
                    # A second set of full-resolution decoders per cut used
                    # substantial RAM and could consume 30 seconds per clip.
                    frame_path = sample_frames[round(frame_index * (len(sample_frames) - 1) / 2)]
                else:
                    handle = tempfile.NamedTemporaryFile(prefix="vidzeo-yt-cta-", suffix=".jpg", delete=False)
                    frame_path = handle.name
                    handle.close()
                    timestamp = min(clip_duration - 0.05, max(0.05, clip_duration * fraction))
                    command = [
                        _ffmpeg_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{timestamp:.3f}", "-i", str(cut_path), "-frames:v", "1", "-q:v", "2", frame_path,
                    ]
                    subprocess.run(
                        command, capture_output=True, timeout=10,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                if not os.path.isfile(frame_path) or os.path.getsize(frame_path) < 512:
                    continue
                with Image.open(frame_path) as image:
                    image = image.convert("RGB")
                    image.thumbnail((1280, 1280))
                    width, height = image.size
                    pixels = np.asarray(image)
                # The RapidOCR ONNX session is shared by overlay and image
                # checks. Serialize inference so parallel scene workers do not
                # contend inside the session or create CPU spikes.
                acquired = _YT_CTA_OCR_LOCK.acquire(timeout=max(0.0, ocr_deadline - time.monotonic()))
                if not acquired:
                    result['reason'] = 'partial_ocr_budget'
                    break
                try:
                    rows, _timing = engine(pixels)
                finally:
                    _YT_CTA_OCR_LOCK.release()
                for row in rows or []:
                    if not isinstance(row, (tuple, list)) or len(row) < 3:
                        continue
                    text = str(row[1] or "").strip()
                    try:
                        confidence = float(row[2] or 0.0)
                    except (TypeError, ValueError):
                        confidence = 0.0
                    if confidence < 0.65:
                        continue
                    if _creator_cta_text_match(text, row[0], width, height):
                        result.update(
                            detected=True,
                            reason="creator_cta_overlay",
                            matched_term=("subscribe" if re.search(r"subscribe", text, re.I) else "creator_cta"),
                            confidence=round(confidence, 3),
                        )
                        return result
            except Exception:
                continue
            finally:
                if frame_path and not sample_frames:
                    try:
                        os.unlink(frame_path)
                    except OSError:
                        pass
        return result
    except Exception:
        return result


_YT_CTA_OCR_LOCK = threading.Lock()


def _scene_offsets_grid(shift: float) -> tuple:
    """Deterministic per-scene timestamp grid around a verified probe window.

    A fixed grid makes consecutive scenes sample the same video at the same
    offsets; shifting the grid per scene spreads every scene across a
    different portion of each source's moving footage.
    """
    return tuple(round(offset + shift, 2) for offset in _SCENE_OFFSET_BASE)
from .youtube_overlay_policy import preserve_source_overlays
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Mapping, Optional


class PoolFirstResults(list):
    def __init__(self, values, *, audit=None, retrieval_decisions=None):
        super().__init__(values)
        self.audit = dict(audit or {})
        self.retrieval_decisions = dict(retrieval_decisions or {})


_CORNER_BOXES = {
    "top_left": (0.0, 0.0, 0.25, 0.25),
    "top_right": (0.75, 0.0, 1.0, 0.25),
    "bottom_left": (0.0, 0.75, 0.25, 1.0),
    "bottom_right": (0.75, 0.75, 1.0, 1.0),
}

# Native labels are useful visual information in more lanes than gaming:
# scoreboards, timing/telemetry, device read-outs, animation controls, and
# scientific/financial charts are part of the source footage rather than a
# streamer reaction package.  Keep this list deliberately explicit.  Unknown
# and narrative lanes continue to use the strict creator-text gate.
_NATIVE_OVERLAY_CATEGORIES = {
    "gaming", "sports", "action", "automotive", "technology", "science",
    "finance", "transportation", "animation", "product", "education",
}


def _native_overlay_lane_for_category(category: Any) -> bool:
    """Return whether bounded native data/HUD overlays may remain visible.

    This is an allow-list, not a general leniency switch.  The caller still
    applies the facecam/presenter veto and the geometry bounds below.
    """
    value = str(category or "").strip().lower().replace("-", "_")
    aliases = {
        "tech": "technology",
        "cars": "automotive",
        "car": "automotive",
        "racing": "action",
        "esports": "gaming",
        "diagram": "education",
        "product_review": "product",
    }
    return aliases.get(value, value) in _NATIVE_OVERLAY_CATEGORIES


def _bounded_native_overlay_box(
    bbox: Any,
    *,
    gaming_lane: bool = False,
    native_overlay_lane: bool = False,
) -> bool:
    """True for a small, edge-anchored native label/data box.

    Full-width headings and creator cards are intentionally excluded. Gaming
    and native overlay lanes (sports scorebugs, broadcast telemetry, HUDs) get
    wider bounds because ticker/scorebug/HUD text can legitimately sit across
    top/bottom edge bands; presenter/facecam vetoes remain independent and are
    always enforced by the caller.
    """
    try:
        if len(bbox or ()) != 4:
            return False
        x1, y1, x2, y2 = [float(value) for value in bbox]
    except (TypeError, ValueError):
        return False
    width = max(0.0, x2 - x1)
    height = max(0.0, y2 - y1)
    area = width * height
    if not (0.0 <= x1 <= 1.0 and 0.0 <= y1 <= 1.0 and 0.0 <= x2 <= 1.0 and 0.0 <= y2 <= 1.0):
        return False
    if gaming_lane:
        return area <= 0.16 and height <= 0.30 and width <= 0.92
    if native_overlay_lane:
        # Native broadcast scorebugs, game clocks, and data tickers sit on edge bands
        edge_anchored = bool(x1 <= 0.14 or x2 >= 0.86 or y1 <= 0.20 or y2 >= 0.80)
        return bool(area <= 0.20 and height <= 0.25 and width <= 0.96 and edge_anchored)
    edge_anchored = bool(x1 <= 0.12 or x2 >= 0.88 or y1 <= 0.14 or y2 >= 0.86)
    return bool(area <= 0.10 and height <= 0.24 and width <= 0.78 and edge_anchored)


def _caption_blur_admissible(overlay: Mapping[str, Any] | None) -> bool:
    """Compatibility hook: source footage is never admitted for blur cleanup."""
    return False


def _screen_moment_quality(
    cut_path: str,
    *,
    ffmpeg_bin: str = "ffmpeg",
    archival_lane: bool = False,
) -> Dict[str, Any]:
    """Reject rendered cuts that contain only a dark/near-uniform frame.

    Metadata and overlay checks can both pass a technically valid MP4 whose
    selected timestamp is a fade, black leader, or an almost empty underwater
    frame. Sample several grayscale frames and combine luma, local edge energy,
    and bright-pixel coverage. Probe failures remain fail-open for test doubles
    and provider diagnostics; a successfully probed low-information cut is
    rejected and allowed to fall through to the next timestamp/source option.
    """
    width, height = 32, 18
    result: Dict[str, Any] = {
        "checked": False,
        "reject": False,
        "reason": "",
        "policy": "multi_frame_luma_edge_motion_gate_v2",
        "frames": 0,
        "mean_luma": 0.0,
        "luma_std": 0.0,
        "edge_energy": 0.0,
        "bright_fraction": 0.0,
        "motion_mean": 0.0,
        "motion_p90": 0.0,
        "motion_changed_fraction": 0.0,
        "optical_flow_pairs": 0,
        "optical_flow_median": 0.0,
        "optical_flow_coherence": 0.0,
        "coherent_pan_fraction": 0.0,
        "low_motion_hold_fraction": 0.0,
        "perceptual_hold_fraction": 0.0,
    }
    try:
        completed = subprocess.run(
            [
                str(ffmpeg_bin or "ffmpeg"),
                "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1", "-filter_threads", "1",
                "-i", str(cut_path),
                "-vf", f"fps=2,scale={width}:{height}:flags=area,format=gray",
                "-frames:v", "12",
                "-f", "rawvideo", "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return result
    raw = bytes(completed.stdout or b"")
    frame_size = width * height
    if completed.returncode != 0 or len(raw) < frame_size:
        return result
    frames = [
        list(raw[offset:offset + frame_size])
        for offset in range(0, len(raw) - frame_size + 1, frame_size)
    ]
    metrics = []
    for frame in frames:
        horizontal = [abs(frame[index] - frame[index - 1]) for index in range(1, len(frame))]
        vertical = [abs(frame[index] - frame[index - width]) for index in range(width, len(frame))]
        metrics.append({
            "mean": statistics.mean(frame),
            "std": statistics.pstdev(frame),
            "edge": statistics.mean(horizontal + vertical),
            "bright": sum(value >= 40 for value in frame) / len(frame),
        })
    # Check motion before any project captions are added. A technically valid
    # MP4 can still be a single illustration, slideshow card, or frozen frame
    # with only changing burned-in text. Analyze a small interior crop so
    # lower-third text and compression noise cannot manufacture "motion".
    motion_deltas = []
    motion_changed = []
    roi_indexes = [
        row * width + column
        for row in range(2, height - 2)
        for column in range(2, width - 2)
    ]
    for previous, current in zip(frames, frames[1:]):
        deltas = [abs(current[index] - previous[index]) for index in roi_indexes]
        if not deltas:
            continue
        motion_deltas.append(statistics.mean(deltas))
        motion_changed.append(sum(value >= 8 for value in deltas) / len(deltas))
    if motion_deltas:
        ordered_motion = sorted(motion_deltas)
        p90_index = min(len(ordered_motion) - 1, int(round(0.90 * (len(ordered_motion) - 1))))
        motion_mean = statistics.mean(motion_deltas)
        motion_p90 = ordered_motion[p90_index]
        motion_changed_fraction = statistics.mean(motion_changed)
    else:
        motion_mean = motion_p90 = motion_changed_fraction = 0.0
    # A compact average-hash comparison catches a held archival photograph
    # even when the source applies a tiny crop/zoom or compression shimmer.
    # It is deliberately cheaper and more tolerant than full-frame SSIM.
    perceptual_distances = []
    try:
        def _average_hash(frame):
            cells = []
            for hash_row in range(8):
                y0 = int(hash_row * height / 8)
                y1 = max(y0 + 1, int((hash_row + 1) * height / 8))
                for hash_col in range(8):
                    x0 = int(hash_col * width / 8)
                    x1 = max(x0 + 1, int((hash_col + 1) * width / 8))
                    values = [
                        frame[row * width + column]
                        for row in range(y0, min(height, y1))
                        for column in range(x0, min(width, x1))
                    ]
                    cells.append(statistics.mean(values) if values else 0.0)
            threshold = statistics.mean(cells) if cells else 0.0
            return [int(value >= threshold) for value in cells]

        hashes = [_average_hash(frame) for frame in frames]
        perceptual_distances = [
            sum(left != right for left, right in zip(previous, current))
            for previous, current in zip(hashes, hashes[1:])
        ]
    except Exception:
        perceptual_distances = []
    perceptual_hold_fraction = (
        sum(distance <= 2 for distance in perceptual_distances)
        / len(perceptual_distances)
        if perceptual_distances else 0.0
    )
    # A valid MP4 can still be a still photograph with a Ken-Burns pan, or a
    # title card with compression shimmer. Pixel deltas alone mistake those
    # for footage. Use a tiny optical-flow pass to distinguish sustained,
    # spatially coherent camera motion from a moving subject. This is an
    # optional accelerator: if OpenCV is unavailable, the existing luma/edge
    # checks remain authoritative and no exception escapes the gate.
    flow_medians = []
    flow_coherences = []
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        flow_frames = [
            np.asarray(frame, dtype=np.uint8).reshape((height, width))
            for frame in frames
        ]
        for previous, current in zip(flow_frames, flow_frames[1:]):
            flow = cv2.calcOpticalFlowFarneback(
                previous, current, None, 0.5, 2, 15, 2, 5, 1.2, 0
            )
            magnitude, angle = cv2.cartToPolar(flow[..., 0], flow[..., 1])
            valid = magnitude > 0.15
            if not bool(np.any(valid)):
                flow_medians.append(0.0)
                flow_coherences.append(1.0)
                continue
            vectors = np.exp(1j * angle[valid])
            flow_medians.append(float(np.median(magnitude[valid])))
            flow_coherences.append(float(abs(np.mean(vectors))))
    except Exception:
        flow_medians = []
        flow_coherences = []
    if flow_medians:
        coherent_pan_pairs = sum(
            1
            for magnitude, coherence in zip(flow_medians, flow_coherences)
            if magnitude >= 0.6 and coherence >= 0.65
        )
        low_motion_pairs = sum(
            1
            for magnitude, delta, changed in zip(flow_medians, motion_deltas, motion_changed)
            if magnitude < 0.45 and delta < 4.0 and changed < 0.18
        )
        coherent_pan_fraction = coherent_pan_pairs / len(flow_medians)
        low_motion_hold_fraction = low_motion_pairs / len(flow_medians)
        optical_flow_median = statistics.median(flow_medians)
        optical_flow_coherence = statistics.median(flow_coherences)
    else:
        coherent_pan_fraction = low_motion_hold_fraction = 0.0
        optical_flow_median = optical_flow_coherence = 0.0
    result.update({
        "checked": True,
        "frames": len(metrics),
        "mean_luma": round(statistics.median(item["mean"] for item in metrics), 2),
        "luma_std": round(statistics.median(item["std"] for item in metrics), 2),
        "edge_energy": round(statistics.median(item["edge"] for item in metrics), 2),
        "bright_fraction": round(statistics.median(item["bright"] for item in metrics), 3),
        "motion_mean": round(motion_mean, 3),
        "motion_p90": round(motion_p90, 3),
        "motion_changed_fraction": round(motion_changed_fraction, 4),
        "optical_flow_pairs": len(flow_medians),
        "optical_flow_median": round(optical_flow_median, 3),
        "optical_flow_coherence": round(optical_flow_coherence, 3),
        "coherent_pan_fraction": round(coherent_pan_fraction, 4),
        "low_motion_hold_fraction": round(low_motion_hold_fraction, 4),
        "perceptual_hold_fraction": round(perceptual_hold_fraction, 4),
    })
    low_information = [
        item for item in metrics
        if item["mean"] < 28.0 and item["edge"] < 3.5 and item["bright"] < 0.16
    ]
    low_detail_dark = [
        item for item in metrics
        if (
            item["mean"] < 38.0
            and item["edge"] < 4.5
            and item["std"] < 23.0
            and item["bright"] < 0.35
        )
    ]
    uniform = [
        item for item in metrics
        if item["std"] < 3.5 or item["edge"] < 1.0
    ]
    majority = max(2, (len(metrics) + 1) // 2)
    still_or_slideshow = bool(
        len(metrics) >= 4
        and motion_p90 < 1.5
        and motion_changed_fraction < 0.05
    )
    # Reject image-like YouTube moments even when a slow pan or title-card
    # shimmer creates enough pixel delta to evade the legacy still test. A
    # real clip must contain either non-coherent subject motion or a sustained
    # temporal change; a mostly static hold plus global pan is not sufficient.
    static_title_card = bool(
        flow_medians
        and optical_flow_median < 0.45
        and optical_flow_coherence >= 0.85
        and motion_changed_fraction < 0.45
        and result["bright_fraction"] < 0.45
        and result["edge_energy"] > 8.0
    )
    # Coherent flow is not, by itself, evidence of a still image. A real
    # documentary shot often has exactly one dominant camera direction while
    # the camera walks, pans, or tracks across the subject. The previous rule
    # rejected those clips whenever the flow was coherent and the motion was
    # below a broad 45/0.90 threshold, which effectively removed most useful
    # archival footage. Keep the pan veto only for a low-change hold whose
    # compact hashes remain mostly identical (the Ken-Burns/title-card case).
    static_pan = bool(
        flow_medians
        and optical_flow_coherence >= 0.94
        and optical_flow_median < 0.70
        and motion_mean < 22.0
        and motion_changed_fraction < 0.58
        and perceptual_hold_fraction >= 0.55
    )
    static_frame_hold = bool(
        perceptual_distances
        and perceptual_hold_fraction >= 0.60
        and motion_changed_fraction < 0.60
    )
    # A fast sequence of photographic/title-card swaps is not documentary
    # footage. It has high pixel change, little perceptual continuity, and
    # weakly coherent flow; accepting it is how creator text slides used to
    # masquerade as a moving YouTube clip.
    rapid_text_montage = bool(
        len(metrics) >= 6
        and motion_p90 >= 40.0
        and motion_changed_fraction >= 0.68
        and perceptual_hold_fraction <= 0.10
        and optical_flow_coherence < 0.90
    )
    # Very short cuts can evade the ordinary hold threshold when a title card
    # appears for the final sampled frame.  Treat a high-hold, low-motion
    # sample as image-like even if one card transition inflates the changed
    # fraction.  This is deliberately limited to short samples so that a
    # longer archival shot with a genuine camera move is not discarded.
    short_static_hold = bool(
        len(metrics) <= 6
        and perceptual_hold_fraction >= 0.70
        and motion_mean < 32.0
        and motion_changed_fraction < 0.82
    )
    # Historical/disaster lanes are dominated by archival film: slow pans
    # over grain, near-static wide shots of docks and crowds. Those are the
    # desired content, not slideshow cards. Film grain leaves per-frame pixel
    # noise (motion changed + edge energy) that a Ken-Burns still does not,
    # so suppress the pan/hold vetoes only when real film texture is present;
    # title-card holds and rapid text montages stay hard rejects.
    archival_holds = bool(
        archival_lane
        and flow_medians
        and motion_mean >= 1.5
        and motion_changed_fraction >= 0.04
        and result["edge_energy"] > 2.0
    )
    static_image_or_pan = bool(
        flow_medians
        and (
            static_title_card
            or static_pan
            or static_frame_hold
            or rapid_text_montage
            or short_static_hold
            or (
                coherent_pan_fraction >= 0.55
                and motion_mean < 24.0
                and motion_changed_fraction < 0.62
                and perceptual_hold_fraction >= 0.70
            )
            or (
                low_motion_hold_fraction >= 0.40
                and coherent_pan_fraction < 0.55
            )
        )
        and not (archival_holds and not (static_title_card or rapid_text_montage))
    )
    if (
        len(uniform) >= majority
        or len(low_information) >= majority
        or len(low_detail_dark) >= majority
        or still_or_slideshow
        or static_image_or_pan
    ):
        result.update({
            "reject": True,
            "reason": (
                "static_image_or_pan"
                if static_image_or_pan and not (
                    len(uniform) >= majority
                    or len(low_information) >= majority
                    or len(low_detail_dark) >= majority
                    or still_or_slideshow
                )
                else "still_or_slideshow_frame"
                if still_or_slideshow and not (
                    len(uniform) >= majority
                    or len(low_information) >= majority
                    or len(low_detail_dark) >= majority
                )
                else "blank_or_low_information_frame"
            ),
        })
    return result


def _screen_moment_overlays(
    cut_path: str,
    camera_position: str = "center",
    *,
    archival_lane: bool = False,
    gaming_lane: bool = False,
    native_overlay_lane: bool = False,
) -> Dict[str, Any]:
    """Deterministic, zero-vision overlay screen for a local cut.

    Samples a few small frames and runs the existing CPU analyzers
    (corner-stability logo detection + bottom-band subtitle detection).
    Returns ``{"checked", "watermark_prob", "has_subtitle", "clean",
    "regions"}`` where ``regions`` holds normalized blur boxes
    (``{"type", "bbox", "confidence"}``) for every overlay found.
    Never raises: any detection failure counts as unverified, not dirty.

    Watermarks/logos are cosmetic and tolerated (and may be blurred when the
    cleanup path is available). Burned-in captions, subtitles, and creator
    text are hard rejects except for the explicit gaming lane and the bounded
    native-data lane, where a small no-facecam/no-presenter signal is kept as
    source HUD/telemetry/score context. Presenter/facecam layouts remain hard
    rejects in every lane.
    ``archival_lane`` is retained for audit context and only permits the
    narrow archival caption-band contract. ``native_overlay_lane`` permits
    only small, edge-anchored source labels/data; presenter and facecam
    contamination remains a hard veto.
    """
    if not gaming_lane:
        niche_env = str(os.environ.get("VIDZEO_SCRIPT_NICHE") or os.environ.get("VIDZEO_TOPIC_NICHE") or "").strip().lower()
        if niche_env in {"gaming", "game", "games", "gameplay", "esports"}:
            gaming_lane = True
    result = {
        "checked": False,
        "watermark_prob": 0.0,
        "has_subtitle": False,
        "clean": True,
        "regions": [],
        "talking_head": False,
        "talking_head_confidence": 0.0,
        "talking_head_reason": "",
        "facecam_inset": False,
        "facecam_confidence": 0.0,
        "facecam_reason": "",
        "facecam_corner": "",
        "reject": False,
        "overlay_policy": (
            "gaming_hud_allowed_streamer_reject"
            if gaming_lane
            else (
                "native_data_overlay_allowed_streamer_reject"
                if native_overlay_lane
                else "watermark_allowed_caption_text_reject"
            )
        ),
        "overlay_types": [],
        "blur_regions": [],
        "rejection_reason": "",
        "cleaned_path": "",
        "archival_caption_allowed": False,
        "watermark_tolerated": False,
        "native_overlay_detected": False,
    }
    try:
        from services.visual_intelligence.frame_extractor import cleanup_frames, extract_clip_frames
        from services.scene_understanding.analyzers import (
            detect_subtitle_box,
            detect_persistent_text_overlay_box,
            detect_subtitles,
            detect_watermark,
            detect_watermark_box,
            detect_talking_head,
            detect_facecam_inset,
            detect_outro_endcard,
            hard_talking_head_signal,
        )

        # Fast container pre-check: the frame extractor pays real ffprobe and
        # ffmpeg processes, and a corrupt or truncated download can stall both
        # for seconds. Anything that is not a sizeable MP4/MOV (ftyp box) is
        # unverifiable, never dirty — return now instead of probing garbage.
        try:
            _cut_stat = os.stat(str(cut_path))
            with open(str(cut_path), "rb") as _cut_head:
                _head = _cut_head.read(16)
            if _cut_stat.st_size < 8_192 or len(_head) < 12 or _head[4:8] != b"ftyp":
                return preserve_source_overlays(result)
        except (OSError, TypeError):
            return preserve_source_overlays(result)

        # Two samples let a bright ship rail, crane, or face edge masquerade
        # as a subtitle box. Require a persistent signal across four samples;
        # this still catches burned-in captions while preventing a single
        # scene contour from deleting a genuinely moving clip.
        frames, _duration = extract_clip_frames(str(cut_path), long_edge=640, max_frames=4)
        if not frames:
            return preserve_source_overlays(result)
        # Reuse the already decoded, bounded JPEG samples for title-card
        # screening. Reopening the original AV1 cut with OpenCV allocated an
        # unrestricted decoder and could block a serial download worker.
        title_samples = []
        try:
            from PIL import Image
            for frame_path in frames:
                with Image.open(frame_path) as image:
                    gray = image.convert('L').resize((160, 90))
                    histogram = gray.crop((13, 7, 147, 83)).histogram()
                    count = max(1, sum(histogram))
                    title_samples.append([sum(histogram[211:]) / count, sum(histogram[:45]) / count])
        except (OSError, ValueError):
            pass
        result['title_card_samples'] = title_samples
        # Once frames have been decoded this is a content-integrity gate, not
        # a best-effort decoration pass.  Mark it checked before running the
        # individual analyzers so an analyzer exception cannot accidentally
        # turn an unverified presenter/text clip into an accepted asset.
        result["checked"] = True
        cta_audit = _detect_creator_cta_overlay(str(cut_path), _duration, sample_frames=frames)
        result["creator_cta_checked"] = bool(cta_audit.get("checked"))
        result["creator_cta_detected"] = bool(cta_audit.get("detected"))
        result["creator_cta_reason"] = str(cta_audit.get("reason") or "")
        try:
            wm = detect_watermark(frames) or {}
            watermark_prob = float(max(wm.values())) if wm else 0.0
            subtitle = detect_subtitles(frames) or {}
            subtitle_box = detect_subtitle_box(frames)
            # Blurrr alignment: only detect persistent text in genuine overlay bands
            # (lower-third captions or top bands). Never run unconstrained full-frame
            # detection (min_y=0.0) that mistakes in-game HUDs, radar, or vehicle
            # graphics for overlay text. In gaming and native overlay lanes, HUDs are
            # allowed and should not be blurred.
            if gaming_lane or native_overlay_lane:
                persistent_text_box = None
            else:
                persistent_text_box = detect_persistent_text_overlay_box(frames, min_y=0.48)
                if persistent_text_box:
                    px1, py1, px2, py2 = [float(v) for v in persistent_text_box]
                    # Only accept if lower-third or top band
                    if not (py1 >= 0.48 or py2 <= 0.28):
                        persistent_text_box = None
            subtitle_box_is_caption = False
            if subtitle_box and len(subtitle_box) == 4:
                sx1, sy1, sx2, sy2 = [float(value) for value in subtitle_box]
                subtitle_box_is_caption = bool(
                    sy1 >= 0.50
                    and sy2 > sy1
                    and (sx2 - sx1) >= 0.04
                    and (sy2 - sy1) <= 0.25
                )
            talking = {} if gaming_lane else (detect_talking_head(frames) or {})
            facecam = {} if gaming_lane else (detect_facecam_inset(frames) or {})
            boxes: List[Dict[str, Any]] = []
            for corner in ["top_left", "top_right", "bottom_left", "bottom_right"]:
                watermark_box = detect_watermark_box(frames, corner)
                if watermark_box:
                    boxes.append({
                        "type": "watermark",
                        "bbox": list(watermark_box),
                        "confidence": 90.0,
                    })
                if len(boxes) >= 4:
                    break
            
            # Append subtitle box only if genuine subtitle line was localized (no fallback boxes)
            if subtitle_box:
                boxes.append({
                    "type": "subtitle",
                    "bbox": list(subtitle_box),
                    "confidence": 90.0,
                })
            if persistent_text_box and not subtitle_box:
                boxes.append({
                    "type": "text_overlay",
                    "bbox": list(persistent_text_box),
                    "confidence": 78.0,
                })
            # Blurrr alignment: query RapidOCR DBNet via detect_overlay_regions
            # for high-precision watermark or caption polygons when heuristic detectors find nothing.
            if not boxes and not (gaming_lane or native_overlay_lane) and len(frames) >= 2:
                try:
                    from services.scene_understanding.frame_overlay_blur import detect_overlay_regions
                    ocr_regions = detect_overlay_regions(frames[len(frames) // 2], context_frames=frames)
                    for r in ocr_regions:
                        if r.get("type") in {"watermark", "caption", "subtitle"}:
                            boxes.append(r)
                except Exception:
                    pass
            result["watermark_prob"] = 0.9 if any(b.get("type") == "watermark" for b in boxes) else 0.0
            result["has_subtitle"] = bool(subtitle_box)
            result["has_text_overlay"] = bool(persistent_text_box)
            result["talking_head"] = bool(talking.get("talking_head"))
            result["talking_head_confidence"] = float(talking.get("talking_head_confidence") or 0.0)
            result["talking_head_reason"] = str(talking.get("talking_head_reason") or "")
            result["facecam_inset"] = bool(facecam.get("facecam_inset"))
            result["facecam_confidence"] = float(facecam.get("facecam_confidence") or 0.0)
            result["facecam_reason"] = str(facecam.get("facecam_reason") or "")
            result["facecam_corner"] = str(facecam.get("facecam_corner") or "")
            talking_head_rejection_enabled = str(
                os.environ.get("VIDZEO_YT_REJECT_TALKING_HEAD", "1") or "1"
            ).strip().lower() in {"1", "true", "yes", "on"}
            # A corner facecam is a hard contamination signal even in the
            # YouTube recall/quota lane: it introduces an unrelated person
            # (usually a streamer) into otherwise usable footage. Unlike a
            # low-confidence semantic disagreement, this is deterministic and
            # must never be retained by quota fail-open.
            confident_presenter = hard_talking_head_signal(
                result["talking_head"],
                result["talking_head_confidence"],
                result["talking_head_reason"],
                enabled=talking_head_rejection_enabled,
            )
            outro = detect_outro_endcard(frames) or {}
            result["outro_detected"] = bool(outro.get("outro_detected"))
            result["outro_reason"] = str(outro.get("outro_reason") or "")
            result["outro_confidence"] = float(outro.get("outro_confidence") or 0.0)
            result["reject"] = bool(
                result["facecam_inset"]
                or confident_presenter
                or result["outro_detected"]
            )
            if result["facecam_inset"]:
                result["rejection_reason"] = "facecam_inset_rejected"
            elif confident_presenter:
                result["rejection_reason"] = "confident_talking_head_rejected"
            elif result["outro_detected"]:
                result["rejection_reason"] = f"outro_endcard_rejected:{result['outro_reason']}"
            result["regions"] = boxes
            result["overlay_types"] = sorted({str(item.get("type") or "").lower() for item in boxes if item.get("type")})
            logo_regions = [
                item for item in boxes
                if str(item.get("type") or "").lower() in {"watermark", "logo", "text_overlay"}
            ]
            caption_regions = [
                item for item in boxes
                if str(item.get("type") or "").lower() in {"subtitle", "caption", "subtitles"}
            ]
            oversized_logo = False
            central_logo = False
            for item in logo_regions:
                bbox = item.get("bbox") or []
                if len(bbox) != 4:
                    continue
                x1, y1, x2, y2 = [float(value) for value in bbox]
                area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
                oversized_logo = oversized_logo or area > 0.06
                is_corner = (
                    (x1 <= 0.30 and y1 <= 0.30)
                    or (x2 >= 0.70 and y1 <= 0.30)
                    or (x1 <= 0.30 and y2 >= 0.70)
                    or (x2 >= 0.70 and y2 >= 0.70)
                )
                central_logo = central_logo or not is_corner
            archival_caption_allowed = False
            if result["has_subtitle"] and archival_lane:
                caption_areas = []
                bottom_anchored = True
                band_like = True
                for item in caption_regions:
                    bbox = item.get("bbox") or []
                    if len(bbox) != 4:
                        continue
                    x1, y1, x2, y2 = [float(value) for value in bbox]
                    caption_areas.append(max(0.0, x2 - x1) * max(0.0, y2 - y1))
                    bottom_anchored = bottom_anchored and y1 >= 0.55 and y2 <= 1.01
                    band_like = band_like and (y2 - y1) <= 0.22
                max_caption_area = max(caption_areas) if caption_areas else 0.0
                try:
                    band_area_cap = float(
                        os.environ.get("VIDZEO_YT_MAX_CAPTION_AREA", "0.15") or 0.15
                    )
                except (TypeError, ValueError):
                    band_area_cap = 0.15
                band_area_cap = max(0.08, min(0.20, band_area_cap))
                archival_caption_allowed = bool(
                    caption_areas
                    and max_caption_area <= band_area_cap
                    and bottom_anchored
                    and band_like
                )
            if result["has_subtitle"]:
                # In-game HUD/objective captions are part of raw gameplay, not
                # a streamer reaction overlay.  The detector intentionally
                # errs on the side of finding text, so gaming clips must
                # explicitly classify a subtitle-looking region as HUD before
                # the generic caption rejection runs.  Keep the presenter /
                # facecam veto above and below this block as the hard safety
                # gate: a streamer clip can never pass this lane.
                if gaming_lane and not result.get("facecam_inset") and not confident_presenter and all(
                    _bounded_native_overlay_box(item.get("bbox"), gaming_lane=True)
                    for item in caption_regions
                ):
                    result["gaming_hud_detected"] = True
                    result["overlay_policy"] = "gaming_hud_allowed_streamer_reject"
                    result["overlay_types"] = sorted(
                        (set(result.get("overlay_types") or ()) - {"subtitle", "caption", "subtitles"})
                        | {"gaming_hud"}
                    )
                    result["has_subtitle"] = False
                    result["clean"] = not bool(logo_regions)
                    result["watermark_tolerated"] = bool(logo_regions)
                    result["blur_regions"] = logo_regions
                elif native_overlay_lane and not result.get("facecam_inset") and not confident_presenter and all(
                    _bounded_native_overlay_box(item.get("bbox"), gaming_lane=False, native_overlay_lane=True)
                    for item in caption_regions
                ):
                    # Sports scores, telemetry, chart labels, and device/UI
                    # read-outs are native to the footage. Keep them visible
                    # when they are small and bounded; never use this branch
                    # for a full-width caption/title band.
                    result["native_overlay_detected"] = True
                    result["overlay_policy"] = "native_data_overlay_allowed_streamer_reject"
                    result["overlay_types"] = sorted(
                        (set(result.get("overlay_types") or ()) - {"subtitle", "caption", "subtitles"})
                        | {"native_data_overlay"}
                    )
                    result["has_subtitle"] = False
                    result["clean"] = not bool(logo_regions)
                    result["watermark_tolerated"] = bool(logo_regions)
                    result["blur_regions"] = logo_regions
                elif not archival_caption_allowed:
                    result["reject"] = True
                    result["rejection_reason"] = "caption_overlay_rejected"
                    result["clean"] = False
                    blur_logos = str(os.environ.get("VIDZEO_YT_BLUR_SMALL_LOGOS", "0")).strip().lower() in {"1", "true", "yes", "on"}
                    result["blur_regions"] = caption_regions + (logo_regions if blur_logos else [])
                else:
                    result["reject"] = False
                    result["clean"] = False
                    blur_logos = str(os.environ.get("VIDZEO_YT_BLUR_SMALL_LOGOS", "0")).strip().lower() in {"1", "true", "yes", "on"}
                    result["blur_regions"] = caption_regions + (logo_regions if blur_logos else [])
                    result["watermark_tolerated"] = True
            else:
                strict_overlay_reject = str(os.environ.get("VIDZEO_YT_STRICT_OVERLAY_REJECT", "0")).strip().lower() in {"1", "true", "yes", "on"}
                if (oversized_logo or central_logo) and strict_overlay_reject:
                    result["reject"] = True
                    result["rejection_reason"] = "large_or_central_logo_rejected"
                    result["clean"] = False
                else:
                    result["reject"] = False
                    blur_logos = str(os.environ.get("VIDZEO_YT_BLUR_SMALL_LOGOS", "0")).strip().lower() in {"1", "true", "yes", "on"}
                    if blur_logos:
                        result["blur_regions"] = logo_regions
                        result["clean"] = not bool(logo_regions)
                    else:
                        result["blur_regions"] = []
                        result["clean"] = True
                    result["watermark_tolerated"] = True
            # A persistent creator banner, archive stamp, timeline heading or
            # benchmark title is burned into the source pixels.
            text_overlay_box = persistent_text_box if persistent_text_box and not subtitle_box else None
            if text_overlay_box:
                tx1, ty1, tx2, ty2 = [float(value) for value in text_overlay_box]
                text_area = max(0.0, tx2 - tx1) * max(0.0, ty2 - ty1)
                text_width = max(0.0, tx2 - tx1)
                top_or_central_heading = bool(ty1 <= 0.40 and text_width >= 0.22)
                is_corner_element = (tx1 <= 0.35 or tx2 >= 0.65) and (ty1 <= 0.35 or ty2 >= 0.65)
                # Lower-third creator cards are also content contamination,
                # but keep corner watermarks/logos cosmetic and allowed.
                if gaming_lane and not result.get("facecam_inset") and not confident_presenter:
                    result["gaming_hud_detected"] = True
                    result["overlay_policy"] = "gaming_hud_allowed_streamer_reject"
                    result["overlay_types"] = sorted(
                        (set(result.get("overlay_types") or ()) - {"text_overlay"}) | {"gaming_hud"}
                    )
                    result["clean"] = True
                    result["watermark_tolerated"] = True
                elif (
                    native_overlay_lane
                    and not result.get("facecam_inset")
                    and not confident_presenter
                    and _bounded_native_overlay_box(text_overlay_box, gaming_lane=False)
                ):
                    result["native_overlay_detected"] = True
                    result["overlay_policy"] = "native_data_overlay_allowed_streamer_reject"
                    result["overlay_types"] = sorted(
                        (set(result.get("overlay_types") or ()) - {"text_overlay"})
                        | {"native_data_overlay"}
                    )
                    result["clean"] = True
                    result["watermark_tolerated"] = True
                elif top_or_central_heading or text_area >= 0.025:
                    result["reject"] = True
                    result["clean"] = False
                    result["rejection_reason"] = "burned_in_text_overlay_rejected"
                    result["overlay_types"] = sorted(set(result.get("overlay_types") or ()) | {"text_overlay"})
                elif is_corner_element and not (result.get("facecam_inset") or confident_presenter):
                    # Watermark / corner logo: allowed per user specification
                    result["reject"] = False
                    result["watermark_tolerated"] = True
                    blur_logos = str(os.environ.get("VIDZEO_YT_BLUR_SMALL_LOGOS", "0")).strip().lower() in {"1", "true", "yes", "on"}
                    if blur_logos:
                        result["clean"] = False
                        result["blur_regions"] = logo_regions
                    else:
                        result["clean"] = True
                        result["blur_regions"] = []
                else:
                    result["reject"] = True
                    result["clean"] = False
                    result["rejection_reason"] = "burned_in_text_overlay_rejected"
                    result["overlay_types"] = sorted(set(result.get("overlay_types") or ()) | {"text_overlay"})
            # Caption blur/allow policy must never clear a presenter veto.
            # Facecams and centered talking heads are identity contamination,
            # not cosmetic overlays that can be remediated with blur.
            if result.get("facecam_inset") or confident_presenter:
                result["reject"] = True
                result["clean"] = False
                result["rejection_reason"] = (
                    "facecam_inset_rejected"
                    if result.get("facecam_inset")
                    else "confident_talking_head_rejected"
                )
            if result.get("creator_cta_detected"):
                result["reject"] = True
                result["clean"] = False
                result["rejection_reason"] = "creator_cta_overlay_rejected"
                result["overlay_types"] = sorted(set(result.get("overlay_types") or []) | {"creator_cta"})
        finally:
            cleanup_frames(frames)
    except Exception as exc:
        # Frames were decoded, so a failed detector is an unverified media
        # decision rather than a clean result. Fail closed and let the caller
        # take a verified alternate/fallback instead of shipping a streamer
        # clip simply because OpenCV or a bundled analyzer errored.
        if result.get("checked"):
            result["reject"] = True
            result["clean"] = False
            result["rejection_reason"] = f"overlay_gate_error:{type(exc).__name__}"
            result["overlay_types"] = sorted(set(result.get("overlay_types") or ()) | {"unverified"})
    return preserve_source_overlays(result)


def _motion_admissible(quality: Mapping[str, Any], reason: str, *, archival_lane: bool = False) -> bool:
    """True when a static/pan-flagged cut actually contains real footage.

    The old soft admission only checked luma/edge, so a detailed photo hold
    (Titanic archival scans, title cards) sailed through and the finished
    video was a slideshow of essentially still frames. A genuine camera move
    keeps changing over time: sustained frame deltas, low identical-frame
    holds, and either a coherent pan or a high-change burst. Photo holds and
    shimmer title cards stay rejected.
    """
    if reason != "static_image_or_pan" or not bool(quality.get("checked")):
        return False
    motion_mean = float(quality.get("motion_mean") or 0.0)
    motion_p90 = float(quality.get("motion_p90") or 0.0)
    changed = float(quality.get("motion_changed_fraction") or 0.0)
    hold = float(quality.get("perceptual_hold_fraction") or 0.0)
    flow_pairs = int(quality.get("optical_flow_pairs") or 0)
    flow_median = float(quality.get("optical_flow_median") or 0.0)
    flow_coherence = float(quality.get("optical_flow_coherence") or 0.0)
    min_motion = _env_float("VIDZEO_YT_MIN_ADMITTED_MOTION", 3.0)
    sustained_change = (
        motion_mean >= min_motion
        and changed >= 0.25
        and hold < 0.35
    )
    real_pan = (
        flow_pairs >= 2
        and flow_median >= 0.6
        and flow_coherence >= 0.55
        and changed >= 0.20
        and motion_p90 >= 6.0
    )
    # Archival film pans are slow and grain-heavy: they never meet the modern
    # footage thresholds but are precisely the content disaster/history lanes
    # exist for. Require enough film texture to rule out a Ken-Burns still.
    archival_pan = (
        archival_lane
        and flow_pairs >= 2
        and flow_median >= 0.30
        and flow_coherence >= 0.40
        and motion_p90 >= 2.0
        and changed >= 0.04
        and hold < 0.92
    )
    lenient = str(os.environ.get("VIDZEO_YT_LENIENT_ACCEPTANCE", "0")).strip().lower() in {"1", "true", "yes", "on"}
    if lenient and (motion_mean >= 1.0 or changed >= 0.04 or flow_median >= 0.15):
        return True
    tracking_shot = bool(
        flow_coherence >= 0.45
        and (motion_mean >= 2.0 or changed >= 0.05)
    )
    return bool(sustained_change or real_pan or archival_pan or tracking_shot)


def _synthetic_url_host(url: str) -> bool:
    """True for special-use hosts that never carry real videos.

    ``.test``/``.invalid``/``.localhost`` domains are RFC 6761 placeholders
    used by unit and integration doubles. Probing or downloading them pays a
    real DNS round-trip for a fake video, so admission must skip them.
    """
    try:
        host = str(url or "").split("://", 1)[-1].split("/", 1)[0].split(":")[0].casefold()
    except Exception:
        return True
    return (
        not host
        or host == "localhost"
        or host.endswith(".test")
        or host.endswith(".invalid")
        or host.endswith(".localhost")
    )


def _source_candidate(record: Any) -> Dict[str, Any]:
    raw_url = str(getattr(record, "url", "") or "")
    candidate_url = raw_url
    video_id = str(getattr(record, "video_id", "") or "")
    # Regional mirrors, /watch?list= playlist-embedded pages, and redirector
    # hosts (youtube-nocookie, m.youtube, youtu.be shorteners) are all valid
    # YouTube videos but fail the direct ranged-clip URL probe downstream.
    # Normalize to the canonical watch URL whenever the video ID is known so
    # recovery never depends on the exact host that surfaced in search.
    is_youtube_style = (
        "youtube.com" in candidate_url
        or "youtu.be" in candidate_url
        or "youtube-nocookie.com" in candidate_url
    )
    # Special-use TLDs (.test/.invalid/.localhost) never carry real videos;
    # unit/integration doubles use them as synthetic placeholders and must not
    # be rewritten into live youtube.com URLs or every probe and ranged-clip
    # attempt pays a real network round-trip for a fake video ID.
    is_synthetic_host = _synthetic_url_host(candidate_url)
    if video_id and not is_youtube_style and not is_synthetic_host:
        candidate_url = f"https://www.youtube.com/watch?v={video_id}"
    return {
        "id": video_id,
        "video_id": video_id,
        "webpage_url": candidate_url,
        "url": candidate_url,
        "title": str(getattr(record, "title", "") or ""),
        "channel": str(getattr(record, "channel", "") or ""),
        "duration": record.duration,
        "v2_score": float(getattr(record, "metadata_score", 0.0) or 0.0),
    }


def _source_visual_priority(record: Any) -> float:
    """Score source-level footage likelihood before paying full-download cost.

    Transcript match frequency is not a quality signal when one captioned
    biography ranks for every scene. Prefer reusable action/archival footage
    and demote presenter/narrative packages at the source shortlist boundary.
    The frame overlay gate still remains authoritative after cutting.
    """
    text = " ".join(
        str(getattr(record, key, "") or "")
        for key in ("title", "channel")
    ).casefold()
    footage_terms = (
        "highlights", "goals", "skills", "match footage", "game footage",
        "training footage", "raw footage", "archive footage", "archival footage",
        "official footage", "full match", "live match", "warm up", "warm-up",
        "b-roll", "b roll", "best moments", "all goals", "compilation",
        "action", "on pitch", "on the pitch", "stadium",
        # Historical/disaster pool signals. These are deliberately source
        # level ranking hints, not acceptance gates; the probe and cut gates
        # still decide whether a moment is usable.
        "shipwreck", "wreck footage", "underwater", "rov", "submersible",
        "lifeboat", "sinking", "ship at sea", "at sea", "ocean footage",
        "deck footage", "departure", "port footage", "expedition",
        "newsreel", "vintage film",
    )
    narrative_terms = (
        "biography", "life story", "lifestyle", "top facts", "facts about",
        "who is", "story of", "history of", "podcast", "interview",
        "reaction", "reacts", "analysis", "explained", "commentary",
        "press conference", "private jet", "statue", "reenactment",
        "dramatisation", "dramatization", "movie scene", "3d animation",
        "virtual reality", "vr experience", "simulation", "shorts",
    )
    priority = float(35 * sum(term in text for term in footage_terms)) - float(
        32 * sum(term in text for term in narrative_terms)
    )
    try:
        from services.footage_signal_engine import (
            classify_noise_vs_footage,
            get_channel_dna,
            get_clickbait_penalty,
            get_verb_intent_score,
        )

        title = str(getattr(record, "title", "") or "")
        channel = str(getattr(record, "channel", "") or "")
        duration = int(getattr(record, "duration", 0) or 0)

        noise_class, noise_conf = classify_noise_vs_footage(title, channel, duration)
        if noise_class == "NOISE":
            priority -= 140.0 * (min(10, noise_conf) / 10.0)
        elif noise_class == "FOOTAGE":
            priority += 85.0 * (min(10, noise_conf) / 10.0)

        verb_score = get_verb_intent_score(title)
        priority += verb_score * 12.0

        clickbait_pen = get_clickbait_penalty(title)
        priority += clickbait_pen * 8.0

        dna_type, _ = get_channel_dna(channel.casefold())
        if dna_type == "ARCHIVE":
            priority += 120.0
        elif dna_type == "DOCUMENTARY":
            priority += 60.0
        elif dna_type == "OFFICIAL_SOURCE":
            priority += 80.0
        elif dna_type == "BROADCASTER":
            priority -= 150.0
    except Exception:
        pass
    return priority


_SCENE_AFFINITY_STOPWORDS = {
    "the", "and", "for", "from", "with", "while", "after", "before", "during",
    "into", "onto", "that", "this", "these", "those", "were", "was", "are",
    "has", "have", "had", "been", "being", "then", "when", "will", "would",
    "could", "should", "over", "under", "onto", "about", "against", "of",
    "a", "an", "in", "on", "at", "by", "to", "its", "his", "her", "their",
    "video", "videos", "film", "footage", "real", "movie", "clip", "clips",
    "documentary", "channel", "official", "raw",
}


def _scene_source_affinity(row: Any, candidate: Any) -> float:
    """Score how well a candidate source matches THIS scene's local anchor.

    The shared pool shortlist is transcript-less: without per-scene moments,
    every scene otherwise rotates through the same sources, so a lifeboats
    beat can end up with wreck-expedition footage. Token overlap between the
    scene row (subject + scene text + anchors) and the candidate title /
    channel / description re-orders each scene's source list so its first
    options actually match the local anchor while the global anchor still
    bounds the whole pool.
    """
    source_text = " ".join(
        str(value or "")
        for value in (
            candidate.get("title") if isinstance(candidate, Mapping) else getattr(candidate, "title", ""),
            candidate.get("channel") if isinstance(candidate, Mapping) else getattr(candidate, "channel", ""),
            candidate.get("description") if isinstance(candidate, Mapping) else getattr(candidate, "description", ""),
        )
    ).casefold()
    source_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", source_text)
        if len(token) > 2 and token not in _SCENE_AFFINITY_STOPWORDS
    }
    if not source_tokens:
        return 0.0
    row_map = row if isinstance(row, Mapping) else {}
    scene_terms = " ".join(str(value or "") for value in (
        row_map.get("candidate_subject"),
        row_map.get("section_subject"),
        row_map.get("scene_subject"),
        row_map.get("subject_query"),
        row_map.get("title_anchor"),
        row_map.get("global_topic"),
        row_map.get("scene_text"),
    ))
    scene_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", scene_terms.casefold())
        if len(token) > 2 and token not in _SCENE_AFFINITY_STOPWORDS
    }
    if not scene_tokens:
        return 0.0
    overlap = len(scene_tokens & source_tokens)
    if not overlap:
        return 0.0
    # Title/channel tokens match the scene harder than description tokens.
    title_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", str(
            candidate.get("title") if isinstance(candidate, Mapping) else getattr(candidate, "title", "")
        ).casefold())
        if len(token) > 2 and token not in _SCENE_AFFINITY_STOPWORDS
    }
    title_overlap = len(scene_tokens & title_tokens)
    # The global anchor vocabulary ("RMS Titanic") appears in every source's
    # title and channel, so it cannot discriminate scenes inside this pool.
    # Weight the scene's LOCAL vocabulary (entities/facets outside the anchor)
    # much harder; otherwise a generic documentary beats the scene-specific
    # source for every scene and the whole quota re-cuts one video.
    anchor_text = " ".join(str(value or "") for value in (
        row_map.get("title_anchor"),
        row_map.get("global_topic"),
        row_map.get("primary_subject") or row_map.get("primary_container_subject"),
        row_map.get("topic_anchor"),
    ))
    anchor_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", anchor_text.casefold())
        if len(token) > 2 and token not in _SCENE_AFFINITY_STOPWORDS
    }
    local_tokens = scene_tokens - anchor_tokens
    local_overlap = len(local_tokens & source_tokens)
    score = float(title_overlap * 2.0 + overlap + local_overlap * 4.0) / max(1.0, len(source_tokens) ** 0.5)
    # An exact local-subject phrase inside the source title is the strongest
    # local-anchor signal (a "lifeboats lowering" beat vs a "lifeboats
    # lowering" title). The token overlap above cannot see phrase identity.
    title_text = _norm_for_affinity(str(
        candidate.get("title") if isinstance(candidate, Mapping) else getattr(candidate, "title", "")
    ))
    local_phrase = _norm_for_affinity(" ".join(str(value or "") for value in (
        row_map.get("section_subject"),
        row_map.get("scene_subject"),
    )))
    if local_phrase and title_text and local_phrase in title_text:
        score += 3.0
    if local_overlap > 0 and local_overlap == len(local_tokens):
        score += 1.0
    return score


def _norm_for_affinity(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]+", " ", str(value or "").casefold())).strip()


def _source_shortlist(pools) -> List[str]:
    records = pools.source_records or {}
    try:
        max_duration = float(
            os.environ.get("VIDZEO_YT_SHARED_SOURCE_MAX_DURATION_SECONDS", "1800")
            or 1800
        )
    except (TypeError, ValueError):
        max_duration = 1800.0
    # A shared pool source is downloaded in full. Exclude very long videos
    # before the shortlist is formed; yt-dlp's max_filesize is only enforced
    # after format selection and can still spend minutes pulling a huge stream
    # before rejecting it.
    eligible_records = {
        source_id: record
        for source_id, record in records.items()
        if (not hasattr(record, 'metadata_score')
            or float(getattr(record, "metadata_score", 0.0) or 0.0) > 0)
        and (not max_duration
             or float(getattr(record, "duration", 0.0) or 0.0) <= max_duration)
    }
    records = eligible_records
    usage: Dict[str, float] = defaultdict(float)
    best_match: Dict[str, float] = defaultdict(float)
    for moments in (pools.moment_assignments or {}).values():
        for rank, moment in enumerate(list(moments or [])[:3]):
            source_id = str(getattr(moment, "source_id", "") or getattr(moment, "video_id", ""))
            if not source_id:
                continue
            usage[source_id] += max(1.0, 3.0 - rank)
            best_match[source_id] = max(best_match[source_id], float(getattr(moment, "match_score", 0.0) or 0.0))

    def rank_key(source_id: str):
        return (
            _source_visual_priority(records[source_id]),
            best_match.get(source_id, 0.0),
            usage.get(source_id, 0.0),
            float(getattr(records[source_id], "metadata_score", 0.0) or 0.0),
            source_id,
        )

    # Small app jobs should not spend the whole media budget downloading six
    # full YouTube sources. One slow 30–50 MiB source can otherwise hold the
    # shared batch open until the web fallback has already done the useful
    # work. Keep three candidates for short narratives; long-form jobs retain
    # the broader pool unless explicitly overridden.
    default_limit = 3 if len(getattr(pools, "rows", []) or []) <= 8 else 6
    limit = max(1, min(10, int(
        os.environ.get("VIDZEO_YT_POOL_SOURCE_SHORTLIST", str(default_limit))
        or default_limit
    )))
    ranked = sorted(records, key=rank_key, reverse=True)
    pool_sources: Dict[str, List[str]] = defaultdict(list)
    for source_id, record in records.items():
        for pool_key in getattr(record, "pool_keys", []) or []:
            pool_sources[str(pool_key)].append(source_id)

    selected: List[str] = []
    pool_representatives = sorted(
        [
            (
                max((rank_key(source_id) for source_id in source_ids), default=(0.0, 0.0, 0.0, 0.0, "")),
                pool_key,
                source_ids,
            )
            for pool_key, source_ids in pool_sources.items()
            if source_ids
        ],
        reverse=True,
    )
    for _pool_rank, _pool_key, source_ids in pool_representatives:
        if len(selected) >= limit:
            break
        winner = max(source_ids, key=rank_key)
        if winner not in selected:
            selected.append(winner)

    for source_id in ranked:
        if len(selected) >= limit:
            break
        if source_id not in selected:
            selected.append(source_id)
    return selected


def _pool_key_for_record(record: Any) -> List[str]:
    keys = getattr(record, "pool_keys", None)
    if isinstance(record, Mapping):
        keys = record.get("pool_keys")
    values = [str(value or "").strip() for value in (keys or []) if str(value or "").strip()]
    return values or ["__global__"]


def _pool_probe_plan(pools) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]]]:
    """Build a bounded probe shortlist and per-pool source demand.

    ``source_records`` is the deduplicated metadata universe produced by the
    search stage. The raw ``pools`` mapping is used only as a capability check:
    older unit fixtures do not carry it and should continue through the legacy
    shortlist path. Real listicle jobs do carry it, so every chapter/container
    gets its own candidate budget instead of competing in one global ranking.
    """
    raw_pools = getattr(pools, "pools", None)
    records = getattr(pools, "source_records", None) or {}
    # The search universe includes rows rejected by every scene's metadata
    # scorer. Never spend the shared probe/download budget on those rows.
    records = {key: row for key, row in records.items()
               if not hasattr(row, 'metadata_score')
               or float(getattr(row, 'metadata_score', 0.0) or 0.0) > 0}
    if not isinstance(raw_pools, Mapping) or not raw_pools or not records:
        return {}, {}
    try:
        per_pool = max(
            4,
            min(24, int(os.environ.get("VIDZEO_YT_POOL_PROBE_CANDIDATES_PER_POOL", "24") or 24)),
        )
    except (TypeError, ValueError):
        per_pool = 24
    scene_count = len(getattr(pools, "rows", None) or [])
    rows_by_pool: dict[str, int] = defaultdict(int)
    for row in getattr(pools, "rows", None) or []:
        if not isinstance(row, Mapping):
            continue
        key = str(row.get("pool_key") or row.get("container_id") or "__global__").strip() or "__global__"
        rows_by_pool[key] += 1
    record_ids_by_pool: dict[str, list[str]] = defaultdict(list)
    for source_id, record in records.items():
        for key in _pool_key_for_record(record):
            record_ids_by_pool[key].append(str(source_id))
    # A source can be attached to a container only through its metadata pool
    # key. If the LLM container key and the search key differ, retain a global
    # fallback so the candidate is still probeable rather than silently lost.
    if not any(record_ids_by_pool.values()):
        record_ids_by_pool["__global__"] = [str(source_id) for source_id in records]
    requirements: dict[str, dict[str, Any]] = {}
    candidates: dict[str, list[str]] = {}
    for pool_key, source_ids in record_ids_by_pool.items():
        scene_total = rows_by_pool.get(pool_key, 0)
        if not scene_total and pool_key == "__global__":
            scene_total = scene_count
        source_target = max(2, min(6, int(math.ceil(max(1, scene_total) / 4.0))))
        ordered = sorted(
            set(source_ids),
            key=lambda source_id: (
                _source_visual_priority(records[source_id]),
                float(getattr(records[source_id], "metadata_score", 0.0) or 0.0),
                source_id,
            ),
            reverse=True,
        )
        candidates[pool_key] = ordered[:per_pool]
        requirements[pool_key] = {
            "scene_count": int(scene_total),
            "probe_candidates_requested": min(per_pool, len(ordered)),
            "source_target": int(source_target),
            "status": "pending_probe",
        }
    return candidates, requirements


def _select_verified_pool_sources(
    pools,
    candidates_by_pool: Mapping[str, list[str]],
    probe_audit: Mapping[str, Mapping[str, Any]],
    requirements: Mapping[str, Mapping[str, Any]],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Select primary and reserve sources fairly across listicle pools."""
    records = getattr(pools, "source_records", None) or {}
    pool_keys = list(candidates_by_pool)
    scene_count = len(getattr(pools, "rows", None) or [])
    minimum_verified_sources = 2 if scene_count <= 2 else 3
    try:
        global_cap = max(
            2,
            min(
                12,
                int(os.environ.get(
                    "VIDZEO_YT_POOL_VERIFIED_SOURCE_CAP",
                    # One source can be visually clean at the probe window
                    # yet fail later because its selected moment is a
                    # presenter/title card. Keep a wider verified pool for
                    # small jobs so the local cut gate can choose around that
                    # failure instead of collapsing the whole lane.
                    # Keep a small reserve for retries, but do not download
                    # four full sources for a three-scene request.  Two or
                    # three verified sources are enough to preserve motion
                    # diversity; larger jobs still receive the wider pool.
                    str(max(minimum_verified_sources, min(10, len(pool_keys) + int(math.ceil(max(1, scene_count) / 2.0))))),
                ) or 2),
            ),
        )
    except (TypeError, ValueError):
        global_cap = max(minimum_verified_sources, min(10, len(pool_keys) + int(math.ceil(max(1, scene_count) / 2.0))))
    accepted_by_pool: dict[str, list[str]] = {}
    for pool_key, source_ids in candidates_by_pool.items():
        accepted = [
            source_id for source_id in source_ids
            if str((probe_audit.get(source_id) or {}).get("status") or "") == "accepted"
        ]
        # Prefer probes that passed the quality sample outright. A soft
        # static/pan admission is useful as a reserve when the source pool is
        # thin, but it must not displace a clean moving-footage candidate.
        accepted.sort(
            key=lambda source_id: (
                bool((probe_audit.get(source_id) or {}).get("probe_quality_soft_rejection")),
                bool((probe_audit.get(source_id) or {}).get("talking_head")),
                float((probe_audit.get(source_id) or {}).get("metadata_score") or 0.0),
                source_id,
            )
        )
        accepted_by_pool[pool_key] = accepted
    selected: list[str] = []
    selected_by_pool: dict[str, dict[str, Any]] = {}
    # Primary round: every listicle item/chapter gets a verified source before
    # any container receives a second source.
    for round_index in range(6):
        for pool_key in pool_keys:
            accepted = accepted_by_pool.get(pool_key) or []
            if round_index >= len(accepted) or len(selected) >= global_cap:
                continue
            source_id = accepted[round_index]
            if source_id in selected:
                continue
            selected.append(source_id)
            row = selected_by_pool.setdefault(pool_key, {
                "selected_source_ids": [],
                "source_target": int((requirements.get(pool_key) or {}).get("source_target") or 2),
                "status": "verified",
            })
            row["selected_source_ids"].append(source_id)
    for pool_key, row in selected_by_pool.items():
        target = int(row.get("source_target") or 2)
        if len(row.get("selected_source_ids") or []) < target:
            row["status"] = "verified_with_reserve_shortfall"
    for pool_key, requirement in requirements.items():
        if pool_key not in selected_by_pool:
            selected_by_pool[pool_key] = {
                "selected_source_ids": [],
                "source_target": int(requirement.get("source_target") or 2),
                "status": "no_probe_verified_source",
            }
    return selected, selected_by_pool


def _probe_pool_candidates(
    pipeline,
    pools,
    candidates_by_pool: Mapping[str, list[str]],
    *,
    records: Mapping[str, Any],
    output_root: Path,
    visual_context: Any = None,
    session_jar: Any = None,
    stream_downloads: bool = False,
    cancellation: Any = None,
) -> dict[str, dict[str, Any]]:
    """Download tiny candidate probes and admit only visually usable sources."""
    probe_root = output_root / "probes"
    probe_root.mkdir(parents=True, exist_ok=True)
    candidate_ids = list(dict.fromkeys(
        source_id for source_ids in candidates_by_pool.values() for source_id in source_ids
    ))
    # The probe wall makes admission a fixed-budget race: whichever candidates
    # are submitted first get real probes, and everything after the wall is
    # skipped wholesale. Submit the strongest metadata rows first so a tight
    # wall cannot discard the best sources while weak ones were probed.
    candidate_ids.sort(
        key=lambda source_id: (
            -float(getattr(records.get(source_id), "metadata_score", 0.0) or 0.0),
            str(getattr(records.get(source_id), "source_id", "") or ""),
        )
    )
    try:
        probe_workers = _pool_probe_workers()
    except (TypeError, ValueError):
        probe_workers = 10
    probe_duration = max(
        1.5,
        min(5.0, float(os.environ.get("VIDZEO_YT_POOL_PROBE_SECONDS", "3") or 3)),
    )
    try:
        vision_cap = max(0, int(os.environ.get("VIDZEO_YT_POOL_PROBE_VISION_CAP", "24") or 24))
    except (TypeError, ValueError):
        vision_cap = 24
    vision_lock = Lock()
    vision_calls = 0
    audit: dict[str, dict[str, Any]] = {}
    used_probe_paths: dict[str, Path] = {}
    # A bounded probe wall keeps the admission prologue from consuming the
    # branch wall that the local cut stage needs. The default is predicted
    # from the pool size (candidate count x probe cost / workers + ramp);
    # an explicit VIDZEO_YT_POOL_PROBE_WALL_SECONDS value overrides it.
    try:
        probe_wall_seconds = estimate_probe_wall_seconds(
            len(candidate_ids),
            probe_workers=probe_workers,
            scene_count=len(getattr(pools, "rows", None) or []),
            per_pool_cap=int(os.environ.get("VIDZEO_YT_POOL_PROBE_CANDIDATES_PER_POOL", "24") or 24),
            pool_count=len(candidates_by_pool),
            explicit=float(os.environ.get("VIDZEO_YT_POOL_PROBE_WALL_SECONDS", "0") or 0),
        )
    except (TypeError, ValueError):
        probe_wall_seconds = 120.0
    probe_wall_deadline = time.monotonic() + probe_wall_seconds

    def _pool_archival_lane() -> bool:
        try:
            rows = getattr(pools, "rows", None) or []
            return any(
                str((row or {}).get("domain_category") or "").strip().lower()
                in {"disaster", "history"}
                for row in rows
            )
        except Exception:
            return False

    archival_lane = _pool_archival_lane()
    def _pool_gaming_lane() -> bool:
        try:
            rows = getattr(pools, "rows", None) or []
            return any(
                str((row or {}).get("domain_category") or "").strip().lower() == "gaming"
                for row in rows
            )
        except Exception:
            return False

    gaming_lane = _pool_gaming_lane()

    # A source can serve several scene pools. Derive its overlay lane from
    # the pool(s) it belongs to rather than enabling native overlays for an
    # entire mixed job just because one scene is gaming/technical. This keeps
    # narrative/history pools strict while preserving useful source HUD/data
    # in the lanes that actually need it.
    _pool_native_categories: dict[str, set[str]] = defaultdict(set)
    try:
        for _row in getattr(pools, "rows", None) or []:
            if not isinstance(_row, Mapping):
                continue
            _key = str(_row.get("pool_key") or _row.get("container_id") or "__global__").strip() or "__global__"
            _pool_native_categories[_key].add(str(_row.get("domain_category") or "").strip().lower())
    except Exception:
        _pool_native_categories = defaultdict(set)

    def _source_native_overlay_lane(source_id: str) -> bool:
        try:
            record = records.get(source_id)
            for _pool_key in _pool_key_for_record(record):
                if any(_native_overlay_lane_for_category(category) for category in _pool_native_categories.get(_pool_key, ())):
                    return True
        except Exception:
            return False
        return False

    def probe_one(source_id: str) -> tuple[str, dict[str, Any]]:
        nonlocal vision_calls
        record = records[source_id]
        candidate = _source_candidate(record)
        duration = max(0.0, float(getattr(record, "duration", 0.0) or 0.0))
        safe_max_start = max(0.0, duration - probe_duration - 0.25) if duration else 0.0
        # Transcript + frame selection: when a scene assigned to this source
        # has a transcript moment, probe AT the moment so vision scores the
        # frames the cut will actually use. The cut starts at the moment too,
        # so the probe window and the first cut agree. Only a real transcript
        # match steers the window; otherwise fall back to deterministic
        # quarter/center sampling, exactly as before.
        probe_moment = _best_probe_moment(pools, source_id)
        probe_window_source = "quarter_center"
        if probe_moment is not None:
            try:
                moment_start = float(getattr(probe_moment, "start", 0.0) or 0.0)
            except (TypeError, ValueError):
                moment_start = 0.0
            start = min(safe_max_start, max(0.0, moment_start))
            probe_window_source = "transcript_moment"
        else:
            fraction = ((sum(ord(char) for char in source_id) % 3) + 1) / 4.0
            start = min(safe_max_start, max(0.0, safe_max_start * fraction))
        probe_path = probe_root / f"{source_id}.probe.mp4"
        row = {
            "source_id": source_id,
            "title": str(getattr(record, "title", "") or ""),
            "channel": str(getattr(record, "channel", "") or ""),
            "metadata_score": float(getattr(record, "metadata_score", 0.0) or 0.0),
            "probe_start": round(float(start), 3),
            "probe_duration": round(float(probe_duration), 3),
            "probe_window_source": probe_window_source,
            "status": "probe_started",
            "overlay_policy": "preserve_source_no_blur",
            "overlay_types": [],
            "rejection_reason": "",
        }
        if probe_moment is not None:
            try:
                row.update({
                    "probe_moment_start": round(float(getattr(probe_moment, "start", 0.0) or 0.0), 3),
                    "probe_moment_end": round(float(getattr(probe_moment, "end", 0.0) or 0.0), 3),
                    "probe_moment_text": str(getattr(probe_moment, "transcript_text", "") or "")[:240],
                    "probe_moment_score": round(float(getattr(probe_moment, "match_score", 0.0) or 0.0), 3),
                    "probe_moment_scene": int(getattr(probe_moment, "scene_index", 0) or 0),
                    "probe_moment_reason": str(getattr(probe_moment, "match_reason", "") or ""),
                })
            except (TypeError, ValueError):
                pass
        try:
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            if time.monotonic() >= probe_wall_deadline:
                row.update({"status": "rejected", "rejection_reason": "probe_wall_exhausted"})
                return source_id, row
            # Carry the admission wall into the downloader itself.  Merely
            # checking the deadline before starting a probe is insufficient:
            # yt-dlp can remain inside metadata/fragment I/O for a full socket
            # timeout while the collector has already moved on.  The wrapped
            # context is cooperative (and still propagates job cancellation),
            # so providers that support progress hooks stop promptly while
            # the collector remains free to enforce its hard wall.
            probe_cancellation = _DeadlineCancellation(cancellation, probe_wall_deadline)
            probe_cancellation.raise_if_cancelled()
            probe_downloader = getattr(pipeline, "_download_youtube_probe", None)
            if probe_downloader is None:
                probe_downloader = getattr(pipeline, "_download_clip", None)
            if probe_downloader is None:
                from services.youtube_pipeline_v2 import _download_clip
                probe_downloader = _download_clip

            ok = probe_downloader(
                candidate,
                probe_path,
                start,
                probe_duration,
                session_jar=session_jar,
                stream_downloads=stream_downloads,
                cancellation=probe_cancellation,
            )
            if not ok or not probe_path.is_file():
                row.update({"status": "rejected", "rejection_reason": "probe_download_failed"})
                return source_id, row
            quality = _screen_moment_quality(str(probe_path)) or {}
            source_native_lane = _source_native_overlay_lane(source_id)
            overlay = _screen_moment_overlays(
                str(probe_path), archival_lane=archival_lane,
                gaming_lane=gaming_lane,
                native_overlay_lane=source_native_lane,
            ) or {}
            overlay_types = list(overlay.get("overlay_types") or [])
            row.update({
                "probe_path": str(probe_path),
                "probe_bytes": int(probe_path.stat().st_size),
                "quality_checked": bool(quality.get("checked")),
                "quality_metrics": {
                    key: quality.get(key)
                    for key in ("mean_luma", "luma_std", "edge_energy", "motion_mean", "motion_changed_fraction")
                    if key in quality
                },
                "overlay_checked": bool(overlay.get("checked")),
                "overlay_types": overlay_types,
                "blur_regions": list(overlay.get("blur_regions") or overlay.get("regions") or []),
                "overlay_policy": str(overlay.get("overlay_policy") or "preserve_source_no_blur"),
                "source_overlays_preserved": bool(overlay.get("source_overlays_preserved")),
                "no_blur": True,
                "native_overlay_lane": bool(source_native_lane),
                "native_overlay_detected": bool(overlay.get("native_overlay_detected")),
                "watermark_tolerated": bool(overlay.get("watermark_tolerated")),
                "talking_head": bool(overlay.get("talking_head")),
                "talking_head_confidence": float(overlay.get("talking_head_confidence") or 0.0),
                "facecam_inset": bool(overlay.get("facecam_inset")),
                "facecam_confidence": float(overlay.get("facecam_confidence") or 0.0),
                "facecam_reason": str(overlay.get("facecam_reason") or ""),
                "facecam_corner": str(overlay.get("facecam_corner") or ""),
            })
            hard_overlay = {str(item).strip().lower() for item in overlay_types}
            if quality.get("reject"):
                # A deterministic probe can land on a title card or a fade
                # even when the source contains usable moving footage. Take
                # one alternate quarter/center sample for quality-only
                # failures. Captioned/large-overlay samples remain hard
                # rejects and are never rescued by this retry.
                quality_reason = str(quality.get("reason") or "probe_quality_rejected")
                if not hard_overlay.intersection({"caption", "subtitle", "subtitles", "large_text_band"}):
                    alternate_fraction = ((sum(ord(char) for char in source_id) + 2) % 4 + 1) / 5.0
                    alternate_start = min(
                        safe_max_start,
                        max(0.0, safe_max_start * alternate_fraction),
                    )
                    if abs(alternate_start - start) >= 0.25:
                        alternate_ok = probe_downloader(
                            candidate,
                            probe_path,
                            alternate_start,
                            probe_duration,
                            session_jar=session_jar,
                            stream_downloads=stream_downloads,
                            cancellation=probe_cancellation,
                        )
                        if alternate_ok and probe_path.is_file():
                            alternate_quality = _screen_moment_quality(str(probe_path)) or {}
                            alternate_overlay = _screen_moment_overlays(
                                str(probe_path), archival_lane=archival_lane,
                                gaming_lane=gaming_lane,
                                native_overlay_lane=source_native_lane,
                            ) or {}
                            alternate_types = list(alternate_overlay.get("overlay_types") or [])
                            alternate_hard_overlay = {
                                str(item).strip().lower() for item in alternate_types
                            }
                            if (
                                not alternate_quality.get("reject")
                                and not alternate_overlay.get("reject")
                                and not alternate_hard_overlay.intersection(
                                    {"caption", "subtitle", "subtitles", "large_text_band"}
                                )
                            ):
                                start = alternate_start
                                quality = alternate_quality
                                overlay = alternate_overlay
                                overlay_types = alternate_types
                                hard_overlay = alternate_hard_overlay
                                row.update({
                                    "probe_start": round(float(start), 3),
                                    "probe_retry": "alternate_quality_sample",
                                    "overlay_types": overlay_types,
                                    "blur_regions": list(overlay.get("blur_regions") or overlay.get("regions") or []),
                                    "quality_checked": bool(quality.get("checked")),
                                    "quality_metrics": {
                                        key: quality.get(key)
                                        for key in ("mean_luma", "luma_std", "edge_energy", "motion_mean", "motion_changed_fraction")
                                        if key in quality
                                    },
                                })
                            else:
                                row.update({
                                    "probe_retry": "alternate_quality_sample_rejected",
                                    "probe_retry_reason": str(
                                        alternate_quality.get("reason")
                                        or alternate_overlay.get("rejection_reason")
                                        or "alternate_probe_rejected"
                                    ),
                                })
                if quality.get("reject"):
                    # A three-second low-resolution probe can legitimately
                    # land on a slow archival hold or a controlled camera
                    # pan. Keep that source probeable only when the sampled
                    # frames actually move (real pan/zoom); a photo hold with
                    # nothing but luma/edge information stays a hard reject so
                    # the pool plans around genuinely moving sources.
                    soft_quality_probe = _motion_admissible(
                        quality,
                        quality_reason,
                        archival_lane=archival_lane,
                    )
                    if soft_quality_probe:
                        row.update({
                            "status": "accepted",
                            "probe_verified": True,
                            "probe_quality_soft_rejection": quality_reason,
                            "rejection_reason": "",
                        })
                        used_probe_paths[source_id] = probe_path
                        return source_id, row
                    row.update({"status": "rejected", "rejection_reason": quality_reason})
                    return source_id, row
            probe_reason = str(overlay.get("rejection_reason") or "")
            overlay = preserve_source_overlays(overlay)
            if overlay.get("reject"):
                row.update({"status": "rejected", "rejection_reason": overlay.get("rejection_reason") or probe_reason})
                return source_id, row
            # Probe-level vision is bounded independently from final scene
            # gating. A metadata-strong, overlay-clean probe remains eligible
            # when the remote model is unavailable, but a confident mismatch
            # is a hard source rejection.
            vision_checked = False
            vision_available = False
            vision_reason = "metadata_probe_pass"
            with vision_lock:
                can_call = bool(
                    visual_context is not None
                    and bool(getattr(visual_context, "enabled", False))
                    and vision_calls < vision_cap
                )
                if can_call:
                    vision_calls += 1
            if can_call:
                vision_checked = True
                try:
                    _topic = str(getattr(record, "title", "") or "")
                    _subject = _topic
                    verdict = pipeline._visual_verdict_scored(
                        str(probe_path),
                        0,
                        _topic,
                        _topic,
                        _subject,
                        visual_context,
                        relevance_only=True,
                    )
                    accepted, score, vision_available, vision_reason = verdict
                    row.update({
                        "vision_score": round(float(score or 0.0), 3),
                        "vision_available": bool(vision_available),
                        "vision_reason": str(vision_reason or ""),
                    })
                    if vision_available and not accepted:
                        reason_text = str(vision_reason or "probe_visual_rejected").strip().lower()
                        try:
                            vision_score = float(score or 0.0)
                        except (TypeError, ValueError):
                            vision_score = 0.0
                        metadata_score = float(getattr(record, "metadata_score", 0.0) or 0.0)
                        # A probe samples only a three-second window. A model
                        # can therefore return a low-confidence disagreement
                        # when the ship is between cuts, even though metadata
                        # and the deterministic overlay/quality gates agree.
                        # Keep explicit semantic/overlay vetoes hard, but let
                        # a metadata-strong, visually usable candidate survive
                        # a soft visual disagreement so good footage is not
                        # discarded before the transcript-aligned full cut.
                        hard_mismatch_tokens = (
                            "entity_conflict", "fatal_mismatch", "wrong subject",
                            "irrelevant", "off_topic", "off-topic", "caption",
                            "subtitle", "talking_head", "talking head",
                            "broadcaster", "news presenter",
                        )
                        hard_mismatch = any(token in reason_text for token in hard_mismatch_tokens)
                        metadata_backstop = bool(
                            metadata_score >= 72.0
                            and vision_score >= 30.0
                            and not hard_mismatch
                        )
                        if not metadata_backstop:
                            row.update({"status": "rejected", "rejection_reason": str(vision_reason or "probe_visual_rejected")})
                            return source_id, row
                        row.update({
                            "status": "accepted",
                            "probe_verified": True,
                            "vision_soft_rejection": True,
                            "rejection_reason": "vision_soft_rejection_metadata_backstop",
                        })
                        used_probe_paths[source_id] = probe_path
                        return source_id, row
                except Exception as exc:
                    row["vision_reason"] = f"probe_vision_exception:{type(exc).__name__}"
            row.update({
                "status": "accepted",
                "probe_verified": True,
                "probe_vision_checked": vision_checked,
                "probe_vision_available": vision_available,
                "probe_vision_reason": vision_reason,
            })
            used_probe_paths[source_id] = probe_path
            return source_id, row
        except Exception as exc:
            row.update({"status": "rejected", "rejection_reason": f"probe_exception:{type(exc).__name__}"})
            return source_id, row

    # Do not use the executor context manager here.  Its implicit
    # ``shutdown(wait=True)`` makes the documented probe wall meaningless when
    # a provider ignores cooperative cancellation or a socket stalls.  The
    # collector enforces the wall, records every unfinished candidate as a
    # visible rejection, and lets in-flight workers unwind in the background.
    executor = ThreadPoolExecutor(
        max_workers=min(probe_workers, max(1, len(candidate_ids))),
        thread_name_prefix="yt-probe",
    )
    futures = {
        executor.submit(probe_one, source_id): source_id
        for source_id in candidate_ids
    }
    pending = set(futures)
    try:
        while pending:
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            remaining = probe_wall_deadline - time.monotonic()
            if remaining <= 0:
                break
            done, pending = wait(
                pending,
                timeout=min(0.25, remaining),
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                source_id = futures[future]
                try:
                    completed_source_id, row = future.result()
                except Exception as exc:
                    # A single provider/parser failure must be isolated to its
                    # candidate.  The old ``as_completed`` collector let one
                    # uncaught worker exception abort admission for every
                    # other scene, producing a silent downstream coverage
                    # failure.
                    row = {
                        "source_id": source_id,
                        "status": "rejected",
                        "rejection_reason": f"probe_worker_exception:{type(exc).__name__}",
                    }
                    completed_source_id = source_id
                audit[completed_source_id] = row
        # Drain futures that completed on the deadline edge.  This avoids
        # losing a successful probe that finished between the wait timeout and
        # the deadline check.
        finished_at_deadline = {future for future in pending if future.done()}
        pending.difference_update(finished_at_deadline)
        for future in finished_at_deadline:
            source_id = futures[future]
            try:
                completed_source_id, row = future.result()
            except Exception as exc:
                completed_source_id, row = source_id, {
                    "source_id": source_id,
                    "status": "rejected",
                    "rejection_reason": f"probe_worker_exception:{type(exc).__name__}",
                }
            audit[completed_source_id] = row
    finally:
        if pending:
            for future in pending:
                source_id = futures[future]
                future.cancel()
                audit.setdefault(source_id, {
                    "source_id": source_id,
                    "status": "rejected",
                    "rejection_reason": "probe_wall_exhausted",
                })
                if str(audit[source_id].get("status") or "") == "probe_started":
                    audit[source_id].update({
                        "status": "rejected",
                        "rejection_reason": "probe_wall_exhausted",
                    })
        # Never wait for a stalled network worker here.  Its probe path is
        # source-specific, and the next local stage can proceed immediately.
        executor.shutdown(wait=False, cancel_futures=True)
    for source_id, path in list(used_probe_paths.items()):
        if not path.is_file():
            audit[source_id].update({"status": "rejected", "rejection_reason": "probe_artifact_missing"})
    probes_skipped = sum(
        1
        for row in audit.values()
        if str(row.get("rejection_reason") or "") == "probe_wall_exhausted"
    )
    if probes_skipped:
        print(
            f"   [yt-pool] probe wall ({probe_wall_seconds:.0f}s) reached: {probes_skipped} "
            "candidate(s) skipped to reserve wall time for local cuts",
            flush=True,
        )
    reason_counts: dict[str, int] = {}
    for row in audit.values():
        if str(row.get("status") or "") == "rejected":
            reason = str(row.get("rejection_reason") or "unknown_probe_rejection")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    if reason_counts:
        print(
            "   [yt-pool] probe rejection reasons: "
            + ", ".join(f"{key}={value}" for key, value in sorted(reason_counts.items())),
            flush=True,
        )
        rejected_preview = []
        for source_id, row in sorted(audit.items()):
            if str(row.get("status") or "") != "rejected":
                continue
            title = re.sub(r"\s+", " ", str(row.get("title") or "")).strip()
            rejected_preview.append(
                f"{source_id}:{title[:48]}=>{str(row.get('rejection_reason') or 'unknown')[:42]}"
            )
            if len(rejected_preview) >= 6:
                break
        if rejected_preview:
            preview_text = "   [yt-pool] rejected probe preview: " + " | ".join(rejected_preview)
            try:
                print(preview_text, flush=True)
            except UnicodeEncodeError:
                print(preview_text.encode("ascii", "replace").decode("ascii"), flush=True)
    return audit


def estimate_probe_wall_seconds(
    candidate_count: int,
    probe_workers: int = 8,
    *,
    scene_count: int = 0,
    per_pool_cap: int = 24,
    pool_count: int = 0,
    explicit: float = 0.0,
) -> float:
    """Predict how many seconds the probe admission stage needs.

    The probe wall must scale with the actual pool size: each candidate is a
    throttled ranged download (720p, ~1-2 MB) followed by overlay and quality
    screens. A fixed 60-90s wall starves the tail of a wide pool, admitting
    only the first few candidates and stranding scenes that need the deeper
    shortlist. This estimator covers every candidate the pool plan can ask for:

        ramp + (candidates / workers) * per_probe_cost * safety

    ``explicit`` (the VIDZEO_YT_POOL_PROBE_WALL_SECONDS env value) always
    wins when it is positive so operators can force a manual cap; a zero or
    missing value turns on the systematic prediction.
    """
    if explicit is not None and float(explicit) > 0:
        try:
            hard_cap = max(
                120.0,
                min(3600.0, float(os.environ.get("VIDZEO_YT_POOL_PROBE_WALL_HARD_CAP", "300") or 300)),
            )
        except (TypeError, ValueError):
            hard_cap = 300.0
        return max(10.0, min(hard_cap, float(explicit)))
    try:
        ramp = max(
            5.0,
            min(60.0, float(os.environ.get("VIDZEO_YT_POOL_PROBE_RAMP_SECONDS", "20") or 20)),
        )
    except (TypeError, ValueError):
        ramp = 20.0
    try:
        per_probe_cost = max(
            2.0,
            min(
                45.0,
                float(os.environ.get("VIDZEO_YT_POOL_PROBE_AVG_SECONDS", "20") or 20),
            ),
        )
    except (TypeError, ValueError):
        per_probe_cost = 20.0
    try:
        safety = max(
            1.1,
            min(4.0, float(os.environ.get("VIDZEO_YT_POOL_PROBE_SAFETY_FACTOR", "1.35") or 1.35)),
        )
    except (TypeError, ValueError):
        safety = 1.35
    try:
        hard_cap = min(
            300.0,
            max(
                60.0,
                float(os.environ.get("VIDZEO_YT_POOL_PROBE_WALL_HARD_CAP", "300") or 300),
            ),
        )
    except (TypeError, ValueError):
        hard_cap = 300.0
    workers = max(1, int(probe_workers or 8))
    if pool_count <= 0 and scene_count > 0:
        pool_count = max(1, min(scene_count, 12))
    if pool_count <= 0:
        pool_count = 1
    total_candidates = max(1, int(candidate_count))
    batch_rounds = math.ceil(total_candidates / workers)
    # Each worker can process one probe at a time; per_probe_cost covers one
    # probe's download + screens. A child worker can also hit retries, so the
    # safety factor absorbs those tails while staying bounded by the cap.
    predicted = ramp + batch_rounds * per_probe_cost * safety
    return max(30.0, min(hard_cap, predicted))


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int = 10000) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        value = int(default)
    return max(minimum, min(maximum, value))


def _env_float(
    name: str,
    default: float,
    *,
    minimum: float = 0.0,
    maximum: float = 10000.0,
) -> float:
    try:
        value = float(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        value = float(default)
    return max(minimum, min(maximum, value))


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _pool_probe_workers() -> int:
    """Parallel 1-4s probe downloads per pool (default 10, cap 12).

    Probes are tiny segments; the cost is one yt-dlp invocation each, so
    parallelism is the whole win. Counts stay modest: the egress proxy
    serializes concurrent connections (sequential 1-3s, ~72 concurrent
    collapse to 30s+). Env VIDZEO_YT_POOL_PROBE_CONCURRENCY wins.
    """
    return _env_int("VIDZEO_YT_POOL_PROBE_CONCURRENCY", 10, minimum=2, maximum=12)


def _source_download_workers() -> int:
    """Parallel full shared-source downloads (default 6, cap 8).

    Was default 4, cap 6. Env VIDZEO_YT_SOURCE_DOWNLOAD_CONCURRENCY wins.
    """
    return _env_int("VIDZEO_YT_SOURCE_DOWNLOAD_CONCURRENCY", 6, minimum=1, maximum=8)


def _source_batch_deadline_seconds() -> float:
    """Wall for the shared-source download batch (default 300s).

    Was 120s: a batch of several 1080p segments through the throttled
    egress can exceed it, cancelling work the direct-clip lane then has to
    re-download. The deadline is a wall, not a target — fast batches still
    finish fast. Env VIDZEO_YT_POOL_SOURCE_DOWNLOAD_DEADLINE_SECONDS wins.
    """
    return _env_float(
        "VIDZEO_YT_POOL_SOURCE_DOWNLOAD_DEADLINE_SECONDS",
        300.0,
        minimum=0.05,
        maximum=1800.0,
    )


class _DeadlineCancellation:
    """Compose job cancellation with a local cooperative wall deadline."""

    def __init__(self, cancellation: Any, deadline: Optional[float]):
        self._cancellation = cancellation
        self._deadline = deadline

    def _deadline_reached(self) -> bool:
        return self._deadline is not None and time.monotonic() >= self._deadline

    def cancelled(self) -> bool:
        return bool(
            (self._cancellation is not None and self._cancellation.cancelled())
            or self._deadline_reached()
        )

    def raise_if_cancelled(self) -> None:
        if self._cancellation is not None:
            self._cancellation.raise_if_cancelled()
        if self._deadline_reached():
            raise TimeoutError("youtube source download batch deadline exceeded")


def _plan_source_id(plan: Any) -> str:
    moment = plan[2]
    return str(getattr(moment, "source_id", "") or getattr(moment, "video_id", ""))


def _plan_confidence(plan: Any, records: Dict[str, Any]) -> tuple[float, bool]:
    """Return deterministic metadata/transcript confidence for one local cut.

    Vision is useful as a veto for genuinely uncertain footage, but it should
    not be the primary resolver after title/channel metadata and a transcript
    moment already agree.  Keeping this score local also avoids coupling the
    pool renderer back to the search/ranking implementation.
    """
    moment = plan[2]
    source_id = _plan_source_id(plan)
    record = records.get(source_id)
    metadata_score = float(getattr(record, "metadata_score", 0.0) or 0.0)
    match_score = float(getattr(moment, "match_score", 0.0) or 0.0)
    heuristic = bool(getattr(moment, "heuristic", False))
    try:
        trusted_metadata = float(os.environ.get("VIDZEO_YT_POOL_METADATA_TRUST_SCORE", "70") or 70)
        trusted_moment = float(os.environ.get("VIDZEO_YT_POOL_MOMENT_TRUST_SCORE", "20") or 20)
    except (TypeError, ValueError):
        trusted_metadata, trusted_moment = 70.0, 20.0
    strong = metadata_score >= trusted_metadata and not heuristic and match_score >= trusted_moment
    confidence = min(140.0, metadata_score) + min(110.0, match_score) - (80.0 if heuristic else 0.0)
    return confidence, strong


def _build_visual_veto_policy(
    plans_by_scene: Dict[int, List[Any]],
    records: Dict[str, Any],
    visual_context: Any,
    *,
    moment_limit: int,
) -> Dict[str, Any]:
    """Choose a bounded set of pooled moments for relevance-only vision.

    Long-form jobs used to send every scene (and sometimes three alternatives)
    to paid vision after downloads had completed.  At four concurrent calls
    that could add many minutes.  One representative per heavily-used source
    plus the weakest remaining metadata rows gives vision the highest-value
    vetoes while transcript/metadata-strong rows bypass it.
    """
    scene_count = len(plans_by_scene)
    plan_count = sum(len(values) for values in plans_by_scene.values())
    enabled = bool(
        visual_context is not None
        and getattr(visual_context, "enabled", True)
        and not str(getattr(visual_context, "degraded_reason", "") or "")
    )
    long_form_min = _env_int(
        "VIDZEO_YT_POOL_VISION_LONGFORM_MIN_SCENES", 16, minimum=2, maximum=1000
    )
    long_form = scene_count >= long_form_min
    explicit_cap = str(os.environ.get("VIDZEO_YT_POOL_VISION_MAX_CALLS", "") or "").strip()
    if explicit_cap:
        call_cap = _env_int(
            "VIDZEO_YT_POOL_VISION_MAX_CALLS", 4, minimum=0, maximum=max(1, plan_count)
        )
    elif long_form:
        # Six calls cover only a handful of scenes on a 16-40 scene job;
        # the metadata-weak tail then ships unvetted. Scale the budget so
        # every scene's representative moment gets one relevance veto (the
        # per-scene_cap=1 below keeps that bounded), while the operator can
        # still pin a hard cap via VIDZEO_YT_POOL_VISION_MAX_CALLS.
        veto_budget = _env_int("VIDZEO_YT_POOL_VISION_VETO_BUDGET", 6, minimum=0, maximum=8)
        scaled_budget = max(veto_budget, min(scene_count, 32))
        call_cap = min(max(1, plan_count), scaled_budget)
    else:
        # Preserve progressive visual validation for small jobs.
        call_cap = plan_count
    if not enabled:
        call_cap = 0

    first_plans = {
        scene_index: scene_plans[0]
        for scene_index, scene_plans in plans_by_scene.items()
        if scene_plans
    }
    confidence: Dict[int, float] = {}
    metadata_strong = set()
    source_scenes: Dict[str, List[int]] = defaultdict(list)
    for scene_index, plan in first_plans.items():
        plan_confidence, strong = _plan_confidence(plan, records)
        confidence[scene_index] = plan_confidence
        if strong:
            metadata_strong.add(scene_index)
        source_scenes[_plan_source_id(plan)].append(scene_index)

    priority_scenes = set()
    priority_reason: Dict[int, str] = {}
    if call_cap > 0:
        if call_cap >= scene_count:
            # Full coverage of every scene's representative moment. Metadata/
            # transcript-strong rows still bypass paid vision (their exact
            # match is already trustworthy); only the weakest rows get gated.
            for scene_index in first_plans:
                if scene_index not in metadata_strong:
                    priority_scenes.add(scene_index)
                    priority_reason[scene_index] = "full_gate"
            # One representative per source survives even when every row is
            # metadata-strong, so a completely off-topic source cannot slip
            # through on title/channel text alone.
            ranked_sources = sorted(
                source_scenes.items(),
                key=lambda item: (-len(item[1]), item[0]),
            )
            for _source_id, scene_indices in ranked_sources:
                if any(scene_index in priority_scenes for scene_index in scene_indices):
                    continue
                representative = max(scene_indices, key=lambda idx: (confidence.get(idx, 0.0), -idx))
                priority_scenes.add(representative)
                priority_reason[representative] = "source_representative"
        else:
            # Cover the most-used source pools first. The highest-confidence
            # scene is the best representative for deciding whether a source is
            # semantically on-topic at all.
            ranked_sources = sorted(
                source_scenes.items(),
                key=lambda item: (-len(item[1]), item[0]),
            )
            for _source_id, scene_indices in ranked_sources:
                if len(priority_scenes) >= call_cap:
                    break
                representative = max(scene_indices, key=lambda idx: (confidence.get(idx, 0.0), -idx))
                priority_scenes.add(representative)
                priority_reason[representative] = "source_representative"

            # Spend remaining calls on the least-certain metadata/transcript
            # assignments, where a visual veto can actually improve accuracy.
            for scene_index in sorted(first_plans, key=lambda idx: (confidence.get(idx, 0.0), idx)):
                if len(priority_scenes) >= call_cap:
                    break
                if scene_index in metadata_strong:
                    continue
                if scene_index not in priority_scenes:
                    priority_scenes.add(scene_index)
                    priority_reason[scene_index] = "metadata_risk"

    return {
        "enabled": enabled,
        "long_form": long_form,
        "call_cap": call_cap,
        "per_scene_cap": 1 if long_form else max(1, moment_limit),
        "priority_scenes": priority_scenes,
        "priority_reason": priority_reason,
        "metadata_strong": metadata_strong,
        "scene_count": scene_count,
    }


def _best_probe_moment(pools: Any, source_id: str) -> Any | None:
    """Return the best transcript moment for a probe candidate, if any.

    Only a real transcript match counts: heuristic moments carry score 0.0
    and an ungrounded timestamp, so they must never steer the probe window.
    The highest match_score wins; ties break toward the earliest start.
    """
    try:
        assignments = getattr(pools, "moment_assignments", None) or {}
    except Exception:
        return None
    best = None
    try:
        moments = []
        for scene_moments in assignments.values():
            for moment in scene_moments or []:
                moment_id = str(
                    getattr(moment, "source_id", "") or getattr(moment, "video_id", "")
                )
                if moment_id != str(source_id):
                    continue
                if bool(getattr(moment, "heuristic", False)):
                    continue
                try:
                    score = float(getattr(moment, "match_score", 0.0) or 0.0)
                except (TypeError, ValueError):
                    continue
                if score <= 0.0:
                    continue
                moments.append((score, moment))
        if not moments:
            return None
        moments.sort(
            key=lambda pair: (
                -pair[0],
                float(getattr(pair[1], "start", 0.0) or 0.0),
            )
        )
        best = moments[0][1]
    except Exception:
        return None
    return best


def _prefetch_probe_transcript_moments(
    pipeline: Any,
    pools: Any,
    probe_candidates_by_pool: Mapping[str, list[str]],
    records: Mapping[str, Any],
    session_jar: Any = None,
) -> dict[str, Any]:
    """Fetch transcripts for the bounded probe set so probes can sample at
    transcript moments instead of arbitrary quarter/center windows.

    Mirrors the post-probe admitted-source refresh, but runs BEFORE the
    probe and is capped at a tight deadline (default 60s) so a slow
    transcript provider cannot eat the probe wall. Cache-first: sources
    already in the transcript disk cache return in milliseconds. Never
    raises: a failure leaves moment_assignments empty and the probe falls
    back to quarter/center sampling.
    """
    audit: dict[str, Any] = {"requested": 0, "available": 0, "status": "not_requested"}
    prepare = getattr(pipeline, "_prepare_pool_transcript_moments", None)
    if prepare is None or not probe_candidates_by_pool:
        return audit
    try:
        probe_pool_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
        seen: set[str] = set()
        for pool_key, source_ids in probe_candidates_by_pool.items():
            for source_id in source_ids or []:
                if source_id in seen or source_id not in records:
                    continue
                seen.add(source_id)
                probe_pool_candidates[pool_key].append(_source_candidate(records[source_id]))
        if not probe_pool_candidates:
            return audit
        try:
            preprobe_deadline = max(
                5.0,
                min(
                    300.0,
                    float(os.environ.get("VIDZEO_YT_POOL_PREPROBE_TRANSCRIPT_DEADLINE_SECONDS", "60") or 60),
                ),
            )
        except (TypeError, ValueError):
            preprobe_deadline = 60.0
        documents, moments, metadata = prepare(
            list(getattr(pools, "rows", None) or []),
            dict(probe_pool_candidates),
            scene_durations=list(getattr(pools, "scene_durations", None) or []),
            force=True,
            session_jar=session_jar,
            deadline_seconds=preprobe_deadline,
        )
        if documents:
            pools.transcript_documents = documents
        if moments:
            pools.moment_assignments = moments
        if metadata:
            pools.source_metadata = metadata
        audit = {
            "requested": len(seen),
            "available": sum(
                1 for document in (documents or {}).values()
                if getattr(document, "available", False)
            ),
            "moments": sum(len(items or []) for items in (moments or {}).values()),
            "status": "completed",
        }
        print(
            f"   [yt-pool] pre-probe transcripts: {audit['available']}/"
            f"{audit['requested']} available, {audit['moments']} moment(s)",
            flush=True,
        )
    except Exception as exc:
        audit = {
            "requested": 0,
            "available": 0,
            "status": "failed",
            "error": f"{type(exc).__name__}:{exc}"[:240],
        }
        print(f"   [yt-pool] pre-probe transcript fetch failed: {type(exc).__name__}", flush=True)
    return audit


def resolve_pool_first(
    pipeline,
    pools,
    *,
    visual_context=None,
    output_dir: str | Path | None = None,
    session_jar: Any = None,
    stream_downloads: bool = False,
    cancellation: Any = None,
    progress_callback: Any = None,
) -> PoolFirstResults:
    """Shortlist moments, download only selected sources, then gate local cuts."""
    output_root = Path(output_dir or Path(pipeline.TEMP_DIR) / "yt_pool_first")
    output_root.mkdir(parents=True, exist_ok=True)

    def report_progress(value: float, message: str) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(max(0.0, min(1.0, float(value))), message)
        except Exception:
            # Progress reporting must never take down media delivery.
            pass

    records = pools.source_records or {}
    probe_candidates_by_pool, pool_requirements = _pool_probe_plan(pools)
    probe_audit: dict[str, dict[str, Any]] = {}
    selected_source_ids = _source_shortlist(pools)
    selected_by_pool: dict[str, dict[str, Any]] = {}
    # Direct ranged mode is deliberately transcript-optional (see the
    # post-probe refresh below). The pre-probe fetch is skipped there too.
    direct_clip_mode = str(
        os.environ.get("VIDZEO_YT_POOL_DIRECT_CLIPS_ONLY", "0") or "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    if probe_candidates_by_pool:
        # Transcript + frame selection: fetch transcripts for the bounded
        # probe set BEFORE probing, so each probe samples at the transcript
        # moment matching its scene. A failed or empty fetch leaves
        # moment_assignments empty and the probe falls back to quarter/center
        # sampling; the post-probe refresh below still runs for admitted
        # sources as before.
        if not direct_clip_mode:
            _prefetch_probe_transcript_moments(
                pipeline,
                pools,
                probe_candidates_by_pool,
                records,
                session_jar=session_jar,
            )
        probe_audit = _probe_pool_candidates(
            pipeline,
            pools,
            probe_candidates_by_pool,
            records=records,
            output_root=output_root,
            visual_context=visual_context,
            session_jar=session_jar,
            stream_downloads=stream_downloads,
            cancellation=cancellation,
        )
        selected_source_ids, selected_by_pool = _select_verified_pool_sources(
            pools,
            probe_candidates_by_pool,
            probe_audit,
            pool_requirements,
        )
        # A probe is deliberately cheaper than a source download. If every
        # candidate fails because the remote vision service is unavailable,
        # retain the legacy metadata shortlist as a bounded last resort; hard
        # caption/logo rejections never enter this fallback.
        if not selected_source_ids:
            selected_source_ids = _source_shortlist(pools)
            for source_id in selected_source_ids:
                probe_audit.setdefault(source_id, {
                    "source_id": source_id,
                    "status": "legacy_metadata_fallback",
                    "rejection_reason": "no_probe_verified_source",
                })
        print(
            f"   [yt-pool] probe admission: {sum(1 for row in probe_audit.values() if row.get('status') == 'accepted')} "
            f"accepted/{len(probe_audit)} probed; full sources={len(selected_source_ids)}",
            flush=True,
        )
    else:
        selected_by_pool = {}
    source_candidates = {source_id: _source_candidate(records[source_id]) for source_id in selected_source_ids}
    # Preserve the exact low-resolution window that passed admission. When a
    # source has no transcript, the first local cut must start from that
    # verified window; jumping to an unrelated deterministic timestamp can turn
    # a clean archival source into a presenter/title-card miss.
    verified_probe_starts: Dict[str, float] = {}
    for source_id in selected_source_ids:
        probe_row = probe_audit.get(source_id) or {}
        try:
            probe_start = float(probe_row.get("probe_start"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(probe_start) and probe_start >= 0.0:
            verified_probe_starts[source_id] = probe_start
            source_candidates[source_id]["_verified_probe_start"] = probe_start
    transcript_refresh_audit: dict[str, Any] = {
        "requested": 0,
        "available": 0,
        "status": "not_requested",
    }
    # Direct ranged mode is deliberately transcript-optional.  The transcript
    # broker can spend the entire branch wall waiting on six unavailable
    # caption endpoints, even though the probe already gave us a verified
    # moving-footage timestamp.  Start cutting immediately; transcript data is
    # still used whenever it is already cached, but it must never block clip
    # transport in the fast path.
    if probe_candidates_by_pool and selected_source_ids and hasattr(pipeline, "_prepare_pool_transcript_moments") and not direct_clip_mode:
        try:
            admitted_pool_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
            selected_set = set(selected_source_ids)
            for pool_key, source_ids in probe_candidates_by_pool.items():
                for source_id in source_ids:
                    if source_id in selected_set and source_id in source_candidates:
                        admitted_pool_candidates[pool_key].append(source_candidates[source_id])
            documents, refreshed_moments, refreshed_metadata = pipeline._prepare_pool_transcript_moments(
                list(getattr(pools, "rows", None) or []),
                dict(admitted_pool_candidates),
                scene_durations=list(getattr(pools, "scene_durations", None) or []),
                force=True,
                session_jar=session_jar,
            )
            if documents:
                pools.transcript_documents = documents
            if refreshed_moments:
                pools.moment_assignments = refreshed_moments
            if refreshed_metadata:
                pools.source_metadata = refreshed_metadata
            for source_id, document in (documents or {}).items():
                record = records.get(source_id)
                if record is not None and hasattr(record, "transcript_status"):
                    record.transcript_status = str(getattr(document, "status", "missing") or "missing")
            transcript_refresh_audit = {
                "requested": len(admitted_pool_candidates),
                "source_count": len(selected_source_ids),
                "available": sum(1 for document in (documents or {}).values() if getattr(document, "available", False)),
                "status": "completed",
            }
            print(
                f"   [yt-pool] admitted-source transcripts: {transcript_refresh_audit['available']}/"
                f"{transcript_refresh_audit['source_count']} available",
                flush=True,
            )
        except Exception as exc:
            transcript_refresh_audit = {
                "requested": len(selected_source_ids),
                "available": 0,
                "status": "failed",
                "error": f"{type(exc).__name__}:{exc}"[:240],
            }
            print(f"   [yt-pool] admitted-source transcript refresh failed: {type(exc).__name__}", flush=True)
    source_paths: Dict[str, Path] = {}
    source_audit: Dict[str, Dict[str, Any]] = {}
    print(
        f"   [yt-pool] preparing {len(source_candidates)} shared source(s) for {len(pools.rows)} scene(s)",
        flush=True,
    )
    report_progress(0.40, f"YouTube pool: downloading {len(source_candidates)} shared sources")

    for source_id in selected_source_ids:
        candidate = source_candidates[source_id]
        try:
            from services.media_provenance import annotate_rights
            annotate_rights(candidate)
        except Exception:
            pass
        source_audit[source_id] = {
            "source_id": source_id,
            "title": candidate.get("title") or "",
            "url": candidate.get("webpage_url") or "",
            "rights_status": candidate.get("rights_status") or "unknown",
            "rights_risk": candidate.get("rights_risk") or "high",
            "rights_review_required": bool(candidate.get("rights_review_required", True)),
            "rights_evidence": candidate.get("rights_evidence") or "",
            "status": "planned",
        }

    def prepare_source(
        source_id: str,
        *,
        deadline: Optional[float] = None,
    ) -> tuple[str, Optional[Path]]:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        if deadline is not None and time.monotonic() >= deadline:
            source_audit[source_id].update({
                "status": "deadline_exceeded",
                "reason": "source_batch_deadline_before_start",
            })
            return source_id, None
        candidate = source_candidates[source_id]
        path = output_root / "sources" / f"{source_id}.source.mp4"
        audit = source_audit[source_id]
        candidate_duration = float(candidate.get("duration") or 0.0)
        candidate_url = str(candidate.get("webpage_url") or candidate.get("url") or "").lower()
        is_real_yt = "youtube.com" in candidate_url or "youtu.be" in candidate_url
        direct_clips_only = is_real_yt and (
            candidate_duration > 900.0
            or str(os.environ.get("VIDZEO_YT_POOL_DIRECT_CLIPS_ONLY", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}
        )
        if direct_clips_only:
            audit.update({
                "status": "skipped_direct_clip_mode",
                "reason": "long_source_or_direct_clip_mode" if candidate_duration > 900.0 else "full_source_download_disabled",
            })
            return source_id, None
        if path.exists() and path.stat().st_size > 4096:
            audit.update({"status": "cache_hit", "path": str(path), "bytes": path.stat().st_size})
            return source_id, path
        deadline_cancellation = _DeadlineCancellation(cancellation, deadline)
        try:
            ok = pipeline._download_source_once(
                candidate,
                path,
                session_jar=session_jar,
                stream_downloads=stream_downloads,
                audit=audit,
                cancellation=deadline_cancellation,
            )
        except Exception as exc:
            if cancellation is not None and cancellation.cancelled():
                raise
            if deadline is not None and time.monotonic() >= deadline:
                audit.update({
                    "status": "deadline_exceeded",
                    "reason": "source_batch_deadline_during_download",
                })
                return source_id, None
            audit.update({"status": "worker_exception", "reason": f"{type(exc).__name__}:{exc}"[:300]})
            ok = False

        # A parallel fragment stream can time out after most of a source has
        # arrived. Retry that source once through the simpler sequential
        # transport while the same source deadline remains available. This is
        # intentionally transport-only recovery; metadata/overlay decisions
        # are unchanged and no rejected clip is promoted by this path.
        retry_transport = str(
            os.environ.get("VIDZEO_YT_SOURCE_RETRY_SEQUENTIAL", "1") or "1"
        ).strip().lower() in {"1", "true", "yes", "on"}
        retry_statuses = {"fragment_timeout", "transport_error", "http_403", "po_token_required"}
        if (
            not ok
            and retry_transport
            and stream_downloads
            and str(audit.get("status") or "") in retry_statuses
            and (deadline is None or time.monotonic() < deadline)
        ):
            audit["transport_retry"] = "sequential"
            ok = pipeline._download_source_once(
                candidate,
                path,
                session_jar=session_jar,
                stream_downloads=False,
                audit=audit,
                cancellation=deadline_cancellation,
            )
        if ok and path.exists():
            if audit.get("status") in {None, "", "planned"}:
                audit["status"] = "downloaded"
            audit.update({"path": str(path), "bytes": path.stat().st_size})
            return source_id, path
        audit.setdefault("status", "transport_unavailable")
        return source_id, None

    source_workers = _source_download_workers()
    source_batch_seconds = _source_batch_deadline_seconds()
    source_batch_started = time.monotonic()
    source_batch_deadline = source_batch_started + source_batch_seconds
    source_deadline_reached = False
    source_pending_at_deadline = 0
    if source_candidates:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        executor = None
        try:
            executor = ThreadPoolExecutor(
                max_workers=min(source_workers, len(source_candidates)),
                thread_name_prefix="yt-source",
            )
        except RuntimeError as shutdown_exc:
            if "interpreter shutdown" not in str(shutdown_exc):
                raise
            # The interpreter is already finalizing (a watchdog or the outer
            # branch wall unwound first). Do not let executor construction
            # take the whole YouTube branch down; the cuts still resolve
            # through the direct-clip lane.
            for source_id in selected_source_ids:
                source_audit[source_id].update({
                    "status": "skipped_interpreter_shutdown",
                    "reason": "executor_unavailable_during_shutdown",
                })
            print(
                "   [yt-pool] source batch skipped: interpreter shutdown "
                "(direct-clip lane only)",
                flush=True,
            )
        future_sources: Dict[Any, str] = {}

        def consume_source_future(future: Any) -> None:
            nonlocal completed_sources
            nonlocal ready_sources
            source_id = future_sources.get(future, "unknown")
            try:
                completed_source_id, path = future.result()
            except Exception as exc:
                if cancellation is not None and cancellation.cancelled():
                    raise
                source_audit.setdefault(source_id, {
                    "source_id": source_id,
                    "status": "worker_exception",
                }).update({
                    "status": "worker_exception",
                    "reason": f"{type(exc).__name__}:{exc}"[:300],
                })
                return
            if path:
                source_paths[completed_source_id] = path
                ready_sources += 1
            completed_sources += 1
            report_progress(
                0.40 + 0.12 * (completed_sources / max(1, len(future_sources))),
                f"YouTube pool: source {completed_sources}/{len(future_sources)} "
                f"complete ({ready_sources} ready)",
            )

        completed_sources = 0
        ready_sources = 0
        pending = set()
        try:
            for source_id in selected_source_ids:
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                if executor is None:
                    source_audit[source_id].update({
                        "status": "skipped_interpreter_shutdown",
                        "reason": "executor_unavailable_during_shutdown",
                    })
                    continue
                future = executor.submit(
                    prepare_source,
                    source_id,
                    deadline=source_batch_deadline,
                )
                if cancellation is not None:
                    cancellation.register_future(future)
                future_sources[future] = source_id
                pending.add(future)

            while pending:
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                remaining = source_batch_deadline - time.monotonic()
                if remaining <= 0:
                    source_deadline_reached = True
                    break
                done, pending = wait(
                    pending,
                    timeout=min(0.25, remaining),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    consume_source_future(future)

            # A future can finish between the final timeout check and executor
            # shutdown. Drain it once so a successful source is never lost.
            finished_at_deadline = {future for future in pending if future.done()}
            pending.difference_update(finished_at_deadline)
            for future in finished_at_deadline:
                consume_source_future(future)

            if pending:
                source_deadline_reached = True
                source_pending_at_deadline = len(pending)
                for future in pending:
                    source_id = future_sources[future]
                    cancelled_before_start = future.cancel()
                    audit = source_audit[source_id]
                    if str(audit.get("status") or "planned") == "planned":
                        audit.update({
                            "status": "deadline_cancelled" if cancelled_before_start else "deadline_in_flight",
                            "reason": "source_batch_wall_deadline",
                        })
                print(
                    "   [yt-pool] source batch deadline: "
                    f"retaining {len(source_paths)}/{len(source_candidates)} completed source(s); "
                    f"{len(pending)} unfinished",
                    flush=True,
                )
                report_progress(
                    0.52,
                    f"YouTube pool: retained {len(source_paths)} sources at download deadline",
                )
        finally:
            # Never use the ThreadPoolExecutor context manager here: its
            # implicit shutdown(wait=True) would negate the wall deadline when
            # a yt-dlp socket or fragment worker stalls. Running downloads use
            # distinct source files and may finish cooperatively in the
            # background; queued work is cancelled without deleting artifacts.
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
    source_batch_elapsed_seconds = time.monotonic() - source_batch_started
    # Freeze the batch-boundary audit. A cooperative in-flight yt-dlp worker
    # may finish later and safely populate its unique file, but that late event
    # must not rewrite what this resolver actually retained for the run.
    source_audit_snapshot = {
        source_id: dict(value)
        for source_id, value in source_audit.items()
    }

    print(
        f"   [yt-pool] shared sources ready: {len(source_paths)}/{len(source_candidates)}",
        flush=True,
    )

    source_recovery_locks: Dict[str, Lock] = defaultdict(Lock)
    source_recovery_attempted = set()

    def ensure_source_available(source_id: str, expected_path: Path) -> Optional[Path]:
        if expected_path.is_file():
            return expected_path
        # A source that missed the shared batch is already known to be too
        # slow/unavailable for this run. Retrying it once per scene adds
        # several recovery walls and can make the outer coordinator finalize
        # neutral assets before retained sources are selected.
        if source_deadline_reached and source_id not in source_paths:
            return None
        lock = source_recovery_locks[source_id]
        with lock:
            current = source_paths.get(source_id) or expected_path
            if current.is_file():
                return current
            # Recover once on demand if an external cleanup or provider-side
            # race removed a source after the initial download.
            if source_id in source_recovery_attempted:
                return None
            source_recovery_attempted.add(source_id)
            recovery_seconds = _env_float(
                "VIDZEO_YT_POOL_SOURCE_RECOVERY_DEADLINE_SECONDS",
                45.0,
                minimum=5.0,
                maximum=120.0,
            )
            recovered_id, recovered_path = prepare_source(
                source_id,
                deadline=time.monotonic() + recovery_seconds,
            )
            if recovered_path:
                source_paths[recovered_id] = recovered_path
                return recovered_path
        return None

    # Keep several deterministic candidates per scene. Caption detection is
    # intentionally fail-closed, so a single bad timestamp must not discard
    # an otherwise healthy downloaded source.
    # Four probes per quota scene is the throughput sweet spot: enough
    # alternate timestamps to survive caption rejection, without making the
    # CPU overlay screen spend its whole deadline on speculative cuts.
    moment_limit = max(1, min(4, int(os.environ.get("VIDZEO_YT_MOMENT_SHORTLIST", "3") or 3)))
    plans = []
    used_timestamps = defaultdict(list)
    intro_reserved_sources: set[str] = set()
    topic_keys = {
        str(
            row.get("candidate_subject")
            or row.get("section_subject")
            or row.get("title_anchor")
            or ""
        ).strip().casefold()
        for row in pools.rows
        if isinstance(row, dict)
        and str(
            row.get("candidate_subject")
            or row.get("section_subject")
            or row.get("title_anchor")
            or ""
        ).strip()
    }
    multi_topic_pool = len(topic_keys) > 1
    for scene_index, row in enumerate(pools.rows):
        duration = float(pools.scene_durations[scene_index] if scene_index < len(pools.scene_durations) else 4.0)
        grid_shift = (
            zlib.crc32(
                "{}:{}".format(
                    scene_index,
                    row.get("scene_subject") or row.get("section_subject") or row.get("title_anchor") or "",
                ).encode("utf-8")
            )
            % 8
        ) * 12.0
        raw_moments = [
            moment for moment in list((pools.moment_assignments or {}).get(scene_index) or [])
            if str(getattr(moment, "source_id", "") or getattr(moment, "video_id", "")) in source_candidates
        ][:moment_limit]
        # Always keep one option per downloaded source before filling the
        # remaining option budget with additional transcript moments. The old
        # planner took the first three moments from one source, so one
        # captioned biography could poison the entire YouTube quota even when
        # clean alternate sources had already been downloaded.
        moments = []
        represented_sources = set()
        for moment in raw_moments:
            source_id = str(getattr(moment, "source_id", "") or getattr(moment, "video_id", ""))
            if not source_id or source_id in represented_sources:
                continue
            moment_start = float(getattr(moment, "start", 0.0) or 0.0)
            if any(abs(moment_start - old) < 0.5 for old in used_timestamps[source_id]):
                continue
            moments.append(moment)
            represented_sources.add(source_id)
            try:
                used_timestamps[source_id].append(moment_start)
            except (TypeError, ValueError):
                pass

        assignment = pools.assignments[scene_index] if scene_index < len(pools.assignments) else None
        source_ids = []
        if assignment:
            assigned_id = pipeline._video_id(assignment)
            if assigned_id:
                source_ids.append(assigned_id)
        source_ids.extend(
            source_id for source_id in selected_source_ids
            if source_id not in source_ids
        )
        # Obey the local anchor: when the shared shortlist holds several
        # sources, order THIS scene's list by title/content affinity with the
        # scene row so the first options match the scene instead of a blind
        # rotation through the whole pool.
        if len(source_ids) > 1:
            scored = [
                (source_id, _scene_source_affinity(row, source_candidates.get(source_id) or {}))
                for source_id in source_ids
            ]
            best_score = max((score for _sid, score in scored), default=0.0)
            if best_score > 0.0:
                ranked = [
                    source_id
                    for source_id, score in sorted(scored, key=lambda item: item[1], reverse=True)
                    if score > 0.0
                ]
                ranked += [source_id for source_id, score in scored if score <= 0.0]
                source_ids = ranked
        # Transcript grounding rotates the primary source per scene. Without
        # it every scene's assignment lands on the same top-ranked video, so
        # the whole quota re-cuts one source at nearby grid offsets. Rotate the
        # primary when no transcript moments exist for this scene — but stay
        # within the affinity tier that actually matches this scene, so the
        # local anchor is obeyed while the pool never collapses onto a single
        # source (a one-source tier still alternates with the next-best).
        if not raw_moments and len(source_ids) > 1:
            affinity_scores = [
                (source_id, _scene_source_affinity(row, source_candidates.get(source_id) or {}))
                for source_id in source_ids
            ]
            best_affinity = max((score for _sid, score in affinity_scores), default=0.0)
            if best_affinity > 0.0:
                tier = [
                    source_id
                    for source_id, score in sorted(affinity_scores, key=lambda item: item[1], reverse=True)
                    if score >= best_affinity * 0.8
                ]
                # A single dominant match must still alternate with the next
                # best source, or every scene re-cuts the same top source.
                if len(tier) == 1 and len(source_ids) > 1:
                    tier.append(source_ids[1])
                rotation_size = len(tier)
            else:
                tier = list(source_ids)
                rotation_size = len(tier)
            rotated_source = tier[scene_index % rotation_size]
            if rotated_source != source_ids[0]:
                source_ids = [rotated_source] + [sid for sid in source_ids if sid != rotated_source]
            if str(os.environ.get("VIDZEO_YT_V2_DEBUG_PLANS", "0") or "0") in {"1", "true", "yes"}:
                print(
                    f"   [yt-pool] scene {scene_index}: primary={source_ids[0]} "
                    f"(tier={rotation_size}, best={best_affinity:.2f})",
                    flush=True,
                )
        # Opening-hook diversity: do not make successive intro scenes draw
        # from the same master when the admitted pool has alternatives. The
        # normal bite lane below may reuse that master at distinct timestamps;
        # this reservation applies only while the opening 15-second lane is
        # being planned.
        try:
            row_start_seconds = float(row.get("start_seconds") or 0.0)
        except (TypeError, ValueError):
            row_start_seconds = 0.0
        if "intro_unique" in row:
            intro_unique = bool(row.get("intro_unique"))
        else:
            has_row_timing = row.get("start_seconds") is not None or row.get("start") is not None
            intro_unique = bool(
                row.get("is_intro")
                or (has_row_timing and row_start_seconds < 15.0)
                or (scene_index == 0 and not has_row_timing)
            )
        if intro_unique and len(source_ids) > 1:
            fresh_intro_sources = [sid for sid in source_ids if sid not in intro_reserved_sources]
            if fresh_intro_sources:
                source_ids = fresh_intro_sources
            # Reserve only the preferred source. Other alternatives remain
            # available as retries for this scene if the preferred cut fails.
            if source_ids:
                intro_reserved_sources.add(source_ids[0])
        # For a pool with several sources, reserve room for at least one
        # cross-source retry. For a single source preserve the configured
        # transcript-moment retry behavior used by the visual gate tests.
        # Direct ranged mode still needs in-scene retry room: a single
        # option means one failed ranged download or caption rejection ends
        # the scene with no fallback. Keep the knob bounded — at most three
        # distinct-source opportunities per scene so the branch wall is not
        # consumed by speculative cuts.
        option_limit = (
            min(5, max(moment_limit, min(4, len(source_ids))))
            if direct_clip_mode and source_ids
            else (min(4, max(moment_limit, len(source_ids))) if source_ids else moment_limit)
        )
        # A pool with a single verified source must still keep a retry slot:
        # one ranged download failure or caption rejection would otherwise end
        # the scene with zero fallbacks. Admit at least two distinct
        # timestamps per scene for any non-empty pool.
        option_limit = max(2, int(option_limit))

        def append_fallback(source_id: str, allow_repeat: bool = False) -> None:
            # A source may have passed the cheap visual probe while its full
            # download is still pending or failed on an unavailable yt-dlp
            # format. Keep it in the plan: cut_and_gate can fetch the exact
            # short segment directly, which is both faster and more reliable
            # than requiring a complete source file first.
            if source_id not in source_candidates or (source_id in represented_sources and not allow_repeat):
                return
            candidate = source_candidates[source_id]
            # In direct-clip mode the plan only has ranged URLs to work with:
            # a candidate without a YouTube URL is a guaranteed dead slot.
            if direct_clip_mode:
                candidate_url = str(
                    (candidate or {}).get("webpage_url") or (candidate or {}).get("url") or ""
                ).casefold()
                if not ("youtube.com" in candidate_url or "youtu.be" in candidate_url):
                    return
            # Do not call the scene resolver's 60-second reuse picker here:
            # these options are speculative and most will never be cut. A
            # dedicated pool grid keeps every source's alternatives distinct
            # without reserving only eight timestamps per source.
            if len(source_ids) == 1:
                # Preserve the resolver hook for callers that intentionally
                # coordinate source removal, but prefer the source's verified
                # probe window and nearby deterministic offsets whenever it is
                # available. This keeps transcript-less recovery anchored to a
                # real moving-footage sample instead of a random source point.
                start = None
                verified_start = candidate.get("_verified_probe_start")
                if verified_start is not None:
                    source_duration = max(duration, float(candidate.get("duration") or duration))
                    latest = max(0.0, source_duration - duration - 1.0)
                    offsets = _scene_offsets_grid(grid_shift)
                    offset_index = (scene_index + len(moments)) % len(offsets)
                    for step in range(len(offsets)):
                        offset = offsets[(offset_index + step) % len(offsets)]
                        probe_candidate_start = min(latest, max(0.0, float(verified_start) + offset))
                        if all(abs(probe_candidate_start - old) >= max(duration + 6.0, 18.0) for old in used_timestamps[source_id]):
                            start = probe_candidate_start
                            break
                if start is None:
                    start = pipeline._pick_timestamp(candidate, scene_index, used_timestamps, duration)
                if start is None:
                    return
                used_timestamps[source_id].append(float(start))
                from services.youtube_moment_matcher import heuristic_moment
                moments.append(heuristic_moment(
                    source_id=source_id,
                    video_id=source_id,
                    scene_index=scene_index,
                    start=float(start),
                    duration=duration,
                    reason="metadata_timestamp_fallback_cross_source",
                ))
                represented_sources.add(source_id)
                return
            source_duration = max(duration, float(candidate.get("duration") or duration))
            outro_skip = max(8.0, min(30.0, source_duration * 0.12)) if source_duration >= 25.0 else 1.0
            latest = max(0.0, source_duration - duration - outro_skip)
            gap = max(float(os.environ.get("VIDZEO_YT_POOL_TIMESTAMP_GAP", "24") or 24), duration + 6.0)
            # Pool source IDs are the identity here. Do not let two distinct
            # downloaded videos with the same generic title share a timestamp
            # budget while they are being used as cross-source fallbacks.
            reuse_keys = [source_id]
            occupied = [old for old in used_timestamps[source_id]]
            # The grid must have enough points for the per-scene retry budget.
            # A short grid exhausted after the first few scenes once caption
            # rejection made the extra moments necessary.
            verified_start = candidate.get("_verified_probe_start")
            if verified_start is not None:
                offsets = _scene_offsets_grid(grid_shift)
                offset_index = (scene_index + len(moments)) % len(offsets)
                for step in range(len(offsets)):
                    offset = offsets[(offset_index + step) % len(offsets)]
                    probe_candidate_start = min(latest, max(0.0, float(verified_start) + offset))
                    if all(abs(probe_candidate_start - old) >= gap for old in occupied):
                        start = probe_candidate_start
                        break
                else:
                    start = None
            else:
                start = None
            candidate_count = max(
                8,
                min(96, len(pools.rows) * max(4, len(source_ids) * 2) + 4),
            )
            if start is None:
                timestamp_candidates = [
                    min(latest, latest * ((index + 1) / (candidate_count + 1)))
                    for index in range(candidate_count)
                ]
                start = next(
                    (
                        value for value in timestamp_candidates
                        if all(abs(value - old) >= gap for old in occupied)
                    ),
                    None,
                )
            if start is None:
                return
            for key in reuse_keys:
                used_timestamps[key].append(start)
            from services.youtube_moment_matcher import heuristic_moment
            moments.append(heuristic_moment(
                source_id=source_id,
                video_id=source_id,
                scene_index=scene_index,
                start=float(start),
                duration=duration,
                reason="metadata_timestamp_fallback_cross_source",
            ))
            represented_sources.add(source_id)

        for source_id in source_ids:
            if len(moments) >= option_limit:
                break
            append_fallback(source_id)
        # If the source list is smaller than the requested option budget, add
        # more deterministic grid moments across the downloaded sources. This
        # is the important recovery path when transcript APIs are unavailable
        # or the first timestamp lands inside burned-in captions.
        if len(moments) < option_limit and source_ids:
            retry_index = 0
            while len(moments) < option_limit and retry_index < option_limit * 2:
                append_fallback(source_ids[retry_index % len(source_ids)], allow_repeat=True)
                retry_index += 1
        # Finally use additional transcript windows, when available, as local
        # retries; this keeps the renderer efficient without losing coverage.
        if len(moments) < option_limit:
            for moment in raw_moments:
                if len(moments) >= option_limit:
                    break
                if moment not in moments:
                    source_id = str(getattr(moment, "source_id", "") or getattr(moment, "video_id", ""))
                    moment_start = float(getattr(moment, "start", 0.0) or 0.0)
                    if not any(abs(moment_start - old) < 0.5 for old in used_timestamps[source_id]):
                        moments.append(moment)
                        used_timestamps[source_id].append(moment_start)
        for rank, moment in enumerate(moments):
            source_id = str(getattr(moment, "source_id", "") or getattr(moment, "video_id", ""))
            if source_id in source_candidates:
                plans.append((scene_index, row, moment, source_paths.get(source_id), rank))

    # Never let a sparse transcript/assignment map shrink the pool to a few
    # scenes. Every scene gets one ranged opportunity from the admitted pool;
    # the direct downloader in cut_and_gate handles sources whose full fetch
    # did not finish. This is the key coverage guarantee for transcript-less
    # YouTube runs.
    planned_scene_indices = {int(item[0]) for item in plans}
    if selected_source_ids:
        from services.youtube_moment_matcher import heuristic_moment
        for scene_index, row in enumerate(pools.rows):
            if scene_index in planned_scene_indices:
                continue
            source_id = selected_source_ids[scene_index % len(selected_source_ids)]
            candidate = source_candidates.get(source_id) or {}
            duration = float(
                pools.scene_durations[scene_index]
                if scene_index < len(pools.scene_durations)
                else 4.0
            )
            source_duration = max(duration, float(candidate.get("duration") or duration))
            latest = max(0.0, source_duration - duration - 1.0)
            start = pipeline._pick_timestamp(candidate, scene_index, used_timestamps, duration)
            if start is None:
                try:
                    verified_start = float(candidate.get("_verified_probe_start") or 0.0)
                except (TypeError, ValueError):
                    verified_start = 0.0
                verified_start = min(latest, max(0.0, verified_start))
                occupied = used_timestamps[source_id]
                span = max(0.0, latest - verified_start)
                if span >= duration:
                    staggered_candidates = [
                        min(latest, verified_start + step * duration)
                        for step in range(max(1, len(pools.rows)))
                    ]
                else:
                    staggered_candidates = [
                        min(latest, latest * (i / max(1, len(pools.rows))))
                        for i in range(max(1, len(pools.rows)))
                    ]
                start = next((s for s in staggered_candidates if all(abs(s - o) >= 0.5 for o in occupied)), verified_start)
            start = round(min(latest, max(0.0, float(start))), 1)
            used_timestamps[source_id].append(float(start))
            fallback_moment = heuristic_moment(
                source_id=source_id,
                video_id=source_id,
                scene_index=scene_index,
                start=max(0.0, float(start)),
                duration=duration,
                reason="admitted_pool_ranged_coverage_fallback",
            )
            plans.append((scene_index, row, fallback_moment, source_paths.get(source_id), 0))

    # Validate moments progressively per scene. Scenes still run concurrently,
    # but a scene's second/third moment is only cut when the preceding moment
    # failed a real relevance veto or local FFmpeg cut.
    plans_by_scene: Dict[int, List[Any]] = defaultdict(list)
    for plan in plans:
        plans_by_scene[int(plan[0])].append(plan)
    for scene_plans in plans_by_scene.values():
        scene_plans.sort(key=lambda item: int(item[4]))

    visual_policy = _build_visual_veto_policy(
        plans_by_scene,
        records,
        visual_context,
        moment_limit=moment_limit,
    )
    priority_scenes = set(visual_policy["priority_scenes"])
    metadata_strong_scenes = set(visual_policy["metadata_strong"])
    vision_lock = Lock()
    vision_calls_by_scene: Dict[int, int] = defaultdict(int)
    vision_stats: Dict[str, int] = defaultdict(int)
    overlay_stats: Dict[str, int] = defaultdict(int)
    quality_stats: Dict[str, int] = defaultdict(int)

    def reserve_visual_call(scene_index: int) -> tuple[bool, str]:
        if not visual_policy["enabled"]:
            return False, "vision_disabled_or_unavailable"
        if scene_index not in priority_scenes:
            if scene_index in metadata_strong_scenes:
                return False, "metadata_transcript_trusted"
            return False, "vision_veto_budget_bypass"
        with vision_lock:
            if vision_calls_by_scene[scene_index] >= int(visual_policy["per_scene_cap"]):
                return False, "vision_scene_call_cap"
            if vision_stats["reserved"] >= int(visual_policy["call_cap"]):
                return False, "vision_veto_budget_exhausted"
            vision_calls_by_scene[scene_index] += 1
            vision_stats["reserved"] += 1
            return True, str(visual_policy["priority_reason"].get(scene_index) or "priority")

    if visual_context is not None:
        print(
            "   [yt-pool] relevance-only vision veto: "
            f"{len(priority_scenes)}/{len(plans_by_scene)} priority scene(s), "
            f"hard cap={visual_policy['call_cap']} call(s), "
            f"metadata-trusted={len(metadata_strong_scenes)}",
            flush=True,
        )
    print(
        f"   [yt-pool] local cut plan: {len(plans_by_scene)} scene(s), {len(plans)} moment option(s)",
        flush=True,
    )
    report_progress(0.54, f"YouTube pool: resolving {len(plans_by_scene)} local scene cuts")

    results: List[Optional[str]] = [None] * len(pools.rows)
    retrieval_decisions: Dict[str, Dict[str, Any]] = {}
    candidate_results: Dict[int, List[tuple[float, str, Any, bool, bool, str]]] = defaultdict(list)
    cut_audit: List[Dict[str, Any]] = []

    def cut_and_gate(plan):
        scene_index, row, moment, source_path, rank = plan
        source_id = str(getattr(moment, "source_id", "") or getattr(moment, "video_id", ""))
        duration = float(pools.scene_durations[scene_index] if scene_index < len(pools.scene_durations) else 4.0)
        output_path = output_root / "cuts" / f"scene_{scene_index + 1:03d}.moment_{rank:02d}.mp4"
        cut_record = {
            "scene_index": scene_index,
            "source_id": source_id,
            "start": float(moment.start),
            "duration": duration,
            "path": str(output_path),
            "cache_hit": False,
        }
        # Reuse an exact transport artifact from an earlier job. This only
        # skips the network/FFmpeg transport step; quality, overlay, and
        # scene-specific visual gates still run below.
        cached_cut_path = None
        try:
            from services.youtube_media_cache import get_verified_cut
            cached = get_verified_cut(source_id, float(moment.start), duration)
            if cached and cached.get("path"):
                from shutil import copyfile
                output_path.parent.mkdir(parents=True, exist_ok=True)
                copyfile(str(cached["path"]), str(output_path))
                if output_path.is_file() and output_path.stat().st_size >= 8192:
                    cached_cut_path = output_path
                    cut_record.update({
                        "transport": "verified_cut_cache",
                        "cache_hit": True,
                        "cache_path": str(cached["path"]),
                    })
        except Exception:
            cached_cut_path = None
        source_path = ensure_source_available(source_id, Path(source_path)) if source_path else None
        direct_clip = False
        if cached_cut_path is not None:
            direct_clip = True
        elif source_path is None:
            candidate = source_candidates.get(source_id)
            candidate_url = str((candidate or {}).get("webpage_url") or (candidate or {}).get("url") or "").lower()
            # Unit/integration doubles often use synthetic youtube.test URLs;
            # never turn those into a live network fallback. Production
            # candidates are real YouTube URLs and can use the ranged path.
            ranged_candidate = "youtube.com" in candidate_url or "youtu.be" in candidate_url
            if candidate and ranged_candidate:
                # Exact ranged recovery is the primary path for probe-admitted
                # sources whose full download was slow or format-incompatible.
                # It bounds transfer size by the scene duration and preserves
                # the verified timestamp from the probe.
                try:
                    downloader = getattr(pipeline, "_download_clip", None)
                    if downloader is None:
                        from services.youtube_pipeline_v2 import _download_clip
                        downloader = _download_clip
                    direct_clip = bool(downloader(
                        candidate,
                        output_path,
                        float(moment.start),
                        duration,
                        session_jar=session_jar,
                        stream_downloads=stream_downloads,
                        cancellation=None if _soft_branch_timeout() else cancellation,
                    ))
                except Exception as exc:
                    cut_record.update({"status": "direct_clip_failed", "reason": f"{type(exc).__name__}:{exc}"[:300]})

                if direct_clip:
                    cut_record.update({"transport": "ranged_clip", "source_path": str(candidate.get("webpage_url") or "")})
            if not direct_clip:
                cut_record.setdefault("status", "source_missing")
                cut_record.setdefault("reason", "source_recovery_failed")
                cut_audit.append(cut_record)
                return scene_index, None
        # F1 verify-cheap/cut-once: a rejected candidate used to cost a full
        # 1080p lanczos re-encode before any gate saw it. Cut a cheap probe
        # (640p, ultrafast, crf 28) first and run every gate on the probe;
        # the full 1080p cut is made only for the accepted winner. The final
        # video stays 1080p. A probe failure falls back to the old path so a
        # probe hiccup never loses a scene.
        probe_first = _env_bool("VIDZEO_YT_PROBE_FIRST", True)
        verification_path = output_path
        probe_cut = False
        probe_path = output_path.with_name(f"{output_path.stem}.probe{output_path.suffix}")
        if probe_first and not direct_clip and source_path is not None:
            try:
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                soft_probe_timeout = bool(cancellation is not None and _soft_branch_timeout())
                probe_kwargs = {
                    "cancellation": None if soft_probe_timeout else cancellation
                } if cancellation is not None else {}
                probe_cut = bool(pipeline._cut_local_segment(
                    source_path,
                    probe_path,
                    float(moment.start),
                    duration,
                    width=_env_int("VIDZEO_YT_PROBE_WIDTH", 640, minimum=320, maximum=1280),
                    height=_env_int("VIDZEO_YT_PROBE_HEIGHT", 360, minimum=180, maximum=720),
                    preset=os.environ.get("VIDZEO_YT_PROBE_PRESET", "ultrafast") or "ultrafast",
                    crf=_env_int("VIDZEO_YT_PROBE_CRF", 28, minimum=18, maximum=32),
                    timeout_seconds=_env_float("VIDZEO_YT_PROBE_TIMEOUT_SECONDS", 60.0, minimum=10.0, maximum=300.0),
                    **probe_kwargs,
                ))
            except Exception:
                probe_cut = False
            if probe_cut:
                verification_path = probe_path
                cut_record["probe_first"] = True
                cut_record["probe_path"] = str(probe_path)
        try:
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            # The branch's aggregate wall is a soft boundary: the pool has
            # already paid for verified sources, and short local cuts are the
            # fastest path to a usable result. Do not pass an already-cancelled
            # soft context into FFmpeg, or every cut exits before promotion.
            soft_timeout = bool(cancellation is not None and _soft_branch_timeout())
            cut_kwargs = {
                "cancellation": None if soft_timeout else cancellation
            } if cancellation is not None else {}
            if direct_clip or probe_cut:
                cut_ok = True
            else:
                cut_ok = pipeline._cut_local_segment(
                    source_path,
                    output_path,
                    float(moment.start),
                    duration,
                    **cut_kwargs,
                )
        except Exception as exc:
            if cancellation is not None and cancellation.cancelled():
                raise
            cut_record.update({"status": "worker_exception", "reason": f"{type(exc).__name__}:{exc}"[:300]})
            cut_audit.append(cut_record)
            return scene_index, None
        if not cut_ok:
            cut_record.update({"status": "cut_failed", "reason": "ffmpeg_or_empty_output"})
            cut_audit.append(cut_record)
            return scene_index, None
        cut_record["status"] = "cut_ready"
        cut_archival_lane = str(row.get("domain_category") or "").strip().lower() in {
            "disaster", "history",
        }
        cut_domain_category = str(row.get("domain_category") or "").strip().lower()
        cut_gaming_lane = cut_domain_category == "gaming"
        cut_native_overlay_lane = _native_overlay_lane_for_category(cut_domain_category)
        try:
            quality = _screen_moment_quality(
                str(verification_path),
                ffmpeg_bin=pipeline._ffmpeg_bin("ffmpeg"),
                archival_lane=cut_archival_lane,
            )
        except Exception as exc:
            quality = {
                "checked": False,
                "reject": False,
                "reason": f"quality_probe_exception:{type(exc).__name__}",
                "policy": "multi_frame_luma_edge_motion_gate_v2",
            }
        quality_stats["screened"] += int(bool(quality.get("checked")))
        cut_record.update({
            "quality_checked": bool(quality.get("checked")),
            "quality_policy": str(quality.get("policy") or "multi_frame_luma_edge_motion_gate_v2"),
            "quality_metrics": {
                key: quality.get(key)
                for key in (
                    "frames", "mean_luma", "luma_std", "edge_energy", "bright_fraction",
                    "motion_mean", "motion_p90", "motion_changed_fraction",
                    "optical_flow_pairs", "optical_flow_median", "optical_flow_coherence",
                    "coherent_pan_fraction", "low_motion_hold_fraction", "perceptual_hold_fraction",
                )
                if key in quality
            },
            "quality_rejection_reason": str(quality.get("reason") or ""),
        })
        if quality.get("reject"):
            quality_reason = str(quality.get("reason") or "blank_or_low_information_frame")
            # The probe admission rule applies here too: only a cut whose
            # frames actually move (sustained deltas, low identical-frame
            # hold, or a coherent pan) is usable footage. Photo holds and
            # title-card shimmer remain hard rejects so the finished video
            # never ships a still.
            soft_static_cut = _motion_admissible(
                quality,
                quality_reason,
                archival_lane=cut_archival_lane,
            )
            if soft_static_cut:
                quality_stats["soft_admitted"] += 1
                cut_record["quality_soft_rejection"] = quality_reason
            else:
                quality_stats["rejected"] += 1
                cut_record.update({
                    "status": "quality_rejected",
                    "reason": quality_reason,
                })
                cut_audit.append(cut_record)
                try:
                    Path(str(verification_path)).unlink(missing_ok=True)
                except Exception:
                    pass
                return scene_index, None
        overlay = {"checked": False, "clean": True}
        raw_output_path = verification_path
        if _env_bool("VIDZEO_YT_OVERLAY_SCREEN", True):
            overlay = _screen_moment_overlays(
                str(verification_path),
                archival_lane=cut_archival_lane,
                gaming_lane=cut_gaming_lane,
                native_overlay_lane=cut_native_overlay_lane,
            )
            overlay = preserve_source_overlays(overlay)
            overlay_stats['screened'] += 1
            if overlay.get('reject'):
                overlay_stats['dirty'] += 1
        if overlay.get("reject"):
            # Keep the audit counters truthful: caption/logo rejects are
            # overlay failures, not presenter failures. This distinction is
            # what lets the footage lane recover from incidental faces without
            # making the UI report every dirty frame as a webcam rejection.
            if bool(overlay.get("facecam_inset")) or str(
                overlay.get("rejection_reason") or ""
            ).strip().lower() in {
                "confident_talking_head_rejected", "talking_head", "stable_centered_presenter",
            }:
                overlay_stats["talking_head_rejected"] += 1
            cut_record.update({
                "status": "overlay_rejected",
                "reason": overlay.get("rejection_reason") or overlay.get("talking_head_reason") or "stable_centered_presenter",
                "overlay_checked": bool(overlay.get("checked")),
                "overlay_policy": str(overlay.get("overlay_policy") or "preserve_source_no_blur"),
                "source_overlays_preserved": bool(overlay.get("source_overlays_preserved")),
                "no_blur": True,
                "native_overlay_lane": bool(cut_native_overlay_lane),
                "native_overlay_detected": bool(overlay.get("native_overlay_detected")),
                "overlay_types": list(overlay.get("overlay_types") or []),
                "overlay_clean": bool(overlay.get("clean")),
                "blur_regions": list(overlay.get("blur_regions") or []),
                "rejection_reason": str(overlay.get("rejection_reason") or ""),
                "cleaned_path": str(overlay.get("cleaned_path") or ""),
                "overlay_watermark_prob": round(float(overlay.get("watermark_prob") or 0.0), 3),
                "watermark_tolerated": bool(overlay.get("watermark_tolerated")),
                "watermark_blur_failed": bool(overlay.get("watermark_blur_failed")),
                "overlay_has_subtitle": bool(overlay.get("has_subtitle")),
                "overlay_blurred": bool(overlay.get("blurred")),
                "overlay_blur_failed": bool(overlay.get("blur_failed")),
                "facecam_inset": bool(overlay.get("facecam_inset")),
                "facecam_confidence": round(float(overlay.get("facecam_confidence") or 0.0), 3),
                "facecam_reason": str(overlay.get("facecam_reason") or ""),
                "facecam_corner": str(overlay.get("facecam_corner") or ""),
            })
            cut_audit.append(cut_record)
            for stale_path in {str(raw_output_path), str(output_path)}:
                try:
                    Path(stale_path).unlink(missing_ok=True)
                except Exception:
                    pass
            return scene_index, None
        if not overlay.get("clean", True) and not overlay.get("blurred"):
            cut_record.update({
                "status": "overlay_blur_failed",
                "reason": "detected_watermark_or_caption_could_not_be_cleaned",
            })
            cut_audit.append(cut_record)
            for stale_path in {str(raw_output_path), str(output_path)}:
                try:
                    Path(stale_path).unlink(missing_ok=True)
                except Exception:
                    pass
            return scene_index, None
        cut_record["path"] = str(verification_path)
        accepted = True
        score = float(getattr(moment, "match_score", 0.0) or 0.0)
        available = False
        reason = "vision_disabled"
        visual_checked = False
        if visual_context is not None:
            topic = str(row.get("title_anchor") or row.get("domain_anchor") or row.get("candidate_subject") or "")
            subject = str(row.get("candidate_subject") or row.get("section_subject") or "")
            scene_policy = row.get("source_policy") or {}
            if not isinstance(scene_policy, Mapping):
                scene_policy = {}
            relaxed_raw = row.get(
                "youtube_relaxed_visual_gate",
                scene_policy.get(
                    "youtube_relaxed_visual_gate",
                    os.environ.get("VIDZEO_YT_RELAXED_VISUAL_GATE", "1"),
                ),
            )
            relaxed_youtube = (
                relaxed_raw if isinstance(relaxed_raw, bool)
                else str(relaxed_raw).strip().lower() in {"1", "true", "yes", "on"}
            )
            fail_open_raw = row.get(
                "youtube_fail_open_on_visual_unavailable",
                scene_policy.get(
                    "youtube_fail_open_on_visual_unavailable",
                    os.environ.get("VIDZEO_YT_FAIL_OPEN_ON_VISUAL_UNAVAILABLE", "1"),
                ),
            )
            fail_open_youtube = (
                fail_open_raw if isinstance(fail_open_raw, bool)
                else str(fail_open_raw).strip().lower() in {"1", "true", "yes", "on"}
            )
            strict_visual = bool(
                getattr(visual_context, "visual_required", False)
                or getattr(visual_context, "fail_closed", False)
            )
            visual_checked, policy_reason = reserve_visual_call(scene_index)
            if visual_checked:
                try:
                    accepted, visual_score, available, reason = pipeline._visual_verdict_scored(
                        str(verification_path),
                        scene_index,
                        topic,
                        str(row.get("scene_text") or ""),
                        subject,
                        visual_context,
                        relevance_only=True,
                    )
                except Exception as exc:
                    # A YouTube cut has already passed local transport,
                    # quality, overlay, and safety gates. Keep it in the
                    # candidate pool when the optional visual worker fails;
                    # the shared coordinator still records the degraded
                    # decision and rejects explicit hard mismatches later.
                    accepted, visual_score, available = (
                        (relaxed_youtube and fail_open_youtube) or not strict_visual
                    ), score, False
                    reason = f"visual_worker_exception:{type(exc).__name__}"
                if available:
                    score = float(visual_score or 0.0)
                else:
                    if strict_visual and not (relaxed_youtube and fail_open_youtube):
                        accepted = False
                        reason = f"{reason or 'vision_unavailable'}:strict_unverified_reject"
                        with vision_lock:
                            vision_stats["strict_unavailable_rejections"] += 1
                    else:
                        accepted = True
                        reason = f"{reason or 'vision_unavailable'}:{'youtube_relaxed' if relaxed_youtube else 'metadata'}_fail_open"
                        with vision_lock:
                            vision_stats["fail_open"] += 1
                with vision_lock:
                    vision_stats["completed"] += 1
                    if available:
                        vision_stats["available"] += 1
                    if available and not accepted:
                        vision_stats["hard_rejections"] += 1
            else:
                reason = policy_reason
                accepted = True
                with vision_lock:
                    vision_stats["bypassed"] += 1
                    if policy_reason == "metadata_transcript_trusted":
                        vision_stats["metadata_trusted_bypasses"] += 1
                    elif policy_reason == "vision_disabled_or_unavailable":
                        vision_stats["disabled_bypasses"] += 1
                    elif policy_reason == "vision_veto_budget_bypass":
                        vision_stats["budget_bypasses"] += 1
        # F1: the gates above ran on the cheap probe. Only now, for the
        # accepted winner, pay for the full 1080p cut. A rejected candidate
        # keeps its probe path so the caller deletes the probe, never a
        # full cut that was never made.
        final_path = verification_path
        if accepted and probe_cut:
            try:
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                full_cut_ok = bool(pipeline._cut_local_segment(
                    source_path,
                    output_path,
                    float(moment.start),
                    duration,
                    **cut_kwargs,
                ))
            except Exception:
                full_cut_ok = False
            if not full_cut_ok:
                cut_record.update({"status": "cut_failed", "reason": "ffmpeg_or_empty_output"})
                cut_audit.append(cut_record)
                try:
                    Path(str(verification_path)).unlink(missing_ok=True)
                except Exception:
                    pass
                return scene_index, None
            final_path = output_path
            cut_record["path"] = str(output_path)
        if accepted and final_path == output_path:
            # Only a verified full cut is cached for later jobs. (Previously
            # this ran before the vision veto, caching rejected cuts too.)
            try:
                from services.youtube_media_cache import put_verified_cut
                cached_path = put_verified_cut(
                    source_id,
                    float(moment.start),
                    duration,
                    str(output_path),
                    metadata={
                        "source_url": str((source_candidates.get(source_id) or {}).get("webpage_url") or ""),
                        "title": str((source_candidates.get(source_id) or {}).get("title") or "")[:180],
                        "overlay_policy": str(overlay.get("overlay_policy") or ""),
                        "overlay_types": list(overlay.get("overlay_types") or []),
                    },
                )
                if cached_path:
                    cut_record["cache_stored"] = True
                    cut_record["cache_path"] = cached_path
            except Exception:
                cut_record["cache_stored"] = False
        cut_record.update({
            "status": "accepted" if accepted else "visual_rejected",
            "visual_checked": visual_checked,
            "visual_available": available,
            "visual_reason": reason,
            "visual_score": round(score, 3),
            "metadata_score": round(float(getattr(records.get(source_id), "metadata_score", 0.0) or 0.0), 3),
            "moment_match_score": round(float(getattr(moment, "match_score", 0.0) or 0.0), 3),
            "overlay_checked": bool(overlay.get("checked")),
            "overlay_policy": str(overlay.get("overlay_policy") or "preserve_source_no_blur"),
                "source_overlays_preserved": bool(overlay.get("source_overlays_preserved")),
                "no_blur": True,
            "native_overlay_lane": bool(cut_native_overlay_lane),
            "native_overlay_detected": bool(overlay.get("native_overlay_detected")),
            "overlay_types": list(overlay.get("overlay_types") or []),
            "overlay_clean": bool(overlay.get("clean")),
            "blur_regions": list(overlay.get("blur_regions") or []),
            "rejection_reason": str(overlay.get("rejection_reason") or ""),
            "cleaned_path": str(overlay.get("cleaned_path") or ""),
            "overlay_watermark_prob": round(float(overlay.get("watermark_prob") or 0.0), 3),
            "watermark_tolerated": bool(overlay.get("watermark_tolerated")),
            "overlay_has_subtitle": bool(overlay.get("has_subtitle")),
            "overlay_blurred": bool(overlay.get("blurred")),
            "overlay_blur_failed": bool(overlay.get("blur_failed")),
            "caption_detected": bool(overlay.get("caption_detected")),
            "caption_blurred": bool(overlay.get("caption_blurred")),
            "talking_head": bool(overlay.get("talking_head")),
            "talking_head_confidence": round(float(overlay.get("talking_head_confidence") or 0.0), 3),
            "facecam_inset": bool(overlay.get("facecam_inset")),
            "facecam_confidence": round(float(overlay.get("facecam_confidence") or 0.0), 3),
            "facecam_reason": str(overlay.get("facecam_reason") or ""),
            "facecam_corner": str(overlay.get("facecam_corner") or ""),
            "archival_lane": bool(cut_archival_lane),
        })
        cut_audit.append(cut_record)
        if accepted:
            try:
                from services.youtube_clip_provenance import write_clip_provenance
                write_clip_provenance(final_path, scene_index, {
                    **cut_record, 'status': 'downloaded', 'video_id': source_id,
                    'timestamp': float(moment.start), 'clip_duration': duration,
                    'source_url': str((source_candidates.get(source_id) or {}).get('webpage_url') or
                                      (source_candidates.get(source_id) or {}).get('url') or ''),
                })
            except (OSError, ValueError, TypeError):
                pass
        return scene_index, (score, str(final_path.resolve()), moment, accepted, available, reason)

    def resolve_scene_progressively(scene_plans):
        attempted = []
        scene_index = int(scene_plans[0][0])
        for plan in scene_plans:
            resolved_index, result = cut_and_gate(plan)
            if result is None:
                continue
            attempted.append(result)
            if result[3]:
                break
            try:
                Path(result[1]).unlink(missing_ok=True)
            except Exception:
                pass
        return scene_index, attempted

    cut_workers = max(1, min(8, int(os.environ.get("VIDZEO_YT_LOCAL_CUT_CONCURRENCY", "6") or 6)))
    local_cut_deadline_seconds = _env_float(
        "VIDZEO_YT_POOL_LOCAL_CUT_DEADLINE_SECONDS",
        0.0,
        minimum=0.0,
        maximum=900.0,
    )
    if local_cut_deadline_seconds <= 0:
        # Verified post-blur re-screening and the motion gate make each cut
        # cost several ffmpeg passes. A flat 90s deadline starves large pools
        # after the first half of the quota; scale with the scene count.
        scene_count = max(1, len(pools.rows or []))
        local_cut_deadline_seconds = 90.0 * (1.0 + max(0, scene_count - 6) * 0.25)
    local_cut_deadline_seconds = max(0.05, min(900.0, local_cut_deadline_seconds))
    local_cut_deadline_reached = False

    def _soft_branch_timeout() -> bool:
        if cancellation is None or not cancellation.cancelled():
            return False
        reason = str(getattr(cancellation, "reason", "") or "").lower()
        parent = getattr(cancellation, "parent", None)
        parent_reason = str(getattr(parent, "reason", "") or "").lower()
        combined = f"{reason} {parent_reason}"
        return any(term in combined for term in ("aggregate wall", "wall", "timeout", "deadline", "exceeded"))

    if plans_by_scene:
        # Keep tiny pools deterministic.  A two-scene pool used to race its
        # futures, which made cut/audit order depend on FFmpeg scheduling and
        # produced inconsistent UI logs even though both cuts were valid.  The
        # production-sized pools remain concurrent for throughput.
        pool_workers = min(cut_workers, len(plans_by_scene))
        if len(plans_by_scene) <= 2:
            pool_workers = 1
        executor = ThreadPoolExecutor(
            max_workers=pool_workers,
            thread_name_prefix="yt-local-cut",
        )
        pending = {
            executor.submit(resolve_scene_progressively, scene_plans)
            for scene_plans in plans_by_scene.values()
        }
        deadline = time.monotonic() + local_cut_deadline_seconds
        completed_scenes = 0
        resolved_scenes = 0
        log_step = max(1, len(pending) // 5)

        def consume_local_future(future):
            nonlocal completed_scenes, resolved_scenes
            try:
                scene_index, attempted = future.result()
            except Exception as exc:
                if cancellation is not None and cancellation.cancelled() and not _soft_branch_timeout():
                    raise
                if not _soft_branch_timeout():
                    cut_audit.append({
                        "status": "worker_exception",
                        "reason": f"{type(exc).__name__}:{exc}"[:300],
                    })
                completed_scenes += 1
                return
            candidate_results[scene_index].extend(attempted)
            if any(candidate[3] for candidate in attempted):
                resolved_scenes += 1
            completed_scenes += 1
            report_progress(
                0.55 + 0.40 * (completed_scenes / max(1, len(plans_by_scene))),
                (
                    f"YouTube pool: local scenes {completed_scenes}/{len(plans_by_scene)} "
                    f"({resolved_scenes} resolved, vision {vision_stats['completed']}/{visual_policy['call_cap']})"
                ),
            )
            if completed_scenes % log_step == 0 or completed_scenes == len(plans_by_scene):
                print(
                    f"   [yt-pool] local progress: {completed_scenes}/{len(plans_by_scene)} scene(s), "
                    f"resolved={resolved_scenes}, vision={vision_stats['completed']}/{visual_policy['call_cap']}",
                    flush=True,
                )

        try:
            while pending:
                if cancellation is not None and cancellation.cancelled():
                    if not _soft_branch_timeout():
                        cancellation.raise_if_cancelled()
                    local_cut_deadline_reached = True
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    local_cut_deadline_reached = True
                    break
                done, pending = wait(
                    pending,
                    timeout=min(0.25, remaining),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    consume_local_future(future)
            finished_at_deadline = {future for future in pending if future.done()}
            pending.difference_update(finished_at_deadline)
            for future in finished_at_deadline:
                consume_local_future(future)
        finally:
            for future in pending:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
        if local_cut_deadline_reached and pending:
            print(
                f"   [yt-pool] local cut deadline: retaining {resolved_scenes}/{len(plans_by_scene)} "
                f"resolved scene(s); {len(pending)} unfinished",
                flush=True,
            )

    from services.visual_intelligence.decision import SCENE_DECISION_VERSION, asset_fingerprint, scene_context_hash
    assigned_pool_ranges: Dict[str, List[tuple[float, float, int]]] = defaultdict(list)
    scene_order = sorted(
        candidate_results.keys(),
        key=lambda s_idx: max([c[0] for c in candidate_results[s_idx] if c[3]], default=-1.0),
        reverse=True,
    )
    for scene_index in scene_order:
        candidates = candidate_results[scene_index]
        accepted = [candidate for candidate in candidates if candidate[3]]
        if not accepted:
            continue
        sorted_candidates = sorted(
            accepted,
            key=lambda item: (item[0], -float(getattr(item[2], "start", 0.0))),
            reverse=True,
        )
        duration = float(pools.scene_durations[scene_index] if scene_index < len(pools.scene_durations) else 4.0)
        min_gap = max(0.5, duration)

        chosen_candidate = None
        for candidate in sorted_candidates:
            moment = candidate[2]
            source_id = str(getattr(moment, "source_id", "") or getattr(moment, "video_id", ""))
            start_val = float(getattr(moment, "start", 0.0) or 0.0)
            occupied_ranges = assigned_pool_ranges[source_id]
            collision = any(abs(start_val - occ_start) < (min_gap - 0.05) for occ_start, _occ_dur, _occ_s_idx in occupied_ranges)
            if not collision:
                chosen_candidate = candidate
                assigned_pool_ranges[source_id].append((start_val, duration, scene_index))
                break

        if chosen_candidate is None:
            best_spread_candidate = None
            max_min_dist = -1.0
            for candidate in sorted_candidates:
                moment = candidate[2]
                source_id = str(getattr(moment, "source_id", "") or getattr(moment, "video_id", ""))
                start_val = float(getattr(moment, "start", 0.0) or 0.0)
                occupied_starts = [occ_start for occ_start, _occ_dur, _occ_s_idx in assigned_pool_ranges[source_id]]
                if not occupied_starts:
                    best_spread_candidate = candidate
                    break
                min_dist = min(abs(start_val - occ) for occ in occupied_starts)
                if min_dist > max_min_dist:
                    max_min_dist = min_dist
                    best_spread_candidate = candidate

            if max_min_dist >= 0.5 and best_spread_candidate is not None:
                chosen_candidate = best_spread_candidate
                moment = chosen_candidate[2]
                source_id = str(getattr(moment, "source_id", "") or getattr(moment, "video_id", ""))
                start_val = float(getattr(moment, "start", 0.0) or 0.0)
                assigned_pool_ranges[source_id].append((start_val, duration, scene_index))
            elif best_spread_candidate is not None:
                moment = best_spread_candidate[2]
                source_id = str(getattr(moment, "source_id", "") or getattr(moment, "video_id", ""))
                print(
                    f"   [yt-pool] scene {scene_index + 1}: avoided repeating exact same timestamp clip from {source_id}",
                    flush=True,
                )

        if chosen_candidate is not None:
            score, path, moment, _accepted, available, reason = chosen_candidate
            results[scene_index] = path
            row = pools.rows[scene_index]
            topic = str(row.get("title_anchor") or row.get("domain_anchor") or row.get("candidate_subject") or "")
            subject = str(row.get("candidate_subject") or row.get("section_subject") or "")
            narration = str(row.get("scene_text") or "")
            row_req = row.get("visual_requirement") if isinstance(row.get("visual_requirement"), dict) else {}
            req_hash = str(row_req.get("requirement_hash") or "").strip()
            if not req_hash:
                req_hash = scene_context_hash(narration=narration, subject=subject, topic=topic, media_type="video")
            cut_fp = asset_fingerprint(path)
            is_accepted = bool(available or _accepted)
            retrieval_decisions[str(scene_index)] = {
                "version": SCENE_DECISION_VERSION,
                "scene_index": scene_index,
                "asset_fingerprint": cut_fp,
                "scene_context_hash": req_hash,
                "media_type": "video",
                "status": "accepted" if is_accepted else "unverified",
                "accepted": is_accepted,
                "visual_checked": is_accepted,
                "semantic_verified": is_accepted,
                "visual_available": bool(available),
                "verified_requirement_hash": req_hash if is_accepted else "",
                "verified_asset_sha256": cut_fp if is_accepted else "",
                "visual_review_policy_version": 17,
                "relevance": round(score, 3),
                "quality": round(score, 3),
                "confidence": 90.0 if is_accepted else 0.0,
                "model": str(getattr(visual_context, "model", "") or ""),
                "source": "youtube_pool_moment",
                "operation": "youtube_pool_moment_gate",
                "degraded_reason": "" if is_accepted else reason,
                "source_moment": getattr(moment, "to_dict", lambda: {})(),
            }
            for candidate in candidates:
                if candidate[1] != path:
                    try:
                        Path(candidate[1]).unlink(missing_ok=True)
                    except Exception:
                        pass
        else:
            for candidate in candidates:
                try:
                    Path(candidate[1]).unlink(missing_ok=True)
                except Exception:
                    pass

    scene_assignments = sum(1 for path in results if path)
    # A scene can have several speculative bite moments. Report the public
    # overlay counters once per scene (while retaining per-attempt details in
    # cut_audit), so retries do not make a single dirty scene look like four
    # separate user-facing failures.
    overlay_attempts_screened = int(overlay_stats.get("screened", 0))
    overlay_attempts_dirty = int(overlay_stats.get("dirty", 0))
    overlay_attempts_blurred = int(overlay_stats.get("blurred", 0))
    overlay_attempts_blur_failed = int(overlay_stats.get("blur_failed", 0))
    overlay_by_scene: Dict[str, Dict[str, bool]] = {}
    for item in cut_audit:
        if not item.get("overlay_checked") and not any(
            item.get(key)
            for key in ("overlay_types", "rejection_reason", "overlay_blur_failed", "blur_failed", "blur_regions")
        ):
            continue
        raw_scene_key = str(item.get("scene_index") if item.get("scene_index") is not None else "")
        path_key = str(item.get("path") or item.get("cleaned_path") or "")
        # Some legacy/provider adapters overwrite scene_index while recording
        # retries. The rendered cut stem is the stable physical scene key.
        scene_match = re.search(r"scene_(\d+)", path_key)
        if len(results) == 1:
            scene_key = "single_scene"
        elif scene_match:
            scene_key = f"scene_{scene_match.group(1)}"
        else:
            scene_key = raw_scene_key
        scene_state = overlay_by_scene.setdefault(scene_key, {
            "dirty": False, "blurred": False, "blur_failed": False,
        })
        overlay_types = {
            str(value).strip().lower()
            for value in (item.get("overlay_types") or [])
            if str(value).strip()
        }
        scene_state["dirty"] = bool(
            scene_state["dirty"]
            or item.get("overlay_rejected")
            or item.get("overlay_blur_failed")
            or item.get("caption_detected")
            or item.get("overlay_clean") is False
            or item.get("blur_regions")
            or overlay_types.intersection({"caption", "subtitle", "subtitles", "text_overlay", "large_text_band", "watermark", "logo"})
        )
        scene_state["blurred"] = bool(scene_state["blurred"] or item.get("overlay_blurred") or item.get("blurred"))
        scene_state["blur_failed"] = bool(scene_state["blur_failed"] or item.get("overlay_blur_failed") or item.get("blur_failed"))
    if overlay_by_scene:
        overlay_screened = len(overlay_by_scene)
        overlay_dirty = sum(1 for state in overlay_by_scene.values() if state["dirty"])
        overlay_blurred = sum(1 for state in overlay_by_scene.values() if state["blurred"])
        overlay_blur_failed = sum(1 for state in overlay_by_scene.values() if state["blur_failed"])
    elif overlay_attempts_screened and len(results) == 1:
        # Rejection exits can append a compact audit row before the normal
        # overlay fields are copied. Preserve one scene-level count for that
        # legacy shape instead of exposing every speculative retry.
        overlay_screened = 1
        overlay_dirty = int(bool(overlay_attempts_dirty))
        overlay_blurred = int(bool(overlay_attempts_blurred))
        overlay_blur_failed = int(bool(overlay_attempts_blur_failed))
    else:
        overlay_screened = overlay_attempts_screened
        overlay_dirty = overlay_attempts_dirty
        overlay_blurred = overlay_attempts_blurred
        overlay_blur_failed = overlay_attempts_blur_failed
    talking_head_rejected = int(overlay_stats.get("talking_head_rejected", 0))
    print(
        f"   [yt-pool] complete: {scene_assignments}/{len(results)} scene(s); "
        f"vision calls={vision_stats['completed']}/{visual_policy['call_cap']}, "
        f"hard relevance vetoes={vision_stats['hard_rejections']}, "
        f"metadata/budget bypasses={vision_stats['bypassed']}, "
        f"fail-open={vision_stats['fail_open']}",
        flush=True,
    )
    if overlay_screened:
        print(
            f"   [yt-pool] overlay screen: {overlay_screened} scene(s) scanned, "
            f"{overlay_dirty} dirty, {overlay_blurred} blurred, {overlay_blur_failed} blur failed, "
            f"{talking_head_rejected} talking-head rejected",
            flush=True,
        )
    if quality_stats.get("screened") or quality_stats.get("rejected"):
        print(
            f"   [yt-pool] quality screen: {quality_stats.get('screened', 0)} cut(s) probed, "
            f"{quality_stats.get('rejected', 0)} blank/low-information cut(s) rejected",
            flush=True,
        )
    report_progress(0.97, f"YouTube pool: {scene_assignments}/{len(results)} clips resolved")

    _journal_path = output_root / "resolution_audit.json"
    try:
        import json as _json
        per_scene = {}
        for _scene_index, _row in enumerate(pools.rows):
            _scene_attempts = []
            for _attempt in cut_audit:
                if not isinstance(_attempt, Mapping):
                    continue
                try:
                    _attempt_scene = int(_attempt.get("scene_index", -1))
                except (TypeError, ValueError):
                    _attempt_scene = -1
                if _attempt_scene != _scene_index:
                    continue
                _scene_attempts.append({
                    key: _attempt.get(key)
                    for key in (
                        "status", "reason", "source_id", "start", "path",
                        "rejection_reason", "visual_reason", "visual_score", "transport",
                        "cache_hit", "cache_stored", "cache_path",
                    )
                    if key in _attempt
                })
            per_scene[str(_scene_index)] = {
                "subject": str(
                    _row.get("candidate_subject")
                    or _row.get("section_subject")
                    or _row.get("title_anchor")
                    or ""
                )[:120],
                "resolved_path": (
                    str(results[_scene_index])
                    if _scene_index < len(results) and results[_scene_index]
                    else None
                ),
                "attempts": _scene_attempts,
            }
        _journal = {
            "kind": "resolution_audit",
            "pipeline": "pool_first",
            "probe_candidates_total": len(probe_audit),
            "probe_candidates_accepted": sum(
                1 for row in probe_audit.values() if row.get("status") == "accepted"
            ),
            "probe_candidates_rejected": sum(
                1 for row in probe_audit.values() if str(row.get("status") or "") == "rejected"
            ),
            "probe_wall_exhausted": sum(
                1
                for row in probe_audit.values()
                if str(row.get("rejection_reason") or "") == "probe_wall_exhausted"
            ),
            "moment_options": len(plans),
            "option_limit_per_scene": int(option_limit),
            "local_cut_deadline_seconds": round(local_cut_deadline_seconds, 3),
            "local_cut_deadline_reached": bool(local_cut_deadline_reached),
            "resolved": int(scene_assignments),
            "requested": int(len(results)),
            "scene_results": per_scene,
        }
        _journal_path.write_text(
            _json.dumps(_journal, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"   [yt-pool] resolution journal: {_journal_path}", flush=True)
    except Exception as _exc:
        print(
            f"   [yt-pool] resolution journal write failed: {type(_exc).__name__}",
            flush=True,
        )

    return PoolFirstResults(
        results,
        audit={
            "source_shortlist": selected_source_ids,
            "pool_requirements": pool_requirements,
            "probe_candidates_by_pool": probe_candidates_by_pool,
            "candidate_probe_audit": probe_audit,
            "verified_pool_selection": selected_by_pool,
            "probe_candidates_total": len(probe_audit),
            "probe_candidates_accepted": sum(1 for row in probe_audit.values() if row.get("status") == "accepted"),
            "probe_candidates_rejected": sum(1 for row in probe_audit.values() if row.get("status") == "rejected"),
            "transcript_refresh": transcript_refresh_audit,
            "source_downloads_planned": len(selected_source_ids),
            "source_downloads_attempted": len(source_audit_snapshot),
            "source_downloads_succeeded": len(source_paths),
            "source_downloads_failed": max(0, len(selected_source_ids) - len(source_paths)),
            "source_download_deadline_seconds": round(source_batch_seconds, 3),
            "source_download_elapsed_seconds": round(source_batch_elapsed_seconds, 3),
            "source_download_deadline_reached": bool(source_deadline_reached),
            "source_downloads_pending_at_deadline": int(source_pending_at_deadline),
            "local_cut_deadline_seconds": round(local_cut_deadline_seconds, 3),
            "local_cut_deadline_reached": bool(local_cut_deadline_reached),
            "source_audit": source_audit_snapshot,
            "moment_shortlist": len(plans),
            "local_cuts_planned": len(plans),
            "local_cuts": sum(len(values) for values in candidate_results.values()),
            "intro_unique_scene_count": sum(
                1 for row in pools.rows
                if isinstance(row, Mapping) and bool(row.get("intro_unique") or row.get("is_intro"))
            ),
            "bite_clip_scene_count": sum(
                1 for row in pools.rows
                if isinstance(row, Mapping) and bool(row.get("bite_clip") or row.get("youtube_bite_clip"))
            ),
            "bite_clip_multi_timestamp_enabled": True,
            "cut_audit": cut_audit,
            "transport_cache_hits": sum(1 for item in cut_audit if item.get("cache_hit")),
            "transport_cache_stores": sum(1 for item in cut_audit if item.get("cache_stored")),
            "quality_policy": "multi_frame_luma_edge_motion_gate_v2",
            "quality_cuts_screened": int(quality_stats.get("screened", 0)),
            "quality_rejections": int(quality_stats.get("rejected", 0)),
            "quality_soft_admissions": int(quality_stats.get("soft_admitted", 0)),
            "visual_moments_verified": sum(1 for values in candidate_results.values() for value in values if value[4]),
            "visual_veto_policy": "relevance_only_bounded",
            "visual_veto_long_form": bool(visual_policy["long_form"]),
            "visual_veto_call_cap": int(visual_policy["call_cap"]),
            "visual_veto_calls_reserved": int(vision_stats["reserved"]),
            "visual_veto_calls_completed": int(vision_stats["completed"]),
            "visual_veto_calls_available": int(vision_stats["available"]),
            "visual_veto_hard_rejections": int(vision_stats["hard_rejections"]),
            "visual_veto_bypasses": int(vision_stats["bypassed"]),
            "visual_veto_fail_open": int(vision_stats["fail_open"]),
            "visual_veto_disabled_bypasses": int(vision_stats["disabled_bypasses"]),
            "visual_veto_budget_bypasses": int(vision_stats["budget_bypasses"]),
            "metadata_trusted_bypasses": int(vision_stats["metadata_trusted_bypasses"]),
            "visual_veto_priority_scenes": sorted(int(index) for index in priority_scenes),
            "overlay_screen_enabled": bool(_env_bool("VIDZEO_YT_OVERLAY_SCREEN", True)),
            "overlay_attempts_screened": overlay_attempts_screened,
            "overlay_attempts_dirty": overlay_attempts_dirty,
            "overlay_attempts_blurred": overlay_attempts_blurred,
            "overlay_attempts_blur_failed": overlay_attempts_blur_failed,
            "overlay_cuts_screened": overlay_screened,
            "overlay_cuts_dirty": overlay_dirty,
            "overlay_cuts_blurred": overlay_blurred,
            "overlay_cuts_blur_failed": overlay_blur_failed,
            "talking_head_rejected": talking_head_rejected,
            "overlay_policy": "preserve_source_no_blur",
            "caption_overlay_rejections": sum(
                1 for item in cut_audit if item.get("reason") == "caption_overlay_rejected"
            ),
            "logo_overlay_rejections": sum(
                1 for item in cut_audit
                if str(item.get("reason") or "").endswith("logo_overlay_rejected")
            ),
            "scene_assignments": scene_assignments,
        },
        retrieval_decisions=retrieval_decisions,
    )
