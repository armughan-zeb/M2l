from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from services.media_integrity import resolve_ffmpeg_pair
import app as server_app


def test_render_snapshot_transcodes_av1_to_h264_for_remotion(tmp_path: Path) -> None:
    ffmpeg, ffprobe = resolve_ffmpeg_pair(Path(__file__).resolve().parents[1])
    if not Path(ffmpeg).is_file() or not Path(ffprobe).is_file():
        pytest.skip("bundled ffmpeg/ffprobe pair is unavailable")

    encoder_listing = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if "libaom-av1" not in f"{encoder_listing.stdout}\n{encoder_listing.stderr}":
        pytest.skip("ffmpeg build cannot create an AV1 regression fixture")

    source = tmp_path / "retrieved_av1.mp4"
    generated = subprocess.run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=1.0",
            "-an", "-c:v", "libaom-av1", "-cpu-used", "8", "-crf", "48", "-b:v", "0",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr

    # Discovery owns a durable copy, but cannot serialize codec conversions
    # ahead of the batch handoff. The selected-shot boundary does that work.
    discovery = server_app._snapshot_media_for_render([str(source)], namespace="discovery",
        root_dir=tmp_path / "run", normalize_videos=False)
    assert Path(discovery[0]).read_bytes() == source.read_bytes()
    from services.visual_intelligence.decision import asset_fingerprint
    from services.editorial_director.production_contract import _verified_render_transcode
    source_sha = asset_fingerprint(discovery[0])
    shot = {'path': discovery[0], 'visual_decision': {'verified_asset_sha256': source_sha}}
    manifest = {'scenes': [{'media': {'path': discovery[0], 'shots': [shot]}}]}
    receipts = server_app._normalize_selected_render_shots(manifest, root_dir=tmp_path / 'selected')
    assert len(receipts) == 1
    assert shot['path'] != discovery[0]
    assert _verified_render_transcode(shot, shot['path'], source_sha, source_sha)
    assert asset_fingerprint(discovery[0]) == source_sha

    snapshots = server_app._snapshot_media_for_render(
        [str(source)],
        namespace="av1_compat_test",
        root_dir=tmp_path / "run",
    )

    assert len(snapshots) == 1
    snapshot = Path(snapshots[0])
    assert snapshot.is_file()
    assert snapshot.suffix.lower() == ".mp4"
    probe = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_entries", "stream=codec_name,codec_type",
            "-of", "json", str(snapshot),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    streams = json.loads(probe.stdout).get("streams") or []
    video_codecs = [row.get("codec_name") for row in streams if row.get("codec_type") == "video"]
    assert video_codecs == ["h264"]
