# =============================================================================
# SILENCE REMOVER — Natural Audio Silence Removal & Pause Shortening
# =============================================================================
# Shortens dead air pauses (> 350-500ms) down to a natural cadence (~200-250ms),
# while keeping word alignments, Whisper transcription timings, and scene
# boundaries strictly synchronized.
# =============================================================================

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import soundfile as sf

from .speech_timing import audio_fingerprint, load_speech_timing, save_speech_timing, timing_path


def _ffmpeg_path() -> str:
    configured = str(os.environ.get("VIDZEO_FFMPEG_PATH") or "").strip()
    if configured:
        return configured
    bundled = Path(__file__).resolve().parents[2] / "tools" / "ffmpeg" / "ffmpeg.exe"
    return str(bundled) if bundled.is_file() else (shutil.which("ffmpeg") or "ffmpeg")


def detect_silence_intervals(
    samples: np.ndarray,
    sample_rate: int,
    noise_threshold_db: float = -40.0,
    min_silence_seconds: float = 0.35,
    frame_ms: float = 25.0,
    hop_ms: float = 10.0,
) -> List[Tuple[float, float]]:
    """
    Detect intervals of acoustic silence (in seconds) in audio samples.
    Returns list of (start_seconds, end_seconds).
    """
    if len(samples) == 0 or sample_rate <= 0:
        return []

    mono = samples if samples.ndim == 1 else np.mean(samples, axis=1)
    frame_size = max(1, int(sample_rate * (frame_ms / 1000.0)))
    hop_size = max(1, int(sample_rate * (hop_ms / 1000.0)))

    if len(mono) < frame_size:
        return []

    n_frames = (len(mono) - frame_size) // hop_size + 1
    # Vectorized / chunked RMS computation for high performance
    frame_starts = np.arange(n_frames) * hop_size
    frame_ends = frame_starts + frame_size

    # Compute RMS per frame
    rms_values = np.zeros(n_frames, dtype=np.float32)
    for idx, (st, en) in enumerate(zip(frame_starts, frame_ends)):
        chunk = mono[st:en]
        rms_values[idx] = np.sqrt(np.mean(chunk * chunk) + 1e-12)

    db_values = 20.0 * np.log10(np.maximum(rms_values, 1e-6))
    peak_db = float(np.max(db_values)) if len(db_values) > 0 else -100.0

    # Dynamic threshold: adaptive to quiet speech recordings
    effective_thresh = min(noise_threshold_db, peak_db - 24.0)
    is_silent = db_values < effective_thresh

    # Find contiguous runs of silence
    intervals: List[Tuple[float, float]] = []
    in_silence = False
    run_start = 0

    for idx, silent in enumerate(is_silent):
        if silent and not in_silence:
            in_silence = True
            run_start = idx
        elif not silent and in_silence:
            in_silence = False
            duration_sec = (idx - run_start) * (hop_ms / 1000.0)
            if duration_sec >= min_silence_seconds:
                st_sec = run_start * (hop_ms / 1000.0)
                en_sec = idx * (hop_ms / 1000.0)
                intervals.append((round(st_sec, 3), round(en_sec, 3)))

    if in_silence:
        duration_sec = (len(is_silent) - run_start) * (hop_ms / 1000.0)
        if duration_sec >= min_silence_seconds:
            st_sec = run_start * (hop_ms / 1000.0)
            en_sec = len(is_silent) * (hop_ms / 1000.0)
            intervals.append((round(st_sec, 3), round(en_sec, 3)))

    return intervals


