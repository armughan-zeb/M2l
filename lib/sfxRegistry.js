'use strict';

/**
 * Universal Sound Effects (SFX) & Audio Asset Registry & Resolver.
 *
 * Maps all editorial aliases, bare sound names, legacy identifiers,
 * channel music tracks, and paths to verified files in public/.
 *
 * Guarantees:
 * - Bare 'whoosh' routes to 'sound-effects/edit/edit_whoosh_light.wav'
 * - 'map_pulse' routes to 'sound-effects/vidzeo/map_pulse.wav'
 * - 'stamp_hit' routes to 'sound-effects/vidzeo/stamp_hit.wav'
 * - 'typewriter' routes to 'sound-effects/vidzeo/typewriter.wav'
 * - 'sub_boom' routes to 'sound-effects/vidzeo/sub_boom.wav'
 * - 'marker_scratch' routes to 'sound-effects/vidzeo/marker_scratch.wav'
 * - 'clock_tick' routes to 'sound-effects/vidzeo/clock_tick.wav'
 * - Any path containing 'sound-effects/' that does not exist on disk is routed
 *   through alias and fuzzy lookup, and if unresolvable returns null.
 * - UNRESOLVABLE ASSETS NEVER RETURN BARE / INVALID STRINGS (returns null so Remotion
 *   never requests a 404 URL or crashes the render).
 */

const KNOWN_SFX_FILES = new Set([
  // Edit directory sound effects (27 verified files)
  'sound-effects/edit/edit_book_close.wav',
  'sound-effects/edit/edit_check_tick.wav',
  'sound-effects/edit/edit_cursor_blink.wav',
  'sound-effects/edit/edit_flash_bulb.wav',
  'sound-effects/edit/edit_hit_boomed.wav',
  'sound-effects/edit/edit_hit_dry.wav',
  'sound-effects/edit/edit_hit_tight.wav',
  'sound-effects/edit/edit_marker_underline.wav',
  'sound-effects/edit/edit_paper_shuffle.wav',
  'sound-effects/edit/edit_paper_tear_subtle.wav',
  'sound-effects/edit/edit_reveal_chime.wav',
  'sound-effects/edit/edit_reveal_soft.wav',
  'sound-effects/edit/edit_room_shift.wav',
  'sound-effects/edit/edit_shutter_snap.wav',
  'sound-effects/edit/edit_slider_move.wav',
  'sound-effects/edit/edit_staple.wav',
  'sound-effects/edit/edit_sting_major.wav',
  'sound-effects/edit/edit_sting_minor.wav',
  'sound-effects/edit/edit_tape_stop.wav',
  'sound-effects/edit/edit_type_burst_short.wav',
  'sound-effects/edit/edit_type_click.wav',
  'sound-effects/edit/edit_vinyl_pop.wav',
  'sound-effects/edit/edit_whoosh_air.wav',
  'sound-effects/edit/edit_whoosh_deep.wav',
  'sound-effects/edit/edit_whoosh_light.wav',
  'sound-effects/edit/edit_whoosh_sucked.wav',
  'sound-effects/edit/edit_zoom_whirr.wav',

  // Vidzeo directory sound effects (61 verified files)
  'sound-effects/vidzeo/archive_drone.wav',
  'sound-effects/vidzeo/bass_drop.wav',
  'sound-effects/vidzeo/calendar_flip.wav',
  'sound-effects/vidzeo/camera_shutter.wav',
  'sound-effects/vidzeo/camera_wind.wav',
  'sound-effects/vidzeo/cash_counter.wav',
  'sound-effects/vidzeo/clock_tick.wav',
  'sound-effects/vidzeo/coin_tick.wav',
  'sound-effects/vidzeo/crowd_murmur.wav',
  'sound-effects/vidzeo/data_tick.wav',
  'sound-effects/vidzeo/decision_tick.wav',
  'sound-effects/vidzeo/film_splice.wav',
  'sound-effects/vidzeo/heartbeat.wav',
  'sound-effects/vidzeo/impact.wav',
  'sound-effects/vidzeo/impact_glass.wav',
  'sound-effects/vidzeo/impact_metal.wav',
  'sound-effects/vidzeo/impact_soft.wav',
  'sound-effects/vidzeo/impact_wood.wav',
  'sound-effects/vidzeo/keyboard_single.wav',
  'sound-effects/vidzeo/lens_click.wav',
  'sound-effects/vidzeo/low_rumble.wav',
  'sound-effects/vidzeo/map_draw.wav',
  'sound-effects/vidzeo/map_pulse.wav',
  'sound-effects/vidzeo/marker_scratch.wav',
  'sound-effects/vidzeo/mouse_click.wav',
  'sound-effects/vidzeo/news_sting.wav',
  'sound-effects/vidzeo/notification_down.wav',
  'sound-effects/vidzeo/notification_up.wav',
  'sound-effects/vidzeo/page_stack.wav',
  'sound-effects/vidzeo/paper_flip.wav',
  'sound-effects/vidzeo/paper_rustle.wav',
  'sound-effects/vidzeo/paper_tear.wav',
  'sound-effects/vidzeo/pencil_mark.wav',
  'sound-effects/vidzeo/photo_slide.wav',
  'sound-effects/vidzeo/projector.wav',
  'sound-effects/vidzeo/radio_static.wav',
  'sound-effects/vidzeo/reveal_chime.wav',
  'sound-effects/vidzeo/reverse_swell.wav',
  'sound-effects/vidzeo/riser.wav',
  'sound-effects/vidzeo/room_tone.wav',
  'sound-effects/vidzeo/route_ping.wav',
  'sound-effects/vidzeo/signal_ping.wav',
  'sound-effects/vidzeo/sonar.wav',
  'sound-effects/vidzeo/stamp_hit.wav',
  'sound-effects/vidzeo/sub_boom.wav',
  'sound-effects/vidzeo/suspense_pulse.wav',
  'sound-effects/vidzeo/technical_tick.wav',
  'sound-effects/vidzeo/telegraph.wav',
  'sound-effects/vidzeo/tension_string.wav',
  'sound-effects/vidzeo/transition_suck.wav',
  'sound-effects/vidzeo/typewriter.wav',
  'sound-effects/vidzeo/typing_burst.wav',
  'sound-effects/vidzeo/ui_tick.wav',
  'sound-effects/vidzeo/vinyl_crackle.wav',
  'sound-effects/vidzeo/water_pulse.wav',
  'sound-effects/vidzeo/whoosh.wav',
  'sound-effects/vidzeo/whoosh_air.wav',
  'sound-effects/vidzeo/whoosh_dark.wav',
  'sound-effects/vidzeo/whoosh_snap.wav',
  'sound-effects/vidzeo/whoosh_soft.wav',
  'sound-effects/vidzeo/wind_room.wav'
]);

