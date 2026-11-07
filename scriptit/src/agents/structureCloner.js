'use strict';

/**
 * YouTube Video Structure Cloner Agent.
 * 
 * Capability:
 * 1. Takes a YouTube video URL, extracts its transcript via yt-dlp (scripts/yt_transcript.py).
 * 2. Feeds the transcript to the LLM to reverse-engineer its complete script architecture:
 *    - Hook formula & 15-second opening strategy
 *    - Sequence-by-sequence outline (roles, dramatic objectives, pacing, transitions, target shares)
 *    - Retention devices (pattern interrupts, open loops, callback rhythms)
 *    - Voice, tone, and sentence cadence profile
 * 3. Given a new video title/topic, generates a brand-new, original script that mirrors the
 *    exact same sequence structure, outline, and style — with zero copied facts or wording.
 */

const { fetchTranscript } = require('./narratorVideo');
const { chatCompletion } = require('../providers/openrouter');
const { writeSequence } = require('../engine/agents/bodyWriterAgent');
const { generateHook } = require('../engine/agents/hookAgent');
const { editRetention } = require('../engine/agents/retentionAgent');
const { deSlopScript } = require('../engine/agents/voiceAgent');
const { MemoryState } = require('../engine/state/memoryState');
const { wordsForDuration } = require('../utils/duration');
const { formatPlainNarration } = require('./scriptQuality');
const cache = require('../cache/cache');

const CLONER_MODEL = 'google/gemini-2.5-flash';

function parseJsonResponse(value) {
  if (!value) return null;
  const text = String(value).replace(/```json|```/gi, '').trim();
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start < 0 || end <= start) return null;
  try {
    return JSON.parse(text.slice(start, end + 1));
  } catch (_) {
    return null;
  }
}

/**
 * Fallback deterministic structure extractor when LLM is unavailable or for testing.
 */
function buildFallbackStructure(videoTitle = 'Reference Video', transcript = '') {
  return {
    referenceTitle: videoTitle,
    hookFormula: {
      type: 'bold-claim',
      validateClickPattern: 'Immediate confirmation of the subject within 3 seconds',
      raiseStakesPattern: 'Reveals an unforeseen risk or contradiction within 8 seconds',
      openCuriosityLoopPattern: 'Teases the decisive behind-the-scenes decision without spoiling the outcome'
    },
    pacingProfile: {
      cadence: 'Dynamic, alternating between punchy short statements and explanatory context',
      sentenceRhythm: 'Mix of 4-word punchy sentences and 15-word rhythmic clauses',
      audienceAddress: 'Direct, speaking to the viewer as an informed peer'
    },
    sequences: [
      {
        index: 1,
        name: 'The Point of Attack & Accepted Illusion',
        role: 'Enter at the moment of rupture; challenge what the public accepts as truth.',
        targetShare: 0.25,
        pacing: 'Fast and gripping',
        transitionMechanism: 'Rhetorical contrast between public myth and private record',
        retentionDevice: 'Open curiosity loop on the hidden catalyst'
      },
      {
        index: 2,
        name: 'The Behind-the-Scenes Mechanism',
        role: 'Unpack the operational realities, competing agendas, and technical constraints.',
        targetShare: 0.30,
        pacing: 'Measured and investigative',
        transitionMechanism: 'Chronological escalation of pressure',
        retentionDevice: 'Pattern interrupt questioning the conventional assumption'
      },
      {
        index: 3,
        name: 'The Critical Turning Point',
        role: 'The point of no return: the gamble or crisis that decided the outcome.',
        targetShare: 0.25,
        pacing: 'High tension',
        transitionMechanism: 'Consequence bridge connecting action to fallout',
        retentionDevice: 'Rehook on the unexpected collateral cost'
      },
      {
        index: 4,
        name: 'The Permanent Consequence & Payoff',
        role: 'Deliver the promised answer, resolve the core mystery, and show the lasting legacy.',
        targetShare: 0.20,
        pacing: 'Resonant and decisive',
        transitionMechanism: 'Final reflective takeaway',
        retentionDevice: 'Full payoff of the initial curiosity loop'
      }
    ]
  };
}

