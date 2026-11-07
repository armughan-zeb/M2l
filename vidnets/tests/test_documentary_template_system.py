from __future__ import annotations

from pathlib import Path

from server.services.documentary_template_system import (
    CATALOG_VERSION,
    FONT_LIBRARY,
    TEMPLATE_FAMILIES,
    art_direction_pack,
    catalog_manifest,
    resolve_template_design,
)
from server.services.editorial_cue_planner import plan_editorial_cues
from server.services.template_library import list_template_catalog
from server.services.storyboard_v2.text import tokenize_source


ROOT = Path(__file__).resolve().parents[1]


def test_catalog_has_sixteen_reusable_families_and_only_packaged_fonts():
    assert len(TEMPLATE_FAMILIES) == 16
    assert catalog_manifest()["version"] == CATALOG_VERSION
    for font in FONT_LIBRARY.values():
        assert (ROOT / "fonts" / font["file"]).is_file(), font["file"]


def test_art_direction_is_coherent_and_seeded():
    first = art_direction_pack("history", 42)
    second = art_direction_pack("history", 42)
    other = art_direction_pack("history", 43)
    assert first == second
    assert first["display"] in {"lora", "oswald", "plus_jakarta", "archivo_black"}
    assert first != other


def test_template_design_changes_variant_but_stays_within_direction():
    direction = art_direction_pack("crime", 8)
    first = resolve_template_design("evidence-callout", kind="highlight", seed=8, art_direction=direction, position="top_left")
    second = resolve_template_design("evidence-callout", kind="highlight", seed=9, art_direction=direction, position="top_left")
    assert first["family"] == second["family"] == "evidence_file"
    assert first["display_font"] in {"ArchivoBlack-Regular.ttf", "Oswald-SemiBold.ttf", "PlusJakartaSans-Bold.ttf", "DMSans-Bold.ttf"}
    assert first["variant"] != second["variant"] or first["entry_motion"] != second["entry_motion"]


def test_director_headline_center_is_in_the_approved_runtime_catalog():
    templates = {item["id"]: item for item in list_template_catalog()["templates"]}
    assert templates["headline-center"]["status"] == "approved"
    assert templates["headline-center"]["component"] == "kinetic-statement"


def test_unaligned_speech_card_is_suppressed_instead_of_estimated():
    text = "On April 15, 1912 the ship changed course."
    tokens = list(tokenize_source(text))
    scene = {
        "scene_id": "scene-1",
        "container_id": "chapter-1",
        "text": text,
        "start_seconds": 0,
        "end_seconds": 6,
        "word_start_index": 0,
        "word_end_index": len(tokens),
        "timing_quality": "failed",
        "alignment_confidence": 0.1,
    }
    cues = plan_editorial_cues([scene], [], editorial_beats=[{}])
    assert cues == ()


def test_aligned_card_contains_full_text_and_local_word_bounds():
    text = "Prince Charles met Prince Harry in London."
    tokens = list(tokenize_source(text))
    words = [
        {"index": i, "text": token.text, "start_seconds": 0.35 + i * 0.24, "end_seconds": 0.52 + i * 0.24, "confidence": 0.99}
        for i, token in enumerate(tokens)
    ]
    scene = {
        "scene_id": "scene-1",
        "container_id": "general",
        "text": text,
        "start_seconds": 0,
        "end_seconds": 7,
        "word_start_index": 0,
        "word_end_index": len(words),
        "timing_quality": "word_aligned",
        "alignment_confidence": 0.99,
    }
    cues = plan_editorial_cues(
        [scene], words,
        editorial_beats=[{"role": "person", "treatment": "person", "emphasis": "Prince Charles", "_emphasis_explicit": True}],
        variation_seed=4,
    )
    assert len(cues) == 1
    cue = cues[0]
    assert cue.text == "Prince Charles"
    assert cue.timing_quality == "word_aligned"
    assert cue.word_end_index > cue.word_start_index
    assert cue.props["_contract_font"]
    assert cue.props["_template_family"] == "profile_dossier"