const KNOWN_MUSIC_FILES = new Set([
  'music/after-death.mp3',
  'music/ambient_dark.mp3',
  'music/british_orchestral.mp3',
  'music/cinematic.mp3',
  'music/classic-wheels.mp3',
  'music/crown-royal.mp3',
  'music/crown-zone.mp3',
  'music/documentary.mp3',
  'music/industrial_acoustic.mp3',
  'music/investigative_tension.mp3',
  'music/modern_cinematic.mp3',
  'music/nostalgic-brit.mp3',
  'music/retro_groove.mp3',
  'music/royal-celeb.mp3',
  'music/royal_orchestral.mp3',
  'music/suspense.mp3',
  'music/train-crew.mp3'
]);

const KNOWN_MAP_FILES = new Set([
  'map-assets/nasa-blue-marble-topography-mercator.jpg',
  'map-assets/nasa-blue-marble-july-5400.jpg',
  'map-assets/panama-canal-satellite-eox-2016.jpg'
]);

const SFX_REGISTRY_MAP = {
  // Whooshes & transitions — bare 'whoosh' maps to edit_whoosh_light.wav
  'whoosh': 'sound-effects/edit/edit_whoosh_light.wav',
  'whoosh.light': 'sound-effects/edit/edit_whoosh_light.wav',
  'whoosh_light': 'sound-effects/edit/edit_whoosh_light.wav',
  'edit_whoosh_light': 'sound-effects/edit/edit_whoosh_light.wav',
  'whoosh.deep': 'sound-effects/edit/edit_whoosh_deep.wav',
  'whoosh_deep': 'sound-effects/edit/edit_whoosh_deep.wav',
  'edit_whoosh_deep': 'sound-effects/edit/edit_whoosh_deep.wav',
  'whoosh.air': 'sound-effects/edit/edit_whoosh_air.wav',
  'whoosh_air': 'sound-effects/edit/edit_whoosh_air.wav',
  'edit_whoosh_air': 'sound-effects/edit/edit_whoosh_air.wav',
  'whoosh.soft': 'sound-effects/vidzeo/whoosh_soft.wav',
  'whoosh_soft': 'sound-effects/vidzeo/whoosh_soft.wav',
  'whoosh.snap': 'sound-effects/vidzeo/whoosh_snap.wav',
  'whoosh_snap': 'sound-effects/vidzeo/whoosh_snap.wav',
  'whoosh.dark': 'sound-effects/vidzeo/whoosh_dark.wav',
  'whoosh_dark': 'sound-effects/vidzeo/whoosh_dark.wav',
  'whoosh.vidzeo': 'sound-effects/vidzeo/whoosh.wav',
  'whoosh_vidzeo': 'sound-effects/vidzeo/whoosh.wav',
  'transition.suck': 'sound-effects/vidzeo/transition_suck.wav',
  'transition_suck': 'sound-effects/vidzeo/transition_suck.wav',
  'transition': 'sound-effects/edit/edit_whoosh_light.wav',
  'edit.whoosh.sucked': 'sound-effects/edit/edit_whoosh_sucked.wav',
  'edit_whoosh_sucked': 'sound-effects/edit/edit_whoosh_sucked.wav',
  'edit.slider.move': 'sound-effects/edit/edit_slider_move.wav',
  'edit_slider_move': 'sound-effects/edit/edit_slider_move.wav',
  'edit.zoom.whirr': 'sound-effects/edit/edit_zoom_whirr.wav',
  'edit_zoom_whirr': 'sound-effects/edit/edit_zoom_whirr.wav',
  'edit.hit.tight': 'sound-effects/edit/edit_hit_tight.wav',

  // Paper & documents
  'paper.shuffle': 'sound-effects/edit/edit_paper_shuffle.wav',
  'paper_shuffle': 'sound-effects/edit/edit_paper_shuffle.wav',
  'edit_paper_shuffle': 'sound-effects/edit/edit_paper_shuffle.wav',
  'paper.tear': 'sound-effects/vidzeo/paper_tear.wav',
  'paper_tear': 'sound-effects/vidzeo/paper_tear.wav',
  'edit_paper_tear_subtle': 'sound-effects/edit/edit_paper_tear_subtle.wav',
  'paper.flip': 'sound-effects/vidzeo/paper_flip.wav',
  'paper_flip': 'sound-effects/vidzeo/paper_flip.wav',
  'paper.rustle': 'sound-effects/vidzeo/paper_rustle.wav',
  'paper_rustle': 'sound-effects/vidzeo/paper_rustle.wav',
  'marker.scratch': 'sound-effects/vidzeo/marker_scratch.wav',
  'marker_scratch': 'sound-effects/vidzeo/marker_scratch.wav',
  'pencil.mark': 'sound-effects/vidzeo/pencil_mark.wav',
  'pencil_mark': 'sound-effects/vidzeo/pencil_mark.wav',
  'stamp.hit': 'sound-effects/vidzeo/stamp_hit.wav',
  'stamp_hit': 'sound-effects/vidzeo/stamp_hit.wav',
  'pin.click': 'sound-effects/edit/edit_staple.wav',
  'pin_click': 'sound-effects/edit/edit_staple.wav',
  'staple': 'sound-effects/edit/edit_staple.wav',
  'marker.underline': 'sound-effects/edit/edit_marker_underline.wav',
  'marker_underline': 'sound-effects/edit/edit_marker_underline.wav',
  'edit_staple': 'sound-effects/edit/edit_staple.wav',
  'page.stack': 'sound-effects/vidzeo/page_stack.wav',
  'page_stack': 'sound-effects/vidzeo/page_stack.wav',

  // Impacts, stings & risers
  'impact.soft': 'sound-effects/vidzeo/impact_soft.wav',
  'impact_soft': 'sound-effects/vidzeo/impact_soft.wav',
  'impact.heavy': 'sound-effects/vidzeo/impact.wav',
  'impact': 'sound-effects/vidzeo/impact.wav',
  'sub.boom': 'sound-effects/vidzeo/sub_boom.wav',
  'sub_boom': 'sound-effects/vidzeo/sub_boom.wav',
  'bass.drop': 'sound-effects/vidzeo/bass_drop.wav',
  'bass_drop': 'sound-effects/vidzeo/bass_drop.wav',
  'hit.dry': 'sound-effects/edit/edit_hit_dry.wav',
  'hit_dry': 'sound-effects/edit/edit_hit_dry.wav',
  'edit_hit_dry': 'sound-effects/edit/edit_hit_dry.wav',
  'edit_hit_tight': 'sound-effects/edit/edit_hit_tight.wav',
  'sting.major': 'sound-effects/edit/edit_sting_major.wav',
  'sting_major': 'sound-effects/edit/edit_sting_major.wav',
  'sting.minor': 'sound-effects/edit/edit_sting_minor.wav',
  'sting_minor': 'sound-effects/edit/edit_sting_minor.wav',
  'edit_sting_minor': 'sound-effects/edit/edit_sting_minor.wav',
  'riser.tension': 'sound-effects/vidzeo/riser.wav',
  'riser': 'sound-effects/vidzeo/riser.wav',
  'reveal.chime': 'sound-effects/vidzeo/reveal_chime.wav',
  'reveal_chime': 'sound-effects/vidzeo/reveal_chime.wav',
  'reveal.soft': 'sound-effects/edit/edit_reveal_soft.wav',
  'reveal_soft': 'sound-effects/edit/edit_reveal_soft.wav',
  'edit_reveal_soft': 'sound-effects/edit/edit_reveal_soft.wav',
  'reverse.swell': 'sound-effects/vidzeo/reverse_swell.wav',
  'reverse_swell': 'sound-effects/vidzeo/reverse_swell.wav',
  'news.sting': 'sound-effects/vidzeo/news_sting.wav',
  'news_sting': 'sound-effects/vidzeo/news_sting.wav',

  // Telemetry & metrics
  'data.tick': 'sound-effects/vidzeo/data_tick.wav',
  'data_tick': 'sound-effects/vidzeo/data_tick.wav',
  'technical.tick': 'sound-effects/vidzeo/technical_tick.wav',
  'technical_tick': 'sound-effects/vidzeo/technical_tick.wav',
  'coin.tick': 'sound-effects/vidzeo/coin_tick.wav',
  'coin_tick': 'sound-effects/vidzeo/coin_tick.wav',
  'cash.counter': 'sound-effects/vidzeo/cash_counter.wav',
  'cash_counter': 'sound-effects/vidzeo/cash_counter.wav',
  'clock.tick': 'sound-effects/vidzeo/clock_tick.wav',
  'clock_tick': 'sound-effects/vidzeo/clock_tick.wav',
  'ui_tick': 'sound-effects/vidzeo/ui_tick.wav',
  'ui.tick': 'sound-effects/vidzeo/ui_tick.wav',
  'decision_tick': 'sound-effects/vidzeo/decision_tick.wav',
  'decision.tick': 'sound-effects/vidzeo/decision_tick.wav',

  // Maps & radar
  'map.pulse': 'sound-effects/vidzeo/map_pulse.wav',
  'map_pulse': 'sound-effects/vidzeo/map_pulse.wav',
  'map.ping': 'sound-effects/vidzeo/map_pulse.wav',
  'map_ping': 'sound-effects/vidzeo/map_pulse.wav',
  'map.draw': 'sound-effects/vidzeo/map_draw.wav',
  'map_draw': 'sound-effects/vidzeo/map_draw.wav',
  'sonar.ping': 'sound-effects/vidzeo/sonar.wav',
  'sonar': 'sound-effects/vidzeo/sonar.wav',
  'route.ping': 'sound-effects/vidzeo/route_ping.wav',
  'route_ping': 'sound-effects/vidzeo/route_ping.wav',
  'signal.ping': 'sound-effects/vidzeo/signal_ping.wav',
  'signal_ping': 'sound-effects/vidzeo/signal_ping.wav',

  // Camera & profile
  'camera.shutter': 'sound-effects/vidzeo/camera_shutter.wav',
  'camera_shutter': 'sound-effects/vidzeo/camera_shutter.wav',
  'camera.wind': 'sound-effects/vidzeo/camera_wind.wav',
  'camera_wind': 'sound-effects/vidzeo/camera_wind.wav',
  'photo.slide': 'sound-effects/vidzeo/photo_slide.wav',
  'photo_slide': 'sound-effects/vidzeo/photo_slide.wav',

  // Typing & text
  'type.click': 'sound-effects/vidzeo/typewriter.wav',
  'typewriter': 'sound-effects/vidzeo/typewriter.wav',
  'type.burst': 'sound-effects/vidzeo/typing_burst.wav',
  'typing_burst': 'sound-effects/vidzeo/typing_burst.wav',
  'edit_type_click': 'sound-effects/edit/edit_type_click.wav',
  'edit_type_burst_short': 'sound-effects/edit/edit_type_burst_short.wav',

  // Ambient & background
  'room_tone': 'sound-effects/vidzeo/room_tone.wav',
  'low_rumble': 'sound-effects/vidzeo/low_rumble.wav',
  'suspense_pulse': 'sound-effects/vidzeo/suspense_pulse.wav',
  'archive_drone': 'sound-effects/vidzeo/archive_drone.wav',
  'wind_room': 'sound-effects/vidzeo/wind_room.wav',
  'vinyl_crackle': 'sound-effects/vidzeo/vinyl_crackle.wav',
  'heartbeat': 'sound-effects/vidzeo/heartbeat.wav',
  'water_pulse': 'sound-effects/vidzeo/water_pulse.wav'
};