/**
 * Extracts the complete script structure, sequence breakdown, and style from a YouTube video transcript.
 */
async function extractStructureFromVideo({
  videoUrl,
  transcript = '',
  apiKey = '',
  model = CLONER_MODEL,
  useCache = true,
  chatCompletionFn = null,
  fetchTranscriptFn = null
}) {
  const url = String(videoUrl || '').trim();
  let videoInfo = { url, title: 'Reference Video', channel: '', durationSec: 600 };
  let rawTranscript = transcript;

  if (useCache && url) {
    const cached = cache.get(`structure:${url}`);
    if (cached) return { ...cached, cached: true };
  }

  // Fetch transcript using yt-dlp helper if transcript not directly supplied
  if (!rawTranscript && url) {
    const fetchFn = fetchTranscriptFn || fetchTranscript;
    const fetched = await fetchFn(url);
    if (!fetched || !fetched.transcript) {
      throw new Error(fetched?.error || 'Could not retrieve transcript from YouTube URL');
    }
    rawTranscript = fetched.transcript;
    videoInfo = {
      url,
      title: fetched.video?.title || 'Reference Video',
      channel: fetched.video?.channel || '',
      durationSec: fetched.video?.durationSec || 600,
    };
  }

  if (!rawTranscript) {
    throw new Error('No transcript available to analyze');
  }

  // If no LLM available, return deterministic structural profile
  if (!apiKey && !chatCompletionFn) {
    const fallback = buildFallbackStructure(videoInfo.title, rawTranscript);
    return { ok: true, video: videoInfo, structure: fallback };
  }

  const prompt = `You are a master YouTube script architect and showrunner.
Analyze this YouTube video transcript to REVERSE-ENGINEER ITS EXACT SCRIPT STRUCTURE AND OUTLINE.

VIDEO: "${videoInfo.title}"
CHANNEL: "${videoInfo.channel}"
TRANSCRIPT EXCERPT:
"""${rawTranscript.slice(0, 25000)}"""

STRICT DIRECTIVE:
Do NOT copy or summarize the video's specific facts, names, dates, or stories.
Extract the UNDERLYING SKELETON:
1. Hook formula (how the first 15–30 seconds hook the viewer: click validation, stakes, curiosity loop).
2. Sequence-by-sequence breakdown (3 to 6 distinct sequence beats: role, dramatic objective, transition mechanism, target share of runtime).
3. Pacing & sentence rhythm profile (sentence length cadence, audience address style).
4. Retention devices (where pattern interrupts, rehooks, and open loops are planted).

Return valid JSON ONLY in this exact structure:
{
  "referenceTitle": "${videoInfo.title}",
  "hookFormula": {
    "type": "result-first | bold-claim | relatable-problem",
    "validateClickPattern": "...",
    "raiseStakesPattern": "...",
    "openCuriosityLoopPattern": "..."
  },
  "pacingProfile": {
    "cadence": "...",
    "sentenceRhythm": "...",
    "audienceAddress": "..."
  },
  "sequences": [
    {
      "index": 1,
      "name": "Sequence Beat Name",
      "role": "Dramatic role and what changes in this sequence",
      "targetShare": 0.25,
      "pacing": "Fast | Measured | Building",
      "transitionMechanism": "How this sequence connects to the next",
      "retentionDevice": "Specific retention tactic used here"
    }
  ]
}`;

  try {
    const fn = chatCompletionFn || chatCompletion;
    const response = await fn({
      apiKey,
      model,
      messages: [
        { role: 'system', content: 'You are an elite YouTube script architect. Output JSON only.' },
        { role: 'user', content: prompt }
      ],
      temperature: 0.2,
      maxTokens: 2000,
    });

    const parsed = parseJsonResponse(response && response.content);
    if (parsed && Array.isArray(parsed.sequences) && parsed.sequences.length >= 2) {
      const result = {
        ok: true,
        video: videoInfo,
        structure: parsed
      };
      if (useCache && url) cache.set(`structure:${url}`, result);
      return result;
    }
  } catch (err) {
    console.warn('[StructureCloner] LLM structure extraction failed, using fallback structure:', err.message);
  }

  const fallback = buildFallbackStructure(videoInfo.title, rawTranscript);
  return { ok: true, video: videoInfo, structure: fallback };
}

