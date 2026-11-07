'use strict';
/*
 * Narrator reference video agent.
 *
 * Given a YouTube video URL that a channel wants to sound like, this agent:
 *   1. fetches the transcript via yt-dlp (scripts/yt_transcript.py)
 *   2. summarizes it with a cheap flash model (Gemini 2.5 Flash by default)
 *      into a "narrator profile": structure, tonality, script flow,
 *      audience behaviour, hook style, pacing, sentence rhythm, retention
 *      tricks and signature phrasing — STYLE ONLY, never the video's content.
 *
 * The resulting profile is passed to the script writer so new scripts match
 * the narrator's structure and flow without copying wording or facts.
 */
const path = require('path');
const os = require('os');
const fs = require('fs');
const { spawn } = require('child_process');
const { chatCompletion } = require('../providers/openrouter');
const cache = require('../cache/cache');

const NARRATOR_MODEL = require('../providers/modelPolicy').MODEL;
const PYTHON = process.env.SCRIPTIT_PYTHON || process.env.PYTHON || 'python';
const HELPER = path.join(__dirname, '..', '..', 'scripts', 'yt_transcript.py');

const PROFILE_KEYS = [
  'structure',
  'tonality',
  'scriptFlow',
  'audienceBehaviour',
  'hookStyle',
  'pacing',
  'sentenceRhythm',
  'retentionTricks',
  'signaturePhrases',
  'styleNotes',
];

function runPython(args, timeoutMs) {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => {
      try { child.kill('SIGKILL'); } catch (_) {}
      reject(new Error('Transcript fetch timed out after ' + Math.round(timeoutMs / 1000) + 's'));
    }, timeoutMs);
    child.stdout.on('data', d => { stdout += d.toString(); });
    child.stderr.on('data', d => { stderr += d.toString(); });
    child.on('error', err => { clearTimeout(timer); reject(err); });
    child.on('exit', code => {
      clearTimeout(timer);
      // yt-dlp can write carriage-return progress updates immediately before
      // the helper's JSON, even with quiet/no_warnings enabled. Splitting only
      // on newlines therefore loses a valid JSON object that shares the final
      // physical line with progress text.
      let parsed = null;
      const chunks = stdout.split(/[\r\n]+/).reverse();
      for (const chunk of chunks) {
        const start = chunk.indexOf('{');
        if (start < 0) continue;
        try {
          const candidate = JSON.parse(chunk.slice(start).trim());
          if (candidate && typeof candidate === 'object') { parsed = candidate; break; }
        } catch (_) { /* keep looking for the final JSON object */ }
      }
      if (!parsed) {
        reject(new Error('yt-dlp transcript helper produced no output: ' + (stderr.trim().slice(0, 300) || ('exit code ' + code))));
        return;
      }
      resolve(parsed);
    });
  });
}

async function fetchTranscript(videoUrl, timeoutMs) {
  const outdir = fs.mkdtempSync(path.join(os.tmpdir(), 'scriptit-narrator-'));
  try {
    const result = await runPython([HELPER, videoUrl, outdir], timeoutMs || 180000);
    if (!result.ok) throw new Error(result.error || 'Transcript fetch failed');
    return result;
  } finally {
    try { fs.rmSync(outdir, { recursive: true, force: true }); } catch (_) {}
  }
}

function buildTranscriptPrompt(video) {
  return `You are analyzing a YouTube video's NARRATION STYLE so a creator can write new scripts that feel like the same narrator.

VIDEO: ${video.title || '(unknown title)'}
CHANNEL: ${video.channel || '(unknown channel)'}
DURATION: ${video.durationSec ? Math.round(video.durationSec / 60) + ' minutes' : 'unknown'}
TRANSCRIPT (${video.transcriptChars || video.transcript.length} chars):
"""${video.transcript}"""

ANALYZE THE STYLE ONLY. The content, facts, examples and opinions of this video are irrelevant — do NOT summarize or restate them, and do NOT list any specific claims, numbers, names or events from it.

Extract, in detail:
1. structure — how the video is organized (hook → setup → sections → payoff → outro), section transitions, segment lengths
2. tonality — mood, attitude, energy level, directness, humor, formality
3. scriptFlow — how ideas are sequenced and connected, rhetorical devices, callback patterns, question-answer beats
4. audienceBehaviour — who the narrator treats the viewer as, engagement tactics, assumptions about the viewer's knowledge
5. hookStyle — how the opening 30 seconds grab attention, what pattern it uses
6. pacing — speech speed feel, pause rhythm, when details are dumped vs teased
7. sentenceRhythm — sentence length mix, paragraphing, repetition patterns
8. retentionTricks — cliffhangers, loops, value promises, pattern interrupts, mid-roll payoffs
9. signaturePhrases — up to 6 recurring phrases or verbal habits (NO niche-specific jargon)

Return ONLY a JSON object with these exact keys:
{"structure":"...","tonality":"...","scriptFlow":"...","audienceBehaviour":"...","hookStyle":"...","pacing":"...","sentenceRhythm":"...","retentionTricks":"...","signaturePhrases":["...","..."],"styleNotes":"..."}
Keep every field a concise paragraph (2-4 sentences) of style observations. Never quote content from the video.`;
}

