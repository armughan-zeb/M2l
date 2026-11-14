# =============================================================================
# EDGE TTS SERVICE - Free Microsoft Edge Text-to-Speech
# =============================================================================

import asyncio
import threading
import hashlib
import os
import random
import re
import subprocess
import sys
import time
import shutil
import tempfile
from pathlib import Path

try:
    import edge_tts
    _EDGE_TTS_AVAILABLE = True
    # edge-tts pins its websocket TLS to certifi's CA bundle, which does
    # not include a TLS-intercepting proxy's CA, so synthesis fails
    # certificate verification on a host behind such a proxy. Point the
    # library's module-global context at the system one instead: it
    # honours SSL_CERT_FILE and the OS trust store, and on Windows/macOS
    # it loads the native store, so this is safe there too.
    import ssl as _ssl

    edge_tts.communicate._SSL_CTX = _ssl.create_default_context()
except Exception:
    edge_tts = None
    _EDGE_TTS_AVAILABLE = False

# Add config path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import EDGE_VOICES, TEMP_DIR


DEFAULT_EDGE_VOICE = str(
    os.environ.get("VIDZEO_EDGE_VOICE_DEFAULT", "") or "en-US-BrianNeural"
).strip() or "en-US-BrianNeural"
DEFAULT_EDGE_OPTIONS = {
    'rate': 0,
    'pitch': 0,
    'volume': 0,
}
VOICE_CACHE_TTL_SECONDS = 60 * 60 * 12

_VOICE_CACHE = {
    'voices': None,
    'expires_at': 0.0,
}
_EDGE_TTS_MAX_PARALLEL_CHUNKS = 5
_EDGE_TTS_CHUNK_TIMEOUT_SECONDS = 75
_EDGE_TTS_PARALLEL_LADDER = (5, 3, 1)
_EDGE_TTS_CHUNK_TRIGGER_CHARS = max(250, int(os.environ.get("VIDZEO_EDGE_TTS_CHUNK_TRIGGER_CHARS", "450") or 450))
_EDGE_TTS_TARGET_CHUNK_CHARS = max(220, int(os.environ.get("VIDZEO_EDGE_TTS_TARGET_CHUNK_CHARS", "360") or 360))
_EDGE_TTS_MAX_RETRIES = max(3, int(os.environ.get("VIDZEO_EDGE_TTS_MAX_RETRIES", "6") or 6))
_EDGE_TTS_BACKOFF_BASE = float(os.environ.get("VIDZEO_EDGE_TTS_BACKOFF_BASE", "2.0") or 2.0)
_EDGE_TTS_FALLBACK_ENABLED = str(os.environ.get("VIDZEO_EDGE_TTS_FALLBACK", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}


def _edge_tts_proxy() -> str:
    """Proxy for the Edge TTS websocket, if the host needs one.

    Sandboxed / proxied Linux hosts have no direct internet: edge-tts
    connects straight to wss://speech.platform.bing.com unless a proxy is
    passed, and the connection just times out. Read the standard proxy
    env vars (explicit VIDZEO_EDGE_TTS_PROXY wins) so the same code also
    works on an unproxied machine (no vars -> direct, as before).
    """
    for name in ('VIDZEO_EDGE_TTS_PROXY', 'HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy'):
        value = str(os.environ.get(name, '') or '').strip()
        if value:
            return value
    return ''


def is_available() -> bool:
    return _EDGE_TTS_AVAILABLE


def _run_async(coro):
    """Run an async coroutine from sync context safely."""
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="edge-async") as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=300)
        else:
            return asyncio.run(coro)
    except Exception as exc:
        print(f"   Edge TTS async execution error: {exc}")
        raise exc



def _safe_int(value, default=0):
    try:
        return int(float(str(value).strip()))
    except Exception:
        return default


def _format_signed(value, suffix):
    value = _safe_int(value, 0)
    sign = '+' if value >= 0 else ''
    return f"{sign}{value}{suffix}"


def sanitize_edge_options(edge_options=None):
    edge_options = edge_options or {}
    return {
        'rate': _format_signed(edge_options.get('rate', DEFAULT_EDGE_OPTIONS['rate']), '%'),
        'pitch': _format_signed(edge_options.get('pitch', DEFAULT_EDGE_OPTIONS['pitch']), 'Hz'),
        'volume': _format_signed(edge_options.get('volume', DEFAULT_EDGE_OPTIONS['volume']), '%'),
    }


