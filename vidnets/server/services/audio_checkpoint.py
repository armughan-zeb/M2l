"""Reuse an unchanged narration timeline while repairing failed media retrieval."""
import json
import math
import re
from pathlib import Path


def load_saved_audio_plan(package_dir, config):
    checkpoint = Path(package_dir) / 'checkpoints'
    try:
        request = json.loads((checkpoint / 'request.json').read_text(encoding='utf-8'))
        plan = json.loads((checkpoint / 'audio_plan.json').read_text(encoding='utf-8'))
        previous = request['config']
        # Scene and voice settings affect both the audio and its timing contract.
        defaults = {'voice': 'Adam (Male US)', 'vo_mode': 'Use Audiogram',
                    'tts_model': 'audiogram', 'edge_tts_options': {},
                    'openrouter_tts_options': {}, 'scene_mode': 'Every Beat',
                    'language': None, 'pipeline_v2_mode': 'on'}
        for key, default in defaults.items():
            if previous.get(key, default) != config.get(key, default):
                return None
        words = lambda value: re.findall(r"\w+", str(value or '').casefold())
        script = words(config.get('script'))
        if not script or script != words(request.get('script')):
            return None
        texts = plan['scene_texts']
        durations = [float(value) for value in plan['scene_durations']]
        duration = float(plan['duration'])
        if script != words(' '.join(texts)) or len(texts) != len(durations):
            return None
        if not math.isfinite(duration) or duration <= 0 or not durations:
            return None
        if any(not math.isfinite(value) or value <= 0 for value in durations):
            return None
        if abs(sum(durations) - duration) > 0.1:
            return None
        if (plan.get('scene_alignment_validation') or {}).get('status') == 'fail':
            return None
        audio = Path(plan['audio_path'])
        if not audio.is_file() or audio.stat().st_size < 128:
            return None
        from .narration_quality import validate_narration_audio
        plan['narration_quality'] = validate_narration_audio(audio)
        return plan
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        return None
