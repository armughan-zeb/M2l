# =============================================================================
# AUDIO-FIRST PIPELINE — Sequential Orchestrator for Perfect Scene Sync
# =============================================================================
#
# The core of the audio-first architecture:
#   1. TTS generates audio (ground truth duration)
#   2. Whisper transcribes for word-level timestamps
#   3. Scene splitter creates beat-based boundaries from timestamps
#
# Media resolution (images/video) is handled separately by _resolve_media_paths
# in app.py after this pipeline returns.
# =============================================================================

import os
import time

from config import DEFAULT_TTS_MODEL, DEFAULT_VO_MODE, normalize_generation_config

def run_audio_first_pipeline(config: dict, progress_callback=None) -> dict:
    """
    Execute the audio-first video generation pipeline.
    
    Audio is the master clock. Everything else syncs to it.
    This function handles TTS, Whisper transcription, and scene splitting.
    Media resolution (images/video) is handled separately by _resolve_media_paths.
    """
    config = normalize_generation_config(config)
    script = config.get('script', '')
    if not script or len(script) < 10:
        raise ValueError("Script too short for video generation")
    
    voice = config.get('voice', 'Adam (Male US)')
    vo_mode = config.get('vo_mode', DEFAULT_VO_MODE)
    tts_model = config.get('tts_model', DEFAULT_TTS_MODEL)
    scene_mode = config.get('scene_mode', 'Every Beat')
    edge_tts_options = config.get('edge_tts_options') or {}
    openrouter_api_key = (
        config.get('openrouter_api_key')
        or os.environ.get('OPENROUTER_API_KEY')
        or os.environ.get('VIDZEO_OPENROUTER_API_KEY')
        or ''
    )
    openrouter_tts_options = config.get('openrouter_tts_options') or {}
    
    result = {
        'audio_path': None,
        'duration': 0.0,
        'image_paths': [],
        'scene_durations': [],
        'scene_texts': [],
        'whisper_words': [],
    }
    
    print("\n" + "=" * 60)
    print("AUDIO-FIRST PIPELINE — Starting...")
    print("=" * 60)

    from services.transcription_service import (
        start_whisper_job_warmup,
        trigger_whisper_worker_prewarm,
        align_whisper_words_to_script,
    )
    # The offline Linux TTS command supplies no provider word timings, so
    # captions still need Whisper: warm it even for edge/audiogram jobs when
    # that command is configured (see _start_whisper_warmup in app.py).
    _linux_tts = bool(str(os.environ.get('VIDZEO_LINUX_TTS_COMMAND', '') or '').strip())
    _provider_supplies_clock = str(tts_model or 'audiogram').lower() in {'audiogram', 'edge', 'edge-tts', 'edge_tts'} and not _linux_tts
    whisper_handle = None if _provider_supplies_clock else start_whisper_job_warmup(preload_workers=True)

    # ─── TTS ───
    if progress_callback:
        progress_callback(0.05, "Starting TTS...")
    
    a_path, dur = _run_tts(
        script,
        voice,
        vo_mode,
        tts_model,
        edge_tts_options=edge_tts_options,
        openrouter_api_key=openrouter_api_key,
        openrouter_tts_options=openrouter_tts_options,
    )
    if not a_path or dur <= 0:
        raise ValueError("TTS generation failed")
    from .narration_quality import validate_narration_audio
    try:
        result['narration_quality'] = validate_narration_audio(a_path)
    except RuntimeError:
        # A provider may return a valid container containing silence. Recover
        # before transcription creates a fictional clock over that silence.
        from .edge_tts_service import _generate_sapi_audio
        from pathlib import Path
        a_path, dur = _generate_sapi_audio(script, str(Path(a_path).with_name(Path(a_path).stem + '_speech_recovery.wav')))
        result['narration_quality'] = validate_narration_audio(a_path)
    
    # ─── Natural Silence Removal / Pause Shortening ───
    from .silence_remover import shorten_audio_silence
    try:
        a_path, dur, _, silence_report = shorten_audio_silence(
            a_path,
            min_silence_seconds=float(config.get('silence_min_duration_seconds') or 0.38),
            target_cadence_seconds=float(config.get('silence_target_cadence_seconds') or 0.22),
        )
        if silence_report.get('cuts_applied', 0) > 0:
            result['silence_removal'] = silence_report
    except Exception as exc:
        print(f"   [silence_remover] Warning: silence shortening skipped: {exc}")

    result['audio_path'] = a_path
    result['duration'] = dur

    # TTS done — immediately trigger Whisper worker loads
    if whisper_handle is not None:
        trigger_whisper_worker_prewarm(whisper_handle)

    # ─── Whisper Transcription ───
    if progress_callback:
        progress_callback(0.15, "TTS ready, transcribing...")
    
    from .speech_timing import aligned_provider_words
    provider_words, provider_alignment = aligned_provider_words(a_path, script, dur)
    words = provider_words or _run_whisper(
        a_path,
        whisper_mode='local',
        warmup_handle=whisper_handle,
        language=config.get('language'),
    )
    # Keep scene splitting synchronized even when an offline Whisper model is
    # unavailable. This is a timing-only fallback derived from the exact TTS
    # script; it is explicitly marked so it cannot be mistaken for a Whisper
    # transcription, and it preserves every word and punctuation boundary.
    timing_source = "tts_word_boundary" if provider_words else "whisper"
    # S8: the Linux TTS chunk path writes a script-derived timing sidecar
    # (per-chunk measured durations, no speech recognition). When the
    # provider words came from that sidecar, Whisper never ran — report
    # it as script timing, not as measured provider boundaries.
    if provider_words and all(
        row.get("timing_source") == "tts_script_timing" for row in provider_words
    ):
        timing_source = "tts_script_timing"
    alignment = provider_alignment if provider_words else None
    if not words:
        words = _build_script_timing_fallback(script, dur)
        timing_source = "tts_script_timing_fallback"
        print(f"   [whisper] no model output; using script timing fallback ({len(words)} words)")
        alignment = None
    elif not provider_words:
        aligned_words, alignment = align_whisper_words_to_script(script, words, dur)
        if aligned_words and float(alignment.get("exact_match_ratio", 0.0) or 0.0) >= 0.45:
            words = aligned_words
            timing_source = "whisper_script_aligned"
        else:
            words = _build_script_timing_fallback(script, dur)
            timing_source = "tts_script_timing_low_confidence_whisper"
            if alignment:
                alignment["status"] = "low_confidence_script_clock_used"
    result['whisper_words'] = words
    result['timing_source'] = timing_source
    result['whisper_alignment'] = alignment
    
    # ─── Scene Split ───
    if progress_callback:
        progress_callback(0.20, "Splitting scenes...")
    
    from services.scene_splitter import align_expected_scenes_to_transcript, fallback_split_by_text, get_scene_durations, get_scene_texts
    if words:
        scenes = align_expected_scenes_to_transcript(script, scene_mode, words, dur)
    else:
        scenes = fallback_split_by_text(script, scene_mode, dur)
    
    result['scene_durations'] = get_scene_durations(scenes)
    result['scene_texts'] = get_scene_texts(scenes)
    
    print(f"   ✅ Audio pipeline complete ({len(scenes)} scenes)")
    
    return result


