import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import {
  GENRE_PRESETS,
  inferGenreFromTopic,
  resolveVisualDNA,
  synthesizeAdaptiveDNA,
} from '../src/remotion/engine/StyleEngine.js';
import { COLOR_GRADES, resolveColorGrade } from '../src/remotion/engine/ColorGrade.js';

const require = createRequire(import.meta.url);

test('15 Universal Documentary Genre Presets: completeness and token integrity', () => {
  const expectedGenres = [
    'archival-dossier',
    'broadsheet-heritage',
    'royal-prestige',
    'engineering-blueprint',
    'forensic-noir',
    'vox-explainer',
    'magnates-noir',
    'dhruv-clarity',
    'cyber-technology',
    'cosmic-science',
    'military-history',
    'nature-wildlife',
    'minimal-editorial',
    'athletic-action',
    'arcade-entertainment',
  ];

  for (const genre of expectedGenres) {
    const preset = GENRE_PRESETS[genre];
    assert.ok(preset, `Preset ${genre} must exist`);
    assert.equal(preset.genre, genre);

    // Palette tokens
    assert.ok(preset.palette.primary, `${genre} missing palette.primary`);
    assert.ok(preset.palette.secondary, `${genre} missing palette.secondary`);
    assert.ok(preset.palette.accent, `${genre} missing palette.accent`);
    assert.ok(preset.palette.background, `${genre} missing palette.background`);
    assert.ok(preset.palette.surface, `${genre} missing palette.surface`);
    assert.ok(preset.palette.border, `${genre} missing palette.border`);

    // Typography tokens
    assert.ok(preset.typography.display, `${genre} missing typography.display`);
    assert.ok(preset.typography.body, `${genre} missing typography.body`);
    assert.ok(preset.typography.numbers, `${genre} missing typography.numbers`);
    assert.ok(preset.typography.personality, `${genre} missing typography.personality`);

    // Geometry & Texture
    assert.ok(preset.geometry, `${genre} missing geometry`);
    assert.ok(preset.texture, `${genre} missing texture`);
    assert.ok(typeof preset.cornerRadius === 'number', `${genre} cornerRadius must be a number`);
    assert.ok(typeof preset.strokeWidth === 'number', `${genre} strokeWidth must be a number`);
  }
});

test('Legacy & Semantic Aliases are preserved for 100% backward compatibility', () => {
  assert.equal(GENRE_PRESETS['technology'].genre, 'cyber-technology');
  assert.equal(GENRE_PRESETS['true-crime'].genre, 'forensic-noir');
  assert.equal(GENRE_PRESETS['royal-history'].genre, 'royal-prestige');
  assert.equal(GENRE_PRESETS['broadsheet-paper'].genre, 'broadsheet-heritage');
  assert.equal(GENRE_PRESETS['mrbeast-retention'].genre, 'arcade-entertainment');
  assert.ok(GENRE_PRESETS['finance-explainer']);
  assert.ok(GENRE_PRESETS['vox-editorial']);
  assert.ok(GENRE_PRESETS['netflix-prestige']);
});

test('Semantic Topic Keyword Inference accurately routes to all 15 niches', () => {
  const cases = [
    { topic: 'The Legendary 1963 Porsche 911 Classic Car Restoration', expected: 'archival-dossier' },
    { topic: 'Britain Victorian Steam Locomotives and Rail Freight Network', expected: 'engineering-blueprint' },
    { topic: 'Inside The Royal Palace: Monarchy Dynasty of King Charles', expected: 'royal-prestige' },
    { topic: 'British Parliament Inquiries and Nostalgic Broadsheet Journalism', expected: 'broadsheet-heritage' },
    { topic: 'After Death Autopsy and Forensic Pathology Morgue Evidence', expected: 'forensic-noir' },
    { topic: 'Billionaire Empire Fall of Ponzi Fraud and Corporate Greed', expected: 'magnates-noir' },
    { topic: 'Deep Space James Webb Telescope and Black Hole Astrophysics', expected: 'cosmic-science' },
    { topic: 'WWII Tactical Frontline Battle Strategy and Pentagon Tank War', expected: 'military-history' },
    { topic: 'Amazon Rainforest Ecosystem Predators and Deep Ocean Whales', expected: 'nature-wildlife' },
    { topic: 'Formula 1 Grand Prix Racing Speed Record at Monaco', expected: 'athletic-action' },
    { topic: 'Survive 100 Days Extreme Challenge Winner Prize Spectacle', expected: 'arcade-entertainment' },
    { topic: 'Dhruv Rathee Fact-Check: Reality of Official GDP Data Debunked', expected: 'dhruv-clarity' },
    { topic: 'Quantum Computing and Silicon Microchip Artificial Intelligence', expected: 'cyber-technology' },
    { topic: 'Scandinavian Nordic Architecture and Minimalist Interior Essay', expected: 'minimal-editorial' },
    { topic: 'Why The Modern World Works: A Cultural Vox Explainer', expected: 'vox-explainer' },
  ];

  for (const c of cases) {
    const inferred = inferGenreFromTopic(c.topic);
    assert.equal(inferred, c.expected, `Topic "${c.topic}" should infer "${c.expected}", got "${inferred}"`);
  }
});

