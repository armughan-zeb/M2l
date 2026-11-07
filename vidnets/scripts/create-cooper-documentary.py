"""One-minute main-pipeline documentary and provider-reported billing audit."""
import json
import os
from pathlib import Path
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'cooper_one_minute_20261005'
OUT.mkdir(parents=True, exist_ok=True)
for filename in ('.env.public', '.env.defaults', '.env'):
    load_dotenv(ROOT / filename, override=True)
KEY = os.environ.get('OPENROUTER_API_KEY') or os.environ.get('VIDZEO_OPENROUTER_API_KEY') or ''
HEADERS = {'Authorization': 'Bearer ' + KEY}
SCRIPT = (
    'On November 24, 1971, a man bought a one-way ticket from Portland to Seattle. He used the name Dan Cooper. '
    'Once airborne, he told a flight attendant there was a bomb in his briefcase. His demands were precise: four parachutes and $200,000. '
    'In Seattle, 36 passengers walked free. Cooper stayed aboard with members of the crew, the ransom, and the parachutes. '
    'The plane took off again. A little after eight p.m., somewhere between Seattle and Reno, he jumped from the rear of the aircraft into the darkness. '
    'The FBI called the investigation NORJAK. Within five years, investigators had considered more than 800 suspects. '
    'Then, in 1980, a boy found $5,800 in decaying twenty-dollar bills. Their serial numbers matched the ransom. '
    'It was a real clue, but it did not establish what happened to Cooper after the jump. '
    'In 2016, the FBI redirected the case\'s resources toward other investigations. '
    'The hijacker\'s identity and ultimate fate remain unresolved. A ticket began the mystery. The recovered money could not finish it.'
)


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, default=str), encoding='utf-8')


def account_usage():
    try:
        r = requests.get('https://openrouter.ai/api/v1/key', headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()['data']
        return {'timestamp': datetime.now(timezone.utc).isoformat(),
                **{name: data.get(name) for name in ('usage', 'byok_usage')}}
    except Exception as exc:
        return {'status': 'unavailable', 'error_type': type(exc).__name__}


def cost_report():
    before = json.loads((OUT / 'usage-before.json').read_text(encoding='utf-8'))
    after = account_usage()
    write('usage-after.json', after)
    ledger = OUT / 'openrouter-usage.jsonl'
    rows = [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines() if line.strip()] if ledger.exists() else []
    unique = {row['generation_id']: row for row in rows if row.get('generation_id')}
    def lookup(item):
        generation_id, row = item
        try:
            r = requests.get('https://openrouter.ai/api/v1/generation', params={'id': generation_id}, headers=HEADERS, timeout=12)
            r.raise_for_status()
            data = r.json()['data']
            return {**row, 'billing_source': 'openrouter_generation_api',
                    'actual_cost_usd': data['total_cost'], 'provider': data.get('provider_name'),
                    'model': data.get('model') or row['model']}
        except Exception as exc:
            return {**row, 'billing_source': 'openrouter_response_usage' if row.get('reported_cost_usd') is not None else 'unavailable',
                    'actual_cost_usd': row.get('reported_cost_usd'), 'lookup_error_type': type(exc).__name__}
    with ThreadPoolExecutor(max_workers=4) as pool:
        generations = list(pool.map(lookup, unique.items()))
    missing = [row for row in generations if row.get('actual_cost_usd') is None]
    total = sum(float(row['actual_cost_usd']) for row in generations if row.get('actual_cost_usd') is not None)
    delta = float(after['usage']) - float(before['usage']) if after.get('usage') is not None and before.get('usage') is not None else None
    by_model = {}
    for row in generations:
        bucket = by_model.setdefault(row['model'], {'requests': 0, 'cost_usd': 0})
        bucket['requests'] += 1
        bucket['cost_usd'] += float(row.get('actual_cost_usd') or 0)
    result = {'currency': 'USD', 'provider_reported_generation_total_usd': total,
              'key_usage_delta_usd': delta, 'logged_generations': len(generations),
              'unpriced_generations': len(missing), 'by_model': by_model, 'generations': generations,
              'note': 'Per-generation charges are direct OpenRouter billing data. Key usage delta includes concurrent use of this key or unlogged requests. Free Audiogram narration and local rendering are not OpenRouter charges.'}
    write('cost-report.json', result)
    print(json.dumps({name: result[name] for name in ('provider_reported_generation_total_usd', 'key_usage_delta_usd', 'logged_generations', 'unpriced_generations')}), flush=True)


def main():
    if '--cost-only' in sys.argv:
        cost_report()
        return
    before = account_usage()
    write('usage-before.json', before)
    if before.get('usage') is None:
        raise RuntimeError('OpenRouter billing access unavailable; no generation started')
    original = json.loads((ROOT / 'artifacts/niche_editing_revision/api-smoke/request-final.json').read_text(encoding='utf-8'))
    config = dict(original['config'])
    name = 'D.B. Cooper - The Disappearance'
    config.update({'title': name, 'content_title': 'D.B. Cooper 1971 FBI hijacking investigation NORJAK',
        'output_name': name, 'duration': '1 minute', 'render_resolution': [1280, 720],
        'delivery_budget_seconds': 720, 'media_deadline_seconds': 180, 'visual_deadline_seconds': 120,
        'render_reserve_seconds': 150, 'creative_review_budget_seconds': 180,
        'creative_revision_rounds': 1, 'graphic_density': .32,
        'voice': 'Brian (Male US)', 'edge_tts_options': {'rate': '-8%'},
        'strict_creative_review': False})
    # /api/generate consumes a flat request, matching the desktop client.
    # Nesting these settings under config silently selects server defaults.
    payload = {**config, 'title': config['content_title'], 'script': SCRIPT, 'output_name': name,
               'render_resolution': [1280, 720]}
    write('request.json', payload)
    (OUT / 'script.md').write_text('# D.B. Cooper: The Disappearance\n\n' + SCRIPT + '\n\nSource: https://www.fbi.gov/history/cases-and-criminals/db-cooper-hijacking\n', encoding='utf-8')
    started = time.monotonic()
    print(json.dumps({'status': 'started', 'word_count': len(SCRIPT.split()), 'resolution': '1280x720'}), flush=True)
    record = {'endpoint': '/api/generate'}
    try:
        response = requests.post('http://127.0.0.1:5000/api/generate', json=payload, timeout=1200)
        record.update({'http_status': response.status_code, 'response': response.json()})
    except Exception as exc:
        record['transport_error_type'] = type(exc).__name__
    record['elapsed_seconds'] = round(time.monotonic() - started, 2)
    write('response.json', record)
    result = record.get('response') or {}
    if result.get('output_package_dir'):
        source = Path(result['output_package_dir']).resolve()
        source.relative_to((ROOT / 'output').resolve())
        shutil.copytree(source, OUT / 'generation-package', dirs_exist_ok=True)
    print(json.dumps({name: record[name] for name in ('http_status', 'elapsed_seconds', 'transport_error_type') if name in record}), flush=True)
    cost_report()


if __name__ == '__main__':
    main()
