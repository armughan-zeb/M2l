"""Create a media-independent showcase for every advanced visual grammar."""

from __future__ import annotations

import json
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    scenes = [
        ("Timeline", "date_flip_calendar", {"year": "1912", "title": "The date the promise changed", "events": [{"year": "1912", "label": "THE PROMISE"}]}),
        ("TextCard", "field-caption", {"title": "Southampton to New York", "kicker": "FIELD NOTE", "text": "Southampton to New York", "subtitle": "A route becomes a relationship when the evidence is placed in context."}),
        ("StatCard", "hero_counter_large", {"value": "1,500", "label": "PEOPLE ABOARD", "title": "Scale became the story"}),
        ("Diagram", "code_console", {"label": "RADIO LOG", "message": "TWO WARNINGS RECEIVED", "title": "A signal entered the system"}),
        ("QuoteCard", "editorial_large_quote", {"kicker": "WITNESS ACCOUNT", "text": "The sea was calm during the final minute.", "subtitle": "Recorded testimony / source-backed quotation"}),
        ("Headline", "newspaper_front_page", {"title": "THE ARCHIVE REVEALED THE MISSING MESSAGE", "kicker": "DOCUMENT TRACE", "text": "A headline is treated as evidence, not decoration."}),
        ("DocumentHighlight", "yellow_marker_highlight", {"docTitle": "ARCHIVE / EXHIBIT 07", "date": "1985", "paragraph": "The missing message was preserved in the archive.", "highlight": "missing message was preserved"}),
        ("AnimatedMap", "route_flight_arc", {"title": "The route crossed the North Atlantic", "from": "SOUTHAMPTON", "to": "NEW YORK"}),
        ("Chart", "trend_line_with_markers", {"title": "Warnings accumulated before the decision", "data": [{"label": "01", "value": "1"}, {"label": "02", "value": "2"}, {"label": "03", "value": "4"}]}),
        ("Comparison", "split_slider_before_after", {"title": "Confidence became panic", "left": "CONFIDENCE", "right": "PANIC", "leftValue": "BEFORE", "rightValue": "AFTER"}),
        ("Collage", "three_panel_asymmetric", {"title": "Three fragments, one argument", "text": "Archive, testimony, and place converge in the edit."}),
        ("ChapterTitle", "bold_editorial_kicker", {"title": "WHAT THE EVIDENCE CHANGED", "kicker": "CHAPTER 02"}),
    ]
    palette = {"background": "#0b0f14", "surface": "#151c25", "primary": "#f8fafc", "secondary": "#a7b0bc", "accent": "#f2c94c", "accent2": "#e07a5f", "border": "#34404d", "danger": "#d95d5d"}
    scenes_out = []
    for index, (scene_type, variant, props) in enumerate(scenes):
        start = index * 2.5
        scenes_out.append({
            "id": f"showcase-{index + 1}",
            "scene_id": f"showcase-{index + 1}",
            "scene_index": index,
            "start": start,
            "duration": 2.5,
            "narration": props.get("text") or props.get("title") or props.get("message") or "Documentary visual beat",
            "semantic_beat": {"role": scene_type.lower(), "treatment": variant, "importance": .8},
            "primary_visual": {"type": scene_type, "variant": variant, "props": props},
            "graphic": {"template_id": "full-bleed", "props": {}},
            "media": None,
            "motion": {"entrance": "mask_reveal"},
            "transition": {"template_id": "hard_cut", "duration": .2},
        })
    manifest = {
        "title": "Advanced Documentary Grammar Showcase",
        "topic": "Documentary evidence systems",
        "category": "investigative-documentary",
        "fps": 30,
        "width": 1280,
        "height": 720,
        "duration": len(scenes_out) * 2.5,
        "qa_showcase": True,
        "visual_dna": {"genre": "vox-editorial", "palette": palette, "typography": {"display": "Archivo Black, Inter, sans-serif", "body": "Inter, Arial, sans-serif", "numbers": "JetBrains Mono, monospace"}, "render_mode": "advanced-doc-v1", "visual_system": "advanced-doc-v1", "seed": 2026},
        "scenes": scenes_out,
    }
    output = root / "output" / "advanced_documentary_showcase.manifest.json"
    output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
