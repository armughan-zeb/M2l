"""YT live-download volume (Worker A, run-7 fix).

Run 7 (job 6624244a, GTA VI 1-min, HEAD 7d4e1eb): the 343 s branch wall
priced search (~35 s) plus three 80 s transfer waves and nothing else,
while the worker ran search (~55 s), assignment (~150-170 s) and
transcripts (up to 120 s) first -- the download pass started with ~55 s
left, completed 0 live downloads, and the coordinator routed the quota
scenes to web. YT accepted fell 46.2% (run 6) -> 23.1% (run 7).

The fix, all in services/youtube_pipeline_v2.py:
- assignment is made cheap (``re`` cache room for the ~679-pattern
  working set, a ``_score_candidate`` memo, a niche-taxonomy memo, a
  ``_norm`` memo) so it stops eating the wall;
- the download phase keeps its own reserve, subtracted from the
  transcript and search budgets instead of sharing one wall;
- injection skips are audited per entry, so a banked cut whose target
  scene is in no worker's set (run 7: GDuSN0-brZs -> live scene 11) is
  visible instead of a bare count.

No network, no live server, no render.
"""

import json
import os
import re
from pathlib import Path

import pytest

import services.youtube_pipeline_v2 as yt
from services.youtube_media_cache import put_verified_cut
from services.youtube_pipeline_v2 import (
    _assign_candidates,
    _build_scene_rows,
    _norm,
    _row_pool_key,
    _score_candidate,
    _search_container_pools,
    _yt_download_reserve_seconds,
    _yt_download_slot_seconds,
    _yt_ranged_transcript_deadline_seconds,
    get_youtube_clips,
    inject_prep_manifest_candidates,
)

TITLE = "Grand Theft Auto VI: Return to Vice City"

# Real run-7 live scene texts, scenes 0-10 (the main YT worker's set;
# scene 11 is the one no YT worker covered). From the run-7 manifest
# source_scene_texts, verbatim.
LIVE_TEXTS = [
    "After a thirteen-year wait, Grand Theft Auto VI arrives today, November 19, 2026, on PlayStation 5 and Xbox Series XS, and it brings the series back to Vice City. \n\n",
    "The story starts with a score gone wrong. ",
    "Jason Duval and Lucia Caminos, a criminal duo in the fictional state of Leonida, are pulled into a state-wide conspiracy after a failed bank heist, and the two of them are left to protect each other. \n\n",
    "Jason served in the Army, then worked for drug runners in the Leonida Keys. ",
    "Lucia came from Liberty City, fought for her family, and served time in Leonida Penitentiary. ",
    "She is the series' first canon female protagonist, and the partnership takes its cue from Bonnie and Clyde. \n\n",
    "Rockstar began preliminary work in 2014, and principal production followed the launch of Red Dead Redemption 2 in 2018. ",
    "The game was confirmed in February 2022, unfinished footage leaked that September, and the formal reveal came in December 2023. ",
    "Two delays followed before release. \n\n",
    "Leonida is a fictional Florida: Vice City, based on Miami, the Grassrivers, and the Leonida Keys, on a map reported to be three times the size of Red Dead Redemption 2's playable area. \n\n",
    "It became the most preordered game of all time, with estimated sales of more than seventeen million digital copies and over one point six billion dollars in revenue before launch. ",
]

# Real run-7 prep scenes for the two entries in this fixture (manifest
# `scenes`, 1-indexed): prep 1 maps to live 0 with overlap 1.0; prep 22
# ("Ocarina of Time...") maps to live 11, which is in NO worker's set,
# so inside the 0-10 set its best match is live 10 at 3/7 = 0.4286 --
# below the 0.5 floor. That is the run-7 silent skip, replayed here.
PREP_SCENES = {
    1: "After a thirteen-year wait Grand Theft Auto VI arrives today",
    22: "Ocarina of Time as the highest-rated game on Metacritic",
}
PREP_CUTS = [
    (1, "c5qhgwwLY50", 0.0, 4.5),
    (22, "GDuSN0-brZs", 0.08, 4.05),
]


