"""OpenRouter speech models used by Vidzeo.

This module deliberately owns the complete OpenRouter TTS contract.  The UI
can discover the same model and voice catalog that the audio pipeline uses,
and a selected model can no longer be silently routed through Edge TTS.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests

from config import (
    OPENROUTER_TTS_MODEL_IDS,
    TEMP_DIR,
    TTS_MODEL_OPTIONS,
)


OPENROUTER_BASE_URL = (
    os.environ.get("VIDZEO_OPENROUTER_BASE_URL")
    or "https://openrouter.ai/api/v1"
).rstrip("/")
OPENROUTER_MODELS_URL = f"{OPENROUTER_BASE_URL}/models"
OPENROUTER_SPEECH_URL = f"{OPENROUTER_BASE_URL}/audio/speech"
CATALOG_CACHE_TTL_SECONDS = 60 * 60 * 12
CATALOG_TIMEOUT_SECONDS = 12
TTS_TIMEOUT_SECONDS = 120
MAX_CHUNK_CHARS = max(
    1200,
    int(os.environ.get("VIDZEO_OPENROUTER_TTS_CHUNK_CHARS", "2800") or 2800),
)


_FISH_DEFAULT_VOICE = {
    "id": "",
    "label": "Provider default (natural)",
    "language": "multi",
    "language_name": "Multilingual",
    "is_default": True,
    "voice_required": False,
    "catalog_source": "provider_default",
    "category": "provider",
    "description": "Fish Audio chooses the base voice. No fixed preset voice IDs are published through OpenRouter.",
}

# Fish Audio S2.1 Pro is an open-ended voice/style model rather than a fixed
# preset library. These profiles are app-level delivery cues: they are sent as
# Fish bracket instructions and never as fake provider voice IDs. This gives
# users dependable choices while preserving the provider's real contract.
_FISH_STYLE_PROFILES = (
    {
        "id": "fish-style-documentary",
        "label": "Documentary narrator",
        "category": "delivery",
        "instruction": "calm, authoritative documentary narrator",
        "description": "Measured, clear delivery for documentaries and explainers.",
    },
    {
        "id": "fish-style-warm-explainer",
        "label": "Warm explainer",
        "category": "delivery",
        "instruction": "warm, friendly explainer speaking naturally",
        "description": "Approachable and conversational for tutorials and how-to videos.",
    },
    {
        "id": "fish-style-news-anchor",
        "label": "News anchor",
        "category": "delivery",
        "instruction": "clear, confident news anchor with precise emphasis",
        "description": "Crisp pacing for news, facts, and current events.",
    },
    {
        "id": "fish-style-energetic",
        "label": "Energetic presenter",
        "category": "delivery",
        "instruction": "energetic, upbeat presenter with lively emphasis",
        "description": "Higher momentum for gaming, sports, and listicles.",
    },
    {
        "id": "fish-style-cinematic",
        "label": "Cinematic storyteller",
        "category": "delivery",
        "instruction": "cinematic, low-key storyteller with deliberate pauses",
        "description": "Slower, atmospheric delivery for dramatic stories.",
    },
    {
        "id": "fish-character-heroic",
        "label": "Heroic character",
        "category": "character",
        "instruction": "confident heroic character voice, expressive but natural",
        "description": "A confident character style for trailers and fictional moments.",
    },
    {
        "id": "fish-character-mysterious",
        "label": "Mysterious character",
        "category": "character",
        "instruction": "mysterious character voice, restrained and suspenseful",
        "description": "Restrained suspense for crime, mystery, and thriller scenes.",
    },
    {
        "id": "fish-character-playful",
        "label": "Playful character",
        "category": "character",
        "instruction": "playful, curious character voice with bright expression",
        "description": "Light, animated delivery for fun and family-friendly stories.",
    },
)

_FISH_STYLE_BY_ID = {item["id"]: item for item in _FISH_STYLE_PROFILES}

_CATALOG_CACHE: dict[str, Any] = {"expires_at": 0.0, "models": None}
_CATALOG_LOCK = threading.Lock()


def _model_option(model_id: str) -> dict[str, Any]:
    for option in TTS_MODEL_OPTIONS:
        if option.get("id") == model_id:
            return dict(option)
    return {"id": model_id, "label": model_id, "provider": "OpenRouter"}


def is_openrouter_tts_model(model_id: str | None) -> bool:
    return str(model_id or "").strip() in OPENROUTER_TTS_MODEL_IDS


def _voice_label(voice_id: str) -> str:
    value = str(voice_id or "").strip()
    if not value:
        return "English (provider default)"
    parts = value.removeprefix("flux-").removesuffix("-en").split("-")
    name = " ".join(part.capitalize() for part in parts if part)
    return f"{name or value} (English)"


def _is_english_voice(voice: Any) -> bool:
    if isinstance(voice, dict):
        language = str(
            voice.get("language")
            or voice.get("language_code")
            or voice.get("locale")
            or ""
        ).lower()
        voice_id = str(voice.get("id") or voice.get("name") or "").lower()
        return language.startswith("en") or bool(re.search(r"(?:^|[-_])en(?:[-_]|$)", voice_id))
    value = str(voice or "").lower()
    return bool(
        re.search(r"(?:^|[-_])en(?:[-_]|$)", value)
        or value.endswith("-en")
        or value.endswith("_en")
    )


def _normalize_voice(voice: Any) -> dict[str, Any] | None:
    if isinstance(voice, dict):
        voice_id = str(voice.get("id") or voice.get("name") or "").strip()
        if not voice_id:
            return None
        return {
            "id": voice_id,
            "label": str(voice.get("label") or voice.get("name") or _voice_label(voice_id)),
            "language": str(voice.get("language") or voice.get("language_code") or "en"),
            "language_name": "English",
            "gender": voice.get("gender"),
            "is_default": bool(voice.get("is_default", False)),
            "category": voice.get("category"),
            "description": voice.get("description"),
            "voice_required": True,
            "catalog_source": "openrouter_models_api",
        }
    voice_id = str(voice or "").strip()
    if not voice_id:
        return None
    return {
        "id": voice_id,
        "label": _voice_label(voice_id),
        "language": "en",
        "language_name": "English",
        "is_default": False,
        "voice_required": True,
        "catalog_source": "openrouter_models_api",
    }


def _builtin_voices(model_id: str) -> list[dict[str, Any]]:
    if model_id == "fish-audio/s2.1-pro-free:free":
        voices = [dict(_FISH_DEFAULT_VOICE)]
        for profile in _FISH_STYLE_PROFILES:
            voices.append({
                "id": profile["id"],
                "label": f"{'Style' if profile['category'] == 'delivery' else 'Character'} — {profile['label']}",
                "language": "multi",
                "language_name": "Multilingual",
                "is_default": False,
                "voice_required": False,
                "catalog_source": "vidnets_style_profile",
                "category": profile["category"],
                "description": profile["description"],
            })
        return voices
    return []


def _model_rows_from_response(payload: Any) -> dict[str, dict[str, Any]]:
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    return {
        str(row.get("id")): row
        for row in rows
        if isinstance(row, dict) and str(row.get("id") or "") in OPENROUTER_TTS_MODEL_IDS
    }


def _remote_english_voices(model_id: str, row: dict[str, Any] | None) -> list[dict[str, Any]]:
    supported = row.get("supported_voices") if isinstance(row, dict) else None
    if not isinstance(supported, list):
        return []
    voices = []
    for raw_voice in supported:
        if not _is_english_voice(raw_voice):
            continue
        normalized = _normalize_voice(raw_voice)
        if normalized:
            if model_id == "fish-audio/s2.1-pro-free:free" and not normalized.get("category"):
                normalized["category"] = "provider"
            voices.append(normalized)
    return voices


def _merge_voice_catalogs(*catalogs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge provider and bundled options without duplicating the same voice ID."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for catalog in catalogs:
        for voice in catalog:
            voice_id = str(voice.get("id") or "")
            if voice_id in seen:
                continue
            seen.add(voice_id)
            merged.append(dict(voice))
    return merged


def _build_catalog(remote_rows: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    remote_rows = remote_rows or {}
    catalog = []
    for model_id in OPENROUTER_TTS_MODEL_IDS:
        option = _model_option(model_id)
        remote = remote_rows.get(model_id, {})
        remote_voices = _remote_english_voices(model_id, remote)
        bundled_voices = _builtin_voices(model_id)
        # Deepgram's remote catalog is authoritative when available. Fish may
        # expose provider voices in the future; keep those alongside the
        # provider-default option and Vidnets' app-level character profiles.
        voices = (
            _merge_voice_catalogs(remote_voices, bundled_voices)
            if model_id == "fish-audio/s2.1-pro-free:free"
            else (remote_voices or bundled_voices)
        )
        catalog.append({
            "id": model_id,
            "label": option.get("label") or remote.get("name") or model_id,
            "name": remote.get("name") or option.get("label") or model_id,
            "provider": option.get("provider") or remote.get("provider") or "OpenRouter",
            "description": remote.get("description") or "OpenRouter text-to-speech model",
            "architecture": remote.get("architecture") or "text->speech",
            "voices": voices,
            "default_voice": next(
                (voice["id"] for voice in voices if voice.get("is_default") and voice.get("id")),
                voices[0].get("id") if voices and voices[0].get("id") else "",
            ),
            "voice_required": False,
            "voice_catalog_note": (
                "Fish Audio does not publish fixed preset voice IDs through OpenRouter. "
                "Choose a delivery or character profile, or keep the provider default."
                if model_id == "fish-audio/s2.1-pro-free:free" else ""
            ),
            "catalog_source": "openrouter_models_api" if remote else "bundled_fallback",
        })
    return catalog


def _refresh_speech_catalog_locked(api_key=None):
    """Caller owns the refresh lock; regular UI reads never wait for it."""
    try:
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {str(api_key).strip()}"
        response = requests.get(OPENROUTER_MODELS_URL,
            params={"output_modalities": "speech"}, headers=headers, timeout=(4, 8))
        response.raise_for_status()
        rows = _model_rows_from_response(response.json())
        _CATALOG_CACHE["models"] = _build_catalog(rows)
        _CATALOG_CACHE["expires_at"] = time.time() + CATALOG_CACHE_TTL_SECONDS
    except Exception as exc:
        # Retain the bundled usable voice catalog and retry later.
        _CATALOG_CACHE["models"] = _CATALOG_CACHE.get("models") or _build_catalog()
        _CATALOG_CACHE["expires_at"] = time.time() + 60
        print(f"Speech catalog refresh unavailable: {type(exc).__name__}")


def get_openrouter_tts_catalog(force_refresh=False, api_key=None):
    """Serve bundled/cached voices immediately; refresh metadata in the background."""
    if force_refresh:
        with _CATALOG_LOCK:
            _refresh_speech_catalog_locked(api_key)
        return _CATALOG_CACHE["models"]
    catalog = _CATALOG_CACHE.get("models") or _build_catalog()
    if time.time() >= float(_CATALOG_CACHE.get("expires_at", 0)) and _CATALOG_LOCK.acquire(False):
        def refresh():
            try:
                _refresh_speech_catalog_locked(api_key)
            finally:
                _CATALOG_LOCK.release()
        try:
            threading.Thread(target=refresh, name="speech-catalog-refresh", daemon=True).start()
        except Exception:
            _CATALOG_LOCK.release()
    return catalog


def get_voice_catalog_for_model(model_id: str, api_key: str | None = None) -> list[dict[str, Any]]:
    for model in get_openrouter_tts_catalog(api_key=api_key):
        if model.get("id") == model_id:
            return list(model.get("voices") or [])
    return []


def _resolve_api_key(api_key: str | None) -> str:
    return str(
        api_key
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("VIDZEO_OPENROUTER_API_KEY")
        or ""
    ).strip()


def _resolve_voice(model_id: str, voice: str | None) -> str:
    requested = str(voice or "").strip()
    if model_id == "fish-audio/s2.1-pro-free:free":
        # Vidnets character and delivery profiles become bracket cues. If the
        # provider later publishes real voice IDs, allow those through using
        # the same model catalog contract as Deepgram.
        if requested in _FISH_STYLE_BY_ID:
            return requested
        allowed = {
            item.get("id") for item in get_voice_catalog_for_model(model_id)
            if item.get("id") and item.get("id") not in _FISH_STYLE_BY_ID
        }
        return requested if requested in allowed else ""
    if requested:
        allowed = {item.get("id") for item in get_voice_catalog_for_model(model_id)}
        if requested in allowed:
            return requested
    voices = get_voice_catalog_for_model(model_id)
    return str(voices[0].get("id") if voices else "flux-alexis-en")


def _fish_profile_prefix(voice: str | None) -> str:
    profile = _FISH_STYLE_BY_ID.get(str(voice or "").strip())
    if not profile:
        return ""
    # Fish S2-style models interpret natural-language bracket cues. Keep the
    # cue outside the provider `voice` field, which OpenRouter does not expose
    # for this model.
    return f"[{profile['instruction']}]"


def _duration_seconds(path: str, fallback_text: str = "") -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        duration = float((result.stdout or "").strip())
        if duration > 0:
            return duration
    except Exception:
        pass
    raise RuntimeError("The speech provider returned audio that could not be decoded.")


def _request_audio(
    script: str,
    model_id: str,
    voice: str,
    api_key: str,
    response_format: str = "mp3",
    options: dict[str, Any] | None = None,
) -> bytes:
    request_text = str(script or "")
    if model_id == "fish-audio/s2.1-pro-free:free":
        prefix = _fish_profile_prefix(voice)
        if prefix:
            request_text = f"{prefix} {request_text}".strip()
    body: dict[str, Any] = {
        "model": model_id,
        "input": request_text,
        "response_format": response_format,
    }
    if voice and (
        model_id != "fish-audio/s2.1-pro-free:free"
        or voice not in _FISH_STYLE_BY_ID
    ):
        body["voice"] = voice
    for key in ("speed", "sample_rate"):
        if options and options.get(key) is not None:
            body[key] = options[key]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "audio/mpeg, audio/wav, application/octet-stream",
    }
    referer = str(os.environ.get("OPENROUTER_HTTP_REFERER") or "").strip()
    title = str(os.environ.get("OPENROUTER_APP_TITLE") or "Vidzeo").strip()
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title

    from .job_context import remaining_seconds, check_job_active
    deadline = time.monotonic() + min(TTS_TIMEOUT_SECONDS, remaining_seconds(TTS_TIMEOUT_SECONDS))
    last_error: Exception | None = None
    for attempt in range(2):
        check_job_active()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        permanent_failure = False
        try:
            from .bounded_http import provider_post
            response = provider_post(requests.post,
                OPENROUTER_SPEECH_URL,
                headers=headers,
                json=body,
                timeout=remaining,
                usage_kind='tts', operation='narration_audio',
            )
            if getattr(response, "status_code", 200) >= 400:
                status = int(response.status_code)
                permanent_failure = 400 <= status < 500 and status not in {408, 429}
                detail = "OpenRouter TTS request failed"
                try:
                    payload = response.json()
                    detail = str((payload.get("error") or {}).get("message") or payload.get("message") or detail)
                except Exception:
                    text = str(getattr(response, "text", "") or "").strip()
                    if text:
                        detail = text[:500]
                if status == 401:
                    detail = 'OpenRouter API key rejected. Add or replace it: https://openrouter.ai/keys'
                elif status == 402:
                    detail = 'OpenRouter credits or the key spending limit are exhausted. Add credits: https://openrouter.ai/credits'
                elif status == 403:
                    detail = 'OpenRouter denied this model/key request. Check key permissions: https://openrouter.ai/keys'
                raise RuntimeError(f"HTTP {status}: {detail}")
            content = bytes(getattr(response, "content", b"") or b"")
            if not content:
                raise RuntimeError("OpenRouter TTS returned an empty audio response")
            content_type = str(getattr(response, "headers", {}).get("content-type", "")).lower()
            if "json" in content_type or content[:1] in {b"{", b"["}:
                try:
                    payload = json.loads(content.decode("utf-8", errors="replace"))
                    raise RuntimeError(str((payload.get("error") or {}).get("message") or payload.get("message") or "OpenRouter returned an error payload"))
                except json.JSONDecodeError:
                    pass
            return content
        except Exception as exc:
            last_error = exc
            if permanent_failure:
                break
            if attempt == 0:
                time.sleep(min(0.8, max(0.0, deadline - time.monotonic())))
    raise RuntimeError(f"OpenRouter TTS generation failed: {last_error}") from last_error


def generate_audio(
    script: str,
    voice: str = "",
    model: str = "fish-audio/s2.1-pro-free:free",
    api_key: str | None = None,
    response_format: str = "mp3",
    options: dict[str, Any] | None = None,
) -> tuple[str, float]:
    """Generate one OpenRouter TTS request and return ``(path, duration)``."""
    model_id = str(model or "").strip()
    if not is_openrouter_tts_model(model_id):
        raise ValueError(f"Unsupported OpenRouter TTS model: {model_id or '(empty)'}")
    key = _resolve_api_key(api_key)
    if not key:
        raise ValueError("An OpenRouter API key is required for this TTS model. Add one in Settings: https://openrouter.ai/keys")
    text = str(script or "").strip()
    if not text:
        raise ValueError("TTS script is empty")
    fmt = str(response_format or "mp3").strip().lower()
    # The renderer and ffmpeg pipeline expect a containerized audio file.
    # Keep raw PCM out of this path unless a future conversion stage is added.
    if fmt not in {"mp3", "wav"}:
        fmt = "mp3"

    resolved_voice = _resolve_voice(model_id, voice)
    extension = "wav" if fmt == "wav" else "mp3"
    token = hashlib.sha256(f"{model_id}|{resolved_voice}|{text}|{time.time_ns()}".encode()).hexdigest()[:16]
    output_path = str(TEMP_DIR / f"openrouter_tts_{token}.{extension}")
    print(f"   OpenRouter TTS: model={model_id}, voice={resolved_voice or 'provider-default'}")
    audio_bytes = _request_audio(text, model_id, resolved_voice, key, fmt, options)
    Path(output_path).write_bytes(audio_bytes)
    if not Path(output_path).is_file() or Path(output_path).stat().st_size == 0:
        raise RuntimeError("OpenRouter TTS audio could not be written")
    from .narration_quality import validate_narration_audio
    validate_narration_audio(output_path)
    return output_path, _duration_seconds(output_path, text)


def _split_text(script: str) -> list[str]:
    text = re.sub(r"\s+", " ", str(script or "").strip())
    if len(text) <= MAX_CHUNK_CHARS:
        return [text] if text else []
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", text) if item.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences or [text]:
        if current and len(current) + len(sentence) + 1 > MAX_CHUNK_CHARS:
            chunks.append(current.strip())
            current = ""
        if len(sentence) > MAX_CHUNK_CHARS:
            for start in range(0, len(sentence), MAX_CHUNK_CHARS):
                piece = sentence[start:start + MAX_CHUNK_CHARS].strip()
                if piece:
                    if current:
                        chunks.append(current.strip())
                        current = ""
                    chunks.append(piece)
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk]


def _concat_chunks(chunk_paths: list[str], text: str) -> tuple[str, float]:
    if len(chunk_paths) == 1:
        return chunk_paths[0], _duration_seconds(chunk_paths[0], text)
    token = hashlib.sha256(f"{text}|{time.time_ns()}".encode()).hexdigest()[:16]
    output_path = str(TEMP_DIR / f"openrouter_tts_full_{token}.mp3")
    concat_path = str(TEMP_DIR / f"openrouter_tts_concat_{token}.txt")
    try:
        with open(concat_path, "w", encoding="utf-8") as handle:
            for chunk_path in chunk_paths:
                handle.write(f"file '{os.path.abspath(chunk_path).replace(chr(92), '/')}'\n")
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", concat_path, "-c", "copy", output_path],
            capture_output=True,
            timeout=120,
            check=True,
        )
        return output_path, _duration_seconds(output_path, text)
    finally:
        Path(concat_path).unlink(missing_ok=True)
        for chunk_path in chunk_paths:
            if chunk_path != output_path:
                Path(chunk_path).unlink(missing_ok=True)


def generate_audio_chunked(
    script: str,
    voice: str = "",
    model: str = "fish-audio/s2.1-pro-free:free",
    api_key: str | None = None,
    max_workers: int = 1,
    options: dict[str, Any] | None = None,
) -> tuple[str, float]:
    """Generate long narration in sentence-safe chunks and concatenate it."""
    chunks = _split_text(script)
    if not chunks:
        raise ValueError("TTS script is empty")
    if len(chunks) == 1:
        return generate_audio(chunks[0], voice, model, api_key, options=options)

    worker_count = max(1, min(int(max_workers or 1), 4, len(chunks)))
    print(
        f"   OpenRouter TTS: processing {len(chunks)} ordered chunks "
        f"with {worker_count} worker(s)"
    )
    paths: list[str | None] = [None] * len(chunks)
    try:
        def _generate(index: int, chunk: str) -> tuple[int, str]:
            print(f"   OpenRouter TTS: chunk {index + 1}/{len(chunks)}")
            path, _ = generate_audio(chunk, voice, model, api_key, options=options)
            return index, path

        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="openrouter-tts",
        ) as executor:
            futures = {
                executor.submit(_generate, index, chunk): index
                for index, chunk in enumerate(chunks)
            }
            for future in as_completed(futures):
                index, path = future.result()
                paths[index] = path

        ordered_paths = [path for path in paths if path]
        if len(ordered_paths) != len(chunks):
            raise RuntimeError("OpenRouter TTS did not produce every narration chunk")
        return _concat_chunks(ordered_paths, str(script or ""))
    except Exception:
        for path in paths:
            if path:
                Path(path).unlink(missing_ok=True)
        raise


def preview_voice(
    voice: str = "",
    text: str = "Hello! This is a preview of my voice.",
    model: str = "fish-audio/s2.1-pro-free:free",
    api_key: str | None = None,
    options: dict[str, Any] | None = None,
) -> str:
    from .job_context import phase_scope
    with phase_scope(45):
        path, _ = generate_audio(text, voice, model, api_key, options=options)
    return path