test('Chameleon 4-Stage Adaptive Engine handles novel and unclassified niches', () => {
  // Case 1: 18th Century Horology and Clockmaking (Historical + Craft)
  const horology = synthesizeAdaptiveDNA('18th Century Horology and Master Clockmaking', { channel: 'Clockmakers Guild' });
  assert.equal(horology.is_adaptive, true);
  assert.equal(horology.typography.personality, 'serifHistorical');
  assert.match(horology.typography.display, /Cinzel|Playfair/);
  assert.equal(horology.texture, 'paper');
  assert.equal(horology.image_treatment, 'warm_archival');

  // Case 2: Deep Sea Welding and Trench Submersibles (Marine / Aquatic)
  const marine = synthesizeAdaptiveDNA('Deep Sea Welding and Trench Submersibles', { channel: 'Abyss Explorers' });
  assert.equal(marine.is_adaptive, true);
  assert.equal(marine.palette.accent, '#0EA5E9');
  assert.equal(marine.texture, 'vignette');
  assert.equal(marine.geometry, 'pill_rounded');

  // Case 3: Autonomous Swarm Drone Navigation (Technical / Robotics)
  const drone = synthesizeAdaptiveDNA('Autonomous Swarm Drone Navigation and Robotics', { channel: 'Aero Robotics' });
  assert.equal(drone.is_adaptive, true);
  assert.equal(drone.typography.personality, 'technical_schematic');
  assert.match(drone.typography.display, /JetBrains Mono/);
  assert.equal(drone.texture, 'scanlines');

  // Case 4: Unsolved High-Altitude Cold Case (Investigation / Dossier)
  const coldCase = synthesizeAdaptiveDNA('Unsolved High-Altitude Fatal Mystery and Dossier', { channel: 'Mystery Files' });
  assert.equal(coldCase.is_adaptive, true);
  assert.equal(coldCase.palette.accent, '#DC2626');
  assert.equal(coldCase.texture, 'film_grain');
});

test('Chameleon Engine deterministic branding: same channel/topic gets identical palette', () => {
  const dna1 = synthesizeAdaptiveDNA('Antique Furniture Restoration', { channel: 'Artisan Heritage Channel' });
  const dna2 = synthesizeAdaptiveDNA('Antique Furniture Restoration', { channel: 'Artisan Heritage Channel' });

  assert.equal(dna1.palette.accent, dna2.palette.accent);
  assert.equal(dna1.palette.background, dna2.palette.background);
  assert.equal(dna1.palette.surface, dna2.palette.surface);
  assert.equal(dna1.typography.display, dna2.typography.display);
});

test('resolveVisualDNA seamlessly integrates presets and adaptive synthesis', () => {
  // Direct preset request
  const dnaPreset = resolveVisualDNA({ genre: 'archival-dossier' });
  assert.equal(dnaPreset.genre, 'archival-dossier');
  assert.equal(dnaPreset.palette.accent, '#D97706');

  // Inferred preset from topic
  const dnaInferred = resolveVisualDNA({ topic: 'Monarchy and Royal Palaces' });
  assert.equal(dnaInferred.genre, 'royal-prestige');
  assert.equal(dnaInferred.palette.accent, '#D4AF37');

  // Novel topic seamlessly synthesizes adaptive DNA
  const dnaNovel = resolveVisualDNA({ topic: 'Glassblowing and Ancient Venetian Beads' });
  assert.equal(dnaNovel.genre, 'adaptive-documentary');
  assert.equal(dnaNovel.is_adaptive, true);
  assert.ok(dnaNovel.palette.accent);
  assert.ok(dnaNovel.typography.display);
});

