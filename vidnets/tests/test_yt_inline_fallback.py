"""Worker B: inline per-scene YouTube fallback chain + audit gaps + pickle fix.

Covers services/youtube_pipeline_v2.py:

* the inline chain (perfect -> general related -> cached) runs inside the
  per-scene resolution flow, reusing the already-fetched pool / on-disk
  cache — never a second batch from scratch;
* the general layer still enforces the scene-relative game check (a GTA V
  clip stays out of a GTA VI trailer scene; a 2002 Vice City cutscene
  stays rejected everywhere);
* every injected candidate gets an attempt audit, even when never
  scheduled (run 6: scenes 10/11);
* a verified restored cut is the scene's final clip, and is retained
  (not deleted) when vision rejects it, so salvage can still consume it;
* the "cannot pickle 'generator' object" failure: yt-dlp stores DASH
  ``fragments`` as a LazyList over a generator, which ``copy.deepcopy``
  cannot copy — materialize it before any deepcopy / cache store.

No network, no live server, no render. The GTA replay at the end uses
the real run-6 scene texts and prep cuts (job 453e5590).
"""

import copy
import functools
import json
import os
import time
from pathlib import Path

import pytest
from yt_dlp.utils import LazyList

from services.youtube_media_cache import put_verified_cut
from services.youtube_pipeline_v2 import (
    _assign_candidates,
    _build_scene_rows,
    _materialize_youtube_info,
    _row_pool_key,
    _row_retrieval_context,
    _yt_ensure_injected_attempt_audits,
    _yt_layered_scene_options,
    _yt_restore_first_order,
    _youtube_cached_info,
    _youtube_download_with_cached_info,
    get_youtube_clips,
    inject_prep_manifest_candidates,
    make_resolve_state,
    resolve_clip_for_scene,
)

TITLE = "Grand Theft Auto VI: Return to Vice City"

# Real run-6 live scene texts (job 453e5590 manifest, source_scene_texts).
GTA_TEXTS = [
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
    "On release it earned universal critical acclaim, tying The Legend of Zelda: Ocarina of Time as the highest-rated game on Metacritic, with a score of 99. \n\n",
    "Vice City is open again , this time, across all of Leonida.",
]

# Real run-6 prep cuts: (prep scene, video_id, start, duration).
GTA_PREP_CUTS = [
    (1, "c5qhgwwLY50", 0.0, 4.5),
    (3, "TgtfearX668", 4513.36, 4.05),
    (5, "Bhwj-_9Xz6M", 574.08, 6.3),
    (12, "Qa40EwhWAFw", 3.2, 4.5),
    (18, "ZGyKOjovCOI", 671.76, 10.35),
    (20, "MArh6jOUu4g", 48.36, 9.45),
    (22, "GDuSN0-brZs", 0.08, 4.05),
]

# Real run-6 prep scene texts (manifest `scenes`, 1-indexed).
GTA_PREP_SCENES = {
    1: "After a thirteen-year wait Grand Theft Auto VI arrives today",
    2: "November 19, 2026, on PlayStation 5 and Xbox Series XS",
    3: "and it brings the series back to Vice City.",
    4: "The story starts with a score gone wrong.",
    5: "Jason Duval and Lucia Caminos a criminal duo in the fictiona",
    6: "are pulled into a state-wide conspiracy after a failed bank ",
    7: "and the two of them are left to protect each other.",
    8: "Jason served in the Army then worked for drug runners in the",
    9: "Lucia came from Liberty City fought for her family",
    10: "and served time in Leonida Penitentiary.",
    11: "She is the series' first canon female protagonist",
    12: "and the partnership takes its cue from Bonnie and Clyde.",
    13: "Rockstar began preliminary work in 2014 and principal produc",
    14: "The game was confirmed in February 2022 unfinished footage l",
    15: "and the formal reveal came in December 2023.",
    16: "Two delays followed before release.",
    17: "Leonida is a fictional Florida Vice City based on Miami",
    18: "the Grassrivers and the Leonida Keys on a map reported to be",
    19: "It became the most preordered game of all time",
    20: "with estimated sales of more than seventeen million digital ",
    21: "On release it earned universal critical acclaim tying The Le",
    22: "Ocarina of Time as the highest-rated game on Metacritic",
    23: "with a score of 99.",
    24: "Vice City is open again this time across all of Leonida.",
}


def _fake_cut_bytes() -> bytes:
    return b"\x00\x00\x00\x18ftypisom" + b"\x00" * 9000


def _live_candidate(video_id, title, duration=60.0, channel="Gameplay Channel"):
    return {
        "video_id": video_id,
        "id": video_id,
        "title": title,
        "description": title,
        "channel": channel,
        "uploader": channel,
        "duration": duration,
        "query_text": "Grand Theft Auto VI gameplay",
    }


def _injected_candidate(video_id, live_scene, start, cut_path, query="Grand Theft Auto VI gameplay"):
    return {
        "video_id": video_id,
        "id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        "title": "",
        "description": "",
        "channel": "",
        "uploader": "",
        "query_text": query,
        "prep_manifest_injected": True,
        "prep_best_timestamp": round(start, 2),
        "best_timestamp": round(start, 2),
        "prep_cut_duration": 4.5,
        "prep_scene": live_scene + 1,
        "prep_live_scene": live_scene,
        "prep_scene_overlap": 1.0,
        "prep_query": query,
        "prep_cache_path": str(cut_path),
        "prep_verification": "cache",
        "timestamp_evidence": "prep_manifest_verified_moment",
    }


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


