import json
import threading
from types import SimpleNamespace

import pytest

from config import normalize_generation_config, OPENROUTER_TTS_MODEL_IDS
from services import queue_manager as queues
from services import pipeline_v2_media_adapter as adapter
from services import openrouter_tts_service as speech
from services.media_asset_policy import grounded_graphics_allowed
from services.pipeline_contracts import MediaContractError
from services.visual_requirement import build_visual_requirement


def test_first_install_queue_creates_parent_and_cleans_unique_temp(tmp_path, monkeypatch):
    target = tmp_path / 'new-install' / 'server' / 'queue.json'
    monkeypatch.setattr(queues, 'QUEUE_FILE', target)
    manager = object.__new__(queues.QueueManager)
    manager._persistence_lock = threading.Lock()
    manager._queue_lock = threading.Lock()
    manager._save_tick = 0
    manager._full_backup_interval = 15
    manager.queue = [{'id': 'test', 'config': {'access_token': 'private-test'}}]
    manager._save_queue()
    assert json.loads(target.read_text())['items'][0]['id'] == 'test'
    assert 'private-test' not in target.read_text()
    assert not list(target.parent.glob('.queue-*.tmp'))


@pytest.mark.parametrize('permitted', [True, False])
@pytest.mark.parametrize('source_mode', ['Mixed Media', 'YouTube Clips'])
def test_intentional_graphic_survives_with_placeholder_permission_disabled(tmp_path, monkeypatch, permitted, source_mode):
    path = tmp_path / 'graphic_fallback_scene.png'
    path.write_bytes(b'policy-test-pixels')
    text = 'The Toyota Corolla became a global car.'
    requirement = build_visual_requirement(title='Toyota Corolla', script=text, scene_text=text)
    graphic = {'grounded': True, 'source': 'generated_motion_graphic',
               'requirement_hash': requirement['requirement_hash'],
               'entity': requirement.get('canonical_entity', ''), 'facts': [], 'text': text}
    row = {'provider': 'graphic', 'graphic_fallback': graphic,
           'resolution_kind': 'grounded_graphic_fallback'}
    plan = {'media_paths': [str(path)], 'source_labels': ['Grounded Graphic Fallback'],
            'metadata': {'media_resolution_v2': [row], 'source_lane_policy': {'mode': source_mode}}}
    monkeypatch.setattr(adapter, '_validate_renderable_asset', lambda _: (True, '', 'image'))
    kwargs = dict(render_resolution=(640, 360), title='Toyota Corolla',
                  neutral_asset_factory=lambda *_a, **_k: pytest.fail('Must not create a neutral plate'),
                  allow_emergency_media=False, allow_grounded_graphics=permitted)
    scenes = [{'scene_id': 'one', 'text': text, 'visual_requirement': requirement}]
    if permitted:
        assert adapter.validate_media_plan_for_render_v2(plan, scenes, **kwargs)['media_paths'] == [str(path)]
    else:
        with pytest.raises((MediaContractError, RuntimeError)):
            adapter.validate_media_plan_for_render_v2(plan, scenes, **kwargs)


def test_graphic_policy_does_not_enable_neutral_placeholders():
    config = normalize_generation_config({'allow_emergency_media': False})
    assert grounded_graphics_allowed(config)
    assert config['allow_emergency_media'] is False
    assert not grounded_graphics_allowed(normalize_generation_config({'media_fallback_policy': 'real_media_only'}))


def test_flux_is_retired_and_old_selections_migrate_to_free_edge():
    assert OPENROUTER_TTS_MODEL_IDS == ('fish-audio/s2.1-pro-free:free',)
    assert normalize_generation_config({'tts_model': 'deepgram/flux-tts:free'})['tts_model'] == 'audiogram'


def test_speech_duration_never_fabricates_success_from_script_length(monkeypatch):
    monkeypatch.setattr(speech.subprocess, 'run', lambda *_a, **_k: SimpleNamespace(stdout='0'))
    with pytest.raises(RuntimeError, match='could not be decoded'):
        speech._duration_seconds('invalid.mp3', 'A long script cannot prove that the audio exists.')


