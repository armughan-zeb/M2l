"""Regression tests for the media-budget + fan-out + salvage fixes (Nov 21, 2026).

Test 20 (job ed953576) took 1375.9s (~23 min) of coordinator media wall
against a 5-8 min target, plus ~10 min in a post-coordinator gate:
- Fix 1: YT recovery waves and the post-coordinator gate were never bound
  to the 412s total media budget (discovery + tail).
- Fix 2: YT-branch per-clip verification ran inline, one batched vision
  call per scene on the download thread, behind a 4-slot semaphore.
- Fix 3: a hung vision call could block a scene for an hour, and the disk
  salvage re-screened every already-screened file sequentially (~333s).

Each test below pins one of those behaviours at its seam.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import app as server_app
import services.pipeline_v2_media_adapter as adapter
import services.youtube_pipeline_v2 as ytv2
from services.media_resolution_coordinator import resolve_storyboard_media_v2
from services.pipeline_v2_media_adapter import resolve_existing_provider_branches_v2
from services.visual_requirement import build_visual_requirement

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000000020001e221bc330000000049454e44ae426082"
)

TERMINAL_STATUSES = {"accepted", "resolved", "fallback", "unverified", "degraded"}


# ---------------------------------------------------------------------------
# Fix 1(a) — coordinator recovery YT wave bound to the total media budget
# ---------------------------------------------------------------------------

def _recovery_scene(index):
    return {
        "scene_id": f"recovery-{index}",
        "scene_index": index,
        "container_id": "gta",
        "text": "Grand Theft Auto VI gameplay in Vice City",
        "source_policy": {"youtube_quota": True},
    }


def _recovery_web_image(tmp_path, index):
    path = tmp_path / f"recovery-web-{index}.png"
    path.write_bytes(PNG_1X1 + b"\x00" * 512)
    return {
        "path": str(path),
        "source": "web",
        "candidate_id": f"web:REC{index}",
        "title": "GTA VI Vice City gameplay",
        "final_score": 80.0,
    }


def _run_coordinator_recovery(tmp_path, verification_deadline_seconds):
    scenes = [_recovery_scene(i) for i in range(2)]
    images = [_recovery_web_image(tmp_path, i) for i in range(2)]
    yt_fallback_calls = []
    web_fallback_calls = []

    def youtube_fallback_lane(scene, _cancel):
        yt_fallback_calls.append(scene["scene_index"])
        return []

    def web_fallback_lane(scene, _cancel):
        web_fallback_calls.append(scene["scene_index"])
        return [images[scene["scene_index"]]]

    started = time.monotonic()
    result = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=lambda _scene, _cancel: [],
        web_lane=lambda _scene, _cancel: [],
        youtube_fallback_lane=youtube_fallback_lane,
        web_fallback_lane=web_fallback_lane,
        visual_verifier=None,
        neutral_factory=lambda _scene: None,
        deadline_seconds=30,
        scene_budget_seconds=30,
        vision_reserve_seconds=0.5,
        adaptive_media_review=True,
        verification_deadline_seconds=verification_deadline_seconds,
    )
    return result, yt_fallback_calls, web_fallback_calls, time.monotonic() - started


def test_youtube_recovery_wave_skipped_past_total_media_budget(tmp_path):
    """A YouTube recovery wave starting past the total media budget must
    not launch: fetch_recovery returns skipped_after_media_deadline and
    the fallback lane is never called. The web recovery lane keeps its
    own longer window and still runs."""
    result, yt_calls, web_calls, elapsed = _run_coordinator_recovery(tmp_path, 0)
    assert yt_calls == [], "fallback YT wave launched past the total budget"
    assert sorted(web_calls) == [0, 1], "web recovery must still run"
    assert set(result) == {0, 1}
    for index in (0, 1):
        assert result[index].status in TERMINAL_STATUSES
    assert elapsed < 5.0


def test_youtube_recovery_wave_runs_inside_total_media_budget(tmp_path):
    """Control for the skip above: with the total budget still open, the
    fallback YT wave is offered to every scene that needs recovery."""
    result, yt_calls, web_calls, _elapsed = _run_coordinator_recovery(tmp_path, 60)
    assert sorted(yt_calls) == [0, 1]
    assert set(result) == {0, 1}


# ---------------------------------------------------------------------------
# Fix 1(b) — adapter fallback batch bound to the total media budget
# ---------------------------------------------------------------------------

def _adapter_scene():
    narration = "Grand Theft Auto VI returns to Vice City with a new story."
    return [{
        "scene_id": "gta-budget-beat", "order": 0, "duration": 6.0,
        "text": narration, "positive_subjects": ["Grand Theft Auto VI"],
        "visual_requirement": build_visual_requirement(
            title="Grand Theft Auto VI", script=narration, scene_text=narration,
            positive_subjects=("Grand Theft Auto VI",),
        ),
    }]


class _RunnerResult(list):
    """A runner result carrying an audit (attempts present => no retry)."""

    def __init__(self, paths, audit):
        super().__init__(paths)
        self.audit = audit


def _run_adapter_fallback(tmp_path, *, spent_budget):
    scenes = _adapter_scene()
    narration = scenes[0]["text"]
    config = {
        "image_source": "Mixed Media",
        "visual_understanding": False,
        "global_media_guarantee": False,
        "_topic_anchor": "Grand Theft Auto VI",
        "_prebuilt_web_queries": [{
            "scene_subject": "Grand Theft Auto VI",
            "subject_query": "Grand Theft Auto VI",
            "search_query": "Grand Theft Auto VI Vice City photo",
            "_llm": True,
        }],
    }
    if spent_budget:
        # Total = discovery (~0.0001s at this rate) + tail 0: long past by
        # the time the coordinator asks for a fallback wave.
        config["yt_discovery_seconds_per_minute"] = 0.001
        config["yt_media_tail_seconds"] = 0
    runner_calls = []

    def youtube_runner(*_args, **_kwargs):
        runner_calls.append(time.monotonic())
        # A content miss, not a transport failure: attempts exist (so the
        # primary does not retry) and stop_reason keeps failure_reason out
        # of the transport-failure set, so the fallback wave is allowed to
        # run when the total budget is open.
        return _RunnerResult(
            [None],
            {"attempts": {"0": [{"status": "content_rejected",
                                 "rejection_reason": "wrong subject"}]},
             "resolved": 0, "stop_reason": "content_miss"},
        )

    captured = {}

    def fake_coordinator(rows, **kwargs):
        captured["youtube_fallback_lane"] = kwargs.get("youtube_fallback_lane")
        # Production order: the coordinator runs discovery (primary lanes)
        # before it asks for a recovery wave. Drive the primary lane so
        # the runner count distinguishes "primary only" (spent) from
        # "primary + fallback wave" (open budget).
        youtube_lane = kwargs.get("youtube_lane")
        if youtube_lane is not None:
            youtube_lane({"scene_index": 0}, threading.Event())
        # The real coordinator calls the lane after discovery; give the
        # spent total time to elapse so the skip is not a clock race.
        time.sleep(0.05)
        lane = kwargs.get("youtube_fallback_lane")
        if lane is not None:
            captured["lane_result"] = lane({"scene_index": 0}, threading.Event())
        return {"metadata": {"media_resolution_v2": []}}, {}

    with mock.patch.object(adapter, "resolve_and_build_media_plan", fake_coordinator):
        media_plan, _resolutions, _context = resolve_existing_provider_branches_v2(
            config,
            {"scene_contracts": scenes},
            title="Grand Theft Auto VI", script=narration,
            render_resolution=(1280, 720),
            youtube_runner=youtube_runner,
            web_runner=lambda *a, **k: {},
            stock_runner=lambda *a, **k: {},
            snapshotter=lambda paths, **kwargs: paths,
            neutral_asset_factory=lambda *a, **k: None,
            report_progress=lambda *a, **k: None,
        )
    audit = (media_plan.get("metadata") or {}).get("provider_audit") or {}
    return audit, len(runner_calls), captured


def test_youtube_fallback_batch_skipped_past_total_media_budget(tmp_path):
    """A fallback YT wave starting past the total returns {} immediately:
    the runner is not called for a second wave, and the audit records
    skipped_after_media_deadline."""
    audit, runner_calls, captured = _run_adapter_fallback(tmp_path, spent_budget=True)
    assert captured.get("youtube_fallback_lane") is not None
    assert captured.get("lane_result") is None, "skipped batch must serve no rows"
    assert runner_calls == 1, "fallback wave must not start a second YT run"
    assert (audit.get("youtube_fallback") or {}).get("status") == "skipped_after_media_deadline"


def test_youtube_fallback_batch_runs_inside_total_media_budget(tmp_path):
    """Control: with the total budget still open, the fallback wave runs
    the YouTube lane again instead of skipping."""
    audit, runner_calls, captured = _run_adapter_fallback(tmp_path, spent_budget=False)
    assert captured.get("youtube_fallback_lane") is not None
    assert runner_calls == 2, "fallback wave must run the YT lane again"
    assert (audit.get("youtube_fallback") or {}).get("status") != "skipped_after_media_deadline"


# ---------------------------------------------------------------------------
# Fix 1(c) — post-coordinator gate skipped when the total budget is spent
# ---------------------------------------------------------------------------

def test_post_coordinator_gate_skipped_when_total_media_budget_spent(tmp_path):
    """When the total media budget is already spent, the terminal web
    fallback must NOT build the verifier or re-verify scene by scene
    (~60s/scene, ~10 min in test 20). It resolves with best-so-far (the
    downloaded web candidate) and stamps no fake visual pass."""
    text = "The Tesla Model S followed in 2012."
    requirement = build_visual_requirement(title="Tesla", script=text, scene_text=text)
    contracts = [{"text": "Tesla company"}, {"text": text, "visual_requirement": requirement}]
    asset = tmp_path / "tesla.jpg"
    Image.new("RGB", (640, 360), "#345678").save(asset)
    # No visual fields on purpose: without the skip, the
    # _has_live_visual_pass gate would reject this candidate.
    preliminary = {"path": str(asset)}
    verifier = mock.Mock(side_effect=AssertionError(
        "build_qwen_verifier must not run past the total media budget"
    ))
    config = {
        "_topic_anchor": "Tesla",
        "_scene_contracts_v2": contracts,
        "_visual_requirements": [{}, requirement],
        "_media_total_deadline_at": time.monotonic() - 1.0,
    }
    with mock.patch(
        "services.web_image_service.resolve_web_images_for_video_fast",
        return_value={"image_paths": [str(asset)], "scenes": [preliminary]},
    ), mock.patch(
        "app._generate_fast_scene_queries",
        return_value=[{"section_subject": "Tesla"}],
    ), mock.patch(
        "app._resolve_query_llm_settings",
        return_value=("openrouter", "unit-test-key", "qwen"),
    ), mock.patch(
        "services.web_image.safety.image_is_safe", return_value=(True, "")
    ), mock.patch(
        "services.pipeline_v2_media_adapter.build_qwen_verifier",
        return_value=(verifier, None),
    ), mock.patch.object(
        server_app, "_media_duplicate_key", side_effect=lambda path: str(path or "")
    ), mock.patch.object(
        server_app, "_render_image_phash", return_value=None
    ):
        paths = ["existing.jpg", None]
        count = server_app._fill_unresolved_with_general_web_fallback(
            paths, ["web", ""], [1], ["Tesla company", text], (640, 360),
            "Tesla", text, config,
            force_web=True, require_verified_indices={1},
        )
    assert count == 1
    assert paths[1] == str(asset)
    verifier.assert_not_called()
    provenance = (config.get("_late_media_provenance_by_scene") or {}).get(1) or {}
    assert "visual_decision" not in provenance, "no fake visual pass may be stamped"


# ---------------------------------------------------------------------------
# Fix 2 — YT-branch verification fans out across scenes
# Fix 3(a) — a hung verification call fails open on the branch budget
# ---------------------------------------------------------------------------

class _FakeVisualContext:
    """Vision enabled (so the branch verifies instead of finalizing on
    transport alone) but with no client: the contact probe returns
    'unavailable' immediately, so downloads use the prep-cut restore."""

    enabled = True
    visual_required = False
    fail_closed = False
    youtube_contact_only = False

    def get_client(self):
        return None


class _FakeTranscriptIndex:
    def __init__(self, **_kwargs):
        pass

    def fetch_many(self, _ids, **_kwargs):
        return {}


def _prep_cut(tmp_path, name):
    cut = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc=s=320x180:d=2", "-pix_fmt", "yuv420p", str(cut)],
        check=True, timeout=60,
    )
    assert cut.stat().st_size >= 8192
    return cut


def _yt_candidates(tmp_path, scene_count):
    candidates = []
    for index in range(scene_count):
        cut = _prep_cut(tmp_path, f"prep-cut-{index}.mp4")
        candidates.append({
            "id": f"vid{index}", "video_id": f"vid{index}",
            # Distinct titles: the download planner keys timestamp reuse
            # on video id AND title, so identical titles make four
            # different videos collide on one reuse key and starve scenes
            # 2-3 of any timestamp.
            "title": f"Grand Theft Auto VI trailer part {index + 1}",
            "channel": "Rockstar Games", "duration": 60,
            "query_text": "Grand Theft Auto VI Vice City",
            "score": 300 - index * 10, "v2_score": 300 - index * 10,
            "prep_manifest_injected": True,
            # The real prep injection stamps this overlap; without it the
            # candidate gets no prep carve-out (verified moment used
            # verbatim) and falls to heuristic timestamp picking, which
            # starves scenes that share one title key.
            "prep_scene_overlap": 1.0,
            "prep_best_timestamp": 10.0 + index * 10.0,
            "best_timestamp": 10.0 + index * 10.0,
            "prep_live_scene": index,
            "prep_cache_path": str(cut),
        })
    return candidates


def _run_yt_branch(tmp_path, scene_count, verdict_delay, **kwargs):
    candidates = _yt_candidates(tmp_path, scene_count)
    spans = {}
    calls = []

    def fake_search(groups, **_kwargs):
        return {key: [dict(candidate) for candidate in candidates] for key in groups}

    def slow_verdicts(batch_candidates, scene_index, *args, **_kwargs):
        calls.append(scene_index)
        start = time.monotonic()
        time.sleep(verdict_delay)
        spans[scene_index] = (start, time.monotonic())
        return {
            candidate["candidate_id"]: (True, 90.0, True, "matches the scene")
            for candidate in batch_candidates
        }

    scene_texts = [f"Vice City scene {index}." for index in range(scene_count)]
    with mock.patch.dict(os.environ, {"VIDZEO_YT_POOL_FIRST": "0"}, clear=False), \
         mock.patch.object(ytv2, "_search_container_pools", side_effect=fake_search), \
         mock.patch(
             "services.youtube_ranged_moments.prepare_ranged_moments",
             return_value=({}, {"status": "completed", "sources_requested": 0,
                                "scenes_with_moments": 0}),
         ), mock.patch(
             "services.youtube_transcript_index.YouTubeTranscriptIndex",
             _FakeTranscriptIndex,
         ), mock.patch.object(ytv2, "_batched_visual_verdicts", side_effect=slow_verdicts):
        started = time.monotonic()
        result = ytv2.get_youtube_clips(
            script=" ".join(scene_texts),
            title="Grand Theft Auto VI: Return to Vice City",
            num_scenes=scene_count,
            scene_texts=scene_texts,
            scene_durations=[9.0] * scene_count,
            use_authenticated_session=False,
            output_dir=tmp_path,
            visual_context=_FakeVisualContext(),
            download_candidate_limit=1,
            **kwargs,
        )
        elapsed = time.monotonic() - started
    return result, spans, calls, elapsed


def test_yt_branch_verification_fans_out_across_scenes(tmp_path):
    """Test-20 fan-out: with a 2s vision call per scene, all scenes'
    verifications must OVERLAP (filed on the shared verify pool the
    moment each scene's downloads finish), not run one after another
    on the download threads (~82s apart live)."""
    if not pytest.importorskip("shutil").which("ffmpeg"):
        pytest.skip("ffmpeg required")
    result, spans, _calls, elapsed = _run_yt_branch(tmp_path, 4, verdict_delay=2.0)
    assert len(result) == 4
    assert all(result), "every scene must resolve its prep-cut clip"
    assert set(spans) == {0, 1, 2, 3}, "every scene must be vision-verified"
    assert max(start for start, _end in spans.values()) < min(
        end for _start, end in spans.values()
    ), "scene verifications did not overlap — still sequential"
    # Sequential verification would cost 4 x 2s of vision alone.
    assert elapsed < 15.0
    # The finalize step keeps the on-disk attempt audit in step with the
    # rename, so the salvage can trust it by path (Fix 3b).
    audit = json.loads((tmp_path / "youtube_scene_001.attempts.json").read_text())
    downloaded = [row for row in audit if row.get("status") == "downloaded"]
    assert downloaded and downloaded[0].get("path") == result[0]


def test_hung_verification_fails_open_within_branch_budget(tmp_path):
    """Test-20 stall guard: a vision call that never returns must fail
    open to best-so-far on the per-scene verify timeout (30s floor via
    VIDZEO_YT_VERIFY_TIMEOUT_SECONDS) instead of holding the scene for
    the 3600s inbox wall. ~30s wall by design."""
    if not pytest.importorskip("shutil").which("ffmpeg"):
        pytest.skip("ffmpeg required")
    with mock.patch.dict(
        os.environ, {"VIDZEO_YT_VERIFY_TIMEOUT_SECONDS": "30"}, clear=False
    ):
        result, _spans, calls, elapsed = _run_yt_branch(
            tmp_path, 1, verdict_delay=35.0,
        )
    assert calls == [0], "the hung vision call must actually have been filed"
    assert result[0], "hung verification must fail open to the downloaded clip"
    assert elapsed < 40.0, "hung call must not hold the scene past the timeout"


# ---------------------------------------------------------------------------
# Fix 3(b) — salvage trusts the worker's prior screening
# ---------------------------------------------------------------------------

def _salvage_file(tmp_path, scene=1):
    clip = tmp_path / f"yt_clip_scene_{scene}.mp4"
    clip.write_bytes(b"clip" * 256)
    return clip


def _salvage_audit(tmp_path, clip, *, status="downloaded", checked=True,
                   reject=False, overlay_checked=True, rejection_reason=""):
    audit_path = tmp_path / "youtube_scene_001.attempts.json"
    audit_path.write_text(json.dumps([{
        "status": status,
        "quality_check": {"checked": checked, "reject": reject},
        "overlay_checked": overlay_checked,
        "rejection_reason": rejection_reason,
        "path": str(clip.resolve()),
    }]), encoding="utf-8")
    return audit_path


def _run_salvage(tmp_path, **env):
    quality = mock.Mock(return_value={"reject": False})
    overlays = mock.Mock(return_value={"reject": False, "overlay_types": []})
    with mock.patch.object(
        server_app, "_media_path_exists_for_render", return_value=True
    ), mock.patch(
        "services.youtube_pool_renderer._screen_moment_quality", quality
    ), mock.patch(
        "services.youtube_pool_renderer._screen_moment_overlays", overlays
    ), mock.patch.dict(os.environ, env, clear=False):
        recovered = server_app._recover_completed_clips_from_disk(1, run_root=tmp_path)
    return recovered, quality, overlays


def test_salvage_skips_rescreening_audited_files(tmp_path):
    """Files the worker already screened (attempt audit: downloaded,
    quality checked + not rejected, overlay checked) must not be
    re-screened — test 20 spent ~333s doing exactly that."""
    clip = _salvage_file(tmp_path)
    _salvage_audit(tmp_path, clip)
    recovered, quality, overlays = _run_salvage(tmp_path)
    assert recovered == [str(clip.resolve())]
    quality.assert_not_called()
    overlays.assert_not_called()


def test_salvage_screen_budget_accepts_remaining_unscreened(tmp_path):
    """With the salvage screen budget spent (0s here), files with no
    audit verdict are accepted unscreened instead of stalling the
    salvage — the coordinator's vision gate still judges them."""
    clip = _salvage_file(tmp_path)
    recovered, quality, overlays = _run_salvage(
        tmp_path, VIDZEO_YT_SALVAGE_SCREEN_BUDGET_SECONDS="0"
    )
    assert recovered == [str(clip.resolve())]
    quality.assert_not_called()
    overlays.assert_not_called()


def test_salvage_still_screens_unaudited_files(tmp_path):
    """Control: a file with no passing audit verdict is screened exactly
    as before when the budget is open."""
    clip = _salvage_file(tmp_path)
    recovered, quality, overlays = _run_salvage(tmp_path)
    assert recovered == [str(clip.resolve())]
    assert quality.call_count == 1
    assert overlays.call_count == 1


def test_salvage_rejected_audit_row_is_not_trusted(tmp_path):
    """A row the worker rejected (motion/quality reject) must not be
    trusted: the file is screened again and the screen's reject wins."""
    clip = _salvage_file(tmp_path)
    _salvage_audit(tmp_path, clip, status="motion_or_quality_rejected",
                   rejection_reason="static_frame_hold")
    # A non-static reject reason: static reasons are forgiven in lenient
    # mode, so the screen must return a hard reason for the drop to fire.
    quality = mock.Mock(return_value={"reject": True, "reason": "black_frames"})
    overlays = mock.Mock(return_value={"reject": False, "overlay_types": []})
    with mock.patch.object(
        server_app, "_media_path_exists_for_render", return_value=True
    ), mock.patch(
        "services.youtube_pool_renderer._screen_moment_quality", quality
    ), mock.patch(
        "services.youtube_pool_renderer._screen_moment_overlays", overlays
    ):
        recovered = server_app._recover_completed_clips_from_disk(1, run_root=tmp_path)
    assert quality.call_count == 1
    assert recovered == [None]
