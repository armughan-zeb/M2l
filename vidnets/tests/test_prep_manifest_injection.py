"""Prep-manifest-first candidate injection (services/youtube_pipeline_v2.py,
services/advance_prep.py).

No network, no live server, no render. The Panama fixture replays the real
run-1 prep manifest (6 verified YouTube cuts, scenes 1/2/3/7/8/9) against
the real run-2 live beats (13 Whisper-split scenes) with a throwaway
verified-cut cache, so the replay numbers in the test are the numbers in
the Panama measurement record — not a synthetic toy.

Expected Panama replay:
  injected 6/6 (all map with overlap 1.00 to live scenes 0/1/2/5/6);
  assigned 5/13 (only the mapped scenes; live scene 6 takes one of its
  two prep cuts as primary, the other stays a ranked alternate);
  cache-restorable at live durations 4/6 (live scene 6's two prep cuts
  are shorter than the merged 6.7 s live scene, so they need a fresh
  ranged download at the verified moment instead of a cache restore).
"""

import json
import os
from pathlib import Path

import pytest

from services import advance_prep as prep
from services.youtube_media_cache import get_verified_cut, put_verified_cut
from services.youtube_pipeline_v2 import (
    _assign_candidates,
    _build_scene_rows,
    _score_candidate,
    get_youtube_clips,
    inject_prep_manifest_candidates,
    prep_manifest_injection_enabled,
)

TITLE = "How the Panama Canal Changed World Trade"

# Real run-1 prep scene texts (manifest `scenes`, 1-indexed) for the six
# scenes with verified YouTube cuts.
PREP_SCENES = {
    1: "Cross from the Pacific to the Caribbean in just two hours through the Panama Canal.",
    2: "Two oceans two hours one canal.",
    3: "That is the promise of this crossing and the question of this film is what two hours between oceans does to world trade.",
    7: "The canal is the passage.",
    8: "Without it the two oceans are separated by the land itself",
    9: "and every crossing between them must go another way.",
}

# Real run-2 live beats (13 Whisper-split scenes, 0-indexed).
LIVE_BEATS = [
    "Cross from the Pacific to the Caribbean in just two hours through the Panama Canal. ",
    "Two oceans, two hours, one canal. ",
    "That is the promise of this crossing, and the question of this film is what two hours between oceans does to world trade. \n\n",
    "Panamá is a narrow strip of land with two coasts and more than two thousand nine hundred kilometres of coastline, the Caribbean on one side and the Pacific on the other. ",
    "The canal runs straight through it, joining the two oceans in a single transit. \n\n",
    "The canal is the passage. ",
    "Without it, the two oceans are separated by the land itself, and every crossing between them must go another way. ",
    "With it, the passage takes two hours. \n\n",
    "Panamá today is known far beyond the canal. ",
    "Panama City has been recognised as a UNESCO Creative City of Gastronomy since 2017, the country was chosen Best Tourist Destination in Latin America and South America for a third consecutive year, and Chiriquí Province was highlighted in Lonely Planet's Best in Travel for 2025. \n\n",
    "So how did the Panama Canal change world trade? ",
    "It put two oceans two hours apart. ",
    "That crossing, two hours from the Pacific to the Caribbean, is the change itself.",
]

# Real run-2 live scene durations (seconds, from the Whisper word timings).
LIVE_DURATIONS = [4.77, 1.95, 7.27, 9.01, 4.09, 1.47, 6.7, 2.36, 2.63, 17.07, 3.28, 2.36, 4.84]

# Real run-1 prep YouTube entries: (prep scene, video_id, start, duration).
PREP_CUTS = [
    (1, "YzFGkdPmDZU", 98.119, 6.75),
    (2, "-1jOqORJXr4", 0.0, 3.0),
    (3, "ZdFXSbQWa4w", 637.8, 10.35),
    (7, "qPKAtMPt40k", 563.48, 3.0),
    (8, "2gEmYKeziek", 2208.08, 4.95),
    (9, "YzFGkdPmDZU", 166.8, 4.05),
]

EXPECTED_LIVE_SCENE = {1: 0, 2: 1, 3: 2, 7: 5, 8: 6, 9: 6}


def _fake_cut_bytes() -> bytes:
    # _valid_file / put_verified_cut need >= 8 KiB with an ftyp box.
    return b"\x00\x00\x00\x18ftypisom" + b"\x00" * 9000


