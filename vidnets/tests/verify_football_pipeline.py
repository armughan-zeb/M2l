"""End-to-end test of the rebuilt section-aware pipeline."""
import sys, os
sys.path.insert(0, 'server')

# ── Test 1: Section detection on the actual football script ──

def test_football_section_detection():
    from services.semantic_query_agent import detect_list_sections

    scene_texts = [
        "At number five is Erling Haaland. The Norwegian goal machine has rewritten scoring records wherever he's played.",
        "With incredible strength, blistering pace, and deadly finishing, Haaland is one of the most feared strikers in world football.",
        "At number four is Jude Bellingham. Still incredibly young, Bellingham has already become one of the world's best midfielders.",
        "He combines vision, technical ability, defensive work rate, and leadership beyond his years, making him the complete modern midfielder.",
        "Taking the third spot is Kylian Mbappe. Few players in football history possess his combination of speed, skill, and finishing.",
        "Whether it's a World Cup final or a Champions League night, Mbappe consistently delivers on the biggest stages and remains one of the most unstoppable attackers in the game.",
        "At number two is Cristiano Ronaldo. Even after two decades at the highest level, Ronaldo continues to break records and score goals.",
        "With over 900 career goals, multiple Champions League titles, league championships across different countries, and countless individual awards, his legacy as one of football's greatest icons is unquestionable.",
        "And at number one is Lionel Messi. From winning countless league titles and Champions League trophies to finally lifting the FIFA World Cup, Messi has completed football's greatest collection of achievements.",
        "His vision, dribbling, passing, finishing, and ability to decide matches have made him a player unlike any other. For many fans, he is simply the greatest footballer of all time.",
    ]

    sections = detect_list_sections(scene_texts, "top five best footballers")

    expected = [
        "Erling Haaland", "Erling Haaland",
        "Jude Bellingham", "Jude Bellingham",
        "Kylian Mbappe", "Kylian Mbappe",
        "Cristiano Ronaldo", "Cristiano Ronaldo",
        "Lionel Messi", "Lionel Messi",
    ]

    failures = 0
    for i, (exp, sec) in enumerate(zip(expected, sections)):
        got = sec.get("container_subject", "")
        if got != exp:
            print(f"  FAIL Scene {i}: expected='{exp}' got='{got}'")
            failures += 1

    distinct = list(dict.fromkeys(s["container_subject"] for s in sections if s["container_subject"]))
    print(f"\n  Distinct sections: {distinct}")
    assert len(distinct) == 5, f"Expected 5 distinct sections, got {len(distinct)}"
    assert failures == 0, f"{failures} scenes had wrong section subjects"
    print("  PASS")
    return True


# ── Test 2: Mechanical fallback generates correct per-section queries ──

def test_mechanical_query_output():
    from services.semantic_query_agent import detect_list_sections
    from app import _run_mechanical_query_fallback, _repair_concept_topic_query_rows

    scene_texts = [
        "Number 5: Erling Haaland is the Norwegian goal machine.",
        "His incredible strength makes him feared.",
        "Number 4: Jude Bellingham is a complete midfielder.",
        "Young star with vision beyond his years.",
    ]
    title = "top five best footballers current era"

    rows = _run_mechanical_query_fallback("test", title, scene_texts, silent=True)
    repaired = _repair_concept_topic_query_rows(rows, title, scene_texts)

    print(f"\n  Generated {len(repaired)} query rows:")
    for i, r in enumerate(repaired):
        subj = r.get("section_subject", "")
        yt = r.get("youtube_query", "")
        boundary = r.get("container_boundary", False)
        print(f"  Scene {i}: section='{subj}' boundary={boundary} yt='{yt}'")

    # Verify section subjects
    assert repaired[0]["section_subject"] == "Erling Haaland"
    assert repaired[1]["section_subject"] == "Erling Haaland"
    assert repaired[2]["section_subject"] == "Jude Bellingham"
    assert repaired[3]["section_subject"] == "Jude Bellingham"

    # Queries must contain the section subject
    assert "erling" in repaired[0]["youtube_query"].lower()
    assert "erling" in repaired[1]["youtube_query"].lower()
    assert "jude" in repaired[2]["youtube_query"].lower()
    assert "jude" in repaired[3]["youtube_query"].lower()

    # Container boundaries must be set at section transitions
    assert repaired[0]["container_boundary"] == True, "Scene 0 (first marker) should have boundary"
    assert repaired[2]["container_boundary"] == True, "Scene 2 (new section) should have boundary"

    # Domain anchor must NOT be in the query (for section scenes)
    for r in repaired:
        yt = r.get("youtube_query", "").lower()
        # "football" or "players" shouldn't be first word for section scenes
        words = yt.split()
        if words:
            assert words[0] not in ("football", "era", "current"), f"Domain anchor should not be first word in: {yt}"

    print("  PASS")
    return True


# ── Test 3: Web fallback section propagation ──

def test_web_fallback_section_propagation():
    from app import _section_subjects_from_config

    config = {
        "_yt_scene_query_overrides": [
            {"section_subject": "Cristiano Ronaldo"},
            {"section_subject": "Cristiano Ronaldo"},
            {"section_subject": "Kylian Mbappe"},
            {"section_subject": "Kylian Mbappe"},
        ]
    }
    subjects = _section_subjects_from_config(config, 4)
    print(f"\n  Extracted subjects: {subjects}")
    assert subjects == ["Cristiano Ronaldo", "Cristiano Ronaldo", "Kylian Mbappe", "Kylian Mbappe"]
    print("  PASS")
    return True


# ── Test 4: _subject_is_weak doesn't flag real subjects ──