const MUSIC_REGISTRY_MAP = {
  'classic-wheels': 'music/classic-wheels.mp3',
  'classic_wheels': 'music/classic-wheels.mp3',
  'nostalgic-brit': 'music/nostalgic-brit.mp3',
  'nostalgic_brit': 'music/nostalgic-brit.mp3',
  'royal-celeb': 'music/royal-celeb.mp3',
  'royal_celeb': 'music/royal-celeb.mp3',
  'crown-royal': 'music/crown-royal.mp3',
  'crown_royal': 'music/crown-royal.mp3',
  'crown-zone': 'music/crown-zone.mp3',
  'crown_zone': 'music/crown-zone.mp3',
  'after-death': 'music/after-death.mp3',
  'after_death': 'music/after-death.mp3',
  'train-crew': 'music/train-crew.mp3',
  'train_crew': 'music/train-crew.mp3',
  'documentary': 'music/documentary.mp3',
  'cinematic': 'music/cinematic.mp3',
  'ambient_dark': 'music/ambient_dark.mp3',
  'ambient-dark': 'music/ambient_dark.mp3',
  'investigative_tension': 'music/investigative_tension.mp3',
  'investigative-tension': 'music/investigative_tension.mp3',
  'british_orchestral': 'music/british_orchestral.mp3',
  'british-orchestral': 'music/british_orchestral.mp3',
  'royal_orchestral': 'music/royal_orchestral.mp3',
  'royal-orchestral': 'music/royal_orchestral.mp3',
  'industrial_acoustic': 'music/industrial_acoustic.mp3',
  'industrial-acoustic': 'music/industrial_acoustic.mp3',
  'modern_cinematic': 'music/modern_cinematic.mp3',
  'modern-cinematic': 'music/modern_cinematic.mp3',
  'retro_groove': 'music/retro_groove.mp3',
  'retro-groove': 'music/retro_groove.mp3',
  'suspense': 'music/suspense.mp3'
};

