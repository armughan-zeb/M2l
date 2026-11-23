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

# Narration no-progress watchdog (2026-11-21, jobs 64fc9ddb + 26fa018b):
# both narration lanes have per-chunk timeouts, but neither had a
# *no-progress* bound: a provider call that never completes a chunk (an
# Edge websocket against the blocked egress proxy, a wedged Muse CLI
# subprocess) left the worker thread blocked with no progress callback,
# so the queue heartbeat's delivery-deadline stop killed the heartbeat
# while the worker stayed deaf inside the call -- frozen progress AND
# frozen heartbeat, i.e. exactly the "worker+heartbeat dead" stall the
# job-debugger had to restart the server for. If no chunk completes
# within this many seconds, the narration stage fails loudly
# (TimeoutError) instead of hanging silently; the queue worker marks
# the job and the driver/debugger can resume or restart it.
# Default 300s: a healthy Muse chunk finishes in ~25s and a whole dead
# Edge lane returns in ~75s (per-chunk timeout), so 300s of zero
# completions means the provider is wedged, not slow.
_NARRATION_CHUNK_STALL_SECONDS_DEFAULT = 300.0


def _narration_chunk_stall_seconds() -> float:
    """Seconds with zero completed narration chunks before failing loudly."""
    try:
        value = float(os.environ.get("VIDZEO_NARRATION_CHUNK_STALL_SECONDS", "") or _NARRATION_CHUNK_STALL_SECONDS_DEFAULT)
    except (TypeError, ValueError):
        value = _NARRATION_CHUNK_STALL_SECONDS_DEFAULT
    return max(60.0, min(3600.0, value))

# Muse TTS is the PRIMARY narration voice on Linux: this VM's Shortwave
# CLI (/opt/hatch/bin/tts). Edge TTS websockets are blocked by the egress
# proxy on this host, so Edge is only the fallback. The operator can
# override the command/voice/speed with VIDZEO_LINUX_TTS_*; otherwise the
# bundled Muse CLI below is the default whenever it exists.
_MUSE_TTS_BINARY = "/opt/hatch/bin/tts"
_MUSE_TTS_COMMAND_TEMPLATE = (
    "/opt/hatch/bin/tts speak --text-stdin --voice {voice} "
    "--speed {speed} --format mp3 --output {output}"
)
_MUSE_TTS_DEFAULT_VOICE = "avocado_v2:MAI_03"
_MUSE_TTS_DEFAULT_SPEED = "100"


def linux_tts_config():
    """Resolve the primary (Muse) TTS command for this host.

    Returns (command_template, voice, speed), or None when no Linux TTS
    is available (Windows, or no command configured and no Muse CLI).
    VIDZEO_LINUX_TTS_COMMAND wins when set; otherwise the bundled Muse
    CLI is the default on Linux. The Muse default voice applies only to
    the Muse command; a custom command keeps the old empty-voice
    behaviour unless VIDZEO_LINUX_TTS_VOICE is set.
    """
    if sys.platform == 'win32':
        return None
    command = str(os.environ.get('VIDZEO_LINUX_TTS_COMMAND', '') or '').strip()
    if not command:
        if os.path.isfile(_MUSE_TTS_BINARY) and os.access(_MUSE_TTS_BINARY, os.X_OK):
            command = _MUSE_TTS_COMMAND_TEMPLATE
        else:
            return None
    voice = str(os.environ.get('VIDZEO_LINUX_TTS_VOICE', '') or '').strip()
    if not voice and command == _MUSE_TTS_COMMAND_TEMPLATE:
        voice = _MUSE_TTS_DEFAULT_VOICE
    speed = str(os.environ.get('VIDZEO_LINUX_TTS_SPEED', '') or '').strip() or _MUSE_TTS_DEFAULT_SPEED
    return command, voice, speed


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


