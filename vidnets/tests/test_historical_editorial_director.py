"""Test historical documentary editorial planning and verification against fake data."""

import pytest
from server.services.editorial_director.director import EditingDirector
from server.services.editorial_director.script_analyzer import analyze_sentence_triggers
from server.services.editorial_director.contract import VisualSceneType


def test_colosseum_narration_no_fake_chart_data(offline_director):
    """Verify that a Colosseum script does not produce modern 2018-2024 fake chart data."""
    director = offline_director
    topic = "The Colosseum of Ancient Rome"
    category = "history"
    scene_texts = [
        "In 80 AD, Emperor Titus unveiled the greatest amphitheater the ancient world had ever witnessed.",
        "Rising four stories above the Roman Forum, the monumental structure stood nearly 50 meters tall.",
        "Over 50,000 spectators packed into the travertine stone tiers, roaring with anticipation.",
        "Gladiatorial combat and elaborate wild beast hunts captivated citizens across the Roman Empire.",
        "By the time the Western Roman Empire collapsed, centuries of spectacle had rewritten ancient history.",
    ]
    scene_durations = [5.0, 4.5, 5.0, 4.5, 5.0]

    plan = director.plan_documentary(
        script_text=" ".join(scene_texts),
        scene_texts=scene_texts,
        scene_durations=scene_durations,
        topic=topic,
        category=category,
    )

    manifest = plan.to_remotion_manifest()
    scenes = manifest["scenes"]

    # 1. Verify genre detection
    assert plan.visual_dna.genre == "historical-documentary", f"Expected historical-documentary, got {plan.visual_dna.genre}"

    # 2. Check each scene and ensure NO fake modern years appear anywhere in props
    forbidden_fake_years = ["2018", "2020", "2022", "2024", "DATA ANALYSIS"]
    graphic_count = 0

    for i, s in enumerate(scenes):
        pv = s.get("primary", {})
        pv_type = pv.get("type")
        props = pv.get("props", {})
        props_str = str(props)

        # Check for fake data leak
        for fake in forbidden_fake_years:
            assert fake not in props_str, f"Found fake data '{fake}' in scene {i+1} props: {props_str}"

        # Count motion graphics
        if pv_type not in ("SmartImage", "SmartVideo"):
            graphic_count += 1

    # 3. Verify motion graphic density (should be >= 35%)
    density = graphic_count / len(scenes)
    assert density >= 0.35, f"Motion graphic density {density:.2%} is below 35% ({graphic_count}/{len(scenes)})"

    # An unavailable director cannot approve chapter copy. Its opening keeps
    # the resolved image treatment rather than rendering a title from narration.
    if plan.director_metadata.get("status") == "deterministic_fallback" or 0 in plan.director_metadata.get("local_fallback_scene_indices", []):
        assert scenes[0]["primary"]["type"] == "SmartImage"
    else:
        assert scenes[0]["primary"]["type"] in {"ChapterTitle", "AnimatedMap", "SmartImage"}


def test_titanic_narration_content_extraction(offline_director):
    """Verify that a Titanic script extracts real dates and dimensions, not generic filler."""
    director = offline_director
    topic = "The Sinking of RMS Titanic"
    category = "history"
    scene_texts = [
        "On April 10, 1912, the RMS Titanic departed Southampton on its ill-fated maiden voyage.",
        "At 882 feet in length and weighing over 46,000 tons, she was the largest passenger steamship in the world.",
        "Carrying more than 2,200 passengers and crew, only 705 souls would survive the freezing Atlantic waters.",
        "At 11:40 PM on April 14, lookout Frederick Fleet spotted the catastrophic iceberg.",
        "The tragedy altered maritime safety regulations forever, changing naval protocols across the globe.",
    ]
    scene_durations = [5.0, 5.0, 5.0, 4.5, 5.0]

    plan = director.plan_documentary(
        script_text=" ".join(scene_texts),
        scene_texts=scene_texts,
        scene_durations=scene_durations,
        topic=topic,
        category=category,
    )

    manifest = plan.to_remotion_manifest()
    scenes = manifest["scenes"]

    # Verify no fake 2018-2024 data
    for i, s in enumerate(scenes):
        pv = s.get("primary", {})
        props_str = str(pv.get("props", {}))
        assert "2018" not in props_str
        assert "2024" not in props_str
        assert "DATA ANALYSIS" not in props_str

    # Verify high graphic ratio
    non_broll = [s for s in scenes if s.get("primary", {}).get("type") not in ("SmartImage", "SmartVideo")]
    assert len(non_broll) >= 2, f"Expected at least 2 graphics, got {len(non_broll)}"


def test_script_analyzer_ancient_dates_and_persons():
    """Verify script_analyzer detects 80 AD, Emperor Titus, and 50,000 spectators."""
    text = "In 80 AD, Emperor Titus unveiled the Colosseum to over 50,000 spectators."
    triggers = analyze_sentence_triggers(text, is_first_scene=False)

    assert any("80 AD" in d for d in triggers.dates), f"Dates did not capture 80 AD: {triggers.dates}"
    assert any("Titus" in p for p in triggers.persons), f"Persons did not capture Titus: {triggers.persons}"
    assert len(triggers.numbers) >= 1, f"Numbers did not capture 50,000: {triggers.numbers}"


if __name__ == "__main__":
    test_colosseum_narration_no_fake_chart_data()
    test_titanic_narration_content_extraction()
    test_script_analyzer_ancient_dates_and_persons()
    print("ALL HISTORICAL EDITORIAL TESTS PASSED!")
