'use strict';

/**
 * Universal Script Generation Engine.
 * 
 * Orchestrates the full 8-stage multi-agent pipeline:
 * [1] Classifier: picks format & structure template
 * [2] Outline Agent: sequence-level outline with thesis / want-need / escalating list
 * [3] Memory State: tracks open loops, established facts, entity states
 * [4] Body Writer: expands sequence-by-sequence conditioned on memory state
 * [5] Hook Agent: runs LAST with 3-part formula and 3 variants (result-first, bold-claim, relatable-problem)
 * [6] Retention Editor: pattern interrupts every ~90s, section bridges, retention annotations
 * [7] Voice / De-Slop Agent: bans AI-tells ("not X but Y", buzzwords, stock intros), enforces cadence
 * [8] Critique & Scoring Agent: scores 1-5 rubric (coherence, surprise, pacing, voice), bounded revision
 */

const { classifyTopic } = require('../agents/classifier');
const { getStructureSchema } = require('./structure/structureSchemas');
const { MemoryState } = require('./state/memoryState');
const { generateOutline } = require('./agents/outlineAgent');
const { writeSequence } = require('./agents/bodyWriterAgent');
const { generateHook } = require('./agents/hookAgent');
const { editRetention } = require('./agents/retentionAgent');
const { deSlopScript } = require('./agents/voiceAgent');
const { critiqueScript } = require('./agents/critiqueAgent');

async function runUniversalEngine({
  topic,
  duration = '10min',
  contentType = 'auto',
  voice = 'conversational',
  evidence = null,
  brief = null,
  apiKey = '',
  model = '',
  chatCompletionFn = null,
  styleFingerprint = {},
  maxRevisionLoops = 1
}) {
  const startedAt = Date.now();
  const cleanTopic = String(topic || '').trim();

  // [1] Classification & Structure Schema Selection
  let detectedType = contentType && contentType !== 'auto' ? contentType : classifyTopic(cleanTopic).type;
  const schema = getStructureSchema(detectedType);

  // [2] Outline Generation (Sequence-level, Thesis / Want-Need / Ranked list)
  const outline = await generateOutline({
    topic: cleanTopic,
    format: schema.format || detectedType,
    duration,
    evidence,
    briefText: brief?.query || '',
    apiKey,
    model,
    chatCompletionFn
  });

  const sequences = Array.isArray(outline.sequences) && outline.sequences.length >= 2
    ? outline.sequences
    : [
        { id: 'seq_1', name: 'The Setup & Rupture', summary: `Introduce ${cleanTopic} and the core paradox.`, targetWords: 300 },
        { id: 'seq_2', name: 'The Escalation', summary: `The unseen pressures and turning point.`, targetWords: 400 },
        { id: 'seq_3', name: 'The Climax & Payoff', summary: `The resolution and enduring consequence.`, targetWords: 300 }
      ];

  // [3] World & Memory State Initialization
  const memoryState = new MemoryState({
    topic: cleanTopic,
    format: outline.format || detectedType,
    totalSequences: sequences.length
  });

  // [4] Section-by-Section Body Writing (conditioned on running memory state)
  const sectionResults = [];
  const evidenceText = evidence && Array.isArray(evidence.claims)
    ? evidence.claims.slice(0, 15).map((c) => `- [${c.id}] ${c.text}`).join('\n')
    : '';

  for (let idx = 0; idx < sequences.length; idx++) {
    const seq = sequences[idx];
    const secResult = await writeSequence({
      topic: cleanTopic,
      sequence: seq,
      sequenceIndex: idx,
      totalSequences: sequences.length,
      thesisStatement: outline.thesisStatement,
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

  // [5] Hook Agent (runs LAST, conditioned on finished body payoff)
  const hookResult = await generateHook({
    topic: cleanTopic,
    bodyScript: rawBodyScript,
    thesisStatement: outline.thesisStatement,
    apiKey,
    model,
    chatCompletionFn
  });

  // [6] Retention Editor (bridges, ~90s pattern interrupts, rehooks, retention tags)
  const retentionResult = editRetention({
    hook: hookResult.selectedHook,
    sections: sectionResults,
    memoryState,
    duration
  });

  // [7] Voice & De-Slop Agent (negative constraints, "not X but Y", cadence)
  let voiceResult = await deSlopScript({
    script: retentionResult.script,
    topic: cleanTopic,
    voice,
    styleFingerprint,
    apiKey,
    model,
    chatCompletionFn
  });

  // [8] Critique & Rubric Scoring Agent (Coherence, Surprise, Pacing, Voice 1-5)
  let critiqueResult = await critiqueScript({
    script: voiceResult.script,
    topic: cleanTopic,
    thesisStatement: outline.thesisStatement,
    retentionChecklist: retentionResult.checklist,
    memoryState,
    apiKey,
    model,
    chatCompletionFn
  });

  // Optional Bounded Revision Loop if score is below threshold
  if (!critiqueResult.passes && maxRevisionLoops > 0 && (apiKey || chatCompletionFn)) {
    console.log('[UniversalEngine] Revision loop triggered with feedback:', critiqueResult.feedback.join('; '));
    // Apply second targeted polish with feedback
    voiceResult = await deSlopScript({
      script: voiceResult.script,
      topic: cleanTopic,
      voice,
      styleFingerprint: {
        ...styleFingerprint,
        bannedWords: [...(styleFingerprint.bannedWords || []), 'delve', 'testament', 'unwavering']
      },
      apiKey,
      model,
      chatCompletionFn
    });

    critiqueResult = await critiqueScript({
      script: voiceResult.script,
      topic: cleanTopic,
      thesisStatement: outline.thesisStatement,
      retentionChecklist: retentionResult.checklist,
      memoryState,
      apiKey,
      model,
      chatCompletionFn
    });
  }

  const totalTimeMs = Date.now() - startedAt;
  const finalScript = voiceResult.script;
  const wordCount = finalScript.split(/\s+/).filter(Boolean).length;

  return {
    script: finalScript,
    meta: {
      topic: cleanTopic,
      format: outline.format || detectedType,
      duration,
      wordCount,
      thesisStatement: outline.thesisStatement,
      pointOfAttack: outline.pointOfAttack,
      hookVariants: hookResult.variants,
      selectedHookType: hookResult.selectedHook?.type,
      memoryAudit: memoryState.auditState(),
      retentionChecklist: retentionResult.checklist,
      retentionBlocks: retentionResult.annotatedBlocks,
      rubricScores: critiqueResult.scores,
      critiqueAverage: critiqueResult.average,
      critiquePasses: critiqueResult.passes,
      critiqueFeedback: critiqueResult.feedback,
      voiceAuthenticityScore: voiceResult.authenticityScore,
      timing: {
        totalMs: totalTimeMs,
        sequenceCount: sequences.length
      }
    }
  };
}

module.exports = {
  runUniversalEngine
};