def get_default_edge_options():
    return dict(DEFAULT_EDGE_OPTIONS)


def _fallback_voice_catalog():
    catalog = []
    for label, voice_id in EDGE_VOICES.items():
        gender = 'Female' if 'female' in label.lower() else 'Male'
        locale = voice_id.rsplit('-', 1)[0] if '-' in voice_id else 'en-US'
        catalog.append({
            'id': voice_id,
            'label': label,
            'short_name': voice_id,
            'locale': locale,
            'gender': gender,
            'friendly_name': label,
            'content_categories': [],
            'voice_personalities': [],
        })
    return catalog


def _build_voice_label(raw_voice):
    short_name = raw_voice.get('ShortName') or raw_voice.get('Name') or DEFAULT_EDGE_VOICE
    locale = raw_voice.get('Locale') or short_name.rsplit('-', 1)[0]
    gender = raw_voice.get('Gender') or ('Female' if 'female' in short_name.lower() else 'Male')
    base_name = short_name.rsplit('-', 1)[-1].replace('Neural', '').strip() or short_name
    gender_label = 'Female' if gender.lower() == 'female' else 'Male'
    return f"{base_name} ({gender_label} {locale})"


def _normalize_voice_entry(raw_voice):
    voice_tag = raw_voice.get('VoiceTag') or {}
    short_name = raw_voice.get('ShortName') or raw_voice.get('Name') or DEFAULT_EDGE_VOICE
    raw_gender = str(raw_voice.get('Gender') or '').strip().lower()
    gender = 'Female' if raw_gender == 'female' or 'female' in short_name.lower() else 'Male'
    label = _build_voice_label(raw_voice)
    return {
        'id': short_name,
        'label': label,
        'short_name': short_name,
        'locale': raw_voice.get('Locale') or short_name.rsplit('-', 1)[0],
        'gender': gender,
        'friendly_name': raw_voice.get('FriendlyName') or label,
        'content_categories': voice_tag.get('ContentCategories') or [],
        'voice_personalities': voice_tag.get('VoicePersonalities') or [],
    }


def _refresh_voice_catalog():
    force_refresh = True
    now = time.time()
    if (
        not force_refresh
        and _VOICE_CACHE['voices']
        and _VOICE_CACHE['expires_at'] > now
    ):
        return list(_VOICE_CACHE['voices'])

    if not _EDGE_TTS_AVAILABLE:
        fallback_catalog = _fallback_voice_catalog()
        _VOICE_CACHE['voices'] = fallback_catalog
        _VOICE_CACHE['expires_at'] = now + 300
        return list(fallback_catalog)

    try:
        proxy = _edge_tts_proxy() or None
        raw_voices = _run_async(asyncio.wait_for(edge_tts.list_voices(proxy=proxy), timeout=15))
        # Keep strictly English voices (en-* locale) and remove other languages
        en_raw_voices = [
            raw_voice for raw_voice in (raw_voices or [])
            if (raw_voice.get('Locale') or raw_voice.get('ShortName') or '').lower().startswith('en-')
        ]
        catalog = [_normalize_voice_entry(raw_voice) for raw_voice in en_raw_voices]
        catalog.sort(key=lambda entry: (entry['gender'], entry['locale'], entry['label']))
        if catalog:
            _VOICE_CACHE['voices'] = catalog
            _VOICE_CACHE['expires_at'] = now + VOICE_CACHE_TTL_SECONDS
            return list(catalog)
    except Exception as exc:
        print(f"   Edge TTS voice catalog fetch failed, using fallback list: {exc}")

    fallback_catalog = _fallback_voice_catalog()
    _VOICE_CACHE['voices'] = fallback_catalog
    _VOICE_CACHE['expires_at'] = now + 300
    return list(fallback_catalog)


_VOICE_REFRESH_LOCK = threading.Lock()

def get_voice_catalog(force_refresh=False):
    if force_refresh:
        with _VOICE_REFRESH_LOCK:
            return _refresh_voice_catalog()
    voices = list(_VOICE_CACHE['voices'] or _fallback_voice_catalog())
    if _VOICE_CACHE['expires_at'] <= time.time() and _VOICE_REFRESH_LOCK.acquire(False):
        def refresh():
            try:
                _refresh_voice_catalog()
            finally:
                _VOICE_REFRESH_LOCK.release()
        try:
            threading.Thread(target=refresh, name='edge-voice-catalog', daemon=True).start()
        except Exception:
            _VOICE_REFRESH_LOCK.release()
    return voices