@pytest.fixture()
def offline_env(tmp_path, monkeypatch):
    """No network, no ranged moments, deterministic motion/overlay gates."""
    cache_dir = tmp_path / "yt-cuts"
    monkeypatch.setenv("VIDZEO_YT_CUT_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("VIDZEO_YT_PREP_MANIFEST_INJECTION", "1")
    # Isolate from the REAL prep root on disk (~/workspace/vidnets-runtime
    # /temp/prep holds run-6's manifest): without this, the pipeline's
    # internal injection restores real GTA cuts into every fixture.
    monkeypatch.setenv("VIDZEO_PREP_ROOT", str(tmp_path / "empty-prep"))
    monkeypatch.setattr(
        "services.youtube_ranged_moments.prepare_ranged_moments",
        lambda *args, **kwargs: ({}, {"status": "disabled"}),
    )
    monkeypatch.setattr(
        "services.youtube_pipeline_v2._screen_downloaded_motion",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "services.youtube_pipeline_v2._clean_downloaded_clip_overlays", _fake_overlay_clean
    )
    monkeypatch.delenv("VIDZEO_YT_V2_SKIP_DOWNLOAD", raising=False)
    return {"cache_dir": cache_dir}


def _stub_download_clip(fail_vids, calls):
    """A _download_clip stub: fail the given vids, write fake bytes otherwise."""
    def _stub(candidate, output_path, start, duration, *args, **kwargs):
        vid = str(candidate.get("video_id") or "")
        calls.append(vid)
        if vid in fail_vids:
            return False
        Path(output_path).write_bytes(_fake_cut_bytes())
        return True

    return _stub


def _run_get_youtube_clips(monkeypatch, tmp_path, texts, pools, *, fail_vids=frozenset(), **kwargs):
    calls: list = []
    monkeypatch.setattr(
        "services.youtube_pipeline_v2._search_container_pools",
        lambda *args, **kwargs: {key: list(value) for key, value in pools.items()},
    )
    monkeypatch.setattr(
        "services.youtube_pipeline_v2._download_clip",
        _stub_download_clip(set(fail_vids), calls),
    )
    monkeypatch.setattr(
        "services.youtube_pipeline_v2._download_youtube_probe",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("probe must not run")),
    )
    started = time.monotonic()
    result = get_youtube_clips(
        script=" ".join(texts),
        title=TITLE,
        num_scenes=len(texts),
        scene_texts=list(texts),
        scene_durations=[4.0] * len(texts),
        openrouter_api_key="",
        use_authenticated_session=False,
        output_dir=str(tmp_path / "yt-out"),
        **kwargs,
    )
    elapsed = time.monotonic() - started
    return result, calls, elapsed


def _pools_for_rows(rows, candidates):
    pools = {}
    for row in rows:
        pools.setdefault(_row_pool_key(row), list(candidates))
    return pools


# ---------------------------------------------------------------------------
# Pickle fix: yt-dlp LazyList fragments
# ---------------------------------------------------------------------------

def test_materialize_youtube_info_exhausts_lazylist_fragments():
    """A LazyList over a generator (yt-dlp DASH ``fragments``) breaks
    ``copy.deepcopy`` with "cannot pickle 'generator' object" — the exact
    run-6 failure. Materializing first makes the info dict copyable, with
    the same fragment items, as plain lists."""
    info = {
        "id": "tJbzMqJGH4k",
        "formats": [
            {
                "format_id": "137",
                "fragments": LazyList(
                    {"url": f"https://example.invalid/seg{i}"} for i in range(3)
                ),
            },
            {"format_id": "140", "fragments": [{"url": "https://example.invalid/a"}]},
        ],
    }
    with pytest.raises(TypeError, match="cannot pickle 'generator' object"):
        copy.deepcopy(info)
    materialized = _materialize_youtube_info(info)
    assert isinstance(materialized["formats"], list)
    assert isinstance(materialized["formats"][0]["fragments"], list)
    assert [f["url"] for f in materialized["formats"][0]["fragments"]] == [
        "https://example.invalid/seg0",
        "https://example.invalid/seg1",
        "https://example.invalid/seg2",
    ]
    assert materialized["formats"][1]["fragments"] == [{"url": "https://example.invalid/a"}]
    # And now a deepcopy — the operation that used to raise — succeeds.
    assert copy.deepcopy(materialized)["formats"][0]["fragments"][2]["url"].endswith("seg2")


def test_materialize_youtube_info_leaves_live_partial_fragments_alone():
    """Live-stream ``fragments`` is a ``functools.partial`` (a callable
    yt-dlp still needs at download time), not a generator: it must pass
    through untouched, not be consumed or dropped."""
    marker = functools.partial(lambda x: x, 1)
    info = {"formats": [{"format_id": "live", "fragments": marker}]}
    materialized = _materialize_youtube_info(info)
    assert materialized["formats"][0]["fragments"] is marker


