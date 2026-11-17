'use strict';

/**
 * Linux render path for the Vidzeo Muse worker — NO AI VIDEO FOOTAGE.
 *
 * Implements the MUSE_RENDER_COMMAND contract from adapters/video.js:
 *   stdin JSON: { job, scriptText, scriptPath, workDir, outputPath }
 *   stdout JSON (only JSON on stdout): { videoPath, thumbnailPath, meta }
 *   on failure: stdout JSON { ok: false, error } + non-zero exit.
 * All progress logging goes to stderr so stdout stays parseable.
 *
 * Pipeline (Tier A, ffmpeg only):
 *   1. Read job + narration, split into 8-15s beats.
 *   2. Real narration via /opt/hatch/bin/tts synthesize-script (Shortwave).
 *      The CMS job voice (e.g. en-US-EricNeural) is an Azure-style voice id
 *      with no Shortwave equivalent, so the default Shortwave voice is used
 *      and the mismatch is logged. Speed is auto-adjusted so the narration
 *      fills the target duration (default TTS runs ~220 wpm).
 *   3. Media per beat, in Vidnets priority order (adapters/vidnets.js):
 *        (a) YouTube clips — public videos only, yt-dlp android client
 *            (the default web client is bot-blocked from this VM),
 *            -f "bv*[height<=1080]+ba", short sections only.
 *        (b) Web images/video — Wikimedia Commons (CC) then Openverse (CC).
 *        (c) Pexels / Pixabay stock video — ONLY if keys are in env.
 *      Every asset is recorded in media/media-manifest.json.
 *      If a beat has no usable media after every source, the render FAILS
 *      with a clear error. It never substitutes a solid colour / sine tone.
 *   4. Motion graphics with ffmpeg only: zoompan Ken Burns on stills,
 *      xfade transitions, drawtext captions + title, 1920x1080 30fps,
 *      libx264 yuv420p, AAC 192k, narration mixed in.
 *   5. Thumbnail: 1920x1080 title card from the best still (Pillow),
 *      falling back to ffmpeg drawtext, then to a frame from the master.
 *
 * No new npm dependencies. Requires ffmpeg/ffprobe, yt-dlp, tts, python3+PIL
 * (PIL only for the thumbnail; ffmpeg fallback covers its absence).
 *
 * Env overrides:
 *   TTS_BIN (default /opt/hatch/bin/tts), YTDLP_BIN (default ~/.local/bin/yt-dlp)
 *   RENDER_LINUX_TTS_VOICE (default avocado_v2:MAI_03)
 *   RENDER_LINUX_MAX_YOUTUBE_CLIPS (default 2 per job)
 *   RENDER_LINUX_SKIP_YOUTUBE=1, RENDER_LINUX_SKIP_TTS=1 (debug/offline)
 *   PEXELS_API_KEY, PIXABAY_API_KEY (optional stock rescue)
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawn } = require('child_process');

const FONT_BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf';
const FONT_REG = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf';
const TTS_BIN = process.env.TTS_BIN || '/opt/hatch/bin/tts';
const YTDLP_BIN = process.env.YTDLP_BIN || path.join(os.homedir(), '.local/bin', 'yt-dlp');
const TTS_VOICE = process.env.RENDER_LINUX_TTS_VOICE || 'avocado_v2:MAI_03';
const MAX_YOUTUBE_CLIPS = Number(process.env.RENDER_LINUX_MAX_YOUTUBE_CLIPS || 2);
const XFADE = 0.5; // seconds of crossfade between beats

const log = (...a) => console.error('[render-linux]', ...a);
const fail = (msg) => {
  process.stdout.write(JSON.stringify({ ok: false, error: String(msg) }) + '\n');
  process.exit(1);
};

// ---------------------------------------------------------------- utilities
function run(cmd, args, { timeoutMs = 120000, input = null } = {}) {
  return new Promise((resolve) => {
    let stdout = '';
    let stderr = '';
    let child;
    try {
      child = spawn(cmd, args, { stdio: ['pipe', 'pipe', 'pipe'] });
    } catch (e) {
      resolve({ code: -1, stdout, stderr: String(e.message || e) });
      return;
    }
    const timer = setTimeout(() => { try { child.kill('SIGKILL'); } catch {} }, timeoutMs);
    child.stdout.on('data', (c) => { stdout += c.toString('utf8'); });
    child.stderr.on('data', (c) => { stderr += c.toString('utf8'); });
    child.on('error', (e) => { clearTimeout(timer); resolve({ code: -1, stdout, stderr: String(e.message || e) }); });
    child.on('close', (code) => { clearTimeout(timer); resolve({ code, stdout, stderr }); });
    if (input != null) child.stdin.end(input);
    else child.stdin.end();
  });
}

async function ffprobeDuration(file) {
  const r = await run('ffprobe', ['-v', 'error', '-show_entries', 'format=duration',
    '-of', 'csv=p=0', file], { timeoutMs: 60000 });
  const d = parseFloat(String(r.stdout || '').trim());
  return Number.isFinite(d) ? d : 0;
}

async function ffprobeInfo(file) {
  const r = await run('ffprobe', ['-v', 'error', '-show_entries',
    'format=duration,size:stream=codec_type,codec_name,width,height',
    '-of', 'json', file], { timeoutMs: 60000 });
  try { return JSON.parse(r.stdout || '{}'); } catch { return {}; }
}

function fmtTime(s) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  return [h, m, sec].map((n) => String(n).padStart(2, '0')).join(':');
}

function stripUtm(u) {
  try { const url = new URL(u); url.search = ''; return url.toString(); } catch { return u; }
}

async function downloadFile(url, dest, { timeoutMs = 60000, maxBytes = 250 * 1024 * 1024 } = {}) {
  const res = await fetch(url, {
    signal: AbortSignal.timeout(timeoutMs),
    headers: { 'User-Agent': 'VidzeoLinuxRenderer/1.0 (contact: muse-worker; Wikimedia/Openverse/CC media)' },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  const buf = Buffer.from(await res.arrayBuffer());
  if (buf.length < 1024) throw new Error(`Download too small (${buf.length} bytes): ${url}`);
  if (buf.length > maxBytes) throw new Error(`Download too large (${buf.length} bytes): ${url}`);
  fs.writeFileSync(dest, buf);
  return buf.length;
}

function wrapText(text, width) {
  const words = String(text || '').split(/\s+/).filter(Boolean);
  const lines = [];
  let line = '';
  for (const w of words) {
    const next = line ? line + ' ' + w : w;
    if (next.length > width && line) { lines.push(line); line = w; }
    else line = next;
  }
  if (line) lines.push(line);
  return lines;
}

// ---------------------------------------------------------------- input
async function readStdin() {
  if (process.stdin.isTTY) return '';
  return new Promise((resolve) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (c) => { data += c; });
    process.stdin.on('end', () => resolve(data));
    setTimeout(() => resolve(data), 5000);
  });
}

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const m = argv[i].match(/^--([^=]+)(?:=(.*))?$/);
    if (m) out[m[1]] = m[2] !== undefined ? m[2] : argv[++i];
  }
  return out;
}

function extractNarration(raw) {
  let text = String(raw || '').trim();
  // Draft files carry a "--- NARRATION ---" section; use only that.
  const m = text.match(/---\s*NARRATION[\s\S]*?---\s*\n([\s\S]*?)(?:\n---\s*VISUAL BEATS|$)/);
  if (m) text = m[1].trim();
  // Drop any remaining header lines / markers.
  text = text.split('\n').filter((l) => !/^---/.test(l.trim()) && !/^DRAFT ONLY/i.test(l)).join('\n').trim();
  return text.replace(/\s+/g, ' ').trim();
}

// ---------------------------------------------------------------- beats
const STOPWORDS = new Set(('a an the and or but of to in on for with by at from as is are was were be been it its this that these those he she they we you i his her their our your not no when where which who whom what how why if then than so such into over under after before between through during each few more most other some any all can could will would should may might must do does did done have has had train trains railway railways track tracks'.split(' ')));

function keywords(text, topN = 4) {
  const freq = new Map();
  const first = new Map();
  String(text).toLowerCase().replace(/[^a-z0-9\s-]/g, ' ').split(/\s+/)
    .forEach((w, i) => {
      if (w.length < 4 || STOPWORDS.has(w)) return;
      freq.set(w, (freq.get(w) || 0) + 1);
      if (!first.has(w)) first.set(w, i);
    });
  return [...freq.entries()]
    .sort((a, b) => b[1] - a[1] || (first.get(a[0]) - first.get(b[0])))
    .slice(0, topN).map(([w]) => w);
}

function splitBeats(scriptText, job) {
  const sentences = String(scriptText).match(/[^.!?]+[.!?]+["')\]]*\s*/g)
    || [scriptText];
  const beats = [];
  let cur = [];
  let curWords = 0;
  const flush = () => {
    if (!cur.length) return;
    const text = cur.join(' ').replace(/\s+/g, ' ').trim();
    beats.push({ index: beats.length, text, words: curWords, caption: text });
    cur = []; curWords = 0;
  };
  for (const s of sentences) {
    const w = s.trim().split(/\s+/).filter(Boolean).length;
    // 8-15s at ~150 wpm = ~20-37 words per beat.
    if (curWords >= 18 && curWords + w > 34) flush();
    cur.push(s.trim());
    curWords += w;
    if (curWords >= 30) flush();
  }
  flush();
  const niche = String(job.channel?.niche || job.channel?.name || '').toLowerCase();
  const nicheWord = /\btrain/.test(niche) ? 'train' : (niche.split(/[^a-z]+/).find((w) => w.length > 3) || '');
  for (const b of beats) {
    const kw = keywords(b.text, 4);
    const q = [...kw, nicheWord].filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).join(' ');
    b.queries = [q, String(job.title || ''), nicheWord ? `${nicheWord} documentary` : String(job.title || '')]
      .filter(Boolean).filter((v, i, a) => a.indexOf(v) === i);
    b.query = b.queries[0];
  }
  return beats;
}

