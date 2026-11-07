'use strict';

/**
 * Retention Editor Agent.
 * 
 * Implements the Momentum Mechanics layer:
 * 1. Pattern Interrupts every ~90 seconds (~200 words) to boost average view duration 15–25%.
 * 2. Rehooks / mini-hooks every 2–3 minutes on longer scripts.
 * 3. Section Bridges at the seams between sections to prevent drop-off.
 * 4. Programmatic Retention Annotation tagging checklist:
 *    [hook], [open-loop], [pattern-interrupt], [rehook], [bridge], [payoff].
 */

const PATTERN_INTERRUPT_TEMPLATES = [
  'Stop and think about what that actually means for a second.',
  'Now, look at what happened next, because almost nobody noticed this.',
  'Here is the part where the conventional story completely falls apart.',
  'Notice the contradiction here.',
  'If that sounds unbelievable, the recorded numbers get even stranger.',
  'Keep that detail in mind, because it changes everything in just a moment.'
];

const REHOOK_TEMPLATES = [
  'What came next is the part that changed the entire trajectory.',
  'Just when it looked like the outcome was decided, a new problem emerged.',
  'The real crisis was only getting started.',
  'Behind the scenes, the stakes were about to double.'
];

const WORDS_PER_MINUTE = 140; // Standard YouTube speaking pace

/**
 * Ensures clean section bridges between section ends and next section starts.
 */
