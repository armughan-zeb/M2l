import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module

SCENE_TEXTS = [
    "Prince Harry, the Duke of Sussex, grew up in the global spotlight as the son of King Charles III and the late Princess Diana.",
    "In May 2018, Prince Harry and Meghan Markle celebrated their royal wedding at St George's Chapel in Windsor Castle.",
    "Their family journey began with the joyful birth of their son, Prince Archie of Sussex, in London in May 2019.",
    "Seeking a new chapter of independence and privacy, Harry and Meghan made the decision to step back from senior royal duties and move to California.",
    "In June 2021, their family was blessed with the birth of their daughter, Princess Lilibet of Sussex, named in honor of Queen Elizabeth II and Princess Diana.",
    "Today, Prince Harry and Meghan cherish family life in Montecito, balancing their philanthropic Archewell Foundation with raising their young children."
]

SCRIPT = "\n\n".join(SCENE_TEXTS)

config = {
    'script': SCRIPT,
    'title': 'Prince Harry and His Family: The Sussex Story',
    'name': 'Prince Harry and His Family: The Sussex Story',
    'topic': 'Prince Harry family',
    'topic_anchor': 'Prince Harry Meghan Markle Sussex royal family',
    'category': 'people',
    'image_source': 'Mixed Media',
    'type': 'long',
    'scene_mode': 'Every 2 Beats',
    'vo_mode': 'Use Audiogram',
    'voice': 'en-GB-RyanNeural',
    'tts_model': 'audiogram',
    'whisper_mode': 'local',
    'aspect_ratio': '16:9',
    'resolution': '1080p',
    'allow_emergency_media': True,
}

PHASES = {}

def on_progress(pct, msg):
    now = time.time()
    key = str(msg)[:60]
    if key not in PHASES:
        PHASES[key] = now
        print(f"  [progress {pct:5.1f}%] {msg}", flush=True)
    else:
        print(f"  [progress {pct:5.1f}%] {msg} (cont.)", flush=True)

if __name__ == '__main__':
    t0 = time.time()
    print('=' * 80)
    print('STARTING PRINCE HARRY FAMILY PRODUCTION (Mixed Media + 1080p + British Voice)')
    print('=' * 80, flush=True)

    os.environ['VIDZEO_VIP_SAFETY_OVERRIDE'] = '1'
    os.environ['VIDZEO_WEB_FAST_MODE'] = '1'
    os.environ['VIDZEO_ALLOW_EMERGENCY_MEDIA'] = '1'
    os.environ.setdefault('VIDZEO_WEB_WAIT_FOR_FINAL_AUDIO_SCENES', '1')

    try:
        result = app_module._run_video_pipeline(config, progress_callback=on_progress)
        print('\n' + '=' * 80)
        print('PIPELINE RESULT COMPLETED SUCCESSFULLY!')
        print('=' * 80)
        print(repr(result))
        elapsed = time.time() - t0
        print(f'\nTotal Elapsed Time: {elapsed:.1f}s')
        if isinstance(result, dict):
            out = result.get('video_path') or result.get('output_path') or ''
            print(f'FINAL VIDEO OUTPUT: {out}')
    except Exception as e:
        print('\n' + '=' * 80)
        print('PIPELINE FAILED WITH ERROR')
        print('=' * 80)
        traceback.print_exc()
