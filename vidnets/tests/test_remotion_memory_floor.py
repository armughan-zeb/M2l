"""Serial admission must agree in Flask and the Node renderer."""
import pytest

from services import render_resource_manager as resources


@pytest.mark.parametrize('available,blocked,state', [(870,False,'low_memory'), (200,True,'waiting'), (4096,False,'ready')])
def test_ui_and_render_preflight_share_the_admission_floor(monkeypatch, available, blocked, state):
    monkeypatch.setattr(resources, '_memory_snapshot', lambda: {'total_mb':8192,'available_mb':available,'swap_free_mb':600})
    status = resources.render_memory_status()
    assert status['render_blocked'] is blocked
    assert status['admission_state'] == state
    assert status['minimum_available_mb'] == 512


def test_gta_vi_steady_state_admits_serial_render():
    # GTA VI ef306926 replay (Nov 16, 2026): 7.7 GB box, 600 MB available,
    # 600 MB swap free — the old sum-of-caps gate demanded ~1101 MB and
    # waited forever. The working-set gate admits one serial worker.
    plan = resources.select_remotion_workers(600, 7880, cpu_count=4, video_heavy=True, swap_free_mb=600)
    assert plan['recommended_workers'] == 1
    assert plan['single_worker_fallback'] == 1
    assert plan['minimum_available_mb'] == 512
    assert plan['headroom_mb'] >= plan['working_set_mb']
    # Same level with no swap headroom, and below the floor, still wait.
    assert resources.select_remotion_workers(600, 7880, cpu_count=4, video_heavy=True, swap_free_mb=0)['recommended_workers'] == 0
    assert resources.select_remotion_workers(500, 7880, cpu_count=4, video_heavy=True, swap_free_mb=600)['recommended_workers'] == 0


def test_memory_recovery_never_closes_unrelated_apps_from_stale_env(monkeypatch):
    monkeypatch.setenv('VIDZEO_CLOSE_UNNECESSARY_APPS', '1')
    monkeypatch.setattr(resources, '_memory_snapshot', lambda: {'total_mb':8192,'available_mb':870,'swap_free_mb':600})
    def forbidden(*args, **kwargs):
        raise AssertionError('A render must not close unrelated desktop applications')
    monkeypatch.setattr(resources, '_close_visible_user_apps_until_headroom', forbidden)
    assert resources.prepare_render_resources(release_idle=False)['selected_workers'] == 1


@pytest.mark.parametrize('setting', [None, '0'])
@pytest.mark.parametrize('available_mb', [200, 900])
def test_render_pressure_never_closes_other_apps_without_explicit_opt_in(monkeypatch, setting, available_mb):
    if setting is None:
        monkeypatch.delenv('VIDZEO_CLOSE_UNNECESSARY_APPS', raising=False)
    else:
        monkeypatch.setenv('VIDZEO_CLOSE_UNNECESSARY_APPS', setting)
    monkeypatch.setattr(resources, '_memory_snapshot', lambda: {
        'total_mb': 8192, 'available_mb': available_mb, 'free_mb': available_mb,
        'process_rss_mb': 100})
    monkeypatch.setattr(resources, '_trim_current_process_working_set', lambda: False)
    monkeypatch.setattr(resources, '_release_cached_gpu_blocks', lambda: False)
    def unrelated_apps(*args, **kwargs):
        raise AssertionError('Rendering cannot close unrelated apps without opt-in')
    monkeypatch.setattr(resources, '_close_visible_user_apps_until_headroom', unrelated_apps)
    if available_mb < resources.MIN_REMOTION_AVAILABLE_MB:
        with pytest.raises(resources.RenderMemoryLowError):
            resources.prepare_render_resources(release_idle=True, video_heavy=True)
    else:
        assert resources.prepare_render_resources(release_idle=True, video_heavy=True)['selected_workers'] >= 1


def test_continuous_render_has_no_elapsed_time_cutoff():
    from services.native_render_policy import _render_deadlines
    import math

    assert all(math.isinf(value) for value in _render_deadlines(
        continue_while_active=True, now=10.0, timeout_seconds=30.0))
    assert _render_deadlines(
        continue_while_active=False, now=10.0, timeout_seconds=30.0) == (40.0, 7210.0)


