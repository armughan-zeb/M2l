'use strict';

/**
 * One-Minute Documentary adapter for the Vidzeo Muse worker.
 *
 * Implements the `one_minute_documentary` skill (yt-dlp footage, verified
 * web/Wikimedia images, TTS voiceover, and Remotion 1080p@30fps rendering)
 * with compulsory Muse inference (never OpenRouter) and strict isolation
 * from 2D Stickman.
 */

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const log = require('../lib/log');
const { resolvePython } = require('../lib/python');
const { writeJsonAtomic } = require('../lib/config');
const ffmpeg = require('../lib/ffmpeg');
const { assertNoOpenRouterKey } = require('../lib/museInference');
const { resolveVideoStyle, ENGINES, defaultVoiceForStyle } = require('../lib/videoStyle');
const { stampDocumentaryManifest } = require('../lib/masterManifestGuard');
const { cleanGeneratedCode, runProcess, inferWithMuse, createFrameProgressReporter, readStageCheckpoint, writeStageCheckpoint } = require('../lib/museBridgeInfer');

const FPS = 30;
const WIDTH = 1920;
const HEIGHT = 1080;

const SHOTS_PLAN_SCHEMA = {
  type: 'json_schema',
  json_schema: {
    name: 'documentary_shots_plan',
    schema: {
      type: 'object',
      additionalProperties: false,
      required: ['topicTag', 'headerText', 'shots'],
      properties: {
        topicTag: { type: 'string' },
        headerText: { type: 'string' },
        shots: {
          type: 'array',
          minItems: 4,
          items: {
            type: 'object',
            additionalProperties: false,
            required: ['beat', 'kind', 'durationSec', 'query', 'title', 'sub', 'zoomIn', 'center'],
            properties: {
              beat: { type: 'string', enum: ['hook', 'background', 'development', 'impact', 'closing'] },
              kind: { type: 'string', enum: ['video', 'image'] },
              durationSec: { type: 'number' },
              query: { type: 'string' },
              sourceUrl: { type: 'string' },
              title: { type: 'string' },
              sub: { type: 'string' },
              zoomIn: { type: 'boolean' },
              center: { type: 'boolean' },
            },
          },
        },
      },
    },
  },
};

function documentaryRoot(env = process.env) {
  const explicit = String(env.MUSE_DOCUMENTARY_ROOT || '').trim();
  if (explicit) return explicit;
  return path.join(__dirname, '..', 'one_minute_documentary');
}

function documentaryRuntimeStatus(env = process.env) {
  const root = documentaryRoot(env);
  const templateDir = path.join(root, 'templates', 'remotion-project');
  const required = [
    path.join(root, 'SKILL.md'),
    path.join(root, 'skill.yaml'),
    path.join(templateDir, 'package.json'),
    path.join(templateDir, 'src', 'Composition.tsx'),
    path.join(templateDir, 'src', 'RemotionRoot.tsx'),
    path.join(templateDir, 'src', 'index.ts'),
  ];
  const missing = required.filter((file) => !fs.existsSync(file));
  return {
    available: missing.length === 0,
    root,
    templateDir,
    missing,
  };
}

function buildShotsPlanMessages({ title, scriptText, voiceDurationSec, targetDurationMinutes, channel }) {
  const dur = Math.max(20, Number(voiceDurationSec) || Math.round((Number(targetDurationMinutes) || 1) * 60));
  const targetMinutes = Math.max(1, Number(targetDurationMinutes) || Math.round((dur / 60) * 10) / 10);
  const targetShots = Math.max(6, Math.min(240, Math.round(dur / 7)));
  const minShots = Math.max(5, Math.min(180, Math.round(dur / 10)));
  const structure = String(channel?.scriptSettings?.customStructure || '').trim();
  const sys = [
    'You are the director for the Documentary Remotion engine (`one_minute_documentary` template, scaled dynamically to any CMS runtime).',
    `Plan a continuous shot list covering the ENTIRE ${dur.toFixed(2)}s (~${targetMinutes}-minute) measured voiceover.`,
    structure
      ? `Documentary structure: ${structure}`
      : 'Distribute shots proportionally across the 5 documentary beats: 1) hook (0-13%), 2) background (13-33%), 3) development (33-58%), 4) impact (58-80%), 5) closing (80-100%, last shot usually center=true).',
    '',
    'Rules:',
    `1. CRITICAL DURATION: Sum of shot \`durationSec\` MUST cover the full ${dur.toFixed(2)}s voiceover (~${targetShots} shots, at least ${minShots} shots, typically 4-10s per shot — NEVER cap a multi-minute video at a 1-minute shot count).`,
    '2. Mix `kind: "video"` (for yt-dlp documentary/real footage) and `kind: "image"` (archival or photo stills).',
    '3. Keep `topicTag` (2-20 chars uppercase) and `headerText` (short uppercase subtitle) specific to the topic.',
    '4. Keep each shot `title` short and punchy (2-6 uppercase words) and `sub` one short descriptive line (<= 10 words).',
    '5. Each `query` must be concrete, visual, and specific to that segment of narration (never generic filler).',
    '6. Output valid JSON matching the schema ONLY.',
    '7. NEVER repeat a `query`: every shot gets a different search query with different subject, angle, or setting words — two shots must never search the same thing, or the video will show the same clip twice.',
  ].join('\n');

  const refs = Array.isArray(channel?.scriptReferences)
    ? channel.scriptReferences.map((r) => r.title || r.url).filter(Boolean).slice(0, 3).join('; ')
    : '';
  const tone = String(channel?.scriptSettings?.tone || '').trim();

  return [
    { role: 'system', content: sys },
    {
      role: 'user',
      content: [
        `TITLE: ${title}`,
        `CHANNEL: ${channel?.name || 'Documentary'} (${channel?.niche || 'General'})`,
        tone ? `TONE: ${tone}` : '',
        refs ? `REFERENCES: ${refs}` : '',
        `TARGET_MINUTES: ${targetMinutes}`,
        `MEASURED_VOICEOVER_SECONDS: ${dur.toFixed(2)}`,
        '',
        'NARRATION:',
        scriptText,
      ].filter(Boolean).join('\n'),
    },
  ];
}