class _FakeYDL:
    """Minimal yt-dlp double: extract_info returns LazyList fragments,
    process_ie_result records, download records the plain-path fallback."""

    def __init__(self, *, fail_process=False):
        self.fail_process = fail_process
        self.processed = []
        self.downloaded = []
        self.info = {
            "id": "tJbzMqJGH4k",
            "formats": [
                {
                    "format_id": "137",
                    "fragments": LazyList(
                        {"url": f"https://example.invalid/seg{i}"} for i in range(2)
                    ),
                }
            ],
        }

    def extract_info(self, url, download=False):
        return self.info

    def process_ie_result(self, info, download=False):
        if self.fail_process:
            raise TypeError("cannot pickle 'generator' object")
        assert isinstance(info["formats"][0]["fragments"], list)
        self.processed.append(info["id"])

    def download(self, urls):
        self.downloaded.extend(urls)


def test_cached_download_survives_lazylist_fragments(monkeypatch):
    """First call extracts + stores (materialized), second call reuses the
    cached copy — neither raises, and the plain download path is never
    needed."""
    from services.youtube_pipeline_v2 import _YOUTUBE_INFO_CACHE

    _YOUTUBE_INFO_CACHE.clear()
    ydl = _FakeYDL()
    url = "https://www.youtube.com/watch?v=tJbzMqJGH4k"
    _youtube_download_with_cached_info(ydl, url, ["web"])
    _youtube_download_with_cached_info(ydl, url, ["web"])
    assert ydl.processed == ["tJbzMqJGH4k", "tJbzMqJGH4k"]
    assert ydl.downloaded == []
    assert _youtube_cached_info(url, ["web"]) is not None
    # The stored copy is materialized too: a later deepcopy of it works.
    assert _youtube_cached_info(url, ["web"])["formats"][0]["fragments"][1]["url"].endswith("seg1")


def test_cached_download_falls_back_to_plain_download(monkeypatch):
    """When the cached/process path still fails (e.g. a live partial the
    materializer deliberately leaves alone), the download falls back to
    yt-dlp's plain path instead of failing the scene as a 'format' error."""
    from services.youtube_pipeline_v2 import _YOUTUBE_INFO_CACHE

    _YOUTUBE_INFO_CACHE.clear()
    ydl = _FakeYDL(fail_process=True)
    url = "https://www.youtube.com/watch?v=tJbzMqJGH4k"
    _youtube_download_with_cached_info(ydl, url, ["web"])
    assert ydl.downloaded == [url]
    assert ydl.processed == []


# ---------------------------------------------------------------------------
# Inline chain: layer order, game gate, restore-first
# ---------------------------------------------------------------------------

def test_layered_options_order_and_exclusions(offline_env, tmp_path):
    """perfect ranked options first, then general pool candidates, then
    prep-cache restores; a GTA V clip and a 2002 Vice City cutscene never
    enter the chain for a GTA VI trailer scene; another scene's injected
    cut is not this scene's fallback."""
    texts = [GTA_TEXTS[2]]  # Jason/Lucia/Leonida conspiracy (GTA VI)
    rows = _build_scene_rows(TITLE, texts)
    cut_path = tmp_path / "prep-cut.mp4"
    cut_path.write_bytes(_fake_cut_bytes())
    perfect = _live_candidate("perfect1", "Grand Theft Auto VI Jason Lucia Leonida bank heist gameplay")
    alt = _live_candidate("alt1", "Grand Theft Auto VI Leonida conspiracy gameplay")
    general = _live_candidate("general1", "Grand Theft Auto VI gameplay")
    gta_v = _live_candidate("gtav1", "Grand Theft Auto V gameplay walkthrough")
    vice_2002 = _live_candidate("vc2002", "Grand Theft Auto: Vice City cutscene")
    injected = _injected_candidate("inj1", 0, 3.2, cut_path)
    other_scene_injected = _injected_candidate("inj-other", 1, 9.0, cut_path)
    pool = [perfect, alt, general, gta_v, vice_2002, injected, other_scene_injected]
    assignments = _assign_candidates(
        rows, _pools_for_rows(rows, pool), [4.0],
        lenient_acceptance=True, allow_same_source_reuse=True, prefer_distinct_sources=True,
    )
    assert assignments[0] is not None
    assert assignments[0]["video_id"] == "inj1"  # injected primary, unchanged
    # Hand-build the ranked options so the layering is deterministic
    # regardless of scorer ranking: injected + perfect + alt are the
    # ranked (perfect-layer) options; general is a 4th-ranked pool
    # candidate and must land in the general layer.
    assignment = dict(assignments[0])
    assignment["ranked_options"] = [dict(injected), dict(perfect), dict(alt)]
    layered = _yt_layered_scene_options(
        rows[0], pool, assignment,
        subject=rows[0].get("candidate_subject") or "",
        scene_text=texts[0],
        retrieval_context=_row_retrieval_context(rows[0]),
        lenient_acceptance=True,
    )
    vids = [str(item.get("video_id")) for item in layered]
    layers = [str(item.get("_yt_fallback_layer")) for item in layered]
    assert vids == ["inj1", "perfect1", "alt1", "general1"]
    assert layers == ["perfect", "perfect", "perfect", "general"]
    assert "gtav1" not in vids  # GTA V footage blocked for a VI scene
    assert "vc2002" not in vids  # 2002 Vice City cutscene rejected everywhere
    assert "inj-other" not in vids  # another scene's verified moment
    assert layers.index("general") > layers.index("perfect")


def test_restore_first_order_puts_prep_scenes_first():
    assignments = [
        {"video_id": "live1", "ranked_options": [{"video_id": "live1"}]},
        {"video_id": "inj1", "prep_cache_path": "/tmp/cut.mp4",
         "ranked_options": [{"video_id": "inj1", "prep_cache_path": "/tmp/cut.mp4"}]},
        None,
        {"video_id": "live2", "ranked_options": [{"video_id": "live2"}]},
    ]
    assert _yt_restore_first_order([0, 1, 3], assignments) == [1, 0, 3]


