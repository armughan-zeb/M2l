import sys, os, time, traceback

os.environ.setdefault('VIDZEO_YT_MIN_SOURCE_DURATION', '20')
os.environ['VIDZEO_VIP_SAFETY_OVERRIDE'] = '1'
os.environ['VIDZEO_WEB_FAST_MODE'] = '1'
os.environ['VIDZEO_ALLOW_EMERGENCY_MEDIA'] = '1'
os.environ['VIDZEO_YT_ALLOW_PARTIAL_COVERAGE'] = '1'
os.environ.setdefault('VIDZEO_WEB_WAIT_FOR_FINAL_AUDIO_SCENES', '1')
os.environ['VIDZEO_XFADE_MIN_REMAINING_SECONDS'] = '60'
os.environ['VIDZEO_SEMANTIC_GRAPHIC_COVERAGE'] = '0.75'

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module  # noqa: E402

SCENE_TEXTS = [
    "In the early hours of March 18, 1990, two men disguised as Boston police officers approached the Isabella Stewart Gardner Museum.",
    "Bluffing their way past the side security entrance, they tied up the night guards and locked them in the basement.",
    "For eighty-one minutes, the thieves roamed the quiet galleries, slicing thirteen priceless masterpieces directly out of their frames.",
    "Among the stolen treasures were works by Rembrandt, Degas, Manet, and Johannes Vermeer rarest masterpiece, The Concert.",
    "Totaling over five hundred million dollars, it remains the largest unsolved private property theft in recorded history.",
    "The Federal Bureau of Investigation launched an international manhunt, chasing leads from Boston mobsters to overseas syndicates.",
    "To this day, the museum displays the empty gilded frames on its walls, waiting for the lost masterpieces to return.",
    "With a ten million dollar reward still unclaimed, the mystery of the missing masterpieces continues to haunt the world."
]

SCRIPT = "\n\n".join(SCENE_TEXTS)

config = {
    'script': SCRIPT,
    'title': 'The Gardner Museum Heist - 500 Million Dollar Mystery',
    'name': 'The Gardner Museum Heist - 500 Million Dollar Mystery',
    'topic': 'The Isabella Stewart Gardner Museum Heist',
    'topic_anchor': 'Gardner Museum art heist',
    'category': 'crime',
    'semantic_theme': 'investigative_crime',
    'image_source': 'All Sources',
    'type': 'long',
    'scene_mode': 'Every 2 Beats',
    'vo_mode': 'Use Audiogram',
    'voice': 'en-US-ChristopherNeural',
    'tts_model': 'audiogram',
    'whisper_mode': 'local',
    'aspect_ratio': '16:9',
    'resolution': '1080p',
    'allow_emergency_media': True,
    'semantic_render_required': True,
    'semantic_variation_seed': 42,
}


def on_progress(pct, msg):
    print(f"  [progress {pct:5.1f}%] {msg}", flush=True)


if __name__ == '__main__':
    t0 = time.time()
    print("=" * 80)
    print("STARTING CRIME SMART EDITING PRODUCTION (1 MINUTE)")
    print("Niche: Crime | Branding: Crimson Red / Noir | Font: Anton / Open Sans")
    print("Editing: Transitions + Template Animations + Synchronized SFX")
    print("=" * 80, flush=True)

    try:
        result = app_module._run_video_pipeline(config, progress_callback=on_progress)
        print("\n" + "=" * 80)
        print("CRIME PRODUCTION COMPLETED SUCCESSFULLY!")
        print("=" * 80)
        print(repr(result))
        if isinstance(result, dict) and result.get("output_path"):
            print(f"\nFINAL VIDEO OUTPUT: {result['output_path']}")
    except Exception as e:
        print("\n" + "=" * 80)
        print("PIPELINE FAILED WITH ERROR")
        print("=" * 80)
        traceback.print_exc()
        print("ERROR:", repr(e))
    finally:
        print(f"\nTotal Elapsed Time: {time.time() - t0:.1f}s", flush=True)