def _build_script_timing_fallback(script: str, duration: float) -> list:
    """Create monotonic word timings from the source script as a last resort."""
    from .speech_timing import script_words_for_audio
    return script_words_for_audio(script, duration, timing_source="tts_script_timing_fallback")

# =============================================================================
# INTERNAL HELPERS
# =============================================================================

def _run_tts(script: str, voice: str, vo_mode: str = DEFAULT_VO_MODE,
             tts_model: str = DEFAULT_TTS_MODEL,
             edge_tts_options: dict | None = None,
             openrouter_api_key: str | None = None,
             openrouter_tts_options: dict | None = None) -> tuple:
    """Run the selected TTS provider and return (audio_path, duration)."""
    start = time.time()
    script_text = script or ""
    word_count = len(script_text.split())
    long_form_parallel_tts = len(script_text) >= 450 or word_count >= 80

    del vo_mode
    from services.openrouter_tts_service import is_openrouter_tts_model
    if is_openrouter_tts_model(tts_model):
        from services import openrouter_tts_service
        try:
            if long_form_parallel_tts:
                try:
                    openrouter_tts_workers = max(
                        1,
                        min(
                            4,
                            int(os.environ.get("VIDZEO_OPENROUTER_TTS_WORKERS", "3") or 3),
                        ),
                    )
                except (TypeError, ValueError):
                    openrouter_tts_workers = 3
                path, dur = openrouter_tts_service.generate_audio_chunked(
                    script,
                    voice,
                    model=tts_model,
                    api_key=openrouter_api_key,
                    max_workers=openrouter_tts_workers,
                    options=openrouter_tts_options or {},
                )
            else:
                path, dur = openrouter_tts_service.generate_audio(
                    script,
                    voice,
                    model=tts_model,
                    api_key=openrouter_api_key,
                    options=openrouter_tts_options or {},
                )
            print(f"   ⏱️ TTS completed in {time.time() - start:.1f}s (provider=openrouter)")
            return path, dur
        except Exception as exc:
            # OpenRouter is the preferred voice provider, but a DNS/HTTP
            # outage must not discard a fully resolved media job. Edge TTS is
            # already bundled with the desktop runtime and gives the queue a
            # bounded, audible recovery path. Keep the original exception in
            # the log so the run is diagnosable instead of silently switching.
            fallback_enabled = str(
                os.environ.get("VIDZEO_OPENROUTER_TTS_FALLBACK_EDGE", "1")
            ).strip().lower() in {"1", "true", "yes", "on"}
            if not fallback_enabled:
                raise
            print(
                "   [tts] OpenRouter unavailable; using bundled Edge TTS fallback: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            from services import edge_tts_service
            fallback_voice = str(
                (edge_tts_options or {}).get("fallback_voice")
                or os.environ.get("VIDZEO_EDGE_TTS_FALLBACK_VOICE", "en-US-BrianNeural")
            ).strip() or "en-US-BrianNeural"
            if long_form_parallel_tts:
                path, dur = edge_tts_service.generate_audio_chunked(
                    script,
                    fallback_voice,
                    max_workers=3,
                    edge_options=edge_tts_options,
                )
            else:
                path, dur = edge_tts_service.generate_audio(
                    script,
                    fallback_voice,
                    edge_options=edge_tts_options,
                )
            print(f"   [tts] completed in {time.time() - start:.1f}s (provider=edge_fallback)")
            return path, dur

    from services import edge_tts_service
    if long_form_parallel_tts:
        path, dur = edge_tts_service.generate_audio_chunked(
            script, voice, max_workers=5, edge_options=edge_tts_options
        )
    else:
        path, dur = edge_tts_service.generate_audio(
            script, voice, edge_options=edge_tts_options
        )
    
    print(f"   ⏱️ TTS completed in {time.time() - start:.1f}s")
    return path, dur

def _run_whisper(audio_path: str, whisper_mode: str = 'local',
                 warmup_handle: dict | None = None, language: str | None = None,
                 **_legacy_options) -> list:
    """Run Whisper transcription and return word timestamps."""
    try:
        del whisper_mode, _legacy_options
        from services.transcription_service import transcribe_audio
        normalized_language = str(language or '').strip()
        if normalized_language.lower() in {'', 'auto', 'detect'}:
            normalized_language = None
        words, _ = transcribe_audio(
            audio_path,
            warmup_handle=warmup_handle,
            language=normalized_language,
        )
        return words if words else []
    except Exception as e:
        print(f"   ⚠️ Whisper error: {e}")
        return []