function normalizeShots(rawShots, totalFrames) {
  const total = Math.max(300, Math.round(Number(totalFrames) || 1800));
  const list = Array.isArray(rawShots) && rawShots.length ? rawShots : [
    { beat: 'hook', kind: 'image', durationSec: total / (FPS * 5), query: 'documentary opening scene', title: 'THE STORY', sub: 'Where it all began.', zoomIn: true, center: false },
    { beat: 'background', kind: 'image', durationSec: total / (FPS * 5), query: 'historical archive photograph', title: 'ORIGINS', sub: 'The earliest chapter.', zoomIn: false, center: false },
    { beat: 'development', kind: 'video', durationSec: total / (FPS * 5), query: 'action footage documentary', title: 'THE TURNING POINT', sub: 'Everything changed quickly.', zoomIn: true, center: false },
    { beat: 'impact', kind: 'video', durationSec: total / (FPS * 5), query: 'modern global scene', title: 'THE IMPACT', sub: 'Felt across the world.', zoomIn: true, center: false },
    { beat: 'closing', kind: 'image', durationSec: total / (FPS * 5), query: 'dramatic closing horizon', title: 'THE LEGACY', sub: 'A story still unfolding.', zoomIn: false, center: true },
  ];

  const weights = list.map((s) => Math.max(2, Number(s.durationSec) || 6));
  const weightSum = weights.reduce((acc, w) => acc + w, 0) || 1;
  let cursor = 0;
  // The planner sometimes repeats one query across shots; identical queries
  // resolve to the identical clip, so force later duplicates apart by folding
  // the shot's own title words into the query. Fetch-level ID exclusion below
  // is the hard guarantee — this keeps the search terms honest too.
  const seenQueries = new Set();
  return list.map((shot, index) => {
    const isLast = index === list.length - 1;
    const rawFrames = Math.max(60, Math.round((weights[index] / weightSum) * total));
    const dur = isLast ? Math.max(60, total - cursor) : Math.min(rawFrames, Math.max(60, total - cursor - (list.length - 1 - index) * 60));
    const from = cursor;
    cursor += dur;
    let query = String(shot.query || '').trim() || 'documentary archive';
    const queryKey = query.toLowerCase().replace(/\s+/g, ' ');
    if (seenQueries.has(queryKey)) {
      const discriminator = String(shot.title || '').trim().toLowerCase();
      if (discriminator) query = `${query} ${discriminator}`;
    }
    seenQueries.add(queryKey);
    return {
      index,
      beat: ['hook', 'background', 'development', 'impact', 'closing'].includes(shot.beat) ? shot.beat : 'development',
      kind: shot.kind === 'video' ? 'video' : 'image',
      from,
      dur,
      query,
      sourceUrl: String(shot.sourceUrl || '').trim(),
      title: String(shot.title || 'DOCUMENTARY').trim().slice(0, 80),
      sub: String(shot.sub || '').trim().slice(0, 140),
      zoomIn: shot.zoomIn !== false,
      center: Boolean(shot.center),
    };
  });
}

// NOTE: the generated Shot template deliberately puts NO `loop` prop on
// <OffthreadVideo>. In Remotion 4.0.547 the loop prop leaks through
// OffthreadVideoForRendering into an internal <Img>, which renders a looping
// <Sequence> with no durationInFrames, so the render dies at frame 0.
// (Do NOT put this note as a {/* */} comment inside the generated JSX: a
// comment next to the video element makes two adjacent children and breaks
// the bundle with `Expected ")" but found "src"` — proven live 2026-11-27.)
// Clips are trimmed to roughly the shot length, so a clip that ends a few
// frames early simply holds its last frame.
function generateCompositionTsx({ topicTag, headerText, shots, captionsEnabled = true }) {
  const safeTag = JSON.stringify(String(topicTag || 'DOCUMENTARY').toUpperCase().slice(0, 32));
  const safeHeader = JSON.stringify(String(headerText || '').toUpperCase().slice(0, 48));
  const shotLines = shots.map((shot) => {
    const mediaAttr = shot.assetType === 'video'
      ? `video=${JSON.stringify(shot.assetPath)}`
      : `image=${JSON.stringify(shot.assetPath)}`;
    const attrs = [
      `from={${shot.from}}`,
      `dur={${shot.dur}}`,
      mediaAttr,
      `title=${JSON.stringify(shot.title)}`,
      shot.sub ? `sub=${JSON.stringify(shot.sub)}` : '',
      shot.zoomIn ? 'zoomIn' : '',
      shot.center ? 'center' : '',
    ].filter(Boolean).join(' ');
    return `      <Shot ${attrs} />`;
  }).join('\n');

  return `import React from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  OffthreadVideo,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

const FONT = "Arial, Helvetica, sans-serif";
const ORANGE = "#ff7a1a";

const TOPIC_TAG = ${safeTag};
const HEADER_TEXT = ${safeHeader};

const fade = (local: number, dur: number) =>
  interpolate(local, [0, 15, dur - 15, dur], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

const Gradient: React.FC = () => (
  <div
    style={{
      position: "absolute",
      left: 0,
      right: 0,
      bottom: 0,
      height: 460,
      background:
        "linear-gradient(to top, rgba(0,0,0,0.82) 0%, rgba(0,0,0,0.45) 55%, rgba(0,0,0,0) 100%)",
    }}
  />
);

const Caption: React.FC<{
  title: string;
  sub?: string;
  align?: "left" | "center";
}> = ({ title, sub, align = "left" }) => (
  <div
    style={{
      position: "absolute",
      left: align === "center" ? 0 : 90,
      right: align === "center" ? 0 : 90,
      bottom: 110,
      textAlign: align,
      fontFamily: FONT,
      color: "#fff",
    }}
  >
    <div
      style={{
        display: "inline-block",
        background: ORANGE,
        color: "#111",
        fontWeight: 900,
        fontSize: 26,
        letterSpacing: 6,
        padding: "8px 18px",
        marginBottom: 18,
      }}
    >
      {TOPIC_TAG}
    </div>
    <div
      style={{
        fontSize: 84,
        fontWeight: 900,
        lineHeight: 1.02,
        letterSpacing: 1,
        textShadow: "0 4px 30px rgba(0,0,0,0.7)",
      }}
    >
      {title}
    </div>
    {sub ? (
      <div
        style={{
          marginTop: 16,
          fontSize: 34,
          fontWeight: 400,
          letterSpacing: 2,
          color: "#f2f2f2",
          textShadow: "0 2px 12px rgba(0,0,0,0.8)",
        }}
      >
        {sub}
      </div>
    ) : null}
  </div>
);

const Shot: React.FC<{
  from: number;
  dur: number;
  video?: string;
  image?: string;
  title: string;
  sub?: string;
  center?: boolean;
  zoomIn?: boolean;
}> = ({ from, dur, video, image, title, sub, center, zoomIn }) => {
  const frame = useCurrentFrame();
  const local = frame - from;
  const opacity = fade(local, dur);
  const scale = interpolate(local, [0, dur], zoomIn ? [1.0, 1.12] : [1.12, 1.0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <Sequence from={from} durationInFrames={dur}>
      <AbsoluteFill style={{ opacity, overflow: "hidden", background: "#000" }}>
        <div
          style={{
            width: "100%",
            height: "100%",
            transform: \`scale(\${scale})\`,
          }}
        >
          {video ? (
            <OffthreadVideo
              src={staticFile(video)}
              muted
              style={{ width: "100%", height: "100%", objectFit: "cover" }}
            />
          ) : (
            <Img
              src={staticFile(image as string)}
              style={{ width: "100%", height: "100%", objectFit: "cover" }}
            />
          )}
        </div>
        ${captionsEnabled ? '<Gradient />\n        <Caption title={title} sub={sub} align={center ? "center" : "left"} />' : ''}
      </AbsoluteFill>
    </Sequence>
  );
};

export const Documentary: React.FC = () => {
  const { durationInFrames } = useVideoConfig();
  const frame = useCurrentFrame();
  const progress = (frame / durationInFrames) * 100;
  return (
    <AbsoluteFill style={{ background: "#000", fontFamily: FONT }}>
      <Audio src={staticFile("audio/voiceover.mp3")} />

${shotLines}

      <div
        style={{
          position: "absolute",
          left: 0,
          bottom: 0,
          height: 10,
          width: \`\${progress}%\`,
          background: ORANGE,
        }}
      />
      <div
        style={{
          position: "absolute",
          right: 36,
          top: 30,
          fontFamily: FONT,
          color: "rgba(255,255,255,0.85)",
          fontSize: 24,
          letterSpacing: 4,
          fontWeight: 700,
          textShadow: "0 2px 8px rgba(0,0,0,0.8)",
        }}
      >
        {HEADER_TEXT}
      </div>
    </AbsoluteFill>
  );
};
`;
}

