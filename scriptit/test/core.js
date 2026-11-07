const assert = require('assert');
const { buildEvidenceLedger } = require('../src/agents/evidence');
const { createNarrativePlan } = require('../src/agents/narrativePlanner');
const { assessScript, similarity } = require('../src/agents/scriptQuality');

const research = {
  gatheredAt: '2026-07-14T00:00:00.000Z',
  rss: [
    {
      title: 'Official update',
      articleText: 'An official institution published a detailed update that contains enough context to support a carefully qualified script claim.',
      source: 'Official institution',
      url: 'https://example.com/update',
    },
  ],
  duckduckgo: [],
  duckduckgoNews: [],
};

const ledger = buildEvidenceLedger(research);
assert.equal(ledger.claims.length, 1);
assert.equal(ledger.claims[0].confidence, 'high');

const officialLedger = buildEvidenceLedger({
  query: 'gta vi',
  gatheredAt: '2026-08-07T00:00:00.000Z',
  rss: [],
  bing: [{
    title: 'Grand Theft Auto VI: An Extended Look - Rockstar Games',
    snippet: 'Rockstar Games announced an extended look for Grand Theft Auto VI.',
    url: 'https://www.rockstargames.com/newswire/article/example',
    source: 'Bing',
    hasArticleBody: false,
  }],
});
assert.equal(officialLedger.claims[0].official, true);
assert.equal(officialLedger.claims[0].confidence, 'high');

const plan = createNarrativePlan({ topic: 'Why a system changed', contentType: 'puzzle', evidence: ledger });
assert.ok(plan.strategy.id);
assert.ok(plan.strategy.flow.length >= 4);

const formatted = assessScript({
  script: '**A plain script.** [No labels] 😊',
  wordTarget: 5,
  previousScripts: [],
});
assert.equal(formatted.script, 'A plain script. No labels');

const lecture = assessScript({
  script: 'Based on the research, Rockstar has not announced anything. I cannot write a script.',
  wordTarget: 5,
  previousScripts: [],
});
assert.equal(lecture.passes, false);

const unsupported = assessScript({
  script: 'This is the first real gameplay footage, the launch date is locked in, and there is no mention of multiplayer at launch.',
  wordTarget: 20,
  previousScripts: [],
});
assert.equal(unsupported.passes, false);

assert.ok(similarity('The first sentence is exactly the same today', 'The first sentence is exactly the same today') > 0.9);
assert.ok(similarity('A completely different story starts here now', 'The first sentence is exactly the same today') < 0.3);

console.log('Core generation checks passed');
