const path = require('path');
const fs = require('fs');
const os = require('os');
const { bundle } = require('@remotion/bundler');
const { renderStill, renderMedia, selectComposition, openBrowser } = require('@remotion/renderer');
const { stageRendererPublicAssets } = require('./render-remotion-manifest');

const root = path.resolve(__dirname, '..');
const output = path.join(os.tmpdir(), 'vidzeo-adaptive-documentary-smoke-contract-v4');
fs.mkdirSync(output, { recursive: true });
const svgFixture = (svg) => `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`;
const imageFixture = svgFixture('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"><defs><linearGradient id="sky" y2="1"><stop stop-color="#82969a"/><stop offset="1" stop-color="#d5b990"/></linearGradient><linearGradient id="sea" y2="1"><stop stop-color="#31545b"/><stop offset="1" stop-color="#102e39"/></linearGradient></defs><rect width="1280" height="420" fill="url(#sky)"/><rect y="420" width="1280" height="300" fill="url(#sea)"/><circle cx="1015" cy="175" r="64" fill="#e6c889" opacity=".88"/><path d="M0 394Q230 330 470 386T930 374T1280 352V455H0Z" fill="#263b37"/><path d="M350 408h450l-46 122H410Z" fill="#17242a"/><rect x="455" y="330" width="245" height="91" fill="#9d593c"/><path d="M455 330h245v30H455zM490 286h176v44H490z" fill="#c58b50"/><path d="M430 420h308M497 286v-42m173 42v-52" stroke="#e6c889" stroke-width="8"/><path d="M0 545q140-28 280 7t280 0t280 2t280-4t160 2" fill="none" stroke="#b8c5bb" stroke-opacity=".42" stroke-width="4"/></svg>');
const imageFixtureAlt = svgFixture('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"><defs><linearGradient id="sky" y2="1"><stop stop-color="#aab8b1"/><stop offset="1" stop-color="#e1c99e"/></linearGradient><linearGradient id="water" y2="1"><stop stop-color="#55747a"/><stop offset="1" stop-color="#173844"/></linearGradient></defs><rect width="1280" height="360" fill="url(#sky)"/><rect y="360" width="1280" height="360" fill="url(#water)"/><path d="M0 360 190 248 335 350 512 224 665 356 875 244 1020 359Z" fill="#45574b"/><path d="M540 338h465l-78 180H610Z" fill="#1b272c"/><rect x="650" y="256" width="256" height="88" fill="#b56443"/><rect x="684" y="210" width="180" height="48" fill="#d0a45e"/><path d="M620 338h400M672 210v-60m190 60v-72" stroke="#e7c98b" stroke-width="8"/><circle cx="1035" cy="156" r="48" fill="#e2bd7d" opacity=".8"/></svg>');
const imageFixtureClose = svgFixture('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"><defs><linearGradient id="sky" y2="1"><stop stop-color="#718990"/><stop offset="1" stop-color="#c8b18d"/></linearGradient><linearGradient id="water" y2="1"><stop stop-color="#42616a"/><stop offset="1" stop-color="#0b2632"/></linearGradient></defs><rect width="1280" height="380" fill="url(#sky)"/><rect y="380" width="1280" height="340" fill="url(#water)"/><path d="M120 492h945l-80 165H226Z" fill="#18252a"/><path d="M334 492V293h485v199" fill="#9c563e"/><path d="M350 300h448v47H350zM390 247h370v52H390z" fill="#c68b51"/><path d="M270 492h720M414 247v-91m342 91V142" stroke="#e3c48b" stroke-width="9"/><path d="M0 606q190-32 380 0t380 0t380-3t140 5" fill="none" stroke="#c0c8bf" stroke-opacity=".4" stroke-width="5"/></svg>');
const evidenceFixture = svgFixture('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"><rect width="1280" height="720" fill="#ece8dc"/><rect x="170" y="70" width="940" height="580" fill="#f8f6ef" stroke="#b9b3a6" stroke-width="3"/><text x="230" y="150" fill="#17222a" font-family="Arial,sans-serif" font-size="34" font-weight="700">MARITIME ROUTE REPORT</text><text x="230" y="194" fill="#66727a" font-family="Arial,sans-serif" font-size="18">ARCHIVE COPY · SHIPPING AND TRANSIT</text><path d="M230 224h820" stroke="#a9a397" stroke-width="2"/><text x="230" y="288" fill="#313a40" font-family="Arial,sans-serif" font-size="24">The report identifies the route.</text><path d="M230 320h780M230 358h820M230 396h750M230 434h810M230 472h690" stroke="#a8a49a" stroke-width="5" stroke-linecap="round" opacity=".72"/><path d="M920 540h120" stroke="#c28b2e" stroke-width="6"/></svg>');
const cutoutFixture = svgFixture('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="700"><ellipse cx="300" cy="350" rx="200" ry="280" fill="#c58b50"/><circle cx="300" cy="200" r="100" fill="#e6c889"/></svg>');
const recipeLayer = (kind, values = {}) => ({
  kind, x: 8, y: 12, width: 84, height: 76, text: '', color: '#f2c94c', asset_id: '',
  motion: 'rise', start_frame: 0, duration_frames: 72, data: [], shape: 'rounded',
  orientation: 'horizontal', focal_x: 50, focal_y: 50, links: [], ...values,
});