// ---------------------------------------------------------------- narration (TTS)
async function synthesizeNarration(scriptText, mediaDir, targetSeconds) {
  const scriptFile = path.join(mediaDir, 'narration-script.txt');
  const outFile = path.join(mediaDir, 'narration.mp3');
  const sentences = String(scriptText).match(/[^.!?]+[.!?]+["')\]]*\s*/g) || [scriptText];
  const scriptContent = sentences.map((s) => `Narrator: ${s.trim()}`).join('\n') + '\n';
  fs.writeFileSync(scriptFile, scriptContent, 'utf8');
  const voice = TTS_VOICE;
  // Reuse a narration already synthesized for this exact script (retries and
  // re-renders must not pay for TTS twice). The sha sidecar proves it matches.
  const shaFile = path.join(mediaDir, 'narration-script.sha');
  const sha = require('crypto').createHash('sha256').update(scriptContent + '|' + voice).digest('hex');
  if (fs.existsSync(outFile) && fs.existsSync(shaFile) && fs.readFileSync(shaFile, 'utf8').trim() === sha) {
    const cachedDur = await ffprobeDuration(outFile);
    if (cachedDur >= targetSeconds * 0.9 && cachedDur <= targetSeconds * 1.5) {
      log(`TTS: reusing cached narration (${cachedDur.toFixed(1)}s) for unchanged script`);
      return { file: outFile, duration: cachedDur, voice, speed: 'cached', skipped: false };
    }
  }
  log(`TTS voice ${voice} (job voice is Azure-style and has no Shortwave mapping; using default)`);
  if (process.env.RENDER_LINUX_SKIP_TTS === '1') {
    log('RENDER_LINUX_SKIP_TTS=1 — skipping narration synthesis');
    return { file: outFile, duration: 0, voice, speed: 100, skipped: true };
  }
  let speed = 100;
  let duration = 0;
  for (let attempt = 0; attempt < 2; attempt++) {
    const r = await run(TTS_BIN, ['synthesize-script', '--script', scriptFile,
      '--speaker', `Narrator=${voice}`, '--output', outFile,
      '--speed', String(speed), '--timeout-secs', '300'], { timeoutMs: 600000 });
    if (r.code !== 0 || !fs.existsSync(outFile)) {
      throw new Error(`TTS synthesize-script failed (speed ${speed}): ${(r.stderr || r.stdout || '').slice(-500)}`);
    }
    duration = await ffprobeDuration(outFile);
    fs.writeFileSync(path.join(mediaDir, 'narration-script.sha'), sha + '\n', 'utf8');
    log(`TTS pass ${attempt + 1}: speed ${speed}, narration ${duration.toFixed(1)}s (target ${targetSeconds}s)`);
    if (duration <= 0) throw new Error('TTS produced an unreadable narration file');
    if (duration >= targetSeconds * 0.99 || attempt === 1) break;
    // Default TTS runs ~220 wpm; slow it so narration fills the target runtime.
    speed = Math.max(60, Math.min(100, Math.round(100 * duration / targetSeconds)));
    if (speed >= 99) break;
    log(`Narration short of target; re-synthesizing at speed ${speed}`);
  }
  return { file: outFile, duration, voice, speed, skipped: false };
}

// ---------------------------------------------------------------- media sources
async function searchCommons(query, mimePrefix, limit = 5, requireWords = [], strictTitle = false) {
  const params = new URLSearchParams({
    action: 'query', format: 'json', generator: 'search',
    gsrsearch: `${query} filetype:${mimePrefix === 'video/' ? 'video' : 'bitmap'}`,
    gsrnamespace: '6', gsrlimit: String(limit),
    prop: 'imageinfo', iiprop: 'url|size|mime|extmetadata', iiurlwidth: '1920',
  });
  const res = await fetch(`https://commons.wikimedia.org/w/api.php?${params}`, {
    signal: AbortSignal.timeout(20000),
    headers: { 'User-Agent': 'VidzeoLinuxRenderer/1.0' },
  });
  if (!res.ok) return [];
  const data = await res.json();
  const pages = Object.values(data?.query?.pages || {});
  let list = pages.map((p) => {
    const ii = (p.imageinfo || [])[0] || {};
    const licence = ii.extmetadata?.LicenseShortName?.value || ii.extmetadata?.License?.value || 'unknown (see Commons page)';
    return {
      title: p.title, mime: ii.mime || '', width: ii.width || 0, height: ii.height || 0,
      size: ii.size || 0,
      url: mimePrefix === 'image/' && ii.thumburl ? stripUtm(ii.thumburl) : stripUtm(ii.url || ''),
      pageUrl: ii.descriptionurl || `https://commons.wikimedia.org/wiki/${encodeURIComponent(String(p.title || '').replace(/ /g, '_'))}`,
      licence: String(licence).replace(/<[^>]+>/g, ''),
    };
  }).filter((x) => x.url && (!mimePrefix || (x.mime || '').startsWith(mimePrefix)));
  // Commons full-text search is loose ("train" matches a Pokemon video about
  // a train-the-trainer programme). Require a beat keyword in the file title;
  // fall back to the unfiltered ranking only when nothing matches.
  const words = (requireWords || []).filter((w) => w && w.length >= 4);
  if (words.length) {
    const matched = list.filter((x) => words.some((w) => String(x.title || '').toLowerCase().includes(w)));
    if (matched.length) list = matched;
    // For video the unfiltered ranking is junk (a "train the trainer"
    // programme, a bird-singing contest). Strict mode returns nothing
    // rather than a wrong clip, so the caller falls through to images.
    else if (strictTitle) return [];
  }
  if (mimePrefix === 'video/') list.sort((a, b) => (a.size || 0) - (b.size || 0));
  return list;
}

async function searchOpenverse(query, limit = 5) {
  const params = new URLSearchParams({ q: query, license_type: 'commercial', page_size: String(limit) });
  const res = await fetch(`https://api.openverse.org/v1/images/?${params}`, {
    signal: AbortSignal.timeout(20000),
    headers: { 'User-Agent': 'VidzeoLinuxRenderer/1.0' },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return (data.results || []).map((r) => ({
    title: r.title || '', mime: 'image/jpeg', width: r.width || 0, height: r.height || 0,
    url: r.url, pageUrl: r.foreign_landing_url || r.detail_url || '',
    licence: `CC ${r.license || '?'} ${r.license_version || ''}`.trim(),
  })).filter((x) => x.url);
}

async function searchYouTube(query, limit = 5) {
  const r = await run(YTDLP_BIN, [
    '--extractor-args', 'youtube:player_client=android',
    `ytsearch${limit}:${query}`, '--get-id', '--get-title', '--get-duration',
    '--no-warnings', '--no-playlist',
  ], { timeoutMs: 90000 });
  if (r.code !== 0) { log(`yt-dlp search failed for "${query}": ${(r.stderr || '').slice(-200)}`); return []; }
  const lines = r.stdout.split('\n').map((s) => s.trim()).filter(Boolean);
  const out = [];
  for (let i = 0; i + 2 < lines.length; i += 3) {
    const [title, id, durRaw] = lines.slice(i, i + 3);
    if (!/^[A-Za-z0-9_-]{11}$/.test(id || '')) continue;
    let dur = 0;
    if (/^\d+$/.test(durRaw)) dur = Number(durRaw);
    else if (/:/.test(durRaw)) dur = durRaw.split(':').reduce((a, v) => a * 60 + Number(v), 0);
    if (dur >= 45 && dur <= 3600) out.push({ id, title, duration: dur });
  }
  return out;
}

async function downloadYouTubeSection(video, startSec, durSec, dest) {
  const end = startSec + durSec;
  const r = await run(YTDLP_BIN, [
    '--extractor-args', 'youtube:player_client=android',
    '-f', 'bv*[height<=1080]+ba/b[height<=1080]',
    '--download-sections', `*${fmtTime(startSec)}-${fmtTime(end)}`,
    '--merge-output-format', 'mp4', '--no-warnings', '--no-playlist',
    '-o', dest, `https://www.youtube.com/watch?v=${video.id}`,
  ], { timeoutMs: 300000 });
  if (r.code !== 0 || !fs.existsSync(dest)) {
    throw new Error(`yt-dlp download failed for ${video.id}: ${(r.stderr || '').slice(-300)}`);
  }
  const d = await ffprobeDuration(dest);
  if (d < durSec * 0.7) throw new Error(`YouTube section too short (${d.toFixed(1)}s < ${durSec}s) for ${video.id}`);
  return d;
}

async function searchStockVideo(query) {
  // Only called when keys exist. Pexels first, then Pixabay.
  const out = [];
  if (process.env.PEXELS_API_KEY) {
    try {
      const res = await fetch(`https://api.pexels.com/videos/search?${new URLSearchParams({ query, per_page: '3', orientation: 'landscape', size: 'large' })}`, {
        signal: AbortSignal.timeout(20000),
        headers: { Authorization: process.env.PEXELS_API_KEY },
      });
      const data = await res.json();
      for (const v of data.videos || []) {
        const f = (v.video_files || []).filter((x) => x.width >= 1280).sort((a, b) => b.width - a.width)[0]
          || (v.video_files || [])[0];
        if (f?.link) out.push({ url: f.link, licence: 'Pexels License', pageUrl: v.url || '', title: `Pexels video ${v.id}`, width: f.width || 0, height: f.height || 0 });
      }
    } catch (e) { log(`Pexels search failed: ${e.message}`); }
  }
  if (process.env.PIXABAY_API_KEY) {
    try {
      const res = await fetch(`https://pixabay.com/api/videos/?${new URLSearchParams({ key: process.env.PIXABAY_API_KEY, q: query, per_page: '3' })}`, { signal: AbortSignal.timeout(20000) });
      const data = await res.json();
      for (const v of data.hits || []) {
        const file = v.videos?.large || v.videos?.medium || v.videos?.small;
        if (file?.url) out.push({ url: file.url, licence: 'Pixabay License', pageUrl: v.pageURL || '', title: `Pixabay video ${v.id}`, width: file.width || 0, height: file.height || 0 });
      }
    } catch (e) { log(`Pixabay search failed: ${e.message}`); }
  }
  return out;
}

// Resolve one beat to a real media file. Throws if nothing usable is found.
async function resolveBeatMedia(beat, ctx) {
  const { mediaDir, usedUrls, job } = ctx;
  const base = `scene-${beat.index}`;
  const queries = beat.queries;

  // (a) YouTube clips — at most MAX_YOUTUBE_CLIPS per job, public videos only.
  if (!process.env.RENDER_LINUX_SKIP_YOUTUBE && ctx.youtubeClips < MAX_YOUTUBE_CLIPS) {
    if (!ctx.youtubeCandidates) {
      ctx.youtubeCandidates = await searchYouTube(`${job.title || ''} ${job.channel?.niche || ''}`.trim(), 5)
        .catch(() => []);
      log(`YouTube search: ${ctx.youtubeCandidates.length} candidates for "${job.title}"`);
    }
    for (const video of ctx.youtubeCandidates) {
      if (ctx.youtubeClips >= MAX_YOUTUBE_CLIPS) break;
      if (ctx.youtubeBad.has(video.id)) continue;
      const clipIndex = ctx.youtubeClips;
      const start = Math.min(15 + clipIndex * 75, Math.max(0, video.duration - 20));
      const dest = path.join(mediaDir, `${base}-youtube-${video.id}.mp4`);
      try {
        const dur = await downloadYouTubeSection(video, start, Math.ceil(beat.duration) + 2, dest);
        ctx.youtubeClips++;
        return { kind: 'youtube-clip', localPath: dest, url: `https://www.youtube.com/watch?v=${video.id}`,
          source: 'youtube', licence: 'Public YouTube video (Standard YouTube Licence; Vidnets all_public mode)', duration: dur, title: video.title };
      } catch (e) {
        ctx.youtubeBad.add(video.id);
        log(`YouTube clip failed (${video.id}): ${e.message}`);
        try { fs.unlinkSync(dest); } catch {}
      }
    }
  }

  // (b) Web video (Wikimedia Commons, CC) — moving footage with a clean licence.
  const beatWords = keywords(beat.text, 4);
  for (const q of queries) {
    for (const cand of await searchCommons(q, 'video/', 5, beatWords, true).catch(() => [])) {
      if (usedUrls.has(cand.url)) continue;
      if (cand.size && cand.size > 60 * 1024 * 1024) { log(`Commons video skipped (too large, ${(cand.size / 1048576).toFixed(0)} MB): ${cand.title}`); continue; }
      const ext = cand.mime.includes('webm') ? 'webm' : cand.mime.includes('mp4') ? 'mp4' : 'ogv';
      const dest = path.join(mediaDir, `${base}-commons-video.${ext}`);
      try {
        await downloadFile(cand.url, dest, { timeoutMs: 90000, maxBytes: 80 * 1024 * 1024 });
        const info = await ffprobeInfo(dest);
        const dur = Number(info.format?.duration) || 0;
        if (dur < 3) throw new Error('Commons video too short');
        usedUrls.add(cand.url);
        return { kind: 'web-video', localPath: dest, url: cand.pageUrl, source: 'wikimedia-commons', licence: cand.licence, duration: dur, title: cand.title };
      } catch (e) { log(`Commons video failed (${cand.title}): ${e.message}`); try { fs.unlinkSync(dest); } catch {} }
    }
  }

  // (b) Web images — Wikimedia Commons first, then Openverse.
  // Prefer candidates whose title contains a beat keyword; keep the rest as fallback.
  for (const q of queries) {
    const cands = [
      ...(await searchCommons(q, 'image/', 5).catch(() => [])),
      ...(await searchOpenverse(q, 5).catch(() => [])),
    ];
    const titleMatch = (c) => beatWords.some((w) => String(c.title || '').toLowerCase().includes(w));
    cands.sort((a, b) => (titleMatch(b) ? 1 : 0) - (titleMatch(a) ? 1 : 0));
    for (const cand of cands) {
      if (usedUrls.has(cand.url)) continue;
      if (cand.width && cand.width < 800) continue;
      const ext = /\.png(\?|$)/i.test(cand.url) ? 'png' : 'jpg';
      const dest = path.join(mediaDir, `${base}-image.${ext}`);
      try {
        await downloadFile(cand.url, dest, { timeoutMs: 60000 });
        const info = await ffprobeInfo(dest);
        const stream = (info.streams || []).find((s) => s.codec_type === 'video');
        if (!stream || !stream.width) throw new Error('downloaded image does not decode');
        usedUrls.add(cand.url);
        return { kind: 'image', localPath: dest, url: cand.pageUrl || cand.url, source: cand.url.includes('wikimedia') || cand.title.startsWith('File:') ? 'wikimedia-commons' : 'openverse', licence: cand.licence, duration: beat.duration, title: cand.title };
      } catch (e) { log(`Image failed (${cand.url.slice(0, 80)}): ${e.message}`); try { fs.unlinkSync(dest); } catch {} }
    }
  }

  // (c) Stock video rescue — only if keys are present in env.
  if (process.env.PEXELS_API_KEY || process.env.PIXABAY_API_KEY) {
    for (const q of queries) {
      for (const cand of await searchStockVideo(q)) {
        if (usedUrls.has(cand.url)) continue;
        const dest = path.join(mediaDir, `${base}-stock.mp4`);
        try {
          await downloadFile(cand.url, dest, { timeoutMs: 90000 });
          const dur = await ffprobeDuration(dest);
          if (dur < 3) throw new Error('stock video too short');
          usedUrls.add(cand.url);
          return { kind: 'stock-video', localPath: dest, url: cand.pageUrl, source: 'stock', licence: cand.licence, duration: dur, title: cand.title };
        } catch (e) { log(`Stock video failed: ${e.message}`); try { fs.unlinkSync(dest); } catch {} }
      }
    }
  } else {
    log('Pexels/Pixabay keys absent — stock rescue skipped (not fatal)');
  }

  throw new Error(`No usable media for scene ${beat.index} ("${beat.query}"): YouTube, Wikimedia Commons, Openverse and stock all failed — refusing to substitute a solid-colour placeholder`);
}

// ---------------------------------------------------------------- segments
function ffmpegEscapeTextFile(text) {
  // drawtext textfile= reads raw lines; no escaping needed.
  return String(text || '');
}

async function buildSegment(beat, media, renderLen, segPath, mediaDir, job) {
  const frames = Math.max(30, Math.round(renderLen * 30));
  const captionFile = path.join(mediaDir, `caption-${beat.index}.txt`);
  const captionLines = wrapText(beat.caption, 52).slice(0, 4);
  fs.writeFileSync(captionFile, captionLines.join('\n'), 'utf8');
  const captionY = 1080 - (captionLines.length * 48) - 36;
  const captionFilter = `drawtext=fontfile=${FONT_BOLD}:textfile=${captionFile}:fontsize=34:fontcolor=white:borderw=2:bordercolor=black:box=1:boxcolor=black@0.55:boxborderw=12:x=(w-text_w)/2:y=${captionY}:line_spacing=6`;
  let titleFilter = '';
  if (beat.index === 0) {
    const titleFile = path.join(mediaDir, 'title.txt');
    fs.writeFileSync(titleFile, wrapText(String(job.title || '').toUpperCase(), 22).join('\n'), 'utf8');
    titleFilter = `,drawtext=fontfile=${FONT_BOLD}:textfile=${titleFile}:fontsize=68:fontcolor=white:borderw=3:bordercolor=black:box=1:boxcolor=black@0.55:boxborderw=16:x=(w-text_w)/2:y=110:line_spacing=8`;
  }

  let filter;
  let inputArgs;
  if (media.kind === 'image') {
    const zoomIn = beat.index % 2 === 0;
    const z = zoomIn ? `1+0.12*on/${frames}` : `1.12-0.12*on/${frames}`;
    filter = `[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,` +
      `zoompan=z='${z}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080:fps=30,` +
      `setpts=PTS-STARTPTS,${captionFilter}${titleFilter},format=yuv420p[v]`;
    inputArgs = ['-loop', '1', '-framerate', '30', '-t', String(renderLen), '-i', media.localPath];
  } else {
    // Real moving clip: trim to the beat, scale/crop. tpad's stop_duration is
    // the amount ADDED, not a target length — pad only the actual shortfall.
    const shortfall = Math.max(0, renderLen - (Number(media.duration) || 0));
    const pad = shortfall > 0.05 ? `,tpad=stop_duration=${shortfall.toFixed(2)}:stop_mode=clone` : '';
    filter = `[0:v]trim=0:${renderLen.toFixed(2)},setpts=PTS-STARTPTS,fps=30,` +
      `scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080${pad},` +
      `${captionFilter}${titleFilter},format=yuv420p[v]`;
    inputArgs = ['-i', media.localPath];
  }
  const r = await run('ffmpeg', ['-y', '-v', 'error', ...inputArgs,
    '-filter_complex', filter, '-map', '[v]', '-t', String(renderLen),
    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
    '-r', '30', '-an', segPath], { timeoutMs: 600000 });
  if (r.code !== 0 || !fs.existsSync(segPath)) {
    throw new Error(`Segment ${beat.index} encode failed: ${(r.stderr || '').slice(-400)}`);
  }
  const d = await ffprobeDuration(segPath);
  if (d < renderLen - 0.6) throw new Error(`Segment ${beat.index} came out ${d.toFixed(1)}s, expected ~${renderLen.toFixed(1)}s`);
  return segPath;
}

async function assembleVideo(segmentPaths, renderLens, narrationFile, totalDuration, outputPath) {
  // xfade chain: segment i has rendered length L_i; total = sum(L_i) - 0.5*(n-1).
  let filter = '';
  let prev = '[0:v]';
  let offsetBase = 0;
  for (let i = 1; i < segmentPaths.length; i++) {
    const offset = (offsetBase + renderLens[i - 1] - XFADE).toFixed(3);
    filter += `${prev}[${i}:v]xfade=transition=fade:duration=${XFADE}:offset=${offset}[x${i}];`;
    prev = `[x${i}]`;
    offsetBase += renderLens[i - 1] - XFADE;
  }
  filter += `${prev}format=yuv420p,fps=30[vout]`;
  const audioIndex = segmentPaths.length;
  const args = ['-y', '-v', 'error'];
  for (const p of segmentPaths) args.push('-i', p);
  args.push('-i', narrationFile);
  args.push('-filter_complex', filter,
    '-map', '[vout]', '-map', `${audioIndex}:a`,
    '-af', 'apad', '-t', String(totalDuration),
    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
    '-r', '30', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', outputPath);
  const r = await run('ffmpeg', args, { timeoutMs: 30 * 60 * 1000 });
  if (r.code !== 0 || !fs.existsSync(outputPath)) {
    throw new Error(`Final assembly failed: ${(r.stderr || '').slice(-600)}`);
  }
  return outputPath;
}

// ---------------------------------------------------------------- thumbnail
async function makeThumbnail(job, bestStill, videoPath, thumbPath, mediaDir) {
  const title = String(job.title || 'Documentary').toUpperCase();
  const channel = String(job.channel?.name || '');
  // Primary: Pillow title card from the best still.
  const pyFile = path.join(mediaDir, 'make_thumbnail.py');
  const src = bestStill || '';
  fs.writeFileSync(pyFile, [
    'import sys',
    'from PIL import Image, ImageDraw, ImageFont, ImageFilter',
    'src, out, title, channel = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]',
    'img = Image.open(src).convert("RGB")',
    'w, h = img.size',
    'scale = max(1920 / w, 1080 / h)',
    'img = img.resize((int(w * scale) + 1, int(h * scale) + 1), Image.LANCZOS)',
    'x = (img.width - 1920) // 2; y = (img.height - 1080) // 2',
    'img = img.crop((x, y, x + 1920, y + 1080))',
    'img = img.filter(ImageFilter.GaussianBlur(1))',
    'ov = Image.new("RGBA", img.size, (0, 0, 0, 0))',
    'd = ImageDraw.Draw(ov)',
    'd.rectangle([0, 620, 1920, 1080], fill=(0, 0, 0, 140))',
    'img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")',
    'bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"',
    'reg = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"',
    'def wrap(t, width):',
    '    words, lines, line = t.split(), [], ""',
    '    for wd in words:',
    '        nxt = (line + " " + wd).strip()',
    '        if len(nxt) > width and line: lines.append(line); line = wd',
    '        else: line = nxt',
    '    if line: lines.append(line)',
    '    return lines',
    'lines = wrap(title, 20)',
    'font = ImageFont.truetype(bold, 104)',
    'd = ImageDraw.Draw(img)',
    'ty = 660',
    'for ln in lines:',
    '    d.text((960, ty), ln, font=font, fill="white", stroke_width=4, stroke_fill="black", anchor="ma")',
    '    ty += 118',
    'if channel:',
    '    f2 = ImageFont.truetype(bold, 44)',
    '    d.text((960, 990), channel.upper(), font=f2, fill=(255, 220, 90), stroke_width=2, stroke_fill="black", anchor="ms")',
    'img.save(out, "JPEG", quality=90)',
    'print("ok")',
  ].join('\n'), 'utf8');
  if (src) {
    const r = await run('python3', [pyFile, src, thumbPath, title, channel], { timeoutMs: 120000 });
    if (r.code === 0 && fs.existsSync(thumbPath) && fs.statSync(thumbPath).size >= 1024) {
      log('Thumbnail: Pillow title card from best still');
      return thumbPath;
    }
    log(`Pillow thumbnail failed, falling back: ${(r.stderr || '').slice(-200)}`);
  }
  // Fallback 1: ffmpeg drawtext title card on the best still.
  if (src) {
    const titleFile = path.join(mediaDir, 'thumb-title.txt');
    fs.writeFileSync(titleFile, wrapText(title, 20).join('\n'), 'utf8');
    const r = await run('ffmpeg', ['-y', '-v', 'error', '-i', src, '-vf',
      `scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,drawbox=x=0:y=620:w=1920:h=460:color=black@0.55:t=fill,drawtext=fontfile=${FONT_BOLD}:textfile=${titleFile}:fontsize=96:fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=680:line_spacing=10`,
      '-frames:v', '1', '-q:v', '2', thumbPath], { timeoutMs: 120000 });
    if (r.code === 0 && fs.existsSync(thumbPath) && fs.statSync(thumbPath).size >= 1024) {
      log('Thumbnail: ffmpeg title card from best still');
      return thumbPath;
    }
  }
  // Fallback 2: a real frame from the finished master.
  const { extractFrame } = require('./lib/ffmpeg');
  await extractFrame(videoPath, thumbPath, {});
  log('Thumbnail: frame extracted from master (no still available)');
  return thumbPath;
}

// ---------------------------------------------------------------- main
async function main() {
  const argv = parseArgs(process.argv.slice(2));
  const stdinRaw = await readStdin();
  let input = {};
  if (stdinRaw.trim()) {
    try { input = JSON.parse(stdinRaw); } catch { fail('stdin was not valid JSON for the render contract'); }
  }
  const workDir = path.resolve(input.workDir || argv.workDir || argv.workdir || '');
  if (!workDir) fail('No workDir: pass the render-contract JSON on stdin or --workDir <dir>');
  fs.mkdirSync(workDir, { recursive: true });
  const mediaDir = path.join(workDir, 'media');
  fs.mkdirSync(mediaDir, { recursive: true });

  // Job: stdin > workDir/job.json (unwrapping {job:{...}}) > --job file.
  let job = input.job || null;
  if (!job) {
    const jobFile = argv.job || path.join(workDir, 'job.json');
    if (fs.existsSync(jobFile)) {
      const parsed = JSON.parse(fs.readFileSync(jobFile, 'utf8'));
      job = parsed.job || parsed;
    }
  }
  if (!job || typeof job !== 'object') fail('No job payload: stdin job, workDir/job.json, or --job <file> is required');
  const scriptPath = path.resolve(input.scriptPath || argv.script || path.join(workDir, 'script.txt'));
  let scriptText = input.scriptText || '';
  if (!scriptText && fs.existsSync(scriptPath)) scriptText = fs.readFileSync(scriptPath, 'utf8');
  scriptText = extractNarration(scriptText);
  if (scriptText.length < 40) fail(`Narration too short (${scriptText.length} chars) at ${scriptPath}`);
  // Persist the cleaned narration so the work folder is the source of truth.
  fs.writeFileSync(path.join(workDir, 'script.txt'), scriptText + '\n', 'utf8');

  const outputPath = path.resolve(input.outputPath || argv.output || path.join(workDir, 'video.mp4'));
  const thumbPath = path.join(workDir, 'thumbnail.jpg');
  const targetMinutes = Number(job.targetDurationMinutes) || 1;
  const targetSeconds = Math.max(30, targetMinutes * 60);
  const wordCount = scriptText.split(/\s+/).filter(Boolean).length;
  log(`Job "${job.title}" (${job.jobId || 'no-id'}): ${wordCount} words, target ${targetSeconds}s, workDir ${workDir}`);

  // 1. Beats.
  const beats = splitBeats(scriptText, job);
  log(`Split narration into ${beats.length} beats (8-15s each)`);

  // 2. Narration — its real duration sets the timeline.
  const narration = await synthesizeNarration(scriptText, mediaDir, targetSeconds);
  const narrationDur = narration.duration || 0;
  const totalDuration = Math.max(targetSeconds, narrationDur);
  if (narrationDur > 0 && narrationDur < targetSeconds) {
    log(`Narration ${narrationDur.toFixed(1)}s < target ${targetSeconds}s; video timeline stretched to ${totalDuration}s (audio padded with silence at the tail)`);
  }
  for (const b of beats) b.duration = Math.max(5, totalDuration * (b.words / wordCount));
  // Normalise so the beats sum exactly to the timeline.
  const sum = beats.reduce((a, b) => a + b.duration, 0);
  beats.forEach((b) => { b.duration = b.duration * totalDuration / sum; });
  log(`Timeline: ${totalDuration.toFixed(1)}s total; beat durations ${beats.map((b) => b.duration.toFixed(1)).join(', ')}s`);

  // 3. Media per beat — fail loudly if any beat ends up with nothing real.
  const ctx = { mediaDir, usedUrls: new Set(), job, youtubeClips: 0, youtubeCandidates: null, youtubeBad: new Set() };
  const manifest = [];
  for (const beat of beats) {
    const media = await resolveBeatMedia(beat, ctx);
    beat.media = media;
    manifest.push({
      scene: beat.index, query: beat.query, beatText: beat.text.slice(0, 120),
      source: media.source, kind: media.kind, url: media.url, licence: media.licence,
      localPath: path.relative(workDir, media.localPath), duration: media.duration,
      title: media.title || '',
    });
    log(`Scene ${beat.index}: ${media.kind} via ${media.source} — ${(media.title || media.url || '').slice(0, 80)}`);
  }
  fs.writeFileSync(path.join(mediaDir, 'media-manifest.json'), JSON.stringify({
    jobId: job.jobId || '', title: job.title || '', channel: job.channel?.name || '',
    generatedAt: new Date().toISOString(), narration,
    stockKeysPresent: { pexels: Boolean(process.env.PEXELS_API_KEY), pixabay: Boolean(process.env.PIXABAY_API_KEY) },
    youtubeClips: ctx.youtubeClips, scenes: manifest,
  }, null, 2) + '\n', 'utf8');

  // 4. Segments + assembly (ffmpeg only, Ken Burns / xfade / captions).
  const segDir = path.join(mediaDir, 'segments');
  fs.mkdirSync(segDir, { recursive: true });
  const segmentPaths = [];
  const renderLens = [];
  for (const beat of beats) {
    const renderLen = beat.duration + (beat.index < beats.length - 1 ? XFADE : 0);
    const segPath = path.join(segDir, `seg-${beat.index}.mp4`);
    await buildSegment(beat, beat.media, renderLen, segPath, mediaDir, job);
    segmentPaths.push(segPath);
    renderLens.push(renderLen);
    log(`Segment ${beat.index} encoded (${renderLen.toFixed(1)}s, ${beat.media.kind})`);
  }
  if (!narration.skipped) {
    await assembleVideo(segmentPaths, renderLens, narration.file, totalDuration, outputPath);
  } else {
    throw new Error('Narration was skipped (RENDER_LINUX_SKIP_TTS=1); refusing to ship a silent master');
  }
  const probe = await ffprobeInfo(outputPath);
  log(`Master: ${outputPath} (${(fs.statSync(outputPath).size / 1048576).toFixed(1)} MB, ${Number(probe.format?.duration || 0).toFixed(1)}s)`);

  // 5. Thumbnail from the best still (first image asset, else first clip's frame).
  const bestStill = (manifest.find((m) => m.kind === 'image') || {}).localPath
    ? path.join(workDir, (manifest.find((m) => m.kind === 'image') || {}).localPath) : '';
  await makeThumbnail(job, bestStill, outputPath, thumbPath, mediaDir);

  // 6. Self-validation with the repo's own checks (lib/ffmpeg.js).
  const ffmpegLib = require('./lib/ffmpeg');
  const videoCheck = await ffmpegLib.validateVideoMaster(outputPath, targetMinutes);
  const thumbCheck = await ffmpegLib.validateThumbnail(thumbPath, '16:9');
  const validation = { video: videoCheck, thumbnail: thumbCheck };
  if (!videoCheck.ok) fail(`Self-validation failed: ${videoCheck.failures.join('; ')}`);
  if (!thumbCheck.ok) fail(`Thumbnail validation failed: ${thumbCheck.error}`);

  const meta = {
    engine: 'linux-ffmpeg-no-ai',
    duration: Number(probe.format?.duration || 0),
    width: Number((probe.streams || []).find((s) => s.codec_type === 'video')?.width || 0),
    height: Number((probe.streams || []).find((s) => s.codec_type === 'video')?.height || 0),
    sizeBytes: fs.statSync(outputPath).size,
    narration: { duration: narrationDur, voice: narration.voice, speed: narration.speed },
    scenes: manifest.length,
    youtubeClips: ctx.youtubeClips,
    validation,
  };
  fs.writeFileSync(path.join(mediaDir, 'render-report.json'), JSON.stringify({ jobId: job.jobId || '', title: job.title || '', meta, manifest }, null, 2) + '\n', 'utf8');
  process.stdout.write(JSON.stringify({ videoPath: outputPath, thumbnailPath: thumbPath, meta }) + '\n');
}

main().catch((e) => fail(e && e.message ? e.message : String(e)));
