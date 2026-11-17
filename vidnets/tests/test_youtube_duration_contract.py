"""Regression coverage for YouTube range promotion and duration guarantees."""
import shutil
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from services import youtube_pipeline_v2 as yt


@pytest.mark.parametrize("source_seconds, accepted", [(3, False), (6, True)])
@pytest.mark.parametrize("preserve", ["0", None])
def test_download_rejects_short_range_even_if_decodable(tmp_path, monkeypatch, source_seconds, accepted, preserve):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg and ffprobe required")
    source = tmp_path / "source.mp4"
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc2=size=320x180:rate=30:duration={source_seconds}",
                    "-f", "lavfi", "-i", "anullsrc=d=6", "-c:a", "aac",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True)

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def download(self, urls):
            shutil.copyfile(source, self.options["outtmpl"].replace("%(ext)s", "mp4"))

    fake_utils = types.ModuleType("yt_dlp.utils")
    fake_utils.download_range_func = lambda *args: None
    monkeypatch.setitem(sys.modules, "yt_dlp.utils", fake_utils)
    monkeypatch.setattr("services.yt_dlp_runtime.load_yt_dlp", lambda: SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    monkeypatch.setattr(yt, "_source_video_height", lambda path: 1080)
    monkeypatch.setattr(yt, "_network_disabled_by_runtime", lambda: False)
    if preserve is None:
        monkeypatch.delenv("VIDZEO_YT_RETRIEVAL_PRESERVE_SOURCE", raising=False)
    else:
        monkeypatch.setenv("VIDZEO_YT_RETRIEVAL_PRESERVE_SOURCE", preserve)
    monkeypatch.setenv("VIDZEO_YT_OUTPUT_FPS", "30")
    monkeypatch.setenv("VIDZEO_PROJECT_WIDTH", "320")
    monkeypatch.setenv("VIDZEO_PROJECT_HEIGHT", "180")
    monkeypatch.setenv("VIDZEO_FFMPEG_PATH", ffmpeg)
    monkeypatch.setenv("VIDZEO_FFPROBE_PATH", ffprobe)
    output = tmp_path / "result.mp4"
    candidate = {"id": "abcdefghijk", "webpage_url": "https://youtube.com/watch?v=abcdefghijk"}
    if preserve == '0':
        candidate['contact_crop_pct'] = 8
    result = yt._download_clip(candidate, output, 0, 5.017)
    assert result is accepted
    assert output.exists() is accepted
    if result:
        from services.media_integrity import probe_media
        probe = probe_media(output, ffprobe_path=ffprobe, min_duration=5.417, duration_from_video=True)
        assert probe.valid
        if preserve == '0':
            assert probe.fps == 30
            assert probe.duration == pytest.approx(163 / 30, abs=1e-6)
            assert candidate['_download_mode'] == 'normalized_range'
        else:
            assert output.read_bytes() == source.read_bytes()
            assert candidate['_download_mode'] == 'preserved_source'
