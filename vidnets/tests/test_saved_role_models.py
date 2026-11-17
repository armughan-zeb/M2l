import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from services.provider_selection import normalize_provider_config
from services.generation_cost_policy import apply_generation_cost_policy
from services.bounded_http import provider_post
from services.job_context import job_scope

FREE = 'nvidia/nemotron-3.5-super-vl:free'
QWEN = 'qwen/qwen3.5-flash-02-23'

def test_cost_mode_retains_distinct_saved_role_models():
    value = apply_generation_cost_policy({'llm_provider':'openrouter', 'llm_model':FREE,
        'scene_brain_model':QWEN, 'qwen_vision_model':QWEN})
    assert value['llm_model'] == FREE
    assert value['scene_brain_model'] == QWEN
    assert value['qwen_vision_model'] == QWEN
    assert value['scene_brain_fallback_chain'] == [QWEN]
    assert normalize_provider_config({})['llm_model'] == QWEN
    with pytest.raises(ValueError, match='paid models'):
        normalize_provider_config({'llm_model':FREE, 'vision_model':'provider/paid'})

def test_outbound_transport_preserves_selected_model_and_rejects_paid_models():
    captured = []
    def post(url, **kwargs):
        captured.append(kwargs['json'])
        return SimpleNamespace(status_code=200, json=lambda: {})
    with job_scope({'llm_provider':'openrouter', 'cost_profile':'efficient'}):
        provider_post(post, 'https://openrouter.ai/api/v1/chat/completions', headers={}, json={'model':QWEN}, timeout=1)
        with pytest.raises(ValueError, match='paid models'):
            provider_post(post, 'https://openrouter.ai/api/v1/chat/completions', headers={}, json={'model':'provider/paid'}, timeout=1)
    assert captured == [{'model':QWEN}]

def test_socket_failure_returns_to_stage_owner_without_unbounded_connectivity_wait():
    post = Mock(side_effect=requests.Timeout('offline fixture'))
    with patch('services.internet_resilience.wait_for_internet_connectivity') as wait:
        with pytest.raises(requests.Timeout, match='offline fixture'):
            provider_post(post, 'https://invalid.test', headers={}, json={'model':FREE}, timeout=1)
    assert post.call_count == 1
    wait.assert_not_called()


def test_clients_retain_scene_and_vision_role_selection():
    from services.openrouter_llm import OpenRouterLLM
    from services.visual_intelligence.vision_client import QwenVisionClient
    with job_scope({'llm_provider':'openrouter', 'cost_profile':'efficient', 'llm_model':FREE,
                    'scene_brain_model':QWEN, 'qwen_vision_model':QWEN}):
        assert OpenRouterLLM('test-key', model=QWEN).model_chain == (QWEN,)
        assert QwenVisionClient('test-key', model=FREE).model == QWEN
