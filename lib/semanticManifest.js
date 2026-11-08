'use strict';

/**
 * Semantic Edit Manifest Generator for Fast-Paced Documentary Remotion Editing.
 *
 * Implements documentary visual grammar, narrative pacing, keyword-based
 * template selection (Animated Maps, Text/Word Highlights, Technical Blueprints,
 * Timelines, Stat Counters, Declassified Records), camera dynamics,
 * cinematic color grading, transitions, and synchronized SFX cues.
 */

const fs = require('fs');
const path = require('path');

const THEMES = ['vox_clarity', 'dark_documentary', 'archival_investigation', 'cinematic_noir'];
const TRANSITIONS = ['dissolve', 'whip_pan', 'glitch', 'zoom_fade', 'film_burn'];
const CAMERAS = ['slow_push', 'pan_left', 'pan_right', 'dynamic_zoom', 'subtle_shake'];

const ROUTE_DATASETS = [
  { origin: 'London Kings Cross', destination: 'Edinburgh Waverley', coords: [[-0.1246, 51.5308], [-0.3, 52.5], [-1.2, 53.8], [-3.19, 55.95]], label: 'EAST COAST MAIN LINE' },
  { origin: 'Chicago Union', destination: 'New York Central', coords: [[-87.64, 41.87], [-84.5, 41.5], [-80.0, 41.0], [-74.0, 40.71]], label: 'TRANSCONTINENTAL CORRIDOR' },
  { origin: 'Sydney Central', destination: 'Perth Terminal', coords: [[151.2, -33.88], [141.4, -31.9], [128.8, -30.7], [115.8, -31.95]], label: 'INDIAN PACIFIC 65-HR RUN' },
  { origin: 'Karachi Cantonment', destination: 'Peshawar Cantt', coords: [[67.04, 24.85], [68.36, 25.39], [71.52, 30.15], [71.57, 34.01]], label: 'MAIN LINE HISTORIC TRACE' }
];

function extractNumbersAndUnits(text) {
  const match = text.match(/(\b\d{1,4}(?:,\d{3})*(?:\.\d+)?(?:\+|%)?\s*(?:mph|km\/h|hours?|mins?|years?|tons?|miles?|km|percent|%)?)/i);
  return match ? match[1].trim() : null;
}

function extractYear(text) {
  const match = text.match(/\b(18\d{2}|19\d{2}|20\d{2})\b/);
  return match ? match[1] : null;
}

function cleanSentences(scriptText) {
  return String(scriptText || '')
    .replace(/\r\n/g, '\n')
    .split(/(?<=[.!?])\s+/)
    .map(s => s.trim())
    .filter(s => s.length > 15);
}

function assignSceneGrammar(sentence, sceneIndex, totalScenes) {
  const lower = sentence.toLowerCase();
  const num = extractNumbersAndUnits(sentence);
  const year = extractYear(sentence);

  // 1. Geography & Track Routes -> AnimatedMap
  if (/map|route|track|switch|junction|line|crossing|corridor|border|distance|station|chicago|london|sydney/i.test(lower)) {
    const route = ROUTE_DATASETS[sceneIndex % ROUTE_DATASETS.length];
    return {
      templateId: 'map-route',
      props: {
        label: route.label,
        sublabel: `${route.origin} → ${route.destination}`,
        originName: route.origin,
        destName: route.destination,
        routeCoords: route.coords,
        markerTitle: 'CRITICAL JUNCTION',
        kicker: 'SYSTEM GEOMETRY'
      },
      sfx: 'map_pulse'
    };
  }

  // 2. Metrics, Speeds, Measurements, Amounts -> StatCard / NumberCounter
  if (num && /mph|speed|record|hours?|percent|ton|mile|fast|rate|number/i.test(lower)) {
    return {
      templateId: 'stat-card',
      props: {
        value: num.toUpperCase(),
        label: sentence.slice(0, 48) + '...',
        kicker: 'MEASURED BENCHMARK',
        detail: 'Documented railway operational metric'
      },
      sfx: 'sub_boom'
    };
  }

  // 3. Technical Engineering & Mechanics -> TechnicalBlueprint
  if (/engine|locomotive|mechanism|steel|wheel|flange|point|gear|steam|pressure|hydraulic|motor|boiler/i.test(lower)) {
    return {
      templateId: 'technical-blueprint',
      props: {
        title: 'SWITCH & TURNOUT SCHEMATIC',
        specNumber: 'DWG-' + (1920 + sceneIndex),
        calloutA: 'Tapered Switch Point Rail',
        calloutB: 'Opposed Guard Rail Clearance',
        calloutC: 'Common Crossing Frog Assembly',
        statusText: 'APPROVED RAILWAY ENGINEERING SPEC'
      },
      sfx: 'marker_scratch'
    };
  }

  // 4. Historical Dates & Milestones -> Timeline
  if (year || /history|century|record|established|decade|timeline|era/i.test(lower)) {
    return {
      templateId: 'date-card',
      props: {
        date: year || 'HISTORIC TIMELINE',
        title: 'RECORD MILESTONE',
        description: sentence.slice(0, 80) + '...',
        kicker: 'CHRONOLOGY'
      },
      sfx: 'clock_tick'
    };
  }

  // 5. Official Records, Investigations, Safety Documents -> DeclassifiedDocument
  if (/report|investigation|official|safety|source|documented|dispatch|archive|evidence/i.test(lower)) {
    return {
      templateId: 'declassified-document',
      props: {
        header: 'MINISTRY OF TRANSPORTATION / ACCIDENT LOG',
        fileId: `REC-${8400 + sceneIndex}`,
        highlightedText: sentence.slice(0, 95) + '...',
        stampText: 'VERIFIED EVIDENCE'
      },
      sfx: 'stamp_hit'
    };
  }

  // 6. Key Words / Phrase Highlights according to the script -> WordHighlighter / DocumentHighlight
  const words = sentence.split(/\s+/).filter(w => w.length > 4);
  const highlightWord = words[sceneIndex % words.length] || 'CRITICAL';
  return {
    templateId: 'source-highlight',
    props: {
      kicker: 'KEY TRANSCRIPT EXCERPT',
      title: sentence.slice(0, 60).toUpperCase(),
      highlightWord: highlightWord.toUpperCase(),
      citation: 'Documentary Narration Track'
    },
    sfx: 'typewriter'
  };
}

