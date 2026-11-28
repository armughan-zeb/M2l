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
const { assertNoOpenRouterKey, bridgeRoot } = require('../lib/museInference');
const { resolveVideoStyle, ENGINES, defaultVoiceForStyle } = require('../lib/videoStyle');
const { stampDocumentaryManifest } = require('../lib/masterManifestGuard');
const { cleanGeneratedCode, runProcess, inferWithMuse, createFrameProgressReporter, readStageCheckpoint, writeStageCheckpoint } = require('../lib/museBridgeInfer');

const FPS = 30;
const WIDTH = 1920;
const HEIGHT = 1080;

// --- Media budgets (Franklin job r_mwztjy7reed4a4: 19 min for 7 shots) ---
// YouTube is BOUNDED per scene; the web image lane is not (per user order
// the image lane itself has no per-request timeout — a slow-but-working
// fetch is never killed on a budget). What bounds a shot is the watchdog:
// agent lane + YouTube phase, then the whole shot. A genuine stall falls
// through to the next lane and finally to a graphic title card, so media
// always finishes for every shot and never stalls silently.
// All values are env-overridable in milliseconds unless noted.
function envInt(name, fallback, env = process.env) {
  const raw = Number(env[name]);
  return Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : fallback;
}
// Fresh budgets on every call (env is re-read, so tests and the daemon
// can tune a run without reloading the module). MEDIA_BUDGETS is the
// load-time snapshot, kept for inspection/back-compat.
function mediaBudgets(env = process.env) {
  return {
    // Full agent-lane budget once a worker has CLAIMED the request (a
    // claimed request means a worker is composing; cutting it short would
    // discard real work). Unclaimed requests die far earlier — see the
    // claim grace below.
    agentTimeoutMs: envInt('MUSE_DOCUMENTARY_MEDIA_AGENT_TIMEOUT_MS', 120000, env),
    // Unanswered lane request: if no worker claims any fresh
    // documentary_media request within this grace, the lane is a miss and
    // the shot falls through to the direct engines. Franklin's 7/7 misses
    // each cost the full 120s; a miss now costs ~this.
    agentClaimGraceMs: envInt('MUSE_DOCUMENTARY_MEDIA_CLAIM_GRACE_MS', 20000, env),
    // yt-dlp search (ytsearch8 metadata only).
    ytSearchTimeoutMs: envInt('MUSE_DOCUMENTARY_YT_SEARCH_TIMEOUT_MS', 60000, env),
    // yt-dlp download of one clip. Franklin's shot 6 burned ~10 min in
    // yt-dlp's default 10-retry curl-error-56 loop; fragment/HTTP retries
    // are now capped (see fetchVideoClip) and the whole download dies here.
    ytDownloadTimeoutMs: envInt('MUSE_DOCUMENTARY_YT_DOWNLOAD_TIMEOUT_MS', 120000, env),
    ytFragmentRetries: envInt('MUSE_DOCUMENTARY_YT_FRAGMENT_RETRIES', 2, env),
    ytSocketTimeoutSec: envInt('MUSE_DOCUMENTARY_YT_SOCKET_TIMEOUT_S', 15, env),
    // ffmpeg trim of a downloaded clip.
    ytTrimTimeoutMs: envInt('MUSE_DOCUMENTARY_YT_TRIM_TIMEOUT_MS', 120000, env),
    // Direct YouTube phase (search/download/trim). Firing skips the
    // rest of the clip attempt and moves the shot to the image lane —
    // it never jumps straight to graphic while an image lane is still
    // untried.
    ytPhaseBudgetMs: envInt('MUSE_DOCUMENTARY_YT_PHASE_BUDGET_MS', 300000, env),
    // Whole shot: direct clip + image + agent lanes. Firing aborts
    // whatever is still in flight (via the shot AbortSignal) and the
    // shot falls back to the agent lane, then a generated graphic
    // title card. 8 min covers the legitimate worst case (direct YT
    // search 60s + download 120s + trim 120s + image lane + agent
    // lane 120s) with headroom; a shot past this is a stall, not a
    // slow success.
    shotBudgetMs: envInt('MUSE_DOCUMENTARY_SHOT_BUDGET_MS', 480000, env),
  };
}
const MEDIA_BUDGETS = mediaBudgets();

// Pool heartbeats older than this mean the pool is not live — same
// threshold the spool transport uses to report MUSE_TRANSPORT_UNAVAILABLE.
const POOL_HEARTBEAT_MAX_AGE_MS = 90000;

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

function defaultTemplateDir(env = process.env) {
  return path.join(documentaryRoot(env), 'templates', 'remotion-project');
}

// Template contract (built by another agent): lib/documentaryTemplates.js
// exports resolveDocumentaryTemplate(job) -> { id, dir, label }. Until
// that file exists, this falls back to the default template dir — the
// adapter never fails a job for a missing template helper, and a template
// whose dir is missing on disk also falls back (with a warning) instead
// of rendering into a half-copied project.
function resolveDocumentaryTemplate(job) {
  const fallback = { id: 'default', dir: defaultTemplateDir(), label: 'default' };
  let helper;
  try {
    helper = require('../lib/documentaryTemplates');
  } catch (error) {
    if (error && error.code !== 'MODULE_NOT_FOUND') {
      log.warn(`[documentary] documentaryTemplates helper failed to load (${error.message}); using default template`);
    }
    return fallback;
  }
  if (!helper || typeof helper.resolveDocumentaryTemplate !== 'function') return fallback;
  let picked = null;
  try {
    picked = helper.resolveDocumentaryTemplate(job || {});
  } catch (error) {
    log.warn(`[documentary] resolveDocumentaryTemplate threw (${error.message}); using default template`);
    return fallback;
  }
  if (!picked || typeof picked.dir !== 'string' || !picked.dir.trim()) return fallback;
  if (!fs.existsSync(picked.dir)) {
    log.warn(`[documentary] Template "${picked.id || '?'}" dir missing (${picked.dir}); using default template`);
    return fallback;
  }
  return {
    id: String(picked.id || 'default'),
    dir: picked.dir,
    label: String(picked.label || picked.id || 'default'),
  };
}

