"""Produce a 1-minute documentary with EVERYTHING included.

Runs the real pipeline stages (Edge TTS voice-over, Whisper scene sync, live web
media retrieval, captions) and then renders through the semantic FFmpeg renderer
so VOX text cards, stat counters, kinetic type, overlays, transitions, and
procedural sound cues are all baked into the final master. Captions are burned in
as a final pass (the semantic renderer does not burn them itself).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

os.environ.setdefault("VIDZEO_REMOTION_RENDER", "0")
os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
os.environ.setdefault("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")

os.environ.setdefault("VIDZEO_QWEN_VISION_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_QWEN_VISION_DEEP_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_QWEN_VISION_FALLBACK_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_LLM_MODEL", "qwen/qwen-2.5-7b-instruct")
os.environ.setdefault("VIDZEO_QUERY_LLM_MODEL", "qwen/qwen-2.5-7b-instruct")
os.environ.setdefault("VIDZEO_OPENROUTER_QUERY_MODEL", "qwen/qwen-2.5-7b-instruct")

SCRIPT = """In July 1969, the world watched as Apollo 11 carried three astronauts toward the Moon. Neil Armstrong, Buzz Aldrin, and Michael Collins launched from Kennedy Space Center atop a Saturn V rocket, the most powerful machine ever built.

The journey covered nearly a quarter million miles. While Collins orbited alone in the command module, Armstrong and Aldrin descended to the surface in the lunar module named Eagle.

On July 20, with just seconds of fuel remaining, Armstrong guided Eagle to a safe landing. He radioed back to Earth, "The Eagle has landed." Hours later, he became the first human to step onto another world.

Armstrong's words, "one small step for a man, one giant leap for mankind," were heard by half a billion people across the planet. The astronauts planted an American flag and collected lunar samples before returning to Collins.