# ---------------------------------------------------------------------------
# Inline chain through get_youtube_clips (offline, stubbed downloads)
# ---------------------------------------------------------------------------

def test_inline_chain_perfect_fails_then_general_resolves(offline_env, monkeypatch, tmp_path):
    """One scene, four live candidates: the perfect winner + two ranked
    alternates fail their downloads, the 4th-ranked general GTA VI clip
    resolves inline. The audit records which layer resolved the scene."""
    texts = [GTA_TEXTS[2]]
    rows = _build_scene_rows(TITLE, texts)
    perfect = _live_candidate("perfect1", "Grand Theft Auto VI Jason Lucia Leonida bank heist gameplay")
    alt1 = _live_candidate("alt1", "Grand Theft Auto VI Leonida conspiracy gameplay")
    alt2 = _live_candidate("alt2", "Grand Theft Auto VI Jason Duval gameplay")
    general = _live_candidate("general1", "Grand Theft Auto VI gameplay")
    pool = _pools_for_rows(rows, [perfect, alt1, alt2, general])
    result, calls, _elapsed = _run_get_youtube_clips(
        monkeypatch, tmp_path, texts, pool, fail_vids={"perfect1", "alt1", "alt2"},
    )
    paths = list(result or [])
    assert len(paths) == 1 and paths[0]
    assert Path(paths[0]).read_bytes() == _fake_cut_bytes()
    audit = getattr(result, "audit", {}) or {}
    attempts = audit.get("attempts") or {}
    rows_audit = attempts.get("0") or []
    assert [row.get("candidate_id") for row in rows_audit] == ["perfect1", "alt2", "alt1", "general1"]
    assert [row.get("resolution_layer") for row in rows_audit] == [
        "perfect", "perfect", "perfect", "general",
    ]
    assert audit.get("resolution_layers", {}).get("general") == 1
    assert audit.get("resolution_layer_by_scene", {}).get("0") == "general"
    assert set(calls) == {"perfect1", "alt1", "alt2", "general1"}


def test_inline_chain_general_fails_then_cached_resolves(offline_env, monkeypatch, tmp_path):
    """Perfect + general live downloads fail; a general candidate with a
    banked transport-cache cut at its planned moment resolves from disk
    (layer "cached") with no network call for that video."""
    texts = [GTA_TEXTS[2]]
    rows = _build_scene_rows(TITLE, texts)
    perfect = _live_candidate("perfect1", "Grand Theft Auto VI Jason Lucia Leonida bank heist gameplay")
    general_a = _live_candidate("generalA", "Grand Theft Auto VI gameplay")
    general_b = _live_candidate("generalB", "Grand Theft Auto VI Leonida open world gameplay")
    pool = _pools_for_rows(rows, [perfect, general_a, general_b])
    # Learn generalB's planned moment from the offline assignment, then
    # bank a verified cut covering exactly that window.
    assignments = _assign_candidates(
        rows, pool, [4.0],
        lenient_acceptance=True, allow_same_source_reuse=True, prefer_distinct_sources=True,
    )
    ranked = assignments[0].get("ranked_options") or []
    moment = next(
        (float(item.get("best_timestamp")) for item in ranked if item.get("video_id") == "generalB"),
        None,
    )
    assert moment is not None
    cache_dir = offline_env["cache_dir"]
    cut_source = tmp_path / "banked-cut.mp4"
    cut_source.write_bytes(_fake_cut_bytes())
    stored = put_verified_cut("generalB", moment, 4.0, str(cut_source), metadata={})
    assert stored, "could not bank the transport-cache cut"
    result, calls, _elapsed = _run_get_youtube_clips(
        monkeypatch, tmp_path, texts, pool, fail_vids={"perfect1", "generalA"},
    )
    paths = list(result or [])
    assert len(paths) == 1 and paths[0]
    assert Path(paths[0]).read_bytes() == _fake_cut_bytes()
    audit = getattr(result, "audit", {}) or {}
    rows_audit = (audit.get("attempts") or {}).get("0") or []
    cached_rows = [row for row in rows_audit if row.get("candidate_id") == "generalB"]
    assert len(cached_rows) == 1
    assert cached_rows[0].get("resolution_layer") == "cached"
    assert cached_rows[0].get("cache_hit") is True
    assert audit.get("resolution_layer_by_scene", {}).get("0") == "cached"
    assert "generalB" not in calls  # restored from disk, never downloaded


def test_general_layer_rejects_wrong_game_footage(offline_env, monkeypatch, tmp_path):
    """The general layer must not launder wrong-game footage: with the
    perfect winner failing, the chain tries the GTA VI general clip and
    never downloads the GTA V clip or the 2002 Vice City cutscene."""
    texts = [GTA_TEXTS[9]]  # Leonida / Vice City map (GTA VI, not a V scene)
    rows = _build_scene_rows(TITLE, texts)
    perfect = _live_candidate("perfect1", "Grand Theft Auto VI Leonida Vice City map gameplay")
    general = _live_candidate("general1", "Grand Theft Auto VI gameplay")
    gta_v = _live_candidate("gtav1", "Grand Theft Auto V gameplay walkthrough")
    vice_2002 = _live_candidate("vc2002", "Grand Theft Auto: Vice City cutscene")
    pool = _pools_for_rows(rows, [perfect, general, gta_v, vice_2002])
    result, calls, _elapsed = _run_get_youtube_clips(
        monkeypatch, tmp_path, texts, pool, fail_vids={"perfect1"},
    )
    paths = list(result or [])
    assert len(paths) == 1 and paths[0]
    assert "gtav1" not in calls and "vc2002" not in calls
    assert "general1" in calls
    audit = getattr(result, "audit", {}) or {}
    tried = [row.get("candidate_id") for row in (audit.get("attempts") or {}).get("0") or []]
    assert "gtav1" not in tried and "vc2002" not in tried