def _split_script_for_parallel_tts(script: str, *, target_chunk_chars: int | None = None,
                                   max_chunks: int | None = None) -> list[str]:
    """Split a script into chunks at sentence boundaries.

    Default (Edge TTS): short ~360-char chunks, at most 5, matching the
    Edge websocket lane's per-chunk timeout and parallel ladder.

    Linux (Muse) TTS: pass ``target_chunk_chars`` for balanced chunks of
    roughly that size, at most ``max_chunks``. The Muse CLI is a network
    client whose per-call cost is dominated by session setup plus
    synthesis at ~0.18s per audio-second, so a few large chunks beat many
    small ones: 56 x 360-char calls pay the setup cost 56 times and take
    ~280s for a 2,711-word script, while 8 x ~2,000-char calls at 4
    workers take ~80s. The balanced split below sizes chunks from the
    total length (no per-chunk char cap fighting the chunk-count target,
    which is what produced the 56-chunk blowup).
    """
    sentences = re.split(r'(?<=[.!?])\s+', str(script or '').strip())
    sentences = [sentence.strip() for sentence in sentences if sentence.strip()]
    if not sentences:
        return []

    if target_chunk_chars is not None:
        total_chars = len(script)
        count = max(1, min(
            max(1, int(max_chunks or 1)),
            -(-total_chars // max(1, int(target_chunk_chars))),
            len(sentences),
        ))
        target = max(1.0, total_chars / count)
        chunks = []
        current = []
        current_chars = 0
        for sentence in sentences:
            if current and current_chars >= target and len(chunks) < count - 1:
                chunks.append(' '.join(current).strip())
                current = []
                current_chars = 0
            current.append(sentence)
            current_chars += len(sentence) + 1
        if current:
            chunks.append(' '.join(current).strip())
        return [chunk for chunk in chunks if chunk]

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

        def _decode_chunk(chunk_path):
            """Decode one chunk to wav and read its duration + timings.

            One sequential retry: a transient ffmpeg failure under load
            must not discard a whole narration — decode is idempotent,
            so retrying is cheap. (rc=228 is ffmpeg's exit code for
            ENOSPC: it was seen twice when /tmp, a 512 MB tmpfs, filled
            with pytest basetemps; the live TEMP_DIR is on disk, and the
            final concat now preflights disk space, but the retry still
            covers genuine transient decode failures.)
            """
            decoded = str(Path(chunk_path).with_suffix('.decoded.wav'))
            last_exc = None
            for attempt in range(2):
                try:
                    subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                                    '-i', chunk_path, '-ar', '24000', '-ac', '1',
                                    '-c:a', 'pcm_s16le', decoded], capture_output=True, timeout=60, check=True)
                    with wave.open(decoded, 'rb') as wav:
                        chunk_duration = wav.getnframes() / wav.getframerate()
                    rows = load_speech_timing(chunk_path, _get_audio_duration(chunk_path))
                    return decoded, chunk_duration, rows
                except subprocess.CalledProcessError as exc:
                    detail = (exc.stderr or b'').decode('utf-8', 'replace').strip()[-300:]
                    last_exc = RuntimeError(
                        f"ffmpeg decode of {Path(chunk_path).name} failed "
                        f"(rc={exc.returncode}): {detail}")
                except Exception as exc:
                    last_exc = exc
                if attempt == 0:
                    print(f"   [tts-concat] decode of {Path(chunk_path).name} failed; "
                          f"retrying once", flush=True)
                    time.sleep(1.0)
            raise last_exc

        # Decode in parallel (ffmpeg + ffprobe per chunk are independent);
        # pool.map preserves chunk order, so the merge below still walks
        # the chunks in narration order and the timing cursor stays exact.
        from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(chunk_paths)),
                                thread_name_prefix="tts-concat-decode") as pool:
            decoded_results = list(pool.map(_decode_chunk, chunk_paths))
        decoded_paths, merged_words = [], []
        cursor = 0.0
        timing_complete = True
        for decoded, chunk_duration, rows in decoded_results:
            decoded_paths.append(decoded)
            timing_complete = timing_complete and bool(rows)
            merged_words.extend({**row, 'start': row['start'] + cursor,
                                 'end': min(row['end'], chunk_duration) + cursor} for row in rows)
            cursor += chunk_duration
        with open(concat_file, 'w', encoding='utf-8') as handle:
            for chunk_path in decoded_paths:
                abs_path = os.path.abspath(chunk_path).replace('\\', '/')
                handle.write(f"file '{abs_path}'\n")

        # Preflight the output write: the concatenated wav is ~48 kB per
        # audio-second (24 kHz mono 16-bit). A full TEMP_DIR makes ffmpeg
        # fail with the cryptic rc=228 ("No space left on device"); fail
        # here with a clear message instead of after a wasted concat.
        # (Seen in the harness when /tmp, a 512 MB tmpfs, filled with
        # pytest basetemps; the live TEMP_DIR is on the 150 GB disk.)
        needed_bytes = int(cursor * 48000) + 10 * 1024 * 1024
        try:
            free_bytes = shutil.disk_usage(str(TEMP_DIR)).free
        except Exception:
            free_bytes = needed_bytes
        if free_bytes < needed_bytes:
            raise RuntimeError(
                f"not enough disk space to concatenate narration in {TEMP_DIR}: "
                f"need ~{needed_bytes // (1024 * 1024)} MB for {cursor:.0f}s audio, "
                f"have {free_bytes // (1024 * 1024)} MB free")

        concat_argv = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-f', 'concat', '-safe', '0', '-i', concat_file,
            '-c:a', 'pcm_s16le', output_path
        ]
        concat_timeout = max(60, int(cursor / 5) + 30)
        for concat_attempt in range(2):
            try:
                subprocess.run(
                    concat_argv,
                    capture_output=True,
                    timeout=concat_timeout,
                    check=True,
                )
                break
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or b'').decode('utf-8', 'replace').strip()[-300:]
                if concat_attempt == 0:
                    print(f"   [tts-concat] final concat failed (rc={exc.returncode}): "
                          f"{detail}; retrying once", flush=True)
                    time.sleep(2.0)
                    continue
                raise RuntimeError(
                    f"final ffmpeg concat failed (rc={exc.returncode}): {detail}"
                ) from exc

        if timing_complete and merged_words:
            # S8: keep the script-timing label when every chunk's timings
            # were script-derived (the Linux TTS path); real provider
            # boundaries keep the default label. The caption step reports
            # the label, so a script-derived clock is never mistaken for
            # measured word boundaries.
            merged_source = (
                'tts_script_timing'
                if all(row.get('timing_source') == 'tts_script_timing' for row in merged_words)
                else 'tts_word_boundary'
            )
            save_speech_timing(output_path, merged_words, source=merged_source)

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


