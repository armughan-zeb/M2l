'use strict';
const crypto = require('crypto');
const { boundedCall } = require('../utils/execution');

function ledgerIds(evidenceText) {
  const rows = [...String(evidenceText || '').matchAll(/^(claim-[a-z0-9]+)\s+\|/gmi)].map(match => match[1]);
  return new Set(rows.length ? rows : String(evidenceText || '').match(/claim-[a-z0-9]+/gi) || []);
}

function validateReview(response, batch, sourceIds) {
  if (!response || !Array.isArray(response.reviews)) {
    return Array.isArray(response?.issues) && response.issues.length
      ? { invalid: false, issues: response.issues.map(String), reviews: [] }
      : { invalid: true, problems: ['Missing reviews array'] };
  }
  const problems = [], seen = new Set(), reviews = [];
  if (response.reviews.length !== batch.length) problems.push('Review must include exactly ' + batch.length + ' sentence IDs');
  for (const row of response.reviews) {
    if (!row || typeof row !== 'object') { problems.push('Invalid review row'); continue; }
    const citations = row.sourceIds === undefined && ['neutral', 'unsupported'].includes(row.verdict) ? [] : row.sourceIds;
    if (!batch.some(unit => unit.id === row.id) || seen.has(row.id)) problems.push('Invalid or duplicated sentence ID');
    if (!['supported', 'neutral', 'unsupported'].includes(row.verdict)) problems.push('Invalid verdict');
    if (typeof row.reason !== 'string' || !row.reason.trim()) problems.push('Missing reason');
    if (!Array.isArray(citations) || citations.some(id => !sourceIds.has(id)) || (row.verdict === 'supported' && !citations.length)) problems.push('Missing or invented evidence ID');
    seen.add(row.id);
    reviews.push({ id: row.id, verdict: row.verdict, sourceIds: Array.isArray(citations) ? citations : [], reason: row.reason });
  }
  const issues = Array.isArray(response.issues) ? response.issues.filter(Boolean).map(String) : [];
  // A semantic rejection is retained; format recovery cannot overwrite it.
  if (response.passes === false && !reviews.some(row => row.verdict === 'unsupported') && !issues.length) problems.push('Contradictory failure without findings');
  return { invalid: problems.length > 0, problems: [...new Set(problems)], reviews, issues };
}

function sentenceUnits(script) {
  // Decimal points and abbreviated initials must not split the claim being reviewed.
  const protectedText = String(script || '').replace(/\.(?=\d)|(?<=\b[A-Z])\.(?=\s*[A-Z]\.)/g, '\u0000');
  return (protectedText.match(/[^.!?]+(?:[.!?]+|$)/g) || [])
    .map(text => text.replace(/\u0000/g, '.').trim()).filter(Boolean)
    .map((text, index) => ({ id: index + 1, text }));
}

