"""Word clocks bound to the exact generated narration bytes."""
import hashlib
import json
import math
import re
from pathlib import Path


def script_words_for_audio(text, duration, *, timing_source='tts_script_timing'):
    """Distribute a known narration text across a measured audio duration.

    Each word gets a slice proportional to its length (longer words take
    longer to say), with a small bonus after sentence/clause punctuation
    for the natural pause. The rows are bounded by [0, duration] of THIS
    audio span, so when a long narration is built from per-chunk spans
    each chunk re-anchors at its own measured start and timing errors
    cannot accumulate across chunks (no drift). This is script-derived
    timing, not speech recognition: it is only valid when ``text`` is the
    exact text spoken in the audio, which is true for TTS narration.
    """
    tokens = re.findall(r"\S+", str(text or "").strip())
    if not tokens:
        return []
    total = max(0.1, float(duration or 0.1))
    weights = []
    for token in tokens:
        clean = re.sub(r"[^A-Za-z0-9']", "", token)
        weight = max(1.0, min(2.8, 0.55 + len(clean) * 0.075))
        if re.search(r"[.!?]$", token):
            weight += 0.22
        elif re.search(r"[,;:]$", token):
            weight += 0.08
        weights.append(weight)
    step = total / max(1.0, sum(weights))
    cursor = 0.0
    rows = []
    for index, token in enumerate(tokens):
        start = cursor
        end = min(total, start + step * weights[index])
        rows.append({"word": token, "start": round(start, 3),
                     "end": round(max(start + 0.02, end), 3),
                     "index": index, "timing_source": timing_source})
        cursor = end
    if rows:
        rows[-1]["end"] = total
    return rows


def timing_path(audio_path):
    return Path(str(audio_path) + '.speech-timing.json')


def audio_fingerprint(audio_path):
    digest = hashlib.sha256()
    with open(audio_path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def save_speech_timing(audio_path, words, *, source='tts_word_boundary'):
    payload = {'version': 1, 'audio_sha256': audio_fingerprint(audio_path),
               'source': source, 'words': words}
    target = timing_path(audio_path)
    temporary = target.with_suffix(target.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    temporary.replace(target)


def load_speech_timing(audio_path, duration):
    try:
        payload = json.loads(timing_path(audio_path).read_text(encoding='utf-8'))
        if payload.get('audio_sha256') != audio_fingerprint(audio_path):
            return []
        rows = payload.get('words') or []
        previous = 0.0
        cleaned = []
        for index, row in enumerate(rows):
            start, end = float(row['start']), float(row['end'])
            text = str(row.get('word') or row.get('text') or '').strip()
            if (not text or not math.isfinite(start) or not math.isfinite(end)
                    or start < 0 or start < previous - .025 or start >= duration
                    or end <= start or end > duration + .25):
                return []
            cleaned.append({'word': text, 'start': start, 'end': min(end, duration),
                            'index': index, 'timing_source': payload['source']})
            previous = start
        return cleaned
    except (OSError, ValueError, KeyError, TypeError):
        return []


def aligned_provider_words(audio_path, script, duration):
    words = load_speech_timing(audio_path, duration)
    if not words:
        return [], None
    tokens = re.findall(r'\S+', str(script or '').strip())
    expanded = []
    for row in words:
        pieces = row['word'].split()
        if len(pieces) == 1:
            expanded.append(row)
            continue
        # Some Edge events group a preposition and year ("in 2008"). Split
        # inside that measured phrase, rather than interpolating against
        # unrelated words elsewhere in the recording.
        weights = [max(1, len(piece)) * (2 if any(c.isdigit() for c in piece) else 1) for piece in pieces]
        cursor = row['start']
        for index, (piece, weight) in enumerate(zip(pieces, weights)):
            end = row['end'] if index == len(pieces) - 1 else cursor + (row['end'] - row['start']) * weight / sum(weights)
            expanded.append({**row, 'word': piece, 'start': cursor, 'end': end,
                             'timing_source': 'tts_phrase_boundary'})
            cursor = end
    words = [{**row, 'index': index} for index, row in enumerate(expanded)]
    normalize = lambda value: re.sub(r'[^\w]+', '', str(value).casefold(), flags=re.UNICODE)
    if len(tokens) == len(words) and all(normalize(token) == normalize(row['word']) for token, row in zip(tokens, words)):
        # Provider boundaries are already measured. ASR's smoothing and
        # end-of-file extension must not alter short words or trailing silence.
        return [{**row, 'word': token} for token, row in zip(tokens, words)], {
            'status': 'aligned', 'source': 'tts_word_boundary',
            'script_word_count': len(tokens), 'asr_word_count': len(words),
            'exact_match_ratio': 1.0, 'interpolated_word_count': 0,
            'phrase_split_word_count': sum(row['timing_source'] == 'tts_phrase_boundary' for row in words),
            'audio_sha256': audio_fingerprint(audio_path),
        }
    from .transcription_service import align_whisper_words_to_script
    aligned, audit = align_whisper_words_to_script(script, words, duration)
    if not aligned or audit.get('exact_match_ratio', 0) < .85:
        return [], audit
    for row in aligned:
        row['timing_source'] = 'interpolated_from_tts' if 'interpolated' in row.get('timing_source', '') else 'tts_word_boundary'
    audit.update(source='tts_word_boundary', audio_sha256=audio_fingerprint(audio_path))
    return aligned, audit
