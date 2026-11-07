# =============================================================================
# SCENE SPLITTER — Beat-Based Scene Splitting Using Whisper Timestamps
# =============================================================================
#
# Audio-First Architecture: TTS generates first, Whisper provides word-level
# timestamps, then this module splits the script into scenes aligned to
# natural speech boundaries (beats).
#
# A "beat" = one natural speech segment (sentence or clause), detected by
# finding sentence-ending punctuation (. ! ?) or long pauses in the audio.
#
# Scene pacing is fixed at one visual scene per narration beat. Legacy modes
# are accepted at the call boundary but canonicalized to this contract.
# =============================================================================

import math
import os
import re
import hashlib
import json
import threading
from dataclasses import dataclass, asdict
from typing import List, Optional


# Minimum gap between words to consider a natural pause
PAUSE_THRESHOLD = 0.25  # 250ms
MAX_SCENE_BEAT_SECONDS = max(
    3.0,
    float(os.environ.get("VIDZEO_MAX_SCENE_BEAT_SECONDS", "4.5") or 4.5),
)
_ABBREVIATION_MARKER = "\uE000"


def _is_abbreviation_period(words: list, index: int) -> bool:
    """Protect dotted initials that Whisper emits as separate word tokens."""
    if index < 0 or index >= len(words):
        return False
    def initial_text(row):
        return str(row.get("word", "") or "").strip().strip('"\'“”‘’\ufffd,;:')
    current = initial_text(words[index])
    if re.fullmatch(r"(?:[A-Za-z]\.){2,}", current):
        return True
    if not re.fullmatch(r"[A-Za-z]\.", current):
        return False
    initial = lambda row: bool(re.fullmatch(r"[A-Za-z]\.", initial_text(row)))
    return (
        (index > 0 and initial(words[index - 1]))
        or (index + 1 < len(words) and initial(words[index + 1]))
    )


@dataclass
class Beat:
    """A single speech beat (sentence/clause) with precise timestamps."""
    index: int
    start_time: float
    end_time: float
    duration: float
    text: str
    word_count: int


@dataclass
class SceneBoundary:
    """A scene boundary with exact timing from audio."""
    index: int
    start_time: float
    end_time: float
    duration: float
    text: str
    word_count: int
    beat_count: int  # How many beats this scene spans


def detect_beats(whisper_words: list) -> List[Beat]:
    """
    Detect natural speech beats from Whisper word-level timestamps.
    
    A beat boundary occurs at:
      1. Sentence-ending punctuation (. ! ?)
      2. Long pauses between words (> PAUSE_THRESHOLD)
      3. Semicolons and colons (secondary boundaries)
    
    Args:
        whisper_words: List of dicts with 'word', 'start', 'end' keys
    
    Returns:
        List of Beat objects with precise start/end timestamps
    """
    if not whisper_words:
        return []
    
    beats = []
    current_words = []
    current_text_parts = []
    beat_start = whisper_words[0].get('start', 0.0)
    
    for i, word_data in enumerate(whisper_words):
        word = word_data.get('word', '').strip()
        start = word_data.get('start', 0.0)
        end = word_data.get('end', 0.0)
        
        if not word:
            continue
        
        current_words.append(word_data)
        current_text_parts.append(word)
        
        # Check if this word ends a beat
        is_abbreviation = _is_abbreviation_period(whisper_words, i)
        is_sentence_end = bool(re.search(r'[.!?]$', word)) and not is_abbreviation
        is_clause_end = bool(re.search(r'[,;:—–-]$', word))
        
        # Check for a long pause after this word
        has_long_pause = False
        pause_gap = 0.0
        if i + 1 < len(whisper_words):
            next_start = whisper_words[i + 1].get('start', end)
            gap = next_start - end
            pause_gap = gap
            has_long_pause = gap > PAUSE_THRESHOLD
        
        is_last_word = (i == len(whisper_words) - 1)
        
        # Protect compound numbers: never split between a number and its scale word
        _SCALE_WORDS = {'million', 'millions', 'billion', 'billions', 'trillion', 'trillions', 'thousand', 'thousands', 'hundred', 'hundreds', 'percent', 'percentage'}
        is_compound_number_prefix = False
        if i + 1 < len(whisper_words):
            next_word_raw = str(whisper_words[i + 1].get('word', '')).strip()
            next_word_clean = re.sub(r'[^a-zA-Z]', '', next_word_raw).lower()
            current_word_clean = re.sub(r'[^0-9]', '', word)
            if next_word_clean in _SCALE_WORDS and (current_word_clean or re.search(r'\d', word)):
                is_compound_number_prefix = True

        # Beat boundary conditions: require >= 8 words and pause > 0.45s for clause ends to prevent fragmented half-sentences
        date_boundary = _is_narrated_date_boundary(whisper_words, i)
        clause_split = is_clause_end and len(current_words) >= 8 and (pause_gap > 0.45 or has_long_pause)
        pause_split = has_long_pause and len(current_words) >= 8
        if is_last_word or (not is_abbreviation and (is_sentence_end or clause_split or pause_split) and not is_compound_number_prefix and not date_boundary):
            cleaned_text = ' '.join(current_text_parts).rstrip(' ,;:—–-')
            beat = Beat(
                index=len(beats),
                start_time=beat_start,
                end_time=end,
                duration=round(end - beat_start, 3),
                text=cleaned_text,
                word_count=len(current_words)
            )
            beats.append(beat)
            
            # Reset for next beat
            current_words = []
            current_text_parts = []
            if i + 1 < len(whisper_words):
                beat_start = whisper_words[i + 1].get('start', end)
    
    # Handle any remaining words that didn't form a beat
    if current_words:
        if beats:
            last_beat = beats[-1]
            extra_text = ' '.join(current_text_parts).rstrip(' ,;:—–-')
            new_text = (last_beat.text + " " + extra_text).rstrip(' ,;:—–-') if extra_text else last_beat.text
            last_end = whisper_words[-1].get('end', last_beat.end_time)
            beats[-1] = Beat(
                index=last_beat.index,
                start_time=last_beat.start_time,
                end_time=last_end,
                duration=round(last_end - last_beat.start_time, 3),
                text=new_text,
                word_count=last_beat.word_count + len(current_words)
            )
        else:
            beats.append(Beat(
                index=0,
                start_time=whisper_words[0].get('start', 0.0),
                end_time=whisper_words[-1].get('end', 0.0),
                duration=round(whisper_words[-1].get('end', 0.0) - whisper_words[0].get('start', 0.0), 3),
                text=' '.join(current_text_parts).rstrip(' ,;:—–-'),
                word_count=len(current_words)
            ))
    
    return beats


