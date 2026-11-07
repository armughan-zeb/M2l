"""Fit a small TTS pace mismatch before captions, media contracts and rendering.

No new synthesis, invented narration, silence padding or repeated picture.
The original narration remains immutable and the derived audio is checkpointed.
"""
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import time
import uuid

from .media_integrity import resolve_ffmpeg_pair
from .speech_timing import audio_fingerprint, save_speech_timing


def duration_fit(duration, minimum, maximum=0):
    duration, minimum = float(duration), float(minimum)
    maximum=float(maximum or 0)
    if not math.isfinite(maximum) or maximum<0 or (maximum and minimum>maximum):
        raise ValueError('Invalid narration duration limit')
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(minimum) or minimum < 0:
        raise ValueError('Invalid narration duration contract')
    if maximum and duration>maximum:
        target=max(.5,maximum-.2)
        tempo=duration/target
        if tempo>1.2:
            raise ValueError('PLAN_LIMIT_REACHED: Narration exceeds this plan duration at a natural pace. Shorten the script or upgrade. The narration checkpoint is retained.')
        return {'tempo':tempo,'target':target,'scale':1/tempo}
    if minimum == 0 or duration + .5 >= minimum:
        return None
    target = minimum + .25
    tempo = duration / target
    if tempo < .85:
        raise ValueError(
            f'Narration is too short for natural pacing ({duration:.2f}s / {minimum:.2f}s); '
            'expand the script before media retrieval/rendering. Narration checkpoint retained.'
        )
    return {'tempo': tempo, 'target': target, 'scale': 1 / tempo}


def _cancelled(cancellation):
    if cancellation is not None:
        if callable(getattr(cancellation, 'raise_if_cancelled', None)):
            cancellation.raise_if_cancelled()
        elif callable(getattr(cancellation, 'is_set', None)) and cancellation.is_set():
            raise RuntimeError('Narration pacing cancelled')


def _encode(args, cancellation):
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, creationflags=0x08000000 if os.name == 'nt' else 0)
    deadline = time.monotonic() + 180
    try:
        while True:
            _cancelled(cancellation)
            if time.monotonic() >= deadline:
                raise TimeoutError('Narration pacing exceeded its 180-second deadline')
            try:
                _, error = process.communicate(timeout=.5)
                if process.returncode:
                    raise RuntimeError('Narration pacing failed: ' + error.decode(errors='replace')[-1000:])
                return
            except subprocess.TimeoutExpired:
                pass
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


def fit_audio_plan(plan, config, expected_plan, cancellation=None):
    """Return aligned, paced audio; a matching completed plan is a pure no-op."""
    minimum = config.get('minimum_duration_seconds') or 0
    maximum = config.get('maximum_duration_seconds') or 0
    fit = duration_fit(plan['duration'], minimum, maximum)
    if not fit:
        return plan
    _cancelled(cancellation)
    source = Path(plan['audio_path'])
    source_hash = audio_fingerprint(source)
    package = config.get('_output_package_dir') or config.get('output_package_dir')
    directory = Path(package) / 'checkpoints' if package else source.parent
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f'narration-paced-{source_hash[:16]}-{round(fit["target"] * 1000)}.wav'
    receipt_file = output.with_suffix('.json')
    ffmpeg, ffprobe = resolve_ffmpeg_pair()
    valid_saved = False
    try:
        saved = json.loads(receipt_file.read_text(encoding='utf-8'))
        valid_saved = (saved['source_sha256'] == source_hash and saved['minimum'] == minimum
                       and saved['output_sha256'] == audio_fingerprint(output))
    except (OSError, ValueError, KeyError):
        pass
    if not valid_saved:
        temporary = output.with_name(output.stem + '.' + uuid.uuid4().hex + '.partial.wav')
        try:
            _encode([ffmpeg, '-y', '-nostdin', '-v', 'error', '-i', str(source),
                     '-af', f'atempo={fit["tempo"]}', '-c:a', 'pcm_s16le', str(temporary)], cancellation)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    result = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration',
                             '-of', 'json', str(output)], capture_output=True, check=True, timeout=15)
    duration = float(json.loads(result.stdout)['format']['duration'])
    if duration + .1 < float(minimum) or abs(duration - fit['target']) > .5 or (maximum and duration>float(maximum)+.1):
        raise RuntimeError('Paced narration failed duration validation; original retained')
    scale = duration / float(plan['duration'])
    words = []
    for raw in plan.get('whisper_words') or []:
        word = dict(raw)
        for key in ('start', 'end'):
            word[key] = float(word[key]) * scale
        words.append(word)
    # Rebuild immutable V2 scene hashes and the persisted storyboard on the new
    # word clock, rather than mutating IDs/hashes or stretching only captions.
    if expected_plan.get('script_plan_v2') is not None:
        from .pipeline_v2_runtime import build_aligned_audio_plan_v2
        aligned = build_aligned_audio_plan_v2(expected_plan, words, audio_path=str(output),
                    audio_duration=duration, scene_mode=plan.get('scene_mode', 'Every Beat'),
                    runtime=config.get('_pipeline_v2_runtime'))
        updated = {**plan, **aligned}
    else:
        updated = copy.deepcopy(plan)
        updated.update(audio_path=str(output), duration=duration, whisper_words=words,
                       scene_durations=[float(value) * scale for value in plan['scene_durations']])
        for window in updated.get('scene_timing_windows') or []:
            for key in ('start_seconds', 'end_seconds', 'duration_needed'):
                if key in window:
                    window[key] *= scale
    from .audio_timeline_validation import validate_whisper_scene_plan
    updated['scene_alignment_validation'] = validate_whisper_scene_plan(
        config.get('script', ''), updated['scene_texts'], updated.get('scene_timing_windows') or [],
        words, duration, timing_source=plan.get('timing_source', 'whisper'))
    if updated['scene_alignment_validation'].get('status') == 'fail':
        raise RuntimeError('Paced narration scene alignment failed')
    output_hash = audio_fingerprint(output)
    if updated.get('whisper_alignment'):
        updated['whisper_alignment'] = {**updated['whisper_alignment'], 'audio_sha256': output_hash}
    if words:
        save_speech_timing(output, words, source=plan.get('timing_source', 'whisper'))
    receipt = {'version': 1, 'source_sha256': source_hash, 'output_sha256': output_hash,
               'source_duration': plan['duration'], 'duration': duration, 'minimum': minimum,
               'tempo': fit['tempo'], 'timeline_scale': scale, 'pitch_preserved': True,
               'narration_regenerated': False, 'voice': config.get('voice', '')}
    updated['duration_normalization'] = receipt
    temporary_receipt = receipt_file.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    temporary_receipt.write_text(json.dumps(receipt), encoding='utf-8')
    os.replace(temporary_receipt, receipt_file)
    return updated