def test_subject_weak_guard():
    from services.youtube_clips_service import _subject_is_weak

    real_subjects = ["Erling Haaland", "Kylian", "Jude", "Mbappe", "Apple", "Banana", "Cristiano Ronaldo"]
    weak_words = ["video", "footage"]

    print(f"\n  Real subjects (should NOT be weak):")
    for s in real_subjects:
        weak = _subject_is_weak(s)
        print(f"    '{s}': weak={weak}")
        assert not weak, f"'{s}' should NOT be classified as weak"

    print(f"  Search terms check:")
    for s in weak_words:
        weak = _subject_is_weak(s)
        print(f"    '{s}': weak={weak}")

    print("  PASS")
    return True


# ── Test 5: Domain noise stripping ──

def test_domain_noise_stripping():
    from app import _repair_concept_topic_query_rows

    scene_texts = [
        "Number 1: Cristiano Ronaldo is the GOAT.",
        "Number 2: Lionel Messi is magic.",
    ]
    rows = [
        {"youtube_query": "Cristiano Ronaldo football highlights real footage", "section_subject": ""},
        {"youtube_query": "Lionel Messi football skills real footage", "section_subject": ""},
    ]
    repaired = _repair_concept_topic_query_rows(rows, "football Cristiano Ronaldo", scene_texts)

    print(f"\n  Repaired queries:")
    for i, r in enumerate(repaired):
        yt = r.get("youtube_query", "")
        section = r.get("section_subject", "")
        print(f"    Scene {i}: section='{section}' yt='{yt}'")

    # Domain noise "football" should be stripped from section queries
    q0 = repaired[0].get("youtube_query", "").lower()
    q1 = repaired[1].get("youtube_query", "").lower()
    assert q0.startswith("cristiano"), f"Scene 0 query should start with cristiano: {q0}"
    assert q1.startswith("lionel"), f"Scene 1 query should start with lionel: {q1}"

    print("  PASS")
    return True


# ── Test 6: Full football script end-to-end ──

def test_full_football_script():
    from services.semantic_query_agent import detect_list_sections
    from app import _run_mechanical_query_fallback, _repair_concept_topic_query_rows

    scene_texts = [
        "At number five is Erling Haaland. The Norwegian goal machine.",
        "With incredible strength, blistering pace, and deadly finishing, Haaland is one of the most feared strikers in world football.",
        "At number four is Jude Bellingham. Still incredibly young, Bellingham has already become one of the world's best midfielders.",
        "He combines vision, technical ability, defensive work rate, and leadership beyond his years.",
        "Taking the third spot is Kylian Mbappe. Few players in football history possess his combination of speed, skill, and finishing.",
        "Whether it's a World Cup final or a Champions League night, Mbappe consistently delivers on the biggest stages.",
        "At number two is Cristiano Ronaldo. Even after two decades at the highest level, Ronaldo continues to break records and score goals.",
        "With over 900 career goals, multiple Champions League titles, league championships across different countries, and countless individual awards.",
        "And at number one is Lionel Messi. From winning countless league titles and Champions League trophies to finally lifting the FIFA World Cup.",
        "His vision, dribbling, passing, finishing, and ability to decide matches have made him a player unlike any other.",
    ]
    title = "top five best footballers current era"

    rows = _run_mechanical_query_fallback("test", title, scene_texts, silent=True)
    repaired = _repair_concept_topic_query_rows(rows, title, scene_texts)

    print(f"\n  Full pipeline output ({len(repaired)} rows):")
    for i, r in enumerate(repaired):
        subj = r.get("section_subject", "")
        boundary = r.get("container_boundary", False)
        yt = r.get("youtube_query", "")
        print(f"  Scene {i}: section='{subj:20s}' boundary={str(boundary):5s} yt='{yt}'")

    # Verify all 5 distinct sections
    sections = detect_list_sections(scene_texts, title)
    distinct = list(dict.fromkeys(s["container_subject"] for s in sections if s["container_subject"]))
    assert len(distinct) == 5, f"Expected 5 sections, got {len(distinct)}: {distinct}"

    # Verify NO cross-contamination
    for i, r in enumerate(repaired):
        yt = r.get("youtube_query", "").lower()
        section = r.get("section_subject", "").lower()
        if section and "erling" in section:
            assert "kylian" not in yt, f"Scene {i}: Erling section has Kylian in query: {yt}"
            assert "jude" not in yt, f"Scene {i}: Erling section has Jude in query: {yt}"
        elif section and "kylian" in section:
            assert "erling" not in yt, f"Scene {i}: Kylian section has Erling in query: {yt}"
            assert "cristiano" not in yt, f"Scene {i}: Kylian section has Cristiano in query: {yt}"
        elif section and "jude" in section:
            assert "erling" not in yt, f"Scene {i}: Jude section has Erling in query: {yt}"
            assert "mess" not in yt, f"Scene {i}: Jude section has Messi in query: {yt}"

    print("  PASS: No cross-contamination detected")
    return True


# ── Run all tests ──

if __name__ == "__main__":
    print("=" * 60)
    print("END-TO-END PIPELINE VERIFICATION")
    print("=" * 60)

    tests = [
        ("Football section detection", test_football_section_detection),
        ("Mechanical query output", test_mechanical_query_output),
        ("Web fallback section propagation", test_web_fallback_section_propagation),
        ("Subject-is-weak guard", test_subject_weak_guard),
        ("Domain noise stripping", test_domain_noise_stripping),
        ("Full football script end-to-end", test_full_football_script),
    ]

    passed = 0
    failed = 0
    for name, test_fn in tests:
        print(f"\n--- {name} ---")
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"RESULTS: {passed} passed, {failed} failed out of {len(tests)}")
    print(f"{'=' * 60}")
