import subprocess

import pytest

from services.render_media_timing import repair_short_video_slots


def film(duration=12, **media):
    return {'fps': 30, 'duration': duration, 'scenes': [
        {'duration': duration, 'narration': 'The exact accepted narration remains.',
         'media': {'type': 'video', 'src': 'cut.mp4', **media},
         'primary_visual': {'type': 'SmartVideo', 'asset': {'src': 'cut.mp4'}}}]}


def test_short_clip_plays_once_then_uses_an_unverified_graphic_tail():
    manifest = film(verified_asset_sha256='real-video-digest')
    calls = []
    def probe(path):
        calls.append(path)
        return 5
    result = repair_short_video_slots(manifest, probe)
    shots = manifest['scenes'][0]['media']['shots']
    assert [(s['shot_start'], s['duration']) for s in shots] == [(0, 5), (5, 7)]
    assert shots[0]['src'] == 'cut.mp4'
    assert shots[0]['verified_asset_sha256'] == 'real-video-digest'
    assert shots[1]['is_graphic_fallback'] is True
    assert shots[1]['verification_status'] == 'not_applicable'
    assert not shots[1]['verified_asset_sha256']
    assert result['graphic_tail_seconds'] == 7
    assert result['graphic_tail_runtime_percent'] == pytest.approx(58.3333)
    assert manifest['scenes'][0]['narration'] == 'The exact accepted narration remains.'
    assert manifest['scenes'][0]['duration'] == 12
    assert calls == ['cut.mp4']
    assert repair_short_video_slots(manifest, probe) == result
    assert len(manifest['scenes'][0]['media']['shots']) == 2


def test_short_cut_never_extends_into_the_next_authored_cut():
    manifest = film(10, shots=[{'src': 'cut.mp4', 'shot_start': 0},
                               {'src': 'second.mp4', 'shot_start': 4}])
    result = repair_short_video_slots(manifest, lambda path: 2 if path == 'cut.mp4' else 20)
    shots = manifest['scenes'][0]['media']['shots']
    assert [(s['shot_start'], s['duration']) for s in shots] == [(0, 2), (2, 2), (4, 6)]
    assert shots[-1]['src'] == 'second.mp4'
    assert result['graphic_tail_seconds'] == 2
    assert shots[0]['shot_end'] == 2
    assert shots[1]['shot_end'] == 4


def test_local_seek_is_respected_and_original_source_timestamp_is_not_a_seek():
    manifest = film(6, shots=[{'src': 'cut.mp4', 'shot_start': 0,
                              'video_start': 300, 'render_clip_start': 0}])
    assert repair_short_video_slots(manifest, lambda _: 5)['graphic_tail_seconds'] == 1
    manifest = film(6, shots=[{'src': 'cut.mp4', 'source_start': 2}])
    assert repair_short_video_slots(manifest, lambda _: 5)['graphic_tail_seconds'] == 3


def test_complete_clips_and_unknown_durations_are_not_stretched_or_guessed():
    for measured in (20, None):
        manifest = film()
        result = repair_short_video_slots(manifest, lambda _: measured)
        assert result['status'] == 'pass'
        assert not manifest['scenes'][0]['media'].get('shots')


def test_container_rounding_does_not_create_a_three_frame_graphic_flash():
    manifest = film(8.6, shots=[{'src': 'cut.mp4', 'shot_start': 0, 'shot_end': 8.6}])
    assert repair_short_video_slots(manifest, lambda _: 8.5)['status'] == 'pass'
    assert len(manifest['scenes'][0]['media']['shots']) == 1


def test_a_seek_past_end_becomes_a_graphic_without_requesting_a_video_frame():
    manifest = film(3, shots=[{'src': 'cut.mp4', 'source_start': 5}])
    assert repair_short_video_slots(manifest, lambda _: 2)['graphic_tail_seconds'] == 3
    shots = manifest['scenes'][0]['media']['shots']
    assert len(shots) == 1 and shots[0]['type'] == 'graphic'


def test_still_with_stale_video_metadata_is_not_probed_as_a_video():
    manifest = film(src='recovered.jpg')
    def unexpected(_):
        raise AssertionError('JPEG must not enter the video probe')
    assert repair_short_video_slots(manifest, unexpected)['status'] == 'pass'


def test_real_probe_measures_the_downloaded_file_not_its_requested_length(tmp_path):
    from services.media_integrity import resolve_ffmpeg_pair
    ffmpeg, _ = resolve_ffmpeg_pair()
    video = tmp_path / 'short.mp4'
    generated = subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'testsrc2=size=320x180:rate=30:duration=1', '-c:v', 'libx264', str(video)],
        capture_output=True, timeout=30)
    assert generated.returncode == 0
    manifest = film(3, src=str(video))
    report = repair_short_video_slots(manifest)
    assert report['graphic_tail_seconds'] == pytest.approx(2, abs=.04)
    assert manifest['scenes'][0]['media']['shots'][0]['duration'] == pytest.approx(1, abs=.04)