const dna = {
  palette: { background: '#111820', surface: '#1e2833', primary: '#f7f6f2', secondary: '#b4bcc7', accent: '#f2c94c', border: '#728091' },
  typography: { display: 'Archivo Black', body: 'DM Sans', editorial: 'Plus Jakarta Sans', numbers: 'JetBrains Mono' },
  render_mode: 'advanced',
};

const cases = [
  {
    narration: 'Japan narrows to Osaka.', type: 'AnimatedMap', variant: 'country_spotlight', frame: 'map',
    props: { country: 'Japan', location: 'Osaka', map_shot: { enabled: true, location_names: ['Japan', 'Osaka'], feature_ids: [], route_id: '', source_ids: [], motion_profile: 'city_reveal' } },
    cues: [{ element: 'map', action: 'pin', anchor_text: 'Osaka', time_local: .35, duration_seconds: .42 }],
  },
  {
    narration: 'The figure rose from 2 million to 12 million.', type: 'Chart', variant: 'editorial_bar', frame: 'data',
    props: { data: [{ label: '2010', value: '2 million' }, { label: '2020', value: '12 million' }] },
    cues: [{ element: 'data', action: 'count', anchor_text: '12 million', time_local: .4, duration_seconds: .65 }, { element: 'copy', action: 'reveal', anchor_text: '12 million', time_local: .6, duration_seconds: .42 }],
    copy: '12 million',
  },
  {
    narration: 'Rivers carry sediment, then deposits form a delta.', type: 'Diagram', variant: 'cause_and_effect_flow', frame: 'diagram',
    props: { steps: [{ desc: 'Rivers carry sediment' }, { desc: 'Deposits form a delta' }],
      node_copy_contract: { source: 'llm', approved: true, nodes: [
        { label: 'Rivers carry sediment', source_span: 'Rivers carry sediment' },
        { label: 'Deposits form a delta', source_span: 'Deposits form a delta' },
      ] } },
    cues: [{ element: 'steps', action: 'trace', anchor_text: 'Rivers carry sediment', time_local: .25, duration_seconds: .85 }],
  },
  {
    narration: 'Ocean heat shifts currents, and currents carry warmth toward the coast.',
    type: 'SmartImage', variant: 'cause_and_effect_flow', templateId: 'cause-effect-flow',
    visualRole: 'media_overlay', roleTag: 'diagram_media_overlay', frame: 'diagram_overlay',
    media: { type: 'image', src: imageFixtureAlt },
    props: { steps: [{ desc: 'Ocean heat shifts currents' }, { desc: 'Currents carry warmth toward the coast' }],
      node_copy_contract: { source: 'llm', approved: true, nodes: [
        { label: 'Ocean heat shifts currents', source_span: 'Ocean heat shifts currents' },
        { label: 'Currents carry warmth toward the coast', source_span: 'currents carry warmth toward the coast' },
      ] } },
    cues: [{ element: 'steps', action: 'trace', anchor_text: 'Ocean heat shifts currents', time_local: .2, duration_seconds: .65 }],
  },
  {
    narration: 'The first milestone was 1948, followed by 1949.', type: 'Timeline', variant: 'horizontal_milestone_trail', frame: 'data',
    props: { events: [{ year: '1948', label: 'First milestone' }, { year: '1949', label: 'Second milestone' }] },
    cues: [{ element: 'steps', action: 'trace', anchor_text: '1948', time_local: .3, duration_seconds: .95 }],
  },
  {
    narration: 'The total moved from 20 to 80.', type: 'Comparison', variant: 'dual_metric_comparison', frame: 'data',
    props: { leftLabel: 'Before', rightLabel: 'After', leftValue: '20', rightValue: '80' },
    cues: [{ element: 'comparison', action: 'split', anchor_text: '20 to 80', time_local: .2, duration_seconds: .7 }],
  },
  {
    narration: 'The image gives the viewer a quiet moment.', type: 'SmartImage', variant: 'full_bleed_cinematic', frame: 'media_only',
    props: {}, media: { type: 'image', shots: [
      { path: imageFixture, type: 'image', shot_start: 0, shot_end: 1.1, duration: 1.1, verification_status: 'accepted' },
      { path: imageFixtureAlt, type: 'image', shot_start: 1.1, shot_end: 2.05, duration: .95, verification_status: 'accepted', camera: 'push_in' },
      { path: imageFixtureClose, type: 'image', shot_start: 2.05, shot_end: 3, duration: .95, verification_status: 'accepted', camera: 'pan_left' },
    ] },
    cues: [{ element: 'image', action: 'pan', anchor_text: 'image', time_local: .25, duration_seconds: .65 }],
  },
  {
    narration: 'The report identifies the route.', type: 'DocumentHighlight', variant: 'yellow_marker_highlight', frame: 'evidence',
    props: { source_capture: { status: 'verified_capture', verification_status: 'exact_passage_verified', exact_excerpt: 'The report identifies the route.', image_path: evidenceFixture, article_title: 'Report', domain: 'example.test' } },
    cues: [{ element: 'source', action: 'wipe', anchor_text: 'report', time_local: .2, duration_seconds: .42 }],
  },
  {
    narration: 'The totals rank 8, 5, and 3.', type: 'Chart', variant: 'ranked_horizontal_bars', frame: 'data',
    props: { data: [{ label: 'First', value: '8' }, { label: 'Second', value: '5' }, { label: 'Third', value: '3' }] },
    cues: [{ element: 'data', action: 'wipe', anchor_text: 'totals', time_local: .1, duration_seconds: .85 }],
  },
  {
    narration: 'The shares are 40 percent, 35 percent, and 25 percent.', type: 'Chart', variant: 'donut_distribution', frame: 'data',
    props: { data: [{ label: 'North', value: '40%' }, { label: 'South', value: '35%' }, { label: 'West', value: '25%' }] },
    cues: [{ element: 'data', action: 'trace', anchor_text: 'shares', time_local: .1, duration_seconds: .85 }],
  },
  {
    narration: 'The trend moved from 2 to 5 to 9.', type: 'Chart', variant: 'trend_line_with_markers', frame: 'data',
    props: { data: [{ label: '2010', value: '2' }, { label: '2015', value: '5' }, { label: '2020', value: '9' }] },
    cues: [{ element: 'data', action: 'trace', anchor_text: 'trend', time_local: .1, duration_seconds: .85 }],
  },
  {
    narration: 'The number changed from 20 to 80.', type: 'Chart', variant: 'before_after_delta', frame: 'data',
    props: { before: { label: 'Before', value: '20' }, after: { label: 'After', value: '80' } },
    cues: [{ element: 'data', action: 'reveal', anchor_text: '20 to 80', time_local: .1, duration_seconds: .85 }],
  },
  {
    narration: 'A second map view locates Japan and Osaka.', type: 'AnimatedMap', variant: 'country_spotlight', frame: 'map',
    props: { map_style: 'blueprint_slate', country: 'Japan', location: 'Osaka', map_shot: { enabled: true, location_names: ['Japan', 'Osaka'], feature_ids: [], route_id: '', source_ids: [], motion_profile: 'city_reveal' } },
    cues: [{ element: 'map', action: 'pin', anchor_text: 'Osaka', time_local: .35, duration_seconds: .42 }],
  },
  {
    narration: 'The ship measured 882 feet in length.', type: 'StatCard', variant: 'hero_counter_large', frame: 'data',
    props: { value: 882, suffix: ' FEET', label: 'LENGTH' }, media: { type: 'image', src: imageFixture },
    cues: [{ element: 'data', action: 'count', anchor_text: '882 feet', time_local: .2, duration_seconds: .7 }],
  },
  {
    narration: 'The ship measured 882 feet in length.', type: 'StatCard', variant: 'stat_with_context', frame: 'data',
    props: { value: 882, suffix: ' FEET', label: '882 feet in length' }, media: { type: 'image', src: imageFixtureAlt },
    cues: [{ element: 'data', action: 'count', anchor_text: '882 feet', time_local: .2, duration_seconds: .7 }],
  },
  {
    narration: 'The vote split was 60 percent yes and 40 percent no.', type: 'StatCard', variant: 'percentage_ring', frame: 'data',
    props: { value: 60, suffix: '%', label: 'yes' },
    cues: [{ element: 'data', action: 'count', anchor_text: '60 percent', time_local: .2, duration_seconds: .7 }],
  },
  {
    narration: 'The vote split was 60 percent yes and 40 percent no.', type: 'Chart', variant: 'ranked_horizontal_bars', frame: 'data',
    templateId: 'generated-recipe',
    recipe: { version: 2, id: 'smoke-vote-split', name: 'Vote split', layers: [
      recipeLayer('chart', { x: 13, y: 19, width: 74, height: 62, data: [
        { label: 'yes', value: 60 }, { label: 'no', value: 40 },
      ] }),
    ] },
    props: { data: [{ label: 'yes', value: '60' }, { label: 'no', value: '40' }] },
    cues: [{ element: 'data', action: 'reveal', anchor_text: '60 percent', time_local: .2, duration_seconds: .7 }],
  },
  {
    narration: 'Japan narrows to Osaka.', type: 'AnimatedMap', variant: 'country_spotlight', frame: 'map',
    templateId: 'generated-recipe',
    recipe: { version: 2, id: 'smoke-map-reveal', name: 'Map reveal', layers: [
      recipeLayer('map', { x: 8, y: 8, width: 84, height: 84, motion: 'fade' }),
    ] },
    props: { country: 'Japan', location: 'Osaka', map_shot: { enabled: true, location_names: ['Japan', 'Osaka'], feature_ids: [], route_id: '', source_ids: [], motion_profile: 'city_reveal' } },
    cues: [{ element: 'map', action: 'pin', anchor_text: 'Osaka', time_local: .35, duration_seconds: .42 }],
  },
  {
    narration: 'Ocean heat shifts currents, and currents carry warmth toward the coast.', type: 'Diagram', variant: 'cause_and_effect_flow', frame: 'diagram',
    templateId: 'generated-recipe',
    recipe: { version: 2, id: 'smoke-science-flow', name: 'Ocean heat mechanism', layers: [
      recipeLayer('diagram', { data: [{ label: 'Ocean heat' }, { label: 'Currents' }, { label: 'Warmth toward the coast' }], links: [{ from: 'Ocean heat', to: 'Currents', evidence: 'Ocean heat shifts currents' }, { from: 'Currents', to: 'Warmth toward the coast', evidence: 'currents carry warmth toward the coast' }] }),
      recipeLayer('line', { x: 8, y: 88, width: 84, height: .4, color: '#70C5C4', motion: 'draw' }),
    ] },
    props: { steps: [{ desc: 'Ocean heat' }, { desc: 'Currents' }, { desc: 'Warmth toward the coast' }],
      node_copy_contract: { source: 'llm_scene_repair', approved: true, nodes: [
        { label: 'Ocean heat', source_span: 'Ocean heat' },
        { label: 'Currents', source_span: 'Currents' },
        { label: 'Warmth toward the coast', source_span: 'Warmth toward the coast' },
      ] } },
    cues: [{ element: 'steps', action: 'trace', anchor_text: 'currents carry warmth', time_local: .2, duration_seconds: .75 }],
  },
  {
    narration: 'The liner entered service in 1909, sailed in 1912, and the wreck was found in 1985.', type: 'Timeline', variant: 'horizontal_milestone_trail', frame: 'data',
    templateId: 'generated-recipe',
    recipe: { version: 2, id: 'smoke-history-timeline', name: 'Three dates, three turns', layers: [
      recipeLayer('timeline', { x: 8, y: 25, width: 84, height: 52, color: '#E59E63', data: [{ label: 'entered service', value: 1909 }, { label: 'sailed', value: 1912 }, { label: 'wreck was found', value: 1985 }] }),
    ] },
    props: { events: [{ year: '1909', label: 'entered service' }, { year: '1912', label: 'sailed' }, { year: '1985', label: 'wreck was found' }] },
    cues: [{ element: 'steps', action: 'trace', anchor_text: 'wreck was found in 1985', time_local: .5, duration_seconds: .7 }],
  },
  {
    narration: 'The first trial recorded 12 units; the second trial recorded 19 units.', type: 'Comparison', variant: 'dual_metric_comparison', frame: 'data',
    templateId: 'generated-recipe',
    recipe: { version: 2, id: 'smoke-lab-comparison', name: 'Measured trial split', layers: [
      recipeLayer('comparison', { data: [{ label: 'First trial', value: 12 }, { label: 'Second trial', value: 19 }], color: '#8FC5A8', shape: 'arch' }),
    ] },
    props: { leftLabel: 'First trial', leftValue: '12', rightLabel: 'Second trial', rightValue: '19' },
    cues: [{ element: 'comparison', action: 'split', anchor_text: '12 units; the second trial recorded 19 units', time_local: .4, duration_seconds: .65 }],
  },
  {
    narration: 'Different views of the ship show its scale and construction.', type: 'Collage', variant: 'archive_contact_sheet', frame: 'media_only',
    templateId: 'generated-recipe',
    recipe: { version: 2, id: 'smoke-media-montage', name: 'Three ship views', layers: [
      recipeLayer('media_montage', { x: 11, y: 12, width: 78, height: 76, motion: 'wipe', focal_x: 60, focal_y: 42 }),
    ] },
    media: { type: 'image', shots: [
      { path: imageFixture, type: 'image', shot_start: 0, shot_end: 1, label: 'Ship profile' },
      { path: imageFixtureAlt, type: 'image', shot_start: 1, shot_end: 2, label: 'Deck detail' },
      { path: imageFixtureClose, type: 'image', shot_start: 2, shot_end: 3, label: 'Hull detail' },
      { path: imageFixture, type: 'image', shot_start: 3, shot_end: 4, label: 'Duplicate ignored' },
    ] },
    props: {}, cues: [{ element: 'image', action: 'wipe', anchor_text: 'views of the ship', time_local: .2, duration_seconds: .6 }],
  },
  {
    narration: 'The source photograph identifies the captain.', type: 'PersonProfile', variant: 'portrait_full_bleed', frame: 'portrait',
    templateId: 'generated-recipe',
    recipe: { version: 2, id: 'smoke-portrait-frame', name: 'Portrait frame system', layers: [
      recipeLayer('image', { x: 4, y: 8, width: 37, height: 84, shape: 'arch', focal_x: 54, focal_y: 28, motion: 'parallax' }),
      recipeLayer('rect', { x: 43, y: 18, width: 49, height: 62, color: '#F2C94C', motion: 'fade' }),
    ] },
    props: { name: 'The captain' }, media: { type: 'image', src: imageFixture },
    cues: [{ element: 'image', action: 'pan', anchor_text: 'source photograph', time_local: .3, duration_seconds: .65 }],
  },
  {
    narration: 'The archive page records the route.', type: 'DocumentHighlight', variant: 'magnifier_zoom_lens', frame: 'evidence',
    props: { source_capture: { status: 'verified_capture', verification_status: 'exact_passage_verified', exact_excerpt: 'The archive page records the route.', image_path: evidenceFixture, article_title: 'Archive Record', domain: 'records.example.test' } },
    cues: [{ element: 'source', action: 'focus', anchor_text: 'archive page', time_local: .2, duration_seconds: .55 }],
  },
  {
    narration: 'The route changed the story.', type: 'ChapterTitle', variant: 'technical_timecode_chapter', frame: 'media_only',
    props: { title: 'The route changed the story', kicker: 'THE TURNING POINT' },
    copy: 'The route changed the story',
    cues: [{ element: 'copy', action: 'reveal', anchor_text: 'route changed', time_local: .1, duration_seconds: .7 }],
  },
];

