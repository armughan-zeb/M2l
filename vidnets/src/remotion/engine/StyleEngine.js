import React from 'react';
import { AbsoluteFill } from 'remotion';
import { rgba } from '../theme.js';

/**
 * 15 Universal Documentary Genre Presets.
 * Provides instant visual DNA differentiation across documentary styles:
 * 1.  archival-dossier: Classic motoring, vintage aviation, mechanical archives (Amber/ochre, technical grid, dust & scratch)
 * 2.  broadsheet-heritage: British history, Victorian/Edwardian heritage, colonial diplomacy (Aged newsprint, serif, paper texture)
 * 3.  royal-prestige: Monarchy, dynasties, palaces, royal intrigue (Velvet espresso, antique gold, classical serif, vignette)
 * 4.  engineering-blueprint: Railways, locomotives, mega-structures, industrial machinery (Draftsman blue/amber, monospace, scanlines)
 * 5.  forensic-noir: After death, true crime, cold cases, forensic pathology (Ink black, crimson tape, bold sans, film grain)
 * 6.  vox-explainer: Fast-paced modern explainers, social dynamics, cultural phenomena (Canary yellow, brutalist sharp, clean)
 * 7.  magnates-noir: Billionaires, corporate greed, empire falls, Wall Street corruption (Dark slate, gold foil, luxury serif, clean)
 * 8.  dhruv-clarity: Factual journalism, civic education, data analysis, truth vs myth (Clean navy, vibrant yellow, modern grotesk)
 * 9.  cyber-technology: AI, semiconductors, surveillance, cybersecurity, robotics (Cyber slate, electric cyan, technical HUD)
 * 10. cosmic-science: Astrophysics, quantum mechanics, deep space, speculative science (Deep space black, nebula indigo/ultraviolet, circular radar)
 * 11. military-history: Tactical warfare, battle lines, covert ops, defense logistics (Gunmetal/olive, tactical orange, crosshair, subtle noise)
 * 12. nature-wildlife: Ecosystems, deep oceans, biology, environmental shifts (Deep moss/canopy, emerald lime, organic rounded)
 * 13. minimal-editorial: Scandinavian architecture, design philosophy, quiet essays (Stone charcoal, cobalt blue, geometric pill)
 * 14. athletic-action: Motorsports, extreme sports, athletic legends, speed records (Carbon, high-vis crimson, slanted velocity)
 * 15. arcade-entertainment: High-retention spectacles, pop culture, entertainment challenges (High-contrast comic, arcade yellow, punch)
 */
