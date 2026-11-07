const clamp = (value) => Math.max(0, Math.min(1, value));

export function hasApprovedDirectorCopy(scene = {}) {
  const s = scene || {};
  const copy = s.editorial_contract || {};
  const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase();
  const narration = normalize(s.narration);
  const display = normalize(copy.display_text);
  const span = normalize(copy.source_span);
  const provenance = copy.source === 'llm' || (!copy.source && Number(copy.version) === 4
    && scene.media_plan?.decision_source === 'llm_director');
  return copy.approved === true && Number(copy.version) >= 4 && provenance
    && typeof copy.display_text === 'string' && Boolean(copy.display_text.trim())
    && typeof copy.source_span === 'string' && Boolean(copy.source_span.trim())
    && Boolean(narration) && narration.includes(span) && span.includes(display);
}

export function spokenPhraseFrame(scene, phrase, fps) {
  const byIndex = new Map();
  for (const cue of scene.word_cues || []) for (const word of cue.timed_words || []) {
    if (Number.isFinite(Number(word.index))) byIndex.set(Number(word.index), word);
  }
  const indexed = [...byIndex.values()].sort((a,b)=>Number(a.index)-Number(b.index));
  const captionWords = (Array.isArray(scene.caption_words) ? scene.caption_words : []).map((word, index) => ({
    index, text: word.word || word.text || '',
    start: Number(word.start) - (scene.caption_clock === 'scene' ? 0 : Number(scene.start || 0)),
  }));
  const sequences = [indexed, captionWords];
  const token = (value)=>String(value || '').toLowerCase().replace(/[^a-z0-9]/g,'');
  const target = String(phrase || '').split(/\s+/).map(token).filter(Boolean);
  if (!target.length) return null;
  for (const words of sequences) for (let i=0;i<=words.length-target.length;i++) {
    if (target.every((value,k)=>token(words[i+k].text)===value && Number(words[i+k].index)===Number(words[i].index)+k)) {
      const local = Number(words[i].start);
      const shifted = local - Number(scene.narration_frame_offset || 0) / fps;
      if (Number.isFinite(shifted) && shifted>=0 && shifted<Number(scene.duration || Infinity)) return Math.round(shifted*fps);
    }
  }
  return null;
}

/** Keep Remotion interpolation keyframes strictly increasing on very short scenes. */
export function safeFadeOutRange(durationFrames, requestedFrames = 8) {
  const end = Math.max(1, Math.round(Number(durationFrames) || 1));
  const fade = Math.max(1, Math.round(Number(requestedFrames) || 1));
  return [Math.max(0, end - Math.min(fade, end)), end];
}

export function safeRevealRange(durationFrames, requestedFrames = 8) {
  const end = Math.max(1, Math.round(Number(durationFrames) || 1));
  const reveal = Math.max(1, Math.round(Number(requestedFrames) || 1));
  return [0, Math.min(end, reveal)];
}

/** A three point entrance needs three distinct frame positions. */
export function safeStagedRevealRange(durationFrames, requestedFrames = 18) {
  const end = Math.max(1, Math.round(Number(durationFrames) || 1));
  if (end === 1) return [0, 1];
  const reveal = Math.max(1, Math.min(end - 1, Math.round(Number(requestedFrames) || 1)));
  return [0, reveal, end];
}

export function directorCue(scene = {}, element) {
  const cues = Array.isArray(scene.motion_cues) ? scene.motion_cues : [];
  return cues.find((cue) => cue?.element === element && Number.isFinite(Number(cue.time_local))) || null;
}

export function cueProgress(scene, element, frame, fps, fallbackStart = 0, fallbackDuration = 0.7) {
  const cue = directorCue(scene, element);
  const start = cueStartFrame(scene, element, fps, fallbackStart);
  const duration = Math.max(1, Number(cue?.duration_seconds ?? fallbackDuration) * fps);
  return clamp((frame - start) / duration);
}

export function cueStartFrame(scene, element, fps, fallbackStart = 0) {
  const cue = directorCue(scene, element);
  const anchor = cue?.anchor_text || cue?.trigger_phrase || cue?.narration_phrase;
  const narrated = anchor && cue?.preview_before_narration !== true ? spokenPhraseFrame(scene, anchor, fps) : null;
  const duration = Math.max(1, Number(scene.duration_in_frames || Number(scene.duration || 4) * fps));
  return Math.min(duration - 1, Math.max(0, narrated ?? Math.round(Number(cue?.time_local ?? fallbackStart) * fps)));
}