function checkTemplateDir(templateDir) {
  const required = [
    path.join(templateDir, 'package.json'),
    path.join(templateDir, 'src', 'Composition.tsx'),
    path.join(templateDir, 'src', 'RemotionRoot.tsx'),
    path.join(templateDir, 'src', 'index.ts'),
  ];
  const missing = required.filter((file) => !fs.existsSync(file));
  return { available: missing.length === 0, templateDir, missing };
}

function documentaryRuntimeStatus(env = process.env) {
  const root = documentaryRoot(env);
  const templateDir = defaultTemplateDir(env);
  const template = checkTemplateDir(templateDir);
  const missing = [
    path.join(root, 'SKILL.md'),
    path.join(root, 'skill.yaml'),
    ...template.missing,
  ].filter((file) => !fs.existsSync(file));
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
      // No timeout: media-first order — a web search never dies on a
      // budget, only on the job's own stop signal.
      signal,
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
      signal,
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
      signal,
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
      signal,
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
      signal,
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

const MEDIA_AGENT_SCHEMA = {
  type: 'json_schema',
  json_schema: {
    name: 'documentary_media',
    schema: {
      type: 'object',
      additionalProperties: false,
      required: ['candidates'],
      properties: {
        candidates: {
          type: 'array',
          items: {
            type: 'object',
            additionalProperties: false,
            required: ['type'],
            properties: {
              type: { type: 'string', enum: ['video', 'image'] },
              videoId: { type: 'string' },
              url: { type: 'string' },
              title: { type: 'string' },
              source: { type: 'string' },
            },
          },
        },
      },
    },
  },
};

function buildMediaAgentMessages({ title, topicTag, shot }) {
  const system = 'You find real media for a documentary shot. Use web search and video search to find real, existing images and YouTube videos that match the shot. Never invent a URL or a video ID: every candidate must come from a real search result you observed. Prefer the exact subject of the shot (people, places, events) over generic stock. Return only candidates you actually found.';
  const user = [
    `Video: ${title}`,
    `Topic tag: ${topicTag}`,
    `Shot beat: ${shot.beat}`,
    `Shot kind: ${shot.kind} (prefer this kind, but a strong candidate of the other kind is useful too)`,
    `Shot query: ${shot.query}`,
    `Shot length: ${Math.max(2, Math.round((shot.dur || 180) / FPS))}s`,
    'Return up to 4 candidates, best first. For a video candidate give the 11-character YouTube videoId. For an image candidate give the direct image URL (a link that serves the image file itself, not a search page).',
  ].join('\n');
  return [
    { role: 'system', content: system },
    { role: 'user', content: user },
  ];
}

function normalizeAgentMediaCandidates(data) {
  const list = Array.isArray(data?.candidates) ? data.candidates : [];
  const out = [];
  const seen = new Set();
  for (const c of list) {
    if (out.length >= 4) break;
    if (!c || (c.type !== 'video' && c.type !== 'image')) continue;
    if (c.type === 'video') {
      const id = normalizeYoutubeVideoId(c.videoId || c.url || '');
      if (!id || seen.has(`v:${id}`)) continue;
      seen.add(`v:${id}`);
      out.push({ type: 'video', videoId: id, title: String(c.title || '').trim(), source: String(c.source || '').trim() });
    } else {
      const url = String(c.url || '').trim();
      if (!/^https?:\/\//i.test(url) || seen.has(`i:${url}`)) continue;
      seen.add(`i:${url}`);
      out.push({ type: 'image', url, title: String(c.title || '').trim(), source: String(c.source || '').trim() });
    }
  }
  return out;
}

// --- Agent-lane liveness: make an unanswered lane cheap -----------------
// The lane posts through the bridge inbox, and the resident pool claims
// spool requests by atomically renaming `<id>.request.json` to
// `<id>.request.json.claimed-<worker>` (scripts/muse-inference-spool.cjs).
// Two guards keep a dead/ignorant pool from costing a shot its full
// 120s budget (Franklin: 7/7 misses x 120s):
//  1. mediaPoolLiveness() — before posting, check the pool is actually
//     alive (fresh worker heartbeat, same 90s rule as the spool
//     transport). A dead pool skips the lane in ~0s.
//  2. waitForMediaPoolClaim() — after posting, watch the spool (and the
//     bridge inbox) for a claim/answer on any fresh documentary_media
//     request. A request that sits unclaimed past the claim grace is a
//     miss: the lane is aborted and the shot falls through to the direct
//     engines. A claim means a worker IS composing, so the lane then runs
//     to its full budget. The watch is pool-level, not per-request: with
//     3 shots in parallel, one claim proves the pool knows the op.
function spoolDirForMedia(env = process.env) {
  const explicit = String(env.MUSE_INFERENCE_SPOOL_DIR || '').trim();
  if (explicit) return explicit;
  try {
    return path.join(bridgeRoot(env), '..', 'inference-spool');
  } catch {
    return '';
  }
}

function mediaPoolLiveness(env = process.env) {
  const dir = spoolDirForMedia(env);
  if (!dir) return { live: false, reason: 'no spool dir configured' };
  let heartbeats = 0;
  let youngestAgeMs = Infinity;
  try {
    for (const entry of fs.readdirSync(dir)) {
      if (!entry.startsWith('worker-alive-') || !entry.endsWith('.json')) continue;
      heartbeats += 1;
      try {
        youngestAgeMs = Math.min(youngestAgeMs, Date.now() - fs.statSync(path.join(dir, entry)).mtimeMs);
      } catch { /* ignore */ }
    }
  } catch {
    return { live: false, reason: `spool dir unreadable (${dir})` };
  }
  if (heartbeats > 0 && youngestAgeMs <= POOL_HEARTBEAT_MAX_AGE_MS) {
    return { live: true, reason: `${heartbeats} heartbeat file(s), youngest ${Math.round(youngestAgeMs / 1000)}s ago` };
  }
  // Fallback: the supervisor's pool-status.json (workers>0, fresh).
  try {
    const status = JSON.parse(fs.readFileSync(path.join(dir, 'pool-status.json'), 'utf8'));
    const ageMs = Date.now() - Number(status.updatedAt || 0);
    if (Number(status.workers) > 0 && status.ownerResponsive !== false && ageMs <= 5 * 60 * 1000) {
      return { live: true, reason: `pool-status reports ${status.workers} worker(s)` };
    }
  } catch { /* ignore */ }
  return {
    live: false,
    reason: heartbeats > 0
      ? `youngest worker heartbeat ${Math.round(youngestAgeMs / 1000)}s ago (stale)`
      : 'no worker heartbeat in spool dir',
  };
}

// Scan the spool dir + bridge inbox for fresh documentary_media requests.
// Returns { unclaimed, claimed, answered } counts for requests created
// at/after sinceMs (5s of slack for file timing). A claim file carries
// the renamed request, so its operation is checkable; an answer file is
// transient (deleted on delivery) and carries no operation, so any fresh
// answer/error counts as pool-responsive — a pool answering other work
// while our request waits is busy, not ignorant, and must not be aborted.
function scanMediaPoolClaims({ spoolDir, bridgeDir, sinceMs }) {
  let unclaimed = 0;
  let claimed = 0;
  let answered = 0;
  const fresh = (mtimeMs) => Number.isFinite(mtimeMs) && mtimeMs >= sinceMs - 5000;
  if (spoolDir) {
    let entries = [];
    try { entries = fs.readdirSync(spoolDir); } catch { entries = []; }
    for (const entry of entries) {
      const claimMatch = entry.match(/^([a-f0-9]{32})\.request\.json(\.claimed-.*)?$/);
      if (claimMatch) {
        const full = path.join(spoolDir, entry);
        let stat = null;
        try { stat = fs.statSync(full); } catch { continue; }
        if (!fresh(stat.mtimeMs)) continue;
        let operation = '';
        try { operation = JSON.parse(fs.readFileSync(full, 'utf8'))?.request?.operation || ''; } catch { /* ignore */ }
        if (operation !== 'documentary_media') continue;
        if (claimMatch[2]) claimed += 1;
        else unclaimed += 1;
        continue;
      }
      if (/^[a-f0-9]{32}\.(answer|error)\.json$/.test(entry)) {
        try {
          if (fresh(fs.statSync(path.join(spoolDir, entry)).mtimeMs)) answered += 1;
        } catch { /* ignore */ }
      }
    }
  }
  if (bridgeDir) {
    let entries = [];
    try { entries = fs.readdirSync(bridgeDir); } catch { entries = []; }
    for (const entry of entries) {
      if (!/^[a-f0-9]{32}$/.test(entry)) continue;
      const dir = path.join(bridgeDir, entry);
      let responseStat = null;
      try { responseStat = fs.statSync(path.join(dir, 'response.json')); } catch { continue; }
      if (!fresh(responseStat.mtimeMs)) continue;
      let operation = '';
      try { operation = JSON.parse(fs.readFileSync(path.join(dir, 'request.json'), 'utf8'))?.operation || ''; } catch { /* ignore */ }
      if (operation === 'documentary_media') answered += 1;
    }
  }
  return { unclaimed, claimed, answered };
}

// Resolves true as soon as any fresh documentary_media request is claimed
// or answered; false when the grace expires with >=1 fresh request still
// unclaimed and nothing claimed/answered. Resolves false on abort too
// (caller treats abort as "stop watching"). When NO fresh request is
// visible yet (bridge forwarding lag, or a transport that writes no
// files), it keeps watching until aborted — absence alone never aborts
// a lane that may just be slow to post.
async function waitForMediaPoolClaim({ sinceMs, graceMs, signal, pollMs = 1000, env = process.env } = {}) {
  const spoolDir = spoolDirForMedia(env);
  let bridgeDir = '';
  try { bridgeDir = bridgeRoot(env); } catch { bridgeDir = ''; }
  const deadline = Date.now() + Math.max(1000, Number(graceMs) || mediaBudgets().agentClaimGraceMs);
  while (true) {
    if (signal?.aborted) return false;
    const seen = scanMediaPoolClaims({ spoolDir, bridgeDir, sinceMs });
    if (seen.claimed > 0 || seen.answered > 0) return true;
    if (Date.now() >= deadline && seen.unclaimed > 0) return false;
    // Past the deadline with nothing fresh posted yet: keep watching at
    // the normal poll interval (absence alone never aborts the lane).
    const waitMs = Date.now() >= deadline ? pollMs : Math.min(pollMs, Math.max(50, deadline - Date.now()));
    await new Promise((resolve) => {
      const timer = setTimeout(resolve, waitMs);
      if (typeof timer.unref === 'function') timer.unref();
      if (signal) signal.addEventListener('abort', () => { clearTimeout(timer); resolve(); }, { once: true });
    });
    // Loop re-checks: a claim that lands just after the deadline is
    // still a claim — the next scan returns true before the abort fires.
    if (signal?.aborted) return false;
    const late = scanMediaPoolClaims({ spoolDir, bridgeDir, sinceMs });
    if (late.claimed > 0 || late.answered > 0) return true;
    if (Date.now() >= deadline && late.unclaimed > 0) return false;
  }
}

// Agent-first media lane: ask Muse (the resident inference pool) to find
// real clips/images for a shot, the same way a direct chat with Muse does.
// Best-effort with a bounded budget: a dead pool is skipped before
// posting, an unclaimed request is aborted at the claim grace, and any
// other failure falls through to the direct engines below, so a slow or
// empty pool can never stall the shot.
async function fetchAgentMediaCandidates({ shot, title, topicTag, jobId, workDir, signal, timeoutMs, inferFn }) {
  const budgets = mediaBudgets();
  const budget = Math.max(10000, Math.min(300000, Number(timeoutMs) || budgets.agentTimeoutMs));
  // The spool/claim guards only apply to the bridge path: an HTTP
  // transport answers directly (no spool files), and an injected inferFn
  // (tests) bypasses the pool entirely.
  const usePoolGuards = !process.env.MUSE_INFERENCE_URL && typeof inferFn !== 'function';
  if (usePoolGuards) {
    const liveness = mediaPoolLiveness();
    if (!liveness.live) {
      log.info(`[documentary] Agent media lane skipped for "${shot.query}": pool not live (${liveness.reason})`);
      return [];
    }
  }
  const laneController = new AbortController();
  if (signal) {
    if (signal.aborted) laneController.abort();
    else signal.addEventListener('abort', () => laneController.abort(), { once: true });
  }
  const laneSignal = signal ? AbortSignal.any([signal, laneController.signal]) : laneController.signal;
  const laneDone = new AbortController();
  const startedAt = Date.now();
  if (usePoolGuards) {
    waitForMediaPoolClaim({
      sinceMs: startedAt,
      graceMs: budgets.agentClaimGraceMs,
      signal: AbortSignal.any([laneSignal, laneDone.signal]),
    }).then((claimed) => {
      if (!claimed && !laneDone.signal.aborted) {
        log.info(`[documentary] Agent media lane missed for "${shot.query}": no worker claimed the request within ${Math.round(budgets.agentClaimGraceMs / 1000)}s; falling through to the graphic fallback`);
        laneController.abort();
      }
    }).catch(() => { /* watcher is best-effort */ });
  }
  try {
    const reply = await inferWithMuse({
      operation: 'documentary_media',
      kind: 'text',
      messages: buildMediaAgentMessages({ title, topicTag, shot }),
      responseFormat: MEDIA_AGENT_SCHEMA,
      jobId,
      timeoutMs: budget,
      signal: laneSignal,
      workDir,
      inferFn,
    });
    const candidates = normalizeAgentMediaCandidates(reply.data);
    if (candidates.length) {
      log.info(`[documentary] Agent media lane found ${candidates.length} candidate(s) for "${shot.query}"`);
    }
    return candidates;
  } catch (error) {
    log.info(`[documentary] Agent media lane missed for "${shot.query}": ${String(error?.message || error).slice(0, 160)}`);
    return [];
  } finally {
    laneDone.abort();
  }
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
    const { stdout } = await runProcess(py, args, { timeoutMs: mediaBudgets().ytSearchTimeoutMs, signal, label: 'yt-dlp documentary search' });
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
  // Reserve the pick immediately: parallel shots share this set, and
  // without a reservation two shots can pick the same video before
  // either download finishes. Released on every failure path below.
  if (excludeVideoIds instanceof Set) excludeVideoIds.add(picked.id);
  const target = `https://www.youtube.com/watch?v=${picked.id}`;
  // Bounded download: yt-dlp's defaults retry a stalled/curl-56 download
  // 10 times (Franklin shot 6 burned ~10 min that way) and a wedged
  // socket can hang with no timeout at all. Retries and the socket
  // timeout are capped here, and runProcess kills the whole download at
  // the download budget — a failed clip falls through to the image lane
  // within minutes instead of stalling the shot.
  const clipBudgets = mediaBudgets();
  // Only the first clipDur seconds are ever used (ffmpeg trims to that
  // below), so fetch only that section: a full-video download is 35–79
  // MiB at ~1 MiB/s from this VM and times out the 120s budget, while
  // the section is ~1–3 MiB and lands in seconds. Proven in the Franklin
  // proof run: all three wave-1 downloads timed out on full videos.
  const clipDur = Math.max(2, Math.min(30, Math.ceil(Number(durationSec) || 6)));
  const ytdlpArgs = [
    '-m', 'yt_dlp',
    '--no-playlist',
    '--no-warnings',
    '--retries', String(clipBudgets.ytFragmentRetries),
    '--fragment-retries', String(clipBudgets.ytFragmentRetries),
    '--socket-timeout', String(clipBudgets.ytSocketTimeoutSec),
    '--retry-sleep', '2',
    '--download-sections', `*0-${clipDur}`,
    '-f', 'bv*[height<=1080][ext=mp4]/bv*[height<=1080]/best[height<=1080]/best',
    '--merge-output-format', 'mp4',
    '-o', tempRaw,
    '--impersonate', 'chrome', '--extractor-args', 'youtube:player_client=android', '--no-check-certificates',
    target,
  ];

  const py = pythonBin || await resolvePython();
  try {
    await runProcess(py, ytdlpArgs, { timeoutMs: clipBudgets.ytDownloadTimeoutMs, signal, label: 'yt-dlp documentary clip' });
  } catch (error) {
    const ageGated = /sign in to confirm your age/i.test(String(error?.message || ''));
    log.info(`[documentary] Clip download failed for "${query}" (id=${picked.id}${ageGated ? ', age-gated' : ''}): ${error.message}`);
    if (excludeVideoIds instanceof Set) excludeVideoIds.delete(picked.id);
    cleanupRaw();
    return null;
  }
  if (!fs.existsSync(tempRaw) || fs.statSync(tempRaw).size < 16384) {
    log.info(`[documentary] Clip download too small/empty for "${query}" (id=${picked.id})`);
    if (excludeVideoIds instanceof Set) excludeVideoIds.delete(picked.id);
    cleanupRaw();
    return null;
  }

  const ffmpegBin = await ffmpeg.resolveBinary('ffmpeg');
  if (!ffmpegBin) {
    cleanupRaw();
    return null;
  }
  const outW = Number(width) || WIDTH;
  const outH = Number(height) || HEIGHT;
  try {
    await runProcess(ffmpegBin, [
      '-y', '-i', tempRaw,
      '-t', clipDur.toFixed(2),
      '-vf', `scale=${outW}:${outH}:force_original_aspect_ratio=increase,crop=${outW}:${outH},fps=${FPS}`,
      '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
      destPath,
    ], { timeoutMs: clipBudgets.ytTrimTimeoutMs, signal, label: 'ffmpeg documentary trim' });
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
  if (excludeVideoIds instanceof Set) excludeVideoIds.delete(picked.id);
  return null;
}

// Last-resort graphic: a branded title card for a shot whose every lane
// failed or whose watchdog fired. Real media always wins over this — it
// only fires when the alternative is a hole in the video. The Remotion
// caption overlay already carries the shot title/sub, so the card is a
// dark background with the title burned in (DejaVu Bold ships on the VM);
// if drawtext fails, a plain dark card is still a valid frame.
async function makeGraphicFallback(shot, destPath, { signal, width = WIDTH, height = HEIGHT } = {}) {
  const ffmpegBin = await ffmpeg.resolveBinary('ffmpeg');
  if (!ffmpegBin) return null;
  fs.mkdirSync(path.dirname(destPath), { recursive: true });
  const outW = Number(width) || WIDTH;
  const outH = Number(height) || HEIGHT;
  const title = String(shot?.title || 'DOCUMENTARY').toUpperCase().replace(/[^A-Za-z0-9 .,!?'&-]/g, '').trim().slice(0, 48) || 'DOCUMENTARY';
  const escaped = title.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/:/g, '\\:');
  const fontFile = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf';
  const attempts = [];
  if (fs.existsSync(fontFile)) {
    attempts.push(`drawtext=fontfile=${fontFile}:text='${escaped}':fontsize=72:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2`);
  }
  attempts.push(null); // plain dark card
  for (const drawtext of attempts) {
    const vf = drawtext ? `,${drawtext}` : '';
    try {
      await runProcess(ffmpegBin, [
        '-y', '-f', 'lavfi', '-i', `color=c=0x141414:s=${outW}x${outH}:d=1`,
        '-vf', `format=yuv420p${vf}`,
        '-frames:v', '1', destPath,
      ], { timeoutMs: 30000, signal, label: 'ffmpeg documentary graphic fallback' });
      if (fs.existsSync(destPath) && fs.statSync(destPath).size >= 1024) return destPath;
    } catch (error) {
      log.info(`[documentary] Graphic fallback attempt failed for "${shot?.query || ''}": ${error.message}`);
    }
  }
  try { fs.rmSync(destPath, { force: true }); } catch { /* ignore */ }
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

// Media stage: resolve every shot in parallel (default 3 at a time),
// direct engines first, agent lane only as fallback. Direct search
// (yt-dlp + web images) is the fastest path — a clip lands in ~40s
// with no pool round-trip — so a shot that direct resolves never
// touches the lane. The agent lane (Muse finding clips/images the way
// a direct chat does) runs only for shots direct could not resolve,
// as the last hope before the graphic fallback. A shot that cannot be
// resolved in the parallel pass gets a second, sequential pass over the
// least-used verified assets — repeats are a last resort, never a
// first choice, and never across the parallel pass itself.
//
// Per-scene watchdog (no mid-job stops): every shot runs under two
// deadlines. The YouTube phase (direct yt-dlp search/download/trim)
// is raced against the YT-phase budget; firing aborts the YouTube
// work and moves the shot to the image lane. The whole shot is raced
// against the shot budget; firing aborts whatever is still in flight
// and the shot falls back to the agent lane, then a graphic title
// card. The image lane's own fetches have no per-request timeout
// (per user order) — the watchdog only fires when the lane is
// genuinely stalled, never on a slow success. The stage always
// returns an asset for every shot (real clip, real image, or graphic)
// unless even a graphic cannot be produced, in which case it throws
// — there is nothing left to render with.
//
// Lane functions are injectable via `overrides` ({ agentMediaFn,
// videoClipFn, imageAssetFn, graphicFn }, same signatures as the module
// functions) so tests can stall a lane without touching the network.
function raceAgainstDeadline(promise, deadlineMs) {
  const remaining = deadlineMs - Date.now();
  if (remaining <= 0) return Promise.resolve({ timedOut: true });
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve({ timedOut: true }), remaining);
    if (typeof timer.unref === 'function') timer.unref();
    promise.then(
      (value) => { clearTimeout(timer); resolve({ value }); },
      (error) => { clearTimeout(timer); resolve({ error }); },
    );
  });
}

async function resolveDocumentaryMedia({
  shots, projectDir, workDir, job, topicTag, signal, pythonBin,
  renderWidth, renderHeight, onProgress, mediaCheckpoints: savedCheckpoints,
  mediaCheckpointPath, inputDigest, overrides = {},
}) {
  const agentMediaFn = overrides.agentMediaFn || fetchAgentMediaCandidates;
  const videoClipFn = overrides.videoClipFn || fetchVideoClip;
  const imageAssetFn = overrides.imageAssetFn || fetchImageAsset;
  const graphicFn = overrides.graphicFn || makeGraphicFallback;
  const budgets = mediaBudgets();
  const mediaCheckpoints = savedCheckpoints ? { ...savedCheckpoints } : {};

  const mediaSources = [];
  const verifiedFallbacks = [];
  const fallbackUsage = new Map();
  const usedVideoIds = new Set();
  const usedAssetPaths = new Set();
  const resolvedShots = [];
  let videoShotCount = 0;
  let imageShotCount = 0;
  let graphicShotCount = 0;

  const mediaConcurrency = Math.max(1, Math.min(6, Math.floor(Number(process.env.MUSE_DOCUMENTARY_MEDIA_CONCURRENCY) || 3)));
  const mediaResults = new Array(shots.length).fill(null);
  let mediaResolvedCount = 0;

  const writeMediaCheckpoint = () => {
    writeJsonAtomic(mediaCheckpointPath, { digest: inputDigest, updatedAt: new Date().toISOString(), shots: mediaCheckpoints });
  };
  const wireJobAbort = (controller) => {
    if (!signal) return;
    if (signal.aborted) controller.abort();
    else signal.addEventListener('abort', () => controller.abort(), { once: true });
  };

  const resolveShotMedia = async (i) => {
    const shot = shots[i];
    const pct = () => 28 + Math.round((mediaResolvedCount / shots.length) * 32);
    const shotStartedAt = Date.now();
    const shotDeadline = shotStartedAt + budgets.shotBudgetMs;
    const ytDeadline = shotStartedAt + Math.min(budgets.ytPhaseBudgetMs, budgets.shotBudgetMs);
    // Separate controllers per phase: a YouTube-phase timeout must abort
    // the YouTube work WITHOUT killing the image lane that follows it,
    // and the agent lane gets its own controller so a shot-budget
    // timeout there does not cancel the graphic fallback below.
    const ytController = new AbortController();
    const shotController = new AbortController();
    const agentController = new AbortController();
    wireJobAbort(ytController);
    wireJobAbort(shotController);
    wireJobAbort(agentController);
    let asset = null;
    const cachedShot = mediaCheckpoints[String(i)];
    if (cachedShot && cachedShot.query === shot.query && cachedShot.beat === shot.beat && cachedShot.assetPath) {
      const diskPath = path.join(projectDir, 'public', cachedShot.assetPath);
      try {
        // An old checkpoint may itself repeat one asset across shots; a
        // repeat here is a miss, not a reuse — re-fetch for a distinct clip.
        // This check-and-add is synchronous, so parallel shots cannot
        // both claim the same cached asset.
        if (!usedAssetPaths.has(cachedShot.assetPath) && fs.statSync(diskPath).size >= 1024) {
          asset = { assetType: cachedShot.assetType, assetPath: cachedShot.assetPath, source: cachedShot.source || 'checkpoint', videoId: cachedShot.videoId || '', isGraphic: Boolean(cachedShot.isGraphic) };
          if (asset.videoId) usedVideoIds.add(asset.videoId);
          usedAssetPaths.add(asset.assetPath);
          log.info(`[documentary] Shot ${i + 1}/${shots.length} reused from checkpoint (${asset.assetPath})`);
        }
      } catch { /* fall through to fetch */ }
    }

    if (!asset) {
      log.info(`[documentary] Shot ${i + 1}/${shots.length} (${shot.beat}, ${shot.kind}): "${shot.query}"`);
      onProgress(pct(), `[documentary] Fetching media for shot ${i + 1}/${shots.length}: ${shot.query.slice(0, 60)}`, true);

      // Phase A — direct YouTube clip, bounded by the YT-phase budget.
      // Direct search is the fastest path (no pool round-trip): a clip
      // lands in ~40s. Image shots skip this phase — their primary is
      // the image lane below, and a clip is only their cross-lane
      // second chance there.
      if (shot.kind === 'video') {
        const runYtPhase = async () => {
          const clipFile = path.join(projectDir, 'public', 'footage', `shot-${i + 1}.mp4`);
          const fetched = await videoClipFn(shot.query, shot.sourceUrl, clipFile, shot.dur / FPS, { signal: ytController.signal, pythonBin, width: renderWidth, height: renderHeight, excludeVideoIds: usedVideoIds });
          if (fetched) {
            return { assetType: 'video', assetPath: `footage/shot-${i + 1}.mp4`, source: fetched.source, videoId: fetched.videoId || '' };
          }
          return null;
        };
        const ytOutcome = await raceAgainstDeadline(runYtPhase(), ytDeadline);
        if (ytOutcome.error) throw ytOutcome.error;
        if (ytOutcome.timedOut) {
          ytController.abort();
          log.info(`[documentary] Shot ${i + 1}/${shots.length}: YouTube phase exceeded its ${Math.round((ytDeadline - shotStartedAt) / 1000)}s budget; moving to the image lane`);
        } else {
          asset = ytOutcome.value || null;
        }
      }

      // Phase B — image lane (fetches have no per-request timeout), then
      // a cross-lane fresh clip for image shots, bounded by the whole
      // shot budget. Firing here means the shot is genuinely stalled.
      if (!asset) {
        const runImagePhase = async () => {
          const imgStem = path.join(projectDir, 'public', 'images', `shot-${i + 1}`);
          const fetchedImg = await imageAssetFn(shot.query, shot.sourceUrl, imgStem, { signal: shotController.signal });
          if (fetchedImg) {
            return { assetType: 'image', assetPath: `images/${path.basename(fetchedImg.path)}`, source: fetchedImg.source };
          }
          if (shot.kind === 'image') {
            // Cross-lane second chance: an image shot whose stills lanes are
            // empty gets a fresh related clip before any repeat. A fresh clip
            // beats a repeated clip; the manifest counts it honestly as video.
            const clipFile = path.join(projectDir, 'public', 'footage', `shot-${i + 1}.mp4`);
            const fetched = await videoClipFn(shot.query, shot.sourceUrl, clipFile, shot.dur / FPS, { signal: shotController.signal, pythonBin, width: renderWidth, height: renderHeight, excludeVideoIds: usedVideoIds });
            if (fetched) {
              return { assetType: 'video', assetPath: `footage/shot-${i + 1}.mp4`, source: fetched.source, videoId: fetched.videoId || '' };
            }
          }
          return null;
        };
        const imgOutcome = await raceAgainstDeadline(runImagePhase(), shotDeadline);
        if (imgOutcome.error) throw imgOutcome.error;
        if (imgOutcome.timedOut) {
          shotController.abort();
          log.info(`[documentary] Shot ${i + 1}/${shots.length}: exceeded its ${Math.round(budgets.shotBudgetMs / 1000)}s shot budget; moving to the agent lane`);
        } else {
          asset = imgOutcome.value || null;
        }
      }

      // Phase C — agent lane, last hope before the graphic fallback,
      // bounded by the whole shot budget. Only reached when the direct
      // engines resolved nothing for this shot: the lane files a
      // documentary_media request and a pool worker finds real
      // clips/images for it, the same way a direct chat with Muse
      // does. A miss or a timeout here means the graphic below. The
      // deadline guard skips the lane entirely when the shot budget
      // is already spent — filing a request nobody waits for would
      // only leave an expired request in the spool.
      if (!asset && Date.now() < shotDeadline) {
        const runAgentPhase = async () => {
          const agentCandidates = await agentMediaFn({
            shot, title: job.title || 'Documentary', topicTag,
            jobId: job.jobId, workDir, signal: agentController.signal,
          });
          const tryAgentCandidates = async (wantVideoFirst) => {
            const ordered = [...agentCandidates].sort((a, b) => {
              const aScore = (a.type === 'video') === wantVideoFirst ? 0 : 1;
              const bScore = (b.type === 'video') === wantVideoFirst ? 0 : 1;
              return aScore - bScore;
            });
            for (const cand of ordered) {
              if (cand.type === 'video') {
                if (usedVideoIds.has(cand.videoId)) continue;
                const clipFile = path.join(projectDir, 'public', 'footage', `shot-${i + 1}.mp4`);
                const fetched = await videoClipFn(shot.query, `https://www.youtube.com/watch?v=${cand.videoId}`, clipFile, shot.dur / FPS, { signal: agentController.signal, pythonBin, width: renderWidth, height: renderHeight, excludeVideoIds: usedVideoIds });
                if (fetched) return { assetType: 'video', assetPath: `footage/shot-${i + 1}.mp4`, source: fetched.source, videoId: fetched.videoId || '' };
              } else {
                const imgStem = path.join(projectDir, 'public', 'images', `shot-${i + 1}`);
                const saved = await downloadImageUrl(cand.url, imgStem, { signal: agentController.signal });
                if (saved) return { assetType: 'image', assetPath: `images/${path.basename(saved)}`, source: cand.url };
              }
            }
            return null;
          };
          return tryAgentCandidates(shot.kind === 'video');
        };
        const agentOutcome = await raceAgainstDeadline(runAgentPhase(), shotDeadline);
        if (agentOutcome.error) throw agentOutcome.error;
        if (agentOutcome.timedOut) {
          agentController.abort();
          log.info(`[documentary] Shot ${i + 1}/${shots.length}: agent lane exceeded the remaining shot budget; falling back to a graphic title card`);
        } else {
          asset = agentOutcome.value || null;
        }
      }

      // Last resort: a graphic title card keeps the video complete.
      // NOTE: the graphic runs under the JOB signal, not the shot signal
      // — the watchdog has just aborted the shot signal to kill the
      // stalled lanes, and a graphic made under it would be cancelled
      // before it starts.
      if (!asset) {
        const graphicPath = path.join(projectDir, 'public', 'images', `shot-${i + 1}-graphic.png`);
        const made = await graphicFn(shot, graphicPath, { signal, width: renderWidth, height: renderHeight });
        if (made) {
          asset = { assetType: 'image', assetPath: `images/${path.basename(made)}`, source: 'graphic:watchdog', isGraphic: true };
          log.info(`[documentary] Shot ${i + 1}/${shots.length}: graphic fallback (all lanes empty or watchdog fired)`);
        }
      }

      if (asset) {
        if (asset.videoId) usedVideoIds.add(asset.videoId);
        usedAssetPaths.add(asset.assetPath);
        if (asset.assetType === 'video') log.info(`[documentary] Shot ${i + 1}/${shots.length}: video ready (${(shot.dur / FPS).toFixed(1)}s, id=${asset.videoId || 'n/a'})`);
        else if (asset.isGraphic) log.info(`[documentary] Shot ${i + 1}/${shots.length}: graphic ready`);
        else log.info(`[documentary] Shot ${i + 1}/${shots.length}: verified image ready`);
        mediaCheckpoints[String(i)] = { query: shot.query, beat: shot.beat, ...asset };
        writeMediaCheckpoint();
      }
    }

    mediaResults[i] = asset;
    if (asset) {
      mediaResolvedCount += 1;
      onProgress(pct(), `[documentary] Media ready for shot ${i + 1}/${shots.length}`, true);
    }
    return asset;
  };

  // Phase 1: resolve all shots concurrently (bounded).
  {
    let next = 0;
    const workers = Array.from({ length: Math.min(mediaConcurrency, shots.length) }, async () => {
      while (next < shots.length) {
        const idx = next;
        next += 1;
        await resolveShotMedia(idx);
      }
    });
    await Promise.all(workers);
  }

  // Phase 2: sequential least-used fallback for anything still unresolved.
  for (const a of mediaResults.filter(Boolean)) {
    if (!fallbackUsage.has(a.assetPath)) fallbackUsage.set(a.assetPath, 0);
  }
  for (let i = 0; i < shots.length; i += 1) {
    if (mediaResults[i]) continue;
    const shot = shots[i];
    const pool = mediaResults.filter(Boolean).map((a) => ({ assetPath: a.assetPath, assetType: a.assetType, source: a.source, videoId: a.videoId || '', isGraphic: Boolean(a.isGraphic) }));
    if (pool.length) {
      const reused = pickLeastUsedFallback(pool, fallbackUsage);
      if (reused) {
        const asset = { ...reused };
        fallbackUsage.set(reused.assetPath, (fallbackUsage.get(reused.assetPath) || 0) + 1);
        mediaResults[i] = asset;
        mediaCheckpoints[String(i)] = { query: shot.query, beat: shot.beat, ...asset };
        writeMediaCheckpoint();
        log.info(`[documentary] Shot ${i + 1}/${shots.length}: reused verified asset ${reused.assetPath} (least-used fallback)`);
      }
    }
    if (!mediaResults[i]) {
      const graphicPath = path.join(projectDir, 'public', 'images', `shot-${i + 1}-graphic.png`);
      const made = await graphicFn(shot, graphicPath, { signal, width: renderWidth, height: renderHeight });
      if (made) {
        mediaResults[i] = { assetType: 'image', assetPath: `images/${path.basename(made)}`, source: 'graphic:watchdog', isGraphic: true };
        mediaCheckpoints[String(i)] = { query: shot.query, beat: shot.beat, ...mediaResults[i] };
        writeMediaCheckpoint();
        log.info(`[documentary] Shot ${i + 1}/${shots.length}: graphic fallback (phase 2)`);
      }
    }
    if (!mediaResults[i]) {
      throw new Error(`Documentary shot ${i + 1} ("${shot.query}") could not retrieve any verified video clip, real image, or graphic fallback.`);
    }
  }

  for (let i = 0; i < shots.length; i += 1) {
    const asset = mediaResults[i];
    const shot = shots[i];
    if (asset.isGraphic) graphicShotCount += 1;
    else if (asset.assetType === 'video') videoShotCount += 1;
    else imageShotCount += 1;
    fallbackUsage.set(asset.assetPath, fallbackUsage.get(asset.assetPath) || 0);
    verifiedFallbacks.push(asset);
    mediaSources.push(asset.source);
    resolvedShots.push({ ...shot, ...asset });
  }

  return {
    resolvedShots,
    videoShotCount,
    imageShotCount,
    graphicShotCount,
    mediaSources,
    mediaCheckpoints,
    mediaResults,
  };
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
  // Template is picked per job via the documentaryTemplates helper
  // contract (another agent's module); until it exists, this resolves to
  // the default template dir. The project copy + node_modules below use
  // the picked template, never the hardcoded default.
  const template = resolveDocumentaryTemplate(job);
  const templateCheck = checkTemplateDir(template.dir);
  if (!templateCheck.available) {
    throw new Error(`One-Minute Documentary template "${template.id}" is missing required files under ${template.dir}: ${templateCheck.missing.join(', ')}`);
  }
  log.info(`[documentary] Job ${job.jobId}: using Remotion template "${template.id}" (${template.label})`);

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
    copyTemplateProject(template.dir, projectDir);
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

  const mediaStartMs = Date.now();
  const media = await resolveDocumentaryMedia({
    shots: normalizedShots,
    projectDir,
    workDir,
    job,
    topicTag: plan.topicTag || 'DOCUMENTARY',
    signal,
    pythonBin,
    renderWidth,
    renderHeight,
    onProgress,
    mediaCheckpoints,
    mediaCheckpointPath,
    inputDigest,
    overrides: options.mediaOverrides || {},
  });
  const resolvedShots = media.resolvedShots;
  const videoShotCount = media.videoShotCount;
  const imageShotCount = media.imageShotCount;
  const graphicShotCount = media.graphicShotCount;
  const mediaSources = media.mediaSources;
  log.info(`[documentary] Job ${job.jobId}: media stage finished in ${Math.round((Date.now() - mediaStartMs) / 1000)}s (${videoShotCount} video, ${imageShotCount} image, ${graphicShotCount} graphic)`);
  writeStageCheckpoint(checkpointPath, inputDigest, { mediaReady: { totalShots: resolvedShots.length, videoShotCount, imageShotCount, graphicShotCount } });

  log.info(`[documentary] Job ${job.jobId}: building Remotion project (${videoShotCount} video, ${imageShotCount} image, ${graphicShotCount} graphic shots)`);
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

  await ensureNodeModules(template.dir, projectDir, signal);

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
      graphicShotCount,
      templateId: template.id,
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
  MEDIA_AGENT_SCHEMA,
  MEDIA_BUDGETS,
  mediaBudgets,
  buildMediaAgentMessages,
  normalizeAgentMediaCandidates,
  fetchAgentMediaCandidates,
  mediaPoolLiveness,
  scanMediaPoolClaims,
  waitForMediaPoolClaim,
  spoolDirForMedia,
  resolveDocumentaryTemplate,
  checkTemplateDir,
  resolveDocumentaryMedia,
  makeGraphicFallback,
  fetchVideoClip,
  listYoutubeCandidates,
  downloadImageUrl,
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