function defaultStaticFile(relPath) {
  return '/' + String(relPath || '').replace(/^\/+/, '');
}

/**
 * Resolves any raw SFX identifier, alias, or path to its clean relative path
 * in public/ (e.g. 'sound-effects/edit/edit_whoosh_light.wav').
 *
 * Guarantees that:
 * 1. Bare 'whoosh' always resolves to 'sound-effects/edit/edit_whoosh_light.wav'
 * 2. Any path containing 'sound-effects/' that does NOT exist on disk is routed through
 *    registry and fuzzy lookup.
 * 3. Never returns an invalid relative string that does not exist in KNOWN_SFX_FILES.
 * 4. Returns null for unresolvable cues so Remotion safely bypasses them without 404s.
 */
function resolveSfxRelativePath(raw) {
  if (!raw || typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  if (!trimmed || trimmed === 'none' || trimmed === 'null' || trimmed === 'undefined') return null;

  // External URLs or data URIs: return directly
  if (trimmed.startsWith('http://') || trimmed.startsWith('https://') || trimmed.startsWith('data:') || trimmed.startsWith('blob:') || trimmed.startsWith('file://')) {
    return trimmed;
  }

  const normalized = trimmed.replace(/\\/g, '/').replace(/^\/+/, '');

  // 1. Exact path containing sound-effects/ that matches verified existing file
  const sfxIdx = normalized.indexOf('sound-effects/');
  if (sfxIdx !== -1) {
    const sfxRel = normalized.slice(sfxIdx);
    if (KNOWN_SFX_FILES.has(sfxRel)) {
      return sfxRel;
    }
    // If NOT in KNOWN_SFX_FILES, DO NOT return sfxRel (it would 404)!
    // Continue to extract basename and resolve via aliases/registry.
  }

  // 2. Extract base identifier (strip query parameters, audio extensions, and directories)
  let baseId = normalized;
  if (baseId.includes('?')) {
    baseId = baseId.split('?')[0];
  }
  if (baseId.endsWith('.wav') || baseId.endsWith('.mp3') || baseId.endsWith('.ogg') || baseId.endsWith('.aac') || baseId.endsWith('.m4a')) {
    baseId = baseId.slice(0, baseId.lastIndexOf('.'));
  }
  if (baseId.includes('/')) {
    baseId = baseId.split('/').pop();
  }

  const cleanId = baseId.toLowerCase().trim();
  if (!cleanId) return null;

  const dotId = cleanId.replace(/_/g, '.');
  const underscoreId = cleanId.replace(/\./g, '_');

  // 3. Registry map lookup
  const mapped = SFX_REGISTRY_MAP[cleanId]
    || SFX_REGISTRY_MAP[dotId]
    || SFX_REGISTRY_MAP[underscoreId]
    || SFX_REGISTRY_MAP['edit_' + underscoreId];
  if (mapped && KNOWN_SFX_FILES.has(mapped)) {
    return mapped;
  }

  // 4. Edit folder prefix check (edit_*.wav)
  if (cleanId.startsWith('edit_')) {
    const editCandidate = `sound-effects/edit/${cleanId}.wav`;
    if (KNOWN_SFX_FILES.has(editCandidate)) {
      return editCandidate;
    }
  }

  // 5. Vidzeo folder direct check
  const vidzeoCandidate = `sound-effects/vidzeo/${underscoreId}.wav`;
  if (KNOWN_SFX_FILES.has(vidzeoCandidate)) {
    return vidzeoCandidate;
  }

  // 6. Edit folder fallback check (edit_<name>.wav)
  const editFallback = `sound-effects/edit/edit_${underscoreId}.wav`;
  if (KNOWN_SFX_FILES.has(editFallback)) {
    return editFallback;
  }

  // 7. Edit folder fallback without edit_ prefix
  const editDirect = `sound-effects/edit/${underscoreId}.wav`;
  if (KNOWN_SFX_FILES.has(editDirect)) {
    return editDirect;
  }

  // 8. Whoosh & Transition family fallback (bare whoosh, variants, trans_whoosh, etc.)
  if (cleanId === 'whoosh' || cleanId.startsWith('whoosh') || cleanId.endsWith('whoosh') || cleanId === 'transition' || cleanId.startsWith('trans_whoosh') || cleanId.startsWith('edit_whoosh')) {
    if (cleanId.includes('deep')) return 'sound-effects/edit/edit_whoosh_deep.wav';
    if (cleanId.includes('air')) return 'sound-effects/edit/edit_whoosh_air.wav';
    if (cleanId.includes('soft')) return 'sound-effects/vidzeo/whoosh_soft.wav';
    if (cleanId.includes('snap')) return 'sound-effects/vidzeo/whoosh_snap.wav';
    if (cleanId.includes('dark')) return 'sound-effects/vidzeo/whoosh_dark.wav';
    return 'sound-effects/edit/edit_whoosh_light.wav';
  }

  // Unresolvable: safely return null so Remotion does not request a 404 URL
  return null;

}

/**
 * Resolves an SFX path using the supplied staticFile function.
 * Never returns a bare string that would trigger a 404 in Remotion.
 */
function resolveSfxPath(raw, staticFileFn = defaultStaticFile) {
  const relPath = resolveSfxRelativePath(raw);
  if (!relPath) return null;
  if (relPath.startsWith('http://') || relPath.startsWith('https://') || relPath.startsWith('data:') || relPath.startsWith('blob:') || relPath.startsWith('file://')) {
    return relPath;
  }
  return staticFileFn(relPath);
}

/**
 * Resolves a cue object (with fields sfx_id, sound_id, file, kind, type, asset, src, path, etc.)
 * or a raw string to a safe Remotion audio source.
 *
 * Guarantees:
 * - Transition cues default to 'sound-effects/edit/edit_whoosh_light.wav'
 * - Bare names ('whoosh', 'map_pulse', 'stamp_hit', 'typewriter', etc.) resolve cleanly
 * - Unresolvable cues return null
 */
function resolveSfxSource(cue, staticFileFn = defaultStaticFile) {
  if (!cue) return null;
  if (typeof cue === 'string') {
    return resolveSfxPath(cue, staticFileFn);
  }

  // 1. Try explicit candidate fields
  const candidate = cue.sfx
    || cue.sfx_id
    || cue.sound_id
    || cue.asset_id
    || cue.token
    || cue.file
    || cue.sound
    || cue.effect
    || cue.effect_id
    || (cue.kind && cue.kind !== 'transition' && cue.kind !== 'feature' ? cue.kind : null)
    || (cue.type && cue.type !== 'transition' && cue.type !== 'feature' ? cue.type : null)
    || cue.asset
    || cue.src
    || cue.path
    || (cue.id && !String(cue.id).startsWith('sfx-') ? cue.id : null)
    || cue.name;

  if (candidate && typeof candidate === 'string') {
    const resolved = resolveSfxPath(candidate, staticFileFn);
    if (resolved) return resolved;
  }

  // 2. Default transition sound fallback
  if (cue.kind === 'transition' || cue.type === 'transition' || String(cue.id || '').includes('trans')) {
    return staticFileFn('sound-effects/edit/edit_whoosh_light.wav');
  }

  return null;
}

/**
 * Resolves music identifiers and file paths to verified files in public/music/.
 */
function resolveMusicRelativePath(raw) {
  if (!raw || typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  if (!trimmed || trimmed === 'none' || trimmed === 'null' || trimmed === 'undefined') return null;

  if (trimmed.startsWith('http://') || trimmed.startsWith('https://') || trimmed.startsWith('data:') || trimmed.startsWith('blob:') || trimmed.startsWith('file://')) {
    return trimmed;
  }

  const normalized = trimmed.replace(/\\/g, '/').replace(/^\/+/, '');

  // 1. Direct path check
  const musicIdx = normalized.indexOf('music/');
  if (musicIdx !== -1) {
    const musicRel = normalized.slice(musicIdx);
    if (KNOWN_MUSIC_FILES.has(musicRel)) return musicRel;
  }

  // 2. Base ID extraction
  let baseId = normalized;
  if (baseId.endsWith('.mp3') || baseId.endsWith('.wav')) {
    baseId = baseId.slice(0, baseId.lastIndexOf('.'));
  }
  if (baseId.includes('/')) {
    baseId = baseId.split('/').pop();
  }

  const cleanId = baseId.toLowerCase().trim();
  const hyphenId = cleanId.replace(/_/g, '-');
  const underscoreId = cleanId.replace(/-/g, '_');

  const mapped = MUSIC_REGISTRY_MAP[cleanId] || MUSIC_REGISTRY_MAP[hyphenId] || MUSIC_REGISTRY_MAP[underscoreId];
  if (mapped && KNOWN_MUSIC_FILES.has(mapped)) return mapped;

  const candidateHyphen = `music/${hyphenId}.mp3`;
  if (KNOWN_MUSIC_FILES.has(candidateHyphen)) return candidateHyphen;

  const candidateUnderscore = `music/${underscoreId}.mp3`;
  if (KNOWN_MUSIC_FILES.has(candidateUnderscore)) return candidateUnderscore;

  // General documentary fallback
  if (cleanId.includes('doc') || cleanId.includes('track') || cleanId.includes('soundtrack')) {
    return 'music/documentary.mp3';
  }

  return null;
}

function resolveMusicPath(raw, staticFileFn = defaultStaticFile) {
  const relPath = resolveMusicRelativePath(raw);
  if (!relPath) return null;
  if (relPath.startsWith('http://') || relPath.startsWith('https://') || relPath.startsWith('data:') || relPath.startsWith('blob:') || relPath.startsWith('file://')) {
    return relPath;
  }
  return staticFileFn(relPath);
}

/**
 * Resolves a generic static asset value (image, audio, remotion asset) safely.
 * Unresolvable bare strings or missing sound effects return null to prevent 404 render aborts.
 */
function resolveStaticAsset(value, staticFileFn = defaultStaticFile) {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (!trimmed || trimmed === 'none' || trimmed === 'null' || trimmed === 'undefined') return null;
  if (trimmed.startsWith('http://') || trimmed.startsWith('https://') || trimmed.startsWith('data:') || trimmed.startsWith('blob:') || trimmed.startsWith('file://')) {
    return trimmed;
  }
  const normalized = trimmed.replace(/\\/g, '/');

  // 1. Check SFX registry
  const sfxResolved = resolveSfxPath(trimmed, staticFileFn);
  if (sfxResolved) {
    return sfxResolved;
  }
  // If it was explicitly in sound-effects/ but could not be resolved, do not request it (prevent 404)
  if (normalized.includes('sound-effects/')) {
    return null;
  }

  // 2. Check Music registry
  const musicResolved = resolveMusicPath(trimmed, staticFileFn);
  if (musicResolved) {
    return musicResolved;
  }
  if (normalized.includes('music/') && (normalized.endsWith('.mp3') || normalized.endsWith('.wav'))) {
    return null;
  }

  // Any audio file that cannot be resolved in SFX or Music registry MUST return null.
  // Never allow Remotion to request a non-existent audio file from the webpack server (prevents fatal 404).
  const isAudioFile = /\.(?:wav|mp3|ogg|aac|m4a|flac)(?:[?#]|$)/i.test(trimmed);
  if (isAudioFile) {
    return null;
  }

  // 3. Map assets
  if (normalized.includes('map-assets/') || normalized.includes('blue-marble') || normalized.includes('panama-canal') || normalized.includes('satellite-eox')) {
    const mapRel = normalized.includes('map-assets/')
      ? normalized.slice(normalized.indexOf('map-assets/'))
      : `map-assets/${normalized.split('/').pop()}`;
    const verifiedMap = KNOWN_MAP_FILES.has(mapRel) ? mapRel : 'map-assets/nasa-blue-marble-topography-mercator.jpg';
    return staticFileFn(verifiedMap);
  }

  // 4. Remotion assets
  const remotionIdx = normalized.indexOf('remotion-');
  if (remotionIdx !== -1) {
    return staticFileFn(normalized.slice(remotionIdx));
  }

  // 5. Font library
  if (normalized.includes('font-library/')) {
    return staticFileFn(normalized.slice(normalized.indexOf('font-library/')));
  }

  // 6. If it's a bare name (no slashes, no dots/extension), DO NOT return bare string to prevent 404
  if (!trimmed.includes('/') && !trimmed.includes('\\') && !trimmed.includes('.')) {
    return null;
  }

  return staticFileFn(trimmed.replace(/^\/+/, ''));
}

module.exports = {
  KNOWN_SFX_FILES,
  KNOWN_MUSIC_FILES,
  KNOWN_MAP_FILES,
  SFX_REGISTRY_MAP,
  MUSIC_REGISTRY_MAP,
  resolveSfxRelativePath,
  resolveSfxPath,
  resolveSfxSource,
  resolveMusicRelativePath,
  resolveMusicPath,
  resolveStaticAsset
};
