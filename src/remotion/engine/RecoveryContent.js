// Recovery uses scene-local narration, never neighbouring subjects or invented data.
export function recoveryVisualKind(scene = {}, niche = '') {
  const spec = scene.media?.graphic_fallback || scene.graphic_fallback || {};
  const requirement = scene.visual_requirement || {};
  const blob = `${niche} ${scene.category || ''} ${scene.genre || ''} ${requirement.domain || ''} ${requirement.subject_type || ''} ${spec.subject_type || ''} ${spec.entity || ''} ${scene.narration || scene.scene_text || ''}`.toLowerCase();
  if (/braking|brakes?|hydraulic pressure/.test(blob)) return 'braking';
  if (/\b(?:nfl|football|quarterback|touchdown|super bowl|soccer|basketball|baseball|sports?)\b/.test(blob)) return 'sports';
  if (/\b(?:crime|murder|investigat|evidence|witness|court|case file|forensic|mystery|heist)\b/.test(blob)) return 'evidence';
  if (/\b(?:automobile|car|vehicle|engine|motor|battery|electric|energy|charging)\b/.test(blob)) return /battery|electric|energy|charging/.test(blob) ? 'energy' : 'vehicle';
  if (/\b(?:software|computer|technology|processor|robot|semiconductor)\b/.test(blob)) return 'technology';
  if (/\b(?:travel|route|geography|map|border|country|city|journey|canal|river)\b/.test(blob)) return 'geography';
  if (/\b(?:ship|vessel|ocean|maritime|aviation|aircraft|airplane|flight|spacecraft)\b/.test(blob)) return 'transport';
  if (/\b(?:biography|biographical|person|president|queen|king|prince|princess|founder|life of)\b/.test(blob)) return 'biography';
  if (/\b(?:politics|political|election|parliament|government|campaign|policy)\b/.test(blob)) return 'politics';
  if (/\b(?:history|historical|archive|century|decade|royal|monarch|war)\b/.test(blob)) return 'history';
  if (/\b(?:science|physics|biology|astronomy|climate|experiment)\b/.test(blob)) return 'science';
  return '';
}

const numericValue = (value) => Number.isFinite(Number(String(value ?? '').replace(/,/g, '').match(/-?\d+(?:\.\d+)?/)?.[0]));

export function recoveryVisualGrammar(scene = {}, niche = '') {
  const spec = scene.media?.graphic_fallback || scene.graphic_fallback || {};
  const requirement = scene.visual_requirement || {};
  const visualType = String(scene.primary_visual?.type || '').toLowerCase().replace(/[_\s]/g, '-');
  const requested = String(spec.template_id || scene.graphic?.template_id || '').toLowerCase().replace(/[_\s]/g, '-');
  const props = {
    ...(scene.primary_visual?.props || {}),
    ...(scene.graphic?.props || {}),
    ...(spec.props || {}),
  };
  const mapShot = scene.map_shot || props.map_shot || {};
  const mapNames = Array.isArray(mapShot.location_names) ? mapShot.location_names : [];
  const hasRoutePair = Boolean((props.from || props.start || props.startCity)
    && (props.to || props.end || props.endCity));
  const hasCoordinatePair = Boolean((props.p1 && props.p2)
    || (props.from_coordinates && props.to_coordinates));
  const hasGroundedMap = Boolean(props.country || props.target_country || props.location || props.place
    || hasRoutePair || hasCoordinatePair
    || mapNames.length || (Array.isArray(props.highlight_countries) && props.highlight_countries.length >= 2));
  const data = Array.isArray(props.data) ? props.data : (Array.isArray(props.values) ? props.values : []);
  const hasChartData = data.filter((row) => row && row.label && numericValue(row.value)).length >= 2
    || Boolean(props.before?.value != null && props.after?.value != null
      && numericValue(props.before.value) && numericValue(props.after.value));
  const events = Array.isArray(props.events) ? props.events : [];
  const hasTimelineData = events.length > 0 || Boolean(props.date || props.year || props.eraSpan);
  const hasSteps = Array.isArray(props.steps) && props.steps.filter((step) => step && (step.title || step.label || step.description || step.desc)).length >= 2;

  // A director's specific visual treatment remains authoritative, but an
  // incomplete data shape must not dispatch an empty chart or blank map.
  if ((visualType.includes('animated-map') || visualType.includes('satellite-map') || /map|route/.test(requested)) && hasGroundedMap) return 'map';
  if ((visualType.includes('chart') || /chart|histogram|ranked-bars|metric-grid/.test(requested)) && hasChartData) return 'chart';
  if ((visualType.includes('timeline') || /timeline|date-card|date-reveal|date-numeral/.test(requested)) && hasTimelineData) return 'timeline';
  if ((visualType.includes('diagram') || /diagram|process|cause-effect/.test(requested)) && hasSteps) return 'diagram';
  if (visualType.includes('evidence-pinboard') || requested.includes('evidence-pinboard')) return 'evidence';
  if (visualType.includes('declassified-document') || /declassified|dossier|redaction/.test(requested)) return 'archive';
  if (visualType.includes('technical-blueprint') || requested.includes('blueprint')) return 'blueprint';

  const kind = recoveryVisualKind(scene, niche);
  if (kind === 'evidence') return Number(scene.scene_index || 0) % 3 === 1 ? 'archive' : 'evidence';
  if (kind === 'geography' && hasGroundedMap) return 'map';
  if (kind === 'vehicle' || kind === 'braking') return 'blueprint';
  if (kind === 'biography') return 'profile';
  if (kind === 'politics') return 'headline';
  if ((kind === 'history' || kind === 'transport') && hasTimelineData) return 'timeline';
  if (kind === 'science' && hasSteps) return 'diagram';
  if (kind === 'sports' && hasChartData) return 'chart';
  if (kind === 'sports' || kind === 'politics') return 'headline';
  if (hasGroundedMap) return 'map';
  if (hasChartData) return 'chart';
  if (hasTimelineData && /history|ship|maritime|royal|biography/i.test(`${niche} ${requirement.domain || ''}`)) return 'timeline';
  return 'archive';
}

