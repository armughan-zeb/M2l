'use strict';

/**
 * Universal Script Generation Engine Test Suite.
 * 
 * Verifies all 5 layers and 8 modular agents from the Research Spec:
 * 1. Structure Schemas (Story Circle, Save the Cat, 3-Act Thesis Doc, Escalating List, Commentary)
 * 2. Memory State & Explicit Open Loops tracking
 * 3. 3-Part Hook Formula & 3 Variants generation
 * 4. Momentum Mechanics (Pattern interrupts ~90s, section bridges, retention tagging)
 * 5. Voice & De-Slop Agent (negative constraints, "not X but Y", buzzwords, recap removal)
 * 6. Critique Agent (1-5 rubric scoring)
 * 7. End-to-end Universal Engine orchestration
 */

const assert = require('assert');
const { classifyTopic } = require('../src/agents/classifier');
const { getStructureSchema, FORMATS, STORY_CIRCLE_NODES, SAVE_THE_CAT_BEATS } = require('../src/engine/structure/structureSchemas');
const { MemoryState } = require('../src/engine/state/memoryState');
const { generateOutline, buildDeterministicOutline } = require('../src/engine/agents/outlineAgent');
const { generateHook, scoreHookVariant, buildDeterministicHooks } = require('../src/engine/agents/hookAgent');
const { editRetention, buildSectionBridge, PATTERN_INTERRUPT_TEMPLATES } = require('../src/engine/agents/retentionAgent');
const { cleanVoiceRules, evaluateVoiceAuthenticity, deSlopScript } = require('../src/engine/agents/voiceAgent');
const { critiqueScript, evaluateRubricProgrammatic } = require('../src/engine/agents/critiqueAgent');
const { runUniversalEngine } = require('../src/engine/universalEngine');

