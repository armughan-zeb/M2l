"""Render individual template preview thumbnails via native FFmpeg."""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))


# ── All 42 template IDs with sample preview props ──
TEMPLATE_PREVIEWS = {
    "chapter-opener":     {"title": "CHAPTER ONE", "kicker": "THE STORY BEGINS"},
    "keyword-reveal":     {"text": "BREAKTHROUGH", "detail": "A key discovery that changed everything we knew about the case"},
    "date-card":          {"label": "JULY 20, 1969", "kicker": "HISTORIC DATE"},
    "lower-third":        {"title": "DR. SARAH CHEN", "subtitle": "Lead Investigator"},
    "evidence-callout":   {"text": "CLASSIFIED", "detail": "The documents were marked top secret until 2018"},
    "quote-card":         {"quote": "One small step for a man, one giant leap for mankind.", "attribution": "Neil Armstrong"},
    "stat-counter":       {"value": "8473", "label": "DOCUMENTS DECLASSIFIED"},
    "money-counter":      {"value": "24000000", "label": "UNTRACEABLE FUNDS", "unit": "M"},
    "kinetic-statement":  {"text": "THE SYSTEM WAS BROKEN", "detail": "Decades of oversight failure led to this moment"},
    "cold-open":          {"title": "WHAT REALLY HAPPENED?", "kicker": "THE QUESTION", "subtitle": "New evidence casts doubt on the official story"},
    "paper-explainer":    {"title": "SOURCE NOTE", "body": "Archival records from the National Security Archive reveal a pattern of deliberate misdirection across three administrations.", "source": "NSA Archives"},
    "profile-dossier":    {"title": "JAMES DONOVAN", "subtitle": "NEGOTIATOR", "detail": "Brooklyn-born lawyer who brokered the release of 9,700 prisoners"},
    "evidence-board":     {"text": "THE RECORD DID NOT MATCH", "detail": "Flight logs contradicted official testimony at three critical junctures", "stamp": "EVIDENCE"},
    "field-caption":      {"title": "MARIANA TRENCH", "subtitle": "DEEPEST POINT ON EARTH"},
    "exhibit-label":      {"title": "LUNAR SAMPLE 15415", "subtitle": "GENESIS ROCK"},
    "end-card":           {"title": "FOLLOW THE STORY", "subtitle": "MORE REVELATIONS COMING"},
    "listicle-build":     {"title": "KEY FINDINGS", "items": ["Classified documents destroyed", "Witness testimony suppressed", "Financial trail hidden"]},
    "witness-quote":      {"quote": "I saw the documents myself. They were destroyed the next morning.", "attribution": "Former Analyst"},
    "source-highlight":   {"title": "PENTAGON PAPERS", "body": "The 7,000-page study revealed systematic deception across four administrations.", "source": "Daniel Ellsberg"},
    "word-cascade":       {"text": "THE COVER-UP BEGAN IN 1973", "detail": "And continued for forty-two years"},
    "redaction-reveal":   {"text": "SECTION 4 WAS REMOVED", "label": "RESTRICTED FILE", "detail": "The missing pages detail payments to offshore accounts"},
    "cause-effect":       {"cause": "DEREGULATION", "effect": "MARKET CRASH", "label": "CAUSE → EFFECT"},
    "split-screen":       {"left": "BEFORE", "right": "AFTER", "label": "TRANSFORMATION"},
    "countdown-clock":    {"value": "T-30", "label": "SECONDS TO IMPACT"},
    "archival-frame":     {"title": "1947 ROSWELL REPORT"},
    "data-race":          {"left_label": "USA", "right_label": "USSR", "left_value": "12", "right_value": "1", "label": "MOON LANDINGS"},
    "blueprint-reveal":   {"title": "ESCAPE TUNNEL ROUTE", "detail": "120 meters from cell block D to the perimeter fence"},
    "signal-scan":        {"text": "DISTRESS SIGNAL DETECTED", "label": "RADIO INTERCEPT"},
    "bar-chart":          {"label": "ANNUAL BUDGET", "items": [{"label": "2020", "value": "24"}, {"label": "2021", "value": "38"}, {"label": "2022", "value": "42"}]},
    "line-chart":         {"label": "TEMPERATURE RISE", "points": [12, 18, 25, 31, 38, 44, 52]},
    "donut-chart":        {"label": "BUDGET ALLOCATION", "value": "67", "detail": "Defense spending"},
    "decision-tree":      {"root": "INVESTIGATE?", "left": "COVER UP", "right": "EXPOSE TRUTH"},
    "newsprint-reveal":   {"title": "SCANDAL BREAKS", "detail": "Front page story shocks the nation"},
    "timeline-trace":     {"label": "KEY EVENTS", "items": [{"label": "1973", "text": "Oil Crisis"}, {"label": "1979", "text": "Revolution"}, {"label": "1989", "text": "Wall Falls"}]},
    "map-route":          {"from": "NEW YORK", "to": "LOS ANGELES", "label": "TRANSCONTINENTAL ROUTE"},
    "gallery-stack":      {"title": "EVIDENCE ROOM", "detail": "14 classified photographs"},
    "callout-arrow":      {"text": "CRITICAL FINDING", "label": "FORENSIC ANALYSIS"},
    "pictogram-stat":     {"value": "8", "total": "10", "label": "MISSIONS COMPLETED"},
    "spotlight-lens":     {"title": "SUBJECT IDENTIFIED", "detail": "Facial recognition match: 94%"},
    "metric-grid":        {"value": "3,241", "label": "TOTAL CASES", "detail": "Pending review as of December"},
    "stamp-mark":         {"text": "DECLASSIFIED", "label": "TOP SECRET"},
    "index-card":         {"title": "CASE #4472", "detail": "Subject: Project Blue Book"},
    "calendar-rip":       {"date": "JULY 20", "event": "Apollo 11 Landing"},
}