def split_scenes(
    whisper_words: list,
    script: str,
    scene_mode: str,
    audio_duration: float
) -> List[SceneBoundary]:
    """
    Split script into scenes using actual audio timestamps from Whisper.
    
    This is the core function of the audio-first architecture. It guarantees:
    - Scene boundaries align to word/sentence boundaries (never mid-word)
    - Total scene durations sum to exactly audio_duration
    - No gaps or overlaps between scenes
    - One scene per detected narration beat, including short beats
    
    Args:
        whisper_words: List of dicts with 'word', 'start', 'end' keys
        script: Original script text (used as fallback context)
        scene_mode: Legacy-compatible input; output always keeps one beat per scene.
        audio_duration: Total audio duration in seconds (ground truth)
    
    Returns:
        List of SceneBoundary objects with exact timing
    """
    # Old callers can still supply Every N Beats / Adaptive, but the generated
    # documentary timeline is always normalized to one beat per scene.
    scene_mode = "Every Beat"
    # Step 1: Detect beats from Whisper timestamps
    beats = detect_beats(whisper_words)
    
    if not beats:
        # Fallback: if Whisper failed or returned nothing, create a single scene
        print("   ⚠️ No beats detected — single scene fallback")
        return [SceneBoundary(
            index=0,
            start_time=0.0,
            end_time=audio_duration,
            duration=audio_duration,
            text=script,
            word_count=len(script.split()),
            beat_count=1
        )]
    
    print(f"   🎵 Detected {len(beats)} speech beats")
    
    # Step 2: Determine beats-per-scene from mode
    beats_per_scene = 1
    
    # Step 3: Group beats into scenes
    scenes = _group_beats_into_scenes(beats, beats_per_scene, audio_duration)
    
    print(f"   🎬 {len(scenes)} scenes from {len(beats)} beats (mode: {scene_mode})")
    
    # Step 4: Verify total duration matches audio
    total_scene_dur = sum(s.duration for s in scenes)
    drift_ms = abs(total_scene_dur - audio_duration) * 1000
    if drift_ms > 50:
        print(f"   ⚠️ Duration drift: {drift_ms:.0f}ms — correcting last scene")
        # Adjust last scene to absorb any drift
        scenes[-1] = SceneBoundary(
            index=scenes[-1].index,
            start_time=scenes[-1].start_time,
            end_time=audio_duration,
            duration=round(audio_duration - scenes[-1].start_time, 3),
            text=scenes[-1].text,
            word_count=scenes[-1].word_count,
            beat_count=scenes[-1].beat_count
        )

    aligned_texts = align_script_to_scene_texts(script, scenes)
    if aligned_texts:
        for idx, aligned_text in enumerate(aligned_texts):
            if idx >= len(scenes):
                break
            scenes[idx] = SceneBoundary(
                index=scenes[idx].index,
                start_time=scenes[idx].start_time,
                end_time=scenes[idx].end_time,
                duration=scenes[idx].duration,
                text=aligned_text,
                word_count=len(aligned_text.split()),
                beat_count=scenes[idx].beat_count,
            )
    
    return _rebalance_short_scenes(scenes, audio_duration, min_duration=1.25)

def calculate_scene_mode(beat_count: int, group_count: int, candidates_per_group: int = 5, requested_mode: str = "Every Beat") -> str:
    """Return the fixed one-beat cadence, independent of footage volume."""
    return "Every Beat"

def _parse_scene_mode(scene_mode: str) -> int:
    """Legacy-compatible parser; documentary pacing is always one beat."""
    return 1


def _group_beats_into_scenes(
    beats: List[Beat],
    beats_per_scene: int,
    audio_duration: float
) -> List[SceneBoundary]:
    """
    Emit exactly one scene for each narration beat.
    
    Guarantees:
    - No gaps between scenes
    - Last scene extends to exactly audio_duration
    - Short beats are never merged into adjacent scenes
    """
    scenes = []
    total_beats = len(beats)
    
    beats_per_scene = 1
    i = 0
    scene_idx = 0
    
    while i < total_beats:
        # Grab the next chunk of beats
        chunk_end = min(i + beats_per_scene, total_beats)
        chunk = beats[i:chunk_end]
        
        if not chunk:
            break
        
        start_time = chunk[0].start_time
        
        # End time: use next scene's start if available, else audio_duration
        if chunk_end < total_beats:
            end_time = beats[chunk_end].start_time
        else:
            end_time = audio_duration
        
        # Combine text from all beats in this scene
        combined_text = ' '.join(b.text.rstrip(' ,;:—–-') for b in chunk).rstrip(' ,;:—–-')
        combined_words = sum(b.word_count for b in chunk)
        
        duration = round(end_time - start_time, 3)
        
        # Keep even very short beats separate. Exact narration timing takes
        # priority over a minimum visual hold; Remotion can cut on the beat.
        scenes.append(SceneBoundary(
            index=scene_idx,
            start_time=start_time,
            end_time=end_time,
            duration=max(0.01, duration),
            text=combined_text,
            word_count=combined_words,
            beat_count=len(chunk)
        ))
        scene_idx += 1
        
        i = chunk_end
    
    # Re-index after potential merges
    for idx, scene in enumerate(scenes):
        scene.index = idx
    
    return scenes