def _generate_linux_tts_audio(text: str, output_path: str, timeout_seconds: float | None = None,
                              deadline: float | None = None) -> tuple | None:
    """Primary narration on Linux: shell out to the Muse TTS command.

    Edge TTS needs a websocket, which sandboxed/proxied Linux hosts often
    block, and the SAPI fallback is Windows-only. The command is resolved
    by linux_tts_config(): VIDZEO_LINUX_TTS_COMMAND wins, otherwise the
    bundled Muse CLI (/opt/hatch/bin/tts) is the default on Linux. The
    command is a template with {text_file}, {output}, {voice} and {speed}
    placeholders; the script text is also piped on stdin. Example (also
    what vidnets-runtime/.env sets):
        VIDZEO_LINUX_TTS_COMMAND=/opt/hatch/bin/tts speak --text-stdin --voice {voice} --speed {speed} --format mp3 --output {output}
        VIDZEO_LINUX_TTS_VOICE=avocado_v2:MAI_03
        VIDZEO_LINUX_TTS_SPEED=100
    Each attempt gets 60s by default (VIDZEO_LINUX_TTS_TIMEOUT_SECONDS
    overrides; was 300s — a single hung chunk then killed the whole render
    at video 60% on 2026-11-14). A caller may pass timeout_seconds for one
    call and a deadline (epoch seconds) that bounds all 3 attempts together.
    Returns None when no command is configured, so callers keep the
    previous behaviour (Edge TTS, then SAPI on Windows / offline speech).
    """
    import shlex

    resolved = linux_tts_config()
    if resolved is None:
        return None
    template, voice, speed = resolved
    text_file = Path(str(output_path) + '.txt')
    destination = Path(output_path)
    attempt_output = destination.with_name(destination.stem + '.attempt' + destination.suffix)
    try:
        text_file.write_text(text, encoding='utf-8')
        argv = [
            part.format(text_file=str(text_file), output=str(attempt_output), voice=voice, speed=speed)
            for part in shlex.split(template)
        ]
        timeout = float(timeout_seconds if timeout_seconds is not None
                        else os.environ.get('VIDZEO_LINUX_TTS_TIMEOUT_SECONDS', '60') or 60)
        for attempt in range(3):
            attempt_output.unlink(missing_ok=True)
            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(f'Linux TTS chunk budget exhausted before attempt {attempt + 1}/3')
                attempt_timeout = min(timeout, remaining)
            else:
                attempt_timeout = timeout
            try:
                result = subprocess.run(
                    argv, input=text.encode('utf-8'),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=attempt_timeout,
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


# One narration chunk gets at most 4 minutes all-in (never-fail plan): up
# to 3 attempts at 60s for the whole chunk, then (once) a re-split into
# halves, each with its own attempts inside the same 240s deadline.
_LINUX_TTS_CHUNK_BUDGET_SECONDS = 240.0


def _split_chunk_half(text: str) -> list[str]:
    """Split a narration chunk in half at a sentence boundary if possible.

    Used when a whole chunk fails to synthesize: each half is a fresh cache
    entry with its own attempts, so one bad sentence cannot kill the whole
    chunk (and the whole render). Falls back to a word midpoint when the
    chunk is a single sentence.
    """
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', str(text or '').strip()) if s.strip()]
    if len(sentences) >= 2:
        half = len(sentences) // 2
        return [' '.join(sentences[:half]).strip(), ' '.join(sentences[half:]).strip()]
    words = str(text or '').split()
    if len(words) < 2:
        return [str(text or '').strip()]
    half = len(words) // 2
    return [' '.join(words[:half]).strip(), ' '.join(words[half:]).strip()]


def _linux_chunk_cache_path(cache_dir, identity, text):
    digest = hashlib.sha256((identity + '\0' + text).encode('utf-8')).hexdigest()
    return cache_dir / (digest + '.mp3')


def _linux_chunk_is_valid(path):
    try:
        return path.is_file() and path.stat().st_size >= 128 and _get_audio_duration(str(path)) > 0
    except Exception:
        return False


def _synthesize_linux_chunk(text, cache_dir, identity, work_dir, index, total, chunk_start,
                            *, sub=None, allow_split=True):
    """Synthesize one narration chunk; return its work-dir file(s), in order.

    Normally one file. When the whole chunk fails after its 3 attempts and
    at least one full attempt of budget is left, it is re-split into halves
    (each its own cache entry, own attempts, same 240s deadline) and the two
    half files are returned instead — the caller assembles them in order,
    so the narration keeps its word order. A half that fails after its
    attempts raises: at ~30–40 words a further failure means the TTS
    backend itself is down, not the chunk.
    """
    label = f"{index + 1}" + (f".{sub + 1}" if sub is not None else "") + f"/{total}"
    suffix = f'-{sub}' if sub is not None else ''
    cached = _linux_chunk_cache_path(cache_dir, identity, text)
    valid = _linux_chunk_is_valid(cached)
    print(f"   [TTS] Narration chunk {label}" + (" (cached)" if valid else ""), flush=True)
    if not valid:
        deadline = chunk_start + _LINUX_TTS_CHUNK_BUDGET_SECONDS
        generated = work_dir / f'generated-{index}{suffix}.mp3'
        try:
            _generate_linux_tts_audio(text, str(generated),
                                      timeout_seconds=_linux_tts_chunk_attempt_timeout(),
                                      deadline=deadline)
            os.replace(generated, cached)
        except Exception:
            if not allow_split:
                raise
            halves = _split_chunk_half(text)
            if len(halves) < 2 or deadline - time.time() < 60.0:
                raise
            print(f"   [TTS] Narration chunk {label} failed; re-splitting into halves", flush=True)
            parts = []
            for half_index, half in enumerate(halves):
                parts.extend(_synthesize_linux_chunk(
                    half, cache_dir, identity, work_dir, index, total, chunk_start,
                    sub=half_index, allow_split=False))
            return parts
    local = work_dir / f'chunk-{index}{suffix}.mp3'
    shutil.copyfile(cached, local)
    # S8: the Linux TTS binary writes no word timings, so without this the
    # caption step falls back to slow local Whisper. The chunk text is
    # known and its duration is measured, so write a script-derived timing
    # sidecar for this chunk file; _concat_edge_chunks merges them (each
    # chunk re-anchors at its own measured start, so no drift accumulates)
    # and the caption step then uses them instead of Whisper. Best effort:
    # a missing sidecar only costs a Whisper run, never the narration.
    try:
        from .speech_timing import save_speech_timing, script_words_for_audio
        chunk_duration = _get_audio_duration(str(local))
        rows = script_words_for_audio(text, chunk_duration)
        if rows:
            save_speech_timing(str(local), rows, source='tts_script_timing')
    except Exception:
        pass
    return [str(local)]


def _linux_tts_workers() -> int:
    """Bounded parallelism for Linux TTS chunks (default 5, spec §4.1).

    The Muse TTS CLI is a network client (remote inference): each worker
    is a subprocess mostly waiting on the API, so 5 concurrent calls run
    at ~full speed each (measured: 8 x 400-char chunks take 58s
    sequential vs 16s at 4 workers, with no per-chunk slowdown) and use
    little local CPU/RAM. VIDZEO_LINUX_TTS_WORKERS overrides (clamped
    to 1–5).
    """
    try:
        workers = int(os.environ.get("VIDZEO_LINUX_TTS_WORKERS", "") or 5)
    except (TypeError, ValueError):
        workers = 5
    return max(1, min(5, workers))


# Linux chunk sizing: ~1,600-char target, chunk count scales with script
# length up to 32 chunks. A 2,711-word (~14.5k-char) script becomes 10
# x ~1,450-char chunks (~90s audio each); each synthesizes in ~25s,
# inside the 90s per-attempt timeout below. The count MUST scale: a
# fixed cap (the old code effectively made 8) would let chunk size grow
# unboundedly on long scripts — a 30k-char script would become 8 x
# ~3,750-char chunks (~235s audio, ~50s+ wall each, flirting with the
# attempt timeout) instead of 15 bounded ones. 32 covers scripts to
# ~64k chars at target size; beyond that chunks grow gracefully
# (~3,100 chars at 100k chars, still inside the timeout). More chunks
# also means more progress events and better wave balancing; the cost
# is one ~2s session setup per chunk, negligible next to synthesis.
# Target 2000 (not 1600): with 4 workers the canonical ~14.5k-char
# script lands on 8 chunks = 2 full waves. At 1600 it made 10 chunks,
# a third mostly-empty wave — measured 96.7s cold vs 81.2s for 8 chunks.
_LINUX_TTS_TARGET_CHUNK_CHARS = 2000
_LINUX_TTS_MAX_CHUNKS = 32


def _linux_tts_chunk_attempt_timeout() -> float:
    """Per-attempt timeout for one Linux TTS chunk (default 90s).

    A ~2,000-char chunk synthesizes ~130s of audio and needs ~25s wall
    unloaded, more under load; 60s was tight enough to burn retries.
    The 240s whole-chunk budget in _synthesize_linux_chunk still bounds
    all attempts together. VIDZEO_LINUX_TTS_CHUNK_TIMEOUT_SECONDS
    overrides.
    """
    try:
        return float(os.environ.get("VIDZEO_LINUX_TTS_CHUNK_TIMEOUT_SECONDS", "") or 90.0)
    except (TypeError, ValueError):
        return 90.0


def _generate_linux_tts_chunked(script: str, progress_callback=None) -> tuple:
    """Keep completed speech chunks across retries and assemble them in order.

    Linux chunks are large and balanced (~1,600 chars, count scales with
    script length up to 32) and synthesize at up to 4 workers: the CLI is
    network-bound, so this is ~3x faster than the old 56 x 360-char /
    2-worker shape. Progress is reported as each chunk finishes (``progress_callback(fraction,
    message)``) so the job percentage moves during narration instead of
    sitting at 10%; assembly still happens strictly in chunk order.
    """
    chunks = _split_script_for_parallel_tts(
        script,
        target_chunk_chars=_LINUX_TTS_TARGET_CHUNK_CHARS,
        max_chunks=_LINUX_TTS_MAX_CHUNKS,
    )
    if not chunks:
        raise ValueError("Cannot synthesize empty narration.")
    cache_dir = Path(TEMP_DIR) / 'linux-tts-chunks'
    cache_dir.mkdir(parents=True, exist_ok=True)
    resolved = linux_tts_config()
    if resolved is None:
        raise ValueError("No Linux TTS command available.")
    identity = '\0'.join(resolved)
    work_dir = Path(tempfile.mkdtemp(prefix='linux-tts-', dir=str(TEMP_DIR)))
    assembled = []
    total = len(chunks)

    def _report(done: int):
        if progress_callback is not None:
            progress_callback(done / total, f"Generating voice over... chunk {done}/{total}")

    try:
        def _run(args):
            index, text = args
            return _synthesize_linux_chunk(
                text, cache_dir, identity, work_dir, index, len(chunks), time.time())

        workers = _linux_tts_workers()
        if workers <= 1 or len(chunks) <= 1:
            for index, text in enumerate(chunks):
                assembled.extend(_run((index, text)))
                _report(index + 1)
        else:
            from concurrent.futures import as_completed
            from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
            # as_completed reports each chunk as it finishes (progress
            # moves during narration); parts are collected by index and
            # assembled in order below, so word order is preserved. A
            # failed chunk raises after its siblings drain (the pool
            # context waits on exit), as pool.map did before.
            #
            # No-progress watchdog: as_completed's timeout bounds each
            # wait for the NEXT chunk, so a provider that stops
            # completing chunks entirely raises TimeoutError after
            # _narration_chunk_stall_seconds() instead of blocking the
            # worker thread forever with no progress callback (the
            # 2026-11-21 narration stall: heartbeat dead, worker deaf).
            # Pending futures are cancelled and the error names the
            # completed/total counts, so the queue worker can fail the
            # job loudly and the driver can resume it.
            stall_seconds = _narration_chunk_stall_seconds()
            narration_started = time.monotonic()
            parts_by_index = {}
            with ThreadPoolExecutor(max_workers=min(workers, len(chunks)),
                                    thread_name_prefix="linux-tts") as pool:
                futures = {
                    pool.submit(_run, (index, text)): index
                    for index, text in enumerate(chunks)
                }
                done = 0
                try:
                    for future in as_completed(futures, timeout=stall_seconds):
                        parts_by_index[futures[future]] = future.result()
                        done += 1
                        _report(done)
                except TimeoutError:
                    for future in futures:
                        if not future.done():
                            future.cancel()
                    raise TimeoutError(
                        f"Linux TTS narration stalled: no chunk completed in "
                        f"{stall_seconds:.0f}s ({done}/{len(chunks)} chunks done, "
                        f"{time.monotonic() - narration_started:.0f}s elapsed); "
                        "the TTS provider is not answering, failing loudly "
                        "instead of hanging the narration stage"
                    ) from None
            for index in range(len(chunks)):
                assembled.extend(parts_by_index[index])
        if len(assembled) == 1:
            final = Path(TEMP_DIR) / ('linux-tts-full-' + hashlib.sha256(os.urandom(32)).hexdigest()[:16] + '.mp3')
            old = assembled[0]
            os.replace(old, final)
            # S8: keep the per-chunk timing sidecar with the audio when the
            # single chunk is renamed, so the caption step can still use it.
            # The fingerprint is unchanged (same bytes, just a new name).
            try:
                from .speech_timing import timing_path
                old_sidecar = timing_path(old)
                if old_sidecar.is_file():
                    os.replace(old_sidecar, timing_path(str(final)))
            except Exception:
                pass
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
    Generate audio from text. On Linux the Muse TTS command goes first
    (see _generate_linux_tts_audio); Edge TTS is the fallback, with
    exponential backoff retries. Recovers with offline spoken narration
    if the online provider fails.
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
            # S8: the single-call Linux path also writes no timings; give
            # the caption step a script-derived sidecar so it skips Whisper
            # for short narrations too. Best effort only.
            try:
                from .speech_timing import save_speech_timing, script_words_for_audio
                rows = script_words_for_audio(script, linux_duration)
                if rows:
                    save_speech_timing(linux_path, rows, source='tts_script_timing')
            except Exception:
                pass
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
                           max_workers: int = 2, edge_options=None,
                           progress_callback=None):
    """
    Generate audio in chunks for long scripts, then concatenate.
    Primary narration on Linux is Muse TTS (chunked, in parallel);
    Edge TTS is only the fallback — its websockets are blocked by the
    egress proxy on this host. Retries with exponential backoff between
    ladder levels. Falls back to single-chunk generate_audio if all
    chunked attempts fail.

    ``progress_callback`` receives ``(fraction_done, message)`` as chunks
    resolve, so the job percentage moves during narration.
    """
    # Muse TTS first; a failure here falls through to the Edge ladder
    # below instead of killing the narration.
    if linux_tts_config() is not None:
        try:
            return _generate_linux_tts_chunked(script, progress_callback=progress_callback)
        except Exception as exc:
            print(f"   Muse TTS (chunked) failed, falling back to Edge TTS: {exc}", flush=True)

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
    # No-progress watchdog (same stall as the Muse lane): a lane whose
    # chunks all time out returns in ~75s per chunk at most, so a lane
    # that runs past the stall with zero new completions means the
    # provider call itself is wedged (not merely slow). Fail loudly
    # instead of grinding through the remaining lanes + single-audio
    # fallback against the same dead websocket.
    stall_seconds = _narration_chunk_stall_seconds()
    last_chunk_completed_at = time.monotonic()
    resolved_before_lane = 0

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
        if progress_callback is not None and chunks:
            progress_callback(
                len(resolved_paths) / len(chunks),
                f"Generating voice over... chunk {len(resolved_paths)}/{len(chunks)}",
            )
        if len(resolved_paths) > resolved_before_lane:
            resolved_before_lane = len(resolved_paths)
            last_chunk_completed_at = time.monotonic()
        elif time.monotonic() - last_chunk_completed_at >= stall_seconds:
            raise TimeoutError(
                f"Edge TTS narration stalled: no chunk completed in "
                f"{stall_seconds:.0f}s "
                f"({len(resolved_paths)}/{len(chunks)} chunks done); "
                "the Edge websocket is not answering, failing loudly "
                "instead of hanging the narration stage"
            )
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
