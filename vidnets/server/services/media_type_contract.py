"""Final mixed-media type quota enforcement without another network search."""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any, Dict, List


VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v")


def _is_video(path: str) -> bool:
    return bool(path and str(path).lower().endswith(VIDEO_EXTENSIONS) and os.path.exists(path))


def _is_image(path: str) -> bool:
    return bool(path and not str(path).lower().endswith(VIDEO_EXTENSIONS) and os.path.exists(path))


def _terms(text: str) -> set:
    return {
        word for word in re.findall(r"[a-z0-9]{3,}", str(text or "").lower())
        if word not in {
            "the", "and", "that", "this", "with", "from", "into", "were",
            "was", "are", "for", "its", "his", "her", "their", "about",
        }
    }


def enforce_mixed_media_type_contract(
    media_plan: Dict[str, Any],
    scene_texts: List[str],
) -> Dict[str, Any]:
    """Restore planned image/video counts by reusing the closest valid asset.

    Retrieval and visual QA remain authoritative. This function never turns a
    rejected asset back on and never launches another search. It only repairs
    type drift introduced by fallback by choosing a non-adjacent, semantically
    closest accepted asset of the requested type.
    """
    metadata = media_plan.get("metadata") or {}
    grouped = (
        (metadata.get("final_media_plan") or {}).get("grouped_indices")
        or (metadata.get("media_plan") or {}).get("grouped_indices")
        or {}
    )
    if not grouped:
        return media_plan

    paths = list(media_plan.get("media_paths") or [])
    labels = list(media_plan.get("source_labels") or [])
    labels.extend([""] * max(0, len(paths) - len(labels)))
    expected_video = set(grouped.get("YouTube Clips") or []) | set(grouped.get("Stock Media") or [])
    expected_image = set(grouped.get("Web Images") or []) | set(grouped.get("AI Images") or [])
    strict_percentages = metadata.get("strict_mixed_percentages")
    if strict_percentages is None:
        strict_percentages = str(
            os.environ.get("VIDZEO_STRICT_MIXED_PERCENTAGES", "1")
        ).strip().lower() in {"1", "true", "yes", "on"}
    if not strict_percentages:
        metadata["media_type_contract"] = {
            "target_video": len(expected_video),
            "target_image": len(expected_image),
            "actual_video": sum(1 for path in paths if _is_video(path)),
            "actual_image": sum(1 for path in paths if _is_image(path)),
            "repaired_type_drift": 0,
            "shortfall_scene_indices": [],
            "diversity_shortfall_scene_indices": [],
            "asset_reuse_cap": None,
            "unique_video_assets": len({path for path in paths if _is_video(path)}),
            "unique_image_assets": len({path for path in paths if _is_image(path)}),
            "policy": "target_not_strict",
        }
        media_plan["metadata"] = metadata
        return media_plan
    def usable(index: int) -> bool:
        label = str(labels[index] if index < len(labels) else "").lower()
        return not any(term in label for term in ("emergency", "placeholder", "text card"))

    # Snapshot assets, labels, and semantic source positions before mutating
    # the output. Index-only pools are unsafe: retyping an early scene can
    # make a later pool index point at the opposite media type.
    def unique_pool(wants_video: bool) -> List[tuple]:
        pool = []
        seen_paths = set()
        for index, path in enumerate(paths):
            valid_type = _is_video(path) if wants_video else _is_image(path)
            if not valid_type or not usable(index) or path in seen_paths:
                continue
            seen_paths.add(path)
            pool.append((index, path, labels[index]))
        return pool

    video_pool = unique_pool(True)
    image_pool = unique_pool(False)
    usage = Counter(paths)

    def choose(index: int, pool: List[tuple]) -> tuple:
        wanted = _terms(scene_texts[index] if index < len(scene_texts) else "")
        previous = paths[index - 1] if index > 0 else ""
        return max(
            pool,
            key=lambda candidate: (
                len(wanted & _terms(
                    scene_texts[candidate[0]]
                    if candidate[0] < len(scene_texts)
                    else ""
                )),
                candidate[1] != previous,
                -usage[candidate[1]],
                -abs(candidate[0] - index),
            ),
        )

    repaired = 0
    shortfall = []
    for index in range(len(paths)):
        wants_video = index in expected_video
        wants_image = index in expected_image
        if wants_video and (not _is_video(paths[index]) or not usable(index)):
            if not video_pool:
                shortfall.append(index)
                continue
            _source_index, candidate_path, candidate_label = choose(index, video_pool)
            usage[paths[index]] -= 1
            paths[index] = candidate_path
            base_label = re.sub(
                r"(?:\s+\(type quota reuse\))+$",
                "",
                str(candidate_label or "Video"),
            )
            labels[index] = f"{base_label} (type quota reuse)"
            usage[paths[index]] += 1
            repaired += 1
        elif wants_image and (not _is_image(paths[index]) or not usable(index)):
            if not image_pool:
                shortfall.append(index)
                continue
            _source_index, candidate_path, candidate_label = choose(index, image_pool)
            usage[paths[index]] -= 1
            paths[index] = candidate_path
            base_label = re.sub(
                r"(?:\s+\(type quota reuse\))+$",
                "",
                str(candidate_label or "Image"),
            )
            labels[index] = f"{base_label} (type quota reuse)"
            usage[paths[index]] += 1
            repaired += 1

    # Do not satisfy a percentage target by repeating one accepted clip/image
    # across an unlimited number of scenes. Rotate semantically closest unique
    # assets up to a bounded reuse cap; expose a diversity shortfall when the
    # accepted pool cannot cover the requested scene count.
    try:
        reuse_cap = max(
            1,
            min(
                5,
                int(os.environ.get("VIDZEO_FINAL_MEDIA_REUSE_CAP", "3") or 3),
            ),
        )
    except Exception:
        reuse_cap = 3
    diversity_shortfall = []

    def enforce_diversity(indices: set, pool: List[tuple]) -> None:
        target_usage = Counter(
            paths[index]
            for index in indices
            if 0 <= index < len(paths)
        )
        for index in sorted(indices):
            if index >= len(paths) or target_usage[paths[index]] <= reuse_cap:
                continue
            candidates = [
                candidate
                for candidate in pool
                if target_usage[candidate[1]] < reuse_cap
            ]
            if not candidates:
                diversity_shortfall.append(index)
                continue
            _source_index, candidate_path, candidate_label = choose(index, candidates)
            target_usage[paths[index]] -= 1
            paths[index] = candidate_path
            target_usage[candidate_path] += 1
            base_label = re.sub(
                r"(?:\s+\(type quota reuse\))+$",
                "",
                str(candidate_label or ("Video" if pool is video_pool else "Image")),
            )
            labels[index] = f"{base_label} (type quota reuse)"

    enforce_diversity(expected_video, video_pool)
    enforce_diversity(expected_image, image_pool)

    # Verify the postcondition from the actual files, not from the fact that a
    # repair branch ran. This makes any provider/type shortfall explicit.
    remaining_shortfall = set(shortfall)
    for index in expected_video:
        if index >= len(paths) or not _is_video(paths[index]) or not usable(index):
            remaining_shortfall.add(index)
    for index in expected_image:
        if index >= len(paths) or not _is_image(paths[index]) or not usable(index):
            remaining_shortfall.add(index)
    shortfall = sorted(remaining_shortfall)
    shortfall = sorted(set(shortfall) | set(diversity_shortfall))

    media_plan["media_paths"] = paths
    media_plan["source_labels"] = labels
    contract = {
        "target_video": len(expected_video),
        "target_image": len(expected_image),
        "actual_video": sum(1 for path in paths if _is_video(path)),
        "actual_image": sum(1 for path in paths if _is_image(path)),
        "repaired_type_drift": repaired,
        "shortfall_scene_indices": shortfall,
        "diversity_shortfall_scene_indices": sorted(set(diversity_shortfall)),
        "asset_reuse_cap": reuse_cap,
        "emergency_asset_count": sum(1 for index in range(len(paths)) if not usable(index)),
        "unique_video_assets": len({path for path in paths if _is_video(path)}),
        "unique_image_assets": len({
            path for index, path in enumerate(paths)
            if _is_image(path) and usable(index)
        }),
    }
    metadata["media_type_contract"] = contract
    media_plan["metadata"] = metadata
    if repaired or shortfall:
        print(
            "   [mix] media-type contract: "
            f"{contract['actual_video']}/{contract['target_video']} video, "
            f"{contract['actual_image']}/{contract['target_image']} image, "
            f"{repaired} drift repair(s), {len(shortfall)} shortfall(s)"
        )
    return media_plan