def get_scene_durations(scenes: List[SceneBoundary]) -> list:
    """Extract duration list from scene boundaries (for video_service)."""
    return [s.duration for s in scenes]


def get_scene_texts(scenes: List[SceneBoundary]) -> list:
    """Extract text list from scene boundaries (for prompt generation)."""
    return [s.text for s in scenes]


def _rebalance_short_scenes(
    scenes: List[SceneBoundary],
    audio_duration: float,
    min_duration: float = 0.75,
) -> List[SceneBoundary]:
    if len(scenes) <= 1:
        return scenes

    working = list(scenes)
    while len(working) > 1:
        short_index = next((idx for idx, scene in enumerate(working) if scene.duration < min_duration), None)
        if short_index is None:
            break

        if short_index == 0:
            current = working[0]
            nxt = working[1]
            merged = SceneBoundary(
                index=0,
                start_time=current.start_time,
                end_time=nxt.end_time,
                duration=round(max(0.3, nxt.end_time - current.start_time), 3),
                text=(current.text.rstrip(' ,;:—–-') + " " + nxt.text.rstrip(' ,;:—–-')).strip(),
                word_count=current.word_count + nxt.word_count,
                beat_count=current.beat_count + nxt.beat_count,
            )
            working = [merged] + working[2:]
            continue

        previous = working[short_index - 1]
        current = working[short_index]
        merged = SceneBoundary(
            index=previous.index,
            start_time=previous.start_time,
            end_time=current.end_time,
            duration=round(max(0.3, current.end_time - previous.start_time), 3),
            text=(previous.text.rstrip(' ,;:—–-') + " " + current.text.rstrip(' ,;:—–-')).strip(),
            word_count=previous.word_count + current.word_count,
            beat_count=previous.beat_count + current.beat_count,
        )
        working = working[:short_index - 1] + [merged] + working[short_index + 1:]

    rebalanced = []
    for idx, scene in enumerate(working):
        start_time = 0.0 if idx == 0 else rebalanced[-1].end_time
        end_time = float(audio_duration) if idx == len(working) - 1 else scene.end_time
        rebalanced.append(
            SceneBoundary(
                index=idx,
                start_time=round(start_time, 3),
                end_time=round(end_time, 3),
                duration=round(max(0.3, end_time - start_time), 3),
                text=scene.text,
                word_count=scene.word_count,
                beat_count=scene.beat_count,
            )
        )
    return rebalanced


def _finalize_scene_timeline(scenes: List[SceneBoundary], audio_duration: float) -> List[SceneBoundary]:
    """Make the scene list an exact, contiguous audio timeline."""
    if not scenes:
        return scenes
    total = max(0.01, float(audio_duration or 0.01))
    finalized: List[SceneBoundary] = []
    cursor = 0.0
    for idx, scene in enumerate(scenes):
        start = cursor
        if idx == len(scenes) - 1:
            end = total
        else:
            try:
                requested_end = float(scene.end_time)
            except (TypeError, ValueError):
                requested_end = start + max(0.3, float(scene.duration or 0.3))
            end = max(start + 0.05, min(total, requested_end))
        if idx == len(scenes) - 1:
            end = max(start + 0.05, total)
        finalized.append(SceneBoundary(
            index=idx,
            start_time=round(start, 3),
            end_time=round(end, 3),
            duration=round(max(0.05, end - start), 3),
            text=scene.text,
            word_count=max(0, scene.word_count),
            beat_count=max(1, scene.beat_count),
        ))
        cursor = end
    # Preserve the exact duration after rounding by correcting the last row.
    if finalized:
        last = finalized[-1]
        finalized[-1] = SceneBoundary(
            index=last.index,
            start_time=last.start_time,
            end_time=round(total, 3),
            duration=round(max(0.05, total - last.start_time), 3),
            text=last.text,
            word_count=last.word_count,
            beat_count=last.beat_count,
        )
    return finalized


def _split_text_for_scene_parts(text: str, part_count: int) -> List[str]:
    if part_count <= 1:
        return [(text or "").strip()]

    units = [unit.strip() for unit in _split_script_visual_units(text or "") if unit.strip()]
    while units and len(units) < part_count:
        longest_index = max(range(len(units)), key=lambda idx: len(units[idx].split()))
        split_units = _split_long_visual_unit(units[longest_index])
        if len(split_units) < 2:
            break
        units = units[:longest_index] + split_units + units[longest_index + 1:]

    if len(units) >= part_count:
        grouped: List[str] = []
        cursor = 0
        for idx in range(part_count):
            slots_left = part_count - idx
            take = max(1, math.ceil((len(units) - cursor) / slots_left))
            grouped.append(" ".join(units[cursor:cursor + take]).strip())
            cursor += take
        return grouped[:part_count]

    words = (text or "").split()
    if not words:
        return [""] * part_count
    # Merge compound number tokens so they are never split across parts
    _SCALE_WORDS_SET = {'million', 'millions', 'billion', 'billions', 'trillion', 'trillions', 'thousand', 'thousands', 'hundred', 'hundreds', 'percent', 'percentage'}
    merged_words = []
    skip_next = False
    for wi, w in enumerate(words):
        if skip_next:
            skip_next = False
            continue
        if wi + 1 < len(words) and re.sub(r'[^a-zA-Z]', '', words[wi + 1]).lower() in _SCALE_WORDS_SET and re.search(r'\d', w):
            merged_words.append(w + ' ' + words[wi + 1])
            skip_next = True
        else:
            merged_words.append(w)
    # Never duplicate the tail to satisfy a requested visual density. If the
    # narration has fewer atomic word groups than requested scenes, return only
    # the real groups; the caller will create fewer scenes rather than invent
    # repeated narration.
    part_count = max(1, min(int(part_count), len(merged_words)))
    base_size, extra = divmod(len(merged_words), part_count)
    parts = []
    cursor = 0
    for index in range(part_count):
        size = base_size + (1 if index < extra else 0)
        parts.append(" ".join(merged_words[cursor:cursor + size]).strip())
        cursor += size
    return parts