cases.push({
  narration: 'The first trial measured minus 3 units, the baseline measured zero, and the final trial measured 9 units.',
  type: 'Chart', variant: 'editorial_bar', frame: 'data',
  props: { data: [{ label: 'First trial', value: '-3' }, { label: 'Baseline', value: '0' }, { label: 'Final trial', value: '9' }] },
  cues: [{ element: 'data', action: 'reveal', anchor_text: 'minus 3 units', time_local: .1, duration_seconds: .7 }],
});

cases.push({
  narration: 'Declassified records from the national archive confirm the operation.',
  type: 'DeclassifiedDocument', variant: 'default', templateId: 'declassified-document', frame: 'evidence',
  props: { title: 'OPERATION CHASTISE // 1943 BOMBER LOG', classification: 'TOP SECRET // DECLASSIFIED RECORD', stampText: 'DECLASSIFIED' },
  copy: 'OPERATION CHASTISE',
  cues: [{ element: 'copy', action: 'reveal', anchor_text: 'records', time_local: .2, duration_seconds: .6 }],
});

cases.push({
  narration: 'The aircraft charted a low-altitude route across the North Sea.',
  type: 'IsometricMap', variant: 'default', templateId: 'isometric-map', frame: 'map',
  props: { title: 'TRANSIT FLIGHT TELEMETRY', origin: 'RAF SCAMPTON', destination: 'MÖHNE DAM' },
  cues: [{ element: 'map', action: 'pin', anchor_text: 'route', time_local: .3, duration_seconds: .5 }],
});