function extractProfileJson(text) {
  if (!text) return null;
  let candidate = String(text).trim();
  const fence = candidate.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fence) candidate = fence[1].trim();
  const open = candidate.indexOf('{');
  const close = candidate.lastIndexOf('}');
  if (open !== -1 && close > open) candidate = candidate.slice(open, close + 1);
  try {
    const parsed = JSON.parse(candidate);
    if (parsed && typeof parsed === 'object') return parsed;
  } catch (_) {}
  return null;
}

function sanitizeProfile(parsed, fallbackText) {
  const profile = {};
  for (const key of PROFILE_KEYS) {
    if (key === 'signaturePhrases') {
      const v = parsed && parsed.signaturePhrases;
      profile.signaturePhrases = Array.isArray(v) ? v.map(String).filter(Boolean).slice(0, 6) : [];
    } else {
      const v = parsed && parsed[key];
      profile[key] = typeof v === 'string' && v.trim() ? v.trim() : '';
    }
  }
  if (!Object.values(profile).filter(v => Array.isArray(v) ? v.length : v).length) {
    profile.styleNotes = String(fallbackText || '').trim().slice(0, 2000);
  }
  return profile;
}

function narratorProfileToPromptText(profile) {
  if (!profile) return '';
  const parts = [];
  const labels = {
    structure: 'STRUCTURE', tonality: 'TONALITY', scriptFlow: 'SCRIPT FLOW',
    audienceBehaviour: 'AUDIENCE BEHAVIOUR', hookStyle: 'HOOK STYLE', pacing: 'PACING',
    sentenceRhythm: 'SENTENCE RHYTHM', retentionTricks: 'RETENTION TRICKS', styleNotes: 'STYLE NOTES',
  };
  for (const key of PROFILE_KEYS) {
    if (key === 'signaturePhrases') {
      const phrases = profile.signaturePhrases || [];
      if (phrases.length) parts.push('SIGNATURE PHRASES: ' + phrases.join(' | '));
      continue;
    }
    const value = profile[key];
    if (value) parts.push(labels[key] + ': ' + value);
  }
  return parts.join('\n');
}

async function summarizeTranscript({ apiKey, video, model = NARRATOR_MODEL, chatCompletionFn }) {
  const fn = chatCompletionFn || chatCompletion;
  const result = await fn({
    apiKey,
    model,
    messages: [
      { role: 'system', content: 'You are a precise YouTube narration style analyst. You extract storytelling technique, never content. Always answer with valid JSON only.' },
      { role: 'user', content: buildTranscriptPrompt(video) },
    ],
    temperature: 0.3,
    maxTokens: 2000,
    timeoutMs: 240000,
  });
  return extractProfileJson(result.content);
}

async function buildNarratorProfile({ videoUrl, apiKey, model = NARRATOR_MODEL, useCache = true, chatCompletionFn, fetchTranscriptFn }) {
  if (require('../providers/agentInference').isAgentHandle(apiKey)) model = 'external-agent';
  else model = require('../providers/modelPolicy').normalizeModel(model);
  const url = String(videoUrl || '').trim();
  if (!url) throw new Error('videoUrl is required');
  if (!apiKey) throw new Error('OpenRouter API key is required');

  let cached = false;
  if (useCache) {
    const hit = cache.get(`narrator:${url}`);
    if (hit) {
      cached = true;
      return { ...hit, cached: true };
    }
  }

  const fetchFn = fetchTranscriptFn || fetchTranscript;
  const fetched = await fetchFn(url);
  const video = {
    url,
    title: fetched.video && fetched.video.title || '',
    channel: fetched.video && fetched.video.channel || '',
    durationSec: fetched.video && fetched.video.durationSec || 0,
    transcript: fetched.transcript,
    transcriptChars: fetched.transcriptChars || fetched.transcript.length,
    subtitleKind: fetched.subtitleKind || '',
  };

  const raw = await summarizeTranscript({ apiKey, video, model, chatCompletionFn });
  const profile = sanitizeProfile(raw, video.transcript);

  const result = {
    ok: true,
    video: {
      url: video.url,
      title: video.title,
      channel: video.channel,
      durationSec: video.durationSec,
    },
    transcriptChars: video.transcriptChars,
    subtitleKind: video.subtitleKind,
    model,
    profile,
  };
  if (useCache) cache.set(`narrator:${url}`, result);
  return result;
}

module.exports = {
  NARRATOR_MODEL,
  PROFILE_KEYS,
  fetchTranscript,
  buildTranscriptPrompt,
  extractProfileJson,
  sanitizeProfile,
  narratorProfileToPromptText,
  summarizeTranscript,
  buildNarratorProfile,
};