def _split_long_scenes(
    scenes: List[SceneBoundary],
    audio_duration: float,
    max_duration: float = MAX_SCENE_BEAT_SECONDS,
) -> List[SceneBoundary]:
    if not scenes or max_duration <= 0:
        return scenes

    split_scenes: List[SceneBoundary] = []
    for scene in scenes:
        duration = max(0.3, float(scene.duration or 0.0))
        part_count = max(1, int(math.ceil(duration / max_duration)))
        if part_count <= 1:
            split_scenes.append(scene)
            continue

        word_count = len((scene.text or "").split())
        part_count = min(part_count, max(1, word_count))
        text_parts = _split_text_for_scene_parts(scene.text, part_count)
        part_count = len(text_parts)
        if part_count <= 1:
            split_scenes.append(scene)
            continue
        part_duration = duration / part_count
        for part_index in range(part_count):
            start_time = scene.start_time + (part_duration * part_index)
            end_time = scene.end_time if part_index == part_count - 1 else scene.start_time + (part_duration * (part_index + 1))
            text_value = text_parts[part_index] if part_index < len(text_parts) else scene.text
            split_scenes.append(SceneBoundary(
                index=len(split_scenes),
                start_time=round(start_time, 3),
                end_time=round(end_time, 3),
                duration=round(max(0.3, end_time - start_time), 3),
                text=text_value,
                word_count=max(1, len((text_value or "").split())),
                beat_count=1,
            ))

    if len(split_scenes) == len(scenes):
        return scenes

    reindexed: List[SceneBoundary] = []
    for idx, scene in enumerate(split_scenes):
        start_time = 0.0 if idx == 0 else reindexed[-1].end_time
        end_time = float(audio_duration) if idx == len(split_scenes) - 1 else scene.end_time
        reindexed.append(SceneBoundary(
            index=idx,
            start_time=round(start_time, 3),
            end_time=round(end_time, 3),
            duration=round(max(0.3, end_time - start_time), 3),
            text=scene.text,
            word_count=scene.word_count,
            beat_count=scene.beat_count,
        ))
    return reindexed


def _scene_contract_cap(script: str, estimated_duration: float) -> int:
    """Keep media prefetch scene counts practical before Whisper timing lands."""
    words = re.findall(r"\b[\w'-]+\b", script or "")
    word_count = len(words)
    duration = max(1.0, float(estimated_duration or 0.0))
    env_cap = max(12, int(os.environ.get("VIDZEO_MAX_SCRIPT_FIRST_SCENES", "80") or 80))

    if word_count <= 260:
        return max(6, min(24, math.ceil(word_count / 11), math.ceil(duration / 4.5)))
    if word_count <= 700:
        return max(10, min(48, math.ceil(word_count / 14), math.ceil(duration / 4.75)))

    return max(14, min(env_cap, math.ceil(word_count / 18), math.ceil(duration / 5.0)))


def _merge_scenes_to_cap(scenes: List[SceneBoundary], cap: int, audio_duration: float) -> List[SceneBoundary]:
    if len(scenes) <= cap or cap <= 0:
        return scenes

    merged: List[SceneBoundary] = []
    total = len(scenes)
    for idx in range(cap):
        start_idx = math.floor(idx * total / cap)
        end_idx = math.floor((idx + 1) * total / cap)
        group = scenes[start_idx:max(start_idx + 1, end_idx)]
        first = group[0]
        last = group[-1]
        start_time = 0.0 if idx == 0 else merged[-1].end_time
        end_time = float(audio_duration) if idx == cap - 1 else last.end_time
        merged.append(SceneBoundary(
            index=idx,
            start_time=round(start_time, 3),
            end_time=round(end_time, 3),
            duration=round(max(0.3, end_time - start_time), 3),
            text=" ".join(s.text.strip() for s in group if s.text).strip(),
            word_count=sum(max(0, s.word_count) for s in group),
            beat_count=sum(max(1, s.beat_count) for s in group),
        ))

    print(f"   [flow] scene prefetch cap: merged {len(scenes)} script fragments into {len(merged)} retrieval scenes")
    return merged


_LLM_SCENE_SPLIT_CACHE = {}
_SPLIT_CACHE_LOCK = threading.Lock()