Apollo 11 was not just a technical achievement. It was a moment when humanity saw itself from a distance for the first time, a small blue planet suspended in the blackness of space."""

OUTPUT_NAME = "apollo_11_1min_everything"
TITLE = "Apollo 11: One Small Step"


def _progress_bar(fraction: float, message: str) -> None:
    pct = int(max(0.0, min(1.0, fraction)) * 100)
    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
    print(f"\r  [{bar}] {pct:3d}% {str(message)[:60]:60s}", end="", flush=True)
    if fraction >= 1.0:
        print()


def _ffmpeg_subtitles_filter(path: Path) -> str:
    norm = path.resolve().as_posix()
    escaped = norm.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return f"subtitles='{escaped}'"


def _burn_captions(video: Path, subtitle: Path, output: Path) -> None:
    print(f"\n  [captions] burning subtitle file into final master...")
    vf = _ffmpeg_subtitles_filter(subtitle)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(video),
        "-vf", vf,
        "-c:a", "copy",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=900)
    if result.returncode != 0 or not output.exists() or output.stat().st_size < 4096:
        raise RuntimeError(f"Caption burn failed: {result.stderr.decode(errors='replace')[:400]}")
    print(f"  [captions] burned -> {output}")


def main() -> int:
    import app as vidzeo_app
    from services.native_render_policy import procedural_sound_library, render_semantic_documentary
    from services.semantic_edit_engine import build_semantic_edit_manifest
    from services.transcription_service import generate_captions, unload_whisper_model

    words = len(SCRIPT.split())
    print(f"[everything] words={words} (target ~60s at ~3 wps)", flush=True)

    config = {
        "type": "long",
        "name": OUTPUT_NAME,
        "title": TITLE,
        "script": SCRIPT,
        "image_source": "Web Images",
        "scene_mode": "Every 2 Beats",
        "vo_mode": "Use Audiogram",
        "tts_model": "audiogram",
        "voice": "Adam (Male US)",
        "whisper_mode": "local",
        "captions_enabled": True,
        "caption_mode": "phrase",
        "words_per_group": 4,
        "quality": "standard",
        "resolution": "1080p",
        "visual_understanding": False,
        "strict_mixed_percentages": False,
        "strict_retrieval_accuracy": True,
        "fail_on_unresolved_media": False,
        "auto_export": True,
        "semantic_edit_enable": True,
        "semantic_render_enabled": True,
        "semantic_theme": "history_archive",
    }

    report_progress = vidzeo_app._make_progress_reporter(_progress_bar)
    print(f"[everything] Starting at {time.strftime('%H:%M:%S')}", flush=True)
    started = time.monotonic()

    whisper_handle = vidzeo_app._start_whisper_warmup(config)
    expected_scene_plan = vidzeo_app._build_expected_scene_plan(config, report_progress)
    audio_plan = vidzeo_app._build_audio_scene_plan(
        config, report_progress, expected_scene_plan, whisper_handle
    )

    scene_texts = list(audio_plan.get("scene_texts") or [])
    scene_durations = [float(value or 1) for value in (audio_plan.get("scene_durations") or [])]
    print(f"\n[everything] audio+whisper ready: {len(scene_texts)} scenes", flush=True)

    media_plan = vidzeo_app._resolve_media_paths(config, audio_plan, report_progress)
    media_plan = vidzeo_app._remap_media_plan_to_audio_scenes(media_plan, audio_plan)
    media_paths = vidzeo_app._align_scene_media_count(
        media_plan.get("media_paths") or [], len(scene_texts), cycle_existing=True
    )
    source_labels = vidzeo_app._align_scene_media_count(
        media_plan.get("source_labels") or [], len(scene_texts), cycle_existing=True
    )
    media_plan["media_paths"] = media_paths
    media_plan["source_labels"] = source_labels
    media_plan = vidzeo_app._repair_media_plan_for_render(
        media_plan=media_plan,
        audio_plan=audio_plan,
        job_config=config,
        render_resolution=vidzeo_app._resolve_render_resolution(config),
        title=TITLE,
        script=SCRIPT,
        target_scene_count=len(scene_texts),
    )
    fixed_paths, fixed_labels, repeat_fixes = vidzeo_app._break_back_to_back_media_repeats(
        media_plan.get("media_paths") or [],
        media_plan.get("source_labels") or [],
        config,
    )
    media_plan["media_paths"] = fixed_paths
    media_plan["source_labels"] = fixed_labels

    resolved = sum(1 for path in media_plan["media_paths"] if path)
    print(f"[everything] media ready: {resolved}/{len(media_plan['media_paths'])} assets", flush=True)

    report_progress(0.58, "Generating captions...")
    subtitle_path = generate_captions(
        audio_path=audio_plan["audio_path"],
        caption_mode="phrase",
        words_per_group=4,
        font="Arial",
        fontsize=48,
        bold=True,
        uppercase=True,
        text_color="#FFFFFF",
        highlight_color="#FFFF00",
        outline_color="#000000",
        outline_size=3,
        shadow_on=True,
        shadow_color="#000000",
        shadow_depth=2,
        bg_on=False,
        bg_color="#000000",
        bg_opacity=80,
        position="bottom",
        margin_v=50,
        animation="Pop",
        resolution=vidzeo_app._resolve_render_resolution(config),
        precomputed_words=audio_plan.get("whisper_words"),
    )
    try:
        unload_whisper_model()
    except Exception:
        pass
    if subtitle_path:
        print(f"[everything] captions ready: {subtitle_path}", flush=True)

    media_items = [
        {
            "path": str(Path(path).resolve()),
            "src": str(Path(path).resolve()),
            "type": "image",
            "label": (source_labels[index] if index < len(source_labels) else "") or "Web Images",
        }
        for index, path in enumerate(media_plan["media_paths"])
        if path
    ]

    voiceover = {"path": str(Path(audio_plan["audio_path"]).resolve()),
                 "src": str(Path(audio_plan["audio_path"]).resolve())}
    manifest = build_semantic_edit_manifest(
        scene_texts=scene_texts,
        scene_durations=scene_durations,
        media=media_items,
        editorial_beats=None,
        topic=TITLE,
        category="history",
        config={
            "fps": 30,
            "width": 1920,
            "height": 1080,
            "theme_id": "history_archive",
            "variation_seed": int(time.time()) % 2 ** 31,
            "sound_library": procedural_sound_library(),
            "voiceover": voiceover,
            "music": {},
            "render_quality": "cinematic",
            "captions_enabled": True,
        },
    )
    audit = (manifest.get("metrics") or {}).get("variation_audit") or {}
    if audit.get("status") != "pass":
        print(f"[everything] WARNING: variation audit status={audit.get('status')}")

    manifest_path = ROOT / "output" / f"{OUTPUT_NAME}_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"semantic_edit_manifest": manifest}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"[everything] semantic manifest saved: {manifest_path}", flush=True)

    report_progress(0.70, "Rendering semantic master (text cards, overlays)...")
    render_output = ROOT / "output" / f"{OUTPUT_NAME}_semantic.mp4"

    def _render_progress(fraction: float, message: str) -> None:
        report_progress(0.70 + (max(0.0, min(1.0, float(fraction))) * 0.28), message)

    result = render_semantic_documentary(
        manifest,
        render_output,
        {
            "name": TITLE,
            "semantic_render_quality": "cinematic",
            "semantic_render_scale": 1,
            "semantic_render_concurrency": 2,
            "semantic_render_required": True,
            "semantic_variation_required": False,
            "semantic_render_timeout_seconds": 1800,
        },
        progress_callback=_render_progress,
    )
    report_progress(0.98, "Burning captions...")
    print(f"[everything] semantic master: {result['output_path']}", flush=True)

    final_path = ROOT / "output" / f"{OUTPUT_NAME}.mp4"
    if subtitle_path:
        _burn_captions(Path(result["output_path"]), Path(subtitle_path), final_path)
    else:
        final_path = Path(result["output_path"])

    elapsed = time.monotonic() - started
    print(f"\n[everything] COMPLETE in {elapsed:.0f}s -> {final_path}", flush=True)

    payload = {
        "success": True,
        "video_path": str(final_path),
        "video": f"/output/{final_path.name}",
        "duration": round(float(audio_plan.get("duration") or 0.0), 3),
        "render_seconds": round(elapsed, 1),
        "scenes": len(scene_texts),
        "media_resolved": resolved,
        "semantic_manifest": str(manifest_path),
        "metrics": manifest.get("metrics") or {},
    }
    result_path = ROOT / "output" / f"{OUTPUT_NAME}_result.json"
    result_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"[everything] result saved: {result_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
