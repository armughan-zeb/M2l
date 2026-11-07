"""Opt-in, prompt-free provider billing ledger for local generation audits."""
import json
import math
import os
from pathlib import Path
import threading
import uuid
from datetime import datetime, timezone

_LOCK = threading.Lock()
_SEEN = set()


def _api_provider(row):
    """Distinguish the gateway from its upstream model host (e.g. Alibaba)."""
    explicit = row.get('api_provider')
    if explicit in {'openrouter', 'external_agent', 'chatgpt'}:
        return explicit
    provider = row.get('provider')
    if provider in {'external_agent', 'chatgpt'}:
        return provider
    if row.get('billing') == 'external_agent_runtime':
        return 'external_agent'
    return 'openrouter'


def record_cache_hit(model, operation, *, kind='text'):
    """Local savings evidence; a cache hit is not a provider generation."""
    from .job_context import current_job
    context = current_job()
    if not context.get('usage_path'):
        return
    try:
        path = Path(context['usage_path']).with_name('cost-optimizations.jsonl')
        row = {'attempt_id': context.get('attempt_id'), 'model': model,
               'operation': operation, 'kind': kind, 'event': 'validated_response_reused',
               'timestamp': datetime.now(timezone.utc).isoformat()}
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row) + '\n')
    except OSError:
        pass


def record_usage(payload, *, kind, model='', operation=''):
    from .job_context import current_job
    job = current_job()
    local_target = job.get('usage_path')
    shared_target = os.environ.get('VIDZEO_OPENROUTER_USAGE_LOG', '').strip()
    targets = list(dict.fromkeys(str(path) for path in (local_target, shared_target) if path))
    if not targets or not isinstance(payload, dict):
        return
    try:
        usage = payload.get('usage') if isinstance(payload.get('usage'), dict) else {}
        cost = usage.get('cost', usage.get('total_cost', payload.get('cost')))
        cost = float(cost) if cost is not None else None
        if cost is not None and (not math.isfinite(cost) or cost < 0):
            cost = None
        finished = job.get('finished_event')
        row = {'timestamp': datetime.now(timezone.utc).isoformat(),
               'job_id': job.get('job_id'), 'attempt_id': job.get('attempt_id'),
               'late_response': bool(finished and finished.is_set()),
               'generation_id': str(payload.get('id') or '')[:128],
               'kind': kind, 'model': str(payload.get('model') or model)[:160],
               'provider': payload.get('provider') or 'openrouter',
               'billing': payload.get('billing') or 'openrouter_credits',
               'operation': operation, 'reported_cost_usd': cost,
               'usage': {key: usage[key] for key in
                         ('prompt_tokens', 'completion_tokens', 'total_tokens', 'cost', 'total_cost') if key in usage}}
        row['api_provider'] = _api_provider({**row, 'api_provider': payload.get('api_provider')})
        # Keep only numeric billing details: no prompts, images or secrets.
        for detail_name, fields in (
                ('prompt_tokens_details', ('cached_tokens', 'cache_write_tokens')),
                ('completion_tokens_details', ('reasoning_tokens',))):
            detail = usage.get(detail_name)
            if isinstance(detail, dict):
                numbers = {}
                for field in fields:
                    value = detail.get(field)
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                        numbers[field] = value
                if numbers:
                    row['usage'][detail_name] = numbers
        with _LOCK:
            for target in targets:
                identity = (target, row['generation_id'])
                if row['generation_id'] and identity in _SEEN:
                    continue
                path = Path(target)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(row) + '\n')
                if row['generation_id']:
                    _SEEN.add(identity)
            if len(_SEEN) > 50000:
                _SEEN.clear()
        if finished and finished.is_set() and local_target:
            write_job_receipt(job, None)
    except Exception:
        # Billing telemetry must never fail a generation or log credentials.
        return