cases.push({
  narration: 'Investigators connected the surveillance photos to the declassified manifest.',
  type: 'EvidencePinboard', variant: 'default', templateId: 'evidence-pinboard', frame: 'evidence',
  media: { type: 'image', src: imageFixture },
  props: { title: 'FORENSIC INVESTIGATION BOARD' },
  copy: 'PRIMARY INCIDENT RECORD',
  cues: [{ element: 'evidence', action: 'focus', anchor_text: 'surveillance', time_local: .25, duration_seconds: .6 }],
});

cases.push({
  narration: 'The locomotive chassis was engineered to exacting Third-Angle tolerances.',
  type: 'TechnicalBlueprint', variant: 'default', templateId: 'technical-blueprint', frame: 'diagram',
  props: { title: 'HIGH SPEED TRANSIT CHASSIS', specNumber: 'SPEC-DWG-1944' },
  cues: [{ element: 'blueprint', action: 'trace', anchor_text: 'chassis', time_local: .2, duration_seconds: .7 }],
});

cases.push({
  narration: 'This was the critical turning point in the campaign.',
  type: 'DepthTypography', variant: 'default', templateId: 'depth-typography', frame: 'media_only',
  props: { text: 'TURNING POINT', kicker: 'CRITICAL MOMENT' },
  copy: 'TURNING POINT',
  cues: [{ element: 'copy', action: 'reveal', anchor_text: 'turning point', time_local: .1, duration_seconds: .8 }],
});

