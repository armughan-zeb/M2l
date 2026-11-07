"""Validate alternate free vision models against observed public catalog data."""
import threading
import time
import requests

DEFAULT_MODEL = 'qwen/qwen3.5-flash-02-23'
_lock = threading.Lock()
_cache = {'expires': 0, 'ids': set()}
def _zero(value):
    try:
        return float(value) == 0
    except (TypeError, ValueError):
        return False

def confirm_free_vision_model(model):
    if model in {DEFAULT_MODEL, 'qwen/qwen3.5-flash-02-23'}:
        return model
    if not str(model).endswith(':free'):
        raise ValueError('Automatic routing and paid model replacements are disabled')
    with _lock:
        if time.monotonic() >= _cache['expires']:
            try:
                response = requests.get('https://openrouter.ai/api/v1/models', timeout=10)
                response.raise_for_status()
                rows = response.json().get('data') or []
                ids = {row['id'] for row in rows if str(row.get('id', '')).endswith(':free')
                       and 'image' in (row.get('architecture', {}).get('input_modalities') or [])
                       and _zero(row.get('pricing', {}).get('prompt'))
                       and _zero(row.get('pricing', {}).get('completion'))}
            except Exception as exc:
                raise ValueError('Free vision-model catalog could not be confirmed; saved work is retained. Retry the same selected model.') from exc
            _cache.update(expires=time.monotonic()+3600, ids=ids)
        if model not in _cache['ids']:
            raise ValueError('Selected model is absent from the free image-capable catalog; choose a supported model in Settings')
    return model