function buildSectionBridge(currentSectionText, nextSectionName) {
  const cleanNext = String(nextSectionName || 'the next phase').replace(/^#?\d+[:.]\s*/, '');
  return `That leads directly to ${cleanNext.toLowerCase()}, where the real test began.`;
}

/**
 * Retention Editor: Processes the hook and body sections into a seamless, high-retention script
 * with annotated retention tags.
 */
function editRetention({
  hook,
  sections = [],
  memoryState = null,
  duration = '10min'
}) {
  const annotatedBlocks = [];
  const assembledParagraphs = [];

  // 1. Hook Block
  const hookText = hook && hook.text ? hook.text.trim() : (typeof hook === 'string' ? hook.trim() : '');
  if (hookText) {
    annotatedBlocks.push({
      type: 'hook',
      tags: ['hook', 'click-validation', 'stakes', 'curiosity-loop'],
      text: hookText,
      wordCount: hookText.split(/\s+/).filter(Boolean).length,
      estimatedTimeSec: Math.round((hookText.split(/\s+/).filter(Boolean).length / WORDS_PER_MINUTE) * 60)
    });
    assembledParagraphs.push(hookText);
  }

  let totalWordsSoFar = hookText ? hookText.split(/\s+/).filter(Boolean).length : 0;
  let wordsSinceLastInterrupt = totalWordsSoFar;
  let wordsSinceLastRehook = totalWordsSoFar;
  let interruptIndex = 0;
  let rehookIndex = 0;

  // 2. Iterate through body sections
  for (let sIdx = 0; sIdx < sections.length; sIdx++) {
    const sec = sections[sIdx];
    const secText = String(sec.text || '').trim();
    if (!secText) continue;

    const paragraphs = secText.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);

    for (let pIdx = 0; pIdx < paragraphs.length; pIdx++) {
      let para = paragraphs[pIdx];
      const pWords = para.split(/\s+/).filter(Boolean).length;
      totalWordsSoFar += pWords;
      wordsSinceLastInterrupt += pWords;
      wordsSinceLastRehook += pWords;

      // Check if we need a Pattern Interrupt (~90s = ~210 words)
      if (wordsSinceLastInterrupt >= 210 && sIdx > 0) {
        const interrupt = PATTERN_INTERRUPT_TEMPLATES[interruptIndex % PATTERN_INTERRUPT_TEMPLATES.length];
        interruptIndex++;
        wordsSinceLastInterrupt = 0;

        // Prepend pattern interrupt to this paragraph or inject as block
        para = `${interrupt} ${para}`;
        annotatedBlocks.push({
          type: 'pattern-interrupt',
          tags: ['pattern-interrupt', 'retention-boost'],
          text: interrupt,
          wordCount: interrupt.split(/\s+/).filter(Boolean).length,
          estimatedTimeSec: 4
        });
      }

      // Check if we need a Rehook (~2.5 minutes = ~350 words on longer scripts)
      if (wordsSinceLastRehook >= 350 && sIdx < sections.length - 1) {
        const rehook = REHOOK_TEMPLATES[rehookIndex % REHOOK_TEMPLATES.length];
        rehookIndex++;
        wordsSinceLastRehook = 0;

        annotatedBlocks.push({
          type: 'rehook',
          tags: ['rehook', 'mini-hook', 'open-loop'],
          text: rehook,
          wordCount: rehook.split(/\s+/).filter(Boolean).length,
          estimatedTimeSec: 5
        });
        para = `${para}\n\n${rehook}`;
      }

      // Check if this sequence closes any open loops
      const closingLoops = Array.isArray(sec.closeLoops) ? sec.closeLoops : [];
      const tags = ['content'];
      if (closingLoops.length) {
        tags.push('payoff', ...closingLoops.map((id) => `payoff:${id}`));
      }

      annotatedBlocks.push({
        type: closingLoops.length ? 'payoff' : 'content',
        sequenceId: sec.sequenceId,
        tags,
        text: para,
        wordCount: para.split(/\s+/).filter(Boolean).length,
        estimatedTimeSec: Math.round((para.split(/\s+/).filter(Boolean).length / WORDS_PER_MINUTE) * 60)
      });

      assembledParagraphs.push(para);
    }

    // 3. Check section seam bridge before the next section
    if (sIdx < sections.length - 1) {
      const nextSec = sections[sIdx + 1];
      const bridge = buildSectionBridge(secText, nextSec.name);
      annotatedBlocks.push({
        type: 'bridge',
        tags: ['bridge', 'seam-retention'],
        fromSequence: sec.sequenceId,
        toSequence: nextSec.sequenceId,
        text: bridge,
        wordCount: bridge.split(/\s+/).filter(Boolean).length,
        estimatedTimeSec: 4
      });
      assembledParagraphs.push(bridge);
      totalWordsSoFar += bridge.split(/\s+/).filter(Boolean).length;
      wordsSinceLastInterrupt += bridge.split(/\s+/).filter(Boolean).length;
    }
  }

  const finalScriptText = assembledParagraphs.join('\n\n').trim();
  const finalWordCount = finalScriptText.split(/\s+/).filter(Boolean).length;

  // Retention density checklist
  const interruptCount = annotatedBlocks.filter((b) => b.type === 'pattern-interrupt').length;
  const rehookCount = annotatedBlocks.filter((b) => b.type === 'rehook').length;
  const bridgeCount = annotatedBlocks.filter((b) => b.type === 'bridge').length;
  const hasPayoff = annotatedBlocks.some((b) => b.type === 'payoff' || b.tags.includes('payoff'));

  const checklist = {
    hasHook: Boolean(hookText),
    patternInterruptCount: interruptCount,
    rehookCount,
    bridgeCount,
    hasPayoff,
    estimatedTotalMinutes: Math.round((finalWordCount / WORDS_PER_MINUTE) * 10) / 10,
    retentionScore: Math.min(100, (hookText ? 30 : 0) + (interruptCount >= 2 ? 25 : interruptCount * 12) + (bridgeCount >= 1 ? 20 : 0) + (hasPayoff ? 25 : 0))
  };

  return {
    script: finalScriptText,
    annotatedBlocks,
    checklist,
    wordCount: finalWordCount
  };
}

module.exports = {
  editRetention,
  buildSectionBridge,
  PATTERN_INTERRUPT_TEMPLATES,
  REHOOK_TEMPLATES
};