export const GENRE_PRESETS = {
  // 1. Archival Motoring & Mechanical Dossier (e.g. Classic Wheels)
  'archival-dossier': {
    genre: 'archival-dossier',
    palette: {
      primary: '#F5F5F0',
      secondary: '#B8B5AD',
      accent: '#D97706', // Industrial Amber / Ochre
      background: '#121210',
      surface: '#1C1B17',
      text_contrast: '#FFFFFF',
      border: '#D97706',
      glow: 'rgba(217, 119, 6, 0.4)',
    },
    typography: {
      display: 'Archivo Black, Impact, sans-serif',
      body: 'Inter, DM Sans, sans-serif',
      numbers: 'JetBrains Mono, Roboto Mono, monospace',
      personality: 'industrial_archival',
    },
    geometry: 'technical_grid',
    texture: 'dust_and_scratch',
    cornerRadius: 2,
    strokeWidth: 2,
    image_treatment: 'warm_archival',
  },

  // 2. Broadsheet Heritage & Historical Inquiries (e.g. Nostalgic Brit)
  'broadsheet-heritage': {
    genre: 'broadsheet-heritage',
    palette: {
      primary: '#18181B',
      secondary: '#52525B',
      accent: '#991B1B', // Rich Crimson Stamp
      background: '#F4EFEB', // Aged Newsprint
      surface: '#E6DFD5',
      text_contrast: '#FFFFFF',
      border: '#18181B',
      glow: 'rgba(0, 0, 0, 0.15)',
    },
    typography: {
      display: 'Playfair Display, Georgia, serif',
      body: 'Lora, Merriweather, Georgia, serif',
      numbers: 'Playfair Display, Georgia, serif',
      personality: 'serifEditorial',
    },
    geometry: 'sharp',
    texture: 'paper',
    cornerRadius: 0,
    strokeWidth: 2,
    image_treatment: 'warm_archival',
  },

  // 3. Royal Prestige & Dynastic History (e.g. Royal Celeb, Crown Royal, Crown Zone)
  'royal-prestige': {
    genre: 'royal-prestige',
    palette: {
      primary: '#FDFBF7',
      secondary: '#D4C5B9',
      accent: '#D4AF37', // Imperial Antique Gold
      background: '#0D080E', // Deep Velvet Plum
      surface: '#1A0E18',
      text_contrast: '#FFF9E6',
      border: 'rgba(212, 175, 55, 0.4)',
      glow: 'rgba(212, 175, 55, 0.35)',
    },
    typography: {
      display: 'Cinzel, Georgia, serif',
      body: 'Lora, Inter, Georgia, serif',
      numbers: 'Cinzel, serif',
      personality: 'serifHistorical',
    },
    geometry: 'framed',
    texture: 'vignette',
    cornerRadius: 4,
    strokeWidth: 2,
    image_treatment: 'cinematic_grade',
  },

  // 4. Engineering Blueprint & Industrial Infrastructure (e.g. Train Crew)
  'engineering-blueprint': {
    genre: 'engineering-blueprint',
    palette: {
      primary: '#E0F2FE',
      secondary: '#7DD3FC',
      accent: '#0284C7', // Draftsman Blue
      warning: '#F59E0B', // Safety Amber
      background: '#081726',
      surface: '#0C233B',
      text_contrast: '#FFFFFF',
      border: '#38BDF8',
      glow: 'rgba(56, 189, 248, 0.35)',
    },
    typography: {
      display: 'JetBrains Mono, Roboto Mono, monospace',
      body: 'Inter, sans-serif',
      numbers: 'JetBrains Mono, monospace',
      personality: 'technical_schematic',
    },
    geometry: 'blueprint_cad',
    texture: 'scanlines',
    cornerRadius: 0,
    strokeWidth: 1.5,
    image_treatment: 'cool_modern',
  },

  // 5. Forensic Noir & Investigative Dossier (e.g. After Death, True Crime)
  'forensic-noir': {
    genre: 'forensic-noir',
    palette: {
      primary: '#F8FAFC',
      secondary: '#94A3B8',
      accent: '#DC2626', // Crime Scene Crimson
      background: '#08080A',
      surface: '#141316',
      text_contrast: '#FFFFFF',
      border: '#DC2626',
      glow: 'rgba(220, 38, 38, 0.45)',
    },
    typography: {
      display: 'Archivo Black, Impact, sans-serif',
      body: 'Inter, DM Sans, sans-serif',
      numbers: 'JetBrains Mono, monospace',
      personality: 'dossier_classified',
    },
    geometry: 'sharp',
    texture: 'film_grain',
    cornerRadius: 0,
    strokeWidth: 3,
    image_treatment: 'desaturated_contrast',
  },

  // 6. Vox Explainer (Fast-paced social & cultural explainers)
  'vox-explainer': {
    genre: 'vox-explainer',
    palette: {
      primary: '#FFFFFF',
      secondary: '#E2E8F0',
      accent: '#FACC15', // Canary Yellow
      background: '#0F172A',
      surface: '#1E293B',
      text_contrast: '#000000',
      border: '#FACC15',
      glow: 'rgba(250, 204, 21, 0.45)',
    },
    typography: {
      display: 'Archivo Black, Plus Jakarta Sans, sans-serif',
      body: 'Inter, DM Sans, sans-serif',
      numbers: 'DM Sans, Inter, sans-serif',
      personality: 'condensed',
    },
    geometry: 'brutalist_sharp',
    texture: 'clean',
    cornerRadius: 0,
    strokeWidth: 3,
    image_treatment: 'cinematic_grade',
  },

  // 7. Magnates Noir (Business scandals, corporate empires, financial empires)
  'magnates-noir': {
    genre: 'magnates-noir',
    palette: {
      primary: '#FFFFFF',
      secondary: '#94A3B8',
      accent: '#D4AF37', // Gold Foil
      danger: '#DC2626',
      background: '#08080A',
      surface: '#141316',
      text_contrast: '#D4AF37',
      border: 'rgba(212, 175, 55, 0.4)',
      glow: 'rgba(212, 175, 55, 0.4)',
    },
    typography: {
      display: 'Cinzel, Montserrat, Bebas Neue, serif',
      body: 'Plus Jakarta Sans, Inter, sans-serif',
      numbers: 'JetBrains Mono, Montserrat, sans-serif',
      personality: 'luxury_cinematic',
    },
    geometry: 'editorial_fine',
    texture: 'clean',
    cornerRadius: 4,
    strokeWidth: 2,
    image_treatment: 'cinematic_grade',
  },

  // 8. Dhruv Clarity (Civic truth vs myth, data explainers, fact checking)
  'dhruv-clarity': {
    genre: 'dhruv-clarity',
    palette: {
      primary: '#FFFFFF',
      secondary: '#CBD5E1',
      accent: '#FFD600', // Vibrant Yellow
      success: '#10B981',
      danger: '#EF4444',
      background: '#0B192C',
      surface: '#1E293B',
      text_contrast: '#000000',
      border: '#FFD600',
      glow: 'rgba(255, 214, 0, 0.35)',
    },
    typography: {
      display: 'Montserrat, Oswald, sans-serif',
      body: 'Inter, DM Sans, Roboto, sans-serif',
      numbers: 'Inter, JetBrains Mono, sans-serif',
      personality: 'modern_grotesk',
    },
    geometry: 'rounded_clean',
    texture: 'clean',
    cornerRadius: 10,
    strokeWidth: 2,
    image_treatment: 'natural',
  },

  // 9. Cyber Technology (AI, semiconductors, surveillance, cybersecurity)
  'cyber-technology': {
    genre: 'cyber-technology',
    palette: {
      primary: '#FFFFFF',
      secondary: '#94A3B8',
      accent: '#00F0FF', // Electric Cyan
      background: '#05070E',
      surface: '#0C101F',
      text_contrast: '#FFFFFF',
      border: 'rgba(0, 240, 255, 0.4)',
      glow: 'rgba(0, 240, 255, 0.4)',
    },
    typography: {
      display: 'Plus Jakarta Sans, Inter, sans-serif',
      body: 'Inter, DM Sans, sans-serif',
      numbers: 'JetBrains Mono, Inter, monospace',
      personality: 'technical',
    },
    geometry: 'sharp',
    texture: 'scanlines',
    cornerRadius: 4,
    strokeWidth: 2,
    image_treatment: 'cool_modern',
  },

  // 10. Cosmic Science & Speculative Physics (Astrophysics, space telescopes, deep time)
  'cosmic-science': {
    genre: 'cosmic-science',
    palette: {
      primary: '#F1F5F9',
      secondary: '#A5B4FC',
      accent: '#818CF8', // Nebula Indigo / Ultraviolet
      background: '#030712',
      surface: '#0B0F19',
      text_contrast: '#FFFFFF',
      border: 'rgba(129, 140, 248, 0.35)',
      glow: 'rgba(129, 140, 248, 0.4)',
    },
    typography: {
      display: 'Plus Jakarta Sans, Montserrat, sans-serif',
      body: 'Inter, DM Sans, sans-serif',
      numbers: 'JetBrains Mono, monospace',
      personality: 'scientific_stellar',
    },
    geometry: 'circular_radar',
    texture: 'vignette',
    cornerRadius: 8,
    strokeWidth: 1.5,
    image_treatment: 'cool_modern',
  },

  // 11. Military History & Tactical Analysis (Frontlines, battle maps, defense doctrine)
  'military-history': {
    genre: 'military-history',
    palette: {
      primary: '#ECEFF1',
      secondary: '#90A4AE',
      accent: '#E65100', // Tactical Hazard Orange
      tactical: '#687F58', // Field Olive
      background: '#101411',
      surface: '#1B211C',
      text_contrast: '#FFFFFF',
      border: 'rgba(230, 81, 0, 0.4)',
      glow: 'rgba(230, 81, 0, 0.35)',
    },
    typography: {
      display: 'Archivo Black, Oswald, sans-serif',
      body: 'Inter, DM Sans, sans-serif',
      numbers: 'JetBrains Mono, monospace',
      personality: 'tactical_field',
    },
    geometry: 'tactical_crosshair',
    texture: 'subtle_noise',
    cornerRadius: 0,
    strokeWidth: 2,
    image_treatment: 'desaturated_contrast',
  },

  // 12. Nature & Wildlife Documentary (Ecosystems, deep ocean, biology)
  'nature-wildlife': {
    genre: 'nature-wildlife',
    palette: {
      primary: '#F7FEE7',
      secondary: '#D9F99D',
      accent: '#84CC16', // Canopy Emerald Lime
      background: '#05130B',
      surface: '#0D2115',
      text_contrast: '#05130B',
      border: 'rgba(132, 204, 22, 0.35)',
      glow: 'rgba(132, 204, 22, 0.3)',
    },
    typography: {
      display: 'Poppins, Montserrat, sans-serif',
      body: 'Open Sans, Inter, sans-serif',
      numbers: 'Poppins, sans-serif',
      personality: 'organic_natural',
    },
    geometry: 'pill_rounded',
    texture: 'clean',
    cornerRadius: 20,
    strokeWidth: 1.5,
    image_treatment: 'natural',
  },

  // 13. Minimal Editorial (Architecture, design philosophy, quiet essays)
  'minimal-editorial': {
    genre: 'minimal-editorial',
    palette: {
      primary: '#FFFFFF',
      secondary: '#CBD5E1',
      accent: '#3B82F6', // Cobalt Blue
      background: '#0A0A0C',
      surface: '#18181B',
      text_contrast: '#FFFFFF',
      border: 'rgba(255, 255, 255, 0.15)',
      glow: 'rgba(59, 130, 246, 0.3)',
    },
    typography: {
      display: 'Poppins, sans-serif',
      body: 'Open Sans, sans-serif',
      numbers: 'Poppins, sans-serif',
      personality: 'geometric',
    },
    geometry: 'pill_rounded',
    texture: 'clean',
    cornerRadius: 24,
    strokeWidth: 1,
    image_treatment: 'natural',
  },

  // 14. Athletic Action & Motorsports (Speed records, racing, athletic drama)
  'athletic-action': {
    genre: 'athletic-action',
    palette: {
      primary: '#FFFFFF',
      secondary: '#E2E8F0',
      accent: '#FF3366', // High-Vis Crimson / Velocity Pink
      background: '#090A0F',
      surface: '#151722',
      text_contrast: '#FFFFFF',
      border: '#FF3366',
      glow: 'rgba(255, 51, 102, 0.45)',
    },
    typography: {
      display: 'Oswald, Archivo Black, sans-serif',
      body: 'Inter, sans-serif',
      numbers: 'Oswald, sans-serif',
      personality: 'velocity_italic',
    },
    geometry: 'slanted_skew',
    texture: 'clean',
    cornerRadius: 4,
    strokeWidth: 3,
    image_treatment: 'cinematic_grade',
  },

  // 15. Arcade Entertainment & Spectacle (Retention challenges, pop spectacles)
  'arcade-entertainment': {
    genre: 'arcade-entertainment',
    palette: {
      primary: '#FFFFFF',
      secondary: '#00F0FF',
      accent: '#FFEE00', // Electric Sun Yellow
      danger: '#FF0033',
      success: '#39FF14',
      background: '#000000',
      surface: '#111111',
      text_contrast: '#000000',
      border: '#000000',
      glow: 'rgba(255, 238, 0, 0.6)',
    },
    typography: {
      display: 'Archivo Black, Anton, Bebas Neue, sans-serif',
      body: 'Inter, Montserrat, sans-serif',
      numbers: 'Archivo Black, Anton, sans-serif',
      personality: 'arcade_punch',
    },
    geometry: 'chunky_comic',
    texture: 'clean',
    cornerRadius: 14,
    strokeWidth: 5,
    image_treatment: 'cinematic_grade',
  },

  // Legacy & Semantic Aliases
  'finance-explainer': {
    genre: 'finance-explainer',
    palette: {
      primary: '#F8FAFC',
      secondary: '#94A3B8',
      accent: '#10B981',
      background: '#070B14',
      surface: '#0F172A',
      text_contrast: '#FFFFFF',
      border: 'rgba(16, 185, 129, 0.35)',
      glow: 'rgba(16, 185, 129, 0.35)',
    },
    typography: {
      display: 'Montserrat, sans-serif',
      body: 'Open Sans, sans-serif',
      numbers: 'Roboto, sans-serif',
      personality: 'neoGrotesk',
    },
    geometry: 'editorial_fine',
    texture: 'clean',
    cornerRadius: 6,
    strokeWidth: 2,
    image_treatment: 'cool_modern',
  },
  'vox-editorial': {
    genre: 'vox-editorial',
    palette: {
      primary: '#FFFFFF',
      secondary: '#E2E8F0',
      accent: '#FACC15',
      background: '#0F172A',
      surface: '#1E293B',
      text_contrast: '#0F172A',
      border: '#FACC15',
      glow: 'rgba(250, 204, 21, 0.45)',
    },
    typography: {
      display: 'Archivo Black, Plus Jakarta Sans, sans-serif',
      body: 'Inter, DM Sans, Open Sans, sans-serif',
      numbers: 'JetBrains Mono, Roboto, monospace',
      personality: 'voxBold',
    },
    geometry: 'brutalist_sharp',
    texture: 'clean',
    cornerRadius: 0,
    strokeWidth: 3,
    image_treatment: 'cinematic_grade',
  },
  'netflix-prestige': {
    genre: 'netflix-prestige',
    palette: {
      primary: '#FFFFFF',
      secondary: '#E8D8C8',
      accent: '#D4A373',
      background: '#0C0D0E',
      surface: '#181A1D',
      text_contrast: '#FFFFFF',
      border: 'rgba(255, 255, 255, 0.15)',
      glow: 'rgba(212, 163, 115, 0.2)',
    },
    typography: {
      display: 'Oswald, Montserrat, sans-serif',
      body: 'Lora, Inter, Georgia, serif',
      numbers: 'JetBrains Mono, Inter, sans-serif',
      personality: 'prestigious_understated',
    },
    geometry: 'minimal_hairline',
    texture: 'clean',
    cornerRadius: 2,
    strokeWidth: 1,
    image_treatment: 'cinematic_grade',
  },
};

