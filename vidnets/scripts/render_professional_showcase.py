"""Render only the professional documentary template showcase."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from PIL import Image, ImageDraw

from services.semantic_ffmpeg_renderer import render_semantic_documentary_ffmpeg


TEMPLATES = [
    ("archival_caption", {"location": "Rumeli Hisar, Istanbul", "date": "1868"}),
    ("date_numeral", {"date": "1969", "kicker": "A TURNING POINT", "detail": "The year the impossible became history"}),
    ("evidence_highlight", {"source": "ARCHIVE", "title": "THE RECORD SHOWS", "detail": "A grounded fact from the narration"}),
    ("profile_card", {"name": "NEIL ARMSTRONG", "role": "COMMANDER, APOLLO 11", "date": "JULY 1969"}),
    ("subject_grid", {"name": "MISSION CONTROL", "role": "HOUSTON, TEXAS", "detail": "The people behind the signal"}),
    ("listicle_steps", {"title": "THREE STEPS", "line1": "BUILD THE MACHINE", "line2": "CROSS THE DISTANCE", "line3": "BRING THE STORY HOME"}),
    ("timeline_strip", {"title": "THE TIMELINE", "line1": "LAUNCH", "line2": "LANDING", "line3": "RETURN"}),
    ("headline_center", {"kicker": "THE ARCHIVE", "line1": "ONE SMALL", "line2": "STEP", "subtitle": "A moment remembered"}),
    ("polaroid_card", {"caption": "THE CREW", "date": "SUMMER 1969"}),
    ("photo_frame", {"title": "SATURN V", "date": "APOLLO 11"}),
    ("presenter_card", {"name": "DR. GENE KRANZ", "role": "FLIGHT DIRECTOR"}),
    ("card_overlay", {"label": "NOTE", "title": "THE SIGNAL", "detail": "The critical detail behind the mission"}),
    ("archive_canvas", {"title": "MISSION FILE", "detail": "A single record survived the journey.", "date": "JULY 1969"}),
    ("step_progress", {"title": "THE PLAN", "line1": "IGNITE", "line2": "ASCEND", "line3": "TRANSFER"}),
    ("map_card", {"location": "CAPE KENNEDY", "detail": "LAUNCH COMPLEX 39A"}),
]

# Intensity values follow the overlays library semantics (film_grain ~0.04,
# dark_vignette ~0.6, dust_specks ~0.03, scanlines ~0.15, flicker ~0.2).
OVERLAY_ROTATION = [
    {"id": "film_grain", "intensity": 0.05}, {"id": "dark_vignette", "intensity": 0.55},
    {"id": "dust_specks", "intensity": 0.03}, {"id": "scanlines", "intensity": 0.12},
    {"id": "film_grain", "intensity": 0.04}, {"id": "letterbox"},
    {"id": "light_leak"}, {"id": "flicker", "intensity": 0.10},
    {"id": "film_grain", "intensity": 0.06}, {"id": "dust_specks", "intensity": 0.04},
    {"id": "dark_vignette", "intensity": 0.60}, {"id": "letterbox"},
    {"id": "film_grain", "intensity": 0.05}, {"id": "dark_vignette", "intensity": 0.50},
    {"id": "dust_specks", "intensity": 0.03},
]


def main() -> int:
    showcase_dir = ROOT / "temp" / "professional_showcase"
    showcase_dir.mkdir(parents=True, exist_ok=True)
    colors = [(24, 31, 43), (33, 29, 24), (20, 34, 35), (35, 25, 28)]

    scenes = []
    for index, (template_id, props) in enumerate(TEMPLATES):
        bg = showcase_dir / f"bg_{index}.png"
        Image.new("RGB", (1920, 1080), colors[index % len(colors)]).save(bg)
        duration = 3.0
        overlay_id = OVERLAY_ROTATION[index % len(OVERLAY_ROTATION)]
        scenes.append({
            "id": f"showcase-{index + 1}",
            "scene_index": index,
            "start": index * duration,
            "duration": duration,
            "narration": template_id,
            "media": {"src": str(bg), "type": "image"},
            "graphic": {
                "template_id": "full-bleed",
                "family": "media",
                "props": {},
                "variant": index,
                "start": index * duration,
                "duration": duration,
                "seed": index,
                "vox_template": template_id,
                "vox_props": props,
            },
            "overlays": [overlay_id],
            "camera": {"motion": "static", "seed": index, "scale": 1.0},
            "atmosphere": {"grain": 0.04, "vignette": 0.08, "dust": 0.04, "light_leak": False, "layers": [], "intensity": 0.05, "seed": index},
            "transition": {"template_id": "cut", "duration": 0.0},
            "design": {"image_treatment": "hold", "graphics_enabled": True},
            "sound_cues": [],
        })

    transition_rotation = ["smooth_left", "dissolve", "zoom_in", "squeeze_h",
                           "diagonal_tr", "circle_open", "wipe_up", "hblur",
                           "smooth_up", "fade_grays", "zoom_out", "slide_left",
                           "radial", "smooth_right"]
    transitions = [
        {"template_id": transition_rotation[i % len(transition_rotation)], "duration": 0.4}
        for i in range(len(scenes) - 1)
    ]

    palette = {"primary": "#f3f0e7", "accent": "#d8a84e", "secondary": "#b9c3c8", "danger": "#c94a3d", "background": "#101720", "surface": "#1c2530"}
    manifest = {
        "version": 1, "kind": "vidzeo-semantic-edit", "fps": 30, "width": 1920, "height": 1080,
        "duration": len(scenes) * 3.0, "duration_in_frames": len(scenes) * 90,
        "topic": "Professional Template Showcase", "category": "documentary",
        "render_quality": "standard", "aspect_ratio": "16:9",
        "theme": {"id": "editorial_showcase", "label": "Editorial Showcase", "palette": palette, "font_family": "Montserrat", "display_font": "Anton", "accent_font": "Roboto", "density": "balanced", "pace": "measured", "transition_policy": ["cut"], "graphic_density": 0.4, "sound_density": 0.2, "grade": {"style": "none", "strength": 0, "grain": 0, "vignette": 0}, "caption": {"color": "#fff", "background": "rgba(0,0,0,.8)", "position": "bottom", "weight": 650}, "safe_area": {"x": 0.06, "y": 0.06, "width": 0.88, "height": 0.88}},
        "editing_profile": {"palette": palette},
        "scenes": scenes,
        "audio": {"voiceover": {"src": "", "path": ""}, "music": {"src": "", "path": "", "volume": 0}, "effects": []},
        "transitions": transitions, "captions": {"enabled": False},
    }
    output = ROOT / "output" / "professional_template_showcase.mp4"
    metrics = render_semantic_documentary_ffmpeg(manifest, output, {"name": "Professional Showcase"})
    print(json.dumps({"output": str(output), "metrics": metrics}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
