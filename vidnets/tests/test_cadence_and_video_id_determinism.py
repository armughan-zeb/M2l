"""Test suite verifying fast-paced 2-to-3 scene editorial cadence,
facts & figures prioritization (StatCard/Chart/TextCard), and video-ID determinism.
"""

import pytest
from server.services.semantic_edit_engine import build_semantic_edit_manifest, stable_seed


def test_cadence_window_pacing_and_fact_prioritization():
    """Verify that graphics are placed every 2 scenes, and facts/figures get priority."""
    texts = [
        "In 1962, the world stood at the brink of nuclear catastrophe.",       # Scene 0: Date -> Graphic
        "Military radar units tracked unusual movements across the horizon.",     # Scene 1: Clean visual
        "Declassified memos revealed an estimated $42 billion defense deficit.", # Scene 2: Money ($42B) -> Graphic
        "Naval fleets formed a tight perimeter around international waters.",    # Scene 3: Clean visual
        "Over 85 percent of interceptors were placed on highest alert.",         # Scene 4: Percentage (85%) -> Graphic
        "Diplomats met behind closed doors to negotiate a peaceful resolution.", # Scene 5: Clean visual
        "By October 28, both superpowers agreed to dismantle the bases.",        # Scene 6: Date -> Graphic
        "Tension slowly eased as reconnaissance flights confirmed the withdrawal." # Scene 7: Clean visual
    ]
    durations = [4.0] * len(texts)
    media = [{"src": f"scene_{i}.jpg", "type": "image"} for i in range(len(texts))]

    manifest = build_semantic_edit_manifest(
        scene_texts=texts,
        scene_durations=durations,
        media=media,
        topic="Cuban Missile Crisis",
        category="history",
        config={
            "variation_seed": 42,
        },
    )

    scenes = manifest["scenes"]
    assert len(scenes) == 8

    # Verify that Scene 0 (Cold Open) and scenes with heavy facts (Scene 2 with $42B, Scene 4 with 85%, Scene 6 with Oct 28)
    # are chosen for graphic treatments
    graphic_scene_indices = [
        i for i, s in enumerate(scenes)
        if s.get("primary_visual", {}).get("type") not in {"SmartImage", "SmartVideo"}
        or s.get("graphic", {}).get("template_id") != "full-bleed"
    ]

    # Fast-paced cadence: 4 graphic scenes out of 8 scenes (50%)
    assert len(graphic_scene_indices) == 4, f"Expected 4 graphic scenes, got {len(graphic_scene_indices)}"
    assert 0 in graphic_scene_indices, "Scene 0 should have hook/graphic"
    assert 2 in graphic_scene_indices, "Scene 2 ($42B) should have graphic"
    assert 4 in graphic_scene_indices, "Scene 4 (85%) should have graphic"
    assert 6 in graphic_scene_indices, "Scene 6 (October 28) should have graphic"

    # Verify intervening scenes (1, 3, 5, 7) are clean cinematic full-bleed cuts
    for clean_idx in [1, 3, 5, 7]:
        clean_scene = scenes[clean_idx]
        assert clean_scene.get("graphic", {}).get("template_id") == "full-bleed"
        assert clean_scene.get("primary_visual", {}).get("type") in {"SmartImage", "SmartVideo"}

    # Verify that Scene 2 (money/memo) triggered an editorial graphic treatment
    pv2 = scenes[2].get("primary_visual", {})
    assert pv2.get("type") in {"StatCard", "TextCard", "ChapterTitle", "Diagram", "DocumentHighlight"}

    # Verify that Scene 4 (percentage) triggered a stat/chart treatment
    pv4 = scenes[4].get("primary_visual", {})
    assert pv4.get("type") in {"StatCard", "Chart", "TextCard", "Timeline"}


def test_video_id_determinism_and_uniqueness():
    """Verify that identical video IDs produce 100% identical outputs,
    while different video IDs produce distinctly styled and paced cuts.
    """
    texts = [
        "Silicon Valley startups raised unprecedented venture funding in 2021.",
        "Over 600 unicorn companies emerged across global tech markets.",
        "However, interest rate hikes triggered an unexpected cooling cycle.",
        "Founders pivoted swiftly towards sustainable free cash flow margins.",
    ]
    durations = [4.0] * 4
    media = [{"src": f"tech_{i}.jpg", "type": "image"} for i in range(4)]

    # Video A run 1
    seed_a = stable_seed("job-alpha-001")
    manifest_a1 = build_semantic_edit_manifest(
        scene_texts=texts,
        scene_durations=durations,
        media=media,
        topic="Tech Startup Boom",
        config={"variation_seed": seed_a},
    )

    # Video A run 2 (must be 100% identical)
    manifest_a2 = build_semantic_edit_manifest(
        scene_texts=texts,
        scene_durations=durations,
        media=media,
        topic="Tech Startup Boom",
        config={"variation_seed": seed_a},
    )

    # Video B run (distinct video ID)
    seed_b = stable_seed("job-beta-999")
    manifest_b = build_semantic_edit_manifest(
        scene_texts=texts,
        scene_durations=durations,
        media=media,
        topic="Tech Startup Boom",
        config={"variation_seed": seed_b},
    )

    # 1. Determinism check: A1 and A2 must have identical motions, palettes, and templates
    for s1, s2 in zip(manifest_a1["scenes"], manifest_a2["scenes"]):
        assert s1["graphic"]["template_id"] == s2["graphic"]["template_id"]
        assert s1["camera"]["motion"] == s2["camera"]["motion"]
        assert s1["primary_visual"]["variant"] == s2["primary_visual"]["variant"]
        assert s1["palette"] == s2["palette"]

    # 2. Uniqueness check: A and B must differ in camera motions, templates, or visual variants
    a_motions = [s["camera"]["motion"] for s in manifest_a1["scenes"]]
    b_motions = [s["camera"]["motion"] for s in manifest_b["scenes"]]
    a_variants = [s["primary_visual"].get("variant") for s in manifest_a1["scenes"]]
    b_variants = [s["primary_visual"].get("variant") for s in manifest_b["scenes"]]

    differences = (a_motions != b_motions) or (a_variants != b_variants)
    assert differences, f"Expected variations between seed_a ({seed_a}) and seed_b ({seed_b})"
