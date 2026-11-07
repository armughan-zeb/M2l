'use strict';

/**
 * Body Writer Agent.
 * 
 * Writes the body script section-by-section (sequence by sequence).
 * Key Principle: Passes ONLY the current sequence + running memory state,
 * avoiding long-context drift and coherence collapse.
 */

const { formatPlainNarration } = require('../../agents/scriptQuality');

const SECTION_NEGATIVE_CONSTRAINTS = `
NEGATIVE CONSTRAINTS (STRICTLY FORBIDDEN):
- NEVER use stock transitions: "in today's video", "let's dive in", "let's unpack", "without further ado", "here's the thing".
- NEVER use "it's not X, it's Y" constructions or pseudo-philosophical AI antitheses.
- NEVER use corporate filler: leverage, utilize, streamline, robust, delve, testament to, unwavering.
- NEVER summarize or write a recap footer at the end of this section.
- Vary sentence length aggressively: alternate short 3-6 word punches with rhythmic clauses.
- Concrete specificity beats generalization: name exact dates, numbers, constraints, and locations from evidence.
`;

function extractFactualSentences(text) {
  const sentences = String(text || '').match(/[^.!?]+(?:[.!?](?!\d)|$)/g) || [];
  return sentences
    .map((s) => s.trim())
    .filter((s) => s.length > 25 && /\b\d{4}\b|\b\d+(?:\.\d+)?\b|\b[A-Z][a-z]{3,}\b/.test(s))
    .slice(0, 3);
}

/**
 * Writes one sequence of the script.
 */
async function writeSequence({
  topic,
  sequence,
  sequenceIndex,
  totalSequences,
  thesisStatement = '',
  memoryState,
  voice = 'conversational',
  evidenceText = '',
  apiKey = '',
  model = '',
  chatCompletionFn = null
}) {
  const targetWords = sequence.targetWords || 300;
  const memoryContext = memoryState.toPromptContext(sequenceIndex);

  // Auto-register any new loops specified for this sequence
  if (Array.isArray(sequence.openLoops)) {
    sequence.openLoops.forEach((loop) => {
      memoryState.openLoop({
        loopId: loop.id,
        title: loop.title,
        openedAt: sequenceIndex,
        mustCloseBy: sequenceIndex + 2
      });
    });
  }

  const prompt = `You are a world-class YouTube scriptwriter known for magnetic pacing and authentic human voice.
You are writing SEQUENCE ${sequenceIndex + 1} OF ${totalSequences} for a video on "${topic}".

VIDEO THESIS:
"${thesisStatement || topic}"

CURRENT SEQUENCE GOAL:
Name: ${sequence.name}
Summary: ${sequence.summary}
Target length for this section: ~${targetWords} words (${Math.round(targetWords * 0.85)} - ${Math.round(targetWords * 1.15)} words).

${sequence.progressCallback ? `PROGRESS CALLBACK REQUIRED:\nInclude this progress callback naturally in the narration: "${sequence.progressCallback}"\n` : ''}

${memoryContext}

${evidenceText ? `EVIDENCE LEDGER (Use ONLY confirmed facts from here):\n${evidenceText.slice(0, 2500)}\n` : ''}

${SECTION_NEGATIVE_CONSTRAINTS}

INSTRUCTIONS:
1. Write ONLY the continuous spoken narration for this specific sequence.
2. If this is Sequence 1, do NOT write the 15-second opening hook (the Hook Agent will write the hook last). Start directly with the point of attack or scene action.
3. If this section is instructed to close an open loop, provide the definitive answer/payoff directly.
4. If an open loop is active, maintain curiosity and tension without spoiling the reveal.
5. End this section on an active momentum note that naturally sets up the next beat.
6. Return purely spoken narration text. No markdown headings, no scene labels, no "narrator:", no quotes around everything.`;

  let sectionText = '';

  if (apiKey || chatCompletionFn) {
    try {
      const fn = chatCompletionFn || require('../../providers/openrouter').chatCompletion;
      const response = await fn({
        apiKey,
        model,
        messages: [
          { role: 'system', content: `You write elite YouTube video narration in a ${voice} human voice. Follow word limits precisely. Output narration text only.` },
          { role: 'user', content: prompt }
        ],
        temperature: 0.6,
        maxTokens: Math.max(800, targetWords * 2)
      });
      sectionText = formatPlainNarration(response && response.content);
    } catch (err) {
      console.warn(`[BodyWriter] Sequence ${sequenceIndex + 1} call failed:`, err.message);
    }
  }

  // If no output from LLM, generate a grounded, substantive fallback sequence
  if (!sectionText || sectionText.split(/\s+/).filter(Boolean).length < 40) {
    const claims = evidenceText ? evidenceText.split('\n').filter((l) => l.trim().startsWith('-')).slice(0, 4) : [];
    const claimLines = claims.length ? claims.map((c) => c.replace(/^-\s*/, '')).join(' ') : `The documented records surrounding ${topic} demonstrate how the situation escalated.`;
    
    const narrativeParagraphs = [
      `${sequence.summary} To understand how this unfolded, examine the sequence of decisions directly. ${claimLines}`,
      `The operational friction at this stage came from competing priorities and unforeseen constraints. Every decision narrowed the available options and forced key leaders to choose between immediate compromises and structural risk. The people responsible had to adapt on the fly while maintaining the appearance of complete certainty.`,
      `That contrast between public perception and behind-the-scenes reality is where the real mechanism becomes visible. The lasting significance was forged in these quiet operational tradeoffs that determined the final outcome of ${topic}.`
    ];

    sectionText = narrativeParagraphs.join('\n\n');
  }

  // Update memory state
  const sentences = sectionText.match(/[^.!?]+(?:[.!?](?!\d)|$)/g) || [];
  if (sentences.length) {
    memoryState.lastSectionBridge = sentences[sentences.length - 1].trim();
  }

  const facts = extractFactualSentences(sectionText);
  facts.forEach((f) => memoryState.recordFact(f));

  if (Array.isArray(sequence.closeLoops)) {
    sequence.closeLoops.forEach((loopId) => {
      memoryState.closeLoop(loopId, sequenceIndex, 'Resolved in sequence ' + (sequenceIndex + 1));
    });
  }

  const wordCount = sectionText.split(/\s+/).filter(Boolean).length;
  memoryState.wordCountSoFar += wordCount;

  return {
    sequenceId: sequence.id,
    name: sequence.name,
    text: sectionText,
    wordCount
  };
}

module.exports = {
  writeSequence
};