function generateRemotionRootTsx(durationInFrames, { width = WIDTH, height = HEIGHT } = {}) {
  const frames = Math.max(300, Math.round(Number(durationInFrames) || 1800));
  const outW = Number(width) || WIDTH;
  const outH = Number(height) || HEIGHT;
  return `import React from "react";
import { Composition } from "remotion";
import { Documentary } from "./Composition";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Documentary"
      component={Documentary}
      durationInFrames={${frames}}
      fps={${FPS}}
      width={${outW}}
      height={${outH}}
    />
  );
};
`;
}

function isValidImageBuffer(buf) {
  if (!Buffer.isBuffer(buf) || buf.length < 1024) return '';
  if (buf[0] === 0xff && buf[1] === 0xd8 && buf[2] === 0xff) return '.jpg';
  if (buf.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) return '.png';
  if (buf.toString('ascii', 0, 4) === 'RIFF' && buf.toString('ascii', 8, 12) === 'WEBP') return '.webp';
  return '';
}

function selectTtsEngine({ explicitVoice, hatchTtsAvailable } = {}) {
  // Muse TTS (Hatch) is PRIMARY; Edge is only the fallback when the Hatch
  // binary is missing. An explicit voice name must NOT force Edge — the
  // default documentary voice is an Edge-style name, and routing on it
  // silently switched every documentary narration to Edge.
  void explicitVoice;
  return hatchTtsAvailable ? 'hatch' : 'edge';
}

async function synthesizeVoiceover(scriptText, outputPath, { voice, signal, pythonBin } = {}) {
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  const hatchTts = String(process.env.MUSE_HATCH_TTS_BIN || '/opt/hatch/bin/tts').trim();
  const textFile = `${outputPath}.txt`;
  fs.writeFileSync(textFile, String(scriptText || '').trim(), 'utf8');
  const explicitVoice = String(voice || '').trim();
  const py = pythonBin || await resolvePython();

  if (selectTtsEngine({ explicitVoice, hatchTtsAvailable: Boolean(hatchTts) && fs.existsSync(hatchTts) }) === 'hatch') {
    await runProcess(py, ['-c', `
import subprocess, sys
text = open(sys.argv[1], encoding="utf-8").read()
proc = subprocess.run([sys.argv[2], "speak", "--output", sys.argv[3], "--text-stdin"], input=text, text=True)
sys.exit(proc.returncode)
`, textFile, hatchTts, outputPath], { signal, label: 'Hatch TTS synthesis' });
  } else {
    const edgeVoice = explicitVoice || 'en-US-ChristopherNeural';
    await runProcess(py, ['-m', 'edge_tts', '--voice', edgeVoice, '--file', textFile, '--write-media', outputPath], {
      signal,
      label: 'Edge TTS voiceover',
    });
  }

  try { fs.rmSync(textFile, { force: true }); } catch { /* ignore */ }
  const info = await ffmpeg.probeVideo(outputPath);
  if (!info.duration || info.duration < 5) {
    throw new Error(`Voiceover duration is too short or invalid (${info.duration || 0}s)`);
  }
  return info.duration;
}

async function searchWikimediaImages(query, { signal } = {}) {
  const url = `https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrnamespace=6&gsrsearch=${encodeURIComponent(query)}&gsrlimit=6&prop=imageinfo&iiprop=url|mime&format=json&origin=*`;
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': 'VidzeoMuseDocumentary/1.0' },
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000),
    });
    if (!res.ok) return [];
    const data = await res.json();
    const pages = Object.values(data?.query?.pages || {});
    const urls = [];
    for (const page of pages) {
      const info = Array.isArray(page.imageinfo) ? page.imageinfo[0] : null;
      if (info?.url && /^image\/(jpeg|png|webp)$/i.test(String(info.mime || ''))) {
        urls.push(info.url);
      }
    }
    return urls;
  } catch {
    return [];
  }
}