def test_voice_catalog_reads_do_not_wait_for_provider_refresh(monkeypatch):
    monkeypatch.setattr(speech, '_CATALOG_CACHE', {'models': None, 'expires_at': 0})
    started = threading.Event()
    release = threading.Event()
    def slow_refresh(_key=None):
        started.set()
        release.wait(2)
    monkeypatch.setattr(speech, '_refresh_speech_catalog_locked', slow_refresh)
    try:
        catalog = speech.get_openrouter_tts_catalog()
        assert started.wait(1)
        assert catalog[0]['id'] == 'fish-audio/s2.1-pro-free:free'
        assert speech.get_openrouter_tts_catalog()[0]['voices']
    finally:
        release.set()


def test_youtube_worker_reports_actual_transfer_phase_without_private_log_text(tmp_path):
    from services.youtube_worker_progress import YouTubeWorkerProgress
    import app
    path = tmp_path / 'worker.log'
    path.write_text('private-url-must-not-escape\nsource-quality range ready: clip.mp4\n', encoding='utf-8')
    updates = []
    relay = YouTubeWorkerProgress(path, 'test', emit=lambda *_a, **_k: None,
                                  progress_callback=lambda fraction, message: updates.append(message))
    relay.poll()
    relay.poll()
    assert updates == ['Downloading YouTube footage (1 clips received)']
    assert app._sanitize_user_progress_message(.54, updates[0]) == updates[0]
    assert app._sanitize_user_progress_message(.54, 'Checking media relevance 2/7') == 'Checking media relevance 2/7'


def test_cta_screen_reuses_existing_frames_and_does_not_delete_them(tmp_path, monkeypatch):
    from PIL import Image
    from services import youtube_pool_renderer as pool
    from services.scene_understanding import frame_overlay_blur
    frames = []
    for index in range(4):
        path = tmp_path / f'frame{index}.jpg'
        Image.effect_noise((320, 180), 80).convert('RGB').save(path)
        frames.append(str(path))
    reads = []
    monkeypatch.setattr(frame_overlay_blur, '_get_rapidocr', lambda: lambda pixels: (reads.append(pixels.shape) or [], {}))
    monkeypatch.setattr(pool.subprocess, 'run', lambda *_a, **_k: pytest.fail('Must reuse decoded frames'))
    assert pool._detect_creator_cta_overlay('not-decoded-again.mp4', 10, sample_frames=frames)['checked']
    assert len(reads) == 3
    assert all(__import__('pathlib').Path(path).exists() for path in frames)


def test_frame_probes_share_one_deadline(tmp_path, monkeypatch):
    from services.visual_intelligence import frame_extractor as frames
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'fixture')
    clock = [0.0]
    budgets = []
    monkeypatch.setattr(frames, '_video_duration', lambda _path: 10)
    monkeypatch.setattr(frames.time, 'monotonic', lambda: clock[0])
    def slow_decode(command, timeout):
        budgets.append(timeout)
        assert command[command.index('-threads') + 1] == '1'
        clock[0] += timeout
        return False
    monkeypatch.setattr(frames, '_run', slow_decode)
    frames.extract_clip_frames(str(clip), out_dir=str(tmp_path / 'frames'), max_frames=4)
    assert budgets == [20.0, 15.0]


def test_electron_readiness_probe_is_asynchronous_and_avoids_heavy_imports():
    from pathlib import Path
    source = Path(__file__).resolve().parents[1].joinpath('main.js').read_text(encoding='utf-8')
    probe = source.split('async function runtimePythonImportsAreReady(', 1)[1].split('async function ensureRuntimeDependencies', 1)[0]
    assert 'execFile(' in probe and 'spawnSync(' not in probe
    assert 'importlib.util.find_spec(name)' in probe
    assert 'await runtimePythonImportsAreReady(pythonExecutable)' in source


