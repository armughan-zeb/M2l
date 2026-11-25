import shutil
import subprocess
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.youtube_contact_probe import inspect_candidate, select_window


def test_window_validation_rejects_hallucinated_and_short_ranges():
    def response(start, end, **kwargs):
        item = dict(candidate_id='abcdefghijk', score=8, is_usable=True,
                    clean_start_sec=start, clean_end_sec=end, needs_crop_zoom_pct=0)
        item.update(kwargs)
        return {'candidates': [item]}
    assert select_window(response(10, 15.4), 10, 30, 5, candidate_id='abcdefghijk')
    assert not select_window(response(9, 16), 10, 30, 5, candidate_id='abcdefghijk')
    assert not select_window(response(12, 17), 10, 30, 5, candidate_id='abcdefghijk')
    assert not select_window(response(12, 20, candidate_id='wrong'), 10, 30, 5, candidate_id='abcdefghijk')
    assert select_window(response(12, 20, needs_crop_zoom_pct=8), 10, 30, 5, candidate_id='abcdefghijk')['crop_pct'] == 8
    assert not select_window(response(12, 20, needs_crop_zoom_pct=16), 10, 30, 5, candidate_id='abcdefghijk')


def test_missing_vision_is_reported_unavailable_without_downloading(tmp_path):
    def unexpected(*args, **kwargs):
        raise AssertionError('probe should not start without vision')
    status, window, audit = inspect_candidate({'id': 'abcdefghijk'}, 100, 5, 'ship', tmp_path,
                                               None, unexpected, 'ffmpeg', 'ffprobe')
    assert (status, window, audit['reason']) == ('unavailable', None, 'vision_unavailable')


def test_contact_sheet_probe_selects_absolute_source_window(tmp_path):
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('ffmpeg and ffprobe required')
    source = tmp_path / 'fixture.mp4'
    subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=320x180:rate=12:duration=16', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', str(source)], check=True)
    def download(candidate, out, start, duration, **kwargs):
        shutil.copyfile(source, out)
        return True
    class Client:
        def analyze_image(self, path, prompt, system):
            assert Path(path).is_file()
            assert '100.0s' not in prompt
            return {'candidates': [{'candidate_id': 'abcdefghijk', 'score': 8,
                'is_usable': True, 'clean_start_sec': 100, 'clean_end_sec': 106,
                'needs_crop_zoom_pct': 0}]}
    status, winner, audit = inspect_candidate(
        {'id': 'abcdefghijk', 'duration': 300}, 100, 5, 'A ship at sea',
        tmp_path / 'probe', Client(), download, ffmpeg, ffprobe)
    assert status == 'accepted', str(audit)
    assert winner['start'] == 100
    assert audit['probe_start'] == 94
    assert audit['probe_end'] == 110


def test_ranged_pipeline_downloads_vision_selected_timestamp(tmp_path, monkeypatch):
    from services import youtube_pipeline_v2 as yt, youtube_contact_probe, youtube_media_cache
    row = {'scene_index': 0, 'scene_text': 'cargo ship', 'candidate_subject': 'cargo ship',
           'section_subject': 'cargo ship', 'domain_category': 'transportation'}
    candidate = {'id': 'abcdefghijk', 'title': 'cargo ship footage', 'duration': 300,
                 'best_timestamp': 100, 'webpage_url': 'https://youtu.be/abcdefghijk'}
    monkeypatch.setattr(yt, '_build_scene_rows', lambda *_args: [row])
    monkeypatch.setattr(yt, '_search_container_pools', lambda *_args, **_kwargs: {'cargo ship': [candidate]})
    monkeypatch.setattr(yt, '_assign_candidates', lambda *_args, **_kwargs: [candidate])
    monkeypatch.setattr(yt, '_score_candidate', lambda *_args: 100)
    monkeypatch.setattr(yt, '_get_authenticated_youtube_session', lambda **_kwargs: None)
    monkeypatch.setattr(yt, '_screen_downloaded_motion', lambda *_args: {'checked': True, 'reject': False})
    monkeypatch.setattr(yt, '_clean_downloaded_clip_overlays', lambda path, **kwargs: (path, {'checked': True, 'clean': True}))
    monkeypatch.setattr(yt, '_visual_verdict_scored', lambda *args, **kwargs: (True, 90, True, 'pass'))
    monkeypatch.setattr(youtube_media_cache, 'get_verified_cut', lambda *args: None)
    monkeypatch.setattr(youtube_media_cache, 'put_verified_cut', lambda *args, **kwargs: None)
    monkeypatch.setenv('VIDZEO_YT_RANGED_MOMENTS', '0')
    monkeypatch.setattr(youtube_contact_probe, 'inspect_candidate', lambda *args, **kwargs:
        ('accepted', {'start': 103., 'end': 110., 'score': 9., 'crop_pct': 8.}, {'reason': 'selected'}))
    calls = []
    def download(option, out, start, duration, **kwargs):
        calls.append((start, option.get('contact_crop_pct')))
        out.write_bytes(b'x' * 8192)
        return True
    monkeypatch.setattr(yt, '_download_clip', download)
    context = SimpleNamespace(enabled=True, visual_required=True, get_client=lambda: object())
    result = yt.get_youtube_clips('cargo ship', 'cargo ship', num_scenes=1,
        scene_texts=['cargo ship'], scene_durations=[5], visual_context=context,
        pool_first=False, output_dir=str(tmp_path), use_authenticated_session=False)
    assert calls == [(103., 8.)]
    assert result[0]
    assert result.audit['attempts']['0'][0]['timestamp_evidence'] == 'contact_sheet_vision'


def test_isolated_worker_receives_contact_vision_context(tmp_path, monkeypatch):
    from services import youtube_branch_worker, youtube_pipeline_v2
    from services.visual_intelligence.context import VisualDecisionContext
    context = SimpleNamespace(enabled=True)
    monkeypatch.setattr(VisualDecisionContext, 'from_config', lambda config, count: context)
    captured = {}
    def run(**kwargs):
        captured.update(kwargs)
        return youtube_pipeline_v2.YouTubeClipResults([None], audit={})
    monkeypatch.setattr(youtube_pipeline_v2, 'get_youtube_clips', run)
    request = tmp_path / 'request.json'
    result = tmp_path / 'result.json'
    request.write_text(json.dumps({'scene_texts': ['cargo ship'], 'youtube_contact_vision': True,
                                   'visual_config': {'llm_provider': 'external_agent'},
                                   'job_context': {'job_id': 'test-job'},
                                   'cooldown_hard_timeout_seconds': 882}), encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['worker', str(request), str(result)])
    assert youtube_branch_worker.main() == 0
    assert captured['visual_context'] is context
    assert captured['cooldown_hard_timeout_seconds'] == 882
    assert captured['cancellation'].job_id == 'test-job'
    assert context.youtube_contact_only is True
    assert json.loads(result.read_text(encoding='utf-8'))['ok'] is True
