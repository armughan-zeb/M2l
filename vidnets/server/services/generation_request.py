"""Public generation request boundary. Internal checkpoint keys remain private."""
import math


def normalize_request(payload):
    if not isinstance(payload, dict):
        raise ValueError('Generation request must be a JSON object.')
    nested = payload.get('config')
    if nested is not None and not isinstance(nested, dict):
        raise ValueError('config must be a JSON object.')
    # Accept the earlier client envelope explicitly; flat keys take precedence.
    data = {**(nested or {}), **{key: value for key, value in payload.items() if key != 'config'}}
    if any(str(key).startswith('_') for key in data):
        raise ValueError('Internal generation settings cannot be supplied through the API.')
    if data.get('media_retrieval_only') is True:
        raise ValueError('Media retrieval-only mode is reserved for the local CMS diagnostic bridge.')
    version = data.get('request_schema_version', 1)
    if version != 1:
        raise ValueError('Unsupported generation request version; use request_schema_version 1.')
    for key in ('delivery_budget_seconds', 'render_reserve_seconds', 'creative_review_budget_seconds',
                'semantic_render_timeout_seconds', 'semantic_render_budget_seconds'):
        if key in data:
            try:
                value = float(data[key])
            except (TypeError, ValueError):
                raise ValueError(f'{key} must be a positive finite number.') from None
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'{key} must be a positive finite number.')
            data[key] = value
    if 'script' in data and not isinstance(data['script'], str):
        raise ValueError('script must be text.')
    if 'images' in data and not isinstance(data['images'], list):
        raise ValueError('images must be a list.')
    if 'request_id' in data and (not isinstance(data['request_id'], str) or not data['request_id']):
        raise ValueError('request_id must be nonempty text when supplied.')
    mode = data.get('execution_mode', 'sync')
    if mode not in {'sync', 'queue'}:
        raise ValueError('execution_mode must be sync or queue.')
    if data.get('cost_profile', 'efficient') not in {'efficient', 'custom'}:
        raise ValueError('cost_profile must be efficient or custom.')
    if data.get('response_mode', 'json') not in {'json', 'video'}:
        raise ValueError('response_mode must be json or video.')
    if mode == 'queue' and data.get('response_mode') == 'video':
        raise ValueError('Video responses require synchronous generation.')
    data['request_schema_version'] = 1
    return data