// Aliases for 100% backward compatibility
GENRE_PRESETS['technology'] = GENRE_PRESETS['cyber-technology'];
GENRE_PRESETS['true-crime'] = GENRE_PRESETS['forensic-noir'];
GENRE_PRESETS['royal-history'] = GENRE_PRESETS['royal-prestige'];
GENRE_PRESETS['broadsheet-paper'] = GENRE_PRESETS['broadsheet-heritage'];
GENRE_PRESETS['mrbeast-retention'] = GENRE_PRESETS['arcade-entertainment'];

/**
 * Deterministic hash function for brand/topic signature color derivation.
 */
function hashString(str = '') {
  let hash = 0;
  const s = String(str);
  for (let i = 0; i < s.length; i++) {
    hash = (hash << 5) - hash + s.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash);
}

/**
 * The Chameleon 4-Stage Adaptive Engine.
 * Dynamically synthesizes cohesive documentary Visual DNA for unknown / novel niches.
 * Guarantees every video has custom, professional visual direction without generic templates.
 */
export function synthesizeAdaptiveDNA(topicText = '', rawObj = {}) {
  const text = String(topicText || '').toLowerCase();
  const channelHint = String(rawObj.channel || rawObj.channel_name || rawObj.channel_profile_id || topicText).toLowerCase();
  const hash = hashString(channelHint || topicText || 'documentary');

  // Stage 1: Semantic Intent & Domain Extraction
  const isHistorical = /century|victorian|edwardian|medieval|ancient|antique|vintage|retro|classic|renaissance|colony|empire|dynasty|heritage|bygone|era/.test(text);
  const isTechnical = /engineering|mechanic|industrial|railway|machinery|clock|horology|welding|construction|architecture|blueprint|physics|quantum|code|algorithm|robot|drone|navigation|avionics|sensor|automation/.test(text);
  const isMarineOrAquatic = /sea|ocean|marine|naval|submar|deep water|diving|coral|abyss|aquatic/.test(text);
  const isCraftOrArtisan = /artisan|craft|woodwork|carpent|handcrafted|pottery|glassblow|forg|blacksmith/.test(text);
  const isInvestigation = /death|morgue|fatal|grave|mystery|unsolved|classified|dossier|conspiracy|secret|scandal/.test(text);

  // Stage 2 & 3: Procedural Palette Derivation & Deterministic Signature Color
  // Tune hue to look cinematic; never muddy.
  const signatureHue = (hash * 47) % 360;
  let accentHex;
  let bgHex = '#0A0D14';
  let surfaceHex = '#121824';
  let texture = 'clean';
  let personality = 'modern_grotesk';
  let displayFont = 'Montserrat, Oswald, sans-serif';
  let bodyFont = 'Inter, DM Sans, sans-serif';
  let numberFont = 'Inter, monospace';
  let geometry = 'editorial_fine';
  let cornerRadius = 4;
  let strokeWidth = 2;

  if (isHistorical) {
    accentHex = isCraftOrArtisan ? '#D97706' : '#C29B38'; // Antique Gold / Warm Ochre
    bgHex = '#12100E';
    surfaceHex = '#1C1814';
    texture = 'paper';
    personality = 'serifHistorical';
    displayFont = 'Cinzel, Playfair Display, Georgia, serif';
    bodyFont = 'Lora, Merriweather, serif';
    numberFont = 'Playfair Display, serif';
    geometry = 'framed';
    cornerRadius = 2;
  } else if (isMarineOrAquatic) {
    accentHex = '#0EA5E9'; // Deep Sea Cyan / Oceanic Blue
    bgHex = '#03121F';
    surfaceHex = '#072036';
    texture = 'vignette';
    personality = 'organic_natural';
    displayFont = 'Poppins, Montserrat, sans-serif';
    bodyFont = 'Inter, Open Sans, sans-serif';
    numberFont = 'Poppins, sans-serif';
    geometry = 'pill_rounded';
    cornerRadius = 16;
  } else if (isTechnical) {
    accentHex = '#38BDF8'; // Draftsman Sky Blue
    bgHex = '#081726';
    surfaceHex = '#0D2338';
    texture = 'scanlines';
    personality = 'technical_schematic';
    displayFont = 'JetBrains Mono, Roboto Mono, monospace';
    bodyFont = 'Inter, sans-serif';
    numberFont = 'JetBrains Mono, monospace';
    geometry = 'blueprint_cad';
    cornerRadius = 0;
    strokeWidth = 1.5;
  } else if (isInvestigation) {
    accentHex = '#DC2626'; // Red Alert
    bgHex = '#08080A';
    surfaceHex = '#141316';
    texture = 'film_grain';
    personality = 'dossier_classified';
    displayFont = 'Archivo Black, Impact, sans-serif';
    bodyFont = 'Inter, DM Sans, sans-serif';
    numberFont = 'JetBrains Mono, monospace';
    geometry = 'sharp';
    cornerRadius = 0;
    strokeWidth = 3;
  } else {
    // Deterministic curated brand hue
    const colors = ['#00F0FF', '#FACC15', '#10B981', '#F43F5E', '#A855F7', '#FB923C', '#38BDF8', '#EAB308'];
    accentHex = colors[hash % colors.length];
    bgHex = '#0A0C10';
    surfaceHex = '#141820';
    texture = 'clean';
    personality = 'modern_grotesk';
    displayFont = 'Montserrat, Plus Jakarta Sans, sans-serif';
    bodyFont = 'Inter, DM Sans, sans-serif';
    numberFont = 'JetBrains Mono, sans-serif';
    geometry = 'rounded_clean';
    cornerRadius = 8;
  }

  return {
    genre: 'adaptive-documentary',
    is_adaptive: true,
    derived_from: topicText,
    palette: {
      primary: '#FFFFFF',
      secondary: '#94A3B8',
      accent: accentHex,
      background: bgHex,
      surface: surfaceHex,
      text_contrast: '#FFFFFF',
      border: accentHex,
      glow: `${rgba(accentHex, 0.35)}`,
    },
    typography: {
      display: displayFont,
      body: bodyFont,
      numbers: numberFont,
      personality,
    },
    geometry,
    texture,
    cornerRadius,
    strokeWidth,
    image_treatment: isHistorical ? 'warm_archival' : isTechnical ? 'cool_modern' : isInvestigation ? 'desaturated_contrast' : 'natural',
  };
}

