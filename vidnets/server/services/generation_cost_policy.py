"""Explicit generation routing: no silent upgrade from the efficient model."""
DEFAULT_MODEL = 'qwen/qwen3.5-flash-02-23'


def apply_generation_cost_policy(config):
    result = dict(config)
    profile = str(result.get('cost_profile') or 'efficient').strip().lower()
    if profile not in {'efficient', 'custom'}:
        raise ValueError('cost_profile must be efficient or custom.')
    result['cost_profile'] = profile
    from .provider_selection import normalize_provider_config
    result = normalize_provider_config(result)
    # Role choices have already been validated. Cost mode must not silently
    # replace a saved scene-planning or vision selection with the primary model.
    return result