def get_voice_labels(force_refresh=False):
    return [voice['label'] for voice in get_voice_catalog(force_refresh=force_refresh)]


def _get_voice_id(voice_name: str) -> str:
    """Resolve a UI label or raw voice ID to an Edge voice ID."""
    if not voice_name:
        return DEFAULT_EDGE_VOICE

    voice_name = str(voice_name).strip()
    if not voice_name:
        return DEFAULT_EDGE_VOICE

    if voice_name in EDGE_VOICES.values():
        return voice_name

    if voice_name in EDGE_VOICES:
        return EDGE_VOICES[voice_name]

    if voice_name.endswith('Neural') and '-' in voice_name:
        return voice_name

    lowered = voice_name.lower()
    # Prefer the bundled/static voice mapping first so generation never blocks on
    # a live catalog fetch just to resolve a label.
    for voice in _fallback_voice_catalog():
        if voice['id'].lower() == lowered or voice['label'].lower() == lowered:
            return voice['id']

    if _VOICE_CACHE['voices']:
        for voice in _VOICE_CACHE['voices']:
            if voice['id'].lower() == lowered or voice['label'].lower() == lowered:
                return voice['id']

    for voice in get_voice_catalog():
        if voice['id'].lower() == lowered or voice['label'].lower() == lowered:
            return voice['id']

    return DEFAULT_EDGE_VOICE


def _get_audio_duration(path: str) -> float:
    try:
        result = subprocess.run(
            [
                'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1', path
            ],
            capture_output=True,
            text=True,
            timeout=10
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


async def _generate_edge_tts(text: str, voice_id: str, output_path: str, edge_options=None):
    speak_options = sanitize_edge_options(edge_options)
    communicate = edge_tts.Communicate(
        text,
        voice_id,
        rate=speak_options['rate'],
        volume=speak_options['volume'],
        pitch=speak_options['pitch'],
        boundary='WordBoundary',
        proxy=_edge_tts_proxy() or None,
    )
    words = []
    with open(output_path, 'wb') as audio:
        async for event in communicate.stream():
            if event['type'] == 'audio':
                audio.write(event['data'])
            elif event['type'] == 'WordBoundary':
                start = float(event['offset']) / 10_000_000
                end = start + float(event['duration']) / 10_000_000
                if end > start:
                    words.append({'word': event['text'], 'start': start, 'end': end})
    if words:
        from .speech_timing import save_speech_timing
        save_speech_timing(output_path, words)


async def _run_single_chunk(index: int, chunk_text: str, voice_id: str, speak_options: dict, total: int) -> tuple:
    """Run a single TTS chunk with its own timeout. Returns (index, path, error)."""
    hash_seed = f"{chunk_text}{voice_id}{speak_options}{time.time()}{index}"
    hash_str = hashlib.sha256(hash_seed.encode()).hexdigest()[:12]
    chunk_path = str(TEMP_DIR / f"edge_chunk_{hash_str}.mp3")
    try:
        await asyncio.wait_for(
            _generate_edge_tts(chunk_text, voice_id, chunk_path, speak_options),
            timeout=_EDGE_TTS_CHUNK_TIMEOUT_SECONDS,
        )
        if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 0:
            print(f"   Chunk {index + 1}/{total} done")
            return index, chunk_path, None
        return index, None, "empty chunk output"
    except asyncio.TimeoutError:
        return index, None, f"chunk {index + 1} timed out after {_EDGE_TTS_CHUNK_TIMEOUT_SECONDS}s"
    except Exception as exc:
        return index, None, str(exc)


def _run_chunk_in_thread(index: int, chunk_text: str, voice_id: str, speak_options: dict, total: int) -> tuple:
    """Execute a single chunk in its own isolated event loop (thread-safe)."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                _run_single_chunk(index, chunk_text, voice_id, speak_options, total)
            )
        finally:
            loop.close()
            asyncio.set_event_loop(None)
    except Exception as exc:
        return index, None, str(exc)


async def _generate_edge_tts_chunk_batch(chunks, voice_id: str, speak_options: dict, max_workers: int):
    """Run chunks in parallel using a thread pool (one event loop per thread = no asyncio contention)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed as futures_as_completed
    from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
    total = len(chunks)
    worker_count = max(1, min(max_workers, _EDGE_TTS_MAX_PARALLEL_CHUNKS, total))
    results = [None] * total

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="edge-tts-chunk") as pool:
        # Submit all chunks
        future_map = {
            loop.run_in_executor(pool, _run_chunk_in_thread, i, chunk_text, voice_id, speak_options, total): i
            for i, chunk_text in enumerate(chunks)
        }
        # Gather with outer timeout: total = chunks * per-chunk-timeout + 30s slack
        outer_timeout = _EDGE_TTS_CHUNK_TIMEOUT_SECONDS * total + 30
        try:
            done_results = await asyncio.wait_for(
                asyncio.gather(*future_map.keys(), return_exceptions=True),
                timeout=outer_timeout,
            )
            for result in done_results:
                if isinstance(result, Exception):
                    # find orphan slot — assign to first None
                    for i in range(total):
                        if results[i] is None:
                            results[i] = (i, None, str(result))
                            break
                else:
                    idx, path, err = result
                    results[idx] = (idx, path, err)
        except asyncio.TimeoutError:
            print(f"   [edge-tts] outer batch timeout ({outer_timeout}s) — marking remaining chunks as failed")
            for i in range(total):
                if results[i] is None:
                    results[i] = (i, None, f"outer batch timeout after {outer_timeout}s")

    return [r for r in results if r is not None]


