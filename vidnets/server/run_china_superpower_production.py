"""Produce a mixed-media documentary about China's global rise."""

from __future__ import annotations

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


SCRIPT = """In just a few decades, China moved from an inward-looking economy to the manufacturing and trading center of the modern world. How did that transformation happen?

The turning point came in 1978, when Deng Xiaoping began opening the economy while keeping the Communist Party in control. Farms gained new incentives, private enterprise slowly expanded, and foreign investment was invited into special economic zones such as Shenzhen. A fishing village became a laboratory for global capitalism.

China's greatest advantage was scale. Hundreds of millions of workers moved into factories that could produce electronics, clothing, machinery, and eventually electric vehicles at extraordinary speed and competitive cost. Roads, ports, high-speed rail, and power stations connected the country into one enormous industrial system.

Then came global integration. China joined the World Trade Organization in 2001, giving its exporters deeper access to international markets. Companies around the world moved supply chains there, while Chinese firms learned engineering, logistics, and mass production. The result was a manufacturing ecosystem difficult for competitors to reproduce.

The next phase was technological ambition. Beijing invested heavily in telecommunications, artificial intelligence, batteries, solar panels, and advanced infrastructure. Companies such as Huawei, BYD, and Alibaba showed that China could move from assembling other people's products to building globally competitive technology of its own.

China also used trade and infrastructure as diplomacy. Through the Belt and Road Initiative, Chinese banks and companies financed railways, ports, power plants, and digital networks across Asia, Africa, Europe, and Latin America. Economic relationships became strategic influence.

But superpower status brings pressure. An aging population, property debt, environmental costs, and tensions with the United States now test the model. China is powerful because its industrial scale, state planning, private enterprise, and global connections reinforce one another. The question is no longer whether China has risen. It is whether that system can keep adapting as the world around it changes."""


CONFIG = {
    "script": SCRIPT,
    "title": "How China Became the Global Superpower",
    "name": "How China Became the Global Superpower",
    "image_source": "Mixed Media",
    "mixed_media_weights": {"YouTube Clips": 0.48, "Web Images": 0.52},
    "mixed_media_policy": "target",
    "strict_mixed_percentages": False,
    "type": "long",
    "scene_mode": "Every 2 Beats",
    "vo_mode": "Use Audiogram",
    "voice": "en-US-ChristopherNeural",
    "tts_model": "audiogram",
    "whisper_mode": "local",
    "aspect_ratio": "16:9",
    "resolution": "1080p",
    "semantic_render_enabled": True,
    "semantic_render_required": True,
    "semantic_variation_required": True,
    "semantic_render_quality": "standard",
    "captions_enabled": False,
}


def on_progress(percent, message):
    print(f"[china-doc {float(percent):.0f}%] {message}", flush=True)


if __name__ == "__main__":
    started = time.time()
    os.environ.setdefault("VIDZEO_YT_PREFERRED_HEIGHT", "1080")
    os.environ.setdefault("VIDZEO_YT_DOWNLOAD_HEIGHT", "1080")
    os.environ.setdefault("VIDZEO_MIXED_YT_SHADOW_WEB", "0")
    os.environ.setdefault("VIDZEO_WEB_WAIT_FOR_FINAL_AUDIO_SCENES", "1")
    os.environ.setdefault("VIDZEO_WEB_FAST_MODE", "1")
    try:
        result = app_module._run_video_pipeline(CONFIG, progress_callback=on_progress)
        print("RESULT:", result)
    except Exception:
        traceback.print_exc()
        raise
    finally:
        print(f"Elapsed: {time.time() - started:.1f}s", flush=True)