# One distinct candidate per live scene: the title carries that scene's
# own terms, so each scene's top scorer is a different video (as in the
# real 188-candidate pool). Identical candidates tie on score, one video
# wins every scene, and its timestamps exhaust after ~6 scenes in a 60 s
# video -- a fixture artefact, not a pipeline verdict. 600 s videos
# leave timestamp headroom as well.
SCENE_CANDIDATE_TITLES = [
    "Grand Theft Auto VI arrives today November 19 2026 PlayStation 5 Xbox Series Vice City",
    "Grand Theft Auto VI story score gone wrong",
    "Grand Theft Auto VI Jason Duval Lucia Caminos Leonida criminal duo bank heist",
    "Grand Theft Auto VI Jason Army drug runners Leonida Keys",
    "Grand Theft Auto VI Lucia Liberty City family Leonida Penitentiary",
    "Grand Theft Auto VI Lucia Jason Bonnie Clyde first female protagonist",
    "Grand Theft Auto VI Rockstar preliminary work 2014 Red Dead Redemption 2 production",
    "Grand Theft Auto VI confirmed February 2022 footage December 2023 reveal",
    "Grand Theft Auto VI two delays release",
    "Grand Theft Auto VI Leonida fictional Florida Vice City Miami Grassrivers map",
    "Grand Theft Auto VI most preordered game sales seventeen million revenue",
]


def _scene_candidate(idx):
    title = SCENE_CANDIDATE_TITLES[idx]
    return {
        "video_id": f"live{idx:02d}",
        "id": f"live{idx:02d}",
        "title": title,
        "description": title + " gameplay footage trailer",
        "channel": "Rockstar Games",
        "uploader": "Rockstar Games",
        "duration": 600.0,
        "query_text": "Grand Theft Auto VI trailer",
    }


def _fake_cut_bytes() -> bytes:
    # _valid_file / put_verified_cut need >= 8 KiB with an ftyp box.
    return b"\x00\x00\x00\x18ftypisom" + b"\x00" * 9000


def _fake_overlay_clean(*args, **kwargs):
    return {
        "cleaned": True, "blurred_frames": 0, "frames_checked": 0,
        "frames_blurred": 0, "facecam_tolerated": False,
        "source_overlays_preserved": False,
    }