def _split_script_for_parallel_tts(script: str) -> list[str]:
    sentences = re.split(r'(?<=[.!?])\s+', str(script or '').strip())
    sentences = [sentence.strip() for sentence in sentences if sentence.strip()]
    if not sentences:
        return []

    target_chunk_count = max(
        1,
        min(
            _EDGE_TTS_MAX_PARALLEL_CHUNKS,
            round(len(script) / max(1, _EDGE_TTS_TARGET_CHUNK_CHARS)),
            len(sentences),
        ),
    )
    approx_sentences_per_chunk = max(1, round(len(sentences) / target_chunk_count))

    chunks = []
    current = []
    current_chars = 0
    for sentence in sentences:
        sentence_len = len(sentence)
        if current and (
            len(current) >= approx_sentences_per_chunk
            or current_chars + sentence_len > _EDGE_TTS_TARGET_CHUNK_CHARS
        ):
            chunks.append(' '.join(current).strip())
            current = []
            current_chars = 0
        current.append(sentence)
        current_chars += sentence_len + 1

    if current:
        chunks.append(' '.join(current).strip())

    return [chunk for chunk in chunks if chunk]


def _concat_edge_chunks(chunk_paths: list[str], batch_start: float) -> tuple[str, float]:
    if len(chunk_paths) == 1:
        duration = _get_audio_duration(chunk_paths[0])
        return chunk_paths[0], duration

    hash_str = hashlib.sha256(str(time.time()).encode()).hexdigest()[:12]
    output_path = str(TEMP_DIR / f"edge_tts_full_{hash_str}.wav")
    concat_file = str(TEMP_DIR / f"edge_concat_{hash_str}.txt")

    try:
        import wave
        from .speech_timing import load_speech_timing, save_speech_timing, timing_path
        decoded_paths, merged_words = [], []
        cursor = 0.0
        timing_complete = True
        for chunk_path in chunk_paths:
            decoded = str(Path(chunk_path).with_suffix('.decoded.wav'))
            subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                            '-i', chunk_path, '-ar', '24000', '-ac', '1',
                            '-c:a', 'pcm_s16le', decoded], capture_output=True, timeout=60, check=True)
            with wave.open(decoded, 'rb') as wav:
                chunk_duration = wav.getnframes() / wav.getframerate()
            rows = load_speech_timing(chunk_path, _get_audio_duration(chunk_path))
            timing_complete = timing_complete and bool(rows)
            merged_words.extend({**row, 'start': row['start'] + cursor,
                                 'end': min(row['end'], chunk_duration) + cursor} for row in rows)
            cursor += chunk_duration
            decoded_paths.append(decoded)
        with open(concat_file, 'w', encoding='utf-8') as handle:
            for chunk_path in decoded_paths:
                abs_path = os.path.abspath(chunk_path).replace('\\', '/')
                handle.write(f"file '{abs_path}'\n")

        subprocess.run(
            [
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                '-f', 'concat', '-safe', '0', '-i', concat_file,
                '-c:a', 'pcm_s16le', output_path
            ],
            capture_output=True,
            timeout=max(60, int(cursor / 5) + 30),
            check=True
        )

        if timing_complete and merged_words:
            save_speech_timing(output_path, merged_words)

        try:
            os.remove(concat_file)
            for chunk_path in chunk_paths + decoded_paths:
                os.remove(chunk_path)
                timing_path(chunk_path).unlink(missing_ok=True)
        except Exception:
            pass

        duration = _get_audio_duration(output_path)
        elapsed = time.time() - batch_start
        print(f"   Edge TTS chunk batch complete: {duration:.1f}s audio in {elapsed:.1f}s")
        return output_path, duration
    except Exception as exc:
        raise RuntimeError(f"Edge TTS chunk concatenation failed: {exc}") from exc


