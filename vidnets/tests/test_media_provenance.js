const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../src/remotion/engine/MediaProvenance.js'), 'utf8');
const modulePromise = import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const credit = { approved: true, source: 'primary_source_review', label: 'NASA archive · 2020', source_url: 'https://www.nasa.gov/image-article/example/' };
test('verified archival credit survives media and primary-asset handoff', async () => {
  const { approvedSourceCredit } = await modulePromise;
  assert.deepEqual(approvedSourceCredit({ media: { source_credit: credit } }), credit);
  assert.deepEqual(approvedSourceCredit({ primary_visual: { asset: { source_credit: credit } } }), credit);
});
test('unapproved or unsupported source marks stay off screen', async () => {
  const { approvedSourceCredit } = await modulePromise;
  for (const invalid of [{ ...credit, approved: false }, { ...credit, source: 'guessed' },
    { ...credit, source_url: '' }, { ...credit, label: '' }, { ...credit, label: 'x'.repeat(97) }]) {
    assert.equal(approvedSourceCredit({ media: { source_credit: invalid } }), null);
  }
});
