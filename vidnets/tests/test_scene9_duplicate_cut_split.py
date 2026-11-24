"""Scene-9 coordinator loss: byte-identical YouTube cuts shared by two scenes.

Run 7 (job 6624244a): the same injected prep cut (ZGyKOjovCOI @ 671.76 s)
was assigned to live scenes 7 and 9. Both passed every coordinator gate,
but the downstream byte-identity dedup caps YouTube video reuse at 1, so
scene 9's copy was dropped and the scene fell to a graphic. The assembly
split in ``pipeline_v2_media_adapter`` cuts the shared clip into disjoint
timestamp segments instead, so each scene keeps a real clip and no two
scenes show the same moment.
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from services.media_resolution_coordinator import SceneResolution


@pytest.fixture()
def workdir():
    """Media files live outside pytest's shared basetemp.

    Several workers run pytest concurrently with --basetemp=/tmp/vidnets-pytest;
    two sessions can claim the same numbered tmp dir and one session's
    startup cleanup deletes the other's in-flight files. A mkdtemp dir
    under /tmp is unique per test and immune to that collision.
    """
    path = Path(tempfile.mkdtemp(prefix="scene9-split-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_test_clip(path, seconds=6.0):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is not available")
    command = [
        ffmpeg, "-y", "-v", "error",
        "-f", "lavfi", "-i",
        f"testsrc=duration={seconds}:size=640x360:rate=30",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(path),
    ]
    subprocess.run(command, check=True, timeout=120)


def _youtube_row(path, *, scene_index, video_start, candidate_id="youtube:ZGyKOjovCOI"):
    return {
        "scene_index": scene_index,
        "selected_path": str(path),
        "provider": "youtube",
        "source": "youtube",
        "media_type": "video",
        "status": "accepted",
        "verification_status": "verified",
        "resolution_kind": "unique",
        "fallback_reason": "",
        "candidate_id": candidate_id,
        "source_url": "https://www.youtube.com/watch?v=ZGyKOjovCOI",
        "video_start": video_start,
        "video_end": (video_start + 10.35) if video_start is not None else 0.0,
        "asset_fingerprint": _sha256(path),
        "visual_decision": {"accepted": True, "status": "accepted"},
        "attempts": [],
    }


def _youtube_resolution(row):
    return SceneResolution(
        scene_index=int(row["scene_index"]),
        selected_path=str(row["selected_path"]),
        provider="youtube",
        media_type="video",
        status="accepted",
        verification_status="verified",
        resolution_kind="unique",
        candidate_id=str(row["candidate_id"]),
        source_url=str(row["source_url"]),
        video_start=row["video_start"],
        video_end=row["video_end"],
        asset_fingerprint=str(row["asset_fingerprint"]),
        visual_decision=dict(row["visual_decision"]),
        attempts=list(row["attempts"]),
    )


def test_split_gives_each_scene_a_distinct_real_segment(workdir):
    import services.pipeline_v2_media_adapter as adapter
    from services.media_asset_policy import excess_media_reuse_indices

    source = workdir / "source_cut.mp4"
    _make_test_clip(source, seconds=6.0)
    scene7_path = workdir / "scene_0001.mp4"
    scene9_path = workdir / "scene_0008.mp4"
    # Run 7 shape: two different files with byte-identical content.
    shutil.copyfile(source, scene7_path)
    shutil.copyfile(source, scene9_path)
    assert _sha256(scene7_path) == _sha256(scene9_path)

    rows = [
        _youtube_row(scene7_path, scene_index=0, video_start=None),
        _youtube_row(scene9_path, scene_index=1, video_start=671.76),
    ]
    paths = [str(scene7_path), str(scene9_path)]
    labels = ["YouTube Clips", "YouTube Clips"]
    resolutions = {0: _youtube_resolution(rows[0]), 1: _youtube_resolution(rows[1])}

    plan_before = {
        "media_paths": list(paths),
        "metadata": {"media_resolution_v2": [dict(r) for r in rows]},
    }
    # The downstream dedup is the loss mechanism: it flags the second copy.
    assert excess_media_reuse_indices(plan_before) == [1]

    audit = adapter._split_duplicate_youtube_cuts(paths, rows, resolutions)

    assert [entry.get("status") for entry in audit] == ["split", "split"]
    assert paths[0] != paths[1]
    assert paths[0] != str(scene7_path) and paths[1] != str(scene9_path)
    assert _sha256(paths[0]) != _sha256(paths[1])
    # Disjoint moments of the same source video, in scene order.
    assert rows[0]["video_start"] == pytest.approx(671.76)
    assert rows[1]["video_start"] == pytest.approx(674.76)
    assert rows[0]["video_end"] == pytest.approx(674.76)
    assert rows[1]["video_end"] == pytest.approx(677.76)
    assert rows[0]["render_clip_start"] == 0.0
    assert rows[1]["render_clip_start"] == 0.0
    for row in rows:
        assert row["resolution_kind"] == "duplicate_cut_distinct_segment"
        assert row["provider"] == "youtube"
        assert row["status"] == "accepted"
        assert row["verification_status"] == "verified"
        assert row["candidate_id"].endswith(("seg1of2", "seg2of2"))
        assert row["attempts"][-1]["stage"] == "duplicate_cut_split"
        assert row["attempts"][-1]["status"] == "accepted_distinct_timestamp_segment"
    # The resolution objects handed downstream carry the same repair.
    assert resolutions[1].selected_path == paths[1]
    assert resolutions[1].resolution_kind == "duplicate_cut_distinct_segment"
    assert resolutions[1].render_clip_start == 0.0
    # The real downstream dedup now finds nothing to drop.
    plan_after = {"media_paths": list(paths), "metadata": {"media_resolution_v2": rows}}
    assert excess_media_reuse_indices(plan_after) == []
    # Both segments are real, decodable video of ~3 s each.
    for path in paths:
        probe = adapter.probe_media(path, require_video=True, min_bytes=1024)
        assert probe.valid and probe.duration == pytest.approx(3.0, abs=0.2)


def test_split_segments_show_different_frames(workdir):
    import services.pipeline_v2_media_adapter as adapter

    source = workdir / "source_cut.mp4"
    _make_test_clip(source, seconds=6.0)
    first = workdir / "scene_0001.mp4"
    second = workdir / "scene_0008.mp4"
    shutil.copyfile(source, first)
    shutil.copyfile(source, second)
    rows = [
        _youtube_row(first, scene_index=0, video_start=100.0),
        _youtube_row(second, scene_index=1, video_start=100.0),
    ]
    paths = [str(first), str(second)]
    adapter._split_duplicate_youtube_cuts(paths, rows, {})
    frames = []
    for index, path in enumerate(paths):
        frame = workdir / f"frame-{index}.png"
        subprocess.run(
            [shutil.which("ffmpeg"), "-y", "-v", "error", "-ss", "0.5",
             "-i", path, "-frames:v", "1", str(frame)],
            check=True, timeout=60,
        )
        frames.append(frame.read_bytes())
    # testsrc is time-varying: disjoint moments must not show the same frame.
    assert frames[0] != frames[1]


def test_split_skips_clip_too_short_for_disjoint_moments(workdir, monkeypatch):
    import services.pipeline_v2_media_adapter as adapter

    clip = workdir / "short.mp4"
    clip.write_bytes(b"identical-bytes" * 100)
    other = workdir / "short-copy.mp4"
    other.write_bytes(b"identical-bytes" * 100)
    rows = [
        _youtube_row(clip, scene_index=0, video_start=10.0),
        _youtube_row(other, scene_index=1, video_start=10.0),
    ]
    paths = [str(clip), str(other)]

    class _Probe:
        valid = True
        reason = ""
        duration = 3.0  # 1.5 s per scene: below the 2 s floor.

    monkeypatch.setattr(adapter, "probe_media", lambda *a, **k: _Probe())
    audit = adapter._split_duplicate_youtube_cuts(paths, rows, {})
    assert audit and audit[0]["status"] == "skipped"
    assert audit[0]["reason"] == "clip_too_short_for_disjoint_segments"
    assert paths == [str(clip), str(other)]
    assert rows[0]["resolution_kind"] == "unique"


def test_split_leaves_single_use_and_web_rows_alone(workdir, monkeypatch):
    import services.pipeline_v2_media_adapter as adapter

    clip = workdir / "once.mp4"
    clip.write_bytes(b"single-use-clip-bytes" * 100)
    photo_a = workdir / "photo-a.jpg"
    photo_b = workdir / "photo-b.jpg"
    photo_a.write_bytes(b"same-photo-bytes" * 100)
    photo_b.write_bytes(b"same-photo-bytes" * 100)
    rows = [
        _youtube_row(clip, scene_index=0, video_start=5.0),
        {
            "scene_index": 1, "selected_path": str(photo_a), "provider": "web",
            "media_type": "image", "status": "accepted",
            "verification_status": "verified", "resolution_kind": "unique",
            "fallback_reason": "", "candidate_id": "web:photo",
            "video_start": None, "video_end": None, "attempts": [],
        },
        {
            "scene_index": 2, "selected_path": str(photo_b), "provider": "web",
            "media_type": "image", "status": "accepted",
            "verification_status": "verified", "resolution_kind": "unique",
            "fallback_reason": "", "candidate_id": "web:photo",
            "video_start": None, "video_end": None, "attempts": [],
        },
    ]
    paths = [str(clip), str(photo_a), str(photo_b)]
    audit = adapter._split_duplicate_youtube_cuts(paths, rows, {})
    assert audit == []
    assert paths == [str(clip), str(photo_a), str(photo_b)]


def test_split_disabled_by_env_flag(workdir, monkeypatch):
    import services.pipeline_v2_media_adapter as adapter

    source = workdir / "source_cut.mp4"
    _make_test_clip(source, seconds=6.0)
    first = workdir / "scene_0001.mp4"
    second = workdir / "scene_0008.mp4"
    shutil.copyfile(source, first)
    shutil.copyfile(source, second)
    rows = [
        _youtube_row(first, scene_index=0, video_start=100.0),
        _youtube_row(second, scene_index=1, video_start=100.0),
    ]
    paths = [str(first), str(second)]
    monkeypatch.setenv("VIDZEO_DUPLICATE_CUT_SPLIT", "0")
    audit = adapter._split_duplicate_youtube_cuts(paths, rows, {})
    assert audit == []
    assert paths == [str(first), str(second)]


def test_split_rolls_back_when_a_segment_cut_fails(workdir, monkeypatch):
    import services.pipeline_v2_media_adapter as adapter

    source = workdir / "source_cut.mp4"
    _make_test_clip(source, seconds=6.0)
    first = workdir / "scene_0001.mp4"
    second = workdir / "scene_0008.mp4"
    shutil.copyfile(source, first)
    shutil.copyfile(source, second)
    rows = [
        _youtube_row(first, scene_index=0, video_start=100.0),
        _youtube_row(second, scene_index=1, video_start=100.0),
    ]
    paths = [str(first), str(second)]
    calls = {"n": 0}
    real_cut = adapter._cut_video_segment

    def _flaky_cut(src, start, duration, dest, **kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            return False
        return real_cut(src, start, duration, dest, **kwargs)

    monkeypatch.setattr(adapter, "_cut_video_segment", _flaky_cut)
    audit = adapter._split_duplicate_youtube_cuts(paths, rows, {})
    assert audit and audit[0]["status"] == "skipped"
    assert "segment_cut_failed" in str(audit[0]["reason"])
    assert paths == [str(first), str(second)]
    assert rows[0]["resolution_kind"] == "unique"
    assert rows[1]["resolution_kind"] == "unique"
    leftovers = [p for p in workdir.iterdir() if "distinct_seg" in p.name]
    assert leftovers == []


def test_assembly_hook_splits_shared_cut(monkeypatch, workdir):
    import services.pipeline_v2_media_adapter as adapter

    source = workdir / "source_cut.mp4"
    _make_test_clip(source, seconds=6.0)
    first = workdir / "scene_0001.mp4"
    second = workdir / "scene_0008.mp4"
    shutil.copyfile(source, first)
    shutil.copyfile(source, second)
    rows = [
        _youtube_row(first, scene_index=0, video_start=100.0),
        _youtube_row(second, scene_index=1, video_start=100.0),
    ]
    resolutions = {0: _youtube_resolution(rows[0]), 1: _youtube_resolution(rows[1])}
    scenes = [
        {"text": "First beat.", "order": 0, "scene_id": "scn-0", "container_id": "ctr"},
        {"text": "Second beat.", "order": 1, "scene_id": "scn-1", "container_id": "ctr"},
    ]
    monkeypatch.setattr(
        adapter, "resolve_storyboard_media_v2", lambda *a, **k: resolutions
    )
    media_plan, returned = adapter.resolve_and_build_media_plan(
        scenes,
        youtube_lane=lambda scene, event: None,
        web_lane=lambda scene, event: None,
        stock_lane=None,
        visual_verifier=None,
        neutral_factory=lambda scene: None,
    )
    assert media_plan["media_paths"][0] != media_plan["media_paths"][1]
    assert media_plan["metadata"]["duplicate_cut_splits"]
    assert all(
        entry.get("status") == "split"
        for entry in media_plan["metadata"]["duplicate_cut_splits"]
    )
    assert media_plan["source_labels"] == ["YouTube Clips", "YouTube Clips"]
    assert returned[1].resolution_kind == "duplicate_cut_distinct_segment"