def _generate_linux_tts_audio(text: str, output_path: str) -> tuple | None:
    """Linux offline fallback: shell out to a configured TTS command.

    Edge TTS needs a websocket, which sandboxed/proxied Linux hosts often
    block, and the SAPI fallback is Windows-only. When the operator sets
    VIDZEO_LINUX_TTS_COMMAND, run it instead. The command is a template
    with {text_file}, {output}, {voice} and {speed} placeholders; the
    script text is also piped on stdin. Example for this VM's Shortwave
    CLI (set in vidnets-runtime/.env):
        VIDZEO_LINUX_TTS_COMMAND=/opt/hatch/bin/tts speak --text-stdin --voice {voice} --speed {speed} --format mp3 --output {output}
        VIDZEO_LINUX_TTS_VOICE=avocado_v2:MAI_03
        VIDZEO_LINUX_TTS_SPEED=100
    Returns None when no command is configured, so callers keep the
    previous behaviour (SAPI on Windows, clear platform error elsewhere).
    """
    import shlex

    template = str(os.environ.get('VIDZEO_LINUX_TTS_COMMAND', '') or '').strip()
    if not template:
        return None
    voice = str(os.environ.get('VIDZEO_LINUX_TTS_VOICE', '') or '').strip()
    speed = str(os.environ.get('VIDZEO_LINUX_TTS_SPEED', '100') or '100').strip()
    text_file = Path(str(output_path) + '.txt')
    destination = Path(output_path)
    attempt_output = destination.with_name(destination.stem + '.attempt' + destination.suffix)
    try:
        text_file.write_text(text, encoding='utf-8')
        argv = [
            part.format(text_file=str(text_file), output=str(attempt_output), voice=voice, speed=speed)
            for part in shlex.split(template)
        ]
        timeout = float(os.environ.get('VIDZEO_LINUX_TTS_TIMEOUT_SECONDS', '300') or 300)
        for attempt in range(3):
            attempt_output.unlink(missing_ok=True)
            try:
                result = subprocess.run(
                    argv, input=text.encode('utf-8'),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                )
                break
            except subprocess.TimeoutExpired:
                if attempt == 2:
                    raise
                print(f"   [TTS] Linux command timed out; retrying attempt {attempt + 2}/3", flush=True)
        if result.returncode != 0:
            raise RuntimeError(
                f'Linux TTS command failed ({result.returncode}): '
                f'{result.stderr.decode("utf-8", "replace").strip()[-400:]}'
            )
        if not attempt_output.is_file() or attempt_output.stat().st_size < 128:
            raise RuntimeError('Linux TTS command produced no audio file.')
        duration = _get_audio_duration(str(attempt_output))
        if duration <= 0:
            raise RuntimeError('Linux TTS command produced unreadable audio.')
        os.replace(attempt_output, destination)
        print(f"   [TTS] Generated Linux offline spoken narration ({duration:.1f}s)")
        return str(output_path), duration
    finally:
        try:
            text_file.unlink(missing_ok=True)
            attempt_output.unlink(missing_ok=True)
        except Exception:
            pass


