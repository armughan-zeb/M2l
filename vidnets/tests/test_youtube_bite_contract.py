"""Regression coverage for intro diversity and timestamped YouTube bites."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import youtube_pipeline_v2 as yt


def _candidate(video_id: str) -> dict:
    return {
        "id": video_id,
        "title": "RMS Titanic raw archival footage",
        "description": "moving documentary footage from the archive",
        "channel": "Maritime Archive",
        "duration": 240,
    }


def _row(index: int, *, intro: bool = False, bite: bool = False) -> dict:
    return {
        "scene_index": index,
        "candidate_subject": "RMS Titanic",
        "section_subject": "RMS Titanic",
        "scene_text": "RMS Titanic archival footage at sea",
        "domain_category": "history",
        "visual_requirement": {},
        "intro_unique": intro,
        "bite_clip": bite,
    }


def test_intro_assignment_uses_distinct_masters():
    rows = [_row(index, intro=True) for index in range(3)]
    pools = {"rms titanic": [_candidate(f"master-{index}") for index in range(3)]}
    assignments = yt._assign_candidates(
        rows,
        pools,
        [4.0, 4.0, 4.0],
        allow_same_source_reuse=True,
        prefer_distinct_sources=True,
        lenient_acceptance=True,
    )
    assert [item["id"] for item in assignments] == ["master-0", "master-1", "master-2"]


def test_bite_assignment_reuses_master_at_distinct_timestamps():
    rows = [_row(index, bite=True) for index in range(3)]
    pools = {"rms titanic": [_candidate("master-1")]}
    assignments = yt._assign_candidates(
        rows,
        pools,
        [4.0, 4.0, 4.0],
        # This mirrors Mixed Media's distinct-source preference. Bite clips
        # are the explicit exception: the same vetted master may supply more
        # than one timestamped moment.
        allow_same_source_reuse=False,
        prefer_distinct_sources=True,
        lenient_acceptance=True,
    )
    assert all(item and item["id"] == "master-1" for item in assignments)
    starts = [item["best_timestamp"] for item in assignments]
    assert len(set(starts)) == 3


def test_bite_queries_keep_safety_exclusions_but_add_cutaway_recall():
    queries = yt._queries_for_row(
        {
            "_semantic_locked": False,
            "domain_anchor": "RMS Titanic",
            "domain_category": "history",
            "candidate_subject": "RMS Titanic",
            "visual_facet": "lifeboat launch",
            "bite_clip": True,
        },
        "RMS Titanic",
        "lifeboats launch during the sinking",
    )
    joined = " ".join(queries).lower()
    assert "raw footage" in joined or "archive footage" in joined
    assert "-streamer" not in joined and "-webcam" not in joined and "-captioned" not in joined


def test_corner_watermark_metadata_is_not_a_source_package_rejection():
    candidate = _candidate("watermark-master")
    candidate["title"] = "RMS Titanic archive footage with corner watermark"
    assert yt._hard_block_reason(candidate) == ""
    assert yt._score_candidate(
        candidate,
        "RMS Titanic",
        "RMS Titanic archival footage",
        "RMS Titanic archive footage",
        {"domain_category": "history", "container_subject": "RMS Titanic"},
    ) > -999.0