def split_scenes_llm(script: str, estimated_duration: float, api_key: str, model: str) -> Optional[List[SceneBoundary]]:
    """Segment a full script into visual cinematic scenes/moments using OpenRouter."""
    try:
        from services.openrouter_llm import OpenRouterLLM, DEFAULT_OPENROUTER_MODEL
    except ImportError:
        from server.services.openrouter_llm import OpenRouterLLM, DEFAULT_OPENROUTER_MODEL
    
    llm = OpenRouterLLM(api_key=api_key, model=model or DEFAULT_OPENROUTER_MODEL)
    
    system_prompt = (
        "You are an expert cinematic documentary visual director.\n"
        "Convert the given script into consecutive visual scenes.\n"
        "Rules:\n"
        "- Group related sentences/phrases into visual scenes. DO NOT split sentence-by-sentence.\n"
        "- Each scene must represent a visually distinct and searchable concept.\n"
        "- The scenes must cover the entire script in chronological order without any gaps or overlaps.\n"
        "- Limit the output to between 3 and 15 scenes (target 5-10 scenes for typical scripts).\n"
        "- For each scene, output:\n"
        "  - scene_id (1-indexed)\n"
        "  - narration_text (the actual text from the script corresponding to this scene)\n"
        "  - visual_summary (a description of what appears on screen)\n"
        "  - scene_type (e.g., childhood, training, batting, celebration, captaincy, trophy, emotional, controversy, stadium, interview, fans, travel, luxury, retirement, general)\n"
        "  - emotional_tone (e.g., inspirational, dramatic, quiet, intense)\n"
        "- Output JSON format only: {\"scenes\": [{\"scene_id\": 1, \"narration_text\": \"...\", \"visual_summary\": \"...\", \"scene_type\": \"...\", \"emotional_tone\": \"...\"}]}"
    )
    
    user_prompt = f"Script to segment:\n\n{script}"
    
    try:
        response = llm.chat(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            max_tokens=3000,
            json_mode=True
        )
        data = json.loads(response)
        scenes_data = data.get("scenes", [])
        if not scenes_data:
            return None
            
        # The scene director may choose where to cut, but it must not rewrite,
        # omit, or repeat narration. Rebuild every accepted segment from the
        # source script so punctuation and exact wording stay authoritative.
        source_tokens = str(script or "").split()

        def _norm_token(value: str) -> str:
            return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

        returned_tokens = [
            token
            for scene in scenes_data
            for token in str(scene.get("narration_text") or "").split()
        ]
        if [_norm_token(token) for token in returned_tokens if _norm_token(token)] != [
            _norm_token(token) for token in source_tokens if _norm_token(token)
        ]:
            print("   [LLM Split] Rejected scene text that changed or lost source-script words")
            return None

        cursor = 0
        exact_scene_rows = []
        for scene in scenes_data:
            scene_tokens = str(scene.get("narration_text") or "").split()
            if not scene_tokens:
                return None
            count = len(scene_tokens)
            exact_scene_rows.append({**scene, "narration_text": " ".join(source_tokens[cursor:cursor + count])})
            cursor += count
        if cursor != len(source_tokens):
            return None
        scenes_data = exact_scene_rows

        word_counts = [len(s.get("narration_text", "").split()) for s in scenes_data]
        total_words = sum(word_counts)
        if total_words == 0:
            return None
            
        scenes = []
        current_time = 0.0
        for idx, s in enumerate(scenes_data):
            wc = len(s.get("narration_text", "").split())
            proportion = wc / total_words
            dur = max(0.5, round(proportion * estimated_duration, 3))
            end_time = current_time + dur
            if idx == len(scenes_data) - 1:
                end_time = estimated_duration
                dur = round(end_time - current_time, 3)
                
            scenes.append(SceneBoundary(
                index=idx,
                start_time=round(current_time, 3),
                end_time=round(end_time, 3),
                duration=dur,
                text=s.get("narration_text", ""),
                word_count=max(1, wc),
                beat_count=1
            ))
            current_time = end_time
        return scenes
    except Exception as e:
        print(f"   [LLM Split] Error segmenting script: {e}")
        return None


def build_expected_scenes(
    script: str,
    scene_mode: str,
    estimated_duration: float,
    api_key: str = "",
    llm_model: str = "qwen/qwen3.5-flash-02-23"
) -> List[SceneBoundary]:
    """
    Predict one scene per narration beat directly from the written script.

    This is the fast prefetch path used to start prompt/query/media work
    immediately from punctuation, clauses, and sentence endings. ``api_key``
    and ``llm_model`` remain accepted for older callers; scene grouping is
    deterministic so it cannot combine multiple beats.
    """
    scenes = fallback_split_by_text(script, "Every Beat", estimated_duration, emit_log=False)
    scenes = _rebalance_short_scenes(scenes, estimated_duration, min_duration=1.25)
    return _finalize_scene_timeline(scenes, estimated_duration)