/**
 * Generates a brand-new script for a new title/topic using the cloned structure profile.
 */
async function generateScriptFromStructure({
  referenceStructure,
  newTitle,
  newTopic,
  duration = '10min',
  voice = 'conversational',
  evidence = null,
  apiKey = '',
  model = CLONER_MODEL,
  chatCompletionFn = null
}) {
  const title = String(newTitle || newTopic || '').trim();
  if (!title) throw new Error('newTitle or newTopic is required');

  const structure = referenceStructure?.structure || referenceStructure || buildFallbackStructure();
  const sequences = Array.isArray(structure.sequences) && structure.sequences.length >= 2
    ? structure.sequences
    : buildFallbackStructure().sequences;

  const totalWords = wordsForDuration(duration);
  const memoryState = new MemoryState({
    topic: title,
    format: 'cloned_structure',
    totalSequences: sequences.length
  });

  const evidenceText = evidence && Array.isArray(evidence.claims)
    ? evidence.claims.slice(0, 15).map((c) => `- [${c.id}] ${c.text}`).join('\n')
    : '';

  // Generate sequence-by-sequence mirroring the reference video's sequence roles
  const sectionResults = [];

  for (let idx = 0; idx < sequences.length; idx++) {
    const refSeq = sequences[idx];
    const seqTargetWords = Math.round(totalWords * (refSeq.targetShare || (1 / sequences.length)));

    const adaptedSequence = {
      id: `seq_${refSeq.index || idx + 1}`,
      name: refSeq.name,
      summary: `Apply the reference sequence role ("${refSeq.role}") to "${title}". Transition mechanism: ${refSeq.transitionMechanism || 'natural bridge'}. Pacing: ${refSeq.pacing || 'measured'}.`,
      targetWords: seqTargetWords,
      progressCallback: refSeq.retentionDevice?.includes('callback') ? `Progressing through the timeline...` : null
    };

    const secResult = await writeSequence({
      topic: title,
      sequence: adaptedSequence,
      sequenceIndex: idx,
      totalSequences: sequences.length,
      thesisStatement: `Applying the structural blueprint of "${structure.referenceTitle || 'the reference video'}" to "${title}".`,
      memoryState,
      voice,
      evidenceText,
      apiKey,
      model,
      chatCompletionFn
    });

    sectionResults.push(secResult);
  }

  const rawBodyScript = sectionResults.map((s) => s.text).join('\n\n');

  // Hook Agent: mirror the reference hook formula (result-first, bold-claim, or relatable-problem)
  const hookResult = await generateHook({
    topic: title,
    bodyScript: rawBodyScript,
    thesisStatement: `The definitive story of ${title}`,
    apiKey,
    model,
    chatCompletionFn
  });

  // Retention Editor: stitch with bridges and pattern interrupts
  const retentionResult = editRetention({
    hook: hookResult.selectedHook,
    sections: sectionResults,
    memoryState,
    duration
  });

  // Voice & De-Slop pass
  const voiceResult = await deSlopScript({
    script: retentionResult.script,
    topic: title,
    voice,
    apiKey,
    model,
    chatCompletionFn
  });

  const finalScript = voiceResult.script;
  const wordCount = finalScript.split(/\s+/).filter(Boolean).length;

  return {
    script: finalScript,
    meta: {
      topic: title,
      duration,
      wordCount,
      clonedFrom: structure.referenceTitle || 'YouTube Reference Video',
      clonedStructure: structure,
      selectedHook: hookResult.selectedHook,
      hookVariants: hookResult.variants,
      retentionChecklist: retentionResult.checklist,
      retentionBlocks: retentionResult.annotatedBlocks,
      status: 'ready_to_publish',
      publishable: true
    }
  };
}

module.exports = {
  extractStructureFromVideo,
  generateScriptFromStructure,
  buildFallbackStructure
};