def test_injected_candidate_never_attempted_gets_audit_row(offline_env, monkeypatch, tmp_path):
    """Run-6 gap (a): with a 1-scene download cap, the second injected
    scene never gets a pass — its injected cut must still get a
    not_attempted audit row (with WHY) and a per-scene attempts file."""
    texts = [GTA_TEXTS[0], GTA_TEXTS[2], GTA_TEXTS[5]]
    rows = _build_scene_rows(TITLE, texts)
    cut1 = tmp_path / "cut1.mp4"
    cut2 = tmp_path / "cut2.mp4"
    cut1.write_bytes(_fake_cut_bytes())
    cut2.write_bytes(_fake_cut_bytes())
    inj1 = _injected_candidate("inj1", 1, 3.2, cut1)
    inj2 = _injected_candidate("inj2", 2, 9.0, cut2)
    live = _live_candidate("live1", "Grand Theft Auto VI gameplay")
    pool = _pools_for_rows(rows, [live, inj1, inj2])
    result, _calls, _elapsed = _run_get_youtube_clips(
        monkeypatch, tmp_path, texts, pool, max_download_scenes=1,
    )
    paths = list(result or [])
    audit = getattr(result, "audit", {}) or {}
    attempts = audit.get("attempts") or {}
    # Restore-first ordering attempts the prep-mapped scene 1 first; the
    # cap then leaves scene 2 unattempted — but accounted for.
    assert audit.get("injected_not_attempted") == 1
    missing = attempts.get("2") or []
    assert len(missing) == 1
    assert missing[0].get("candidate_id") == "inj2"
    assert missing[0].get("status") == "not_attempted"
    assert missing[0].get("reason") == "download_scene_cap"
    assert missing[0].get("prep_manifest_injected") is True
    assert missing[0].get("resolution_layer") == "none"
    attempts_file = tmp_path / "yt-out" / "youtube_scene_003.attempts.json"
    assert attempts_file.is_file()
    assert json.loads(attempts_file.read_text(encoding="utf-8"))[0]["candidate_id"] == "inj2"
    # The attempted prep scene DID resolve from disk.
    assert paths[1] and Path(paths[1]).read_bytes() == _fake_cut_bytes()


def test_ensure_injected_audit_rows_are_idempotent(offline_env):
    """Calling the guarantee twice must not duplicate rows."""
    rows = _build_scene_rows(TITLE, [GTA_TEXTS[0]])
    cut = {"video_id": "inj1", "prep_manifest_injected": True,
           "prep_best_timestamp": 3.2, "prep_live_scene": 0,
           "prep_cache_path": "/tmp/cut.mp4", "prep_query": "x"}
    pools = {"primary liberty city": [cut]}
    from collections import defaultdict

    attempt_audit = defaultdict(list)
    added = _yt_ensure_injected_attempt_audits(
        attempt_audit, pools, [{"video_id": "inj1"}], stopped_reason="hard_timeout",
        attempted_indices=set(),
    )
    assert added == 1
    assert attempt_audit[0][0]["reason"] == "branch_wall"
    added_again = _yt_ensure_injected_attempt_audits(
        attempt_audit, pools, [{"video_id": "inj1"}], stopped_reason="hard_timeout",
        attempted_indices=set(),
    )
    assert added_again == 0
    assert len(attempt_audit[0]) == 1


# ---------------------------------------------------------------------------
# Consumed restore: resolve path + vision-reject retention + salvage
# ---------------------------------------------------------------------------

def test_resolve_clip_consumes_verified_restore(offline_env, monkeypatch, tmp_path):
    """The resolve path must restore an injected cut from disk instead of
    re-downloading it: _download_clip must never run for that video, and
    the scene's final clip is byte-identical to the banked cut."""
    from services.youtube_pipeline_v2 import ClipPools

    texts = [GTA_TEXTS[5]]  # Bonnie-and-Clyde scene (Qa40EwhWAFw in run 6)
    rows = _build_scene_rows(TITLE, texts)
    cut_path = tmp_path / "prep-cut.mp4"
    cut_path.write_bytes(_fake_cut_bytes())
    injected = _injected_candidate("Qa40EwhWAFw", 0, 3.2, cut_path)
    live = _live_candidate("live1", "Grand Theft Auto VI Bonnie Clyde gameplay")
    pools = _pools_for_rows(rows, [live, injected])
    assignments = _assign_candidates(
        rows, pools, [4.0],
        lenient_acceptance=True, allow_same_source_reuse=True, prefer_distinct_sources=True,
    )
    assert assignments[0]["video_id"] == "Qa40EwhWAFw"

    def _no_network(*args, **kwargs):
        raise AssertionError("network download must not run for a prep cut")

    monkeypatch.setattr("services.youtube_pipeline_v2._download_clip", _no_network)
    clip_pools = ClipPools(
        rows=rows, groups={}, pools=pools, assignments=assignments,
        scene_durations=[4.0],
    )
    state = make_resolve_state(clip_pools)
    out = resolve_clip_for_scene(
        0, clip_pools, state, visual_context=None,
        output_dir=str(tmp_path / "resolve-out"),
        use_authenticated_session=False,
    )
    assert out and Path(out).read_bytes() == _fake_cut_bytes()
    rows_log = [row for row in state.attempt_log if row.get("candidate_id") == "Qa40EwhWAFw"]
    assert len(rows_log) == 1
    assert rows_log[0].get("prep_cache_restored") is True
    assert rows_log[0].get("resolution_layer") == "cached"
    assert rows_log[0].get("status") == "downloaded"