def align_expected_scenes_to_transcript(
    script: str,
    scene_mode: str,
    whisper_words: list,
    audio_duration: float,
) -> List[SceneBoundary]:
    """
    Keep the precomputed script-first scene groups, but replace their rough
    timings with transcription-backed timings once Whisper is ready.
    """
    expected_scenes = build_expected_scenes(script, scene_mode, audio_duration)
    valid_words = [
        word for word in (whisper_words or [])
        if str(word.get('word', '')).strip()
    ]
    if not expected_scenes or not valid_words:
        return expected_scenes

    target_word_counts = [max(1, len(scene.text.split())) for scene in expected_scenes]
    total_target_words = max(1, sum(target_word_counts))
    total_words = len(valid_words)
    boundary_candidates = _collect_transcript_boundary_candidates(valid_words)

    scenes: List[SceneBoundary] = []
    cursor = 0
    remaining_target_words = total_target_words

    for index, expected_scene in enumerate(expected_scenes):
        remaining_scenes = len(expected_scenes) - index
        remaining_words = total_words - cursor
        if remaining_words <= 0:
            break

        if index == len(expected_scenes) - 1:
            end_idx = total_words
        else:
            desired_ratio = target_word_counts[index] / max(1, remaining_target_words)
            ideal_take = round(remaining_words * desired_ratio)
            min_take = 1
            max_take = max(1, remaining_words - (remaining_scenes - 1))
            ideal_take = max(min_take, min(max_take, ideal_take))
            tentative_end = cursor + ideal_take
            end_idx = _snap_boundary_index(
                tentative_end,
                boundary_candidates,
                min_idx=cursor + 1,
                max_idx=cursor + max_take,
            )

        start_time = 0.0 if cursor <= 0 else float(valid_words[cursor].get('start', 0.0) or 0.0)
        if end_idx >= total_words:
            end_time = float(audio_duration)
        else:
            end_time = float(valid_words[end_idx].get('start', valid_words[end_idx - 1].get('end', audio_duration)) or audio_duration)

        if end_time <= start_time:
            fallback_end = float(valid_words[min(total_words - 1, max(cursor, end_idx - 1))].get('end', audio_duration) or audio_duration)
            end_time = max(start_time + 0.01, fallback_end)

        scenes.append(SceneBoundary(
            index=index,
            start_time=round(start_time, 3),
            end_time=round(end_time, 3),
            duration=round(max(0.3, end_time - start_time), 3),
            text=expected_scene.text,
            word_count=max(1, end_idx - cursor),
            beat_count=max(1, expected_scene.beat_count),
        ))

        cursor = end_idx
        remaining_target_words -= target_word_counts[index]

    if not scenes:
        return expected_scenes

    final_scene = scenes[-1]
    scenes[-1] = SceneBoundary(
        index=final_scene.index,
        start_time=final_scene.start_time,
        end_time=round(float(audio_duration), 3),
        duration=round(max(0.3, float(audio_duration) - final_scene.start_time), 3),
        text=final_scene.text,
        word_count=final_scene.word_count,
        beat_count=final_scene.beat_count,
    )
    scenes = _finalize_scene_timeline(scenes, audio_duration)
    scenes = _realign_scene_bounds_to_script_words(scenes, valid_words, audio_duration)
    scenes = _rebalance_short_scenes(scenes, audio_duration, min_duration=1.25)
    return _finalize_scene_timeline(scenes, audio_duration)


def _realign_scene_bounds_to_script_words(
    scenes: List[SceneBoundary],
    whisper_words: List[dict],
    audio_duration: float,
) -> List[SceneBoundary]:
    """Snap split scenes to exact script-word boundaries when word order matches.

    The split text is the source of truth for which words belong to a scene.
    Recomputing timestamps from those exact token ranges avoids proportional
    scene-duration splits drifting into the middle of a spoken phrase.
    """
    if not scenes or not whisper_words:
        return scenes

    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

    scene_tokens = [
        [token for token in re.findall(r"\S+", str(scene.text or "")) if normalize(token)]
        for scene in scenes
    ]
    word_tokens = [
        normalize(word.get("word") or word.get("text") or "")
        for word in whisper_words
        if normalize(word.get("word") or word.get("text") or "")
    ]
    flattened_scene_tokens = [normalize(token) for group in scene_tokens for token in group]
    if not flattened_scene_tokens or flattened_scene_tokens != word_tokens:
        return scenes

    duration = max(0.05, float(audio_duration or 0.05))
    aligned: List[SceneBoundary] = []
    cursor = 0
    for index, scene in enumerate(scenes):
        count = len(scene_tokens[index])
        if count <= 0:
            return scenes
        end_index = cursor + count
        if end_index > len(whisper_words):
            return scenes
        start = 0.0 if cursor == 0 else float(whisper_words[cursor].get("start", 0.0) or 0.0)
        if end_index >= len(whisper_words):
            end = duration
        else:
            end = float(whisper_words[end_index].get("start", whisper_words[end_index - 1].get("end", duration)) or duration)
        if end <= start:
            end = max(start + 0.03, float(whisper_words[end_index - 1].get("end", duration) or duration))
        end = min(duration, end)
        aligned.append(SceneBoundary(
            index=index,
            start_time=round(start, 3),
            end_time=round(end, 3),
            duration=round(max(0.03, end - start), 3),
            text=scene.text,
            word_count=count,
            beat_count=scene.beat_count,
        ))
        cursor = end_index

    if cursor != len(whisper_words):
        return scenes
    return _finalize_scene_timeline(aligned, duration)


def _collect_transcript_boundary_candidates(whisper_words: List[dict]) -> List[int]:
    candidates = {len(whisper_words)}
    for index, word_data in enumerate(whisper_words[:-1]):
        word = str(word_data.get('word', '')).strip()
        end = float(word_data.get('end', 0.0) or 0.0)
        next_start = float(whisper_words[index + 1].get('start', end) or end)
        has_pause = (next_start - end) > PAUSE_THRESHOLD
        is_abbreviation = _is_abbreviation_period(whisper_words, index)
        is_punctuation = bool(re.search(r'[.!?,;:—–-]$', word)) and not is_abbreviation
        if not is_abbreviation and (has_pause or is_punctuation):
            candidates.add(index + 1)
    return sorted(candidate for candidate in candidates if candidate > 0)


def _snap_boundary_index(target_idx: int, candidates: List[int], min_idx: int, max_idx: int) -> int:
    bounded_target = max(min_idx, min(max_idx, target_idx))
    eligible = [candidate for candidate in candidates if min_idx <= candidate <= max_idx]
    if not eligible:
        return bounded_target

    nearby = [candidate for candidate in eligible if abs(candidate - bounded_target) <= 4]
    search_space = nearby or eligible
    return min(search_space, key=lambda candidate: (abs(candidate - bounded_target), candidate))