/**
 * Intelligent Semantic Genre Inference based on Topic / Script Keywords.
 * Comprehensively classifies into the 15 Universal Documentary Presets.
 */
export function inferGenreFromTopic(topicText = '') {
  const text = String(topicText).toLowerCase();

  // 15. Arcade / High-retention spectacle
  if (/beast|challenge|survive|extreme|winner|prize|giveaway|last to leave|stunt|insane|game show|spectacle/.test(text)) {
    return 'arcade-entertainment';
  }

  // 14. Athletic action & motorsports
  if (/formula 1|\bf1\b|nascar|racing|motorsport|grand prix|rally|speed record|supercar|marathon|sprint|olympic|boxer|boxing|ufc|fighter/.test(text)) {
    return 'athletic-action';
  }

  // 4. Engineering & Trains & Megastructures (e.g. Train Crew)
  if (/train|locomotive|railway|steam train|steam engine|subway|transit|freight|diesel train|bridge|tunnel|mega-structure|skyscraper|blueprint|machinery|turbine|mechanics|infrastructure/.test(text)) {
    return 'engineering-blueprint';
  }

  // 1. Archival Motoring & Mechanical Dossier (e.g. Classic Wheels)
  if (/classic car|vintage car|classic wheels|vintage auto|restoration|porsche 911|ferrari|aston martin|jaguar e-type|vintage machine|industrial heritage|aviation history|spitfire|biplane/.test(text)) {
    return 'archival-dossier';
  }

  // 3. Royal Prestige & Dynasties (e.g. Royal Celeb, Crown Royal, Crown Zone)
  if (/royal|king|queen|crown|monarch|palace|windsor|dynasty|heritage|coronation|emperor|tsar|aristocracy|monarchy|buckingham|princess|prince/.test(text)) {
    return 'royal-prestige';
  }

  // 5. Forensic Noir & After Death (e.g. After Death, True Crime)
  if (/after death|death|corpse|autopsy|forensic|morgue|fatal|grave|cemetery|embalm|crime|murder|killer|dossier|classified|leak|conspiracy|\bfbi\b|police|\bcia\b|investigat|witness|trial|serial killer/.test(text)) {
    return 'forensic-noir';
  }

  // 2. Broadsheet Heritage & British History (e.g. Nostalgic Brit)
  if (/nostalgic brit|nostalgia|britain|british|victorian|edwardian|broadsheet|inquiry|times|daily|press|journalism|telegram|parliament|tribune|fleet street|colony|colonial empire/.test(text)) {
    return 'broadsheet-heritage';
  }

  // 7. Magnates Noir (Business noir / billionaires / empires / scandals)
  if (/magnates|billionaire|empire|scandal|corruption|heist|fall of|rise of|fraud|ponzi|monopoly|tycoon|cartel|mafia|greed|wall street|hedge fund/.test(text)) {
    return 'magnates-noir';
  }

  // 9. Cyber Technology
  if (/\btech\b|technology|cyber|\bai\b|artificial intelligence|silicon|robot|surveillance|algorithm|software|crypto|digital|quantum computing|microchip|semiconductor|hacker/.test(text)) {
    return 'cyber-technology';
  }

  // 10. Cosmic Science & Speculative Physics
  if (/space|cosmic|astronomy|nasa|galaxy|black hole|telescope|james webb|mars|interstellar|physics|quantum physics|quantum mechanics|relativity|universe|astrophysics|dark matter/.test(text)) {
    return 'cosmic-science';
  }

  // 11. Military History & Tactical Analysis
  if (/\bwar\b|battle|military|army|navy|air force|tactical|weapon|pentagon|doctrine|tank|missile|conflict|invasion|combat|frontline|wwi|wwii|world war|cold war/.test(text)) {
    return 'military-history';
  }

  // 12. Nature & Wildlife
  if (/nature|wildlife|jungle|ocean|predator|species|safari|forest|ecosystem|creature|whale|shark|extinction|amazon rainforest|marine biology/.test(text)) {
    return 'nature-wildlife';
  }

  // 8. Dhruv Clarity (Factual analysis / truth vs myth / education)
  if (/dhruv|fact-check|reality of|truth behind|debunk|myth|official data|gdp|election|democracy|poll|reform|explained|analysis/.test(text)) {
    return 'dhruv-clarity';
  }

  // 13. Minimal Editorial
  if (/design|architecture|minimal|scandinav|nordic|clean|essay|space|calm|interior|minimalist/.test(text)) {
    return 'minimal-editorial';
  }

  // 6. Vox Explainer (General social/cultural explainers)
  if (/vox|why|how|system|culture|phenomenon|trend|demographics|society|curious|everything you need to know/.test(text)) {
    return 'vox-explainer';
  }

  // If no direct keyword matched, return null to signal adaptive Chameleon synthesis
  return null;
}