@pytest.mark.parametrize('total_mb', [8192, 16384, 32768])
@pytest.mark.parametrize('available_mb', [768, 1000, 1200])
@pytest.mark.parametrize('video_heavy', [False, True])
def test_one_worker_can_start_when_reserve_exhausts_headroom(total_mb, available_mb, video_heavy):
    # Steady-state swap (~600 MB free) is the headroom that lets a serial
    # render start on this VM; without it the working set does not fit.
    plan = resources.select_remotion_workers(available_mb, total_mb, cpu_count=8, video_heavy=video_heavy, swap_free_mb=600)
    assert plan['recommended_workers'] >= 1
    assert plan['minimum_available_mb'] == 512
    assert plan['memory_limit'] >= 1
    if plan['raw_memory_limit'] == 0:
        assert plan['single_worker_fallback'] == 1
        assert plan['recommended_workers'] == 1


@pytest.mark.parametrize('available_mb', [-1, 0, 200, 615, 767])
def test_insufficient_headroom_waits_even_if_worker_estimate_is_overridden(available_mb):
    # 615 MB is the Wright 1-min admission that crawled at ~23x real-time
    # with swap in use: with NO swap headroom supplied here it must wait,
    # not render. The same level admits when steady-state swap (~600 MB)
    # is present — see test_gta_vi_steady_state_admits_serial_render.
    plan = resources.select_remotion_workers(available_mb, 8192, cpu_count=8)
    assert plan['recommended_workers'] == 0
    assert plan['single_worker_fallback'] == 0


def test_headroom_adds_workers_without_exceeding_cpu_or_cap():
    plans = [resources.select_remotion_workers(m, 16384, cpu_count=16, video_heavy=True)
             for m in [1000, 1200, 2048, 4096, 8192, 16384]]
    workers = [p['recommended_workers'] for p in plans]
    assert workers == sorted(workers)
    assert workers[0] == 1
    assert workers[-1] == 8
    assert resources.select_remotion_workers(16384, 32768, cpu_count=2)['recommended_workers'] == 2


@pytest.mark.parametrize('available_mb', [768, 1000, 1200])
def test_preflight_returns_instead_of_raising_at_admission_floor(monkeypatch, available_mb):
    snapshot = {'total_mb': 16384, 'available_mb': available_mb,
                'free_mb': available_mb, 'swap_free_mb': 600, 'process_rss_mb': 100}
    monkeypatch.setattr(resources, '_memory_snapshot', lambda: dict(snapshot))
    plan = resources.prepare_render_resources(release_idle=False, video_heavy=True)
    assert plan['selected_workers'] == 1


def test_memory_wait_resumes_once_free_ram_reaches_floor(monkeypatch, tmp_path):
    from services import native_render_policy as native
    from services import job_context
    calls, sleeps = [], []
    monkeypatch.setattr(job_context, 'check_job_active', lambda: None)
    monkeypatch.setattr(job_context, 'current_job', lambda: {})
    monkeypatch.setattr(job_context, 'remaining_seconds', lambda seconds: seconds)
    monkeypatch.setattr(native.time, 'sleep', sleeps.append)

    def prepare(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise resources.RenderMemoryLowError('Waiting for memory', {'available_mb': 767})
        return resources.select_remotion_workers(768, 16384, cpu_count=8, swap_free_mb=600)

    def render(**kwargs):
        assert kwargs['memory_snapshot']['recommended_workers'] == 1
        return {'output_path': str(tmp_path / 'out.mp4')}

    monkeypatch.setattr(resources, 'prepare_render_resources', prepare)
    monkeypatch.setattr(native, '_render_semantic_documentary_unlocked', render)
    result = native.render_semantic_documentary({'width': 1920, 'height': 1080, 'scenes': []},
        tmp_path / 'out.mp4', config={'continue_render_while_active': True})
    assert len(calls) == 2
    # Worker-aware wait (Nov 10, 2026): admission is re-checked on the
    # short 0.5s poll instead of the old fixed 2.0s sleep, so the render
    # starts within ~0.5s of the worker exit that freed the memory.
    assert sleeps == [0.5]
    assert calls[0]['release_idle'] is True
    assert calls[1]['release_idle'] is False
    assert result['render_resources']['recommended_workers'] == 1