async function auditNarration({ script, evidenceText, topic, complete, signal, initialCheckpoint, onCheckpoint, reviewKey = '', requestTimeoutMs = 90000 }) {
  const units = sentenceUnits(script);
  if (!units.length) return null;
  const sourceIds = ledgerIds(evidenceText);
  const batches = [];
  for (let offset = 0; offset < units.length; offset += 16) batches.push(units.slice(offset, offset + 16));
  const reports = new Array(batches.length);
  const attempts = new Array(batches.length).fill(0);
  const contextHash = crypto.createHash('sha256').update(JSON.stringify({ script, evidenceText, topic, reviewKey })).digest('hex');
  if (initialCheckpoint?.version === 1 && initialCheckpoint.contextHash === contextHash) {
    for (let index = 0; index < batches.length; index++) {
      const saved = initialCheckpoint.reports?.[index];
      if (saved && !saved.invalid) {
        const verdict = validateReview(saved, batches[index], sourceIds);
        if (!verdict.invalid) reports[index] = verdict;
      }
      attempts[index] = Math.max(0, Math.min(3, Number(initialCheckpoint.attempts?.[index]) || 0));
    }
  }
  let checkpointTail = Promise.resolve();
  const save = () => {
    signal?.throwIfAborted();
    const snapshot = JSON.parse(JSON.stringify({ version: 1, contextHash, reports, attempts }));
    checkpointTail = checkpointTail.then(() => { signal?.throwIfAborted(); return onCheckpoint?.(snapshot); });
    return checkpointTail;
  };
  let cursor = 0;
  let failure;
  async function worker() {
    try { while (!failure && cursor < batches.length) {
      signal?.throwIfAborted();
      const index = cursor++;
      if (reports[index] && !reports[index].invalid) continue;
      const batch = batches[index];
      const request = {
        system: `Review every numbered narration sentence about ${topic} against the supplied source ledger. Treat it as a closed world. A faithful paraphrase is supported; do not demand multiple sources for a fact stated in one reliable source. Check EACH subclaim, including comparisons, causation, motives, handling characteristics, dates, people versus teams, and popularity. Marketing language does not establish objective superiority. A sentence with any unsupported subclaim is unsupported. Neutral means a question or a transition making no factual assertion. Never call an assertion neutral to avoid reviewing it. Review only the supplied sentence IDs. Return JSON: {"reviews":[{"id":1,"verdict":"supported|neutral|unsupported","sourceIds":["claim-..."],"reason":"specific support or missing subclaim"}]}. Include every sentence ID exactly once; supported verdicts require real ledger source IDs. Do not invent quotations or facts.`,
        user: `EVIDENCE LEDGER:\n${evidenceText}\n\nNUMBERED SENTENCES:\n${JSON.stringify(batch)}`,
      };
      let verdict = { invalid: true, problems: ['No complete review was recorded'] };
      while (verdict.invalid && attempts[index] < 3) {
        signal?.throwIfAborted();
        const feedback = attempts[index] ? `FORMAT REPAIR: ${verdict.problems.join('; ')}. Include exactly these IDs: ${batch.map(unit => unit.id).join(', ')}. Use real source IDs and retain any unsupported findings.\n\n` : '';
        attempts[index]++;
        await save();
        const response = await boundedCall(ownedSignal => complete({ ...request, user: feedback + request.user, signal: ownedSignal }), { signal, timeoutMs: requestTimeoutMs, label: 'Factual review request' });
        verdict = validateReview(response, batch, sourceIds);
        reports[index] = verdict;
        await save();
      }
      reports[index] = verdict;
    } } catch (error) { failure = error; throw error; }
  }
  // Two bounded provider requests overlap; no unbounded request fan-out.
  await Promise.allSettled([worker(), worker()]);
  if (failure) throw failure;
  if (reports.some(report => !report || report.invalid)) return null;
  const reviews = reports.flatMap(report => report.reviews).sort((a, b) => a.id - b.id);
  const findings = reviews.filter(row => row.verdict === 'unsupported')
    .map(row => ({ ...row, sentence: units.find(unit => unit.id === row.id).text }));
  const issues = [...reports.flatMap(report => report.issues || []), ...findings.map(row => `Unsupported sentence: "${row.sentence}" — ${row.reason}`)];
  return { passes: !issues.length && reviews.length === units.length, issues, findings, reviews,
    version: 2, checkedSentences: reviews.length, scriptHash: crypto.createHash('sha256').update(script).digest('hex') };
}