cases.push({
  narration: 'The field commander directed the advance personally.',
  type: 'SmartImage', variant: 'parallax_float', frame: 'portrait',
  media: { type: 'image', src: imageFixture, cutout_src: cutoutFixture },
  props: { variant: 'parallax_float', cutout_src: cutoutFixture },
  copy: 'FIELD COMMANDER',
  cues: [{ element: 'image', action: 'pan', anchor_text: 'commander', time_local: .2, duration_seconds: .6 }],
});

cases.push({
  narration: 'No historical footage survived the warehouse fire of 1945.',
  type: 'SmartImage', variant: 'full_bleed_cinematic', frame: 'media_only',
  media: { type: 'image', src: '' },
  props: {},
  copy: 'ARCHIVAL DOSSIER',
  cues: [{ element: 'image', action: 'pan', anchor_text: 'warehouse', time_local: .2, duration_seconds: .5 }],
});

const scenes = cases.map((item, index) => {
  const from = index * 90;
  const copy = item.copy || '';
  const soundFixtures = {
    0: ['sound-effects/vidzeo/route_ping.wav', .35],
    1: ['sound-effects/vidzeo/cash_counter.wav', .4],
    2: ['sound-effects/vidzeo/technical_tick.wav', .25],
    4: ['sound-effects/edit/edit_slider_move.wav', .2],
  };
  const sound = soundFixtures[index];
  return {
    id: `adaptive-smoke-${index}`, scene_index: index, from, start: index * 3, duration: 3, duration_in_frames: 90,
    ...(item.visualRole ? { visual_role_contract_version: 2, visual_role: item.visualRole } : {}),
    narration: item.narration, director_treatment_enabled: true,
    primary_visual: { type: item.type, variant: item.variant, props: item.props },
    map_shot: item.props?.map_shot || {},
    media: item.media || {}, graphic: { template_id: item.templateId || 'full-bleed', props: item.props, scene_local_start: 0, scene_local_end: 3 },
    template_recipe: item.recipe || {}, visual_dna: item.visualDNA || undefined,
    text: { frame_mode: item.frame, overlay_policy: copy ? 'single_short_highlight' : 'none', position: copy ? 'top_left' : 'none' },
    editorial_contract: copy ? { version: 4, source: 'llm', approved: true, display_text: copy, source_span: copy, emphasis_spans: [{ start: 0, end: copy.length }] } : {},
    motion_cues: item.cues, camera: { motion: index % 2 ? 'pan_right' : 'slow_push' }, transition: { template_id: 'cut', duration: .12 },
    sound_cues: sound && fs.existsSync(path.join(root, 'public', sound[0]))
      ? [{ src: sound[0], time_local: sound[1], duration: .7, volume: .3 }] : [],
  };
});
const manifest = { version: 4, fps: 30, width: 1280, height: 720, duration: scenes.length * 3, duration_in_frames: scenes.length * 90, visual_dna: dna, scenes };

