import os
import sys
import time
from pathlib import Path


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import video_service
import app as server_app


def test_render_cleanup_never_deletes_shared_youtube_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(video_service, "TEMP_DIR", tmp_path)
    pool = tmp_path / "yt_clips" / "pool_active" / "sources"
    pool.mkdir(parents=True)
    source = pool / "source.source.mp4"
    source.write_bytes(b"video")

    video_service.cleanup_temp_files(job_id="render-job", clips=[])

    assert source.exists()


def test_age_cleanup_keeps_recent_youtube_root_and_removes_stale_one(tmp_path, monkeypatch):
    monkeypatch.setattr(video_service, "TEMP_DIR", tmp_path)
    recent = tmp_path / "yt_recent"
    stale = tmp_path / "yt_stale"
    recent.mkdir()
    stale.mkdir()
    (recent / "source.mp4").write_bytes(b"recent")
    (stale / "source.mp4").write_bytes(b"stale")
    old = time.time() - (48 * 3600)
    os.utime(stale, (old, old))

    video_service.cleanup_old_temp_files(max_age_hours=24)

    assert recent.exists()
    assert not stale.exists()


def test_v2_render_snapshot_stays_inside_the_exact_run_root(tmp_path):
    from PIL import Image

    source = tmp_path / "provider-result.jpg"
    Image.new("RGB", (640, 360), (30, 60, 90)).save(source, "JPEG")
    run_root = tmp_path / "jobs" / "job-a" / "run-a"

    paths = server_app._snapshot_media_for_render(
        [str(source)],
        namespace="web_v2",
        root_dir=run_root,
    )

    copied = Path(paths[0]).resolve()
    assert copied.is_file()
    assert copied.is_relative_to(run_root.resolve())
    assert "job-b" not in copied.parts


def test_v2_timeout_recovery_never_scans_a_sibling_job(tmp_path, monkeypatch):
    run_a = tmp_path / "jobs" / "job-a" / "run-a"
    run_b = tmp_path / "jobs" / "job-b" / "run-b"
    run_a.mkdir(parents=True)
    run_b.mkdir(parents=True)
    expected = run_a / "yt_clip_scene_1.clean.mp4"
    forbidden = run_b / "yt_clip_scene_1.clean.mp4"
    expected.write_bytes(b"job-a")
    forbidden.write_bytes(b"job-b")
    monkeypatch.setattr(server_app, "_media_path_exists_for_render", lambda path: bool(path))

    recovered = server_app._recover_completed_clips_from_disk(1, run_root=run_a)

    assert recovered == [str(expected.resolve())]
    assert str(forbidden.resolve()) not in recovered