export function recoveryContent(scene = {}) {
  const spec = scene.media?.graphic_fallback || scene.graphic_fallback || {};
  const text = [scene.narration, scene.scene_text, scene.text_content,
    spec.grounded === true ? spec.text : '']
    .find((value) => typeof value === 'string' && value.trim());
  if (!text) return [];
  const words = text.trim().replace(/\s+/g, ' ').split(' ');
  const beats = [];
  let beat = '';
  for (const word of words) {
    if (beat && (beat.length + word.length > 105 || /[.!?]$/.test(beat))) {
      beats.push(beat);
      beat = '';
    }
    beat += `${beat ? ' ' : ''}${word}`;
  }
  if (beat) beats.push(beat);
  // Keep short overflow tails readable without merging separate sentences.
  for (let i = 1; i < beats.length; i++) {
    const previous = beats[i - 1].split(' ');
    const current = beats[i].split(' ');
    if (current.length < 4 && previous.length >= 8 && !/[.!?]$/.test(beats[i - 1])) {
      while (current.length < 4 && previous.length > 4) current.unshift(previous.pop());
      beats[i - 1] = previous.join(' ');
      beats[i] = current.join(' ');
    }
  }
  return beats;
}

export function recoveryIsBackdrop(scene = {}) {
  const type = scene.primary_visual?.type || '';
  // Data, profiles and director recipes retain ownership of their explanation.
  return (type && !['SmartImage', 'SmartVideo', 'BrollImage', 'BrollVideo', 'image', 'video',
    'KineticText', 'ChapterTitle', 'Headline'].includes(type))
    || (scene.template_id === 'generated-recipe' && Array.isArray(scene.template_recipe?.layers)
      && scene.template_recipe.layers.length > 0);
}

export function timedRecoveryNarration(manifest, from, duration, fps) {
  const words = manifest.word_timings || manifest.caption_words || manifest.captions?.words || [];
  if (!Array.isArray(words)) return '';
  return words.filter((word) => Number.isFinite(Number(word.start)) && Number.isFinite(Number(word.end))
    && Number(word.start) >= from / fps && Number(word.start) < (from + duration) / fps)
    .map((word) => typeof (word.word || word.text) === 'string' ? (word.word || word.text) : '')
    .filter(Boolean).join(' ');
}