@pytest.fixture()
def panama_env(tmp_path, monkeypatch):
    """A tmp prep root with the Panama manifest + a tmp verified-cut cache.

    The manifest's entry paths point at tmp cut files that are also banked
    in the tmp cache via put_verified_cut, mirroring the real prep flow
    (manifest path == cache path).
    """
    prep_root = tmp_path / "prep"
    cache_dir = tmp_path / "yt-cuts"
    monkeypatch.setenv("VIDZEO_PREP_ROOT", str(prep_root))
    monkeypatch.setenv("VIDZEO_YT_CUT_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("VIDZEO_YT_PREP_MANIFEST_INJECTION", "1")
    title_dir = prep_root / "titles" / ("0" * 64)
    title_dir.mkdir(parents=True)
    entries = []
    for scene, video_id, start, duration in PREP_CUTS:
        stored = put_verified_cut(
            video_id, start, duration,
            str(_write_cut(tmp_path, video_id, start)),
            metadata={"prep_title": "panama"},
        )
        assert stored, f"could not bank {video_id} {start}"
        entries.append({
            "scene": scene, "video_id": video_id, "start": start,
            "duration": duration, "query": "Panama Canal documentary footage",
            "path": stored, "scaled_path": None, "verification": "cache",
        })
    manifest = {
        "schema": "vidzeo_prep_manifest_v1",
        "title": TITLE,
        "title_key": "0" * 64,
        "duration_minutes": 1.0,
        "status": "ready",
        "scenes": [
            {"index": idx, "text": text, "words": len(text.split())}
            for idx, text in sorted(PREP_SCENES.items())
        ],
        "youtube": {"attempted": 17, "verified": 6, "entries": entries},
        "web": {"attempted": 51, "verified": 0, "entries": []},
    }
    (title_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return {"prep_root": prep_root, "cache_dir": cache_dir, "manifest": manifest}


def _write_cut(tmp_path, video_id, start):
    path = tmp_path / f"cut-{video_id}-{int(start)}.mp4"
    path.write_bytes(_fake_cut_bytes())
    return path


def _live_rows():
    return _build_scene_rows(TITLE, list(LIVE_BEATS))


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def test_load_manifest_for_title_finds_by_normalized_title(panama_env):
    manifest = prep.load_manifest_for_title("  how the PANAMA canal changed world trade ")
    assert manifest["title"] == TITLE
    assert len(manifest["youtube"]["entries"]) == 6


def test_load_manifest_for_title_misses_and_ambiguous(panama_env, tmp_path, monkeypatch):
    assert prep.load_manifest_for_title("No Such Title") == {}
    # A second manifest for the same title (another duration) is ambiguous:
    # fail soft with {} rather than picking the wrong duration's cuts.
    other = panama_env["prep_root"] / "titles" / ("1" * 64)
    other.mkdir(parents=True)
    (other / "manifest.json").write_text(
        json.dumps({"title": TITLE, "youtube": {"entries": []}}), encoding="utf-8"
    )
    assert prep.load_manifest_for_title(TITLE) == {}


# ---------------------------------------------------------------------------
# Injection switch / fail-soft
# ---------------------------------------------------------------------------

def test_flag_off_leaves_pools_byte_identical(panama_env, monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_PREP_MANIFEST_INJECTION", "0")
    assert not prep_manifest_injection_enabled()
    pools = {"primary panama canal": [{"video_id": "live1", "title": "Panama Canal documentary"}]}
    before = json.dumps(pools, sort_keys=True)
    audit = inject_prep_manifest_candidates(pools, _live_rows(), TITLE, list(LIVE_BEATS))
    assert audit["injected"] == 0
    assert audit["reason"] == "disabled"
    assert json.dumps(pools, sort_keys=True) == before


def test_no_manifest_leaves_pools_untouched(panama_env):
    pools = {"primary panama canal": [{"video_id": "live1", "title": "Panama Canal documentary"}]}
    before = json.dumps(pools, sort_keys=True)
    audit = inject_prep_manifest_candidates(pools, _live_rows(), "Unprepped Title", list(LIVE_BEATS))
    assert audit["injected"] == 0
    assert json.dumps(pools, sort_keys=True) == before


def test_missing_cache_file_entry_is_skipped(panama_env):
    manifest = panama_env["manifest"]
    manifest["youtube"]["entries"][0]["path"] = "/nonexistent/cut.mp4"
    (panama_env["prep_root"] / "titles" / ("0" * 64) / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    audit = inject_prep_manifest_candidates({}, _live_rows(), TITLE, list(LIVE_BEATS))
    assert audit["injected"] == 5
    assert audit["skipped"] == 1


def test_unmappable_scene_text_is_skipped(panama_env):
    manifest = panama_env["manifest"]
    manifest["scenes"][0]["text"] = "A completely unrelated narration about baking sourdough bread."
    (panama_env["prep_root"] / "titles" / ("0" * 64) / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    audit = inject_prep_manifest_candidates({}, _live_rows(), TITLE, list(LIVE_BEATS))
    assert audit["injected"] == 5
    assert audit["skipped"] == 1


# ---------------------------------------------------------------------------
# Panama replay: injection, scoring, assignment, cache restore
# ---------------------------------------------------------------------------

def test_panama_replay_injects_all_six_cuts(panama_env):
    pools = {}
    audit = inject_prep_manifest_candidates(pools, _live_rows(), TITLE, list(LIVE_BEATS))
    assert audit["injected"] == 6
    assert sorted(audit["scenes"]) == [0, 1, 2, 5, 6]
    assert audit["skipped"] == 0
    injected = pools["primary panama canal"]
    assert len(injected) == 6
    for candidate in injected:
        assert candidate["prep_manifest_injected"] is True
        assert candidate["prep_live_scene"] == EXPECTED_LIVE_SCENE[candidate["prep_scene"]]
        assert candidate["prep_scene_overlap"] == 1.0
        assert candidate["best_timestamp"] == candidate["prep_best_timestamp"]
        assert candidate["timestamp_evidence"] == "prep_manifest_verified_moment"
        # No fabricated metadata: the prep query is not the video's title.
        assert candidate["title"] == "" and candidate["channel"] == ""
        assert candidate["prep_verification"] == "cache"


def test_panama_replay_second_injection_is_a_noop(panama_env):
    pools = {}
    inject_prep_manifest_candidates(pools, _live_rows(), TITLE, list(LIVE_BEATS))
    audit = inject_prep_manifest_candidates(pools, _live_rows(), TITLE, list(LIVE_BEATS))
    assert audit["injected"] == 0
    assert len(pools["primary panama canal"]) == 6


def test_injected_candidate_scores_above_the_lenient_floor_but_below_live(panama_env):
    pools = {}
    inject_prep_manifest_candidates(pools, _live_rows(), TITLE, list(LIVE_BEATS))
    candidate = pools["primary panama canal"][0]
    live_scene = candidate["prep_live_scene"]
    injected_score = _score_candidate(
        dict(candidate), "Panama Canal", LIVE_BEATS[live_scene],
        candidate.get("query_text") or "",
    )
    # The prep carve-out lifts the -180 subject-mismatch return; the
    # candidate stays a backstop (below any real-metadata live winner).
    assert injected_score == -5.0
    assert injected_score > -179.0
    bare_twin = {"video_id": candidate["video_id"], "title": "", "description": "", "channel": ""}
    assert _score_candidate(
        bare_twin, "Panama Canal", LIVE_BEATS[live_scene], candidate.get("query_text") or ""
    ) == -180.0
    live_like = {
        "video_id": "live-winner", "title": "Panama Canal documentary footage",
        "description": "Panama Canal transit", "channel": "Documentary Channel",
        "duration": 600,
    }
    assert _score_candidate(dict(live_like), "Panama Canal", LIVE_BEATS[live_scene], "") > injected_score


def test_hard_blocks_still_apply_to_prep_candidates():
    flagged = {
        "video_id": "synth1", "title": "", "description": "", "channel": "Foo AI Generated",
        "prep_manifest_injected": True, "prep_scene_overlap": 1.0,
    }
    assert _score_candidate(dict(flagged), "Panama Canal", LIVE_BEATS[0], "") == -999.0


def test_panama_replay_assigns_only_the_mapped_scenes(panama_env):
    pools = {}
    inject_prep_manifest_candidates(pools, _live_rows(), TITLE, list(LIVE_BEATS))
    assignments = _assign_candidates(
        _live_rows(), pools, list(LIVE_DURATIONS),
        lenient_acceptance=True, allow_same_source_reuse=True, prefer_distinct_sources=True,
    )
    assigned = {idx for idx, item in enumerate(assignments) if item}
    assert assigned == {0, 1, 2, 5, 6}
    for idx in sorted(assigned):
        item = assignments[idx]
        assert item["prep_manifest_injected"] is True
        prep_scene = next(
            scene for scene, live in EXPECTED_LIVE_SCENE.items() if live == idx
        ) if idx != 6 else 8  # live 6's primary is prep scene 8's cut
        expected_start = next(
            start for scene, _, start, _ in PREP_CUTS if scene == prep_scene
        )
        assert item["best_timestamp"] == pytest.approx(expected_start, abs=0.01)
    # Live 6's second prep cut stays available as a ranked alternate.
    assert len(assignments[6].get("ranked_options") or []) >= 2


def test_panama_replay_cache_restore_four_of_six(panama_env):
    pools = {}
    inject_prep_manifest_candidates(pools, _live_rows(), TITLE, list(LIVE_BEATS))
    hits = {}
    for candidate in pools["primary panama canal"]:
        live_scene = candidate["prep_live_scene"]
        hit = get_verified_cut(
            candidate["video_id"], candidate["prep_best_timestamp"], LIVE_DURATIONS[live_scene]
        )
        hits[(candidate["video_id"], candidate["prep_best_timestamp"])] = hit
    # The four cuts whose cached window covers the live scene restore
    # with zero download; live scene 6's merged 6.7 s scene is longer
    # than both of its prep cuts (4.95 s / 4.05 s), so the cover rule
    # correctly refuses them — they need a fresh ranged download.
    assert sum(1 for hit in hits.values() if hit) == 4
    assert hits[("YzFGkdPmDZU", 98.12)] is not None
    assert hits[("YzFGkdPmDZU", 98.12)]["tolerant_hit"] is True
    assert hits[("YzFGkdPmDZU", 98.12)]["coverage"] == 1.0
    assert hits[("-1jOqORJXr4", 0.0)] is not None
    assert hits[("ZdFXSbQWa4w", 637.8)] is not None
    assert hits[("qPKAtMPt40k", 563.48)] is not None
    assert hits[("2gEmYKeziek", 2208.08)] is None
    assert hits[("YzFGkdPmDZU", 166.8)] is None


# ---------------------------------------------------------------------------
# Job 48cb2324 (GTA VI, Nov 19): prep banked 7 verified cuts and injection
# fired (6 candidates / 5 scenes), yet the final manifest had 0 YouTube
# clips. Two breaks in the same lane, both in youtube_pipeline_v2.py:
#
# 1. Assignment: an injected cut scores -5.0 (empty title/description/
#    channel) against ~+187 for a live candidate with matching terms, so
#    it lost every assignment and the lane re-downloaded live candidates
#    over a 343 s branch wall it could not fit (0/11 resolved).
# 2. Consumption: prep_cache_path was stamped on the candidate but never
#    read anywhere, so even a winning injected cut re-downloaded over the
#    network — and get_verified_cut could not substitute (its cover rule
#    refuses a cut shorter than the live scene; at the real GTA live
#    durations, 9.5-12.55 s, it returned None for all five mapped cuts).
# ---------------------------------------------------------------------------

def test_injected_cut_is_primary_over_higher_scoring_live_candidate(panama_env):
    """A mapped injected cut is a verified local file (zero network
    cost) and must be its scene's primary, not a last-ranked backstop.
    The higher-scoring live candidate stays a ranked alternate."""
    pools = {}
    inject_prep_manifest_candidates(pools, _live_rows(), TITLE, list(LIVE_BEATS))
    live_like = {
        "video_id": "live-winner", "title": "Panama Canal documentary footage",
        "description": "Panama Canal transit", "channel": "Documentary Channel",
        "duration": 600,
    }
    assert _score_candidate(dict(live_like), "Panama Canal", LIVE_BEATS[0], "") > -5.0
    pools["primary panama canal"].append(live_like)
    assignments = _assign_candidates(
        _live_rows(), pools, list(LIVE_DURATIONS),
        lenient_acceptance=True, allow_same_source_reuse=True, prefer_distinct_sources=True,
    )
    assert assignments[0] is not None
    assert assignments[0]["prep_manifest_injected"] is True
    assert assignments[0]["video_id"] == "YzFGkdPmDZU"
    assert assignments[0]["best_timestamp"] == pytest.approx(98.12, abs=0.01)
    ranked = assignments[0].get("ranked_options") or []
    assert any(item.get("video_id") == "live-winner" for item in ranked)


def test_get_youtube_clips_restores_prep_cuts_without_network(panama_env, monkeypatch, tmp_path):
    """The download must copy the banked prep file for an injected
    candidate instead of re-downloading it: _download_clip (and the
    probe) must never be called for the five mapped scenes, and the
    resolved files must be byte-identical copies of the banked cuts."""
    monkeypatch.setattr(
        "services.youtube_pipeline_v2._search_container_pools",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "services.youtube_ranged_moments.prepare_ranged_moments",
        lambda *args, **kwargs: ({}, {"status": "disabled"}),
    )

    def _no_network(*args, **kwargs):
        raise AssertionError("network download must not run for a prep cut")

    monkeypatch.setattr("services.youtube_pipeline_v2._download_clip", _no_network)
    monkeypatch.setattr("services.youtube_pipeline_v2._download_youtube_probe", _no_network)
    monkeypatch.setattr(
        "services.youtube_pipeline_v2._screen_downloaded_motion",
        lambda *args, **kwargs: {},
    )

    def _fake_overlay_clean(path, **kwargs):
        return str(path), {
            "checked": True, "clean": True, "overlay_policy": "preserve_source_no_blur",
            "overlay_types": [], "rejection_reason": "", "regions": [],
            "watermark_prob": 0.0, "watermark_tolerated": False, "has_subtitle": False,
            "blurred": False, "blur_failed": False, "talking_head": False,
            "talking_head_confidence": 0.0, "facecam_inset": False,
            "facecam_confidence": 0.0, "facecam_reason": "", "facecam_corner": "",
            "facecam_tolerated": False, "source_overlays_preserved": False,
        }

    monkeypatch.setattr(
        "services.youtube_pipeline_v2._clean_downloaded_clip_overlays", _fake_overlay_clean
    )
    monkeypatch.delenv("VIDZEO_YT_V2_SKIP_DOWNLOAD", raising=False)
    script = " ".join(LIVE_BEATS)

    result = get_youtube_clips(
        script=script,
        title=ANCHOR,
        prep_manifest_title=TITLE,
        num_scenes=len(LIVE_BEATS),
        scene_texts=list(LIVE_BEATS),
        scene_durations=list(LIVE_DURATIONS),
        openrouter_api_key="",
        use_authenticated_session=False,
        output_dir=str(tmp_path / "yt-out"),
    )
    paths = list(result or [])
    assert sum(1 for path in paths if path) == 5
    audit = getattr(result, "audit", {}) or {}
    attempts = audit.get("attempts") or {}
    restored = sorted(
        int(index) for index, rows in attempts.items()
        if any(row.get("prep_cache_restored") for row in rows)
    )
    assert restored == [0, 1, 2, 5, 6]
    expected_bytes = _fake_cut_bytes()
    for index in restored:
        assert Path(paths[index]).read_bytes() == expected_bytes


# ---------------------------------------------------------------------------
# Run-3 title bug: the YT branch received yt_title = topic anchor
# ("Panama Canal", app.py:11303) instead of the full job title, so
# load_manifest_for_title found no manifest and injection never fired.
# Real run-3 data: 17 prep scenes, 11 verified YouTube cuts, 13 live
# scene texts (from the run-3 job's edit plan narrations).
# ---------------------------------------------------------------------------

ANCHOR = "Panama Canal"

# Real run-3 prep scene texts (manifest `scenes`, 1-indexed).
RUN3_PREP_SCENES = {1: 'Cross from the Pacific to the Caribbean in just two hours through the Panama Canal.', 2: 'Two oceans two hours one canal.', 3: 'That is the promise of this crossing and the question of this film is what two hours between oceans does to world trade.', 4: 'Panamá is a narrow strip of land with two coasts and more than two thousand nine hundred kilometres of coastline', 5: 'the Caribbean on one side and the Pacific on the other.', 6: 'The canal runs straight through it joining the two oceans in a single transit.', 7: 'The canal is the passage.', 8: 'Without it the two oceans are separated by the land itself', 9: 'and every crossing between them must go another way.', 10: 'With it the passage takes two hours.', 11: 'Panamá today is known far beyond the canal.', 12: 'Panama City has been recognised as a UNESCO Creative City of Gastronomy since 2017', 13: 'the country was chosen Best Tourist Destination in Latin America and South America for a third consecutive year', 14: "and Chiriquí Province was highlighted in Lonely Planet's Best in Travel for 2025.", 15: 'So how did the Panama Canal change world trade?', 16: 'It put two oceans two hours apart.', 17: 'That crossing two hours from the Pacific to the Caribbean is the change itself.'}

# Real run-3 prep YouTube entries: (prep scene, video_id, start, duration).
RUN3_PREP_CUTS = [(1, 'YzFGkdPmDZU', 98.119, 6.75), (2, 'tAAi4IWlW9A', 15.065, 3.0), (3, 'YzFGkdPmDZU', 166.8, 10.35), (7, 'qPKAtMPt40k', 563.48, 3.0), (8, 'tAAi4IWlW9A', 15.065, 4.95), (9, 'YzFGkdPmDZU', 166.8, 4.05), (11, 'tAAi4IWlW9A', 19.76, 3.6), (13, 'GRDGQERSmDE', 1237.2, 8.1), (15, '39_4MuOX_2U', 405.095, 4.05), (16, 'iGsQsk5vMBA', 70.2, 3.15), (17, 'zCx7_VzR1Ho', 564.8, 6.3)]

# Real run-3 live scene texts (13, from the job's edit-plan narrations).
RUN3_LIVE_TEXTS = ['Cross from the Pacific to the Caribbean in just two hours through the Panama Canal. ', 'Two oceans, two hours, one canal. ', 'That is the promise of this crossing, and the question of this film is what two hours between oceans does to world trade. \n\n', 'Panamá is a narrow strip of land with two coasts and more than two thousand nine hundred kilometres of coastline, the Caribbean on one side and the Pacific on the other. ', 'The canal runs straight through it, joining the two oceans in a single transit. \n\n', 'The canal is the passage. ', 'Without it, the two oceans are separated by the land itself, and every crossing between them must go another way. ', 'With it, the passage takes two hours. \n\n', 'Panamá today is known far beyond the canal. ', "Panama City has been recognised as a UNESCO Creative City of Gastronomy since 2017, the country was chosen Best Tourist Destination in Latin America and South America for a third consecutive year, and Chiriquí Province was highlighted in Lonely Planet's Best in Travel for 2025. \n\n", 'So how did the Panama Canal change world trade? ', 'It put two oceans two hours apart. ', 'That crossing, two hours from the Pacific to the Caribbean, is the change itself.']


@pytest.fixture()
def panama_run3_env(tmp_path, monkeypatch):
    """A tmp prep root with the real run-3 Panama manifest (11 cuts)."""
    prep_root = tmp_path / "prep"
    cache_dir = tmp_path / "yt-cuts"
    monkeypatch.setenv("VIDZEO_PREP_ROOT", str(prep_root))
    monkeypatch.setenv("VIDZEO_YT_CUT_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("VIDZEO_YT_PREP_MANIFEST_INJECTION", "1")
    title_dir = prep_root / "titles" / ("2" * 64)
    title_dir.mkdir(parents=True)
    entries = []
    for scene, video_id, start, duration in RUN3_PREP_CUTS:
        stored = put_verified_cut(
            video_id, start, duration,
            str(_write_cut(tmp_path, video_id, start)),
            metadata={"prep_title": "panama-run3"},
        )
        assert stored, f"could not bank {video_id} {start}"
        entries.append({
            "scene": scene, "video_id": video_id, "start": start,
            "duration": duration, "query": "Panama Canal documentary footage",
            "path": stored, "scaled_path": None, "verification": "cache",
        })
    manifest = {
        "schema": "vidzeo_prep_manifest_v1",
        "title": TITLE,
        "title_key": "2" * 64,
        "duration_minutes": 1.0,
        "status": "ready",
        "scenes": [
            {"index": idx, "text": text, "words": len(text.split())}
            for idx, text in sorted(RUN3_PREP_SCENES.items())
        ],
        "youtube": {"attempted": 17, "verified": 11, "entries": entries},
        "web": {"attempted": 0, "verified": 0, "entries": []},
    }
    (title_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return {"prep_root": prep_root, "cache_dir": cache_dir, "manifest": manifest}


def test_run3_anchor_title_misses_full_title_injects_nine(panama_run3_env):
    """Run-3 repro, pinned: the anchor looked up no manifest (0 injected);
    the full job title injects 9 cuts across 9 live scenes.

    The 2 skipped entries are the prep scenes 8/9 cuts: both map to
    live scene 6 (the concatenated "Without it…" text) and fail the
    0.5 overlap / 0.10 margin gate.
    """
    rows = _build_scene_rows(ANCHOR, list(RUN3_LIVE_TEXTS))
    anchor_audit = inject_prep_manifest_candidates(
        {}, rows, ANCHOR, list(RUN3_LIVE_TEXTS)
    )
    assert anchor_audit["enabled"] is False
    assert anchor_audit["injected"] == 0
    assert anchor_audit["reason"] == "no_mappable_prep_entries"

    pools = {}
    audit = inject_prep_manifest_candidates(
        pools, rows, TITLE, list(RUN3_LIVE_TEXTS)
    )
    assert audit["enabled"] is True
    assert audit["injected"] == 9
    assert sorted(audit["scenes"]) == [0, 1, 2, 5, 8, 9, 10, 11, 12]
    assert audit["skipped"] == 2
    assert len(pools["primary panama canal"]) == 9


def test_get_youtube_clips_uses_prep_manifest_title(panama_env, monkeypatch, capsys):
    """The live code path: get_youtube_clips(title=<anchor>) must inject
    from the manifest when prep_manifest_title carries the full title,
    and inject nothing (but still log the audit) without it.

    Search is stubbed to {} (the run-3 total search outage) and
    downloads are skipped, so this runs offline with no network.
    """
    monkeypatch.setattr(
        "services.youtube_pipeline_v2._search_container_pools",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setenv("VIDZEO_YT_V2_SKIP_DOWNLOAD", "1")
    script = " ".join(LIVE_BEATS)

    result = get_youtube_clips(
        script=script,
        title=ANCHOR,
        prep_manifest_title=TITLE,
        num_scenes=len(LIVE_BEATS),
        scene_texts=list(LIVE_BEATS),
        scene_durations=list(LIVE_DURATIONS),
        openrouter_api_key="",
        use_authenticated_session=False,
    )
    out = capsys.readouterr().out
    assert "[yt-v2] prep-manifest injection: 6 candidate(s)" in out
    assert "lookup_title='How the Panama Canal Changed World Trade'" in out
    # All 6 injected cuts are the only candidates, and 5 live scenes
    # take one as metadata winner.
    assert getattr(result, "audit", {}).get("metadata_selected") == 5
    assert getattr(result, "audit", {}).get("download_skipped") is True

    result = get_youtube_clips(
        script=script,
        title=ANCHOR,
        num_scenes=len(LIVE_BEATS),
        scene_texts=list(LIVE_BEATS),
        scene_durations=list(LIVE_DURATIONS),
        openrouter_api_key="",
        use_authenticated_session=False,
    )
    out = capsys.readouterr().out
    # A 0-injection result is logged now (the run-3 silent no-fire).
    assert "[yt-v2] prep-manifest injection: 0 candidate(s)" in out
    assert "reason=no_mappable_prep_entries" in out
    assert "lookup_title='Panama Canal'" in out
    assert getattr(result, "audit", {}).get("metadata_selected") == 0


def test_build_clip_pools_uses_prep_manifest_title(panama_env, monkeypatch, capsys):
    """Same threading for build_clip_pools (scene-chain pool path)."""
    from services.youtube_pipeline_v2 import build_clip_pools

    monkeypatch.setattr(
        "services.youtube_pipeline_v2._search_container_pools",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "services.youtube_pipeline_v2._prepare_pool_transcript_moments",
        lambda *args, **kwargs: ({}, {}, {}),
    )
    pools = build_clip_pools(
        ANCHOR, list(LIVE_BEATS),
        prep_manifest_title=TITLE,
        scene_durations=list(LIVE_DURATIONS),
        use_authenticated_session=False,
    )
    out = capsys.readouterr().out
    assert "[yt-v2] prep-manifest injection: 6 candidate(s)" in out
    assert sum(len(v) for v in pools.pools.values()) == 6
    assert sum(1 for item in pools.assignments if item) == 5


# ---------------------------------------------------------------------------
# Run-4 adapter title bug: the mixed-media adapter rewrote the title to the
# topic anchor ("Panama Canal", pipeline_v2_media_adapter.py:4100-4101)
# before calling the YouTube runner (line 5088), so the runner's ``title``
# argument — which the run-3 fix used as ``prep_manifest_title`` — was
# already the anchor. The run-4 audit line DID appear but read
# lookup_title='Panama Canal', 0 injected. The adapter must pass its own
# ``original_title`` (line 4098) as ``prep_manifest_title``; the branch
# must forward it to the in-process call and the child-process payload.
# Real run-4 data: same title/script as run 3 (22 prep cuts, 11 scenes);
# the run-3 fixture (11 cuts, 9 injectable) is the offline replay.
# ---------------------------------------------------------------------------

def _panama_adapter_scenes(texts, durations):
    return [
        {"scene_id": f"panama-{index}", "order": index, "scene_index": index,
         "duration": durations[index], "text": texts[index]}
        for index in range(len(texts))
    ]


def _panama_adapter_config(tmp_path):
    return {
        "image_source": "Mixed Media",
        "visual_understanding": False,
        "global_media_guarantee": False,
        "content_title": TITLE,
        "_run_temp_dir": str(tmp_path),
        # Full YouTube quota so every mapped live scene reaches the lane.
        "youtube_accepted_coverage_target": 1,
        "yt_use_authenticated_session": False,
    }


def _drive_youtube_lane_only(monkeypatch, lane_results):
    """Patch the adapter coordinator so only the YouTube lane runs."""
    import threading

    import services.pipeline_v2_media_adapter as adapter

    def coordinator(rows, **kwargs):
        lane_results.append(kwargs["youtube_lane"](rows[0], threading.Event()))
        return {"media_paths": [], "source_labels": [], "metadata": {}}, {}

    monkeypatch.setattr(adapter, "resolve_and_build_media_plan", coordinator)


def test_adapter_passes_original_title_as_prep_manifest_title(monkeypatch, tmp_path):
    """The adapter's runner call carries the full title separately from
    the (rewritten) search title. Regression: before the fix the runner
    received no prep_manifest_title at all, so the branch fell back to
    its title argument — the anchor — and injection found 0."""
    import threading  # noqa: F401  (kept explicit: the lane needs an Event)
    import services.pipeline_v2_media_adapter as adapter

    calls = []

    def runner(texts, durations, config, *args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs, "texts": list(texts),
                      "config": dict(config or {})})
        return []

    lane_results = []
    _drive_youtube_lane_only(monkeypatch, lane_results)
    adapter.resolve_existing_provider_branches_v2(
        _panama_adapter_config(tmp_path),
        {"scene_contracts": _panama_adapter_scenes(list(LIVE_BEATS), list(LIVE_DURATIONS))},
        title=TITLE, script=" ".join(LIVE_BEATS),
        render_resolution=(960, 540), youtube_runner=runner,
        web_runner=lambda *a, **k: {}, stock_runner=lambda *a, **k: {},
        snapshotter=lambda paths, **k: list(paths),
        neutral_asset_factory=lambda *a, **k: None, report_progress=lambda *a: None,
    )
    assert calls, "the YouTube lane never ran"
    # The Track B general-pool build also calls the runner, with broad
    # topic queries and no prep manifest title by design (fresh topic
    # search only). The title assertion below applies to the primary
    # scene-by-scene lane calls only.
    primary_calls = [
        call for call in calls
        if not (call["config"] or {}).get("_youtube_general_pool_build")
    ]
    assert primary_calls, "the YouTube lane never ran"
    for call in primary_calls:
        # Positional title arg stays the search title (topic anchor).
        assert call["args"][0] == ANCHOR
        # The new kwarg carries the full job title for the manifest lookup.
        assert call["kwargs"]["prep_manifest_title"] == TITLE


def test_branch_forwards_prep_manifest_title_to_get_youtube_clips(monkeypatch, tmp_path):
    """The branch forwards an adapter-supplied full title to
    get_youtube_clips (in-process path) while the search title stays
    the anchor; without the kwarg it falls back to the search title."""
    from unittest import mock

    import app as server_app

    captured = {}

    def fake_get_youtube_clips(**kwargs):
        captured.update(kwargs)
        return [None]

    config = {
        "image_source": "Mixed Media",
        "_run_temp_dir": str(tmp_path),
        "_topic_anchor": ANCHOR,
        "_skip_sync_topic_anchor": True,
        "_semantic_query_lock": True,
        "_semantic_query_rows": [
            {"youtube_query": "Panama Canal documentary footage"}
        ],
    }
    with mock.patch(
        "services.youtube_clips_service.get_youtube_clips",
        side_effect=fake_get_youtube_clips,
    ):
        server_app._run_youtube_clips_branch(
            [LIVE_BEATS[0]], [LIVE_DURATIONS[0]], dict(config),
            ANCHOR, LIVE_BEATS[0], prep_manifest_title=TITLE,
        )
    assert captured["prep_manifest_title"] == TITLE
    assert captured["title"] == ANCHOR

    captured.clear()
    with mock.patch(
        "services.youtube_clips_service.get_youtube_clips",
        side_effect=fake_get_youtube_clips,
    ):
        server_app._run_youtube_clips_branch(
            [LIVE_BEATS[0]], [LIVE_DURATIONS[0]], dict(config),
            ANCHOR, LIVE_BEATS[0],
        )
    # No adapter title: the old behaviour (anchor lookup, 0 injected).
    assert captured["prep_manifest_title"] == ANCHOR


def test_adapter_live_path_injects_prep_cuts_with_full_title(
    panama_run3_env, monkeypatch, tmp_path, capsys,
):
    """Run-4 offline repro through the REAL live path: adapter (Mixed
    Media) -> _run_youtube_clips_branch -> real get_youtube_clips, with
    search stubbed to {} (the run-3 outage) and downloads skipped.
    The audit line must now show the FULL title and 9 injected cuts
    must reach the live pool — the exact line run 4 showed as
    lookup_title='Panama Canal', 0 injected. With downloads skipped
    (offline) no clip file exists, so the lane rows stay empty; the
    observable proof is metadata_selected == 9 in the branch audit:
    the 9 injected cuts are the metadata winners for their 9 mapped
    scenes (before the _assign_candidates prep exemption below, the
    strict adapter requirements rejected every injected cut and this
    was 0 despite 9 injected)."""
    import app as server_app
    import services.pipeline_v2_media_adapter as adapter

    monkeypatch.setattr(
        "services.youtube_pipeline_v2._search_container_pools",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setenv("VIDZEO_YT_V2_SKIP_DOWNLOAD", "1")
    lane_results = []
    _drive_youtube_lane_only(monkeypatch, lane_results)
    media_plan, _resolutions, _visual_context = adapter.resolve_existing_provider_branches_v2(
        _panama_adapter_config(tmp_path),
        {"scene_contracts": _panama_adapter_scenes(
            list(RUN3_LIVE_TEXTS), list(LIVE_DURATIONS))},
        title=TITLE, script=" ".join(RUN3_LIVE_TEXTS),
        render_resolution=(960, 540),
        youtube_runner=server_app._run_youtube_clips_branch,
        web_runner=lambda *a, **k: {}, stock_runner=lambda *a, **k: {},
        snapshotter=lambda paths, **k: list(paths),
        neutral_asset_factory=lambda *a, **k: None, report_progress=lambda *a: None,
    )
    out = capsys.readouterr().out
    assert "[yt-v2] prep-manifest injection: 9 candidate(s) for 9 scene(s)" in out
    assert "lookup_title='How the Panama Canal Changed World Trade'" in out
    assert lane_results, "the YouTube lane never ran"
    youtube_audit = (
        (media_plan or {}).get("metadata", {}).get("provider_audit", {}).get("youtube", {})
    )
    assert youtube_audit.get("metadata_selected") == 9
    assert youtube_audit.get("download_skipped") is True


def test_assign_candidates_keeps_prep_cuts_under_strict_requirement(panama_env):
    """The live path's rows carry strict visual requirements; an injected
    cut has no title/channel metadata to match one, so the requirement
    gate in _assign_candidates used to reject every injected cut before
    scoring (9 injected, 0 assigned). Prep cuts are exempt: they were
    verified at prep time and stay subject to download/visual gates."""
    pools = {}
    rows = _live_rows()
    strict_requirement = {
        "schema_version": "visual_requirement.v2",
        "canonical_entity": "Panama Canal",
        "subject_type": "named_entity",
        "domain": "documentary",
        "required_terms": ["Panama Canal"],
        "strict_identity": True,
    }
    for row in rows:
        row["visual_requirement"] = dict(strict_requirement)
    audit = inject_prep_manifest_candidates(pools, rows, TITLE, list(LIVE_BEATS))
    assert audit["injected"] == 6
    assignments = _assign_candidates(
        rows, pools, list(LIVE_DURATIONS),
        lenient_acceptance=True, allow_same_source_reuse=True, prefer_distinct_sources=True,
    )
    assigned = {idx for idx, item in enumerate(assignments) if item}
    assert assigned == {0, 1, 2, 5, 6}
    for idx in sorted(assigned):
        assert assignments[idx]["prep_manifest_injected"] is True


def test_branch_without_prep_manifest_title_still_injects_zero(
    panama_run3_env, monkeypatch, tmp_path, capsys,
):
    """Control (must not regress): the anchor-only path still injects 0
    and logs the anchor lookup — the fix is the full title reaching the
    lookup, not the anchor suddenly matching."""
    import app as server_app

    monkeypatch.setattr(
        "services.youtube_pipeline_v2._search_container_pools",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setenv("VIDZEO_YT_V2_SKIP_DOWNLOAD", "1")
    result = server_app._run_youtube_clips_branch(
        list(RUN3_LIVE_TEXTS), list(LIVE_DURATIONS),
        {
            "image_source": "Mixed Media",
            "_run_temp_dir": str(tmp_path),
            "_topic_anchor": ANCHOR,
            "_skip_sync_topic_anchor": True,
            "_semantic_query_lock": True,
            "_semantic_query_rows": [
                {"youtube_query": "Panama Canal documentary footage"}
                for _ in RUN3_LIVE_TEXTS
            ],
            "yt_use_authenticated_session": False,
        },
        ANCHOR, " ".join(RUN3_LIVE_TEXTS),
    )
    out = capsys.readouterr().out
    assert "[yt-v2] prep-manifest injection: 0 candidate(s)" in out
    assert "lookup_title='Panama Canal'" in out
    assert getattr(result, "audit", {}).get("metadata_selected") == 0
