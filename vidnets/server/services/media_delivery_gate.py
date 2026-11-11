"""Require complete media slots; record emergency graphics as quality advisories."""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import math

from .media_asset_policy import is_synthetic_media
from .pipeline_contracts import MediaContractError


def validate_documentary_coverage(media_plan, durations=(), config=None):
    if isinstance(durations, dict) and config is None:
        config = durations
        durations = ()
    config = config or {}
    paths = list(media_plan.get('media_paths') or [])
    labels = list(media_plan.get('source_labels') or [])
    rows = list((media_plan.get('metadata') or {}).get('media_resolution_v2') or [])

    counts = Counter()
    emergency_indices, missing_indices = [], []
    seconds, emergency_seconds = 0.0, 0.0
    for index, path in enumerate(paths):
        row = rows[index] if index < len(rows) and isinstance(rows[index], dict) else {}
        label = str(labels[index] if index < len(labels) else '').casefold()
        provider = str(row.get('provider') or label).casefold()
        try:
            duration = float(durations[index]) if index < len(durations) else 1.0
        except (TypeError, ValueError):
            duration = 1.0
        duration = duration if math.isfinite(duration) and duration > 0 else 1.0
        seconds += duration
        if not path or not Path(str(path)).is_file():
            missing_indices.append(index)
        fallback = (is_synthetic_media(path, label) or provider in {'neutral', 'graphic', 'generated_graphic'}
                    or str(row.get('resolution_kind') or '') == 'graphic_fallback')
        if fallback:
            emergency_indices.append(index)
            emergency_seconds += duration
            counts['emergency'] += 1
        elif 'stock' in provider or provider in {'pexels', 'pixabay'}:
            counts['stock'] += 1
        elif 'youtube' in provider or provider == 'yt':
            counts['youtube'] += 1
        else:
            counts['web_or_uploaded'] += 1

    scene_ratio = len(emergency_indices) / max(1, len(paths))
    duration_ratio = emergency_seconds / max(0.001, seconds)
    # Fail-closed: a video whose every scene is an emergency graphic is not
    # a documentary — it is a slideshow of placeholders. When the media
    # guarantee is on (the default for production jobs), all-emergency fails
    # the gate here in Vidnets instead of only warning and letting the outer
    # render wrapper catch it later.
    guarantee = bool(config.get('global_media_guarantee', True))
    all_emergency = bool(paths) and len(emergency_indices) == len(paths)
    passed = bool(paths) and not missing_indices and not (guarantee and all_emergency)
    warnings = ([f'Emergency graphics used in {len(emergency_indices)}/{len(paths)} scenes '
                 f'({scene_ratio:.1%} of scenes, {duration_ratio:.1%} of runtime); '
                 'real-media retrieval remains preferred.'] if emergency_indices else [])
    if guarantee and all_emergency:
        warnings.append('Media guarantee failed: every scene fell back to emergency graphics; no real media was retrieved.')
    summary = {
        'passed': passed, 'scene_count': len(paths), 'source_counts': dict(counts),
        'emergency_scene_indices': emergency_indices, 'missing_scene_indices': missing_indices,
        'emergency_scene_ratio': round(scene_ratio, 4), 'emergency_duration_ratio': round(duration_ratio, 4),
        'max_emergency_visual_ratio': None, 'advisory_only': True, 'warnings': warnings,
    }
    media_plan.setdefault('metadata', {})['documentary_coverage'] = summary
    if not passed:
        raise MediaContractError(
            f'Documentary media coverage failed: {len(missing_indices)} missing scene(s); '
            f'{len(emergency_indices)}/{len(paths)} emergency scenes '
            f'({scene_ratio:.1%} of scenes, {duration_ratio:.1%} of runtime). '
            'Retrieval checkpoints are saved; repair empty or missing media slots before rendering or uploading.',
            summary,
        )
    return summary