def _generate_sapi_audio(text: str, output_path: str) -> tuple:
    """Recover real speech without requiring pywin32 or an online service."""
    if sys.platform != 'win32':
        linux = _generate_linux_tts_audio(text, output_path)
        if linux is not None:
            return linux
    from .local_speech_service import generate_audio as generate_local_speech
    path, duration = generate_local_speech(text, output_path)
    print(f"   [TTS] Generated offline spoken narration ({duration:.1f}s)")
    return path, duration


def _generate_linux_tts_chunked(script: str) -> tuple:
    """Keep completed speech chunks across retries and assemble them in order."""
    chunks = _split_script_for_parallel_tts(script)
    if not chunks:
        raise ValueError("Cannot synthesize empty narration.")
    cache_dir = Path(TEMP_DIR) / 'linux-tts-chunks'
    cache_dir.mkdir(parents=True, exist_ok=True)
    identity = '\0'.join([
        os.environ.get('VIDZEO_LINUX_TTS_COMMAND', ''),
        os.environ.get('VIDZEO_LINUX_TTS_VOICE', ''),
        os.environ.get('VIDZEO_LINUX_TTS_SPEED', '100'),
    ])
    work_dir = Path(tempfile.mkdtemp(prefix='linux-tts-', dir=str(TEMP_DIR)))
    assembled = []
    try:
        for index, text in enumerate(chunks):
            digest = hashlib.sha256((identity + '\0' + text).encode('utf-8')).hexdigest()
            cached = cache_dir / (digest + '.mp3')
            try:
                valid = cached.is_file() and cached.stat().st_size >= 128 and _get_audio_duration(str(cached)) > 0
            except Exception:
                valid = False
            print(f"   [TTS] Narration chunk {index + 1}/{len(chunks)}" +
                  (" (cached)" if valid else ""), flush=True)
            if not valid:
                # Generate in this invocation's private directory, then atomically
                # publish the verified result without exposing partial cache files.
                generated = work_dir / f'generated-{index}.mp3'
                _generate_linux_tts_audio(text, str(generated))
                os.replace(generated, cached)
            local = work_dir / f'chunk-{index}.mp3'
            shutil.copyfile(cached, local)
            assembled.append(str(local))
        if len(assembled) == 1:
            final = Path(TEMP_DIR) / ('linux-tts-full-' + hashlib.sha256(os.urandom(32)).hexdigest()[:16] + '.mp3')
            os.replace(assembled[0], final)
            return str(final), _get_audio_duration(str(final))
        return _concat_edge_chunks(assembled, time.time())
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _permanent_provider_error(error):
    status = getattr(error, 'status', None)
    return status in {401, 403} or bool(re.search(r'\b(?:401|403)\b', str(error)))


