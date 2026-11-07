"""Opt-in isolated transport/decode check; never queues, renders or uploads."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('lane', choices=['youtube', 'web'])
    parser.add_argument('--label', default='')
    parser.add_argument('--isolated-cache', action='store_true')
    parser.add_argument('--cache-from', default='')
    args = parser.parse_args()
    if any(value and not re.fullmatch(r'[A-Za-z0-9_-]+', value)
           for value in (args.label, args.cache_from)):
        parser.error('Benchmark labels must contain only letters, digits, underscores or hyphens')
    from dotenv import dotenv_values
    for key, value in dotenv_values(ROOT / '.env.defaults').items():
        if value is not None and key.startswith(('VIDZEO_YT_', 'VIDZEO_WEB_', 'VIDZEO_MIXED_YT_')):
            os.environ[key] = value
    # This diagnostic measures provider transport, without a model invocation.
    for key in ('OPENROUTER_API_KEY', 'VIDZEO_OPENROUTER_API_KEY'):
        os.environ.pop(key, None)
    output = (ROOT / 'artifacts' / 'retrieval-speed-2026-10-19' / args.label / args.lane
              if args.label else ROOT / 'artifacts' / 'retrieval-yield-2026-10-19' / args.lane)
    output.mkdir(parents=True, exist_ok=True)
    if args.isolated_cache:
        cache_output = (ROOT / 'artifacts' / 'retrieval-speed-2026-10-19' / args.cache_from / args.lane
                        if args.cache_from else output)
        os.environ['VIDZEO_YT_CUT_CACHE_DIR'] = str(cache_output / 'cut-cache')
        os.environ['VIDZEO_YT_CHAPTER_CACHE_DIR'] = str(cache_output / 'chapter-cache')
    timings = {}
    timing_lock = threading.Lock()

    def instrument(module, name):
        original = getattr(module, name)
        def measured(*values, **kwargs):
            before = time.monotonic()
            try:
                return original(*values, **kwargs)
            finally:
                with timing_lock:
                    bucket = timings.setdefault(name, {'calls': 0, 'workerSeconds': 0.0})
                    bucket['calls'] += 1
                    bucket['workerSeconds'] = round(bucket['workerSeconds'] + time.monotonic() - before, 3)
        setattr(module, name, measured)
    scenes = [
        'The streamlined LNER Class A4 steam locomotive runs along the railway.',
        'The driving wheels and valve gear of the LNER Class A4 locomotive move along the track.',
        'A preserved LNER Class A4 locomotive stands beside a station platform.',
    ]
    queries = [
        ['LNER Class A4 steam train running', 'LNER A4 locomotive mainline steam'],
        ['LNER Class A4 locomotive wheels', 'LNER A4 steam train close up'],
        ['LNER Class A4 locomotive station', 'Sir Nigel Gresley A4 locomotive platform'],
    ]
    started = time.monotonic()
    if args.lane == 'youtube':
        from services import youtube_pipeline_v2 as youtube
        from services import youtube_source_chapters as chapters
        for name in ('_search_container_pools', '_download_clip', '_clean_downloaded_clip_overlays'):
            instrument(youtube, name)
        instrument(chapters, 'source_chapters')
        from services.youtube_pipeline_v2 import get_youtube_clips
        result = get_youtube_clips(
            script=' '.join(scenes), title='LNER Class A4', num_scenes=3,
            scene_texts=scenes, scene_durations=[4.0] * 3,
            scene_query_overrides=[{
                '_llm': True, 'section_subject': 'LNER Class A4',
                'scene_subject': 'LNER Class A4', 'domain_category': 'history',
                'youtube_query': variants[0], 'query_variants': variants,
            } for variants in queries],
            output_dir=output, pool_first=False, use_authenticated_session=True,
            lenient_acceptance=True, download_candidate_limit=6,
            max_download_attempts_per_scene=6, download_concurrency=3,
            search_timeout_seconds=50, hard_timeout_seconds=240,
        )
        paths = list(result)
        audit = getattr(result, 'audit', {})
        statuses = Counter(a.get('status', 'unknown') for attempts in audit.get('attempts', {}).values() for a in attempts)
    else:
        from services.web_image import resolver
        for name in ('_download_candidate_batch', '_warm_shared_discovery_pool'):
            instrument(resolver, name)
        from services.web_image.resolver import resolve_web_images_for_video_fast
        os.environ['VIDZEO_WEB_STAGE_DEADLINE_SECONDS'] = '120'
        result = resolve_web_images_for_video_fast(
            script=' '.join(scenes), title='LNER Class A4',
            scene_texts=scenes, scene_durations=[4.0] * 3,
            scene_queries_override=[{'query_variants': variants} for variants in queries],
            subject_override='LNER Class A4', output_namespace=f'yield_live_{int(time.time())}',
            fast_mode=True, max_candidate_downloads=6, max_scene_workers=3,
            api_worker_limit=8, visual_context=None,
        )
        paths = result.get('image_paths', [])
        audit = result.get('audit') or {}
        rows = result.get('results') or (result.get('audit') or {}).get('scenes') or []
        statuses = Counter(row.get('provider', 'unknown') for row in rows if row and row.get('path'))
    artifacts = []
    from services.youtube_pipeline_v2 import _ffmpeg_bin
    for index, value in enumerate(paths):
        if not value or not Path(value).is_file():
            continue
        path = Path(value)
        row = {'scene': index + 1, 'alias': path.name, 'bytes': path.stat().st_size,
               'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        if args.lane == 'web':
            from PIL import Image
            with Image.open(path) as image:
                image.load()
                row.update(width=image.width, height=image.height, decoded=True)
        else:
            probe = subprocess.run([_ffmpeg_bin('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
                                    '-show_entries', 'stream=width,height:format=duration', '-of', 'json', str(path)],
                                   capture_output=True, text=True, timeout=30)
            data = json.loads(probe.stdout)
            row.update(data['streams'][0], duration=float(data['format']['duration']))
            decode = subprocess.run([_ffmpeg_bin('ffmpeg'), '-v', 'error', '-i', str(path), '-f', 'null', '-'],
                                    capture_output=True, timeout=45)
            row['decoded'] = decode.returncode == 0
            subprocess.run([_ffmpeg_bin('ffmpeg'), '-v', 'error', '-y', '-ss', '1', '-i', str(path),
                            '-frames:v', '1', str(output / f'scene-{index + 1}.jpg')], capture_output=True, timeout=30)
        artifacts.append(row)
    summary = {'timePKT': datetime.now().astimezone().isoformat(), 'lane': args.lane,
               'requested': len(scenes), 'retrieved': len(artifacts),
               'elapsedSeconds': round(time.monotonic() - started, 2),
               'statuses': dict(statuses), 'artifacts': artifacts, 'timings': timings,
               'transportCacheHits': audit.get('transport_cache_hits', 0),
               'cacheCondition': ('Shared warm search metadata; reused isolated clip/chapter cache' if args.cache_from
                                  else 'Shared warm search metadata; isolated cold clip/chapter cache' if args.isolated_cache
                                  else 'Existing caches') if args.lane == 'youtube' else 'Live web providers; fresh job-local download cache',
               'settings': {'webFirstWave': os.environ.get('VIDZEO_WEB_FAST_CANDIDATES_PER_QUERY'),
                            'webMaxSceneAttempts': os.environ.get('VIDZEO_WEB_FAST_DOWNLOADS_PER_SCENE'),
                            'youtubeMaxSceneAttempts': 6},
               'scope': 'Isolated live retrieval and objective decode; no production visual acceptance, render or upload'}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))
    return 0 if len(artifacts) == len(scenes) and all(a.get('decoded') for a in artifacts) else 1


if __name__ == '__main__':
    raise SystemExit(main())
