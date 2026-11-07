"""Keep a short downloaded cut from freezing across a longer narration slot."""
from __future__ import annotations

import math
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

_RECOVERY_SRC = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/%3E'
_VIDEO_SUFFIXES = {'.mp4', '.webm', '.mov', '.mkv', '.avi', '.m4v'}


def repair_short_video_slots(manifest, probe_duration=None):
    """Use the real cut once, then an honestly marked graphic for its shortfall.

    Retrieval/stock rescue has already finished at this render boundary. Never
    loop footage, stretch narration, borrow another scene's asset, or call a
    provider while repairing a saved render. Missing duration is not guessed.
    """
    cache = {}
    fps = max(1.0, float(manifest.get('fps') or 30))

    def duration_of(asset):
        raw = str(asset.get('src') or asset.get('path') or '')
        if raw.startswith(('http:', 'https:', 'data:')):
            return None
        if raw.startswith('file:'):
            parsed = urlparse(raw)
            local = ('//' + parsed.netloc if parsed.netloc else '') + unquote(parsed.path)
            if re.match(r'^/[A-Za-z]:/', local):
                local = local[1:]
        else:
            local = raw
        if Path(local).suffix.lower() not in _VIDEO_SUFFIXES:
            return None
        if raw not in cache:
            if probe_duration is not None:
                cache[raw] = probe_duration(local)
            else:
                from .media_integrity import probe_media
                probe = probe_media(local)
                cache[raw] = probe.duration if probe.valid else None
        value = cache[raw]
        return float(value) if value is not None and math.isfinite(float(value)) and float(value) > 0 else None

    advisories = []
    for scene_index, scene in enumerate(manifest.get('scenes') or []):
        media = scene.get('media') or {}
        scene_frames = max(1, int(scene.get('duration_in_frames') or round(float(scene.get('duration') or 0) * fps)))
        source = media.get('src') or media.get('path') or ''
        base_duration = duration_of(media)
        if base_duration is not None:
            media['render_source_duration'] = base_duration
        raw_shots = media.get('shots') or ([{**media, 'shot_start': 0}] if source else [])
        ordered = sorted([(max(0, min(scene_frames - 1, round(float(shot.get('shot_start', shot.get('start', 0)) or 0) * fps))), index, shot)
                          for index, shot in enumerate(raw_shots)], key=lambda row: (row[0], row[1]))
        cuts = [row for index, row in enumerate(ordered) if index == len(ordered) - 1 or row[0] != ordered[index + 1][0]]
        if cuts:
            cuts[0] = (0, cuts[0][1], cuts[0][2])
        repaired = []
        changed = False
        for index, (start, _original_index, shot) in enumerate(cuts):
            end = cuts[index + 1][0] if index + 1 < len(cuts) else scene_frames
            shot = dict(shot)
            measured = duration_of(shot)
            if measured is not None:
                shot['render_source_duration'] = measured
            seek = next((shot[key] for key in ('render_clip_start', 'source_start', 'in_point')
                         if shot.get(key) not in (None, '')), 0)
            available = max(0, math.floor((measured - max(0, float(seek))) * fps + .0001)) if measured is not None else end - start
            usable = min(end - start, available)
            # Container/frame rounding can differ by a few frames. Avoid a
            # distracting flash of recovery graphics for a sub-150ms tail.
            if 0 < end - start - usable <= max(1, round(.15 * fps)):
                usable = end - start
            shot['shot_start'] = start / fps
            shot['shot_end'] = (start + usable) / fps
            shot['duration'] = usable / fps
            if usable:
                repaired.append(shot)
            if measured is not None and usable < end - start:
                tail_start = start + usable
                repaired.append({
                    'src': _RECOVERY_SRC, 'type': 'graphic', 'media_type': 'image',
                    'shot_start': tail_start / fps, 'duration': (end - tail_start) / fps,
                    'shot_end': end / fps,
                    'source_start': 0, 'in_point': 0, 'render_clip_start': 0,
                    'source': 'graphic', 'provider': 'neutral', 'rights_status': 'internal',
                    'is_graphic_fallback': True, 'verification_status': 'not_applicable',
                    'visual_decision': None, 'asset_fingerprint': '', 'verified_asset_sha256': '',
                    'source_url': '', 'render_transform': None,
                    'fallback_reason': 'source_video_ended',
                })
                advisories.append({'scene_index': scene_index, 'start_seconds': tail_start / fps,
                                   'duration_seconds': (end - tail_start) / fps,
                                   'reason': 'source_video_ended'})
                changed = True
            elif shot.get('fallback_reason') == 'source_video_ended':
                advisories.append({'scene_index': scene_index, 'start_seconds': start / fps,
                                   'duration_seconds': (end - start) / fps,
                                   'reason': 'source_video_ended'})
        if changed:
            media['shots'] = repaired
            scene['media'] = media
            primary_asset = (scene.get('primary_visual') or {}).get('asset') or {}
            if source and (primary_asset.get('src') or primary_asset.get('path')) == source:
                primary_asset['shots'] = repaired
        elif media.get('shots'):
            # Retain measured source lengths without changing authored cuts.
            media['shots'] = repaired
    seconds = sum(row['duration_seconds'] for row in advisories)
    report = {'status': 'advisory' if advisories else 'pass', 'shortfalls': advisories,
              'graphic_tail_seconds': round(seconds, 6),
              'graphic_tail_runtime_percent': round(100 * seconds / max(.001, float(manifest.get('duration') or 0)), 4),
              'graphic_tail_scene_percent': round(100 * len({row['scene_index'] for row in advisories}) / max(1, len(manifest.get('scenes') or [])), 4)}
    manifest['render_media_timing'] = report
    return report
