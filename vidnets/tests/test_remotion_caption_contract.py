from pathlib import Path
import json
import re

from server.services.native_render_policy import normalize_remotion_caption_settings


ROOT = Path(__file__).resolve().parents[1]


def test_caption_settings_are_bounded_to_the_bundled_remotion_options():
    assert normalize_remotion_caption_settings({
        "enabled": True,
        "style": "impact",
        "font": "Archivo Black",
        "position": "auto",
        "mode": "karaoke",
    }) == {
        "enabled": True,
        "style": "impact",
        "font": "Archivo Black",
        "position": "auto",
        "mode": "karaoke",
        "animation": "Pop",
    }
    safe = normalize_remotion_caption_settings({
        "enabled": True, "style": "bad-style", "font": "remote-font",
        "position": "outside", "mode": "unbounded",
    })
    assert safe["style"] == "documentary"
    assert safe["font"] == "DM Sans"
    assert safe["position"] == "auto"
    assert safe["mode"] == "word"


def test_remotion_is_the_only_caption_burn_path_for_generated_masters():
    app_source = (ROOT / "server/app.py").read_text(encoding="utf-8")
    composition = (ROOT / "src/remotion/DocumentaryComposition.jsx").read_text(encoding="utf-8")
    ffmpeg_renderer = (ROOT / "server/services/semantic_ffmpeg_renderer.py").read_text(encoding="utf-8")
    # The helper remains for old integrations, but generated and resumed
    # documentary masters must not call it.
    assert app_source.count("_burn_subtitles_into_master(") == 1
    assert '"captions_enabled": bool(job_config.get(\'captions_enabled\', False))' in app_source
    assert "manifest.captions || scene.caption_settings" in composition
    assert "scene.caption_words" in composition
    assert "scene.editor_track_visibility?.captions === false) return null" in composition
    assert "|| !scene.editor_track_visibility" not in composition
    assert "graphicIsTextBearing" in composition
    assert "if (hideDuringGraphics && activeTextOverlay) return null;" in composition
    assert "scene.media_composition?.caption_region" in composition
    assert "const useSafeRegion = placement === 'auto' && safeRegion" in composition
    assert "background: emphasize && styleName === 'impact' ? accent : 'transparent'" in composition
    assert "width: 'fit-content'" in composition
    assert "rgba(4,9,15,.84)" not in composition
    assert ffmpeg_renderer.count("_timed_caption_chains(") == 1


def test_approved_media_focus_template_reaches_remotion_without_placeholder_copy():
    from server.services.editorial_director.production_contract import _grounded_template_props

    approved = {
        "narration": "Against the odds, it became a narrow escape for the crew.",
        "editorial_contract": {
            "version": 4, "approved": True, "source": "llm",
            "display_text": "NARROW ESCAPE", "source_span": "a narrow escape",
        },
    }
    assert _grounded_template_props("media-focus-card", {"title": "NARROW ESCAPE"}, approved)
    assert not _grounded_template_props("media-focus-card", {"title": "KEY FOCUS"}, {})

    composition = (ROOT / "src/remotion/DocumentaryComposition.jsx").read_text(encoding="utf-8")
    template_source = (ROOT / "src/remotion/templates-enhanced.jsx").read_text(encoding="utf-8")
    assert "'media-focus-card': 'MediaFocusCard'" in composition
    assert '<GraphicTemplate id="media-focus"' in composition
    assert "const title = txt(props.title || props.headline || '');" in template_source
    assert "if (!title) return null;" in template_source


def test_every_approved_builtin_template_has_a_live_remotion_route():
    catalog = json.loads((ROOT / "resources/template-library/builtin/manifest.json").read_text(encoding="utf-8"))
    approved_ids = {item["id"] for item in catalog.get("templates", []) if item.get("status") == "approved"}
    composition = (ROOT / "src/remotion/DocumentaryComposition.jsx").read_text(encoding="utf-8")
    mapping = composition.split("const DIRECTOR_GRAPHIC_TYPES = {", 1)[1].split("\n};", 1)[0]
    remotion_ids = set(re.findall(r"['\"]([^'\"]+)['\"]\s*:", mapping))
    copy_mapping = composition.split("const DIRECTOR_COPY_TEMPLATE_TYPES = {", 1)[1].split("\n};", 1)[0]
    copy_ids = set(re.findall(r"['\"]([^'\"]+)['\"]\s*:", copy_mapping))
    from server.services.editorial_director.production_contract import TEMPLATE_PRIMARY_TYPES

    # Every approved catalog id has a validated server contract and a real
    # Remotion route. Copy treatments are dedicated type components; full-bleed
    # is the media base, and data/evidence templates use specialist graphics.
    assert approved_ids <= set(TEMPLATE_PRIMARY_TYPES)
    assert approved_ids <= remotion_ids | copy_ids | {"full-bleed"}
    assert "'headline-center': { component: 'KineticText', variant: 'impact_slab' }" in copy_mapping
    assert "'keyword-reveal': { component: 'KineticText', variant: 'word_by_word_punch' }" in copy_mapping
    assert "'chapter-opener': { component: 'ChapterTitle'" in copy_mapping
    assert "? renderSceneSystem({" in composition
    assert "scene.text?.panel_style !== 'none'" in composition


def test_media_copy_template_handoff_preserves_only_validated_style_choices():
    from server.services.editorial_director.production_contract import _sync_director_graphic_to_primary

    scene = {
        "director_treatment_enabled": True,
        "template_id": "keyword-reveal",
        "graphic": {
            "template_id": "keyword-reveal",
            "variant": "impact_slab",
            "props": {
                "position": "top_left", "panel_style": "impact_slab",
                "accent_color": "#D92631", "text_color": "#FFFFFF",
                "panel_background": "url(https://invalid.example/panel.png)",
            },
        },
        "text": {"display_text": "TWENTY-SIX SECONDS"},
    }
    _sync_director_graphic_to_primary(scene, {
        "type": "SmartVideo", "props": {"variant": "slow_push_in"},
    })

    assert scene["template_id"] == "full-bleed"
    assert scene["text"]["template_id"] == "keyword-reveal"
    assert scene["text"]["variant"] == "impact_slab"
    assert scene["text"]["position"] == "top-left"
    assert scene["text"]["panel_style"] == "impact_slab"
    assert scene["text"]["accent_color"] == "#D92631"
    assert scene["text"]["text_color"] == "#FFFFFF"
    assert "panel_background" not in scene["text"]

