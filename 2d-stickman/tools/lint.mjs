// Pre-render QA gate. `node tools/capture.mjs lint` boots the film, then runs these rules. ERRORS stop the render
// (render.sh refuses to continue); warnings are printed and should be read. Pure function: lint(data, files).
// The rules exist because a first build of a reel shipped with 3 long scenes, plain subtitle captions and no sound.
//   data  = OP.lintData()    files = { mixExists, mixDur, linesDur, soundFiles: Set of names, backgroundsDir }
import { MOTION_NAMES } from '../src/core/sound.js';

export function lint(d, files = {}) {
  const E = [], W = [], I = [];
  const P = d.profile, shots = d.shots, dur = d.dur, [tmin, tmax] = P.sceneTarget;
  const len = (s) => s.end - s.at;
  I.push(`profile "${P.name}" · ${P.format} ${d.size.join('x')} · ${dur.toFixed(1)} s · ${shots.length} scenes · avg ${(dur / shots.length).toFixed(1)} s/scene (target ${tmin}-${tmax} s)`);

  // ---- pacing: scene by scene ----
  const need = Math.ceil(dur / tmax);
  if (shots.length < need) E.push(`TOO FEW SCENES: ${shots.length} scenes for ${dur.toFixed(1)} s — a ${P.name} needs at least ${need} (a new scene every ≤ ${tmax} s). Split lines into shorter sentences or cut inside a line with B(i, frac).`);
  for (const s of shots) {
    if (len(s) > P.sceneMax) E.push(`scene ${s.id} lasts ${len(s).toFixed(1)} s — max ${P.sceneMax} s for a ${P.name}. Cut it into two scenes.`);
    else if (len(s) > tmax) W.push(`scene ${s.id} lasts ${len(s).toFixed(1)} s (target ≤ ${tmax} s).`);
  }
  const lineCount = d.lines.length;
  for (let i = 0; i < lineCount; i++) {
    const have = shots.filter((s) => s.line === i);
    if (!have.length) E.push(`line ${i} ("${d.lines[i].text.slice(0, 40)}…") has no scene — tag a shot with line: ${i} or give the line a "scene": {recipe} in script.json.`);
    const ld = d.lines[i].end - d.lines[i].start;
    if (have.length && ld > tmax * 1.25 && have.length < Math.ceil(ld / tmax)) W.push(`line ${i} is ${ld.toFixed(1)} s long but has ${have.length} scene(s) — cut inside it with B(${i}, frac) (aim for one scene per ≤ ${tmax} s).`);
  }
  if (dur > P.maxDur) W.push(`${dur.toFixed(0)} s is longer than a ${P.name} should be (${P.maxDur} s). Use profile "long" for longer videos.`);

  // ---- motion: every scene must move something inside the frame ----
  for (const s of shots) {
    if (!s.motion) E.push(`scene ${s.id} declares no motion — set motion: one of ${MOTION_NAMES.join(', ')} (what moves INSIDE the frame; the camera does not count).`);
    else if (!MOTION_NAMES.includes(s.motion)) W.push(`scene ${s.id}: unknown motion "${s.motion}" (auto-sound ignores it). Known: ${MOTION_NAMES.join(', ')}.`);
  }

  // ---- sound ----
  const mode = P.sound.mode || 'auto';
  if (mode === 'off') (P.name === 'long' || d.script.lang === 'ja' ? I : W).push('sound effects are OFF. Reels and shorts normally have them ON — only keep it off if the user asked for no sound effects.');
  else {
    const per = shots.map((s) => d.cues.filter((c) => c.t >= s.at - 0.05 && c.t < s.end).length);
    const silent = shots.filter((s, i) => per[i] === 0);
    if (silent.length > Math.max(0, shots.length * 0.2)) E.push(`${silent.length}/${shots.length} scenes have no sound cue (${silent.slice(0, 4).map((s) => s.id).join(', ')}…). Give each scene a motion, or add sound: {name, at}.`);
    shots.forEach((s, i) => { if (per[i] > 4) W.push(`scene ${s.id} has ${per[i]} sound cues — keep it to about 3 (cut transition + one event + one accent).`); });
    for (const c of d.cues) {
      if (c.t < 0 || c.t > dur) E.push(`sound cue "${c.name}" at ${c.t}s is outside the film (0-${dur.toFixed(1)} s).`);
      if (files.soundFiles && !files.soundFiles.has(c.name)) E.push(`sound cue "${c.name}" has no file sounds/${c.name}.wav — run tools/make_sounds.sh.`);
    }
    if (d.bed && files.soundFiles && !files.soundFiles.has(d.bed.name)) E.push(`ambience bed sounds/${d.bed.name}.wav is missing — run tools/make_sounds.sh.`);
    I.push(`${d.cues.length} sound cues${d.bed ? ' + ' + d.bed.name : ''} (${(d.cues.length / dur * 60).toFixed(0)}/min)`);
  }

  // ---- captions ----
  const C = d.captionInfo;
  if (C.mode === 'off') I.push('captions: off (visual cartoon mode)');
  if (C.mode === 'full' && (P.name === 'reel' || P.name === 'short')) W.push('captions mode is "full" (a subtitle box). Reel/short style is "phrase": short phrases with a yellow keyword.');
  if (C.mode === 'phrase' || C.mode === 'ja') {
    if (C.maxWords > P.capWords + 2) W.push(`a caption phrase has ${C.maxWords} words (target ≤ ${P.capWords}). Re-run node tools/plan_captions.mjs --force or shorten the \`caps\` phrases.`);
    if (C.phrases && C.withKeyword < C.phrases * 0.4) W.push(`only ${C.withKeyword}/${C.phrases} caption phrases have a yellow keyword — mark important words with * in \`caps\`.`);
    I.push(`captions: ${C.mode}, ${C.phrases} phrases, ${C.withKeyword} with keyword`);
  }

  // ---- audio files ----
  if (files.mixExists === false && !d.script.silent) E.push('data/mix.wav (the narration) is missing — the video would be SILENT. Run: python3 tools/voice_edge.py (or put your own voiceover in data/mix.wav and lines.json).');
  if (files.mixDur != null && Math.abs(files.mixDur - dur) > 0.35) E.push(`data/mix.wav is ${files.mixDur.toFixed(1)} s but lines.json says ${dur.toFixed(1)} s — regenerate the voiceover so audio and timings match.`);
  return { errors: E, warnings: W, info: I };
}