def write_job_receipt(context, status):
    """Receipt is a snapshot; the ledger remains authoritative for late charges."""
    try:
        path = Path(context['usage_path'])
        target = path.parent / f"cost-{context['attempt_id']}.json"
        with _LOCK:
            rows = []
            if path.exists():
                for line in path.read_text(encoding='utf-8').splitlines():
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict) and row.get('attempt_id') == context['attempt_id']:
                            rows.append(row)
                    except (TypeError, ValueError):
                        continue
            previous = {}
            if status is None and target.exists():
                try:
                    previous = json.loads(target.read_text(encoding='utf-8'))
                except (TypeError, ValueError):
                    pass
            priced = [row for row in rows if row.get('reported_cost_usd') is not None]
            receipt = {'job_id': context['job_id'], 'attempt_id': context['attempt_id'],
                       'status': status or previous.get('status') or 'finished',
                       'captured_generation_count': len(rows),
                       'provider_reported_usd': sum(row['reported_cost_usd'] for row in priced),
                       'unpriced_generation_count': len(rows) - len(priced),
                       'late_response_count': sum(bool(row.get('late_response')) for row in rows),
                       'note': 'Captured provider charges only; unpriced and later responses remain in the job ledger. No account balance delta is treated as job cost.'}
            savings = path.with_name('cost-optimizations.jsonl')
            hits = 0
            if savings.exists():
                for line in savings.read_text(encoding='utf-8').splitlines():
                    try:
                        row = json.loads(line)
                        hits += row.get('attempt_id') == context['attempt_id']
                    except (ValueError, TypeError):
                        continue
            receipt['validated_response_cache_hits'] = hits
            def token_count(usage, field, detail=None):
                nested = usage.get(detail) if detail else None
                value = nested.get(field) if isinstance(nested, dict) else (None if detail else usage.get(field))
                return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else 0
            breakdown = {}
            for row in rows:
                key = str(row.get('operation') or row.get('kind') or 'unspecified')
                group = breakdown.setdefault(key, {'request_count': 0, 'provider_reported_usd': 0.,
                    'unpriced_request_count': 0, 'input_tokens': 0, 'output_tokens': 0,
                    'provider_cached_input_tokens': 0, 'provider_cache_write_tokens': 0,
                    'reasoning_tokens': 0})
                usage = row.get('usage') if isinstance(row.get('usage'), dict) else {}
                group['request_count'] += 1
                group['provider_reported_usd'] += row.get('reported_cost_usd') or 0.
                group['unpriced_request_count'] += row.get('reported_cost_usd') is None
                for target_field, field, detail in (
                    ('input_tokens', 'prompt_tokens', None), ('output_tokens', 'completion_tokens', None),
                    ('provider_cached_input_tokens', 'cached_tokens', 'prompt_tokens_details'),
                    ('provider_cache_write_tokens', 'cache_write_tokens', 'prompt_tokens_details'),
                    ('reasoning_tokens', 'reasoning_tokens', 'completion_tokens_details')):
                    group[target_field] += token_count(usage, field, detail)
            receipt['operations'] = breakdown
            for field in ('input_tokens', 'output_tokens', 'provider_cached_input_tokens',
                          'provider_cache_write_tokens', 'reasoning_tokens'):
                receipt[field] = sum(group[field] for group in breakdown.values())
            # These are avoided local calls, not provider cache hits or an
            # invented dollar saving. Keep all three concepts separate.
            cache_kinds = {}
            if savings.exists():
                for line in savings.read_text(encoding='utf-8').splitlines():
                    try:
                        row = json.loads(line)
                        if row.get('attempt_id') == context['attempt_id']:
                            kind = str(row.get('kind') or 'text')
                            cache_kinds[kind] = cache_kinds.get(kind, 0) + 1
                    except (ValueError, TypeError, AttributeError):
                        continue
            receipt['local_response_cache_hits_by_kind'] = cache_kinds
            receipt['cost_profile'] = context.get('cost_profile', 'custom')
            receipt['ai_provider'] = context.get('llm_provider', 'openrouter')
            receipt['external_agent_request_count'] = sum(_api_provider(row) == 'external_agent' for row in rows)
            receipt['openrouter_request_count'] = sum(_api_provider(row) == 'openrouter' for row in rows)
            if receipt['ai_provider'] == 'external_agent':
                receipt['note'] += ' Reasoning is supplied by the external agent. Its own usage and other services are not included in OpenRouter cost.'
            receipt['chatgpt_plan_request_count'] = sum(_api_provider(row) == 'chatgpt' for row in rows)
            if receipt['chatgpt_plan_request_count']:
                receipt['note'] += ' ChatGPT requests use the selected user plan; no API dollar price is inferred for subscription usage.'
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.part')
            try:
                temporary.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
    except Exception:
        pass