/**
 * Visual DNA Style Engine.
 * Resolves palettes, font stacks, geometry tokens, and atmospheric textures.
 * If a novel or unclassified topic is encountered, delegates seamlessly to the Chameleon Adaptive Engine.
 */
export function resolveVisualDNA(rawDNA = {}) {
  let genreKey = (
    typeof rawDNA === 'string'
      ? rawDNA
      : (rawDNA.genre || rawDNA.themeName || rawDNA.theme_name || rawDNA.id || '')
  ).toLowerCase().replace(/_/g, '-');

  const rawObj = typeof rawDNA === 'object' ? rawDNA : {};
  const topicHint = rawObj.topic || rawObj.title || rawObj.headline || rawObj.subject || rawObj.niche || rawObj.category || '';

  // If no explicit genre provided, infer from topic/title
  if (!genreKey || !GENRE_PRESETS[genreKey]) {
    if (topicHint) {
      const inferred = inferGenreFromTopic(topicHint);
      if (inferred && GENRE_PRESETS[inferred]) {
        genreKey = inferred;
      }
    }
  }

  // If still unknown or novel, use Chameleon Adaptive Synthesis!
  let preset;
  if (genreKey && GENRE_PRESETS[genreKey]) {
    preset = GENRE_PRESETS[genreKey];
  } else {
    preset = synthesizeAdaptiveDNA(topicHint, rawObj);
    genreKey = preset.genre;
  }

  const palette = {
    ...preset.palette,
    ...((typeof rawDNA === 'object' && rawDNA.palette) || {}),
  };

  const typography = {
    ...preset.typography,
    ...((typeof rawDNA === 'object' && rawDNA.typography) || {}),
  };

  return {
    genre: genreKey,
    niche: rawObj.niche || genreKey,
    is_adaptive: Boolean(preset.is_adaptive),
    palette,
    typography: {
      display: typography.display && typography.display.includes(',') ? typography.display : `${typography.display || 'Montserrat'}, Arial, sans-serif`,
      body: typography.body && typography.body.includes(',') ? typography.body : `${typography.body || 'Inter'}, Arial, sans-serif`,
      numbers: typography.numbers && typography.numbers.includes(',') ? typography.numbers : `${typography.numbers || 'JetBrains Mono'}, Arial, sans-serif`,
      personality: typography.personality || preset.typography.personality,
    },
    geometry: rawObj.geometry || preset.geometry || 'sharp',
    texture: rawObj.texture || preset.texture || 'clean',
    motion_style: rawObj.motion_style || 'restrained',
    caption_style: rawObj.caption_style || 'documentary_small',
    map_style: rawObj.map_style || 'dark_desaturated',
    chart_style: rawObj.chart_style || 'editorial',
    image_treatment: rawObj.image_treatment || preset.image_treatment || 'cinematic_grade',
    transition_grammar: rawObj.transition_grammar || 'mostly_cuts',
    render_mode: rawObj.render_mode || 'advanced-doc-v1',
    visual_system: rawObj.visual_system || 'advanced-doc-v1',
    cornerRadius: Number(rawObj.corner_radius ?? rawObj.cornerRadius ?? preset.cornerRadius ?? 4),
    strokeWidth: Number(rawObj.stroke_width ?? rawObj.strokeWidth ?? preset.strokeWidth ?? 2),
    seed: Number(rawObj.seed ?? 0),
  };
}

