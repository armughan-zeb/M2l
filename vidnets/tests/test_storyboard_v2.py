"""Focused contract tests for the canonical Storyboard V2 foundation."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services.storyboard_v2 import (  # noqa: E402
    ContractValidationError,
    GenerationRequestV2,
    MediaCandidateV1,
    StoryboardStore,
    StoryboardV2,
    align_script_plan_to_words,
    align_script_tokens,
    build_script_plan,
    normalize_script_with_map,
    tokenize_source,
)
from services.storyboard_v2.scene_builder import _split_span_at_words  # noqa: E402


def _assert_exact_coverage(plan) -> None:
    leaves = plan.leaf_containers
    assert leaves[0].char_start == 0
    assert leaves[-1].char_end == len(plan.source_script)
    assert all(left.char_end == right.char_start for left, right in zip(leaves, leaves[1:]))
    assert "".join(item.text for item in leaves) == plan.source_script

    assert plan.scenes[0].char_start == 0
    assert plan.scenes[-1].char_end == len(plan.source_script)
    assert all(
        left.char_end == right.char_start for left, right in zip(plan.scenes, plan.scenes[1:])
    )
    assert "".join(item.text for item in plan.scenes) == plan.source_script


def _word_rows(script: str, *, step: float = 0.28):
    rows = []
    clock = 0.0
    for token in tokenize_source(script):
        rows.append({"word": token.text, "start": clock, "end": clock + step * 0.8})
        clock += step
    return rows, clock


def test_generation_request_keeps_content_title_and_output_name_separate():
    request = GenerationRequestV2.from_mapping(
        {
            "script": "A sufficiently complete narration script.",
            "title": "Top Ten Extreme Jobs",
            "name": "client-export-august",
            "scene_mode": "Auto",
        }
    )

    assert request.content_title == "Top Ten Extreme Jobs"
    assert request.output_name == "client-export-august"
    assert request.scene_mode == "adaptive"
    legacy = request.to_mapping()
    assert legacy["title"] == "Top Ten Extreme Jobs"
    assert legacy["name"] == "client-export-august"


def test_scene_boundaries_keep_compound_facts_together():
    script = "The loot was worth 100 million dollars after the vault was opened."
    tokens = tokenize_source(script)
    spans = _split_span_at_words(0, len(script), tokens, max_words=5)
    parts = [script[start:end] for start, end in spans]

    assert all(not part.rstrip().endswith("100") for part in parts)
    assert any("100 million" in part.lower() for part in parts)


def test_generation_request_accepts_app_qwen_aliases_and_string_booleans():
    request = GenerationRequestV2.from_mapping(
        {
            "script": "A sufficiently complete narration script.",
            "qwen_vision_model": "qwen/test-primary",
            "qwen_vision_fallback_model": "qwen/test-fallback",
            "allow_relevant_stock": "false",
        }
    )

    assert request.qwen_model == "qwen/test-primary"
    assert request.qwen_fallback_model == "qwen/test-fallback"
    assert request.allow_relevant_stock is False
    assert request.pipeline_v2_mode == "on"


def test_generation_request_rejects_unknown_explicit_format():
    with pytest.raises(ContractValidationError):
        GenerationRequestV2.from_mapping(
            {"script": "This script is long enough to validate.", "format_hint": "viral_magic"}
        )


@pytest.mark.parametrize(
    ("expected", "title", "script"),
    [
        (
            "narrative",
            "A City at Dawn",
            "A quiet city wakes before dawn. Workers open shops and buses begin moving.",
        ),
        (
            "listicle",
            "Three Extreme Jobs",
            "Welcome.\n\n#3) Cave Diver. The diver repairs tunnels.\n"
            "#2) Tower Climber. The climber repairs antennas.\n"
            "#1) Volcano Guide. The guide studies lava.",
        ),
        (
            "how_to",
            "How to Brew Tea",
            "A quick guide.\nStep 1: Boil fresh water.\n"
            "Step two — Add tea leaves.\nStep 3) Pour and serve.",
        ),
        (
            "timeline",
            "Project History",
            "A short history.\n1999: The project began.\n"
            "2005 - It reached a new city.\nIn 2020, it moved online.",
        ),
        (
            "q_and_a",
            "Sky Questions",
            "Q: Why is the sky blue?\nA: Molecules scatter blue light.\n"
            "Q2: Why is sunset red?\nA: Light travels farther.",
        ),
        (
            "dialogue",
            "Missed Train",
            "ALICE: We should leave now.\nBOB: I need another minute.\n"
            "ALICE: The train is coming.",
        ),
        (
            "mixed_multi_subject",
            "Dangerous Repairs",
            "## Alpine Rescue\nCrews work in snow.\n\n"
            "## Deep Sea Repair\nDivers repair cables.",
        ),
        (
            "comparison",
            "Tesla versus Toyota",
            "Tesla versus Toyota: which company builds more cars? "
            "Tesla focuses on EVs. Toyota has a broad range. Both compete globally.",
        ),
    ],
)
def test_deterministic_parser_recognizes_supported_formats_and_exact_spans(
    expected, title, script
):
    plan = build_script_plan(script, content_title=title)

    assert plan.detected_format == expected
    assert len([item for item in plan.containers if not item.is_leaf]) == 1
    assert all(item.parent_container_id for item in plan.leaf_containers)
    _assert_exact_coverage(plan)


def test_natural_language_list_markers_create_strict_leaf_containers():
    script = (
        "Intro. At number five is Erling Haaland. His sprint creates space. "
        "At number four is Jude Bellingham. His passing breaks the press."
    )
    plan = build_script_plan(script, content_title="Current football stars")
    items = [item for item in plan.leaf_containers if item.kind == "list_item"]

    assert plan.detected_format == "listicle"
    assert [(item.ordinal, item.canonical_subject) for item in items] == [
        (5, "Erling Haaland"),
        (4, "Jude Bellingham"),
    ]
    assert items[0].container_id != items[1].container_id
    _assert_exact_coverage(plan)


def test_embedded_next_item_marker_is_a_hard_boundary_even_without_punctuation():
    script = (
        "Number 1: Apple grows in cool orchards and is packed in crates "
        "Number 2: Banana grows in humid valleys and travels in boxes"
    )
    plan = build_script_plan(script, content_title="Two crops")
    items = [item for item in plan.leaf_containers if item.kind == "list_item"]

    assert len(items) == 2
    assert items[0].char_end == items[1].char_start
    assert "Number 2" not in items[0].text
    assert items[1].text.startswith("Number 2")


def test_adaptive_scene_operations_never_cross_list_item_boundaries():
    apple_detail = " ".join(
        [
            "Apple orchards use ladders and careful pruning.",
            "Workers inspect red fruit under daylight.",
            "Crates move from trees into a packing shed.",
            "Cold rooms protect each harvest before delivery.",
        ]
        * 3
    )
    banana_detail = " ".join(
        [
            "Banana farms grow broad leaves in humid valleys.",
            "Workers cut green bunches with padded tools.",
            "Cable systems carry fruit toward the wash station.",
            "Teams label each box before shipping.",
        ]
        * 3
    )
    script = f"Number 1: Apple. {apple_detail}\nNumber 2: Banana. {banana_detail}"
    plan = build_script_plan(script, content_title="Two Farming Systems")
    by_id = {item.container_id: item for item in plan.leaf_containers}

    assert len(plan.scenes) >= 6
    for scene in plan.scenes:
        container = by_id[scene.container_id]
        assert container.char_start <= scene.char_start < scene.char_end <= container.char_end
        assert scene.positive_subjects == (container.canonical_subject,)
        if container.canonical_subject == "Apple":
            assert "Banana" in scene.forbidden_subjects
        if container.canonical_subject == "Banana":
            assert "Apple" in scene.forbidden_subjects
        assert scene.source_policy["stock_allowed"] is False
        assert scene.reuse_policy["scope"] == "container"
    assert all(
        len(re.findall(r"(?i)\bnumber\s+[12]\s*:", scene.text)) <= 1
        for scene in plan.scenes
    )
    _assert_exact_coverage(plan)


def test_chapters_are_real_parents_and_numbered_items_remain_leaf_containers():
    script = (
        "Chapter 1: Water Work\n"
        "Number 1: Cave Diver. Divers inspect a submerged tunnel.\n"
        "Number 2: Saturation Diver. Teams maintain offshore pipes.\n"
        "Chapter 2: Work at Height\n"
        "Number 3: Tower Climber. Climbers repair antenna systems.\n"
        "Number 4: Window Cleaner. Crews descend from rooftops."
    )
    plan = build_script_plan(script, content_title="Extreme Jobs")
    chapters = [item for item in plan.containers if item.kind == "chapter" and not item.is_leaf]
    items = [item for item in plan.leaf_containers if item.kind == "list_item"]

    assert plan.detected_format == "listicle"
    assert [(item.ordinal, item.canonical_subject) for item in chapters] == [
        (1, "Water Work"),
        (2, "Work at Height"),
    ]
    assert [item.ordinal for item in items] == [1, 2, 3, 4]
    assert items[0].parent_container_id == chapters[0].container_id
    assert items[1].parent_container_id == chapters[0].container_id
    assert items[2].parent_container_id == chapters[1].container_id
    assert items[3].parent_container_id == chapters[1].container_id
    _assert_exact_coverage(plan)


def test_comparison_side_scenes_are_isolated_but_shared_scenes_allow_both_subjects():
    plan = build_script_plan(
        "Tesla versus Toyota: which company builds more cars? "
        "Tesla focuses on EVs. Toyota has a broad range. Both compete globally.",
        content_title="Tesla versus Toyota",
    )
    containers = {item.container_id: item for item in plan.leaf_containers}

    for scene in plan.scenes:
        kind = containers[scene.container_id].kind
        if kind == "comparison_left":
            assert scene.positive_subjects == ("Tesla",)
            assert scene.forbidden_subjects == ("Toyota",)
        elif kind == "comparison_right":
            assert scene.positive_subjects == ("Toyota",)
            assert scene.forbidden_subjects == ("Tesla",)
        elif kind == "comparison_shared":
            assert scene.positive_subjects == ("Tesla", "Toyota")
            assert scene.forbidden_subjects == ()


def test_container_ids_are_stable_across_scene_modes_and_restarts():
    script = (
        "Number 1: Alpha. One clause, another clause, and a final clause. "
        "Number 2: Beta. One clause, another clause, and a final clause."
    )
    adaptive_a = build_script_plan(script, content_title="Greek letters", scene_mode="adaptive")
    adaptive_b = build_script_plan(script, content_title="Greek letters", scene_mode="adaptive")
    every_beat = build_script_plan(script, content_title="Greek letters", scene_mode="Every Beat")

    assert [item.container_id for item in adaptive_a.containers] == [
        item.container_id for item in adaptive_b.containers
    ]
    assert [item.scene_id for item in adaptive_a.scenes] == [item.scene_id for item in adaptive_b.scenes]
    assert [item.container_id for item in adaptive_a.containers] == [
        item.container_id for item in every_beat.containers
    ]


def test_every_sentence_mode_keeps_sentence_boundaries_intact():
    script = (
        "Prince Harry married Meghan Markle in 2018. "
        "Prince Archie was presented to the public in 2019. "
        "King Charles III became monarch in 2022."
    )
    plan = build_script_plan(
        script,
        content_title="British Royal Family",
        scene_mode="Every Sentence",
    )

    assert [scene.text.strip() for scene in plan.scenes] == [
        "Prince Harry married Meghan Markle in 2018.",
        "Prince Archie was presented to the public in 2019.",
        "King Charles III became monarch in 2022.",
    ]
    assert "2019" not in plan.scenes[2].text


def test_nested_contract_payloads_are_immutable():
    plan = build_script_plan(
        "Number 1: Apple grows here.\nNumber 2: Banana grows there.",
        content_title="Fruit",
    )
    scene = plan.scenes[0]

    with pytest.raises(TypeError):
        scene.source_policy["stock_allowed"] = True
    assert isinstance(scene.source_policy["primary_sources"], tuple)


def test_normalization_map_preserves_original_crlf_and_nbsp_boundaries():
    source = "A\r\nB\u00a0C"
    normalized, mapping = normalize_script_with_map(source)

    assert normalized == "A\nB C"
    assert mapping == (0, 1, 3, 4, 5, 6)
    assert source[mapping[1] : mapping[2]] == "\r\n"


def test_monotonic_alignment_handles_numbers_deletions_and_repeated_phrases():
    script = (
        "Number one: Alpha begins. Alpha returns.\n"
        "Number two: Beta begins. Beta returns."
    )
    plan = build_script_plan(script, content_title="Two experiments")
    transcript = [
        "number",
        "1",
        "alpha",
        "begins",
        # The first repeated Alpha is omitted by Whisper.
        "returns",
        "number",
        "2nd",
        "beta",
        "begins",
        "beta",
        "returns",
    ]
    words = [
        {"word": word, "start": index * 0.35, "end": index * 0.35 + 0.25}
        for index, word in enumerate(transcript)
    ]
    token_alignment = align_script_tokens(script, words)
    mapped = [
        word_index
        for word_index, score in zip(
            token_alignment.source_to_word, token_alignment.token_scores
        )
        if word_index is not None and score >= 0.75
    ]

    assert mapped == sorted(mapped)
    assert len(mapped) == len(set(mapped))
    assert token_alignment.matched_ratio >= 0.75

    timed_plan, result = align_script_plan_to_words(plan, words, audio_duration=4.0)
    assert [item.scene_id for item in timed_plan.scenes] == [item.scene_id for item in plan.scenes]
    assert timed_plan.scenes[0].end_seconds <= timed_plan.scenes[-1].start_seconds
    assert timed_plan.scenes[-1].end_seconds == pytest.approx(4.0)
    assert result.token_alignment.confidence >= 0.7


def test_weak_alignment_estimates_timing_without_crossing_scene_or_container_order():
    script = "Number 1: Apple grows here.\nNumber 2: Banana grows there."
    plan = build_script_plan(script, content_title="Fruit")
    words = [
        {"word": "unrelated", "start": 0.2, "end": 0.6},
        {"word": "speech", "start": 1.2, "end": 1.6},
    ]
    timed_plan, _ = align_script_plan_to_words(plan, words, audio_duration=3.0)

    assert all(item.timing_quality == "estimated" for item in timed_plan.scenes)
    assert all(item.word_start_index is None for item in timed_plan.scenes)
    assert timed_plan.scenes[0].start_seconds == 0.0
    assert timed_plan.scenes[-1].end_seconds == 3.0
    assert all(
        left.end_seconds == pytest.approx(right.start_seconds)
        for left, right in zip(timed_plan.scenes, timed_plan.scenes[1:])
    )


def test_storyboard_store_writes_atomic_snapshot_and_redacted_append_ledger(tmp_path):
    script = "A concise documentary narration with one complete scene."
    request = GenerationRequestV2.from_mapping(
        {"script": script, "title": "Documentary", "name": "export"}
    )
    plan = build_script_plan(script, content_title=request.content_title)
    storyboard = StoryboardV2.new(
        job_id="job-1", run_id="run-1", request=request, script_plan=plan
    )
    output = tmp_path / "video.mp4"
    store = StoryboardStore.for_output(output, job_id="job-1", run_id="run-1")

    snapshot_path = store.save_atomic(storyboard)
    assert snapshot_path.name == "video.storyboard.json"
    assert not list(tmp_path.glob("*.part"))

    updated = store.record_attempt(
        storyboard,
        "candidate.discovered",
        {
            "api_key": "secret-value",
            "groq_api_key": "provider-secret",
            "youtube_po_token": "po-secret",
            "authorization": "Bearer hidden-token",
            "url": "https://example.test/image.jpg?token=query-secret&width=1280",
            "token_start": 4,
        },
        scene_id=plan.scenes[0].scene_id,
        container_id=plan.scenes[0].container_id,
    )
    disk_text = snapshot_path.read_text(encoding="utf-8") + store.event_path.read_text(
        encoding="utf-8"
    )

    assert "secret-value" not in disk_text
    assert "provider-secret" not in disk_text
    assert "po-secret" not in disk_text
    assert "hidden-token" not in disk_text
    assert "query-secret" not in disk_text
    assert "[REDACTED]" in disk_text
    assert updated.attempt_ledger[0].payload["token_start"] == 4
    assert store.load().attempt_ledger[0].sequence == 1
    assert [item.sequence for item in store.read_events()] == [1]
    with pytest.raises(ContractValidationError, match="stale"):
        store.record_attempt(storyboard, "candidate.retried", {})
    assert [item.sequence for item in store.read_events()] == [1]


def test_storyboard_rejects_media_candidate_from_another_container():
    script = "Number 1: Apple grows here.\nNumber 2: Banana grows there."
    request = GenerationRequestV2.from_mapping(
        {"script": script, "title": "Fruit", "name": "fruit-export"}
    )
    plan = build_script_plan(script, content_title=request.content_title)
    first, second = plan.scenes
    crossed = MediaCandidateV1(
        candidate_id="candidate-1",
        scene_id=first.scene_id,
        container_id=second.container_id,
        provider="wikimedia",
        source="web_image",
    )

    with pytest.raises(ContractValidationError, match="crosses"):
        StoryboardV2(
            job_id="job",
            run_id="run",
            request=request,
            script_plan=plan,
            media_candidates=(crossed,),
        )


def test_contract_json_round_trip_preserves_ids_spans_and_hashes():
    script = "Number 1: Apple grows here.\nNumber 2: Banana grows there."
    request = GenerationRequestV2.from_mapping(
        {"script": script, "title": "Fruit", "name": "fruit-export"}
    )
    plan = build_script_plan(script, content_title=request.content_title)
    storyboard = StoryboardV2.new(
        job_id="job-roundtrip",
        run_id="run-roundtrip",
        request=request,
        script_plan=plan,
    )

    restored = StoryboardV2.from_dict(json.loads(json.dumps(storyboard.to_dict())))

    assert restored.to_dict() == storyboard.to_dict()
    assert [item.scene_id for item in restored.script_plan.scenes] == [
        item.scene_id for item in storyboard.script_plan.scenes
    ]
    assert all(item.contract_hash for item in restored.script_plan.scenes)
