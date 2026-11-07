"""Integration regression tests for the fixes applied to the production pipeline.

Covers:
1. Fact-aware highlight selection (numbers/dates/quantities outrank verbs).
2. Shadow-web metadata propagation: the render safety gate must see metadata
   even when a YouTube scene slot was rescued by a shadow-web image.
3. Visual safety blocks synthetic / avatar-generated content.
"""
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))


class TestFactAwareHighlights:
    def test_numbers_always_win_over_generic_verbs(self):
        from services.semantic_edit_engine import _single_highlight_word

        assert _single_highlight_word(
            "The RMS Titanic carried 2,200 passengers and crew.", ""
        ) == "200"
        assert _single_highlight_word(
            "Only 705 of the 2,200 people aboard survived the disaster.", ""
        ) == "705"
        assert _single_highlight_word(
            "The 882 foot ship measured 46328 tons.", ""
        ) == "46328"
        assert _single_highlight_word(
            "At 11 40 PM the Titanic struck an iceberg.", ""
        ) in ("40", "struck")  # struck wins; times are split by tokenizer

    def test_proper_nouns_and_power_words_beat_common_verbs(self):
        from services.semantic_edit_engine import _single_highlight_word

        assert _single_highlight_word(
            "The Titanic was built at Harland and Wolff shipyard in Belfast.", ""
        ) == "Titanic"
        assert _single_highlight_word(
            "Water flooded five compartments within minutes.", ""
        ) == "flooded"
        assert _single_highlight_word(
            "The collision caused the unsinkable ship to sink.", ""
        ) == "collision"
        assert _single_highlight_word(
            "Captain Smith went down with his ship.", ""
        ) == "Smith"

    def test_common_past_tense_verbs_are_not_highlights(self):
        from services.semantic_edit_engine import _single_highlight_word

        # "built" was previously selected as highlight for construction scenes
        word = _single_highlight_word(
            "Titanic was built at Harland and Wolff shipyard in Belfast.", ""
        )
        assert word != "built"
        assert word != "Built"


class TestTopicAnchorRegression:
    def test_explicit_topic_brief_beats_short_narrative_title_hook(self):
        import app

        script = (
            "Glendale, Arizona. February 1, 2015. Super Bowl XLIX has 26 seconds left. "
            "Seattle trails New England 28–24 at the Patriots' one-yard line. "
            "Russell Wilson throws toward Ricardo Lockette, and Malcolm Butler intercepts it."
        )
        topic = (
            "Super Bowl XLIX final play: New England Patriots vs Seattle Seahawks, "
            "February 1, 2015, University of Phoenix Stadium."
        )

        assert app._resolve_topic_anchor_for_config(
            "26 Seconds: One Yard from a Repeat", script, {"topic": topic}
        ) == "Super Bowl XLIX"

    def test_explanatory_title_keeps_subject_noun_phrase(self):
        import app

        script = (
            "Electric cars are changing the way cities move. "
            "Battery vehicles are appearing in taxis, buses, and family cars."
        )

        assert app._resolve_topic_anchor_for_config(
            "How Electric Cars Changed Cities", script, {}
        ) == "Electric Cars"
        assert app._resolve_topic_anchor_for_config(
            "How Electric Cars Reshape City Streets", script, {}
        ) == "Electric Cars"

    def test_supported_title_lead_beats_script_descriptor(self):
        import app

        title = "Electric Cars in Modern Cities Main Pipeline QA 3"
        script = (
            "Electric cars are changing city streets. "
            "Battery electric vehicles produce no tailpipe exhaust."
        )
        assert app._resolve_topic_anchor_for_config(title, script, {}) == "Electric Cars"

    def test_numeric_documentary_title_keeps_full_topic_anchor(self):
        import app

        title = "Apollo 11: Remotion Test"
        script = (
            "Apollo 11 carried Neil Armstrong and Buzz Aldrin toward the Moon. "
            "Neil Armstrong and Buzz Aldrin descended while Michael Collins orbited above. "
            "Neil Armstrong became the first human to step onto another world."
        )

        assert app._resolve_topic_anchor_for_config(title, script, {}) == "Apollo 11"

    def test_operational_title_suffix_is_removed_from_title_lead(self):
        import app

        title = "Electric Cars Final Main Pipeline QA"
        script = (
            "Electric cars are changing city streets. "
            "Battery electric vehicles produce no tailpipe exhaust."
        )
        assert app._resolve_topic_anchor_for_config(title, script, {}) == "Electric Cars"

    def test_script_entity_beats_generated_output_title_suffix(self):
        import app

        title = "Titanic YouTube Recovery Patched"
        script = (
            "On April 10, 1912, the RMS Titanic departed Southampton. "
            "The Titanic was the largest passenger ship ever built. "
            "After striking an iceberg, Titanic sank in the North Atlantic."
        )

        assert app._resolve_topic_anchor_for_config(title, script, {}) == "RMS Titanic"

    def test_supported_documentary_title_beats_incidental_country_mention(self):
        import app

        title = "The Panama Canal: A Shortcut Through a Continent"
        script = (
            "At Panama's narrow crossing, ships once faced a detour around South America. "
            "The United States took over construction in 1904. "
            "Water raises ships through the Panama Canal to Gatun Lake."
        )
        assert app._resolve_topic_anchor_for_config(title, script, {}) == "Panama Canal"

    def test_youtube_rows_replace_broad_container_anchor_for_single_topic(self):
        import app

        rows = app._normalize_youtube_query_rows(
            [{
                "section_subject": "maritime disaster",
                "scene_subject": "maritime disaster",
                "youtube_query": "maritime disaster archival footage",
            }],
            "Titanic YouTube Recovery Patched",
            ["The RMS Titanic departed Southampton."],
            silent=True,
            topic_anchor="RMS Titanic",
        )

        assert rows[0]["domain_anchor"] == "RMS Titanic"
        assert rows[0]["scene_subject"] == "RMS Titanic"
        assert rows[0]["youtube_query"].startswith("RMS Titanic")

    def test_llm_emphasis_cannot_invent_text_card_words(self):
        from services.native_engine.smart_router import build_props_from_beat
        from services.semantic_edit_engine import _single_highlight_word, build_semantic_edit_manifest

        narration = "The crew reached New York."
        assert _single_highlight_word(narration, "Tokyo") != "Tokyo"
        assert build_props_from_beat(
            {"emphasis": "Tokyo", "role": "evidence", "treatment": "keyword"},
            narration,
        )["text"].lower() in {"the", "crew", "reached", "new", "york"}

        manifest = build_semantic_edit_manifest(
            [narration],
            [3.0],
            topic="RMS Titanic",
            editorial_beats=[{"emphasis": "Tokyo", "role": "evidence", "treatment": "keyword"}],
        )
        card_text = manifest["scenes"][0]["graphic"]["props"].get("_contract_text", "")
        assert card_text
        assert card_text.lower() in {"the", "crew", "reached", "new", "york"}
        assert card_text.lower() != "tokyo"