def generate_audio(script: str, voice: str = 'Brian (Male US)',
                   speed: float = 1.0, api_key: str = None, edge_options=None,
                   request_timeout_seconds=None, max_attempts=None, allow_offline_fallback=True):
    """
    Generate audio from text using Edge TTS with exponential backoff retries.
    Recovers with offline spoken narration if the online provider fails.
    """
    if not _EDGE_TTS_AVAILABLE:
        if _EDGE_TTS_FALLBACK_ENABLED and allow_offline_fallback:
            duration = max(1.0, len(script) / 15.0)
            hash_seed = f"fallback_{script}{time.time()}"
            hash_str = hashlib.sha256(hash_seed.encode()).hexdigest()[:12]
            output_path = str(TEMP_DIR / f"edge_tts_fallback_{hash_str}.mp3")
            print("   Edge TTS unavailable; generating offline spoken narration")
            return _generate_sapi_audio(script, output_path)
        raise ValueError("Edge TTS package is not installed. Run `pip install -r requirements.txt`.")
    del speed, api_key
    voice_id = _get_voice_id(voice)
    speak_options = sanitize_edge_options(edge_options)

    hash_seed = f"{script}{voice_id}{speak_options}{time.time()}"
    hash_str = hashlib.sha256(hash_seed.encode()).hexdigest()[:12]
    output_path = str(TEMP_DIR / f"edge_tts_{hash_str}.mp3")

    # On a Linux host where the operator configured an offline TTS command
    # (e.g. Edge TTS websockets are blocked by the egress proxy), prefer
    # it over Edge TTS instead of burning minutes on websocket timeouts
    # first. If the command fails, fall through to Edge TTS as usual.
    if sys.platform != 'win32':
        try:
            linux = _generate_linux_tts_audio(script, output_path)
        except Exception as exc:
            print(f"   Linux TTS command failed, trying Edge TTS: {exc}")
            linux = None
        if linux is not None:
            linux_path, linux_duration = linux
            from .narration_quality import validate_narration_audio
            validate_narration_audio(linux_path)
            return linux_path, linux_duration

    print(
        f"   Edge TTS: voice={voice_id}, rate={speak_options['rate']}, "
        f"pitch={speak_options['pitch']}, volume={speak_options['volume']}"
    )

    attempt_limit = max(1, int(max_attempts or min(_EDGE_TTS_MAX_RETRIES, 3)))
    total_budget = max(1.0, float(request_timeout_seconds or 90.0))
    deadline = time.monotonic() + total_budget
    last_error = None
    for attempt in range(attempt_limit):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        start = time.time()
        try:
            _run_async(asyncio.wait_for(
                _generate_edge_tts(script, voice_id, output_path, speak_options),
                timeout=min(_EDGE_TTS_CHUNK_TIMEOUT_SECONDS, remaining),
            ))

            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                from .narration_quality import validate_narration_audio
                validate_narration_audio(output_path)
                duration = _get_audio_duration(output_path)
                elapsed = time.time() - start
                print(f"   Edge TTS complete: {duration:.1f}s audio in {elapsed:.1f}s (Attempt {attempt + 1})")
                return output_path, duration
        except Exception as exc:
            last_error = exc
            print(f"   Edge TTS attempt {attempt + 1}/{_EDGE_TTS_MAX_RETRIES} failed: {exc}")
            if _permanent_provider_error(exc):
                break
            if attempt < attempt_limit - 1:
                backoff = _EDGE_TTS_BACKOFF_BASE ** (attempt + 1)
                jitter = random.uniform(0.5, 1.5)
                sleep_time = backoff * jitter
                print(f"   Retrying in {sleep_time:.1f}s...")
                time.sleep(min(sleep_time, 4.0, max(0.0, deadline-time.monotonic())))

    # Speech provider recovery must produce audible narration.
    if _EDGE_TTS_FALLBACK_ENABLED and allow_offline_fallback:
        fallback_path = output_path.replace(".mp3", "_fallback.mp3")
        print(f"   Edge TTS failed after {_EDGE_TTS_MAX_RETRIES} attempts: {last_error}")
        return _generate_sapi_audio(script, fallback_path)

    raise ValueError(f"Edge TTS generation failed after {_EDGE_TTS_MAX_RETRIES} attempts: {last_error}")