def test_vision_rejected_restore_is_retained_and_salvaged(offline_env, monkeypatch, tmp_path):
    """Run-6 gap (b): a verified restore that vision rejects must NOT be
    deleted — it is retained, and the final salvage scan consumes it when
    every live alternate fails too, so the scene still ships the restore
    instead of a graphic."""
    texts = [GTA_TEXTS[5]]
    rows = _build_scene_rows(TITLE, texts)
    cut_path = tmp_path / "prep-cut.mp4"
    cut_path.write_bytes(_fake_cut_bytes())
    injected = _injected_candidate("Qa40EwhWAFw", 0, 3.2, cut_path)
    live = _live_candidate("live1", "Grand Theft Auto VI Bonnie Clyde gameplay")
    pool = _pools_for_rows(rows, [live, injected])

    class _StubVisualContext:
        enabled = True
        visual_required = True
        fail_closed = False

        def get_client(self):
            return None

    monkeypatch.setattr(
        "services.youtube_pipeline_v2._visual_verdict_scored",
        lambda *args, **kwargs: (False, 10.0, True, "reject"),
    )
    monkeypatch.setattr(
        "services.youtube_pipeline_v2._classify_visual_result",
        lambda accepted, score, available, reason: "pass" if accepted else "hard_fail",
    )
    result, calls, _elapsed = _run_get_youtube_clips(
        monkeypatch, tmp_path, texts, pool,
        fail_vids={"live1"},
        visual_context=_StubVisualContext(),
    )
    paths = list(result or [])
    assert len(paths) == 1 and paths[0]
    assert Path(paths[0]).read_bytes() == _fake_cut_bytes()
    audit = getattr(result, "audit", {}) or {}
    rows_audit = (audit.get("attempts") or {}).get("0") or []
    restored_rows = [row for row in rows_audit if row.get("candidate_id") == "Qa40EwhWAFw"]
    assert restored_rows, "restored cut has no audit row"
    assert restored_rows[0].get("prep_cache_restored") is True
    assert restored_rows[0].get("retained_after_visual_reject") is True
    assert audit.get("resolution_layer_by_scene", {}).get("0") == "cached"


# ---------------------------------------------------------------------------
# Offline replay: run-6 GTA VI data (job 453e5590)
# ---------------------------------------------------------------------------