/**
 * Atmospheric Texture Layer based on project Visual DNA.
 * Stable, restrained optional textures. No random frame-to-frame flicker.
 */
export function VisualTexture({ texture = 'clean', opacity = 0.18, color = '#ffffff' }) {
  if (!texture || texture === 'clean' || texture === 'none') {
    return null;
  }

  if (texture === 'vignette') {
    return React.createElement(AbsoluteFill, {
      style: {
        pointerEvents: 'none',
        opacity: Math.min(.25, Math.max(0, Number(opacity) || .12)),
        background: 'radial-gradient(ellipse at center, transparent 65%, rgba(0, 0, 0, 0.4) 100%)',
        zIndex: 10,
      },
    });
  }

  const backgrounds = {
    paper: `repeating-linear-gradient(24deg, transparent 0 7px, ${color} 8px, transparent 9px)`,
    halftone: `radial-gradient(${color} .6px, transparent .9px)`,
    scanlines: `repeating-linear-gradient(0deg, transparent 0 3px, ${color} 4px, transparent 5px)`,
    film_grain: `radial-gradient(${color} .45px, transparent .7px)`,
    subtle_noise: `radial-gradient(${color} .35px, transparent .7px)`,
    dust_and_scratch: `repeating-linear-gradient(92deg, transparent 0 149px, ${color} 150px, transparent 151px)`,
  };
  if (!backgrounds[texture]) return null;
  return React.createElement(AbsoluteFill, {
    style: {
      pointerEvents: 'none',
      zIndex: 10,
      opacity: Math.min(.025, Math.max(0, Number(opacity) || .015)),
      backgroundImage: backgrounds[texture],
      backgroundSize: texture === 'halftone' ? '7px 7px' : ['film_grain', 'subtle_noise'].includes(texture) ? '3px 5px' : undefined,
    },
  });
}