def generate_audio_chunked(script: str, voice: str = 'Brian (Male US)',
                           speed: float = 1.0, api_key: str = None,
                           max_workers: int = 2, edge_options=None):
    """
    Generate audio in chunks for long scripts, then concatenate.
    Retries with exponential backoff between ladder levels.
    Falls back to single-chunk generate_audio if all chunked attempts fail.
    """
    if sys.platform != 'win32' and str(os.environ.get('VIDZEO_LINUX_TTS_COMMAND', '') or '').strip():
        return _generate_linux_tts_chunked(script)

    if not _EDGE_TTS_AVAILABLE:
        if _EDGE_TTS_FALLBACK_ENABLED:
            duration = max(1.0, len(script) / 15.0)
            hash_seed = f"fallback_chunked_{script}{time.time()}"
            hash_str = hashlib.sha256(hash_seed.encode()).hexdigest()[:12]
            output_path = str(TEMP_DIR / f"edge_tts_fallback_{hash_str}.mp3")
            print("   Edge TTS unavailable; generating offline spoken narration")
            return _generate_sapi_audio(script, output_path)
        raise ValueError("Edge TTS package is not installed. Run `pip install -r requirements.txt`.")
    del speed, api_key

    if len(script) < _EDGE_TTS_CHUNK_TRIGGER_CHARS:
        return generate_audio(script, voice, edge_options=edge_options)

    chunks = _split_script_for_parallel_tts(script)
    if len(chunks) <= 1:
        return generate_audio(script, voice, edge_options=edge_options)

    voice_id = _get_voice_id(voice)
    speak_options = sanitize_edge_options(edge_options)
    batch_start = time.time()
    requested_workers = max(1, int(max_workers or 1))
    remaining = list(enumerate(chunks))
    resolved_paths = {}
    last_errors = {}
    lane_attempt = 0

    for lane in _EDGE_TTS_PARALLEL_LADDER:
        if not remaining:
            break

        worker_count = max(1, min(lane, _EDGE_TTS_MAX_PARALLEL_CHUNKS, requested_workers, len(remaining)))
        pending_chunks = [chunk_text for _, chunk_text in remaining]
        pending_indices = [chunk_index for chunk_index, _ in remaining]
        print(f"   Edge TTS: Processing {len(remaining)} chunk(s) with {worker_count} workers...")

        batch_results = _run_async(
            _generate_edge_tts_chunk_batch(pending_chunks, voice_id, speak_options, worker_count)
        )

        next_remaining = []
        for local_index, chunk_path, error in sorted(batch_results, key=lambda row: row[0]):
            chunk_index = pending_indices[local_index]
            if chunk_path:
                resolved_paths[chunk_index] = chunk_path
                continue
            last_errors[chunk_index] = error or "unknown chunk failure"
            next_remaining.append((chunk_index, chunks[chunk_index]))
            print(f"   Chunk {chunk_index + 1} failed: {last_errors[chunk_index]}")

        remaining = next_remaining
        if remaining and _EDGE_TTS_FALLBACK_ENABLED and all(_permanent_provider_error(last_errors.get(index, '')) for index, _ in remaining):
            print("   Edge TTS authorization rejected; recovering narration offline")
            return _generate_sapi_audio(script, str(TEMP_DIR / f'edge_recovered_{hashlib.sha256((script + str(time.time())).encode()).hexdigest()[:12]}.mp3'))
        if remaining and worker_count > 1:
            next_lane = next((candidate for candidate in _EDGE_TTS_PARALLEL_LADDER if candidate < worker_count), 1)
            lane_attempt += 1
            backoff = _EDGE_TTS_BACKOFF_BASE ** lane_attempt
            jitter = random.uniform(0.5, 1.5)
            sleep_time = backoff * jitter
            print(f"   Edge TTS: retrying {len(remaining)} chunk(s) at {next_lane} worker(s) "
                  f"after {sleep_time:.1f}s backoff...")
            time.sleep(sleep_time)

    if remaining:
        failed_indices = ', '.join(str(index + 1) for index, _ in remaining)
        print(f"   Edge TTS chunk generation failed for chunk(s): {failed_indices}")
        # Fallback: try single generate_audio for the full script
        if _EDGE_TTS_FALLBACK_ENABLED:
            print(f"   Falling back to single-chunk Edge TTS for full script...")
            try:
                return generate_audio(script, voice, edge_options=edge_options)
            except Exception as fallback_exc:
                print(f"   Single-chunk fallback also failed: {fallback_exc}")
                hash_seed = f"fallback_chunked_{script}{time.time()}"
                hash_str = hashlib.sha256(hash_seed.encode()).hexdigest()[:12]
                output_path = str(TEMP_DIR / f"edge_tts_fallback_{hash_str}.mp3")
                return _generate_sapi_audio(script, output_path)
        raise ValueError(f"Edge TTS chunk generation failed for chunk(s): {failed_indices}")

    ordered_paths = [resolved_paths[index] for index in sorted(resolved_paths)]
    if not ordered_paths:
        if _EDGE_TTS_FALLBACK_ENABLED:
            duration = max(1.0, len(script) / 15.0)
            hash_seed = f"fallback_empty_{script}{time.time()}"
            hash_str = hashlib.sha256(hash_seed.encode()).hexdigest()[:12]
            output_path = str(TEMP_DIR / f"edge_tts_fallback_{hash_str}.mp3")
            print("   All Edge TTS chunks failed; generating offline spoken narration")
            return _generate_sapi_audio(script, output_path)
        raise ValueError("All Edge TTS chunks failed")

    try:
        return _concat_edge_chunks(ordered_paths, batch_start)
    except RuntimeError:
        # Never deliver only the first paragraph as a complete narration.
        if _EDGE_TTS_FALLBACK_ENABLED:
            return generate_audio(script, voice, edge_options=edge_options)
        raise


def preview_voice(voice: str, text: str = "Hello! This is a preview of my voice.",
                  api_key: str = None, edge_options=None):
    del api_key
    path, _ = generate_audio(text, voice, edge_options=edge_options,
        request_timeout_seconds=24, max_attempts=2, allow_offline_fallback=False)
    return path