def _default_theme():
    return {
        "palette": {
            "background": "#0b1320", "surface": "#16263b", "primary": "#f4f0e6",
            "accent": "#f2c14e", "secondary": "#49a6d8", "danger": "#ef6f6c",
        },
        "font_family": "Poppins, Arial, sans-serif",
        "display_font": "Bebas Neue, Arial, sans-serif",
    }


def generate_template_previews(output_dir: str | Path = None, concurrency: int = 4) -> dict:
    """Generate a still-frame PNG preview for every template ID.
    
    Returns {template_id: file_path, ...}
    """
    output_dir = Path(output_dir or ROOT / "output" / "template_previews")
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    from services.semantic_ffmpeg_renderer import render_semantic_documentary_ffmpeg

    # Render batches through the same native FFmpeg path used by production.
    batch_size = 6
    template_ids = list(TEMPLATE_PREVIEWS)

    for batch_start in range(0, len(template_ids), batch_size):
        batch = template_ids[batch_start:batch_start + batch_size]
        scenes = []
        for i, tid in enumerate(batch):
            props = TEMPLATE_PREVIEWS[tid]
            scenes.append({
                "id": f"prev-{tid}",
                "scene_index": i,
                "start": i * 1.0,
                "duration": 1.0,
                "narration": "",
                "media": {"type": "image", "src": ""},
                "graphic": {"template_id": tid, "props": props},
                "camera": {"motion": "static"},
                "transition": {"template_id": "cut", "duration": 0.01},
                "design": {"image_treatment": "hold", "seed": i},
                "sound_cues": [],
            })

        manifest = {
            "version": 1, "fps": 30, "width": 1920, "height": 1080,
            "duration": len(batch) * 1.0,
            "duration_in_frames": len(batch) * 30,
            "theme": _default_theme(),
            "scenes": scenes,
            "audio": {},
            "captions": {"enabled": False},
        }

        # Write manifest
        manifest_path = output_dir / f"preview_batch_{batch_start}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))

        output_video = output_dir / f"preview_batch_{batch_start}.mp4"
        try:
            render_semantic_documentary_ffmpeg(
                manifest,
                output_video,
                {"quality": "draft", "width": 1920, "height": 1080, "fps": 30},
            )
        except Exception as exc:
            print(f"   [preview] batch {batch_start} FAILED: {exc}")
            continue

        # Extract a frame from each scene (mid-frame = frame 15)
        import subprocess
        for i, tid in enumerate(batch):
            frame_path = output_dir / f"{tid}.png"
            mid_frame = i * 30 + 15
            extract = subprocess.run(
                [
                    str(ROOT / "tools" / "ffmpeg" / "ffmpeg.exe"),
                    "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(output_video),
                    "-vf", f"select=eq(n\\,{mid_frame})",
                    "-vframes", "1",
                    str(frame_path),
                ],
                capture_output=True, timeout=30,
            )
            if extract.returncode == 0 and frame_path.exists():
                results[tid] = str(frame_path)
                print(f"   [preview] {tid} → {frame_path}")

    print(f"\n[preview] Generated {len(results)}/{len(template_ids)} template previews → {output_dir}")
    return results


if __name__ == "__main__":
    start = time.monotonic()
    previews = generate_template_previews()
    elapsed = time.monotonic() - start
    print(f"[preview] Done in {elapsed:.0f}s")