async function testUniversalEngine() {
  console.log('--- Testing Universal Script Generation Engine ---');

  // ==========================================
  // 1. Structure Schemas & Taxonomy Classification
  // ==========================================
  console.log('1. Testing Structure Schemas & Format Selection...');
  
  const docClass = classifyTopic('The Untold Story of Apollo 11 Documentary');
  assert.equal(docClass.coreFormat, 'documentary');
  assert.ok(docClass.structureTemplate.includes('Documentary'));

  const listClass = classifyTopic('Top 10 Deadliest Animals in History');
  assert.equal(listClass.coreFormat, 'listicle');
  assert.equal(listClass.structureTemplate, 'Escalating Top 10 List');

  const storyClass = classifyTopic('The Rise and Fall of Nikola Tesla');
  assert.equal(storyClass.coreFormat, 'narrative');
  assert.equal(storyClass.structureTemplate, 'Dan Harmon Story Circle');

  const commentaryClass = classifyTopic('Apple vs Samsung: Which One is Truly Better');
  assert.equal(commentaryClass.coreFormat, 'commentary');

  // Verify Story Circle has 8 nodes and Save the Cat has 15 beats
  assert.equal(STORY_CIRCLE_NODES.length, 8);
  assert.equal(STORY_CIRCLE_NODES[0].id, 'comfort_zone');
  assert.equal(STORY_CIRCLE_NODES[7].id, 'changed');
  assert.equal(SAVE_THE_CAT_BEATS.length, 15);

  const docSchema = getStructureSchema('documentary');
  assert.equal(docSchema.actStructure.length, 3);
  assert.ok(docSchema.requiredFields.includes('thesisStatement'));
  assert.ok(docSchema.requiredFields.includes('pointOfAttack'));

  const listSchema = getStructureSchema('listicle');
  assert.ok(listSchema.rules.some((r) => r.includes('Escalate stakes towards #1')));

  // ==========================================
  // 2. Memory State & Open Loops Tracking
  // ==========================================
  console.log('2. Testing Memory State & Open Loops Tracking...');

  const mem = new MemoryState({ topic: 'Apollo 11', format: 'documentary', totalSequences: 4 });
  const loop1 = mem.openLoop({ loopId: 'loop_fuel_alarm', title: 'Why did the 1202 alarm sound?', openedAt: 0, mustCloseBy: 2 });
  assert.equal(loop1.status, 'open');
  assert.equal(mem.getActiveLoops().length, 1);

  // Check urgent loops
  assert.equal(mem.getUrgentLoops(1).length, 0);
  assert.equal(mem.getUrgentLoops(2).length, 1); // Must close by sequence 2

  // Close loop
  mem.closeLoop('loop_fuel_alarm', 2, 'Explained radar computer overload');
  assert.equal(mem.getActiveLoops().length, 0);
  assert.equal(mem.auditState().allLoopsResolved, true);

  // Record facts and check context generation
  mem.recordFact('The lunar module had only 25 seconds of fuel remaining.');
  const promptCtx = mem.toPromptContext(1);
  assert.ok(promptCtx.includes('25 seconds of fuel remaining'));

  // ==========================================
  // 3. Hook Layer (3-Part Formula & 3 Variants)
  // ==========================================
  console.log('3. Testing Hook Layer (3-Part Formula & Sentence Constraints)...');

  const hooks = buildDeterministicHooks('Apollo 11 Landing', 'Neil Armstrong made history.');
  assert.equal(hooks.variants.length, 3);
  
  const types = hooks.variants.map((v) => v.type);
  assert.ok(types.includes('result-first'));
  assert.ok(types.includes('bold-claim'));
  assert.ok(types.includes('relatable-problem'));

  // Check 3-part formula fields
  const hook = hooks.selectedHook;
  assert.ok(hook.validateClick);
  assert.ok(hook.raiseStakes);
  assert.ok(hook.openCuriosityLoop);

  // Check sentence length rule (<10-12 words per sentence)
  const sentences = hook.text.match(/[^.!?]+(?:[.!?]|$)/g) || [];
  sentences.forEach((s) => {
    const wordCount = s.trim().split(/\s+/).filter(Boolean).length;
    assert.ok(wordCount <= 14, `Hook sentence "${s.trim()}" exceeds word limit (${wordCount} words)`);
  });

  // Verify stock phrases are heavily penalized in scoring
  const badHookScore = scoreHookVariant({
    validateClick: 'Hey guys welcome back to the channel in today’s video',
    raiseStakes: 'This will be interesting',
    openCuriosityLoop: 'Let’s dive into it'
  });
  assert.ok(badHookScore < 60, `Bad hook should score poorly, got ${badHookScore}`);

  // ==========================================
  // 4. Momentum Mechanics & Retention Editor
  // ==========================================
  console.log('4. Testing Momentum Mechanics & Retention Annotations...');

  const mockSections = [
    { sequenceId: 'seq_1', name: 'The Setup', text: 'This is the opening scene. ' + 'The team examined the schematics carefully. '.repeat(15) },
    { sequenceId: 'seq_2', name: 'The Crisis', text: 'The pressure mounted rapidly. ' + 'Calculations showed severe discrepancies in the data. '.repeat(20), closeLoops: ['loop_fuel_alarm'] },
    { sequenceId: 'seq_3', name: 'The Climax', text: 'The resolution took shape. ' + 'In the end the problem was neutralized through precision. '.repeat(15) }
  ];

  const retentionOutput = editRetention({
    hook: hooks.selectedHook,
    sections: mockSections,
    memoryState: mem,
    duration: '10min'
  });

  assert.ok(retentionOutput.script.length > 500);
  assert.ok(retentionOutput.checklist.hasHook);
  assert.ok(retentionOutput.checklist.bridgeCount >= 1, 'Should have injected section bridges');
  assert.ok(retentionOutput.checklist.patternInterruptCount >= 1, 'Should have pattern interrupts');
  assert.ok(retentionOutput.checklist.retentionScore >= 70);

  // Verify retention tag annotations
  const blockTypes = retentionOutput.annotatedBlocks.map((b) => b.type);
  assert.ok(blockTypes.includes('hook'));
  assert.ok(blockTypes.includes('bridge'));
  assert.ok(blockTypes.includes('pattern-interrupt'));
  assert.ok(blockTypes.includes('payoff'));

  // ==========================================
  // 5. Voice Layer & De-Slop Agent
  // ==========================================
  console.log('5. Testing Voice Layer & De-Slop Agent...');

  const aiSlopSample = `In today's video, let's dive in and delve into this fascinating story. It is not just a game, it is a cultural revolution. The team utilized groundbreaking technology to leverage their resources, serving as a testament to their unwavering dedication. In conclusion, this changes everything.`;
  
  const cleaned = cleanVoiceRules(aiSlopSample);
  assert.ok(!cleaned.includes("In today's video"), 'Should remove stock video intro');
  assert.ok(!cleaned.includes("let's dive in"), 'Should remove dive in');
  assert.ok(!cleaned.includes("delve"), 'Should replace delve');
  assert.ok(!cleaned.includes("testament to"), 'Should replace testament to');
  assert.ok(!cleaned.includes("unwavering dedication"), 'Should replace unwavering dedication');
  assert.ok(!cleaned.includes("In conclusion"), 'Should remove recap footer');
  assert.ok(!cleaned.includes("It is not just a game, it is"), 'Should eliminate "it is not X, it is Y" construction');

  const voiceEval = evaluateVoiceAuthenticity(cleaned);
  assert.ok(voiceEval.score > 75, `Cleaned script should score high authenticity, got ${voiceEval.score}`);

  // ==========================================
  // 6. Critique & Rubric Scoring Agent
  // ==========================================
  console.log('6. Testing Critique & Rubric Scoring...');

  const critique = evaluateRubricProgrammatic({
    script: retentionOutput.script,
    topic: 'Apollo 11',
    retentionChecklist: retentionOutput.checklist,
    memoryAudit: mem.auditState()
  });

  assert.ok(critique.scores.coherence >= 3);
  assert.ok(critique.scores.pacing >= 3);
  assert.ok(critique.scores.voiceAuthenticity >= 3);
  assert.ok(critique.average >= 3.0);
  assert.equal(critique.passes, true);

  // ==========================================
  // 7. End-to-End Universal Engine Execution
  // ==========================================
  console.log('7. Testing End-to-End Universal Engine Execution (offline & fallback resilience)...');

  const engineResult = await runUniversalEngine({
    topic: 'Why Concorde Really Stopped Flying',
    duration: '5min',
    contentType: 'documentary',
    voice: 'conversational'
  });

  assert.ok(engineResult.script);
  assert.ok(engineResult.script.split(/\s+/).filter(Boolean).length >= 400);
  assert.ok(engineResult.meta.thesisStatement);
  assert.ok(engineResult.meta.hookVariants.length === 3);
  assert.ok(engineResult.meta.retentionChecklist.hasHook);
  assert.ok(engineResult.meta.rubricScores);
  assert.ok(engineResult.meta.critiquePasses);

  console.log('All Universal Script Generation Engine checks passed successfully!');
}

testUniversalEngine().catch((err) => {
  console.error('Universal Engine test failed:', err);
  process.exit(1);
});
