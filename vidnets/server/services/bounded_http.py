"""Bound provider response waits independently of streaming socket activity."""
from contextvars import copy_context
import threading
import time
import requests
from .job_context import check_job_active, remaining_seconds

_SLOTS = threading.BoundedSemaphore(12)


def provider_post(post, url, *, headers, json, timeout, usage_kind='text', operation=''):
    check_job_active()
    from .job_context import current_job
    from .agent_bridge import AGENT_HANDLE, agent_post
    context_state = current_job()
    raw_handle = str((headers or {}).get('Authorization', '')).removeprefix('Bearer ')
    agent_mode = context_state.get('llm_provider') == 'external_agent' or raw_handle.startswith(AGENT_HANDLE)
    if raw_handle.startswith(AGENT_HANDLE) and context_state.get('llm_provider') == 'openrouter':
        raise ValueError('External-agent requests cannot be used in a manual OpenRouter job.')
    if agent_mode:
        if usage_kind == 'tts':
            raise ValueError('External-agent jobs use the local Audiogram voice engine.')
        response = agent_post(json, timeout, operation=operation, kind=usage_kind, handle=raw_handle)
        from .openrouter_usage import record_usage
        record_usage(response.json(), kind=usage_kind, operation=operation, model='external-agent')
        return response
    from .provider_selection import request_account
    account_id = request_account(headers)
    if account_id and usage_kind == 'tts':
        raise ValueError('ChatGPT plan usage does not support TTS. Select the local Audiogram voice engine.')
    if (not account_id and (current_job().get('cost_profile') == 'efficient' or context_state.get('llm_provider') == 'openrouter') and usage_kind != 'tts'
            and url == 'https://openrouter.ai/api/v1/chat/completions'):
        from .provider_selection import normalize_provider_config
        selected = normalize_provider_config({'llm_provider':'openrouter', 'llm_model':json.get('model')})['llm_model']
        json = {**json, 'model': selected}
        json.pop('models', None)
    wall = min(float(timeout), remaining_seconds(float(timeout)))
    deadline = time.monotonic() + wall
    if wall <= 0:
        raise requests.Timeout('Provider request budget or available worker slots exhausted.')
    while not _SLOTS.acquire(timeout=min(.2, max(.001, deadline - time.monotonic()))):
        check_job_active()
        if time.monotonic() >= deadline:
            raise requests.Timeout('Provider workers remained busy until the request deadline.')
    wall = max(.001, deadline - time.monotonic())
    output = {}
    completed = threading.Event()
    context = copy_context()

    def request_once():
        try:
            check_job_active()
            # Retry and connectivity recovery belong to the bounded stage owner.
            # A transport attempt must never extend its own wall-clock deadline.
            if account_id:
                from .chatgpt_inference import chatgpt_post
                response = chatgpt_post(account_id, json, max(.1, wall))
            else:
                response = post(url, headers=headers, json=json, timeout=max(.1, wall))
            output['response'] = response
            if 200 <= response.status_code < 300:
                try:
                    from .openrouter_usage import record_usage
                    record_usage(response.json(), kind=usage_kind, operation=operation,
                                 model=json.get('model', ''))
                except Exception:
                    if usage_kind == 'tts':
                        from .openrouter_usage import record_usage
                        response_headers = getattr(response, 'headers', {}) or {}
                        request_id = response_headers.get('x-generation-id') or response_headers.get('x-request-id') or ''
                        record_usage({'id': request_id if str(request_id).startswith('gen-') else ''},
                                     kind='tts', operation=operation, model=json.get('model', ''))
        except BaseException as exc:
            output['error'] = exc
        finally:
            output['completed_at'] = time.monotonic()
            _SLOTS.release()
            completed.set()

    worker = threading.Thread(target=context.run, args=(request_once,), daemon=True,
                              name='vidzeo-provider-http')
    try:
        worker.start()
    except BaseException:
        _SLOTS.release()
        raise
    while not completed.wait(min(.2, max(.001, deadline - time.monotonic()))):
        check_job_active()
        if time.monotonic() >= deadline:
            raise requests.Timeout(f'Provider response exceeded its {wall:.1f}s wall budget.')
    check_job_active()
    if output.get('completed_at', time.monotonic()) > deadline:
        raise requests.Timeout(f'Provider response exceeded its {wall:.1f}s wall budget.')
    if 'error' in output:
        raise output['error']
    return output['response']