@pytest.fixture()
def gta_prep_env(tmp_path, monkeypatch):
    """A tmp prep root with the real run-6 GTA manifest (7 verified cuts)
    and a tmp verified-cut cache, mirroring the real prep flow."""
    prep_root = tmp_path / "prep"
    cache_dir = tmp_path / "yt-cuts"
    monkeypatch.setenv("VIDZEO_PREP_ROOT", str(prep_root))
    monkeypatch.setenv("VIDZEO_YT_CUT_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("VIDZEO_YT_PREP_MANIFEST_INJECTION", "1")
    title_dir = prep_root / "titles" / ("3" * 64)
    title_dir.mkdir(parents=True)
    entries = []
    for scene, video_id, start, duration in GTA_PREP_CUTS:
        stored = put_verified_cut(
            video_id, start, duration,
            str(_write_gta_cut(tmp_path, video_id, start)),
            metadata={"prep_title": "gta-vi-run6"},
        )
        assert stored, f"could not bank {video_id} {start}"
        entries.append({
            "scene": scene, "video_id": video_id, "start": start,
            "duration": duration, "query": "Grand Theft Auto VI gameplay",
            "path": stored, "scaled_path": None, "verification": "cache",
        })
    manifest = {
        "schema": "vidzeo_prep_manifest_v1",
        "title": TITLE,
        "title_key": "3" * 64,
        "duration_minutes": 1.0,
        "status": "ready",
        "scenes": [
            {"index": idx, "text": text, "words": len(text.split())}
            for idx, text in sorted(GTA_PREP_SCENES.items())
        ],
        "youtube": {"attempted": 22, "verified": 7, "entries": entries},
        "web": {"attempted": 48, "verified": 48, "entries": []},
    }
    (title_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return {"prep_root": prep_root, "cache_dir": cache_dir, "manifest": manifest}


def _write_gta_cut(tmp_path, video_id, start):
    path = tmp_path / f"gta-cut-{video_id}-{int(start)}.mp4"
    path.write_bytes(_fake_cut_bytes())
    return path


def test_gta_run6_offline_replay(offline_env, gta_prep_env, monkeypatch, tmp_path):
    """Replay run 6 offline: the real 13 scene texts + the real 7 banked
    prep cuts, with a synthetic live pool (the run-6 live winners plus
    one general GTA VI clip per scene without a prep cut).

    Run 6 live outcome was 6 YouTube / 0 web / 7 graphics, with scenes
    10/11 unaudited and scene 5's verified restore unconsumed. The
    replay must show the chain fixing all three in-lane: all 7 cuts
    inject, the 6 prep-mapped scenes resolve from disk with zero network
    calls, and every injected candidate is accounted for in the audit.

    HONEST SCOPE NOTE: only 8/13 scenes resolve here, NOT 13/13 — and the
    5 that do not are a scorer limitation, not a chain failure. The
    niche classifier (detect_niche_conflict) reads the scene TEXT and
    mislabels GTA VI scenes: scene 3 ("Army") -> expected niche 'war',
    scene 6 ("Red Dead Redemption 2") -> 'space', scene 8 ("delays") ->
    'travel', so every gaming-titled candidate is hard-rejected (-999)
    for those scenes before the chain ever sees it. The chain cannot
    rescue a candidate the scorer never offers without bypassing a
    shared relevance guard — that bypass is a scorer-owner decision,
    deliberately not taken here. The 5 unresolved scenes are the
    unmapped ones (1, 3, 4, 6, 8); the 2 live scenes the scorer does
    offer (7, 12) resolve through the live layers. If the classifier is
    fixed later, these counts must be re-measured (this test will fail
    and needs updating — that is the point of pinning them).

    Likewise injected_not_attempted is 1, not 0: TWO cuts map to scene 0
    (c5qhgwwLY50 and TgtfearX668) and only one can be primary — the
    second is correctly audited as not_attempted/no_download_plan."""
    texts = list(GTA_TEXTS)
    rows = _build_scene_rows(TITLE, texts)
    pools = {}
    audit = inject_prep_manifest_candidates(pools, rows, TITLE, texts)
    assert audit["injected"] == 7
    assert sorted(audit["scenes"]) == [0, 2, 5, 9, 10, 11]
    # Synthetic live pool: the real run-6 live winners + a general clip
    # per scene that has no prep cut.
    live = [
        _live_candidate("fRmoCG4rTmM", "Grand Theft Auto VI Jason Lucia gameplay"),
        _live_candidate("0ZY_bEuJdX4", "Grand Theft Auto VI Leonida gameplay"),
        _live_candidate("l6RRdTFuepk", "Grand Theft Auto VI Bonnie Clyde gameplay"),
        _live_candidate("gen-s1", "Grand Theft Auto VI opening heist score gone wrong gameplay"),
        _live_candidate("gen-s3", "Grand Theft Auto VI Jason Army Leonida Keys gameplay"),
        _live_candidate("gen-s4", "Grand Theft Auto VI Lucia Liberty City gameplay"),
        _live_candidate("gen-s6", "Grand Theft Auto VI Rockstar production gameplay"),
        _live_candidate("gen-s7", "Grand Theft Auto VI reveal trailer gameplay"),
        _live_candidate("gen-s8", "Grand Theft Auto VI delays gameplay"),
        _live_candidate("gen-s12", "Grand Theft Auto VI Vice City open world gameplay"),
    ]
    for key in {_row_pool_key(row) for row in rows}:
        pools.setdefault(key, []).extend(live)
    result, calls, elapsed = _run_get_youtube_clips(monkeypatch, tmp_path, texts, pools)
    paths = list(result or [])
    audit = getattr(result, "audit", {}) or {}
    attempts = audit.get("attempts") or {}
    assert len(paths) == 13
    # Measured on this code: 8/13 resolve (6 cached + 2 live); the 5
    # unresolved scenes are the scorer-starved unmapped ones (see note).
    assert sum(1 for path in paths if path) == 8
    layers = audit.get("resolution_layers") or {}
    # The 6 prep-mapped scenes resolve from disk; the 2 live scenes the
    # scorer offers resolve through the live layers.
    assert layers.get("cached") == 6, f"layers={layers}"
    assert layers.get("perfect", 0) + layers.get("general", 0) == 2, f"layers={layers}"
    assert layers.get("unresolved", 0) == 5
    restored = sorted(
        int(index) for index, rows_audit in attempts.items()
        if any(row.get("prep_cache_restored") for row in rows_audit)
    )
    assert restored == [0, 2, 5, 9, 10, 11]
    # Zero network calls for the restored videos.
    for vid in ("c5qhgwwLY50", "TgtfearX668", "Bhwj-_9Xz6M", "Qa40EwhWAFw",
                "ZGyKOjovCOI", "MArh6jOUu4g", "GDuSN0-brZs"):
        assert vid not in calls
    # Every resolved scene's final clip is the fake cut bytes.
    for path in paths:
        if path:
            assert Path(path).read_bytes() == _fake_cut_bytes()
    # Offline replay is fast: whole batch + per-scene restore in seconds.
    assert elapsed < 60.0
    per_scene = [
        max((row.get("elapsed_seconds") or 0.0) for row in rows_audit)
        for rows_audit in attempts.values() if rows_audit
    ]
    assert per_scene and max(per_scene) < 5.0
    # The second scene-0 cut is the only injected candidate without a
    # pass, and it is audited with WHY — not silently missing.
    assert audit.get("injected_not_attempted", 0) == 1
    not_attempted = [
        row for rows_audit in attempts.values() for row in rows_audit
        if row.get("status") == "not_attempted"
    ]
    assert len(not_attempted) == 1
    assert not_attempted[0].get("candidate_id") == "TgtfearX668"
    assert not_attempted[0].get("reason") == "no_download_plan"


# ---------------------------------------------------------------------------
# Recovery-wave exclusion: a vision-rejected primary must not be
# re-injected (test 15 scene 0: c5qhgwwLY50 re-downloaded by the recovery
# wave while the unused prep alternate TgtfearX668 sat on disk and the
# scene fell back to a frame).
# ---------------------------------------------------------------------------

def test_prep_injection_skips_excluded_recovery_rejects(offline_env, gta_prep_env):
    """inject_prep_manifest_candidates honours excluded_video_ids: the
    rejected primary is skipped with reason excluded_recovery_reject and
    the alternate for the same scene still injects."""
    texts = list(GTA_TEXTS)
    rows = _build_scene_rows(TITLE, texts)
    pools = {}
    audit = inject_prep_manifest_candidates(
        pools, rows, TITLE, texts, excluded_video_ids={"c5qhgwwLY50"}
    )
    assert audit["injected"] == 6
    skipped = {
        entry.get("video_id"): entry.get("reason")
        for entry in audit.get("skipped_entries") or []
    }
    assert skipped.get("c5qhgwwLY50") == "excluded_recovery_reject"
    injected_ids = {
        candidate.get("video_id")
        for candidates in pools.values()
        for candidate in candidates
        if candidate.get("prep_manifest_injected")
    }
    assert "TgtfearX668" in injected_ids
    assert "c5qhgwwLY50" not in injected_ids


def test_gta_recovery_replay_attempts_unused_prep_alternate(offline_env, gta_prep_env, monkeypatch, tmp_path):
    """Recovery-wave replay: with the vision-rejected primary
    (c5qhgwwLY50) excluded, scene 0 resolves from the previously unused
    prep alternate TgtfearX668 (disk restore, zero network calls) and no
    injected candidate is left unattempted. This is the test-15 scene-0
    failure replayed offline: primary excluded, alternate attempted."""
    texts = list(GTA_TEXTS)
    rows = _build_scene_rows(TITLE, texts)
    pools = {}
    audit = inject_prep_manifest_candidates(
        pools, rows, TITLE, texts, excluded_video_ids={"c5qhgwwLY50"}
    )
    assert audit["injected"] == 6
    assert sorted(audit["scenes"]) == [0, 2, 5, 9, 10, 11]
    live = [
        _live_candidate("fRmoCG4rTmM", "Grand Theft Auto VI Jason Lucia gameplay"),
        _live_candidate("0ZY_bEuJdX4", "Grand Theft Auto VI Leonida gameplay"),
        _live_candidate("l6RRdTFuepk", "Grand Theft Auto VI Bonnie Clyde gameplay"),
        _live_candidate("gen-s1", "Grand Theft Auto VI opening heist score gone wrong gameplay"),
        _live_candidate("gen-s3", "Grand Theft Auto VI Jason Army Leonida Keys gameplay"),
        _live_candidate("gen-s4", "Grand Theft Auto VI Lucia Liberty City gameplay"),
        _live_candidate("gen-s6", "Grand Theft Auto VI Rockstar production gameplay"),
        _live_candidate("gen-s7", "Grand Theft Auto VI reveal trailer gameplay"),
        _live_candidate("gen-s8", "Grand Theft Auto VI delays gameplay"),
        _live_candidate("gen-s12", "Grand Theft Auto VI Vice City open world gameplay"),
    ]
    for key in {_row_pool_key(row) for row in rows}:
        pools.setdefault(key, []).extend(live)
    result, calls, elapsed = _run_get_youtube_clips(
        monkeypatch, tmp_path, texts, pools,
        excluded_video_ids=["c5qhgwwLY50"],
    )
    paths = list(result or [])
    audit = getattr(result, "audit", {}) or {}
    attempts = audit.get("attempts") or {}
    assert len(paths) == 13
    assert sum(1 for path in paths if path) == 8
    layers = audit.get("resolution_layers") or {}
    assert layers.get("cached") == 6, f"layers={layers}"
    # Scene 0 resolves from the alternate, restored from disk.
    scene0_rows = attempts.get("0") or attempts.get(0) or []
    restored0 = [
        row for row in scene0_rows
        if row.get("candidate_id") == "TgtfearX668"
    ]
    assert restored0, "TgtfearX668 has no attempt row for scene 0"
    assert restored0[0].get("prep_cache_restored") is True
    assert restored0[0].get("status") == "downloaded"
    # The excluded primary is attempted nowhere.
    assert not any(
        row.get("candidate_id") == "c5qhgwwLY50"
        for rows_audit in attempts.values()
        for row in rows_audit
    )
    assert "c5qhgwwLY50" not in calls
    # Zero network calls for the prep videos, as in the base replay.
    for vid in ("TgtfearX668", "Bhwj-_9Xz6M", "Qa40EwhWAFw",
                "ZGyKOjovCOI", "MArh6jOUu4g", "GDuSN0-brZs"):
        assert vid not in calls
    # Every resolved scene's final clip is the fake cut bytes.
    for path in paths:
        if path:
            assert Path(path).read_bytes() == _fake_cut_bytes()
    # Nothing injected is left unattempted any more (was 1 in the base
    # replay: TgtfearX668 / no_download_plan).
    assert audit.get("injected_not_attempted", 0) == 0
    assert elapsed < 60.0
