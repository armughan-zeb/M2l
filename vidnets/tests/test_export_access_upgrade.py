import importlib.util
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from services import auth_service, export_entitlements as usage
from services.queue_manager import QueueManager, _redact_sensitive_data
from services.duration_policy import duration_fit


def test_free_duration_pacing_preserves_natural_speech():
    fit=duration_fit(64,0,60)
    assert 1 < fit['tempo'] < 1.2 and fit['target'] < 60
    with pytest.raises(ValueError, match='PLAN_LIMIT_REACHED'):
        duration_fit(85,0,60)


def test_reservation_does_not_persist_account_credentials(monkeypatch):
    rpc = Mock(return_value={'allowed': True, 'receipt': 'a'*64})
    monkeypatch.setattr(auth_service, '_rpc_request', rpc)
    data = {'auth_user_id': 'profile-one', 'request_id': 'video-one'}
    usage.reserve_generation_export(data, 'account-bearer', 55)
    assert 'account-bearer' not in repr(data)
    assert data['_export_usage_ticket'] == 'a'*64
    assert rpc.call_args.args[:2] == ('account-bearer', 'reserve_vidnets_export')


def test_quota_denial_is_an_upgrade_limit(monkeypatch):
    monkeypatch.setattr(auth_service, '_rpc_request', Mock(return_value={'allowed':False,'limit':5}))
    with pytest.raises(ValueError, match='PLAN_LIMIT_REACHED'):
        usage.reserve_generation_export({}, 'jwt', 50)


def test_reporting_outage_retains_completion_for_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(usage.config, 'TEMP_DIR', str(tmp_path))
    monkeypatch.setattr(usage, '_send', Mock(side_effect=RuntimeError('offline')))
    usage.finish_generation_export({'_export_usage_ticket':'a'*64}, True, 52)
    assert len(list((tmp_path/'usage-outbox').glob('*.json'))) == 1
    sender=Mock()
    monkeypatch.setattr(usage, '_send', sender)
    usage.flush_usage_outbox()
    assert sender.call_args.args[0]['p_success'] is True
    assert not list((tmp_path/'usage-outbox').glob('*.json'))


def test_receipts_are_private_but_survive_queue_persistence():
    data={'_export_usage_ticket':'a'*64,'access_token':'account-bearer'}
    assert _redact_sensitive_data(data) == {'_export_usage_ticket':'','access_token':''}
    assert _redact_sensitive_data(data,persistence=True) == {'_export_usage_ticket':'a'*64,'access_token':''}


def test_queue_snapshots_are_filtered_by_owner():
    qm=object.__new__(QueueManager)
    qm._queue_lock=threading.RLock()
    qm.queue=[{'id':'a','config':{'auth_user_id':'alice','_export_usage_ticket':'a'*64}},
              {'id':'b','config':{'auth_user_id':'bob'}}]
    qm.active_jobs={}
    qm.worker_running=False
    qm.worker_count=1
    assert [row['id'] for row in qm.get_status(owner_id='alice')['items']] == ['a']
    assert qm.get_item_snapshot('b',owner_id='alice') is None
    assert qm.get_item_snapshot('a',owner_id='alice')['config']['_export_usage_ticket'] == ''


@pytest.fixture
def admin(monkeypatch):
    path=Path(__file__).resolve().parents[1]/'admin-panel'/'server.py'
    spec=importlib.util.spec_from_file_location('vidnets_admin_test',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module,'service_role_is_configured',lambda:True)
    monkeypatch.setattr(module,'list_users',lambda **kwargs:[])
    return module


def test_admin_requires_same_origin_session_and_rejects_rebinding(admin):
    client=admin.app.test_client()
    base='http://127.0.0.1:5055'
    assert client.get('/api/admin/users',base_url=base).status_code == 401
    assert client.get('/',base_url='http://evil.example:5055').status_code == 403
    response=client.get('/',base_url=base)
    assert response.status_code == 200
    cookie=response.headers['Set-Cookie']
    assert 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
    assert client.get('/api/admin/users',base_url=base).status_code == 403
    headers={'X-Vidnets-Admin':'1'}
    assert client.get('/api/admin/users',base_url=base,headers=headers).status_code == 200
    headers['Origin']='https://attacker.example'
    assert client.post('/api/admin/users',base_url=base,headers=headers,json={}).status_code == 403
    assert client.get('/server.py',base_url=base).status_code == 404


def test_private_trial_cannot_replace_paid_access(monkeypatch):
    monkeypatch.setattr(auth_service,'get_user_profile_by_id',lambda _: {'id':'paid','account_status':'active'})
    monkeypatch.setattr(auth_service,'get_plan',lambda _: {'plan_key':'free_trial'})
    monkeypatch.setattr(auth_service,'_list_assignments_for_profile',lambda _, states: [{'id':'paid-plan','plan_key':'lifetime_pro'}] if states==['active'] else [])
    write=Mock(); monkeypatch.setattr(auth_service,'_rest_request',write)
    with pytest.raises(auth_service.AuthServiceError,match='already has paid access'):
        auth_service.assign_plan_to_user(actor_user_id=None,actor_email='owner',target_user_profile_id='paid',plan_key='free_trial',status='active')
    write.assert_not_called()


def test_public_trial_and_recurring_plan_requests_denied():
    for key in ('free_trial','basic_monthly','plus_yearly'):
        with pytest.raises(auth_service.AuthServiceError,match='Only Lifetime Pro'):
            auth_service.request_plan('unused',key)