def _apply_crossfade(chunk_a: np.ndarray, chunk_b: np.ndarray, crossfade_samples: int) -> np.ndarray:
    """Join two audio chunks with a raised-cosine smooth crossfade to eliminate clicks."""
    if crossfade_samples <= 0 or len(chunk_a) < crossfade_samples or len(chunk_b) < crossfade_samples:
        return np.concatenate([chunk_a, chunk_b], axis=0)

    # Crossfade curves
    fade_t = np.linspace(0.0, 1.0, crossfade_samples, endpoint=True, dtype=np.float32)
    fade_out = 0.5 * (1.0 + np.cos(np.pi * fade_t))
    fade_in = 0.5 * (1.0 - np.cos(np.pi * fade_t))

    if chunk_a.ndim == 2:
        fade_out = fade_out[:, np.newaxis]
        fade_in = fade_in[:, np.newaxis]

    overlap = chunk_a[-crossfade_samples:] * fade_out + chunk_b[:crossfade_samples] * fade_in
    return np.concatenate([chunk_a[:-crossfade_samples], overlap, chunk_b[crossfade_samples:]], axis=0)


def adjust_word_timings(
    words: List[Dict[str, Any]],
    cuts: List[Tuple[float, float]],
    new_duration: float,
) -> List[Dict[str, Any]]:
    """
    Map word timestamps through silence cuts.
    Each cut is (cut_start_seconds, cut_end_seconds).
    Words occurring after a cut are shifted backward by the cut duration.
    """
    if not words or not cuts:
        return [dict(w) for w in words]

    updated = []
    for w in words:
        start = float(w.get("start", 0.0))
        end = float(w.get("end", start + 0.05))
        
        # Calculate how much earlier this word should move
        shift_start = 0.0
        shift_end = 0.0

        for cut_start, cut_end in cuts:
            cut_len = cut_end - cut_start
            if start >= cut_end:
                shift_start += cut_len
            elif start > cut_start:
                # Word started inside cut (unlikely, but snap to cut_start)
                shift_start += (start - cut_start)

            if end >= cut_end:
                shift_end += cut_len
            elif end > cut_start:
                shift_end += (end - cut_start)

        new_start = max(0.0, start - shift_start)
        new_end = max(new_start + 0.02, end - shift_end)

        row = dict(w)
        row["start"] = round(new_start, 3)
        row["end"] = round(min(new_duration, new_end), 3)
        updated.append(row)

    # Enforce strict monotonicity and non-overlap
    prev_end = 0.0
    for idx, row in enumerate(updated):
        s = max(prev_end, row["start"])
        e = max(s + 0.02, row["end"])
        row["start"] = round(s, 3)
        row["end"] = round(min(new_duration, e), 3)
        prev_end = row["end"]

    if updated:
        updated[-1]["end"] = round(min(new_duration, max(updated[-1]["start"] + 0.02, updated[-1]["end"])), 3)

    return updated