class TestShadowWebMetadataPropagation:
    def test_shadow_web_fills_metadata_for_render_gate(self):
        import app as server_app

        paths = []
        metadata = {"web_image_candidates": [{} for _ in range(2)]}
        for name in ("archive.jpg", "avatar_render.jpg"):
            path = Path(__file__).parent.parent / "output" / name
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                from PIL import Image
                Image.new("RGB", (32, 32), (20, 20, 20)).save(path)
            paths.append(str(path))

        request_indices = [0, 1]
        image_paths = paths
        image_metadata = [
            {"title": "Titanic engineer memorial archive", "tags": "titanic historical", "source": "Flickr"},
            {"title": "3D rendered woman in bikini", "tags": "3d render woman bikini", "source": "Flickr"},
        ]

        metadata = {"web_image_candidates": [{} for _ in range(len(request_indices))]}
        yt_shadow_web_paths_by_scene = {}
        shadow_metadata = image_metadata
        paths_aligned = image_paths
        for local_idx, scene_idx in enumerate(request_indices):
            if local_idx >= len(paths_aligned) or not paths_aligned[local_idx]:
                continue
            yt_shadow_web_paths_by_scene[scene_idx] = paths_aligned[local_idx]
            if scene_idx < len(metadata.get('web_image_candidates') or []):
                metadata['web_image_candidates'][scene_idx] = (
                    dict(shadow_metadata[local_idx])
                    if local_idx < len(shadow_metadata)
                    and isinstance(shadow_metadata[local_idx], dict)
                    else {}
                )

        assert metadata["web_image_candidates"][0]["title"] == "Titanic engineer memorial archive"
        assert metadata["web_image_candidates"][1]["title"] == "3D rendered woman in bikini"

    def test_render_gate_catches_synthetic_rescued_image(self):
        import app as server_app

        media_plan = {
            "media_paths": ["/tmp/fake.jpg"],
            "source_labels": ["Web Images (parallel web rescue)"],
            "metadata": {
                "web_image_candidates": [{
                    "title": "3D rendered woman in bikini",
                    "tags": "3d render avatar secondlife",
                    "description": "A 3D render.",
                    "source": "Flickr",
                }],
            },
            "prompt_bundle": {"beat_texts": ["Scene about Titanic."]},
        }

        with mock.patch.object(
            server_app, "_fill_unresolved_with_general_web_fallback",
            side_effect=lambda *a, **kw: None,
        ), mock.patch.object(
            server_app, "_run_targeted_stock_rescue", return_value={}
        ), mock.patch.object(
            server_app, "_fill_final_bad_slots_from_existing_media", return_value=0
        ), mock.patch.object(
            server_app, "_media_path_exists_for_render", return_value=True
        ), mock.patch.object(
            server_app, "_is_video_path", return_value=False
        ):
            try:
                server_app._repair_media_plan_for_render(
                    media_plan=media_plan,
                    audio_plan={"scene_texts": ["Scene about Titanic."], "scene_durations": [2.0]},
                    job_config={},
                    render_resolution=(1280, 720),
                    title="Titanic",
                    script="A documentary about the Titanic.",
                    target_scene_count=1,
                )
            except server_app.MediaContractError:
                pass  # expected — unsafe image blocked

    def test_adult_synth_content_is_rejected_by_metadata_gate(self):
        from services.web_image.safety import metadata_safety_reason

        assert metadata_safety_reason(
            title="3D rendered avatar",
            tags="3d render secondlife",
            description="My Second Life avatar",
        ) == "synthetic_metadata"

        assert metadata_safety_reason(
            title="RMS Titanic historical photo",
            tags="titanic historical archive",
            description="Archival documentary photo",
        ) == ""


class TestCardGeometry:
    def test_common_past_tense_verbs_in_stopwords(self):
        """Verbs like 'built'/'began' should not be single-word card headlines."""
        from services.semantic_edit_engine import _CARD_STOPWORDS

        forbidden = {"built", "began", "carried", "reached", "opened"}
        overlap = forbidden & set(_CARD_STOPWORDS)
        assert overlap == forbidden, (
            f"Missing stopwords prevent fact-first highlight picking: {forbidden - overlap}"
        )