async function downloadImageUrl(url, destStem, { signal } = {}) {
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': 'VidzeoMuseDocumentary/1.0' },
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(20000)]) : AbortSignal.timeout(20000),
    });
    if (!res.ok) return null;
    const buf = Buffer.from(await res.arrayBuffer());
    const ext = isValidImageBuffer(buf);
    if (!ext) return null;
    // A real photo for a 1080p frame is never a few KB: tiny files are
    // UI sprites/icons that pass the magic-byte check (a 2.8 KB Yandex
    // chrome PNG did exactly that in live testing).
    if (buf.length < 8192) return null;
    const dest = `${destStem}${ext}`;
    fs.writeFileSync(dest, buf);
    return dest;
  } catch {
    return null;
  }
}

function normalizeYoutubeVideoId(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  if (/^[A-Za-z0-9_-]{11}$/.test(raw)) return raw;
  const m = raw.match(/(?:[?&]v=|\/shorts\/|\/embed\/|\/v\/|youtu\.be\/)([A-Za-z0-9_-]{11})/);
  return m ? m[1] : '';
}

function pickUnusedVideoCandidate(candidates, usedVideoIds) {
  const used = usedVideoIds instanceof Set ? usedVideoIds : new Set(usedVideoIds || []);
  for (const cand of Array.isArray(candidates) ? candidates : []) {
    const id = normalizeYoutubeVideoId(cand?.id || cand?.url || '');
    if (id && !used.has(id)) return { id, title: String(cand?.title || '').trim() };
  }
  return null;
}

function pickLeastUsedFallback(fallbacks, usageCounts) {
  let best = null;
  let bestCount = Infinity;
  for (const fb of Array.isArray(fallbacks) ? fallbacks : []) {
    const count = Number(usageCounts?.get?.(fb.assetPath)) || 0;
    if (count < bestCount) {
      best = fb;
      bestCount = count;
    }
  }
  return best;
}

async function searchOpenverseImages(query, { signal } = {}) {
  const url = `https://api.openverse.org/v1/images/?q=${encodeURIComponent(query)}&page_size=5`;
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': 'VidzeoMuseDocumentary/1.0', Accept: 'application/json' },
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000),
    });
    if (!res.ok) return [];
    const data = await res.json();
    const urls = [];
    for (const item of Array.isArray(data?.results) ? data.results : []) {
      if (typeof item?.url === 'string' && /^https?:\/\//i.test(item.url)) urls.push(item.url);
    }
    return urls;
  } catch {
    return [];
  }
}

const BROWSER_UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';

function unescapeJsonUrl(raw) {
  return String(raw || '').replace(/\\\//g, '/').replace(/\\u0026/g, '&').replace(/&amp;/g, '&').replace(/&quot;/g, '"');
}

// Pure HTML -> image URL extraction, shared by the Yandex/Bing lanes.
// Yandex embeds originals as "origUrl":"https://..." in data-state JSON;
// Bing embeds them as murl&quot;:&quot;https://...&quot;. The direct-URL
// pattern requires a delimiter right after the extension so it cannot
// span across JSON blobs and return garbage.
function extractImageUrls(html) {
  const out = [];
  const seen = new Set();
  const push = (u) => {
    const url = unescapeJsonUrl(u).split('#')[0];
    // Never accept a URL that spans JSON markup: a real image URL has no
    // braces, brackets, or entity residue in it. Also drop search-engine
    // chrome (Yandex/Bing/Google static hosts serve UI sprites, not photos
    // — a 2.8 KB yastatic.net PNG passed the magic-byte check in live
    // testing and would have shipped as a shot).
    if (!/^https?:\/\//i.test(url) || /[\s{}[\]]/.test(url) || url.includes('&quot;')) return;
    try {
      const host = new URL(url).hostname.toLowerCase();
      if (/(^|\.)(yastatic\.net|bing\.com|google\.com|gstatic\.com|yandex\.(com|ru|com\.tr))$/.test(host)) return;
    } catch { return; }
    if (!seen.has(url)) { seen.add(url); out.push(url); }
  };
  const text = String(html || '').replace(/\\\//g, '/');
  for (const m of text.matchAll(/"origUrl"\s*:\s*"((?:https?:)?\/\/[^"]+)"/g)) {
    push(m[1].startsWith('//') ? `https:${m[1]}` : m[1]);
  }
  for (const m of text.matchAll(/murl(?:&quot;|")\s*:\s*(?:&quot;|")(https?:[^&"\\]+?)(?:&quot;|")/g)) push(m[1]);
  // ';' is excluded so the match cannot span an &quot; entity into a
  // later .jpg; a real image URL almost never carries a ';'.
  for (const m of text.matchAll(/https?:\/\/[^\s"'<>\\;]+?\.(?:jpg|jpeg|png|webp)(?=[\s"'<>&?]|$)/gi)) push(m[0]);
  return out;
}

async function searchYandexImages(query, { signal } = {}) {
  const url = `https://yandex.com/images/search?text=${encodeURIComponent(query)}&isize=large&nomisspell=1`;
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': BROWSER_UA, Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', 'Accept-Language': 'en-US,en;q=0.9', Referer: 'https://yandex.com/images/' },
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(25000)]) : AbortSignal.timeout(25000),
    });
    if (!res.ok) return [];
    const html = await res.text();
    if (/<title[^>]*>\s*Are you not a robot\?/i.test(html)) {
      log.info('[documentary] Yandex image search hit a robot check');
      return [];
    }
    return extractImageUrls(html).slice(0, 8);
  } catch (error) {
    log.info(`[documentary] Yandex image search failed for "${query}": ${error.message}`);
    return [];
  }
}

async function searchBingImages(query, { signal } = {}) {
  const url = `https://www.bing.com/images/search?q=${encodeURIComponent(query)}`;
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': BROWSER_UA, Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', 'Accept-Language': 'en-US,en;q=0.9' },
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(25000)]) : AbortSignal.timeout(25000),
    });
    if (!res.ok) return [];
    return extractImageUrls(await res.text()).slice(0, 8);
  } catch (error) {
    log.info(`[documentary] Bing image search failed for "${query}": ${error.message}`);
    return [];
  }
}

