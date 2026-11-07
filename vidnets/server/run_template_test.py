"""Test video production with editing templates + YouTube clips."""
import sys, os, time, traceback

os.environ.setdefault('VIDZEO_YT_MIN_SOURCE_DURATION', '15')
os.environ.setdefault('VIDZEO_YT_DOWNLOAD_CONCURRENCY', '8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module

SCRIPT = """The Silicon Valley story begins in a small garage in Palo Alto.

In 1939, two Stanford graduates named William Hewlett and David Packard started their company with just 538 dollars. This tiny garage would become the birthplace of Silicon Valley.

Stanford University played a crucial role in nurturing these early entrepreneurs. Professor Frederick Terman encouraged his students to build their own companies rather than leaving for the East Coast.

By the 1960s, the area had earned the name Silicon Valley because of the large number of chip manufacturers operating there. Companies like Intel, Fairchild Semiconductor, and AMD all had their roots in this region.

The 1980s brought personal computing to the masses. Apple Computer, founded by Steve Jobs and Steve Wozniak, released the Macintosh in 1984. It changed how ordinary people interacted with technology forever.

The 1990s saw the rise of the internet. Companies like Google, Amazon, and eBay transformed how we shop, search, and connect with each other across the globe.

Today, Silicon Valley remains the technology capital of the world. From artificial intelligence to electric vehicles, the next revolution is being born in the same small valley that started with a 538 dollar investment in a garage."""

config = {
    'script': SCRIPT,
    'title': 'The Silicon Valley Story',
    'name': 'The Silicon Valley Story',
    'image_source': 'YouTube Clips',
    'type': 'long',
    'scene_mode': 'Every 2 Beats',
    'vo_mode': 'Use Audiogram',
    'voice': 'en-US-AndrewNeural',
    'tts_model': 'audiogram',
    'whisper_mode': 'local',
    'aspect_ratio': '16:9',
    'resolution': '1080p',
    'captions_enabled': True,
    'music_volume': 15,
}


def on_progress(pct, msg):
    print(f"  [{pct:3d}%] {msg}", flush=True)


if __name__ == '__main__':
    t0 = time.time()
    print("=" * 70)
    print("TEMPLATE TEST: Silicon Valley Story (YouTube Clips + VOX Templates)")
    print("=" * 70, flush=True)
    try:
        result = app_module._run_video_pipeline(config, progress_callback=on_progress)
        elapsed = time.time() - t0
        print("\n=== PIPELINE RESULT ===")
        print(f"  Time: {elapsed:.1f}s")
        if isinstance(result, dict):
            for k, v in result.items():
                print(f"  {k}: {v}")
        else:
            print(f"  Result: {result}")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"\n=== PIPELINE FAILED after {elapsed:.1f}s ===")
        traceback.print_exc()