def test_only_youtube_host_dns_failures_trip_network_detection():
    from services.youtube_pipeline_v2 import _youtube_host_dns_failure
    assert _youtube_host_dns_failure("Failed to resolve www.youtube.com: getaddrinfo failed")
    assert not _youtube_host_dns_failure('HTTP 403 Forbidden at www.youtube.com')
    assert not _youtube_host_dns_failure('Failed to resolve one-unrelated-image-host.example')


def test_generic_stock_cannot_claim_to_depict_a_named_corolla():
    text = 'At low speeds, the electric motor can help move the car.'
    requirement = build_visual_requirement(title='Toyota Corolla Hybrid', script=text, scene_text=text)
    assert requirement['strict_identity']
    row = {'provider': 'stock', 'verification_status': 'deterministic_stock_last_resort',
           'candidate_evidence': {'degraded_reason': 'stock_last_resort_no_vision', 'identity_proof': False},
           'visual_decision': {'accepted': True, 'visual_verification_skipped': True}}
    assert adapter._visual_requirement_issue({'text': text, 'visual_requirement': requirement}, row, None) == 'visual_verification_skipped_for_strict_scene'


def test_narrated_vehicle_identity_outweighs_editorial_title_suffix():
    from services.visual_requirement import _script_subject
    script = 'The Toyota Corolla is built for everyday journeys. Its compact shape is practical.'
    assert _script_subject(script) == 'Toyota Corolla'
    req = build_visual_requirement(title='Toyota Corolla on the Road', script=script,
                                   scene_text='Its compact shape is practical.')
    assert req['canonical_entity'] == 'Toyota Corolla'


def test_youtube_tuning_cannot_clear_or_mutate_the_shared_backend_environment():
    from pathlib import Path
    source = Path(__file__).resolve().parents[1].joinpath('server/app.py').read_text(encoding='utf-8')
    block = source.split('def _run_youtube_clips_branch(', 1)[1].split('def _fill_unresolved_with_nearest_resolved(', 1)[0]
    assert 'yt_environment = dict(os.environ)' in block
    assert 'environment=yt_environment' in block
    assert 'os.environ[' not in block and 'os.environ.clear(' not in block


def test_dns_outage_stops_retrying_other_video_ids(tmp_path, monkeypatch):
    from services import youtube_pipeline_v2 as youtube
    import os
    candidates = [dict(id=f'abcdefghij{i}', title='Toyota Corolla Hybrid driving footage',
                       description='Toyota Corolla road test footage', channel='Toyota', duration=180)
                  for i in range(5)]
    monkeypatch.setattr(youtube, '_search_youtube', lambda *_a, **_k: [dict(row) for row in candidates])
    monkeypatch.setattr(youtube, '_get_authenticated_youtube_session', lambda *_a, **_k: None)
    attempted = []
    def unavailable(candidate, *_a, **_k):
        attempted.append(candidate['id'])
        candidate['_transport_failure'] = 'provider_dns_unavailable'
        return False
    monkeypatch.setattr(youtube, '_download_clip', unavailable)
    monkeypatch.setenv('VIDZEO_YT_POOL_FIRST', '0')
    monkeypatch.setenv('VIDZEO_YT_V2_SKIP_DOWNLOAD', '0')
    rows = [dict(_llm=True, scene_subject='Toyota Corolla', section_subject='Toyota Corolla',
                 youtube_query='Toyota Corolla hybrid driving footage') for _ in range(4)]
    result = youtube.get_youtube_clips(script='The Toyota Corolla drives down a road.', title='Toyota Corolla',
        num_scenes=4, scene_texts=['The Toyota Corolla drives down a road.'] * 4,
        scene_durations=[4] * 4, scene_query_overrides=rows, output_dir=str(tmp_path),
        download_concurrency=1, max_download_attempts_per_scene=4)
    assert result.audit['provider_network_unavailable'] is True
    assert result.audit['stop_reason'] == 'provider_network_unavailable'
    assert len(attempted) == 2
