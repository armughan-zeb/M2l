'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { ResearchStore } = require('../src/research/store');
const { runProvider } = require('../src/research/resilience');
const { mergeResearch, runResearchJob } = require('../src/research/pipeline');
const { validateScriptForPublish } = require('../src/research/publishGuard');
const { buildEvidenceLedger, relevanceScore } = require('../src/agents/evidence');
const { buildBrief } = require('../src/agents/compressor');
const { buildResearchPlan, buildCoverageReport } = require('../src/research/questionPlanner');
const { normalizeTopic, researchSubject } = require('../src/research/topicNormalizer');
const { officialSourceSeeds } = require('../src/agents/researcher');
const { detectNiche } = require('../src/agents/nicheDetector');
const { generateScript, isReusableResearchJob } = require('../src/agents/scriptWriter');
const { scopeResearch } = require('../src/agents/scriptWriter');
const { evaluateDraft, sanitizeUnsupportedSentences, auditDraft } = require('../src/agents/scriptWriter');
const { parseYandexResults } = require('../src/providers/yandex');

function source(title, text, url, sourceName) {
  return { title, articleText: text, url, source: sourceName, date: '2026-08-20', articleDate: '2026-08-20', hasArticleBody: true };
}

async function main() {
    const { writeSections } = require('../src/agents/sectionWriter');
    const sectionAttempts = new Map();
    const sectionSources = ['claim-a | documented source passage A', 'claim-b | documented source passage B'];
    const sectionResult = await writeSections({
      topic: 'A documented historical subject',
      wordTarget: 1500,
      evidenceText: sectionSources.join('\n'),
      complete: async ({ user }) => {
        if (user.startsWith('Subject: A documented historical subject. Total narration target:')) {
          return { content: JSON.stringify({ beats: Array.from({ length: 5 }, (_, i) => ({ purpose: `Distinct documented beat ${i + 1}`, sourceIds: [i % 2 ? 'claim-b' : 'claim-a'] })) }) };
        }
        const match = user.match(/Write ONLY beat (\d+)/);
        const beat = Number(match?.[1] || 0);
        const count = (sectionAttempts.get(beat) || 0) + 1;
        sectionAttempts.set(beat, count);
        const words = beat === 1 ? [180, 220, 255][Math.min(count - 1, 2)] : 255;
        return { content: `${Array(words).fill('documented').join(' ')} <evidence:claim-a>` };
      }
    });
    assert.ok(sectionResult.content.split(/\s+/).filter(Boolean).length >= 1275, 'bounded beat corrections must reach the existing 85% narration gate');
    assert.equal(sectionAttempts.get(1), 3, 'a beat that stays short after its first correction receives exactly one final bounded correction');
    const shortSectionAttempts = new Map();
    await assert.rejects(writeSections({
      topic: 'A documented historical subject',
      wordTarget: 1500,
      evidenceText: sectionSources.join('\n'),
      complete: async ({ user }) => {
        if (user.startsWith('Subject: A documented historical subject. Total narration target:')) {
          return { content: JSON.stringify({ beats: Array.from({ length: 5 }, (_, i) => ({ purpose: `Distinct documented beat ${i + 1}`, sourceIds: ['claim-a'] })) }) };
        }
        const beat = Number(user.match(/Write ONLY beat (\d+)/)?.[1] || 0);
        shortSectionAttempts.set(beat, (shortSectionAttempts.get(beat) || 0) + 1);
        return { content: `${Array(120).fill('documented').join(' ')} <evidence:claim-a>` };
      }
    }), /remained under length after two bounded corrections/, 'a persistently short beat must fail before factual audit instead of silently passing downstream');
    assert.ok([...shortSectionAttempts.values()].every(count => count <= 3), 'short beat recovery must stay bounded');

  const dbPath = path.join(os.tmpdir(), `scriptit-production-${Date.now()}.sqlite`);
  const store = new ResearchStore({ dbPath });
  try {
    await store.init();
    assert.equal(await store.integrityCheck(), true, 'SQLite integrity check should pass');

    const first = await store.createJob({ topic: 'Apollo mission', idempotencyKey: 'apollo-test' });
    const duplicate = await store.createJob({ topic: 'Apollo mission', idempotencyKey: 'apollo-test' });
    assert.equal(first.id, duplicate.id, 'duplicate jobs should be idempotent');
    await store.checkpointJob(first.id, { status: 'fast_ready', phase: 'fast_ready', progress: 35, result: { ready: true } });
    const loaded = await store.getJob(first.id);
    assert.equal(loaded.status, 'fast_ready');
    assert.equal(loaded.result.ready, true);
    assert.equal(isReusableResearchJob(loaded), false, 'jobs without a research bundle must not be reused');

    const partialJob = await store.createJob({ topic: 'Partial cache guard', idempotencyKey: 'partial-cache-guard' });
    await store.checkpointJob(partialJob.id, {
      status: 'partial',
      phase: 'context_ready',
      result: { research: { query: 'Partial cache guard' }, brief: { query: 'Partial cache guard' }, evidence: { sufficient: false } },
    });
    assert.equal(isReusableResearchJob(await store.getJob(partialJob.id)), false, 'partial jobs must never become cache hits');

    const research = {
      query: 'Apollo mission',
      gatheredAt: new Date().toISOString(),
      rss: [source('NASA archive', 'NASA records the Apollo mission timeline and landing sequence in detail.', 'https://www.nasa.gov/apollo/archive', 'NASA')],
      bing: [source('Mission history', 'A detailed history explains the Apollo mission engineering decisions and flight plan.', 'https://example.org/apollo-history', 'Example Journal')],
      duckduckgo: [source('Mission analysis', 'A separate analysis covers the Apollo mission consequences and key decisions.', 'https://example.net/apollo-analysis', 'Example Review')],
    };
    const evidence = buildEvidenceLedger(research);
    const citationEvidence = { claims: [{ id: 'claim-car', text: 'The Mini uses 80 percent of its floorpan for passengers.' }, { id: 'claim-race', text: 'Jack Brabham won the 1959 championship in a Cooper.' }] };
    const citationDraft = 'The Mini uses 80 percent of its floorpan for passengers. <evidence:claim-car> Jack Brabham won the 1959 championship in a Cooper. <evidence:claim-race>';
    const tidyNarration = evaluateDraft("Mini's price was $1,295 . It used 80% of its floorpan .", { topic: 'Mini', wordTarget: 14, previousScripts: [], evidence: citationEvidence }).script;
    assert.equal(tidyNarration, "Mini's price was $1,295. It used 80% of its floorpan.", 'citation cleanup must repair punctuation while preserving apostrophes, currency and percentages');
    assert.deepEqual(evaluateDraft(citationDraft, { topic: 'Mini Cooper', wordTarget: 30, previousScripts: [], evidence: citationEvidence }).markerIssues, [], 'a marker after punctuation must support its preceding sentence');
    const keptCitationDraft = sanitizeUnsupportedSentences(citationDraft, citationEvidence);
    assert.ok(keptCitationDraft.includes('floorpan') && keptCitationDraft.includes('Brabham'), 'valid sentences must survive evidence cleanup');
    const wrongCitationDraft = 'Jack Brabham won the 1959 championship in a Cooper. <evidence:claim-car>';
    assert.ok(evaluateDraft(wrongCitationDraft, { topic: 'Mini Cooper', wordTarget: 30, previousScripts: [], evidence: citationEvidence }).markerIssues.length > 0, 'wrong citations must still fail');
    const { repairEvidenceMarkers } = require('../src/agents/scriptWriter');
    assert.ok(repairEvidenceMarkers(wrongCitationDraft, citationEvidence).includes('<evidence:claim-race>'), 'a misassigned marker can be repaired without rewriting valid narration');
    const inventedSentence = 'Jack Brabham won the 1959 championship by sabotaging every rival car. <evidence:claim-race>';
    assert.ok(repairEvidenceMarkers(inventedSentence, citationEvidence).includes('sabotaging'), 'marker repair cannot silently edit an unsupported claim; semantic review must reject it');
    const contradictoryAudit = await auditDraft({ topic: 'Mini Cooper', script: citationDraft, evidenceText: 'fixture', chatCompletionFn: async request => {
      assert.equal(request.responseFormat.type, 'json_schema');
      assert.equal(request.reasoning, undefined, 'optional unsupported reasoning must not exclude schema-capable endpoints');
      return { content: '{"passes":true,"issues":["Unsupported motive"]}' };
    } });
    assert.equal(contradictoryAudit.passes, false, 'a success flag with unresolved factual findings cannot admit narration');
    assert.equal(await auditDraft({ topic: 'Mini Cooper', script: citationDraft, evidenceText: 'fixture', chatCompletionFn: async () => ({ content: '{"passes":true}' }) }), null, 'malformed audits must fail closed');
    const { auditNarration, sentenceUnits } = require('../src/agents/factualAudit');
    const auditScript = Array.from({ length: 19 }, (_, i) => `Mini production detail ${i + 1} is documented.`).join(' ');
    let activeReviews = 0, peakReviews = 0;
    const reviewed = await auditNarration({ script: auditScript, evidenceText: 'claim-car | Mini production details', complete: async ({ user }) => {
      activeReviews++; peakReviews = Math.max(peakReviews, activeReviews);
      await new Promise(resolve => setTimeout(resolve, 5));
      const batch = JSON.parse(user.split('NUMBERED SENTENCES:\n')[1]);
      activeReviews--;
      return { reviews: batch.map(unit => ({ id: unit.id, verdict: unit.id === 17 ? 'unsupported' : 'supported', sourceIds: ['claim-car'], reason: unit.id === 17 ? 'Invented production number' : 'Direct source support' })) };
    } });
    assert.equal(reviewed.checkedSentences, 19, 'review must cover the entire narration, including its final batch');
    assert.equal(reviewed.passes, false, 'a late unsupported claim must block the complete script');
    assert.equal(reviewed.findings[0].sentence, 'Mini production detail 17 is documented.');
    assert.equal(peakReviews, 2, 'reviews overlap with a bounded provider pool');
    const { correctFindings } = require('../src/agents/factualAudit');
    const { auditConsistency } = require('../src/agents/factualAudit');
    const globalContradiction = await auditConsistency({ script: auditScript, topic: 'Mini', evidenceText: 'claim-car', complete: async () => ({ checkedSentences: 19, findings: [{ id: 19, reason: 'Conflicts with the earlier event chronology', sourceIds: ['claim-car'] }] }) });
    assert.equal(globalContradiction.passes, false); assert.equal(globalContradiction.findings[0].sentence, 'Mini production detail 19 is documented.');
    assert.equal(await auditConsistency({ script: auditScript, evidenceText: 'claim-car', complete: async () => ({ checkedSentences: 18, findings: [] }) }), null, 'incomplete full-story review cannot certify a pass');
    assert.equal(await auditConsistency({ script: auditScript, evidenceText: 'claim-car', complete: async () => ({ checkedSentences: 19, findings: [{ id: 1, reason: 'Invented evidence', sourceIds: ['claim-fake'] }] }) }), null);
    const optionalEmptySources = await auditNarration({ script: 'Why did Mini change? This claim is unsupported.', evidenceText: 'claim-car', complete: async () => ({ reviews: [{ id: 1, verdict: 'neutral', reason: 'A question' }, { id: 2, verdict: 'unsupported', reason: 'No supporting evidence' }] }) });
    assert.equal(optionalEmptySources.passes, false, 'an omitted empty citation list must retain the factual rejection');
    assert.equal(optionalEmptySources.findings.length, 1);
    const neutralOnly = await auditNarration({ script: 'Why did Mini change?', evidenceText: 'claim-car', complete: async () => ({ reviews: [{ id: 1, verdict: 'neutral', reason: 'A question' }] }) });
    assert.equal(neutralOnly.passes, true, 'non-factual transitions need no evidence citation');
    let failedReviewCalls = 0;
    await assert.rejects(auditNarration({ script: auditScript, evidenceText: 'claim-car', complete: async () => { failedReviewCalls++; throw new Error('Review transport timeout'); } }), /transport timeout/);
    assert.ok(failedReviewCalls <= 2, 'a provider failure must stop new audit claims and settle owned requests');
    const correctedClaim = await correctFindings({ script: auditScript, findings: reviewed.findings, evidenceText: 'claim-car', complete: async () => ({ replacements: [{ id: 17, oldSentence: reviewed.findings[0].sentence, newSentence: 'The Mini production record continues.' }] }) });
    assert.ok(correctedClaim.includes('detail 16') && correctedClaim.includes('detail 18'), 'a factual correction preserves neighbouring reviewed material');
    assert.ok(!correctedClaim.includes('detail 17'));
    await assert.rejects(correctFindings({ script: auditScript, findings: reviewed.findings, evidenceText: 'claim-car', complete: async () => ({ replacements: [{ id: 17, oldSentence: 'A different draft.', newSentence: 'replacement' }] }) }), /does not match/, 'corrections cannot apply to another draft');
    assert.equal(sentenceUnits('The engine is 1.3 litres. Production continued.').length, 2, 'decimal points must remain in the reviewed claim');
    for (const faulty of [[], [{ id: 1, verdict: 'supported', sourceIds: ['claim-fake'], reason: 'Invented citation' }]]) {
      assert.equal(await auditNarration({ script: 'Mini launched in 1959.', evidenceText: 'claim-car', complete: async () => ({ reviews: faulty }) }), null, 'missing coverage and fabricated citations must fail closed');
    }
    const { writeSections } = require('../src/agents/sectionWriter');
    let sectionCalls = 0, activeSections = 0, peakSections = 0;
    const completeSection = async ({ user }) => {
      if (sectionCalls++ === 0) return { content: JSON.stringify({ beats: Array.from({ length: 5 }, (_, i) => ({ purpose: 'Beat ' + (i + 1), sourceIds: ['claim-car'] })) }) };
      activeSections++; peakSections = Math.max(peakSections, activeSections);
      const beat = Number(user.match(/Write ONLY beat (\d)/)[1]);
      await new Promise(resolve => setTimeout(resolve, beat % 2 ? 10 : 2));
      activeSections--;
      return { content: `Narration beat ${beat}. ${Array(252).fill('documented').join(' ')}.`, modelUsed: 'configured-model' };
    };
    const sectioned = await writeSections({ topic: 'Mini', wordTarget: 1500, evidenceText: 'claim-car | source details', complete: completeSection });
    assert.ok(sectioned.content.indexOf('Narration beat 1.') < sectioned.content.indexOf('Narration beat 2.')
      && sectioned.content.indexOf('Narration beat 2.') < sectioned.content.indexOf('Narration beat 3.')
      && sectioned.content.indexOf('Narration beat 3.') < sectioned.content.indexOf('Narration beat 4.')
      && sectioned.content.indexOf('Narration beat 4.') < sectioned.content.indexOf('Narration beat 5.'), 'sequential chapters retain their narrative order');
    assert.equal(peakSections, 1, 'long narration writes the next beat only after saving its preceding context');
    assert.equal(sectioned.modelUsed, 'configured-model');
    const wrappedCitation = 'Jack Brabham won the 1959 championship in a Cooper. <evidence:claim-race></evidence:claim-race>';
    assert.ok(!evaluateDraft(wrappedCitation, { topic: 'Mini Cooper', wordTarget: 15, previousScripts: [], evidence: citationEvidence }).script.includes('evidence:'), 'closing evidence tags cannot leak into narration or inflate its word count');
    await assert.rejects(writeSections({ topic: 'Mini', wordTarget: 1500, evidenceText: 'claim-car', complete: async () => ({ content: JSON.stringify({ beats: [{ purpose: 'bad', sourceIds: ['claim-fake'] }] }) }) }), /missing or invalid source/);
    const crossCheck = { title: 'Jack Brabham', url: 'https://www.formula1.com/history', articleText: 'Jack Brabham won the 1959 drivers championship in a Cooper.', contextForTopic: 'Mini Cooper', contextEntity: 'Cooper' };
    assert.ok(relevanceScore(crossCheck, 'Mini Cooper') > 0, 'seeded primary entity cross-checks must survive topic filtering');
    assert.equal(relevanceScore({ ...crossCheck, hasArticleBody: false }, 'Mini Cooper'), 0, 'discovery snippets cannot certify a related primary source');
    assert.equal(relevanceScore({ ...crossCheck, url: 'https://example.org/history' }, 'Mini Cooper'), 0, 'untrusted sources cannot declare themselves related cross-checks');
    assert.equal(evidence.sufficient, true, 'fixture should meet evidence minimum');
    assert.equal(relevanceScore({ title: 'Lionel toy trains', articleText: 'Model railroading products', url: 'https://example.org/toys', source: 'Bing' }, 'Lionel Messi'), 0, 'multi-word entity matching should reject one-token noise');
    const normalizedGta = normalizeTopic('Everything Related To GTA San Andreas Explained');
    assert.equal(normalizedGta.canonical, 'Grand Theft Auto: San Andreas', 'wrapper phrases should not change the research entity');
    const miniTopic = normalizeTopic('The Hidden Story Behind the Mini Cooper').canonical;
    assert.equal(miniTopic, 'Mini Cooper', 'headline hooks must not contaminate named-subject research');
    assert.equal(researchSubject('1991 Rover 216 GTi TC: A Closer Look at the Mathewsons Auction Listing'), '1991 Rover 216 GTi TC Mathewsons', 'auction listing titles must retain the vehicle and source identity but drop generic framing');
    assert.equal(researchSubject('Claims of Diana’s Private Correspondence: What Can Be Verified?'), "Diana's Private Correspondence", 'claim-check titles must search the named subject and evidence topic');
    assert.equal(researchSubject('15 British Beers from the Past: Which Names Still Exist?'), '15 British Beers from the Past', 'list hooks must not dominate source discovery');
    assert.equal(researchSubject('Prince Louis Health Headlines: What Has the Palace Confirmed?'), 'Prince Louis Health Palace', 'news hooks must not dominate subject discovery');
    assert.equal(researchSubject('The First Days After Death: What Forensic Science Can Establish'), 'The First Days After Death Forensic Science', 'documentary discipline terms must survive the question hook');
    assert.equal(researchSubject('A 65-Hour Rail Crossing: What Does the Crew’s Routine Involve?'), "A 65-Hour Rail Crossing Crew's Routine", 'specific people and actions from question hooks must survive subject extraction');
    assert.equal(relevanceScore({ title: 'A Closer Look', articleText: 'A Closer Look may refer to an album or news segment.', url: 'https://en.wikipedia.org/wiki/A_Closer_Look', source: 'Wikipedia' }, '1991 Rover 216 GTi TC: A Closer Look at the Mathewsons Auction Listing'), 0, 'generic title words cannot qualify as vehicle evidence');
    assert.ok(relevanceScore({ title: '1991 Rover 216 GTi Twin Cam', articleText: 'The 1991 Rover 216 GTi Twin Cam is listed in a vehicle auction catalogue.', url: 'https://example.org/rover', source: 'Auction catalogue' }, '1991 Rover 216 GTi TC: A Closer Look at the Mathewsons Auction Listing') > 0, 'named vehicle records remain relevant after stripping title hooks');
    assert.equal(relevanceScore({ title: 'Dubai Call Girls for Private Meetings', articleText: 'Private meetings and verified listings.', url: 'https://example.org/private', source: 'Search result' }, 'Claims of Diana’s Private Correspondence: What Can Be Verified?'), 0, 'a generic word from a claim-check hook cannot admit unrelated sources');
    assert.equal(relevanceScore({ title: 'Past', articleText: 'Past may refer to a period of time.', url: 'https://en.wikipedia.org/wiki/Past', source: 'Wikipedia' }, '15 British Beers from the Past: Which Names Still Exist?'), 0, 'a generic disambiguation page cannot qualify as evidence for a beer-history topic');
    assert.ok(officialSourceSeeds("Diana's Private Correspondence", 'general').some(item => item.url.includes('clintonlibrary.gov')), 'Diana correspondence research must include a live presidential archive record');
    assert.ok(officialSourceSeeds('Prince Louis Early Years Royal Record', 'general').some(item => item.url === 'https://www.royal.uk/prince-louis'), 'Prince Louis public-record topics must search the official profile');
    assert.ok(officialSourceSeeds('Indian Pacific 65-Hour Journey Crew Work', 'general').some(item => item.url.includes('greatsouthernrail.com.au')), 'Indian Pacific crew topics must search the operator itinerary');
    assert.ok(officialSourceSeeds('The First Days After Death Forensic Science', 'general').some(item => item.url.includes('nij.ojp.gov')), 'forensic death topics must search primary forensic-science sources');
    assert.ok(officialSourceSeeds('15 British Beers from the Past Which Names Still Exist', 'general').some(item => item.url.includes('wb.camra.org.uk')), 'British beer-history topics must include CAMRA reporting');
    assert.ok(officialSourceSeeds('15 British Beers from the Past Which Names Still Exist', 'general').some(item => item.url.includes('retrowow.co.uk')), 'British beer-history topics must include historical brand records');
    const dianaLedger = buildEvidenceLedger({
      query: "Diana's Private Correspondence",
      official: [source('Diana, Princess of Wales archive', 'A presidential records collection contains correspondence concerning Diana, Princess of Wales.', 'https://www.clintonlibrary.gov/research/diana', 'Clinton Presidential Library')],
      bing: [source('Princess Diana Secret Letters Exposed', 'A sensational report about Princess Diana and private letters.', 'https://radaronline.com/p/princess-diana-secret-letters', 'Radar Online')],
    });
    assert.equal(dianaLedger.claims.some(claim => claim.url.includes('radaronline.com')), false, 'sensational rumor sites must not supply documentary facts');
    const metadataOnlyEvidence = buildEvidenceLedger({
      query: '15 British Beers from the Past',
      structured: [
        { title: 'British beers: a survey of cask ale character', snippet: 'Publication metadata indexed by Crossref in Food Quality.', url: 'https://doi.org/10.1108/00070700610702109', source: 'Crossref' },
        { title: 'Beers and Their Effects on Thirst', snippet: 'Publication metadata indexed by Crossref in Food Quality.', url: 'https://doi.org/10.1108/00070709310023422', source: 'Crossref' },
      ],
    });
    assert.equal(metadataOnlyEvidence.sufficient, false, 'unhydrated scholarly metadata cannot satisfy a documentary evidence gate');
    const blockedOfficialEvidence = buildEvidenceLedger({
      query: "Diana's Private Correspondence",
      official: [{
        title: 'Diana, Princess of Wales correspondence archive',
        snippet: 'A presidential library catalogue page for records concerning Diana, Princess of Wales.',
        articleText: 'A presidential library catalogue page for records concerning Diana, Princess of Wales.',
        url: 'https://www.clintonlibrary.gov/research/diana',
        source: 'Clinton Presidential Library',
        hasArticleBody: false,
      }],
    });
    assert.equal(blockedOfficialEvidence.claims[0]?.sourceTier, 'primary', 'body-blocked first-party page metadata remains attributable primary evidence');
    assert.equal(blockedOfficialEvidence.claims[0]?.hasArticleBody, false, 'blocked body metadata must never be relabeled as fetched article text');
    assert.equal(blockedOfficialEvidence.sufficient, true, 'a relevant first-party catalogue record prevents a false no-attributable-claims failure');
    assert.equal(relevanceScore({ title: 'WhatsApp Web logging out', articleText: 'The story behind login failures.', url: 'https://example.org/unrelated' }, miniTopic), 0, 'hook-word overlap cannot qualify unrelated sources');
    const royalTopic = 'The Untold Details of the Quiet Work Behind a Royal Visit';
    const unrelatedSupport = { title: 'About Get Help | Microsoft Support', articleText: 'The Windows Get Help app provides support, tutorials, and assistance for users who sign in with a personal or work account.', url: 'https://support.microsoft.com/en-us/support/get-help/about-get-help', source: 'Microsoft Support', hasArticleBody: true };
    assert.equal(relevanceScore(unrelatedSupport, royalTopic), 0, 'generic title phrasing and a shared word like work cannot make unrelated support material relevant');
    const unrelatedRoyalLedger = buildEvidenceLedger({ query: royalTopic, official: [unrelatedSupport], bing: [{ title: 'An unrelated game review', articleText: 'A review of a popular game with a console update and new cast members.', url: 'https://example.org/game-review', source: 'IGN', hasArticleBody: true }] });
    assert.equal(unrelatedRoyalLedger.sufficient, false, 'unrelated sources must not satisfy the evidence gate for a royal visit');
    assert.equal(relevanceScore({ title: 'A rare David Bowie self-portrait', articleText: 'The artist created a self-portrait in 1996.', url: 'https://example.org/bowie', source: 'Variety' }, 'The Strange History of the Psychology of Déjà Vu'), 0, 'diacritic normalization and template words must not admit unrelated psychology sources');
    assert.equal(relevanceScore({ title: 'Great Balls of Fire', articleText: 'A song about fire and rock and roll.', url: 'https://example.org/song', source: 'Example' }, 'How the Great Fire of London Quietly Changed Britain'), 0, 'a two-word collision cannot qualify a three-anchor historical subject');
    assert.ok(relevanceScore({ title: 'Great Fire of London', articleText: 'The Great Fire of London began at a bakery in Pudding Lane.', url: 'https://example.org/great-fire', source: 'Example' }, 'How the Great Fire of London Quietly Changed Britain') > 0, 'a matching named event remains relevant');
    const miniLedger = buildEvidenceLedger({ query: miniTopic, official: [source('Mini Cooper heritage', 'Mini Cooper racing history includes the Cooper Car Company and its original designs.', 'https://www.mini.co.uk/en_GB/home/why-mini/history-of-mini.html', 'MINI UK')] });
    assert.equal(miniLedger.claims[0].sourceTier, 'primary', 'manufacturer heritage pages are attributable primary sources');
    assert.equal(detectNiche('Everything Related To GTA San Andreas Explained').niche, 'gaming', 'GTA San Andreas should route to gaming research');
    assert.ok(relevanceScore({ title: 'Grand Theft Auto: San Andreas', articleText: 'Grand Theft Auto: San Andreas is a 2004 action-adventure game.', url: 'https://en.wikipedia.org/wiki/Grand_Theft_Auto:_San_Andreas', source: 'Wikipedia' }, 'Grand Theft Auto: San Andreas') > 0, 'punctuation must not discard an exact named-entity source');
    const communityLedger = buildEvidenceLedger({ query: 'Grand Theft Auto: San Andreas', gatheredAt: new Date().toISOString(), rss: [source('Grand Theft Auto: San Andreas discussion', 'Grand Theft Auto: San Andreas community discussion with enough body text to be retrieved.', 'https://example.org/community-post', 'Hacker News: Grand Theft Auto: San Andreas')] });
    assert.equal(communityLedger.claims[0]?.sourceTier, 'community', 'Hacker News RSS labels must remain community evidence');
    assert.equal(communityLedger.claims[0]?.confidence, 'low', 'community hydration must not turn social material into high-confidence proof');
    const yandexFixture = '<li class="serp-item"><a class="OrganicTitle-Link" href="https://example.com/report">Example report</a><span class="OrganicTextContentSpan">Independent reporting about the topic.</span></li>';
    assert.equal(parseYandexResults(yandexFixture, 3)[0]?.url, 'https://example.com/report', 'Yandex public search results should be parsed into normal web sources');
    const researchPlan = buildResearchPlan({ topic: research.query, niche: 'history' });
    const coverage = buildCoverageReport({ plan: researchPlan, evidence });
    assert.ok(researchPlan.questions.length >= 7, 'research plan should contain multiple investigative questions');
    assert.ok(coverage.rows.some((row) => row.id === 'timeline'), 'coverage should track the timeline question');
    await store.saveResearchBundle({ jobId: first.id, research, evidence, brief: { query: research.query, sources: [] }, plan: researchPlan, coverage });
    assert.equal((await store.searchClaims('Apollo')).length > 0, true, 'claims should be searchable');
    const savedQuestionCount = await store.read((db) => db.exec('SELECT COUNT(*) AS count FROM research_questions WHERE job_id = ?', [first.id])[0].values[0][0]);
    assert.equal(savedQuestionCount, researchPlan.questions.length, 'research questions should be durable');

    const conflicting = buildEvidenceLedger({
      query: 'Apollo mission',
      gatheredAt: '2026-08-26T00:00:00.000Z',
      rss: [source('Apollo mission launch record', 'The Apollo mission launch date is June 1, 2026 and the program begins in Florida.', 'https://example.org/apollo-2026', 'Example Journal')],
      bing: [source('Apollo mission launch report', 'The Apollo mission launch date is June 1, 2027 and the program begins in Florida.', 'https://example.net/apollo-2027', 'Example Review')],
    });
    assert.ok(conflicting.conflicts.length >= 1, 'different dates for the same claim should be reported as a conflict');
    await store.saveResearchBundle({ jobId: first.id, research, evidence: conflicting, brief: { query: research.query, sources: [] } });
    const savedConflictCount = await store.read((db) => db.exec('SELECT COUNT(*) AS count FROM claim_conflicts')[0].values[0][0]);
    assert.ok(savedConflictCount >= 1, 'claim conflicts should be durable');

    const outageTopic = 'Apollo mission';
    const outageSeed = await store.createJob({ topic: outageTopic, idempotencyKey: 'stored-outage-fallback' });
    const outageResearch = { ...research, query: outageTopic };
    const outageEvidence = buildEvidenceLedger(outageResearch);
    await store.saveResearchBundle({ jobId: outageSeed.id, research: outageResearch, evidence: outageEvidence, brief: { query: outageTopic, sources: [] } });
    await store.checkpointJob(outageSeed.id, { status: 'context_ready', phase: 'context_ready', result: { research: outageResearch, evidence: outageEvidence, brief: { query: outageTopic, sources: [] } } });
    const outageRun = await runResearchJob({
      topic: outageTopic,
      store,
      input: { idempotencyKey: 'stored-outage-fallback' },
      backgroundDeep: false,
      gatherResearchFn: async () => ({ query: outageTopic, rss: [], bing: [], duckduckgo: [], errors: ['simulated provider outage'] }),
    });
    assert.equal(outageRun.evidence.sufficient, true, 'stored evidence should keep an outage run usable');
    assert.ok(outageRun.warnings.some((warning) => /stale fallback/i.test(warning)), 'stale fallback should be visible in warnings');

    let writerPipelineOptions;
    let writerSystemPrompt = '';
    let writerRequestOptions;
    const writerProgress = [];
    const deepWriter = await generateScript({
      topic: 'Apollo mission',
      apiKey: 'mock-key',
      useCache: false,
      onProgress: (progress, message) => writerProgress.push({ progress, message }),
      narratorProfile: {
        video: { url: 'https://www.youtube.com/watch?v=reference', title: 'Reference video' },
        model: 'mock-style-model',
        profile: { structure: 'Cold open, chronological escalation, restrained payoff.', pacing: 'Fast opening followed by measured explanatory beats.' },
      },
      researchJobFn: async (options) => {
        writerPipelineOptions = options;
        return { jobId: 'writer-deep-test', research, brief: buildBrief(research) };
      },
      chatCompletionFn: async (options) => { writerRequestOptions ||= options; writerSystemPrompt ||= options.messages?.[0]?.content || ''; throw new Error('simulated writer outage'); },
    });
    assert.equal(writerPipelineOptions.backgroundDeep, false, 'script generation should wait for its selected research pass');
    assert.equal(writerPipelineOptions.quick, true, 'script generation should use the bounded quick research route by default');
    assert.equal(writerPipelineOptions.includeReddit, true, 'quick research should include Reddit as a discovery and reaction lane');
    assert.ok(writerSystemPrompt.includes('Cold open, chronological escalation'), 'an analyzed YouTube reference wrapper must reach the writer as structure guidance');
    assert.equal(writerRequestOptions.totalTimeoutMs, 36000, 'quick writing must carry one hard total provider budget');
    assert.ok(writerRequestOptions.maxTokens >= 5000, 'long-form output budget must include discarded evidence-marker tokens');
    assert.equal(writerRequestOptions.maxAttemptsPerModel, 1, 'quick writing must not hide multiple retries inside one call');
    assert.ok(typeof deepWriter.script === 'string' && deepWriter.script.length > 80, 'deep research writer fallback must remain visible');
    assert.ok(writerProgress.some(event => event.progress === 30 && /sources/.test(event.message)), 'research progress should describe a completed evidence pass');
    assert.ok(writerProgress.some(event => /Writing/.test(event.message)), 'writing progress must be visible');

    const thinResearch = {
      query: 'Grand Theft Auto: San Andreas',
      gatheredAt: new Date().toISOString(),
      wikipedia: [source('Grand Theft Auto: San Andreas', 'Grand Theft Auto: San Andreas is an open-world action-adventure game set in the fictional state of San Andreas.', 'https://en.wikipedia.org/wiki/Grand_Theft_Auto:_San_Andreas', 'Wikipedia')],
    };
    const guaranteedDraft = await generateScript({
      topic: 'Everything Related To GTA San Andreas Explained',
      apiKey: 'mock-key',
      useCache: false,
      research: thinResearch,
      chatCompletionFn: async () => { throw new Error('simulated limited-evidence model outage'); },
    });
    assert.equal(guaranteedDraft.meta.status, 'draft_ready', 'thin research must still return a usable script draft');
    assert.ok(guaranteedDraft.script.includes('What is actually confirmed about'), 'thin research must still return narration-shaped output');
    assert.ok(!guaranteedDraft.script.includes('This story is not ready for a confident factual script yet.'), 'research-status text must not be returned as the script');
    assert.ok(!guaranteedDraft.script.includes('<evidence:'), 'internal evidence markers must never leak into returned narration');

    const timeout = await runProvider('test-timeout', () => new Promise((resolve) => setTimeout(() => resolve([]), 30)), { attempts: 2, timeoutMs: 5, delayMs: 1 });
    assert.equal(timeout.provider, 'test-timeout');
    assert.equal(timeout.retryable, true);

    const merged = mergeResearch({ query: 'x', rss: [{ url: 'https://example.org/a', title: 'A' }] }, { rss: [{ url: 'https://example.org/a', title: 'A updated' }, { url: 'https://example.org/b', title: 'B' }] });
    const hydrated = { url: 'https://example.org/body', title: 'Mini Cooper history', articleText: 'Mini Cooper history with verified engineering details. '.repeat(10), hasArticleBody: true };
    const shallow = { url: hydrated.url, title: hydrated.title, snippet: 'Mini Cooper history' };
    assert.equal(mergeResearch({ official: [shallow] }, { official: [hydrated] }).official[0].articleText, hydrated.articleText);
    assert.equal(mergeResearch({ official: [hydrated] }, { official: [shallow] }).official[0].articleText, hydrated.articleText);
    const bodyLedger = buildEvidenceLedger({ query: 'Mini Cooper', official: [shallow], duckduckgo: [hydrated] });
    assert.equal(bodyLedger.claims[0].hasArticleBody, true, 'duplicate discovery lanes cannot replace hydrated evidence');
    assert.equal(merged.rss.length, 2, 'research merge should deduplicate URLs');

    let phases = 0;
    const researchOptions = [];
    const pipeline = await runResearchJob({
      topic: 'Apollo mission pipeline',
      store,
      input: { researchMode: 'thorough' },
      backgroundDeep: false,
      gatherResearchFn: async (_topic, options) => { phases += 1; researchOptions.push(options); return { ...research, query: 'Apollo mission pipeline' }; },
    });
    assert.equal(phases, 2, 'fast and deep research should both checkpoint');
    assert.ok(researchOptions[0].researchPlan.questions.length >= 7, 'fast research should receive the question plan');
    assert.ok(researchOptions[1].researchPlan.questions.length >= 1, 'deep research should receive open questions');
    assert.ok(pipeline.jobId);
    assert.equal((await store.getJob(pipeline.jobId)).status, 'context_ready');

    let quickPhases = 0;
    const quickPipeline = await runResearchJob({
      topic: 'Apollo quick pipeline',
      store,
      input: { researchMode: 'quick', idempotencyKey: 'apollo-quick-pipeline' },
      gatherResearchFn: async (_topic, options) => { quickPhases += 1; assert.equal(options.quick, true); return { ...research, query: 'Apollo quick pipeline' }; },
    });
    assert.equal(quickPhases, 1, 'quick research should complete after one bounded source pass');
    assert.equal(quickPipeline.quick, true);

    let standardPhases = 0;
    const standardPipeline = await runResearchJob({
      topic: 'Apollo mission standard',
      store,
      input: { researchMode: 'standard', idempotencyKey: 'apollo-standard-pipeline' },
      gatherResearchFn: async (_topic, options) => { standardPhases += 1; assert.equal(options.quick, false); return { ...research, query: 'Apollo mission standard' }; },
    });
    assert.equal(standardPhases, 1, 'standard research should skip the deep pass like quick');
    assert.equal(standardPipeline.quick, false, 'standard is a production run, not a preview');
    assert.equal(standardPipeline.deepPending, false, 'standard returns its final result immediately');
    assert.equal(standardPipeline.partial, false, 'sufficient fast research keeps standard usable');
    assert.equal((await store.getJob(standardPipeline.jobId)).status, 'context_ready');

    // Standard mode deepens when the fast pass is thin: the deep pass is
    // skipped only when fast evidence already satisfies the gate. A live
    // 1-min Apollo run failed RESEARCH_INSUFFICIENT before this fix
    // because fast-only research carries no primary/article-body claims.
    // (Topic words must appear in the fixture text: relevance needs 2 of
    // the topic's anchor words, so 'standard thin' would fail the deep
    // pass on relevance alone instead of exercising the fall-through.)
    let thinPhases = 0;
    const thinPipeline = await runResearchJob({
      topic: 'Apollo mission history',
      store,
      input: { researchMode: 'standard', idempotencyKey: 'apollo-standard-thin' },
      backgroundDeep: false,
      gatherResearchFn: async (_topic, options) => {
        thinPhases += 1;
        if (options.phase === 'fast') return { query: 'Apollo mission history' };
        assert.equal(options.quick, false);
        return { ...research, query: 'Apollo mission history' };
      },
    });
    assert.equal(thinPhases, 2, 'thin fast research must fall through to the deep pass');
    assert.equal(thinPipeline.quick, false, 'deepened standard stays a production run');
    assert.equal(thinPipeline.deepPending, false, 'deepened standard returns its final result');
    assert.equal(thinPipeline.partial, false, 'deep pass supplies the missing evidence');
    assert.equal((await store.getJob(thinPipeline.jobId)).status, 'context_ready');

    const blocked = validateScriptForPublish({ script: 'too short', title: 'Test', description: 'Test', meta: { publishable: false } });
    assert.equal(blocked.ok, false);
    assert.ok(blocked.errors.length >= 2);

    const noModelFailure = await generateScript({
      topic: 'Apollo mission',
      apiKey: 'mock-key',
      useCache: false,
      research,
      chatCompletionFn: async () => { throw new Error('simulated model outage'); },
    });
    assert.ok(typeof noModelFailure.script === 'string' && noModelFailure.script.length > 80, 'model outage must return visible fallback/partial text');
    assert.ok(['draft_ready', 'ready_to_publish'].includes(noModelFailure.meta.status));
    assert.equal(noModelFailure.meta.publishable, false, 'assembled evidence cannot certify itself after writer failure');

    const noKeyDraft = await generateScript({
      topic: 'A topic with no configured model',
      apiKey: '',
      useCache: false,
      research: { query: 'A topic with no configured model', gatheredAt: new Date().toISOString(), rss: [], official: [], wikipedia: [], bing: [], duckduckgo: [] },
    });
    assert.ok(typeof noKeyDraft.script === 'string' && noKeyDraft.script.length > 80, 'missing model configuration must still return visible narration-shaped text');
    assert.ok(!noKeyDraft.script.includes('This story is not ready for a confident factual script yet.'), 'missing model configuration must not return the old research-status paragraph');
    assert.equal(noKeyDraft.meta.publishable, false, 'no-evidence rescue must never be publishable');

    const deadlineStarted = Date.now();
    const deadlineDraft = await generateScript({
      topic: 'Apollo mission deadline',
      apiKey: 'mock-key',
      useCache: false,
      quickDeadlineMs: 25,
      research: { ...research, query: 'Apollo mission deadline' },
      chatCompletionFn: async () => new Promise(() => {}),
    });
    assert.ok(Date.now() - deadlineStarted < 1000, 'a stalled quick writer must return before its hard deadline');
    assert.ok(deadlineDraft.script.length > 80, 'deadline fallback must still return a visible script');
    assert.ok(deadlineDraft.meta.timing.draftCalls <= 2, 'quick mode must make at most two calls inside one shared deadline');

    const originalFetch = global.fetch;
    const scoped = scopeResearch({ query: 'Mini Cooper', official: [{ title: 'MINI Cooper history', url: 'https://www.mini.co.uk/history', articleText: 'In 1961 the Cooper was born.' }, { title: '2027 MINI configurator', url: 'https://www.miniusa.com/build-your-own', articleText: 'Cars and prices' }] }, 'The Hidden Story Behind the Mini Cooper');
    assert.equal(scoped.official.length, 1, 'historical narration must not drift into current vehicle shopping');
    const { formatPlainNarration, openingHasHook, assessScript } = require('../src/agents/scriptQuality');
    assert.ok(assessScript({ script: 'word '.repeat(1100), wordTarget: 1500 }).issues.includes('The draft misses the requested length.'), 'a ten-minute documentary cannot pass with three quarters of the requested narration');
    // Nov 9, 2026: the old 1.35 upper cap rejected a good 219-word draft
    // for a 160-word target (ratio 1.37) — 3 words over the cap — while
    // the revision prompt itself aims the model at wordTarget + max(50,
    // 8%) (ratio 1.31 at 160 words). A modest overshoot is valid (the
    // CMS only floors duration); only a real overshoot must fail.
    assert.ok(!assessScript({ script: 'word '.repeat(219), wordTarget: 160 }).issues.includes('The draft misses the requested length.'), 'a 219-word draft for a 160-word target must pass the length check');
    assert.ok(assessScript({ script: 'word '.repeat(320), wordTarget: 160 }).issues.includes('The draft misses the requested length.'), 'a 2x overshoot must still fail the length check');
    assert.equal(formatPlainNarration('It’s 80% efficient, costs £50, and André agrees.'), "It's 80% efficient, costs £50, and André agrees.");
    assert.equal(openingHasHook('A small car.\n\nBut the Mini Cooper brought a different idea.\n\nHow did it happen?'), true, 'short spoken paragraphs must share the opening window');
    assert.equal(openingHasHook('In 1834 Parliament changed how England provided relief to the poor. ' + 'The documented historical background '.repeat(20) + '. ' + 'The later documented legislation '.repeat(20) + '.'), true, 'later exposition cannot invalidate an already concrete opening sentence');
    assert.equal(openingHasHook('In 1834 ' + Array(100).fill('documented').join(' ') + '.'), false, 'an opening that is itself a long run-on still fails');
    const { buildUserPrompt } = require('../src/prompts/scriptPrompts');
    const shortPrompt = buildUserPrompt({ topic: 'Mini Cooper', title: 'Mini Cooper', duration: '1min' });
    assert.ok(!shortPrompt.includes('at least 1000'), 'short runtime must never demand a thousand words');
    assert.match(shortPrompt, /at least 136 words/i);
    assert.match(shortPrompt, /longer.*(?:acceptable|advisory)/i);
    const { buildDeterministicHooks } = require('../src/engine/agents/hookAgent');
    const safeHooks = buildDeterministicHooks('Mini Cooper', 'A small car changed this particular race.');
    assert.ok(!JSON.stringify(safeHooks).match(/destroyed everything|secret decision|industry forever|overlooked until now/));
    try {
      const { chatCompletion, singleChatAttempt } = require('../src/providers/openrouter');
      let requestBody;
      global.fetch = async (_url, options) => {
        if (_url.endsWith('/models')) return {ok:true,json:async()=>({data:[{id:'test/model:free',architecture:{input_modalities:['text','image']},pricing:{prompt:'0',completion:'0'}}]})};
        requestBody = JSON.parse(options.body); return { ok: true, json: async () => ({ choices: [{ message: { content: '{"passes":true,"issues":[]}' } }] }) };
      };
      await singleChatAttempt({ apiKey: 'fixture', model: 'test/model:free', messages: [], timeoutMs: 1000, maxTokens: 3200, reasoning: { effort: 'low', exclude: true } });
      assert.deepEqual(requestBody.reasoning, { effort: 'low', exclude: true }, 'reasoning budget policy must reach the provider');
      const schemaFormat = { type: 'json_schema', json_schema: { name: 'fixture', strict: true, schema: { type: 'object' } } };
      await singleChatAttempt({ apiKey: 'fixture', model: 'test/model:free', messages: [], timeoutMs: 1000, responseFormat: schemaFormat });
      assert.deepEqual(requestBody.response_format, schemaFormat, 'the strict audit contract must reach the provider');
      assert.equal(requestBody.provider.require_parameters, true, 'audit requests must require endpoints that support the schema');
      global.fetch = async () => ({ ok: true, json: async () => ({ choices: [{ message: { content: '' }, finish_reason: 'length' }] }) });
      await assert.rejects(chatCompletion({ apiKey: 'fixture', model: 'test/model:free', messages: [{ role: 'user', content: 'Test' }], maxTotalAttempts: 1 }), /output budget exhausted/, 'reasoning-only completions need an actionable terminal error');
      global.fetch = async () => ({ ok: true, json: async () => ({ choices: [{ message: { content: 'An incomplete narration because' }, finish_reason: 'length' }] }) });
      const cutOff = await chatCompletion({ apiKey: 'fixture', model: 'test/model:free', messages: [{ role: 'user', content: 'Test' }], maxTotalAttempts: 1 });
      assert.equal(cutOff.truncated, true, 'visible partial output must carry its truncation verdict to the writer');
      let reservationCalls = 0;
      global.fetch = async () => {
        reservationCalls++;
        if (reservationCalls === 1) return { ok: false, status: 402, headers: { get: () => '0.005' }, text: async () => JSON.stringify({ error: { code: 402, metadata: { reason: 'in_flight_budget_exhausted', limit_source: 'openrouter_in_flight_budget' } } }) };
        return { ok: true, json: async () => ({ choices: [{ message: { content: 'Complete response.' }, finish_reason: 'stop' }] }) };
      };
      assert.equal((await chatCompletion({ apiKey: 'fixture', model: 'test/model:free', messages: [{ role: 'user', content: 'Test' }], maxTotalAttempts: 2 })).content, 'Complete response.');
      assert.equal(reservationCalls, 2, 'an explicitly transient reservation with Retry-After receives bounded recovery');
      const { providerError } = require('../src/providers/errorPolicy');
      assert.equal(providerError(402, { error: { code: 402, metadata: { reason: 'weight_exceeds_budget', limit_source: 'openrouter_credits' } } }, { get: () => '1' }).status, 402, 'a single unaffordable request remains a payment blocker');
      assert.equal(providerError(402, { error: { code: 402, metadata: { reason: 'in_flight_budget_exhausted', limit_source: 'openrouter_in_flight_budget' } } }).status, 402, 'without Retry-After, payment errors must not create a retry loop');
      let providerCalls = 0;
      global.fetch = async () => { providerCalls++; return { ok: true, json: async () => ({ error: { code: 401, message: 'Invalid API key' } }) }; };
      await assert.rejects(chatCompletion({ apiKey: 'fixture', model: 'test/model:free', messages: [{ role: 'user', content: 'Test' }], fallbackModels: ['fallback'] }), /401/);
      assert.equal(providerCalls, 1, 'embedded credential errors cannot trigger fallback storms');
      providerCalls = 0;
      global.fetch = async () => { providerCalls++; return { ok: true, json: async () => ({ choices: [] }) }; };
      await assert.rejects(chatCompletion({ apiKey: 'fixture', model: 'test/model:free', messages: [{ role: 'user', content: 'Test' }], fallbackModels: ['a', 'b', 'c'] }), /empty completion/);
      assert.equal(providerCalls, 3, 'all empty model completions must exhaust a bounded three-attempt budget');
      const parent = new AbortController();
      global.fetch = async (_url, { signal }) => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })), { once: true }));
      await assert.rejects(singleChatAttempt({ apiKey: 'fixture', model: 'test/model:free', messages: [], timeoutMs: 5, signal: parent.signal }), /timed out/);
      assert.equal(parent.signal.aborted, false, 'request deadline must work without aborting its caller');
      global.fetch = async (_url, { signal }) => ({ ok: true, json: async () => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(Object.assign(new Error('This operation was aborted'), { name: 'AbortError' })), { once: true })) });
      await assert.rejects(singleChatAttempt({ apiKey: 'fixture', model: 'test/model:free', messages: [], timeoutMs: 5 }), /timed out/, 'a hung response body needs the same retryable timeout classification as fetch');
      const { fetchArticle, extractArticle } = require('../src/providers/article');
      const fullArticle = extractArticle('<script type="application/ld+json">' + JSON.stringify({ description: 'Brief page description. '.repeat(12) }) + '</script><main><p>' + 'Jack Brabham&#x27;s Cooper championship history includes the actual drivers and dates. '.repeat(5) + '</p></main>');
      assert.ok(fullArticle.text.includes("Brabham's Cooper"), 'SEO descriptions cannot shadow visible body evidence or corrupt numeric entities');
      assert.ok(!fullArticle.text.includes('Brief page description'));
      const nestedArticle = extractArticle('<div class="article-body"><p>' + 'The Mini premiere was announced in September 2000. '.repeat(5) + '</p><div><p>' + 'The original Mini was introduced in 1959. '.repeat(5) + '</p></div><p>' + 'The new car was scheduled for Europe in 2001 and America in 2002. '.repeat(5) + '</p></div>');
      assert.ok(nestedArticle.text.includes('America in 2002'), 'article containers must extend past their first nested closing tag');
      let articleCalls = 0;
      global.fetch = async url => { articleCalls++; return { ok: true, headers: { get: () => 'text/html' }, text: async () => '<meta http-equiv="refresh" content="0;url=' + (url.endsWith('/a') ? 'https://example.org/b' : 'https://example.org/a') + '">' }; };
      assert.equal(await fetchArticle('https://example.org/a'), null);
      assert.equal(articleCalls, 2, 'cyclic article meta redirects must stop');
      articleCalls = 0;
      global.fetch = async () => { articleCalls++; if (articleCalls === 1) throw Object.assign(new Error('fetch failed'), { cause: { code: 'EAI_AGAIN' } }); return { ok: true, headers: { get: () => 'text/html' }, text: async () => '<article><p>' + 'Mini Cooper engineering and racing history. '.repeat(12) + '</p></article>' }; };
      assert.ok((await fetchArticle('https://example.org/mini'))?.text.length > 180);
      assert.equal(articleCalls, 2, 'temporary DNS failures get one bounded retry');
    } finally { global.fetch = originalFetch; }
    console.log('Production safety checks passed');
  } finally {
    await store.close().catch(() => undefined);
    for (const file of [dbPath, `${dbPath}.bak`, `${dbPath}.tmp`]) {
      try { fs.rmSync(file, { force: true }); } catch (_) {}
    }
  }
}

main().catch((err) => {
  console.error('Production safety test failed:', err);
  process.exitCode = 1;
});