async function correctFindings({ script, findings, evidenceText, wordTarget, complete, signal, requestTimeoutMs = 90000 }) {
  signal?.throwIfAborted();
  if (!findings?.length) throw new Error('No actionable factual findings to correct');
  const response = await boundedCall(ownedSignal => complete({
    system: 'Correct ONLY the quoted unsupported sentences. Preserve supported subclaims and remove unsupported subclaims. Do not add new motives, scenes, numbers or events. Use only the supplied source passages. A neutral bridge or an empty replacement is allowed when no fact is supported. Return JSON {"replacements":[{"id":1,"oldSentence":"exact quoted sentence","newSentence":"corrected sentence"}]}. Include each requested ID exactly once. Do not rewrite other parts of the draft.',
    user: `COMPLETE CURRENT NARRATION (preserve its chronology, voice, pronouns and story threads):\n${JSON.stringify(sentenceUnits(script))}\nMeasured narration: ${script.split(/\s+/).filter(Boolean).length} words.${wordTarget ? ` Target: ${wordTarget} words. Preserve supported depth; avoid compressing unchanged narration.` : ''}\n\nFINDINGS:\n${JSON.stringify(findings)}\n\nSOURCE LEDGER:\n${evidenceText}`,
    responseFormat: { type: 'json_schema', json_schema: { name: 'narration_corrections', strict: true, schema: { type: 'object', additionalProperties: false, required: ['replacements'], properties: { replacements: { type: 'array', minItems: findings.length, maxItems: findings.length, items: { type: 'object', additionalProperties: false, required: ['id', 'oldSentence', 'newSentence'], properties: { id: { type: 'integer', enum: findings.map(finding => finding.id) }, oldSentence: { type: 'string' }, newSentence: { type: 'string' } } } } } } } },
    signal: ownedSignal,
  }), { signal, timeoutMs: requestTimeoutMs, label: 'Factual correction request' });
  if (!Array.isArray(response?.replacements) || response.replacements.length !== findings.length) throw new Error('Incomplete factual corrections');
  const seen = new Set();
  const spans = new Map();
  let offset = 0;
  for (const unit of sentenceUnits(script)) {
    const start = script.indexOf(unit.text, offset);
    if (start < 0) throw new Error('Factual correction does not match the reviewed draft');
    spans.set(unit.id, { text: unit.text, start, end: start + unit.text.length });
    offset = start + unit.text.length;
  }
  const changes = [];
  for (const patch of response.replacements) {
    const finding = findings.find(finding => finding.id === patch.id);
    const span = spans.get(patch.id);
    if (!finding || seen.has(patch.id) || patch.oldSentence !== finding.sentence || span?.text !== patch.oldSentence || typeof patch.newSentence !== 'string') throw new Error('Factual correction does not match the reviewed draft');
    seen.add(patch.id);
    changes.push({ ...span, replacement: patch.newSentence.trim() });
  }
  let corrected = script;
  for (const change of changes.sort((a, b) => b.start - a.start)) corrected = corrected.slice(0, change.start) + change.replacement + corrected.slice(change.end);
  return corrected;
}

async function auditConsistency({ script, evidenceText, topic, complete, signal, requestTimeoutMs = 90000 }) {
  signal?.throwIfAborted();
  const units = sentenceUnits(script), sourceIds = ledgerIds(evidenceText);
  const response = await boundedCall(ownedSignal => complete({
    system: `Independently review the COMPLETE story about ${topic} for factual errors and contradictions that sentence-by-sentence source matching misses. Compare claims across all paragraphs and sources. Prioritize contemporaneous records for historical dates and the responsible governing body's records for competition results. Manufacturer/dealer marketing may misattribute a team's championship to its owner or claim superiority without evidence; reject that even when a marketing page says it. Resolve conflicting dates from original records. Flag invented motives, reactions and technical claims, and repeated or contradictory event timelines. Also flag concrete continuity defects introduced by sentence corrections: repeated openings, abrupt logical jumps, inconsistent terminology or story threads lost before the conclusion. Do not demand subjective stylistic perfection. Return JSON with checkedSentences equal to the exact number of supplied sentences and findings [{id,reason,sourceIds}]. Cite actual ledger IDs where a stronger source disproves the sentence. No findings means the complete story passed this independent review; do not merely repeat the drafting model's verdict.`,
    user: `A narrower but true claim is not an error: naming two actual achievements does not claim they were the only achievements. Distinguish explicit falsehoods from omissions. Do not reject a correct sentence merely because a different source contains a false claim. Cite the exact unsupported subclaim in each finding.\nSOURCE LEDGER:\n${evidenceText}\n\nCOMPLETE NUMBERED STORY (${units.length} sentences):\n${JSON.stringify(units)}`,
    signal: ownedSignal,
  }), { signal, timeoutMs: requestTimeoutMs, label: 'Story consistency review' });
  if (!response || response.checkedSentences !== units.length || !Array.isArray(response.findings)) return null;
  const seen = new Set(), findings = [];
  for (const row of response.findings) {
    const unit = units.find(unit => unit.id === row.id);
    if (!unit || seen.has(row.id) || typeof row.reason !== 'string' || !row.reason.trim() || !Array.isArray(row.sourceIds) || row.sourceIds.some(id => !sourceIds.has(id))) return null;
    seen.add(row.id); findings.push({ ...row, sentence: unit.text, verdict: 'unsupported' });
  }
  if (response.passes === false && !findings.length) return null;
  return { passes: findings.length === 0, findings, checkedSentences: units.length,
    issues: findings.map(row => `Unsupported sentence: "${row.sentence}" — ${row.reason}`),
    scriptHash: crypto.createHash('sha256').update(script).digest('hex') };
}

module.exports = { auditNarration, auditConsistency, sentenceUnits, correctFindings };