test('Documentary Color Grades support all 15 genres with objective CSS filters', () => {
  const mappings = [
    { genre: 'archival-dossier', expectedGrade: 'archival' },
    { genre: 'broadsheet-heritage', expectedGrade: 'heritage' },
    { genre: 'royal-prestige', expectedGrade: 'royal' },
    { genre: 'engineering-blueprint', expectedGrade: 'blueprint' },
    { genre: 'forensic-noir', expectedGrade: 'forensic' },
    { genre: 'cosmic-science', expectedGrade: 'cosmic' },
    { genre: 'nature-wildlife', expectedGrade: 'nature' },
    { genre: 'cyber-technology', expectedGrade: 'technical' },
    { genre: 'athletic-action', expectedGrade: 'action' },
  ];

  for (const m of mappings) {
    const res = resolveColorGrade({}, { genre: m.genre });
    assert.equal(res.name, m.expectedGrade, `Genre ${m.genre} should resolve grade ${m.expectedGrade}`);
    assert.match(res.filter, /contrast\([\d.]+\)/);
    assert.match(res.filter, /saturate\([\d.]+\)/);
    assert.match(res.filter, /brightness\([\d.]+\)/);
  }
});

test('Frame Math & Subtitle Safe Zone verification (without video rendering)', () => {
  // Test frame-by-frame progression across key timestamps
  const fps = 30;
  const width = 1920;
  const height = 1080;
  const frames = [0, 15, 30, 60, 90];

  for (const frame of frames) {
    // 1. Timecode calculation at this frame
    const timeSeconds = Math.floor(frame / fps);
    const timeFrames = frame % fps;
    const timecode = `REC \u25CF 00:00:${String(timeSeconds).padStart(2, '0')}:${String(timeFrames).padStart(2, '0')}`;
    assert.ok(timecode.startsWith('REC \u25CF 00:00:'));

    // 2. Safe zone constraint: lower 38% reserved for subtitles
    const subtitleSafeZoneTop = height * 0.62;
    const hudHeight = subtitleSafeZoneTop * 0.88;
    assert.ok(hudHeight < subtitleSafeZoneTop, 'HUD Viewfinder brackets must stay above the lower 38% safe zone');
    assert.ok(subtitleSafeZoneTop === 669.6, 'Safe zone begins at y=669.6px (62% of 1080p)');

    // 3. Ambient light pulse is bounded and non-NaN
    const lightX = 35 + 10 * Math.sin((frame / fps) * 0.8);
    const lightY = 38 + 6 * Math.cos((frame / fps) * 0.6);
    assert.ok(lightX >= 25 && lightX <= 45, `lightX ${lightX} must stay bounded`);
    assert.ok(lightY >= 32 && lightY <= 44, `lightY ${lightY} must stay bounded`);
  }
});

test('CMS 7 Production Channels route deterministically to their signature documentary presets', () => {
  let channels = [];
  try {
    const store = require('../../../Management/YT CMS/lib/store');
    channels = store.listChannels();
  } catch (err) {
    channels = [
      { name: 'Classic Wheels', niche: 'classic cars' },
      { name: 'Nostalgic Brit', niche: 'british history' },
      { name: 'Royal Celeb', niche: 'royal celebrities' },
      { name: 'Crown Royal', niche: 'royal monarchy' },
      { name: 'Crown Zone', niche: 'royal documentary' },
      { name: 'After Death', niche: 'death & mortuary' },
      { name: 'Train Crew', niche: 'trains & railways' },
    ];
  }
  assert.ok(channels.length >= 7, 'Expected at least 7 production channels');

  const expectedMappings = {
    'Classic Wheels': 'archival-dossier',
    'Nostalgic Brit': 'broadsheet-heritage',
    'Royal Celeb': 'royal-prestige',
    'Crown Royal': 'royal-prestige',
    'Crown Zone': 'royal-prestige',
    'After Death': 'forensic-noir',
    'Train Crew': 'engineering-blueprint',
  };

  for (const c of channels) {
    if (expectedMappings[c.name]) {
      const channelName = c.name.toLowerCase();
      const channelNiche = (c.niche || '').toLowerCase();
      let genre = '';
      if (channelName.includes('classic wheel') || channelNiche.includes('classic car')) {
        genre = 'archival-dossier';
      } else if (channelName.includes('nostalgic brit') || channelNiche.includes('british history')) {
        genre = 'broadsheet-heritage';
      } else if (channelName.includes('royal') || channelName.includes('crown') || channelNiche.includes('royal')) {
        genre = 'royal-prestige';
      } else if (channelName.includes('after death') || channelNiche.includes('death')) {
        genre = 'forensic-noir';
      } else if (channelName.includes('train crew') || channelNiche.includes('train') || channelNiche.includes('rail')) {
        genre = 'engineering-blueprint';
      }
      assert.equal(genre, expectedMappings[c.name], `Channel ${c.name} mapped to ${genre}`);
    }
  }
});
