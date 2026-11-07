"""Live search/download/decode check using the production stock adapters.

Keys are supplied only in the child environment by CMS. Provider logs are
captured, never persisted, because transport exceptions may contain URLs.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from services.real_media_service import MediaConfig, MultiSourceSearchEngine, MediaDownloader
from services.media_integrity import resolve_ffmpeg_pair


def verify(provider, output):
    cfg = MediaConfig(max_results_per_source=3, search_timeout=15,
                      download_timeout=20, max_file_size_mb=35,
                      cache_dir=str(output / provider))
    cfg.pexels_api_key = os.environ.get('PEXELS_API_KEY', '') if provider == 'pexels' else ''
    cfg.pixabay_api_key = os.environ.get('PIXABAY_API_KEY', '') if provider == 'pixabay' else ''
    cfg.stock_deadline_at = time.monotonic() + 90
    engine = MultiSourceSearchEngine(cfg)
    transport = engine.session.get
    statuses = []
    def observed_get(*args, **kwargs):
        response = transport(*args, **kwargs)
        statuses.append(response.status_code)
        return response
    engine.session.get = observed_get
    rows = []
    for query in ('car driving road', 'car', 'railway'):
        rows = getattr(engine, '_' + provider + '_videos')(query)
        if rows:
            break
    downloader = MediaDownloader(cfg)
    ffmpeg, ffprobe = resolve_ffmpeg_pair(ROOT)
    result = {'provider': provider, 'query': query, 'candidates': len(rows), 'httpStatuses': statuses, 'ok': False}
    for item in rows[:3]:
        if not downloader.download_item(item):
            continue
        probe = subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-show_format',
                                '-of', 'json', item.local_path], capture_output=True, timeout=15)
        if probe.returncode:
            continue
        data = json.loads(probe.stdout)
        video = next((s for s in data['streams'] if s.get('codec_type') == 'video'), {})
        decoded = subprocess.run([ffmpeg, '-v', 'error', '-xerror', '-i', item.local_path,
                                  '-f', 'null', '-'], capture_output=True, timeout=45)
        if decoded.returncode or not video or float(data['format'].get('duration', 0)) <= 0:
            continue
        path = Path(item.local_path)
        result.update(ok=True, alias=str(path.relative_to(output)), bytes=path.stat().st_size,
                      sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                      width=video['width'], height=video['height'],
                      duration=float(data['format']['duration']), fullDecode=True,
                      sourceUrl=item.score_breakdown.get('source_url'),
                      creator=item.score_breakdown.get('creator'),
                      retrievalMethod=item.score_breakdown.get('retrieval_method'))
        break
    return result


if __name__ == '__main__':
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for provider in ('pexels', 'pixabay'):
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                results.append(verify(provider, output))
        except Exception as exc:
            results.append({'provider': provider, 'ok': False, 'errorType': type(exc).__name__})
    report = {'ok': all(r['ok'] for r in results), 'results': results}
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
    sys.exit(0 if report['ok'] else 1)