function relaxedImageQueries(query) {
  const words = String(query || '').trim().split(/\s+/).filter(Boolean);
  const out = [];
  const push = (q) => { if (q && !out.includes(q)) out.push(q); };
  push(words.join(' '));
  if (words.length > 4) push(words.slice(0, 4).join(' '));
  if (words.length > 2) push(words.slice(0, 2).join(' '));
  return out.slice(0, 3);
}

async function fetchImageAsset(query, sourceUrl, destStem, { signal } = {}) {
  if (sourceUrl && /^https?:\/\//i.test(sourceUrl)) {
    const saved = await downloadImageUrl(sourceUrl, destStem, { signal });
    if (saved) return { path: saved, source: sourceUrl };
  }
  // Web image lane, Yandex first (the old pipeline's waterfall order):
  // general web search handles game/modern topics that Wikimedia and
  // Openverse are empty for. Full query first, then relaxed variants.
  // Google is 429 from this VM, so it is not a lane.
  const engines = [searchYandexImages, searchBingImages, searchWikimediaImages, searchOpenverseImages];
  for (const q of relaxedImageQueries(query)) {
    for (const search of engines) {
      const urls = await search(q, { signal });
      for (const url of urls) {
        const saved = await downloadImageUrl(url, destStem, { signal });
        if (saved) return { path: saved, source: url };
      }
    }
  }
  return null;
}

async function listYoutubeCandidates(query, sourceUrl, { signal, pythonBin } = {}) {
  const py = pythonBin || await resolvePython();
  const directId = normalizeYoutubeVideoId(sourceUrl || '');
  if (directId) return [{ id: directId, title: '' }];
  const search = `ytsearch8:${query}`;
  const args = [
    '-m', 'yt_dlp',
    '--no-playlist',
    '--no-warnings',
    '--flat-playlist',
    '-J',
    '--no-check-certificates',
    search,
  ];
  try {
    const { stdout } = await runProcess(py, args, { timeoutMs: 90 * 1000, signal, label: 'yt-dlp documentary search' });
    const data = JSON.parse(String(stdout || '').trim());
    const entries = Array.isArray(data?.entries) ? data.entries : [];
    return entries
      .map((e) => ({ id: normalizeYoutubeVideoId(e?.id || e?.url || ''), title: String(e?.title || '').trim() }))
      .filter((e) => e.id);
  } catch (error) {
    log.info(`[documentary] YouTube candidate search failed for "${query}": ${error.message}`);
    return [];
  }
}

async function fetchVideoClip(query, sourceUrl, destPath, durationSec, { signal, pythonBin, width = WIDTH, height = HEIGHT, excludeVideoIds } = {}) {
  const tempRaw = `${destPath}.raw.mp4`;
  const cleanupRaw = () => {
    for (const p of [tempRaw, `${tempRaw}.part`]) {
      try { fs.rmSync(p, { force: true }); } catch { /* ignore */ }
    }
  };
  // List candidates first and skip any video already used by an earlier shot.
  // The old code downloaded ytsearch2's first hit blindly, so one repeated
  // query (or one popular result) filled half the video with the same clip.
  const candidates = await listYoutubeCandidates(query, sourceUrl, { signal, pythonBin });
  const picked = pickUnusedVideoCandidate(candidates, excludeVideoIds);
  if (!picked) {
    log.info(`[documentary] No unused YouTube candidate for "${query}" (all ${candidates.length} already used or search empty)`);
    return null;
  }
  const target = `https://www.youtube.com/watch?v=${picked.id}`;
  const ytdlpArgs = [
    '-m', 'yt_dlp',
    '--no-playlist',
    '--no-warnings',
    '-f', 'bv*[height<=1080][ext=mp4]/bv*[height<=1080]/best[height<=1080]/best',
    '--merge-output-format', 'mp4',
    '-o', tempRaw,
    '--impersonate', 'chrome', '--extractor-args', 'youtube:player_client=android', '--no-check-certificates',
    target,
  ];

  const py = pythonBin || await resolvePython();
  try {
    await runProcess(py, ytdlpArgs, { timeoutMs: 3 * 60 * 1000, signal, label: 'yt-dlp documentary clip' });
  } catch (error) {
    log.info(`[documentary] Clip download failed for "${query}" (id=${picked.id}): ${error.message}`);
    cleanupRaw();
    return null;
  }
  if (!fs.existsSync(tempRaw) || fs.statSync(tempRaw).size < 16384) {
    log.info(`[documentary] Clip download too small/empty for "${query}" (id=${picked.id})`);
    cleanupRaw();
    return null;
  }

  const ffmpegBin = await ffmpeg.resolveBinary('ffmpeg');
  if (!ffmpegBin) {
    cleanupRaw();
    return null;
  }
  const clipDur = Math.max(2, Math.min(30, Math.ceil(Number(durationSec) || 6)));
  const outW = Number(width) || WIDTH;
  const outH = Number(height) || HEIGHT;
  try {
    await runProcess(ffmpegBin, [
      '-y', '-i', tempRaw,
      '-t', clipDur.toFixed(2),
      '-vf', `scale=${outW}:${outH}:force_original_aspect_ratio=increase,crop=${outW}:${outH},fps=${FPS}`,
      '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
      destPath,
    ], { timeoutMs: 2 * 60 * 1000, signal, label: 'ffmpeg documentary trim' });
    const info = await ffmpeg.probeVideo(destPath);
    if (info.hasVideo && info.duration >= 1.5) {
      return { path: destPath, source: target, videoId: picked.id };
    }
    log.info(`[documentary] Trimmed clip failed probe for "${query}" (id=${picked.id})`);
  } catch (error) {
    log.info(`[documentary] Clip trim failed for "${query}" (id=${picked.id}): ${error.message}`);
  } finally {
    cleanupRaw();
  }
  return null;
}

function browserExecutablePath(env = process.env) {
  const candidates = [
    env.REMOTION_BROWSER_EXECUTABLE,
    env.MUSE_REMOTION_CHROME,
    '/opt/chrome-headless-shell-linux64/chrome-headless-shell',
    '/home/hatch/chrome-headless-shell-linux64/chrome-headless-shell',
  ].filter(Boolean);
  return candidates.find((p) => fs.existsSync(p)) || '';
}

