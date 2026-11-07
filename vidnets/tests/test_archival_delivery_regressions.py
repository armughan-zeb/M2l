"""Reproduce archive transport and review failures without paid requests."""
import sys
import types
from pathlib import Path

import pytest

from services import youtube_pipeline_v2 as yt
from services.native_render_policy import _is_actual_media_mismatch
from services.editorial_director.pixel_revision import _requires_media_repair
from services.pipeline_v2_media_adapter import _score_verdict
from services.visual_intelligence.semantic_guard import explicit_scene_conflict


def archive():
    return {'id': 'archive-1936', 'title': 'The King Abdicates (1936)',
            'channel': 'British Pathé', 'webpage_url': 'https://youtube.com/watch?v=archive-1936'}


def test_archive_floor_requires_source_evidence_and_preserves_hd_default(monkeypatch):
    monkeypatch.delenv('VIDZEO_YT_ALLOW_SD_ARCHIVE', raising=False)
    monkeypatch.delenv('VIDZEO_YT_ARCHIVE_MIN_SOURCE_HEIGHT', raising=False)
    monkeypatch.setenv('VIDZEO_YT_MIN_SOURCE_HEIGHT', '720')
    assert yt._candidate_source_height_floor(archive()) == 360
    assert yt._candidate_source_height_floor({'title': 'Royal family today', 'query': '1936 archive'}) == 720
    assert yt._candidate_source_height_floor({**archive(), 'title': '1936 historical recreation'}) == 720
    assert yt._candidate_source_height_floor({'title': '2026 news archive', 'channel': 'News Archive'}) == 720
    monkeypatch.setenv('VIDZEO_YT_ALLOW_SD_ARCHIVE', '0')
    assert yt._candidate_source_height_floor(archive()) == 720


@pytest.mark.parametrize('pool_source', [False, True])
def test_hd_first_sd_archive_fallback_survives_integrity_handoff(monkeypatch, tmp_path, pool_source):
    from services import yt_dlp_runtime, media_integrity
    calls = []
    class Downloader:
        def __init__(self, options):
            self.options = options
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def download(self, _urls):
            calls.append(self.options['format'])
            if 'height>=360' not in self.options['format']:
                raise RuntimeError('Requested format is not available')
            Path(self.options['outtmpl'].replace('%(ext)s', 'mp4')).write_bytes(b'x' * 8192)
    utils = types.ModuleType('yt_dlp.utils')
    utils.download_range_func = lambda *_args: 'range'
    monkeypatch.setitem(sys.modules, 'yt_dlp.utils', utils)
    monkeypatch.setattr(yt_dlp_runtime, 'load_yt_dlp', lambda: types.SimpleNamespace(YoutubeDL=Downloader))
    monkeypatch.setattr(yt, '_install_youtube_session', lambda *_args: None)
    monkeypatch.setattr(yt, '_source_has_video_stream', lambda *_args: True)
    monkeypatch.setattr(yt, '_source_video_height', lambda *_args: 480)
    monkeypatch.setenv('VIDZEO_YT_ALLOW_SD_ARCHIVE', '1')
    monkeypatch.setenv('VIDZEO_YT_ARCHIVE_MIN_SOURCE_HEIGHT', '360')
    monkeypatch.setenv('VIDZEO_YT_RETRIEVAL_PRESERVE_SOURCE', '1')
    def promote(staged, output, **_kwargs):
        Path(staged).replace(output)
        return types.SimpleNamespace(valid=True, reason='ok')
    monkeypatch.setattr(media_integrity, 'promote_verified_media', promote)
    candidate = archive()
    output = tmp_path / 'archive.mp4'
    audit = {}
    ok = (yt._download_source_once(candidate, output, audit=audit) if pool_source
          else yt._download_clip(candidate, output, 20, 4))
    assert ok and output.is_file()
    assert len(calls) == 2
    assert 'height>=360' not in calls[0] and 'height>=720' in calls[0]
    assert 'height>=360' in calls[1]
    if pool_source:
        assert 'filesize<=' in calls[1]
        assert audit['archival_sd_source'] is True
    assert candidate['archival_sd_source'] is True


def test_wrong_era_cannot_be_softened_into_a_portrait_near_match():
    result = _score_verdict({'text': 'She became queen in 1952.'}, {
        'identity_score': 95, 'action_score': 10, 'context_score': 85, 'quality': 90,
        'domain_match': True, 'scene_match': True, 'entity_conflict': False,
        'fatal_mismatch': True, 'reason': 'Generic portrait: exact action is not shown; wrong era, anachronistic modern imagery.'})
    assert result['accepted'] is False and result['fatal_mismatch'] is True
    assert explicit_scene_conflict({'accepted': True, 'era_mismatch': True})
    assert not explicit_scene_conflict({'reason': 'No evidence of the wrong era.'})


def test_reviewed_anachronism_requests_media_repair_instead_of_layout_only():
    issues = ['Critical anachronism: the modern family contradicts the narration about 1936.']
    assert _requires_media_repair(issues)
    assert _is_actual_media_mismatch({'issues': issues})
    assert not _is_actual_media_mismatch({'issues': ['The date overlay should include the full year.']})


def test_final_delivery_counts_grounded_graphics_separately_from_real_media(monkeypatch):
    from services import pipeline_v2_media_adapter as adapter
    monkeypatch.setattr(adapter, '_validate_renderable_asset', lambda path: (bool(path), '', 'image'))
    monkeypatch.setattr(adapter, '_visual_requirement_issue', lambda *_args: '')
    plan = {'media_paths': ['photo.jpg', 'diagram.jpg'],
            'source_labels': ['Web Images', 'Grounded Graphic Fallback'],
            'metadata': {'global_media_guarantee': {'no_blank_assets': False},
                         'media_resolution_v2': [{'status': 'accepted'}, {'status': 'accepted'}]}}
    result = adapter.validate_media_plan_for_render_v2(
        plan, [{'text': 'A historic event.'}, {'text': 'The timeline continues.'}],
        render_resolution=(640, 360), title='Timeline',
        neutral_asset_factory=lambda **_kwargs: None, allow_emergency_media=False)
    summary = result['metadata']['global_media_guarantee']
    assert summary['no_blank_assets'] is True
    assert summary['real_media_count'] == 1
    assert summary['real_media_complete'] is False
    assert summary['grounded_fallback_count'] == 1
    assert summary['grounded_fallback_indices'] == [1]