def shorten_audio_silence(
    audio_path: str,
    output_path: Optional[str] = None,
    words: Optional[List[Dict[str, Any]]] = None,
    min_silence_seconds: float = 0.38,
    target_cadence_seconds: float = 0.22,
    noise_threshold_db: float = -42.0,
    lead_silence_max_seconds: float = 0.10,
    trail_silence_max_seconds: float = 0.20,
    crossfade_ms: float = 8.0,
) -> Tuple[str, float, List[Dict[str, Any]], Dict[str, Any]]:
    """
    Cleanly shorten dead air pauses in speech audio down to a natural cadence (~200-250ms),
    updating speech timing and word alignments so they remain 100% in sync.

    Returns: (output_audio_path, new_duration_seconds, updated_words, report_dict)
    """
    audio_file = Path(audio_path)
    if not audio_file.is_file() or audio_file.stat().st_size < 128:
        raise ValueError(f"Audio file missing or empty: {audio_path}")

    # Read audio samples
    try:
        samples, sr = sf.read(str(audio_file), dtype="float32")
    except Exception as exc:
        # Fallback to ffmpeg decode if soundfile fails
        ffmpeg = _ffmpeg_path()
        tmp_wav = tempfile.mktemp(suffix=".wav")
        try:
            subprocess.run(
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(audio_file),
                 "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", tmp_wav],
                check=True, capture_output=True, timeout=60,
            )
            samples, sr = sf.read(tmp_wav, dtype="float32")
        finally:
            if os.path.exists(tmp_wav):
                try:
                    os.remove(tmp_wav)
                except OSError:
                    pass

    orig_sample_count = len(samples)
    orig_duration = orig_sample_count / float(sr)

    if orig_duration < 0.35:
        # Audio too short to contain long pauses
        loaded_words = words if words is not None else load_speech_timing(audio_path, orig_duration)
        return (str(audio_path), orig_duration, loaded_words or [], {"cuts_applied": 0, "seconds_removed": 0.0})

    # Load speech timing if not supplied
    if words is None:
        words = load_speech_timing(audio_path, orig_duration)
    else:
        words = [dict(w) for w in words]

    # Detect pauses: combine word boundaries with acoustic silence detection
    cuts: List[Tuple[float, float]] = []

    if words and len(words) >= 2:
        # Method A: Word-Guided Pause Shortening (guarantees words are NEVER touched)
        # 1. Check leading silence before first word
        first_start = float(words[0].get("start", 0.0))
        if first_start > (lead_silence_max_seconds + 0.15):
            cut_start = lead_silence_max_seconds
            cut_end = max(cut_start, first_start - 0.04)
            if cut_end - cut_start >= 0.08:
                cuts.append((round(cut_start, 3), round(cut_end, 3)))

        # 2. Check pauses between consecutive words
        for i in range(len(words) - 1):
            w_curr = words[i]
            w_next = words[i + 1]
            gap_start = float(w_curr.get("end", 0.0))
            gap_end = float(w_next.get("start", gap_start))
            gap_duration = gap_end - gap_start

            if gap_duration > min_silence_seconds:
                # Keep half of target cadence after curr word and half before next word
                cushion = target_cadence_seconds / 2.0
                cut_start = gap_start + cushion
                cut_end = gap_end - cushion
                if cut_end - cut_start >= 0.08:
                    cuts.append((round(cut_start, 3), round(cut_end, 3)))

        # 3. Check trailing silence after last word
        last_end = float(words[-1].get("end", orig_duration))
        if (orig_duration - last_end) > (trail_silence_max_seconds + 0.15):
            cut_start = last_end + trail_silence_max_seconds
            cut_end = orig_duration
            if cut_end - cut_start >= 0.08:
                cuts.append((round(cut_start, 3), round(cut_end, 3)))
    else:
        # Method B: Pure Acoustic Silence Detection
        acoustic_silences = detect_silence_intervals(
            samples, sr,
            noise_threshold_db=noise_threshold_db,
            min_silence_seconds=min_silence_seconds,
        )
        for sil_start, sil_end in acoustic_silences:
            sil_duration = sil_end - sil_start
            if sil_start < 0.15:
                # Leading silence
                cut_start = lead_silence_max_seconds
                cut_end = max(cut_start, sil_end - 0.05)
            elif sil_end >= orig_duration - 0.15:
                # Trailing silence
                cut_start = sil_start + trail_silence_max_seconds
                cut_end = orig_duration
            else:
                # Inter-phrase silence
                cushion = target_cadence_seconds / 2.0
                cut_start = sil_start + cushion
                cut_end = sil_end - cushion

            if cut_end - cut_start >= 0.08:
                cuts.append((round(cut_start, 3), round(cut_end, 3)))

    # Deduplicate and sort cuts
    cuts.sort(key=lambda x: x[0])
    merged_cuts: List[Tuple[float, float]] = []
    for c_st, c_en in cuts:
        if not merged_cuts:
            merged_cuts.append((c_st, c_en))
        else:
            prev_st, prev_en = merged_cuts[-1]
            if c_st <= prev_en + 0.02:
                merged_cuts[-1] = (prev_st, max(prev_en, c_en))
            else:
                merged_cuts.append((c_st, c_en))

    if not merged_cuts:
        # No dead air needed shortening
        return (str(audio_path), orig_duration, words or [], {"cuts_applied": 0, "seconds_removed": 0.0})

    # Splice audio samples
    keep_segments: List[np.ndarray] = []
    cursor_sample = 0
    crossfade_samples = max(4, int(sr * (crossfade_ms / 1000.0)))

    for cut_st, cut_en in merged_cuts:
        cut_start_sample = max(0, min(orig_sample_count, int(cut_st * sr)))
        cut_end_sample = max(cut_start_sample, min(orig_sample_count, int(cut_en * sr)))

        if cut_start_sample > cursor_sample:
            keep_segments.append(samples[cursor_sample:cut_start_sample])
        cursor_sample = cut_end_sample

    if cursor_sample < orig_sample_count:
        keep_segments.append(samples[cursor_sample:orig_sample_count])

    if not keep_segments:
        # Fallback to original if cuts somehow covered entire file
        return (str(audio_path), orig_duration, words or [], {"cuts_applied": 0, "seconds_removed": 0.0})

    # Assemble keep segments with crossfades
    shortened_samples = keep_segments[0]
    for seg in keep_segments[1:]:
        shortened_samples = _apply_crossfade(shortened_samples, seg, crossfade_samples)

    new_duration = len(shortened_samples) / float(sr)
    total_removed = orig_duration - new_duration

    # Output file destination
    target_out = Path(output_path) if output_path else audio_file
    is_mp3 = target_out.suffix.lower() == ".mp3"

    # Write output audio
    try:
        if is_mp3:
            sf.write(str(target_out), shortened_samples, sr, format="MP3")
        else:
            sf.write(str(target_out), shortened_samples, sr, format="WAV", subtype="PCM_16")
    except Exception:
        # Fallback to ffmpeg encoding if libsndfile write fails
        temp_wav = target_out.with_suffix(".temp_splice.wav")
        sf.write(str(temp_wav), shortened_samples, sr, format="WAV", subtype="PCM_16")
        ffmpeg = _ffmpeg_path()
        try:
            if is_mp3:
                subprocess.run(
                    [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(temp_wav),
                     "-c:a", "libmp3lame", "-b:a", "192k", str(target_out)],
                    check=True, capture_output=True, timeout=60,
                )
            else:
                shutil.move(str(temp_wav), str(target_out))
        finally:
            if temp_wav.exists():
                temp_wav.unlink(missing_ok=True)

    # Adjust word timings
    updated_words = adjust_word_timings(words or [], merged_cuts, new_duration)

    # Save synchronized timing receipt if speech timing existed or was loaded
    if updated_words:
        save_speech_timing(str(target_out), updated_words, source="silence_shortened")

    report = {
        "cuts_applied": len(merged_cuts),
        "seconds_removed": round(total_removed, 3),
        "original_duration": round(orig_duration, 3),
        "new_duration": round(new_duration, 3),
        "cuts": merged_cuts,
    }
    print(
        f"   [silence_remover] Shortened {len(merged_cuts)} pauses: "
        f"{orig_duration:.2f}s -> {new_duration:.2f}s (-{total_removed:.2f}s, cadence ~{target_cadence_seconds*1000:.0f}ms)"
    )
    return (str(target_out), new_duration, updated_words, report)


def shorten_sound_sequence(
    sound_path: str,
    output_path: Optional[str] = None,
    min_silence_seconds: float = 0.35,
    target_cadence_seconds: float = 0.20,
) -> Tuple[str, float]:
    """
    Utility to remove dead air from procedural SFX and sound sequences.
    """
    out_path, new_dur, _, _ = shorten_audio_silence(
        sound_path,
        output_path=output_path,
        min_silence_seconds=min_silence_seconds,
        target_cadence_seconds=target_cadence_seconds,
    )
    return out_path, new_dur
