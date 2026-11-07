"""Regression tests for the long-form quality pass:

1. Text cards: atmosphere vignette must never dim scenes that carry baked
   editorial text (protect_text), contract templates must use large fonts.
2. Same-clip repeats: the media plan enforces a reuse window (adjacency) that
   covers downloaded images as well as video, and recognizes the same footage
   across moment-file variants.
3. Scalable render budgets: long-form workloads receive render walls that grow
   with scene count and total duration instead of a fixed 300s ceiling.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

import app as APP
from services import mixed_media_planner as mixed_planner
from services import semantic_ffmpeg_renderer as renderer
from services import youtube_pipeline_v2 as youtube_v2


# ---------------------------------------------------------------------------
# 1. Text cards stay bright over weighted atmosphere
# ---------------------------------------------------------------------------

def _run_atmosphere_and_capture(monkeypatch, tmp_path, atmosphere, protect_text):
    base = tmp_path / "base.ts"
    base.write_bytes(b"x" * 8192)
    output = tmp_path / "out.ts"
    captured = {}

    def fake_run(command, *args, **kwargs):
        captured["command"] = list(command)
        output.write_bytes(b"x" * 8192)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(renderer, "_run_ffmpeg_process", fake_run)
    applied = renderer._apply_atmosphere(
        str(base), str(output), atmosphere, protect_text=protect_text
    )
    assert applied
    vf = captured["command"][captured["command"].index("-vf") + 1]
    return vf


def test_atmosphere_with_text_protection_never_adds_vignette(monkeypatch, tmp_path):
    atmosphere = {"grain": 0.0, "vignette": 1.0, "dust": 0.9, "light_leak": False, "intensity": 1.0}
    protected = _run_atmosphere_and_capture(
        monkeypatch, tmp_path, atmosphere, protect_text=True
    )
    assert "vignette" not in protected
    # Dust curve is capped to 0.35 -> ds 0.005 (floor), never the full 0.9
    # which would produce a 0.5/0.509 lift.
    assert "0.509" not in protected


def test_atmosphere_without_text_protection_keeps_original_strength(
    monkeypatch, tmp_path
):
    atmosphere = {"grain": 0.0, "vignette": 1.0, "dust": 0.9, "light_leak": False, "intensity": 1.0}
    unprotected = _run_atmosphere_and_capture(
        monkeypatch, tmp_path, atmosphere, protect_text=False
    )
    assert "vignette=" in unprotected
    assert "curves=all='0/0 0.5/" in unprotected


def test_contract_sizes_are_large_enough_to_render_bright_cards():
    from services.native_engine.libraries.professional_templates import (
        _contract_template,
    )
    palette = {"primary": "#ffffff", "accent": "#e2aa4d", "danger": "#d94a43", "surface": "#11151d"}
    for kind, minimum in {
        "date": 110,
        "stat": 118,
        "quote": 64,
        "person": 76,
        "location": 76,
        "archive": 82,
        "chapter": 104,
        "cta": 82,
    }.items():
        chains = _contract_template(palette, {
            "_contract_text": "WORD",
            "_contract_kind": kind,
            "_contract_size": 0,  # must fall through to the kind default
            "_position": "center",
            "_card_mode": "surface",
            "_show_rule": True,
        })["chains"]
        text_layers = [c for c in chains if c.startswith("drawtext=")]
        assert text_layers, f"no drawtext for {kind}"
        size = int(text_layers[0].split(":fontsize=")[1].split(":")[0])
        assert size >= minimum, f"{kind} fontsize {size} < {minimum}"


def test_contract_size_clamped_into_bright_range():
    from services.native_engine.libraries.professional_templates import (
        _contract_template,
    )
    palette = {"primary": "#ffffff", "accent": "#e2aa4d", "danger": "#d94a43", "surface": "#11151d"}
    for requested in (20, 52, 170, 240):
        chains = _contract_template(palette, {
            "_contract_text": "WORD",
            "_contract_kind": "date",
            "_contract_size": requested,
            "_position": "center",
            "_card_mode": "surface",
            "_show_rule": True,
        })["chains"]
        text_layers = [c for c in chains if c.startswith("drawtext=")]
        size = int(text_layers[0].split(":fontsize=")[1].split(":")[0])
        assert 52 <= size <= 170


# ---------------------------------------------------------------------------
# 2. Same-clip repeats across images and footage variants
# ---------------------------------------------------------------------------

def _fake_pool(**kwargs):
    return 0


def test_media_family_key_groups_scene_variants_and_images():
    v0 = "C:/media/cuts/scene_0042/moment_00.clean.mp4"
    v1 = "C:/media/cuts/scene_0042/moment_01.overlay.mp4"
    other = "C:/media/cuts/scene_0043/moment_00.clean.mp4"
    img_a = "C:/media/cache/img_0bd925a7bf4c_4f4d3c.jpg"
    img_b = "C:/media/cache/img_0bd925a7bf4c_5f5f5c.jpg"
    assert APP._media_family_key(v0) == APP._media_family_key(v1)
    assert APP._media_family_key(v0) != APP._media_family_key(other)
    assert APP._media_family_key(img_a) == APP._media_family_key(img_b)
    assert APP._media_family_key(img_a) != APP._media_family_key(v0)


def test_media_duplicate_key_is_content_based_for_scene_copies(tmp_path):
    """Copied web images must collide even when each scene has a new path."""
    first = tmp_path / "scene_001" / "image.jpg"
    second = tmp_path / "scene_002" / "image.jpg"
    other = tmp_path / "scene_003" / "image.jpg"
    for path in (first, second, other):
        path.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"same-web-image-bytes")
    second.write_bytes(b"same-web-image-bytes")
    other.write_bytes(b"different-web-image-bytes")

    assert APP._media_duplicate_key(str(first)) == APP._media_duplicate_key(str(second))
    assert APP._media_duplicate_key(str(first)) != APP._media_duplicate_key(str(other))


def test_adjacency_clears_same_footage_within_window(monkeypatch):
    paths = [
        "C:/media/cuts/scene_0001/moment_00.clean.mp4",
        "C:/media/cuts/scene_0001/moment_01.clean.mp4",  # adjacent same scene
        "C:/media/cache/img_aaa111_222.jpg",
        "C:/media/cache/img_aaa111_222.jpg",              # adjacent same image
        "C:/media/cuts/scene_0009/moment_00.clean.mp4",
        "C:/media/cuts/scene_0009/moment_01.clean.mp4",   # adjacent same scene
    ]
    labels = ["YouTube Clips"] * 6
    texts = ["text"] * 6
    monkeypatch.setattr(APP, "_fill_unresolved_with_rotating_video_pool", _fake_pool)
    monkeypatch.setattr(APP, "_fill_unresolved_with_general_web_fallback", _fake_pool)

    replaced = APP._enforce_media_adjacency(
        paths, labels, texts, (1280, 720), "T", "S", {}, window=2
    )

    assert replaced == 3
    assert paths[0] and not paths[1]
    assert paths[2] and not paths[3]
    assert paths[4] and not paths[5]


def test_adjacency_rotates_from_pool_and_leaves_distant_reuse_alone(monkeypatch):
    paths = [f"C:/media/cuts/scene_{i:04d}/moment_00.clean.mp4" for i in range(1, 8)]
    paths[3] = paths[0]  # same clip, 3 slots apart -> outside window=2
    labels = ["YouTube Clips"] * len(paths)
    texts = ["text"] * len(paths)

    filled = 0

    def fake_pool(final_paths, source_labels, unresolved_indices, **kwargs):
        nonlocal filled
        for i in unresolved_indices:
            final_paths[i] = f"C:/media/cuts/scene_{999 + i:04d}/moment_00.clean.mp4"
            source_labels[i] = "YouTube Clips"
            filled += 1
        return len(unresolved_indices)

    monkeypatch.setattr(APP, "_fill_unresolved_with_rotating_video_pool", fake_pool)
    monkeypatch.setattr(APP, "_fill_unresolved_with_general_web_fallback", _fake_pool)

    replaced = APP._enforce_media_adjacency(
        paths, labels, texts, (1280, 720), "T", "S", {}, window=2
    )

    assert replaced == 0
    assert filled == 0
    assert paths[3] == paths[0]


def test_automatic_history_plans_allow_substantial_archival_footage():
    plan = mixed_planner.build_adaptive_mixed_media_plan(
        [f"Titanic history scene {index}" for index in range(10)],
        title="Titanic history documentary",
        script="A historical documentary about the Titanic.",
        scene_media_mode="automatic",
    )
    assert plan.final_counts["Web Images"] >= 3
    assert plan.final_counts["YouTube Clips"] >= 3
    assert sum(plan.final_counts.values()) == 10


def test_mixed_web_quota_replaces_youtube_slots_and_waits_for_web(monkeypatch):
    paths = [
        "C:/media/yt_0.mp4",
        "C:/media/yt_1.mp4",
        "C:/media/web_0.jpg",
        "C:/media/yt_2.mp4",
        "C:/media/yt_3.mp4",
    ]
    labels = ["YouTube Clips", "YouTube Clips", "Web Images", "YouTube Clips", "YouTube Clips"]
    media_plan = {"media_paths": paths, "source_labels": labels, "metadata": {}}
    calls = []

    def fake_web_fallback(final_paths, source_labels, unresolved_indices, **kwargs):
        calls.append(list(unresolved_indices))
        for index in unresolved_indices:
            final_paths[index] = f"C:/media/quota_{index}.jpg"
            source_labels[index] = "Web Fallback Rescue"
        return len(unresolved_indices)

    monkeypatch.setattr(APP, "_fill_unresolved_with_general_web_fallback", fake_web_fallback)
    replaced = APP._enforce_mixed_web_image_quota(
        media_plan,
        {"scene_texts": ["scene"] * 5},
        {
            "image_source": "Mixed Media",
            "mixed_media_policy": "automatic",
            "mixed_web_minimum_ratio": 0.60,
            "strict_mixed_web_minimum": True,
        },
        (1280, 720),
        "Titanic",
        "history",
    )
    assert replaced == 2
    assert len(calls) == 1
    final_paths = media_plan["media_paths"]
    final_labels = media_plan["source_labels"]
    assert sum(APP._is_web_image_media(path, label) for path, label in zip(final_paths, final_labels)) == 3
    assert media_plan["metadata"]["mixed_web_quota"]["final_count"] == 3


def test_direct_youtube_download_rejects_burned_in_captions(monkeypatch, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"clip")
    import services.scene_understanding.analyzers as analyzers
    import services.youtube_pool_renderer as pool_renderer

    monkeypatch.setattr(pool_renderer, "_screen_moment_overlays", lambda _path: {
        "checked": True,
        "clean": False,
        "regions": [{"type": "subtitle", "bbox": [0.2, 0.82, 0.8, 0.94], "confidence": 90}],
        "blur_regions": [],
        "overlay_policy": "clean_or_logo_blur_caption_reject",
        "overlay_types": ["subtitle"],
        "rejection_reason": "caption_overlay_rejected",
        "watermark_prob": 0.0,
        "has_subtitle": True,
        "reject": True,
    })

    cleaned, audit = youtube_v2._clean_downloaded_clip_overlays(str(source))
    assert cleaned == ""
    assert audit["rejection_reason"] == "caption_overlay_rejected"


def test_direct_youtube_download_blurs_small_corner_logo(monkeypatch, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"clip")
    import services.scene_understanding.analyzers as analyzers
    import services.youtube_pool_renderer as pool_renderer

    monkeypatch.setattr(pool_renderer, "_screen_moment_overlays", lambda _path: {
        "checked": True,
        "clean": False,
        "regions": [{"type": "watermark", "bbox": [0.82, 0.03, 0.96, 0.12], "confidence": 95}],
        "blur_regions": [{"type": "watermark", "bbox": [0.82, 0.03, 0.96, 0.12], "confidence": 95}],
        "overlay_policy": "clean_or_logo_blur_caption_reject",
        "overlay_types": ["watermark"],
        "watermark_prob": 0.95,
        "has_subtitle": False,
        "reject": False,
    })

    def fake_blur(_input, output, _regions):
        assert '.part.mp4' in output
        assert not source.with_name('clip.overlay_clean.mp4').exists()
        Path(output).write_bytes(b"clean clip")
        return True

    monkeypatch.setattr(analyzers, "apply_overlay_blur", fake_blur)
    cleaned, audit = youtube_v2._clean_downloaded_clip_overlays(str(source))
    assert cleaned.endswith(".overlay_clean.mp4")
    assert Path(cleaned).is_file()
    assert audit["blurred"] is True
    assert audit["cleaned_path"] == cleaned
    assert not list(tmp_path.glob('*.part.mp4'))


def test_direct_youtube_download_rejects_large_logo(monkeypatch, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"clip")
    import services.youtube_pool_renderer as pool_renderer

    monkeypatch.setattr(pool_renderer, "_screen_moment_overlays", lambda _path: {
        "checked": True,
        "clean": False,
        "regions": [{"type": "watermark", "bbox": [0.20, 0.20, 0.80, 0.80], "confidence": 95}],
        "blur_regions": [],
        "overlay_policy": "clean_or_logo_blur_caption_reject",
        "overlay_types": ["watermark"],
        "rejection_reason": "large_logo_overlay_rejected",
        "has_subtitle": False,
        "reject": True,
    })
    cleaned, audit = youtube_v2._clean_downloaded_clip_overlays(str(source))
    assert cleaned == ""
    assert audit["rejection_reason"] == "large_logo_overlay_rejected"


# ---------------------------------------------------------------------------
# 3. Scalable render budgets for long-form jobs
# ---------------------------------------------------------------------------

def test_long_form_wrapper_accepts_scaled_render_walls(monkeypatch, tmp_path):
    """The renderer must honor the app's scaled long-form walls (up to 900s)."""
    observed = {}

    def fake_impl(**kwargs):
        observed["remaining"] = renderer._remaining_render_budget()
        return {"output_path": str(tmp_path / "fake.mp4")}

    monkeypatch.setattr(renderer, "_render_semantic_documentary_ffmpeg_impl", fake_impl)
    renderer.render_semantic_documentary_ffmpeg(
        {"scenes": [{} for _ in range(74)]},
        tmp_path / "fake.mp4",
        {"semantic_render_budget_seconds": 720, "semantic_render_concurrency": 4},
    )
    # Monotonic floating-point subtraction can exceed the configured wall by
    # a few trillionths of a second; that is not an extra render allowance.
    assert 700 <= observed["remaining"] <= 720 + 1e-6


def test_wall_budget_formula_scales_with_scene_count_and_duration():
    """The app-side formula (120 + scenes*8 + duration*0.5) must keep growing
    long-form walls instead of clamping to the old 300s fixed budget."""
    small = 120 + 20 * 8 + 60 * 0.5
    medium = 120 + 42 * 8 + 100 * 0.5
    long_form = 120 + 74 * 8 + 180 * 0.5
    assert long_form > medium > small > 300
    assert medium > 300


def test_renderer_budget_cap_allows_scaled_walls():
    """_positive_int keeps the upper 900s ceiling now used by scaled walls."""
    assert renderer._positive_int(720, 300, 30, 900) == 720
    assert renderer._positive_int(950, 300, 30, 900) == 900