@pytest.fixture()
def gta_env(tmp_path, monkeypatch):
    """A tmp prep root with the run-7 GTA manifest (2 banked cuts)."""
    prep_root = tmp_path / "prep"
    cache_dir = tmp_path / "yt-cuts"
    monkeypatch.setenv("VIDZEO_PREP_ROOT", str(prep_root))
    monkeypatch.setenv("VIDZEO_YT_CUT_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("VIDZEO_YT_PREP_MANIFEST_INJECTION", "1")
    title_dir = prep_root / "titles" / ("0" * 64)
    title_dir.mkdir(parents=True)
    entries = []
    for scene, video_id, start, duration in PREP_CUTS:
        cut_path = tmp_path / f"cut-{video_id}-{int(start)}.mp4"
        cut_path.write_bytes(_fake_cut_bytes())
        stored = put_verified_cut(
            video_id, start, duration, str(cut_path),
            metadata={"prep_title": "gta-vi"},
        )
        assert stored, f"could not bank {video_id} {start}"
        entries.append({
            "scene": scene, "video_id": video_id, "start": start,
            "duration": duration, "query": "Grand Theft Auto VI trailer",
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
        "youtube": {"attempted": 2, "verified": 2, "entries": entries},
        "web": {"attempted": 0, "verified": 0, "entries": []},
    }
    (title_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return {"prep_root": prep_root, "cache_dir": cache_dir, "manifest": manifest}


# ---------------------------------------------------------------------------
# Assignment cost: caches
# ---------------------------------------------------------------------------

def test_re_cache_has_room_for_taxonomy_working_set():
    # Importing the pipeline raises the ceiling; the ~679-pattern working
    # set of the clip classifiers must fit without clearing.
    assert re._MAXCACHE >= 8192


def test_norm_memo_returns_same_result_and_caches():
    first = _norm("Grand Theft Auto VI")
    second = _norm("Grand Theft Auto VI")
    assert first == second == "grand theft auto vi"
    assert "Grand Theft Auto VI" in yt._norm_cache
    assert _norm(None) == ""


def test_score_niches_memo_reuses_cache(monkeypatch):
    from services import universal_clip_classifier as ucc

    text = "Grand Theft Auto VI trailer gameplay Vice City Leonida UNIQUE-7A"
    before = len(yt._score_niches_cache)
    first = ucc.score_niches(text)
    assert len(yt._score_niches_cache) == before + 1
    second = ucc.score_niches(text)
    assert second == first
    assert len(yt._score_niches_cache) == before + 1
    # Kill-switch: the uncached call still scores, but caches nothing.
    monkeypatch.setenv("VIDZEO_YT_SCORE_NICHES_CACHE", "0")
    third = ucc.score_niches(text + " EXTRA")
    assert third == ucc.score_niches(text + " EXTRA")
    assert len(yt._score_niches_cache) == before + 1


def test_score_memo_same_score_and_mutations(monkeypatch):
    rows = _build_scene_rows(TITLE, LIVE_TEXTS[:1])
    ctx = yt._row_retrieval_context(rows[0])

    def _candidate():
        return {
            "video_id": "testvid123", "title": "Grand Theft Auto VI official trailer gameplay",
            "description": "Official Rockstar Games trailer for Grand Theft Auto VI set in Vice City Leonida",
            "channel": "Rockstar Games", "duration": 60.0,
            "query_text": "Grand Theft Auto VI trailer",
        }

    first = _score_candidate(_candidate(), "Grand Theft Auto VI", LIVE_TEXTS[0], "Grand Theft Auto VI trailer", ctx)
    second = _score_candidate(_candidate(), "Grand Theft Auto VI", LIVE_TEXTS[0], "Grand Theft Auto VI trailer", ctx)
    assert second == first
    # Mutations are replayed on a hit, not just the number: both dicts
    # must end up byte-identical after scoring.
    a, b = _candidate(), _candidate()
    _score_candidate(a, "Grand Theft Auto VI", LIVE_TEXTS[0], "Grand Theft Auto VI trailer", ctx)
    _score_candidate(b, "Grand Theft Auto VI", LIVE_TEXTS[0], "Grand Theft Auto VI trailer", ctx)
    assert a == b
    # Kill-switch: bypass still scores the same.
    monkeypatch.setenv("VIDZEO_YT_SCORE_CACHE", "0")
    third = _score_candidate(_candidate(), "Grand Theft Auto VI", LIVE_TEXTS[0], "Grand Theft Auto VI trailer", ctx)
    assert third == first


def test_assignment_on_real_run7_texts_selects_same_primaries():
    """The caches change cost, not verdicts: assignment over the real
    run-7 texts must pick the same primaries run 7 picked live."""
    # Rows carry the planner's per-scene subject (as in the live job);
    # without it the subject inference falls back to a per-scene noun
    # ("Liberty City") and the niche gate rejects the GTA VI cuts.
    overrides = [{"scene_subject": "Grand Theft Auto VI"}] * len(LIVE_TEXTS)
    rows = _build_scene_rows(TITLE, LIVE_TEXTS, overrides)
    pools = {}
    for idx, row in enumerate(rows):
        pools.setdefault(_row_pool_key(row), []).append(_scene_candidate(idx))
    assigned = _assign_candidates(
        rows, pools, [4.0] * len(LIVE_TEXTS),
        lenient_acceptance=True, allow_same_source_reuse=True,
        prefer_distinct_sources=True,
    )
    assert sum(1 for item in assigned if item) == len(LIVE_TEXTS)
    assert [item.get("video_id") for item in assigned] == [f"live{idx:02d}" for idx in range(len(LIVE_TEXTS))]


# ---------------------------------------------------------------------------
# Download reserve
# ---------------------------------------------------------------------------

def test_download_reserve_math(monkeypatch):
    # Run-7 numbers: 7 live scenes, 5 download workers, 66.7 s per-clip
    # stage value -> 2 waves x (66.7 + 20 merge/probe + 30 screening) + 10 handoff.
    monkeypatch.delenv("VIDZEO_YT_CLIP_GRACE_SECONDS", raising=False)
    monkeypatch.setenv("VIDZEO_YT_PER_CLIP_TIMEOUT", "66.7")
    assert _yt_download_slot_seconds() == pytest.approx(116.7)
    assert _yt_download_reserve_seconds(7, 5) == pytest.approx(243.4)
    assert _yt_download_reserve_seconds(0, 5) == 10.0
    # Default: 60 s transfer, 20 s merge/probe, and 30 s screening.
    monkeypatch.delenv("VIDZEO_YT_PER_CLIP_TIMEOUT", raising=False)
    assert _yt_download_slot_seconds() == pytest.approx(110.0)
    assert _yt_download_reserve_seconds(1, 5) == pytest.approx(120.0)


def test_transcript_deadline_respects_download_reserve(tmp_path, monkeypatch):
    """End to end: with an 11-scene job and a 325 s wall, the transcript
    deadline keeps its 120 s floor even though the download reserve
    (~300 s) would otherwise squeeze it to ~23 s.

    Run 9 superseded the run-7 invariant (transcripts + reserve fit
    inside the wall): the reserve ate the whole wall before transcripts
    started, the deadline floored at 15 s on all four YT workers, and
    transcript completion was 16/60 (27%) with ranged evidence on 7/45
    scenes. The reserve over-prices downloads (a full 110 s slot-wave
    per live scene, while cache restores cost ~0.15 s), so transcripts
    now borrow from it: the deadline is the 120 s floor, still capped
    at 120 s, and the branch hard wall still bounds the whole branch.
    Downloads remain best-effort after transcripts (stub downloads
    here complete instantly, so the fixture still resolves fully)."""
    cache_dir = tmp_path / "yt-cuts"
    monkeypatch.setenv("VIDZEO_YT_CUT_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("VIDZEO_PREP_ROOT", str(tmp_path / "empty-prep"))
    monkeypatch.setenv("VIDZEO_YT_PREP_MANIFEST_INJECTION", "1")
    monkeypatch.setenv("VIDZEO_YT_PER_CLIP_TIMEOUT", "66.7")
    monkeypatch.setenv("VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS", "120")
    recorded = {}

    def _recording_ranged(*args, **kwargs):
        recorded["deadline_seconds"] = kwargs.get("deadline_seconds")
        return {}, {"status": "stubbed", "scenes_with_moments": 0}

    monkeypatch.setattr(
        "services.youtube_ranged_moments.prepare_ranged_moments",
        _recording_ranged,
    )
    monkeypatch.setattr(
        "services.youtube_pipeline_v2._screen_downloaded_motion",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "services.youtube_pipeline_v2._clean_downloaded_clip_overlays",
        _fake_overlay_clean,
    )
    calls = []

    def _stub_download(candidate, output_path, start, duration, *args, **kwargs):
        calls.append(str(candidate.get("video_id") or ""))
        Path(output_path).write_bytes(_fake_cut_bytes())
        return True

    monkeypatch.setattr("services.youtube_pipeline_v2._download_clip", _stub_download)
    monkeypatch.setattr(
        "services.youtube_pipeline_v2._download_youtube_probe",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("probe must not run")),
    )
    rows = _build_scene_rows(TITLE, LIVE_TEXTS, [{"scene_subject": "Grand Theft Auto VI"}] * len(LIVE_TEXTS))
    pools = {}
    for idx, row in enumerate(rows):
        pools.setdefault(_row_pool_key(row), []).append(_scene_candidate(idx))

    def _stub_search(*args, **kwargs):
        return {key: list(value) for key, value in pools.items()}

    monkeypatch.setattr("services.youtube_pipeline_v2._search_container_pools", _stub_search)
    result = get_youtube_clips(
        script=" ".join(LIVE_TEXTS),
        title=TITLE,
        num_scenes=len(LIVE_TEXTS),
        scene_texts=list(LIVE_TEXTS),
        scene_durations=[4.0] * len(LIVE_TEXTS),
        scene_query_overrides=[{"scene_subject": "Grand Theft Auto VI"}] * len(LIVE_TEXTS),
        download_concurrency=5,
        openrouter_api_key="",
        use_authenticated_session=False,
        output_dir=str(tmp_path / "yt-out"),
        hard_timeout_seconds=325,
    )
    assert calls, "no downloads attempted"
    audit = getattr(result, "audit", {}) or {}
    ranged = audit.get("ranged_moment_selection", {})
    workers = audit["download_workers"]
    assert workers == 4
    # All 11 scenes are live (no prep injection in this fixture), so the
    # reserve is priced on 11 scenes, NOT on download attempts (a scene
    # retries alternates against stub bytes, so attempts outnumber scenes).
    reserve = _yt_download_reserve_seconds(len(LIVE_TEXTS), workers)
    assert ranged["download_reserve_seconds"] == pytest.approx(round(reserve, 1), abs=0.2)
    assert ranged["ranged_deadline_seconds"] == pytest.approx(recorded["deadline_seconds"], abs=0.2)
    # The run-9 policy: the transcript floor (120 s) holds even when
    # floor + reserve exceeds the wall. Four workers need three waves
    # for 11 scenes, so the old arithmetic gave ~23 s and run 9
    # hit the 15 s floor on every worker. The deadline is still capped
    # at 120 s (never unbounded).
    assert recorded["deadline_seconds"] == pytest.approx(120.0, abs=0.2)
    assert recorded["deadline_seconds"] <= 120.0
    assert audit.get("resolved", 0) == len(LIVE_TEXTS)
    assert len([path for path in (result or []) if path]) == len(LIVE_TEXTS)


def test_ranged_transcript_deadline_floor(monkeypatch):
    """The ranged transcript floor is the 300 s pipeline default
    (round 12: job 9ab2eb05 retained 0/8 transcripts at the 120 s
    deadline).

    Env override wins inside [30, 300]; the runtime .env value of 300
    is now the default itself, garbage falls back to 300, and a tiny
    configured value clamps up to the 30 s minimum floor.
    """
    monkeypatch.delenv("VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS", raising=False)
    assert _yt_ranged_transcript_deadline_seconds() == pytest.approx(300.0)
    monkeypatch.setenv("VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS", "300")
    assert _yt_ranged_transcript_deadline_seconds() == pytest.approx(300.0)
    monkeypatch.setenv("VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS", "45")
    assert _yt_ranged_transcript_deadline_seconds() == pytest.approx(45.0)
    monkeypatch.setenv("VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS", "10")
    assert _yt_ranged_transcript_deadline_seconds() == pytest.approx(30.0)
    monkeypatch.setenv("VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS", "not-a-number")
    assert _yt_ranged_transcript_deadline_seconds() == pytest.approx(300.0)


def test_search_wall_cap_clamps_timeout(monkeypatch):
    rows = _build_scene_rows(TITLE, LIVE_TEXTS[:1])
    groups = {_row_pool_key(rows[0]): rows}
    seen = {}

    def _stub_pass(*args, **kwargs):
        seen["search_timeout"] = kwargs.get("search_timeout")
        return False, {}

    monkeypatch.setattr("services.youtube_pipeline_v2._run_pool_search_pass", _stub_pass)
    _search_container_pools(groups, wall_cap_seconds=40)
    assert seen["search_timeout"] == pytest.approx(40.0)
    # Without the cap the same fan-out prices its rounds floor instead.
    _search_container_pools(groups)
    assert seen["search_timeout"] > 40.0


# ---------------------------------------------------------------------------
# Injection audit: the run-7 silent skip, replayed
# ---------------------------------------------------------------------------

def test_injection_skipped_entries_names_unmapped_cut(gta_env):
    rows = _build_scene_rows(TITLE, LIVE_TEXTS, [{"scene_subject": "Grand Theft Auto VI"}] * len(LIVE_TEXTS))
    pools = {}
    audit = inject_prep_manifest_candidates(pools, rows, TITLE, list(LIVE_TEXTS))
    assert audit["injected"] == 1
    assert audit["scenes"] == [0]
    injected_ids = [
        item.get("video_id")
        for items in pools.values() for item in items
        if item.get("prep_manifest_injected")
    ]
    assert injected_ids == ["c5qhgwwLY50"]
    # GDuSN0-brZs: its target (live 11) is in no worker's set; inside the
    # 0-10 set its best match is live 10 at 3/7 = 0.4286, below the 0.5
    # floor. The audit must say so per entry, not just count it.
    assert audit["skipped"] == 1
    skipped = {entry["video_id"]: entry for entry in audit["skipped_entries"]}
    assert "GDuSN0-brZs" in skipped
    entry = skipped["GDuSN0-brZs"]
    assert entry["reason"] == "below_overlap_floor"
    assert entry["prep_scene"] == 22
    assert entry["best_overlap"] == pytest.approx(0.4286, abs=0.001)
    assert entry["best_live_scene"] == 10