function copyTemplateProject(templateDir, projectDir) {
  fs.mkdirSync(projectDir, { recursive: true });
  for (const file of ['package.json', 'README.md']) {
    const src = path.join(templateDir, file);
    if (fs.existsSync(src)) fs.copyFileSync(src, path.join(projectDir, file));
  }
  fs.cpSync(path.join(templateDir, 'src'), path.join(projectDir, 'src'), { recursive: true });
  fs.mkdirSync(path.join(projectDir, 'public', 'audio'), { recursive: true });
  fs.mkdirSync(path.join(projectDir, 'public', 'footage'), { recursive: true });
  fs.mkdirSync(path.join(projectDir, 'public', 'images'), { recursive: true });
}

async function ensureNodeModules(templateDir, projectDir, signal) {
  const targetModules = path.join(projectDir, 'node_modules');
  if (fs.existsSync(targetModules)) return;
  const cachedModules = path.join(templateDir, 'node_modules');
  if (fs.existsSync(cachedModules)) {
    fs.symlinkSync(cachedModules, targetModules, process.platform === 'win32' ? 'junction' : 'dir');
    return;
  }
  const npmBin = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  await runProcess(npmBin, ['install', '--no-audit', '--no-fund'], { cwd: projectDir, timeoutMs: 10 * 60 * 1000, signal, label: 'Remotion npm install' });
}