async function main() {
  const lowQuality = process.argv.includes('--low-quality');
  const stillScale = lowQuality ? 0.42 : 0.75;
  const videoScale = lowQuality ? 0.45 : 1;
  const publicDir = stageRendererPublicAssets(path.join(root, 'public'), os.tmpdir());
  let browser;
  try {
  const serveUrl = await bundle({ entryPoint: path.join(root, 'remotion', 'entry.jsx'), publicDir, webpackOverride: (config) => config });
  const inputProps = { manifest };
  browser = await openBrowser('chrome');
  const composition = await selectComposition({ serveUrl, id: 'VidzeoDocumentary', inputProps, puppeteerInstance: browser });
  const overlayIndex = cases.findIndex((item) => item.roleTag === 'diagram_media_overlay');
  const frames = (process.argv.includes('--quick') ? [133, 223, 493, 1578, 2118, ...(overlayIndex >= 0 ? [overlayIndex * 90 + 48] : [])]
    : [13, 48, 77, 103, 133, 168, 223, 313, 403, 465, 493, 525, 583,
      ...cases.slice(7).map((_, index) => (index + 7) * 90 + 48)])
    .filter((frame) => frame >= 0 && frame < manifest.duration_in_frames);
  for (const frame of frames) {
    const target = path.join(output, `frame-${String(frame).padStart(3, '0')}.png`);
    await renderStill({ composition, serveUrl, output: target, inputProps, frame, scale: stillScale, puppeteerInstance: browser });
    console.log(target);
  }
  if (process.argv.includes('--video')) {
    const target = path.join(output, 'adaptive-documentary-smoke.mp4');
    // Include the opening graphics and multi-shot sequence, so this checks the
    // cross-cut path as well as the individual still-frame catalog.
    await renderMedia({ composition, serveUrl, codec: 'h264', outputLocation: target, inputProps, frameRange: [0, 539], scale: videoScale, concurrency: 1, x264Preset: 'ultrafast', puppeteerInstance: browser });
    console.log(target);
  }
  } finally {
    if (browser) await browser.close({ silent: true });
    fs.rmSync(publicDir, { recursive: true, force: true });
  }
}

main().then(() => { process.exit(0); }).catch((error) => { console.error(error); process.exit(1); });