_MONTH_PATTERN = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_DATE_COMMA_MARKER = '\ue001'


def _is_narrated_date_boundary(words: list, index: int) -> bool:
    if index >= len(words) - 1:
        return False
    first = max(0, index - 2)
    tokens = [str(row.get('word') or '').strip() for row in words[first:index + 3]]
    context = ' '.join(tokens)
    boundary = len(' '.join(tokens[:index - first + 1]))
    for match in re.finditer(r"\b" + _MONTH_PATTERN + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?\s*,\s*\d{4},?", context, re.I):
        if match.start() < boundary <= match.end():
            return True
    return False


def _protect_date_punctuation(text: str) -> str:
    """A calendar date is one fact; commas inside it are not visual beats."""
    return re.sub(r"\b" + _MONTH_PATTERN + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?\s*,\s*\d{4},?",
                  lambda match: match.group(0).replace(',', _DATE_COMMA_MARKER), text, flags=re.I)


def _split_script_visual_units(script: str) -> List[str]:
    script = (script or "").strip()
    if not script:
        return []

    # Protect dotted initialisms before punctuation splitting. Restore them
    # after the sentence and clause boundaries have been found.
    protected_script = re.sub(
        r"\b(?:[A-Za-z]\.\s*){2,}",
        lambda match: match.group(0).replace(".", _ABBREVIATION_MARKER),
        script,
    )
    protected_script = _protect_date_punctuation(protected_script)
    sentence_parts = re.split(r'(?<=[.!?])\s+', protected_script)
    units: List[str] = []
    for sentence in sentence_parts:
        sentence = sentence.strip()
        if not sentence:
            continue
        clauses = re.split(r'(?<=[,;:—–-])\s+', sentence)
        sentence_units: List[str] = []
        accumulated: List[str] = []
        for clause in clauses:
            cleaned = clause.strip(" \t\r\n,;:—–-")
            if not cleaned:
                continue
            accumulated.append(cleaned)
            # Require at least 8 words in accumulated clause before splitting into separate visual units,
            # avoiding short 1-3 word fragmented half-sentences ("In 1985,", "However,")
            if len(" ".join(accumulated).split()) >= 8:
                sentence_units.append(" ".join(accumulated).replace(_ABBREVIATION_MARKER, ".").replace(_DATE_COMMA_MARKER, ','))
                accumulated = []
        if accumulated:
            tail = " ".join(accumulated).replace(_ABBREVIATION_MARKER, ".").replace(_DATE_COMMA_MARKER, ',')
            if sentence_units and len(tail.split()) < 5:
                sentence_units[-1] = (sentence_units[-1] + " " + tail).strip()
            else:
                sentence_units.append(tail)
        units.extend(sentence_units)
    return units


def _split_long_visual_unit(unit: str) -> List[str]:
    cleaned = (unit or "").strip()
    if not cleaned:
        return []

    words = cleaned.split()
    if len(words) < 10:
        return [cleaned]

    _SCALE_WORDS = {'million', 'millions', 'billion', 'billions', 'trillion', 'trillions', 'thousand', 'thousands', 'hundred', 'hundreds', 'percent', 'percentage'}

    punctuation_parts = re.split(r'(?<=[,;:—–-])\s+', cleaned, maxsplit=1)
    if len(punctuation_parts) == 2:
        left = punctuation_parts[0].strip(" \t\r\n,;:-")
        right = punctuation_parts[1].strip(" \t\r\n,;:-")
        left_words = left.split()
        right_words = right.split()
        if left_words and right_words:
            if re.sub(r'[^a-zA-Z]', '', right_words[0]).lower() in _SCALE_WORDS and re.search(r'\d', left_words[-1]):
                pass  # Do not split between number and scale word!
            elif left and right:
                return [left, right]

    for marker in (" and ", " but ", " while ", " because ", " which ", " that "):
        lowered = cleaned.lower()
        marker_index = lowered.find(marker)
        if marker_index > 20:
            left = cleaned[:marker_index].strip(" \t\r\n,;:-")
            right = cleaned[marker_index + 1:].strip(" \t\r\n,;:-")
            left_words = left.split()
            right_words = right.split()
            if left_words and right_words and re.sub(r'[^a-zA-Z]', '', right_words[0]).lower() in _SCALE_WORDS and re.search(r'\d', left_words[-1]):
                continue
            if left and right:
                return [left, right]

    midpoint = max(1, len(words) // 2)
    # Never split between a number and a scale word
    if midpoint < len(words):
        prev_w = words[midpoint - 1]
        curr_w = re.sub(r'[^a-zA-Z]', '', words[midpoint]).lower()
        if curr_w in _SCALE_WORDS and re.search(r'\d', prev_w):
            midpoint = min(len(words) - 1, midpoint + 1)

    return [
        " ".join(words[:midpoint]).strip(),
        " ".join(words[midpoint:]).strip(),
    ]


def align_script_to_scene_texts(script: str, scenes: List[SceneBoundary]) -> List[str]:
    if not scenes:
        return []

    script = (script or "").strip()
    if not script:
        return [scene.text for scene in scenes]

    units = _split_script_visual_units(script)
    if not units:
        return [scene.text for scene in scenes]

    target_count = len(scenes)
    while len(units) < target_count:
        longest_index = max(range(len(units)), key=lambda idx: len(units[idx].split()))
        split_units = _split_long_visual_unit(units[longest_index])
        if len(split_units) < 2:
            break
        units = units[:longest_index] + split_units + units[longest_index + 1:]

    _SCALE_WORDS = {'million', 'millions', 'billion', 'billions', 'trillion', 'trillions', 'thousand', 'thousands', 'hundred', 'hundreds', 'percent', 'percentage'}

    if len(units) < target_count:
        all_words = script.split()
        if not all_words:
            return [scene.text for scene in scenes]
        # Merge compound words so chunking never splits them
        merged_all_words = []
        skip_next = False
        for wi, w in enumerate(all_words):
            if skip_next:
                skip_next = False
                continue
            if wi + 1 < len(all_words) and re.sub(r'[^a-zA-Z]', '', all_words[wi + 1]).lower() in _SCALE_WORDS and re.search(r'\d', w):
                merged_all_words.append(w + ' ' + all_words[wi + 1])
                skip_next = True
            else:
                merged_all_words.append(w)

        chunk_size = max(4, len(merged_all_words) // target_count)
        units = []
        for start in range(0, len(merged_all_words), chunk_size):
            units.append(" ".join(merged_all_words[start:start + chunk_size]).strip())
        if units:
            units[-1] = " ".join(merged_all_words[(len(units) - 1) * chunk_size:]).strip()

    total_duration = max(0.001, sum(max(scene.duration, 0.001) for scene in scenes))
    grouped_texts: List[str] = []
    cursor = 0

    for idx, scene in enumerate(scenes):
        slots_left = target_count - idx
        remaining_units = len(units) - cursor
        if slots_left <= 1 or remaining_units <= 1:
            grouped_texts.append(" ".join(units[cursor:]).strip().rstrip(' ,;:—–-'))
            cursor = len(units)
            continue

        target_share = max(scene.duration, 0.001) / total_duration
        target_words = max(4, round(len(script.split()) * target_share))
        current_units: List[str] = []
        current_words = 0

        while cursor < len(units):
            units_left_after = len(units) - (cursor + 1)
            min_units_needed = slots_left - 1
            if units_left_after < min_units_needed:
                break

            next_unit = units[cursor]
            next_word_count = len(next_unit.split())
            current_units.append(next_unit)
            current_words += next_word_count
            cursor += 1

            if current_words >= target_words:
                break

        if not current_units and cursor < len(units):
            current_units.append(units[cursor])
            cursor += 1

        grouped_texts.append(" ".join(current_units).strip().rstrip(' ,;:—–-'))

    while len(grouped_texts) < target_count:
        grouped_texts.append(grouped_texts[-1] if grouped_texts else script)

    return grouped_texts[:target_count]


def scenes_to_dicts(scenes: List[SceneBoundary]) -> list:
    """Convert scene boundaries to serializable dicts."""
    return [asdict(s) for s in scenes]


def fallback_split_by_text(
    script: str,
    scene_mode: str,
    estimated_duration: float,
    emit_log: bool = True,
) -> List[SceneBoundary]:
    """
    Fallback scene splitting when Whisper is unavailable.
    Uses punctuation-aware text splitting and estimates durations
    proportionally by word count.

    This is the graceful degradation path used only when Whisper fails.
    """
    beats_per_scene = _parse_scene_mode(scene_mode)
    units = [s.strip() for s in _split_script_visual_units(script) if s.strip()]

    if not units:
        return [SceneBoundary(
            index=0, start_time=0.0, end_time=estimated_duration,
            duration=estimated_duration, text=script,
            word_count=len(script.split()), beat_count=1
        )]

    groups = []
    for i in range(0, len(units), beats_per_scene):
        group = units[i:i + beats_per_scene]
        groups.append(' '.join(group))

    word_counts = [len(g.split()) for g in groups]
    total_words = sum(word_counts)
    if total_words == 0:
        word_counts = [1] * len(groups)
        total_words = len(groups)

    scenes = []
    current_time = 0.0
    for idx, (text_value, wc) in enumerate(zip(groups, word_counts)):
        proportion = wc / total_words
        dur = max(0.3, round(proportion * estimated_duration, 3))
        end_time = current_time + dur
        if idx == len(groups) - 1:
            end_time = estimated_duration
            dur = round(end_time - current_time, 3)
        scenes.append(SceneBoundary(
            index=idx,
            start_time=round(current_time, 3),
            end_time=round(end_time, 3),
            duration=dur,
            text=text_value,
            word_count=wc,
            beat_count=min(beats_per_scene, max(1, len(_split_script_visual_units(text_value))))
        ))
        current_time = end_time

    if emit_log:
        print(f"   Fallback text-split: {len(scenes)} scenes (no Whisper)")
    return scenes


def estimate_scene_count(script: str, scene_mode: str) -> int:
    """
    Estimate how many scenes/images to generate BEFORE TTS/Whisper.

    Returns:
        int: Number of images to generate.
             -1 = Granular Beats (Every Pause)
             -2 = Sentence-by-Sentence
    """
    mode_lower = scene_mode.lower().strip()

    if 'word by word' in mode_lower:
        word_count = len(script.split())
        return max(3, min(100, word_count // 4))
    if 'sentence by sentence' in mode_lower:
        return -2
    if 'scene-by-scene' in mode_lower or 'every beat' in mode_lower:
        return -1
    if 'hyper retention' in mode_lower:
        return 40
    if 'fast paced' in mode_lower:
        return 20
    if 'standard' in mode_lower:
        return 8

    beats_per_scene = _parse_scene_mode(scene_mode)
    sentences = _split_script_visual_units(script)
    num_beats = max(1, len([s for s in sentences if s.strip() and len(s.strip()) > 5]))

    if beats_per_scene > 1:
        return max(3, num_beats // beats_per_scene)

    return -1 if 'beat' in mode_lower else 8