async function render(job, scriptText, workDir, options = {}) {
  const resolved = resolveVideoStyle(job);
  if (resolved.engine !== ENGINES.DOCUMENTARY) {
    throw new Error(
      `Style isolation violation: Documentary adapter refused job with resolved engine=${resolved.engine} (${resolved.style})`
    );
  }
  assertNoOpenRouterKey();

  const narration = String(scriptText || '').trim();
  if (!narration) throw new Error('Documentary render requires non-empty narration scriptText');

  const runtime = documentaryRuntimeStatus();
  if (!runtime.available) {
    throw new Error(`One-Minute Documentary runtime is missing required files under ${runtime.root}: ${runtime.missing.join(', ')}`);
  }

  const onProgress = typeof options.onProgress === 'function' ? options.onProgress : () => {};
  const signal = options.signal;
  const pythonBin = options.pythonBin || await resolvePython();
  const voice = defaultVoiceForStyle(job);
  const aspect = String(job.documentaryOptions?.aspect || job.videoOptions?.aspect || job.channel?.videoOptions?.aspect || '16:9').trim() || '16:9';
  const isVertical = aspect === '9:16';
  const renderWidth = Number(job.documentaryOptions?.width) || (isVertical ? 1080 : WIDTH);
  const renderHeight = Number(job.documentaryOptions?.height) || (isVertical ? 1920 : HEIGHT);
  const captionsEnabled = job.documentaryOptions?.captionsEnabled !== false && job.videoOptions?.captionsEnabled !== false && job.channel?.videoOptions?.captionsEnabled !== false;
  const targetMinutes = Math.max(1, Number(job.targetDurationMinutes) || Number(job.documentaryOptions?.durationMinutes) || 1);

  const projectDir = path.join(workDir, 'documentary-project');
  const inputDigest = crypto.createHash('sha256').update(JSON.stringify({
    title: job.title || '',
    narration,
    voice,
    aspect,
    captionsEnabled,
    targetMinutes,
    engine: ENGINES.DOCUMENTARY,
  })).digest('hex');

  const markerPath = path.join(projectDir, 'documentary-input.json');
  const checkpointPath = path.join(projectDir, 'checkpoint.json');
  const mediaCheckpointPath = path.join(projectDir, 'media-checkpoint.json');
  let prevMarker = null;
  try { prevMarker = JSON.parse(fs.readFileSync(markerPath, 'utf8')); } catch { /* ignore */ }

  if (!fs.existsSync(path.join(projectDir, 'package.json'))) {
    copyTemplateProject(runtime.templateDir, projectDir);
  } else if (prevMarker && prevMarker.digest !== inputDigest) {
    for (const stale of ['shots-plan.json', 'checkpoint.json', 'media-checkpoint.json', path.join('public', 'audio', 'voiceover.mp3')]) {
      try { fs.rmSync(path.join(projectDir, stale), { force: true }); } catch { /* ignore */ }
    }
  }
  writeJsonAtomic(markerPath, { digest: inputDigest, voice, jobId: job.jobId, updatedAt: new Date().toISOString() });

  const voicePath = path.join(projectDir, 'public', 'audio', 'voiceover.mp3');
  let voiceDurationSec = 0;
  if (fs.existsSync(voicePath) && prevMarker?.digest === inputDigest) {
    try { voiceDurationSec = (await ffmpeg.probeVideo(voicePath)).duration || 0; } catch { voiceDurationSec = 0; }
  }
  if (!voiceDurationSec) {
    log.info(`[documentary] Job ${job.jobId}: synthesizing voiceover (${voice})`);
    onProgress(10, `[documentary] Synthesizing voiceover (${voice})`, true);
    voiceDurationSec = await synthesizeVoiceover(narration, voicePath, { voice, signal, pythonBin });
    log.info(`[documentary] Job ${job.jobId}: voiceover ready (${voiceDurationSec.toFixed(2)}s)`);
  } else {
    log.info(`[documentary] Job ${job.jobId}: reusing voiceover checkpoint (${voiceDurationSec.toFixed(2)}s)`);
  }
  writeStageCheckpoint(checkpointPath, inputDigest, { tts: { durationSec: voiceDurationSec, voice } });

  const totalFrames = Math.max(300, Math.ceil(voiceDurationSec * FPS));
  const inferRequests = [];
  const planFile = path.join(projectDir, 'shots-plan.json');
  let plan = null;
  if (fs.existsSync(planFile) && prevMarker?.digest === inputDigest) {
    try { plan = JSON.parse(fs.readFileSync(planFile, 'utf8')); } catch { plan = null; }
  }
  if (!plan || !Array.isArray(plan.shots) || !plan.shots.length) {
    log.info(`[documentary] Job ${job.jobId}: requesting shot plan via Muse inference (${totalFrames} frames)`);
    onProgress(20, '[documentary] Planning documentary shots and visual queries via Muse', true);
    const reply = await inferWithMuse({
      operation: 'documentary_shots_plan',
      kind: 'text',
      messages: buildShotsPlanMessages({
        title: job.title || 'Documentary',
        scriptText: narration,
        voiceDurationSec,
        targetDurationMinutes: targetMinutes,
        channel: job.channel || {},
      }),
      responseFormat: SHOTS_PLAN_SCHEMA,
      jobId: job.jobId,
      timeoutMs: Number(process.env.MUSE_DOCUMENTARY_PLAN_TIMEOUT_MS) || 15 * 60 * 1000,
      signal,
      workDir,
      inferFn: options.inferFn,
    });
    inferRequests.push({ id: reply.id, operation: 'documentary_shots_plan' });
    plan = reply.data;
    writeJsonAtomic(planFile, { ...plan, inferRequests });
    log.info(`[documentary] Job ${job.jobId}: planned ${Array.isArray(plan?.shots) ? plan.shots.length : 0} shots (tag=${plan?.topicTag || 'DOCUMENTARY'})`);
  } else {
    if (Array.isArray(plan.inferRequests)) inferRequests.push(...plan.inferRequests);
    log.info(`[documentary] Job ${job.jobId}: reusing shots-plan.json checkpoint (${plan.shots.length} shots)`);
  }
  writeStageCheckpoint(checkpointPath, inputDigest, { shotsPlan: { count: plan.shots.length, topicTag: plan.topicTag || '' } });

  const normalizedShots = normalizeShots(plan.shots, totalFrames);
  let savedMediaCheckpoint = null;
  try {
    const parsedMedia = JSON.parse(fs.readFileSync(mediaCheckpointPath, 'utf8'));
    if (parsedMedia?.digest === inputDigest && parsedMedia?.shots && typeof parsedMedia.shots === 'object') {
      savedMediaCheckpoint = parsedMedia;
    }
  } catch { /* ignore */ }
  const mediaCheckpoints = savedMediaCheckpoint?.shots ? { ...savedMediaCheckpoint.shots } : {};

  const mediaSources = [];
  const verifiedFallbacks = [];
  const fallbackUsage = new Map();
  const usedVideoIds = new Set();
  const usedAssetPaths = new Set();
  const resolvedShots = [];
  let videoShotCount = 0;
  let imageShotCount = 0;

  for (let i = 0; i < normalizedShots.length; i += 1) {
    const shot = normalizedShots[i];
    const pct = 28 + Math.round(((i + 1) / normalizedShots.length) * 32);

    let asset = null;
    const cachedShot = mediaCheckpoints[String(i)];
    if (cachedShot && cachedShot.query === shot.query && cachedShot.beat === shot.beat && cachedShot.assetPath) {
      const diskPath = path.join(projectDir, 'public', cachedShot.assetPath);
      try {
        // An old checkpoint may itself repeat one asset across shots; a
        // repeat here is a miss, not a reuse — re-fetch for a distinct clip.
        if (!usedAssetPaths.has(cachedShot.assetPath) && fs.statSync(diskPath).size >= 1024) {
          asset = { assetType: cachedShot.assetType, assetPath: cachedShot.assetPath, source: cachedShot.source || 'checkpoint', videoId: cachedShot.videoId || '' };
          if (asset.videoId) usedVideoIds.add(asset.videoId);
          usedAssetPaths.add(asset.assetPath);
          if (asset.assetType === 'video') videoShotCount += 1;
          else imageShotCount += 1;
          log.info(`[documentary] Shot ${i + 1}/${normalizedShots.length} reused from checkpoint (${asset.assetPath})`);
        }
      } catch { /* fall through to fetch */ }
    }

    if (!asset) {
      log.info(`[documentary] Shot ${i + 1}/${normalizedShots.length} (${shot.beat}, ${shot.kind}): "${shot.query}"`);
      onProgress(pct, `[documentary] Fetching media for shot ${i + 1}/${normalizedShots.length}: ${shot.query.slice(0, 60)}`, true);

      if (shot.kind === 'video') {
        const clipFile = path.join(projectDir, 'public', 'footage', `shot-${i + 1}.mp4`);
        const fetched = await fetchVideoClip(shot.query, shot.sourceUrl, clipFile, shot.dur / FPS, { signal, pythonBin, width: renderWidth, height: renderHeight, excludeVideoIds: usedVideoIds });
        if (fetched) {
          asset = { assetType: 'video', assetPath: `footage/shot-${i + 1}.mp4`, source: fetched.source, videoId: fetched.videoId || '' };
          if (asset.videoId) usedVideoIds.add(asset.videoId);
          usedAssetPaths.add(asset.assetPath);
          videoShotCount += 1;
          log.info(`[documentary] Shot ${i + 1}/${normalizedShots.length}: yt-dlp video ready (${(shot.dur / FPS).toFixed(1)}s, id=${asset.videoId || 'n/a'})`);
        }
      }
      if (!asset) {
        const imgStem = path.join(projectDir, 'public', 'images', `shot-${i + 1}`);
        const fetchedImg = await fetchImageAsset(shot.query, shot.sourceUrl, imgStem, { signal });
        if (fetchedImg) {
          asset = { assetType: 'image', assetPath: `images/${path.basename(fetchedImg.path)}`, source: fetchedImg.source };
          usedAssetPaths.add(asset.assetPath);
          imageShotCount += 1;
          log.info(`[documentary] Shot ${i + 1}/${normalizedShots.length}: verified image ready`);
        }
      }
      if (!asset && shot.kind === 'image') {
        // Cross-lane second chance: an image shot whose stills lanes are
        // empty gets a fresh related clip before any repeat. A fresh clip
        // beats a repeated clip; the manifest counts it honestly as video.
        const clipFile = path.join(projectDir, 'public', 'footage', `shot-${i + 1}.mp4`);
        const fetched = await fetchVideoClip(shot.query, shot.sourceUrl, clipFile, shot.dur / FPS, { signal, pythonBin, width: renderWidth, height: renderHeight, excludeVideoIds: usedVideoIds });
        if (fetched) {
          asset = { assetType: 'video', assetPath: `footage/shot-${i + 1}.mp4`, source: fetched.source, videoId: fetched.videoId || '' };
          if (asset.videoId) usedVideoIds.add(asset.videoId);
          usedAssetPaths.add(asset.assetPath);
          videoShotCount += 1;
          log.info(`[documentary] Shot ${i + 1}/${normalizedShots.length}: yt-dlp video ready via cross-lane fallback (${(shot.dur / FPS).toFixed(1)}s, id=${asset.videoId || 'n/a'})`);
        }
      }
      if (!asset && verifiedFallbacks.length) {
        // Last resort only: spread repeats across the least-used verified
        // asset instead of round-robin by index, which kept re-picking
        // the same first clip.
        const reused = pickLeastUsedFallback(verifiedFallbacks, fallbackUsage);
        asset = { ...reused };
        fallbackUsage.set(reused.assetPath, (fallbackUsage.get(reused.assetPath) || 0) + 1);
        if (reused.assetType === 'video') videoShotCount += 1;
        else imageShotCount += 1;
        log.info(`[documentary] Shot ${i + 1}/${normalizedShots.length}: reused verified asset ${reused.assetPath} (least-used fallback)`);
      }
      if (!asset) {
        throw new Error(`Documentary shot ${i + 1} ("${shot.query}") could not retrieve any verified video clip or real image.`);
      }
      mediaCheckpoints[String(i)] = { query: shot.query, beat: shot.beat, ...asset };
      writeJsonAtomic(mediaCheckpointPath, { digest: inputDigest, updatedAt: new Date().toISOString(), shots: mediaCheckpoints });
    }

    verifiedFallbacks.push(asset);
    fallbackUsage.set(asset.assetPath, fallbackUsage.get(asset.assetPath) || 0);
    mediaSources.push(asset.source);
    resolvedShots.push({ ...shot, ...asset });
  }
  writeStageCheckpoint(checkpointPath, inputDigest, { mediaReady: { totalShots: resolvedShots.length, videoShotCount, imageShotCount } });

  log.info(`[documentary] Job ${job.jobId}: building Remotion project (${videoShotCount} video, ${imageShotCount} image shots)`);
  onProgress(64, '[documentary] Writing Remotion composition from documentary template', true);
  fs.writeFileSync(
    path.join(projectDir, 'src', 'Composition.tsx'),
    generateCompositionTsx({
      topicTag: plan.topicTag || job.channel?.name || 'DOCUMENTARY',
      headerText: plan.headerText || job.title || '',
      shots: resolvedShots,
      captionsEnabled,
    }),
    'utf8'
  );
  fs.writeFileSync(path.join(projectDir, 'src', 'RemotionRoot.tsx'), generateRemotionRootTsx(totalFrames, { width: renderWidth, height: renderHeight }), 'utf8');
  writeStageCheckpoint(checkpointPath, inputDigest, { compositionWritten: { width: renderWidth, height: renderHeight, totalFrames } });

  await ensureNodeModules(runtime.templateDir, projectDir, signal);

  log.info(`[documentary] Job ${job.jobId}: starting Remotion render (${renderWidth}x${renderHeight}@${FPS}fps, ${totalFrames} frames)`);
  onProgress(70, `[documentary] Rendering ${renderWidth}x${renderHeight} master with Remotion`, true);
  const rawVideo = path.join(workDir, 'video.mp4');
  const npxBin = process.platform === 'win32' ? 'npx.cmd' : 'npx';
  const renderArgs = ['remotion', 'render', 'src/index.ts', 'Documentary', `--output=${rawVideo}`, '--codec=h264', '--overwrite'];
  const concurrency = Math.max(0, Math.min(8, Math.floor(Number(process.env.MUSE_REMOTION_CONCURRENCY) || 0)));
  if (concurrency > 0) renderArgs.push(`--concurrency=${concurrency}`);
  const browserExec = browserExecutablePath();
  if (browserExec) renderArgs.push(`--browser-executable=${browserExec}`);

  const reportFrame = createFrameProgressReporter(onProgress, {
    prefix: '[documentary]',
    basePct: 70,
    spanPct: 25,
    logger: log,
  });
  await runProcess(npxBin, renderArgs, {
    cwd: projectDir,
    timeoutMs: Math.max(30 * 60 * 1000, Math.round(voiceDurationSec * 25000)),
    signal,
    label: 'Remotion documentary render',
    onStdoutLine: (line) => {
      const m = line.match(/(\d+)\s*\/\s*(\d+)/);
      if (m) reportFrame(m[1], m[2]);
    },
  });

  if (!fs.existsSync(rawVideo)) {
    throw new Error('Remotion documentary render finished without producing video.mp4');
  }

  const manifestPath = stampDocumentaryManifest(rawVideo, {
    jobId: job.jobId,
    topicTag: plan.topicTag || '',
    headerText: plan.headerText || '',
    voice,
    durationSeconds: voiceDurationSec,
    shotCount: resolvedShots.length,
    videoShotCount,
    imageShotCount,
    mediaSources,
    inferRequests,
  });

  return {
    videoPath: rawVideo,
    thumbnailPath: '',
    meta: {
      engine: ENGINES.DOCUMENTARY,
      videoStyle: resolved.style,
      skill: 'one_minute_documentary',
      voice,
      durationSeconds: voiceDurationSec,
      shotCount: resolvedShots.length,
      videoShotCount,
      imageShotCount,
      documentaryManifestPath: manifestPath,
      inferRequests,
    },
  };
}

module.exports = {
  FPS,
  WIDTH,
  HEIGHT,
  SHOTS_PLAN_SCHEMA,
  documentaryRoot,
  documentaryRuntimeStatus,
  buildShotsPlanMessages,
  normalizeShots,
  generateCompositionTsx,
  generateRemotionRootTsx,
  isValidImageBuffer,
  normalizeYoutubeVideoId,
  pickUnusedVideoCandidate,
  pickLeastUsedFallback,
  relaxedImageQueries,
  extractImageUrls,
  fetchImageAsset,
  searchYandexImages,
  searchBingImages,
  searchOpenverseImages,
  selectTtsEngine,
  synthesizeVoiceover,
  render,
};
