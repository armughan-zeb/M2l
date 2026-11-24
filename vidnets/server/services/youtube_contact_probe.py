"""Scene-specific, bounded YouTube probe and timestamped vision selection."""
from __future__ import annotations

import math
import os
import subprocess
from pathlib import Path


def select_window(response, probe_start, probe_end, required, *, candidate_id):
    """Validate model timestamps against the actual probe, never extrapolate."""
    candidates = response.get('candidates', []) if isinstance(response, dict) else []
    winners = []
    for item in candidates if isinstance(candidates, list) else []:
        if not isinstance(item, dict) or str(item.get('candidate_id', '')) != str(candidate_id):
            continue
        if item.get('is_usable') is not True:
            continue
        try:
            score = float(item['score'])
            start, end = float(item['clean_start_sec']), float(item['clean_end_sec'])
            crop = float(item.get('needs_crop_zoom_pct', 0) or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (score, start, end, crop)):
            continue
        if score < 6.5 or score > 10 or crop < 0 or crop > 15:
            continue
        if start < probe_start or end > probe_end or end - start < required + 0.4:
            continue
        winners.append({'start': start, 'end': end, 'score': score, 'crop_pct': crop,
                        'reason': str(item.get('reason') or '')[:300]})
    return max(winners, key=lambda item: (item['score'], item['end'] - item['start']), default=None)


def build_sheet(probe, destination, start, duration, ffmpeg):
    """Render twelve evenly spaced frames and label their source timestamps."""
    from PIL import Image, ImageDraw
    interval = duration / 12.0
    grid = Path(destination).with_suffix('.grid.jpg')
    try:
        result = subprocess.run([
            ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-i', str(probe),
            '-vf', f'fps=1/{interval:.6f}:start_time=0,scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2,tile=4x3',
            '-frames:v', '1', '-q:v', '3', str(grid),
        ], capture_output=True, timeout=20, check=False)
        if result.returncode or not grid.is_file():
            return False
        with Image.open(grid) as src:
            sheet = src.convert('RGB')
        draw = ImageDraw.Draw(sheet)
        for n in range(12):
            x, y = (n % 4) * 320, (n // 4) * 180
            label = f'{start + n * interval:.1f}s'
            draw.rectangle((x, y, x + 92, y + 20), fill='black')
            draw.text((x + 4, y + 3), label, fill='white')
        sheet.save(destination, 'JPEG', quality=85)
        return True
    finally:
        grid.unlink(missing_ok=True)


def inspect_candidate(candidate, timestamp, required, scene, output_dir, client,
                      downloader, ffmpeg, ffprobe, *, session_jar=None,
                      stream_downloads=False, cancellation=None):
    """Return (status, chosen window, audit); unavailable never masquerades as verified."""
    if client is None:
        return 'unavailable', None, {'reason': 'vision_unavailable'}
    start = max(0., float(timestamp) - 6.)
    end = float(timestamp) + float(required) + 10.
    try:
        source_duration = float(candidate.get('duration') or 0)
    except (TypeError, ValueError):
        source_duration = 0
    if source_duration > 0:
        end = min(end, source_duration)
    if end - start < required + .4:
        return 'rejected', None, {'reason': 'source_too_short'}
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    probe, sheet = output_dir / 'probe.mp4', output_dir / 'contact.jpg'
    audit = {'probe_start': round(start, 3), 'probe_end_requested': round(end, 3)}
    try:
        if not downloader(candidate, probe, start, end - start, session_jar=session_jar,
                          stream_downloads=stream_downloads, cancellation=cancellation):
            return 'unavailable', None, dict(audit, reason='probe_download_failed')
        from services.media_integrity import probe_media
        metadata = probe_media(probe, ffprobe_path=ffprobe, min_bytes=1024, timeout_seconds=10)
        if not metadata.valid or metadata.duration < required + .4:
            return 'rejected', None, dict(audit, reason='probe_too_short_or_invalid')
        actual_end = min(end, start + metadata.duration)
        audit['probe_end'] = round(actual_end, 3)
        if not build_sheet(probe, sheet, start, actual_end - start, ffmpeg):
            return 'unavailable', None, dict(audit, reason='contact_sheet_failed')
        video_id = str(candidate.get('id') or candidate.get('video_id') or '')
        prompt = (f"Review the chronological 4x3 contact sheet, with absolute source timestamps. "
                  f"Scene: {str(scene)[:600]}. Required clean continuous span: {required + .4:.2f} seconds. "
                  "Select footage that actually depicts the scene; reject presenters, irrelevant visuals, "
                  "central text and transitions. Natural camera cuts are acceptable. Corner logos are acceptable "
                  "if removable with a centered crop of at most 15 percent. Return JSON {\"candidates\":[{\"candidate_id\": "
                  f"\"{video_id}\", \"score\": 0-10, \"is_usable\": true/false, "
                  "\"clean_start_sec\": number, \"clean_end_sec\": number, "
                  "\"needs_crop_zoom_pct\": number, \"reason\": string}]}. "
                  "The clean range must be entirely within the sampled probe. Do not invent footage between frames.")
        from services.job_context import phase_scope
        try:
            vision_wall = max(10., min(180., float(os.environ.get('VIDZEO_YT_CONTACT_VISION_TIMEOUT', '120'))))
        except (TypeError, ValueError):
            vision_wall = 120.
        with phase_scope(vision_wall):
            response = client.analyze_image(str(sheet), prompt, 'Return only the requested JSON. Judge visible evidence conservatively.')
        entries = response.get('candidates') if isinstance(response, dict) else None
        if not isinstance(entries, list) or not any(
            isinstance(item, dict) and str(item.get('candidate_id', '')) == video_id
            and isinstance(item.get('is_usable'), bool) for item in entries
        ):
            return 'unavailable', None, dict(audit, reason='invalid_vision_response')
        winner = select_window(response, start, actual_end, required, candidate_id=video_id)
        if winner is None:
            return 'rejected', None, dict(audit, reason='no_valid_vision_window')
        return 'accepted', winner, dict(audit, **winner, status_reason='vision_window_selected')
    except Exception as exc:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        return 'unavailable', None, dict(audit, reason='probe_or_vision_error', error_type=type(exc).__name__)
