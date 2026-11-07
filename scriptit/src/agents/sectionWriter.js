'use strict';
const crypto = require('crypto');
const { boundedCall } = require('../utils/execution');
const { systemMessage } = require('../prompts/scriptPrompts');

// Long narration is written beat by beat. Each request receives the actual
// completed narration before it, so chunking cannot make later beats forget
// the story. Checkpoints make that sequential work resumable after a failure.
async function writeSections({ topic, wordTarget, evidenceText, editorialContext = '', previousDraft = '', continuityReview = false, complete, signal, onProgress, onDraft, onCheckpoint, initialCheckpoint, model = '', requestTimeoutMs = 360000 }) {
  const send = complete;
  let requests = 0, providerAttempts = 0;
  const generations=[];
  complete = async payload => {
    let lastError;
    // An external-agent timeout means the connected manager did not answer
    // within its bounded inbox contract. Reissuing the same beat creates
    // duplicate request IDs and can multiply a single 8-minute wait into
    // hours. The saved section checkpoint is the retry mechanism: reconnect
    // the same session and resume that beat once.
    const externalAgent = String(model || '').trim().toLowerCase() === 'external-agent';
    const maxAttempts = 1; // The owning stage retries saved beats; never multiply provider retries here.
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      signal?.throwIfAborted();
      requests++;
      try {
        const result = await boundedCall(ownedSignal => send({ ...payload, signal: ownedSignal }), {
          signal,
          timeoutMs: requestTimeoutMs,
          label: 'Narration provider request'
        });
        providerAttempts += result?.attempts || 1;
        generations.push({id:result?.raw?.id || null,model:result?.modelUsed || model,inputTokens:result?.usage?.prompt_tokens ?? null,outputTokens:result?.usage?.completion_tokens ?? null,reportedCostUsd:result?.usage?.cost ?? null});
        return result;
      } catch (error) {
        lastError = error;
        providerAttempts += error?.attempts || 1;
        if (signal?.aborted) throw error;
        const msg = String(error?.message || '');
        const isRetryable = /timed out|deadline reached|fetch failed|econnreset|etimedout|socket hang up|network|500|502|503|504|429/i.test(msg) || error?.code === 'SCRIPTIT_TIMEOUT';
        if (externalAgent && error?.code === 'SCRIPTIT_TIMEOUT') error.retryable = false;
        if (!isRetryable || attempt >= maxAttempts) throw error;
        const backoffMs = Math.min(10000, attempt * 2000);
        await new Promise(r => setTimeout(r, backoffMs));
      }
    }
    throw lastError;
  };
  const sourceLines = new Map(String(evidenceText || '').split('\n').flatMap(line => {
    const match = line.match(/^(claim-[a-z0-9]+)\s+\|/i);
    return match ? [[match[1], line]] : [];
  }));
  const sourceIds = sourceLines.size ? new Set(sourceLines.keys()) : new Set(String(evidenceText || '').match(/claim-[a-z0-9]+/gi) || []);
  const contextHash = crypto.createHash('sha256').update(JSON.stringify({ promptVersion: 'prompt-led-v1', topic, wordTarget, evidenceText, editorialContext, model })).digest('hex');
  const normalizeId = id => String(id || '').trim().toLowerCase();
  const sourceIdByNorm = new Map([...sourceIds].map(id => [normalizeId(id), id]));
  const validPlan = value => Array.isArray(value?.beats) && value.beats.length === 5 && value.beats.every(beat => typeof beat.purpose === 'string' && beat.purpose.trim() && Array.isArray(beat.sourceIds) && beat.sourceIds.length && beat.sourceIds.every(id => sourceIds.has(id)));
  let plan;
  const matchingCheckpoint = initialCheckpoint?.version === 1 && initialCheckpoint.contextHash === contextHash;
  const resume = matchingCheckpoint && validPlan(initialCheckpoint.plan);
  let planAttempts = matchingCheckpoint ? Math.max(0, Math.min(3, Number(initialCheckpoint.planAttempts) || 0)) : 0;
  if (resume) plan = initialCheckpoint.plan;
  const promptWordTarget = wordTarget + (wordTarget >= 1000 ? 100 : Math.max(50, Math.round(wordTarget * 0.08)));
  const allowance = wordTarget >= 1000 ? 200 : Math.min(200, Math.round(wordTarget * 0.15));
  const wholeMinimum = Math.max(80, Math.min(Math.ceil(wordTarget * 0.85), wordTarget >= 1400 ? 1300 : wordTarget - allowance));
  const planRequest = { system: 'Plan an original, factual documentary using only the supplied sources. Return JSON {"beats":[{"purpose":"specific story development","sourceIds":["claim-..."]}]}. Use exactly five chronological or logical beats with distinct material. Do not plan unverified motives, conflict, comparisons or scenes. The first beat must establish the named subject; the last must conclude its documented story. Allocate enough real source detail for each beat to sustain its word budget; every source ID must exist.',
    user: `Subject: ${topic}. Total narration target: aim for ${promptWordTarget} words (requested ${wordTarget} words; minimum acceptable is ${wholeMinimum} words). Editorial/style guidance (never factual evidence): ${editorialContext}\nSources:\n${evidenceText}${previousDraft ? '\nPRIOR REJECTED DRAFT (structure only, never evidence):\n' + String(previousDraft).slice(0, 15000) + '\nRetain its supported developments in the appropriate beats and correct its omissions; the sources alone establish facts.' : ''}`, maxTokens: 2600,
    responseFormat: { type: 'json_schema', json_schema: { name: 'narration_beats', strict: true, schema: { type: 'object', additionalProperties: false, required: ['beats'], properties: { beats: { type: 'array', minItems: 5, maxItems: 5, items: { type: 'object', additionalProperties: false, required: ['purpose', 'sourceIds'], properties: { purpose: { type: 'string' }, sourceIds: { type: 'array', items: { type: 'string', enum: [...sourceIds] } } } } } } } } }
  };
  let rejectedPlan = '';
  for (let attempt = planAttempts; !validPlan(plan) && attempt < 3; attempt++) {
    signal?.throwIfAborted();
    planAttempts++;
    await onCheckpoint?.({ version: 1, contextHash, phase: 'planning', plan: null, planAttempts, sections: [], sectionAttempts: [] });
    const planned = await complete({ ...planRequest, user: planRequest.user + (attempt ? `\nPREVIOUS INVALID PLAN:\n${rejectedPlan.slice(0, 6000)}\nReturn exactly five beats and only the listed real source IDs. Repair the shape and references; never invent evidence.` : '') });
    rejectedPlan = String(planned?.content || '');
    try {
      const cleaned = rejectedPlan.replace(/```json|```/gi, '').trim();
      const match = cleaned.match(/\{[\s\S]*\}/);
      plan = JSON.parse(match ? match[0] : cleaned);
      if (Array.isArray(plan?.beats)) {
        for (const beat of plan.beats) {
          if (Array.isArray(beat?.sourceIds)) {
            beat.sourceIds = beat.sourceIds.map(id => {
              const direct = sourceIdByNorm.get(normalizeId(id));
              if (direct) return direct;
              const m = String(id || '').match(/claim-[a-z0-9]+/i);
              return m ? sourceIdByNorm.get(normalizeId(m[0])) : null;
            }).filter(Boolean);
          }
        }
      }
    } catch { plan = null; }
  }
  const totalValid = (plan?.beats || []).reduce((sum, b) => sum + (Array.isArray(b?.sourceIds) ? b.sourceIds.length : 0), 0);
  if (totalValid > 0 && Array.isArray(plan?.beats) && plan.beats.length === 5 && sourceIds.size > 0) {
    const allSourceList = [...sourceIds];
    plan.beats.forEach((beat, idx) => {
      if (!Array.isArray(beat.sourceIds) || beat.sourceIds.length === 0) {
        beat.sourceIds = [allSourceList[idx % allSourceList.length]];
      }
    });
  }
  if (!validPlan(plan)) throw new Error('Narration beat plan has missing or invalid source references after three bounded requests');
  const beats = plan.beats;
  const sections = new Array(beats.length);
  const sectionAttempts = new Array(beats.length).fill(0);
  const words = Math.ceil(promptWordTarget * 1.1 / beats.length);
  // Story beats need not have identical lengths. Keep a loose local floor;
  // the assembled narration's duration gate is the authoritative length check.
  // A beat is a pacing unit, not a standalone deliverable. Keep a generous
  // floor so brief evidence beats can contribute their supported detail while
  // the assembled narration's duration gate controls total runtime.
  const minimumBeatWords = Math.max(35, Math.ceil(wholeMinimum * 0.70 / beats.length));
  const maximumBeatWords = Math.floor(promptWordTarget * 1.35 / beats.length);
  const measure = value => String(value || '').replace(/<\/?evidence(?::[^>]+)?>/gi, '').split(/\s+/).filter(Boolean).length;
  const usable = result => !!result?.content?.trim() && !result.truncated && measure(result.content) >= minimumBeatWords;
  if (resume) for (let index = 0; index < beats.length; index++) {
    const saved = initialCheckpoint.sections?.[index];
    if (saved && typeof saved.content === 'string') sections[index] = { content: saved.content, truncated: saved.truncated === true, modelUsed: saved.modelUsed || model };
    sectionAttempts[index] = Math.max(0, Math.min(3, Number(initialCheckpoint.sectionAttempts?.[index]) || 0));
  }
  let continuity = resume && initialCheckpoint.continuity ? JSON.parse(JSON.stringify(initialCheckpoint.continuity)) : { repairs: 0 };
  const story = () => sections.map((section, index) => ({ beat: index + 1, purpose: beats[index].purpose, narration: section?.content || '' }));
  const priorContext = index => sections.slice(0, index).map((section, before) => section?.content ? `Beat ${before + 1}: ${section.content}` : `Beat ${before + 1} is being written concurrently; follow its planned purpose: ${beats[before].purpose}`).join('\n\n');
  let checkpointTail = Promise.resolve();
  const save = () => {
    signal?.throwIfAborted();
    const checkpoint = { version: 1, contextHash, plan, planAttempts, continuity: JSON.parse(JSON.stringify(continuity)), sectionAttempts: [...sectionAttempts], sections: sections.map(section => section ? { content: section.content, truncated: !!section.truncated, modelUsed: section.modelUsed || model } : null) };
    const content = sections.filter(Boolean).map(section => section.content.trim()).join('\n\n');
    checkpointTail = checkpointTail.then(async () => {
      signal?.throwIfAborted();
      await onCheckpoint?.(checkpoint);
      if (content) await onDraft?.(content);
    });
    return checkpointTail;
  };
  await save();
  for (let index = 0; index < beats.length; index++) {
    signal?.throwIfAborted();
    if (usable(sections[index])) { onProgress?.(index + 1, beats.length); continue; }
    const selected = beats[index].sourceIds.map(id => sourceLines.get(id)).filter(Boolean).join('\n');
    if (sourceLines.size && !selected) throw new Error('Narration beat has no source passages');
    const request = { system: systemMessage({ topic, voice: 'channel supplied', customStructure: editorialContext }) + '\n\nWrite original spoken documentary narration using only supplied source passages. Append <evidence:claim-...> marker after each factual sentence; no closing tags. Faithful paraphrases allowed; no invented motives, reactions, or filler. Natural, varied sentences in two or three paragraphs. Title is an editorial promise, never factual evidence.',
        user: `Subject: ${topic}\nEditorial/style guidance (never factual evidence): ${editorialContext}\nWhole story outline: ${JSON.stringify(beats)}\nWrite ONLY beat ${index + 1}: ${beats[index].purpose}. ${index === 0 ? 'Open with specific source-supported detail or question validating title and subject immediately. State concise promise about documented answer. Create interest through chronology and consequences; do not invent mystery or filler.' : 'Continue directly from actual preceding narration and move story forward without recapping.'} ${index === beats.length - 1 ? 'Finish with a clear, memorable conclusion that resolves opening promise with evidence.' : 'End with natural informative transition; do not manufacture cliffhangers.'}\nLength: ${words}–${Math.ceil(words * 1.15)} narration words, excluding markers. Thoroughly explain source material in depth to meet budget. No filler or repetitive text.\nSOURCE PASSAGES:\n${selected}`, maxTokens: Math.round(words * 2.5 + 400)
    };
    let result = sections[index];
    // A correction that is still short must not silently flow into the
    // combined script. Permit two measured corrections, then fail with a
    // specific bounded error rather than enter an open-ended rewrite loop.
    while (!usable(result) && sectionAttempts[index] < 3) {
      signal?.throwIfAborted();
      const previous = result?.content || '';
      const measured = measure(previous);
      sectionAttempts[index]++;
      await save();
      result = await complete({ ...request, user: request.user + '\n\nACTUAL PRECEDING NARRATION (continuity context, never a new factual source):\n' + priorContext(index) + '\nContinue its tone, chronology, terminology and story threads seamlessly. Build directly upon the preceding facts without re-explaining material already established. Return only the finished narration.' + (previous ? `\n\nCURRENT BEAT DRAFT:\n${previous}\nMeasured narration: ${measured} words. Required range for this beat: ${minimumBeatWords}–${maximumBeatWords} words. ${measured < minimumBeatWords ? `Expand the current beat with distinct, relevant detail explicitly supported by its source passages. Add at least ${minimumBeatWords - measured} narration words.` : 'Compress repetition while preserving its source-supported story; do not cut an unfinished sentence.'} Preserve supported details; remove unsupported ones. Return the full corrected beat, not notes.` : '') });
      if (!result?.content?.trim()) throw new Error('Narration beat correction returned no text');
      sections[index] = result;
      await save();
    }
    if (!usable(result)) {
      const reason = measure(result?.content) < minimumBeatWords ? 'remained under length' : 'failed its length/completion gate';
      throw new Error(`ScriptIT quality rejection: narration beat ${index + 1} ${reason} after two bounded corrections (${measure(result?.content)} words; required ${minimumBeatWords}–${maximumBeatWords})`);
    }
    onProgress?.(sections.filter(usable).length, beats.length);
  }
  const totalWords = () => sections.reduce((count, section) => count + measure(section.content), 0);
  while (totalWords() < wholeMinimum) {
    const index = sections.map((section, index) => ({ index, words: measure(section.content) }))
      .filter(item => sectionAttempts[item.index] < 3 && item.words < maximumBeatWords)
      .sort((a, b) => a.words - b.words)[0]?.index;
    if (index === undefined) throw new Error(`ScriptIT quality rejection: complete narration remains under length (${totalWords()}/${wholeMinimum} words) after bounded beat corrections`);
    const missing = wholeMinimum - totalWords();
    sectionAttempts[index]++;
    await save();
    sections[index] = await complete({ system: 'Expand ONLY the selected documentary beat using distinct detail explicitly supported by its source passages. Preserve the story, voice, chronology and supported existing narration. Do not invent facts, repeat other beats or append filler. Return the full expanded beat with real evidence markers.',
      user: `Subject: ${topic}\nStyle: ${editorialContext}\nWrite ONLY beat ${index + 1}: ${beats[index].purpose}.\nThe complete story is ${missing} words short of its unchanged ${wholeMinimum}-word minimum. This beat currently has ${measure(sections[index].content)} words. Aim for ${Math.min(maximumBeatWords, measure(sections[index].content) + missing + 30)} words, maximum ${maximumBeatWords}.\nCOMPLETE CURRENT STORY:\n${JSON.stringify(story())}\nSOURCE PASSAGES:\n${beats[index].sourceIds.map(id => sourceLines.get(id)).filter(Boolean).join('\n')}`,
      maxTokens: Math.round(words * 2.5 + 400) });
    await save();
    if (!usable(sections[index])) throw new Error(`ScriptIT quality rejection: duration repair failed the word/completion gate for beat ${index + 1}`);
  }
  if (continuityReview) {
    const validReview = review => Array.isArray(review?.reviews) && review.reviews.length === 5
      && new Set(review.reviews.map(row => row?.beat)).size === 5
      && review.reviews.every(row => Number.isInteger(row?.beat) && row.beat >= 1 && row.beat <= 5 && typeof row.passes === 'boolean'
        && Array.isArray(row.issues) && row.issues.every(issue => typeof issue === 'string' && issue.trim()) && row.passes === (row.issues.length === 0));
    while (true) {
      signal?.throwIfAborted();
      const scriptHash = crypto.createHash('sha256').update(sections.map(section => section.content.trim()).join('\n\n')).digest('hex');
      if (continuity.scriptHash !== scriptHash || continuity.policyVersion !== 2) continuity = {
        repairs: continuity.policyVersion === 2 ? continuity.repairs || 0 : 0,
        priorPolicyRepairs: continuity.priorPolicyRepairs || (continuity.policyVersion !== 2 ? continuity.repairs || 0 : 0),
        repairAttempts: continuity.repairAttempts || new Array(5).fill(0),
        policyVersion: 2, scriptHash, attempts: 0
      };
      while (!validReview(continuity.review) && continuity.attempts < 3) {
        continuity.attempts++;
        await save();
        const response = await complete({ system: 'Review the entire assembled documentary as one story, not five independent essays. Check each beat for repeated introductions or facts, abrupt transitions, inconsistent terms or chronology, forgotten story threads, and a conclusion that resolves the opening promise. Assign repeated introductions and facts ONLY to the later beat that repeats them; preserve the earlier anchor. Flag the smallest set of beats that actually need editing. Do not reject a true fact, an omitted example, ordinary historical detail or a merely preferable transition. Do not infer listener confusion from a clear ordinary sentence. Do not demand stylistic perfection or invent missing facts. Flag only concrete defects that materially harm listener comprehension. Return JSON {"reviews":[{"beat":1,"passes":true,"issues":[]},...]} covering exactly beats 1 through 5. A failing beat must include actionable, specific issues; a passing beat has no issues. Factual verification is performed separately and is not a reason to fail this continuity review.',
          user: `Subject: ${topic}\nStyle: ${editorialContext}\nStory outline: ${JSON.stringify(beats)}\nCOMPLETE ASSEMBLED STORY:\n${JSON.stringify(story())}\n${continuity.attempts > 1 ? 'The prior response was missing or malformed. Return complete, consistent coverage for all five beats.' : ''}`,
          maxTokens: 2200, responseFormat: { type: 'json_schema', json_schema: { name: 'story_continuity', strict: true, schema: { type: 'object', additionalProperties: false, required: ['reviews'], properties: { reviews: { type: 'array', minItems: 5, maxItems: 5, items: { type: 'object', additionalProperties: false, required: ['beat', 'passes', 'issues'], properties: { beat: { type: 'integer', minimum: 1, maximum: 5 }, passes: { type: 'boolean' }, issues: { type: 'array', items: { type: 'string' } } } } } } } } } });
        try {
          const raw = String(response?.content || '').replace(/```json|```/gi, '').trim();
          const match = raw.match(/\{[\s\S]*\}/);
          continuity.review = JSON.parse(match ? match[0] : raw);
        } catch { continuity.review = null; }
        await save();
      }
      if (!validReview(continuity.review)) throw new Error('ScriptIT quality rejection: whole-story continuity review has incomplete or contradictory coverage');
      const rejected = continuity.review.reviews.filter(row => !row.passes).sort((a, b) => a.beat - b.beat);
      if (!rejected.length) break;
      if (continuity.repairs >= 2) throw new Error('ScriptIT quality rejection: whole-story continuity remains rejected after bounded corrections');
      continuity.repairs++;
      await save();
      for (const finding of rejected) {
        const index = finding.beat - 1;
        // Draft length recovery and whole-story editing are distinct bounded
        // phases. A previously corrected opening can still need a transition
        // edit; neither phase may erase the other phase's attempt history.
        if (continuity.repairAttempts[index] >= 3) throw new Error(`ScriptIT quality rejection: continuity repair budget exhausted for beat ${finding.beat}`);
        continuity.repairAttempts[index]++;
        await save();
        sections[index] = await complete({ system: 'Repair only the identified narration beat within the complete story. Preserve its supported facts, voice and depth. Resolve the concrete continuity findings without repeating other beats, restarting the introduction or inventing facts. Return only the full corrected beat as spoken narration, with real evidence markers.',
          user: `Subject: ${topic}\nStyle: ${editorialContext}\nWrite ONLY beat ${finding.beat}: ${beats[index].purpose}.\nIssues: ${JSON.stringify(finding.issues)}\nRequired length: ${words}–${Math.ceil(words * 1.1)} narration words. Minimum ${minimumBeatWords}, maximum ${maximumBeatWords}.\nCOMPLETE CURRENT STORY:\n${JSON.stringify(story())}\nSOURCE PASSAGES FOR THIS BEAT:\n${beats[index].sourceIds.map(id => sourceLines.get(id)).filter(Boolean).join('\n')}`,
          maxTokens: Math.round(words * 2.5 + 400) });
        await save();
        if (!usable(sections[index])) throw new Error(`ScriptIT quality rejection: continuity correction failed the word/completion gate for beat ${finding.beat}`);
      }
    }
  }
  return { content: sections.map(result => result.content.trim()).join('\n\n'), modelUsed: sections[0].modelUsed,
    sectionPlan: beats, continuity: continuityReview ? continuity : undefined, usage: { requests, providerAttempts, generations, inputTokens:generations.reduce((s,g)=>s+(g.inputTokens||0),0), outputTokens:generations.reduce((s,g)=>s+(g.outputTokens||0),0), reportedCostUsd:generations.reduce((s,g)=>s+(g.reportedCostUsd||0),0), unpricedRequests:generations.filter(g=>g.reportedCostUsd==null).length } };
}

module.exports = { writeSections };