function buildSemanticManifest(job, scriptText, options = {}) {
  const sentences = cleanSentences(scriptText);
  if (!sentences.length) {
    throw new Error('Narration text must contain complete sentences to assemble the documentary manifest');
  }

  const fps = Number(options.fps) || 30;
  const width = Number(options.width) || 1920;
  const height = Number(options.height) || 1080;
  const targetDurationSeconds = Math.max(30, (Number(job.targetDurationMinutes) || 1) * 60);

  // Divide target duration proportionally across sentences (fast-paced documentary: 4 to 8s per scene)
  const baseSceneDuration = Math.max(4.0, Math.min(8.0, targetDurationSeconds / sentences.length));

  let currentTime = 0;
  const scenes = [];

  sentences.forEach((sentence, idx) => {
    // Last scene stretches or shrinks slightly to land on exact total duration
    const isLast = idx === sentences.length - 1;
    const duration = isLast
      ? Math.max(3.0, targetDurationSeconds - currentTime)
      : Math.round(baseSceneDuration * 10) / 10;

    const grammar = assignSceneGrammar(sentence, idx, sentences.length);
    const cameraMotion = CAMERAS[idx % CAMERAS.length];
    const transitionType = TRANSITIONS[idx % TRANSITIONS.length];

    scenes.push({
      id: `scene-${idx + 1}`,
      scene_index: idx,
      start: Math.round(currentTime * 100) / 100,
      duration: Math.round(duration * 100) / 100,
      start_frame: Math.round(currentTime * fps),
      duration_in_frames: Math.round(duration * fps),
      narration: sentence,
      media: {
        type: 'image',
        src: '',
        fit: 'cover'
      },
      graphic: {
        template_id: grammar.templateId,
        props: grammar.props
      },
      transition: {
        template_id: transitionType,
        duration: 0.35,
        type: transitionType
      },
      camera: {
        motion: cameraMotion,
        scale: 1.08,
        intensity: 'medium'
      },
      overlays: {
        film_grain: 0.15,
        vignette: 0.25,
        radial_spotlight: true
      },
      sound_cues: [
        {
          id: `sfx-${idx}-trans`,
          type: 'transition',
          file: 'whoosh',
          start_offset_seconds: 0.05,
          volume: 0.14
        },
        {
          id: `sfx-${idx}-feat`,
          type: 'feature',
          file: grammar.sfx,
          start_offset_seconds: 0.45,
          volume: 0.2
        }
      ]
    });

    currentTime += duration;
  });

  const totalDuration = Math.round(currentTime * 100) / 100;
  const totalFrames = Math.round(totalDuration * fps);

  return {
    version: 1,
    schema: 'vidzeo-documentary-semantic-manifest-v2',
    fps,
    width,
    height,
    duration: totalDuration,
    duration_in_frames: totalFrames,
    title: job.title || 'Documentary Master',
    channel: job.channel?.name || 'Documentary Channel',
    theme: {
      id: THEMES[0],
      palette: {
        background: '#0B0F19',
        card: '#131B2E',
        accent: '#D97706',
        text: '#F8FAFC',
        muted: '#94A3B8'
      }
    },
    scenes
  };
}

module.exports = {
  buildSemanticManifest,
  assignSceneGrammar,
  THEMES,
  TRANSITIONS,
  CAMERAS
};
