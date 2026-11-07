'use strict';

/**
 * World & Memory State Tracker.
 * 
 * Tracks explicit narrative state between sequence-level generation steps:
 * - Open Loops (questions/mysteries opened, tracked by ID, with must_close_by constraints)
 * - Established Facts (prevents LLM repetition and contradictions)
 * - Entity & Character States (current status, relationships, knowledge)
 * - Retention Milestones (next planned pattern interrupt, rehook, or payoff)
 */

class MemoryState {
  constructor({ topic = '', format = 'documentary', totalSequences = 4 } = {}) {
    this.topic = topic;
    this.format = format;
    this.totalSequences = totalSequences;
    this.currentSequenceIndex = 0;
    
    // Explicit data structures
    this.openLoops = []; // Array of { loopId, title, openedAt, mustCloseBy, prompt, status: 'open'|'closed', closedAt: null }
    this.establishedFacts = new Set(); // Set of concise facts already delivered
    this.entities = new Map(); // entityName -> { role, status, introducedAt }
    this.keyQuotes = [];
    this.lastSectionBridge = '';
    this.wordCountSoFar = 0;
  }

  /**
   * Register a new open curiosity loop.
   */
  openLoop({ loopId, title, openedAt, mustCloseBy, prompt = '' }) {
    const id = loopId || `loop_${this.openLoops.length + 1}`;
    const entry = {
      loopId: id,
      title: title || `Curiosity Loop #${this.openLoops.length + 1}`,
      openedAt: openedAt !== undefined ? openedAt : this.currentSequenceIndex,
      mustCloseBy: mustCloseBy !== undefined ? mustCloseBy : Math.max(this.currentSequenceIndex + 1, this.totalSequences - 1),
      prompt: prompt || title,
      status: 'open',
      closedAt: null,
      resolution: ''
    };
    this.openLoops.push(entry);
    return entry;
  }

  /**
   * Close an open loop when resolved in the script.
   */
  closeLoop(loopId, sequenceIndex, resolution = '') {
    const loop = this.openLoops.find((l) => l.loopId === loopId || l.title === loopId);
    if (loop) {
      loop.status = 'closed';
      loop.closedAt = sequenceIndex !== undefined ? sequenceIndex : this.currentSequenceIndex;
      loop.resolution = resolution;
      return true;
    }
    return false;
  }

  /**
   * Returns loops that are currently open.
   */
  getActiveLoops() {
    return this.openLoops.filter((l) => l.status === 'open');
  }

  /**
   * Returns loops that MUST close in or by the specified sequence.
   */
  getUrgentLoops(sequenceIndex) {
    const seq = sequenceIndex !== undefined ? sequenceIndex : this.currentSequenceIndex;
    return this.openLoops.filter((l) => l.status === 'open' && l.mustCloseBy <= seq);
  }

  /**
   * Record a verified fact as already established in the narration.
   */
  recordFact(fact) {
    if (!fact) return;
    const clean = String(fact).trim();
    if (clean.length > 10) {
      this.establishedFacts.add(clean);
    }
  }

  /**
   * Record an entity / character / organization state.
   */
  recordEntity(name, stateObj = {}) {
    if (!name) return;
    const existing = this.entities.get(name) || {};
    this.entities.set(name, {
      ...existing,
      ...stateObj,
      updatedAt: this.currentSequenceIndex
    });
  }

  /**
   * Builds the focused prompt context for the writer of the current section.
   * NOTE: We intentionally do NOT dump the entire previous transcript or full outline,
   * but rather the memory state and active obligations.
   */
  toPromptContext(sequenceIndex) {
    const seq = sequenceIndex !== undefined ? sequenceIndex : this.currentSequenceIndex;
    const activeLoops = this.getActiveLoops();
    const urgentLoops = this.getUrgentLoops(seq);
    const facts = [...this.establishedFacts].slice(-8); // Most recent 8 established facts

    const lines = [
      `--- MEMORY & RUNNING STATE (Sequence ${seq + 1} of ${this.totalSequences}) ---`
    ];

    if (facts.length) {
      lines.push('ALREADY ESTABLISHED FACTS (Do NOT repeat or contradict these):');
      facts.forEach((f) => lines.push(`- ${f}`));
    }

    if (activeLoops.length) {
      lines.push('\nACTIVE OPEN CURIOSITY LOOPS:');
      activeLoops.forEach((l) => {
        const isUrgent = l.mustCloseBy <= seq;
        lines.push(`- [${l.loopId}] "${l.title}" (Opened in seq ${l.openedAt + 1})${isUrgent ? ' **MUST CLOSE IN THIS SECTION**' : ' (keep tension alive, delay answer)'}`);
      });
    }

    if (this.lastSectionBridge) {
      lines.push(`\nLAST SECTION BRIDGE: "${this.lastSectionBridge}"`);
    }

    lines.push('----------------------------------------------------');
    return lines.join('\n');
  }

  /**
   * Final verification: check for unclosed loops.
   */
  auditState() {
    const unclosed = this.openLoops.filter((l) => l.status === 'open');
    return {
      totalLoops: this.openLoops.length,
      closedLoops: this.openLoops.filter((l) => l.status === 'closed').length,
      unclosedLoops: unclosed,
      allLoopsResolved: unclosed.length === 0
    };
  }
}

module.exports = {
  MemoryState
};
