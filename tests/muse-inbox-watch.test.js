const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { startResidentInbox, createInboxReadiness, pendingRequests, pendingSummary } = require('../lib/museInbox');

test('local diagnostic reports only live requests for the requested job and owner', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-summary-'));
  try {
    const now = Date.now() / 1000;
    for (let index = 1; index <= 8; index++) {
      const id = index.toString(16).padStart(32, '0');
      const dir = path.join(root, id);
      fs.mkdirSync(dir);
      const row = { id, owner_id: index === 3 ? 'other' : 'local-user',
        job_id: index === 4 ? 'other' : 'job', state: index === 5 ? 'answered' : 'pending',
        created_at: now - index, expires_at: now + (index === 6 ? -1 : 60) };
      fs.writeFileSync(path.join(dir, 'request.json'), JSON.stringify(row));
      if (index === 7) fs.writeFileSync(path.join(dir, 'response.json'), '{}');
      if (index === 8) fs.writeFileSync(path.join(dir, 'request.json'), '{');
    }
    const result = await pendingSummary(root, 'job');
    assert.deepEqual(result.rows.map(row => row.id), ['2'.padStart(32, '0'), '1'.padStart(32, '0')]);
    assert.ok(result.elapsedMs >= 0);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('three expired inbox requests invalidate readiness until a fresh probe', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-expiry-'));
  const now = Date.now();
  for (const digit of ['a', 'b', 'c']) {
    const id = digit.repeat(32);
    const dir = path.join(root, id);
    fs.mkdirSync(dir);
    fs.writeFileSync(path.join(dir, 'request.json'), JSON.stringify({
      id, owner_id: 'local-user', state: 'pending',
      created_at: now / 1000, expires_at: (now + 500) / 1000
    }));
  }
  let probes = 0;
  const ready = createInboxReadiness({
    probe: async () => ++probes, onReady() {}, onFailure() {}
  });
  assert.equal(await ready(), true);
  let reportExpiry;
  const expired = new Promise(resolve => { reportExpiry = resolve; });
  const stop = startResidentInbox({
    root, concurrency: 1, pollMs: 20, logger: { info() {}, warn() {} },
    answer: () => new Promise(() => {}),
    onExpiryLimit: () => { ready.invalidate(); reportExpiry(); }
  });
  let timer;
  try {
    await Promise.race([expired, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('expiry threshold did not fire')), 3000);
    })]);
    assert.equal(await ready(), true);
    assert.equal(probes, 2);
  } finally {
    clearTimeout(timer);
    stop();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('watch dispatches a newly installed request without waiting for the poll', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-watch-'));
  const id = 'a'.repeat(32);
  let finish;
  const picked = new Promise(resolve => { finish = resolve; });
  const stop = startResidentInbox({ root, pollMs: 10000, logger: {info(){}, warn(){}},
    answer: async () => { finish(); return 'ok'; }, complete: async () => {} });
  try {
    const staging = path.join(root, 'staging');
    fs.mkdirSync(staging);
    fs.writeFileSync(path.join(staging, 'request.json'), JSON.stringify({id, owner_id:'local-user', state:'pending', created_at:Date.now()/1000, expires_at:Date.now()/1000+30}));
    fs.renameSync(staging, path.join(root, id));
    let timer;
    try { await Promise.race([picked, new Promise((_, reject) => { timer = setTimeout(() => reject(Error('watch did not dispatch')), 3000); })]); }
    finally { clearTimeout(timer); }
  } finally { stop(); fs.rmSync(root, { recursive:true, force:true }); }
});

test('a completed request immediately releases capacity for the next request', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-refill-'));
  for (const digit of ['a', 'b']) {
    const id = digit.repeat(32);
    const dir = path.join(root, id);
    fs.mkdirSync(dir);
    fs.writeFileSync(path.join(dir, 'request.json'), JSON.stringify({ id, owner_id: 'local-user', state: 'pending', created_at: Date.now()/1000, expires_at: Date.now()/1000 + 30 }));
  }
  const seen = [];
  let done;
  const answered = new Promise(resolve => { done = resolve; });
  const stop = startResidentInbox({ root, concurrency: 1, pollMs: 10000,
    logger: { info() {}, warn() {} },
    answer: async row => { seen.push(row.id); if (seen.length === 2) done(); return 'ok'; },
    complete: async () => {} });
  try {
    let timer;
    try { await Promise.race([answered, new Promise((_, reject) => { timer = setTimeout(() => reject(Error('queue did not refill')), 3000); })]); }
    finally { clearTimeout(timer); }
    assert.equal(new Set(seen).size, 2);
  } finally { stop(); fs.rmSync(root, { recursive: true, force: true }); }
});

// Regression (2026-11-21, job a42a1b7b): the daemon, the pool and
// hook-woken agents all raced the same visual_review requests and all
// but the fastest were refused "already answered". A live claim means
// another answerer owns the row — the daemon must not compose a
// duplicate, and an expired claim releases the row back to the pool.

test('pendingRequests skips a live claim and returns the row after the lease lapses', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-claim-'));
  try {
    const now = Date.now() / 1000;
    for (const [id, state, claimExpires] of [
      ['a'.repeat(32), 'claimed', now + 600],
      ['b'.repeat(32), 'claimed', now - 1],
      ['c'.repeat(32), 'pending', null]
    ]) {
      const dir = path.join(root, id);
      fs.mkdirSync(dir);
      const row = { id, owner_id: 'local-user', state, created_at: now, expires_at: now + 600 };
      if (claimExpires !== null) { row.claimed_by = 'hook-agent'; row.claim_expires_at = claimExpires; }
      fs.writeFileSync(path.join(dir, 'request.json'), JSON.stringify(row));
    }
    assert.deepEqual(pendingRequests(root).map(row => row.id).sort(),
      ['b'.repeat(32), 'c'.repeat(32)].sort());
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('resident claims a request on pickup before composing', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-claim-pickup-'));
  const id = 'a'.repeat(32);
  const dir = path.join(root, id);
  fs.mkdirSync(dir);
  fs.writeFileSync(path.join(dir, 'request.json'), JSON.stringify({
    id, owner_id: 'local-user', state: 'pending',
    created_at: Date.now() / 1000, expires_at: Date.now() / 1000 + 60 }));
  const claimedIds = [];
  let done;
  const answered = new Promise(resolve => { done = resolve; });
  const stop = startResidentInbox({ root, pollMs: 10, logger: { info() {}, warn() {} },
    claim: async (root, row) => { claimedIds.push(row.id); return true; },
    release: async () => {},
    answer: async () => { done(); return 'ok'; },
    complete: async () => {} });
  try {
    let timer;
    try { await Promise.race([answered, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('claim pickup did not answer')), 3000); })]); }
    finally { clearTimeout(timer); }
    assert.deepEqual(claimedIds, [id]);
  } finally { stop(); fs.rmSync(root, { recursive: true, force: true }); }
});

test('resident skips a request whose claim is held by another answerer', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-claim-skip-'));
  const id = 'a'.repeat(32);
  const dir = path.join(root, id);
  fs.mkdirSync(dir);
  fs.writeFileSync(path.join(dir, 'request.json'), JSON.stringify({
    id, owner_id: 'local-user', state: 'pending',
    created_at: Date.now() / 1000, expires_at: Date.now() / 1000 + 60 }));
  const claimedIds = [];
  let calls = 0;
  const stop = startResidentInbox({ root, pollMs: 10, logger: { info() {}, warn() {} },
    claim: async (root, row) => { claimedIds.push(row.id); return false; },
    release: async () => {},
    answer: async () => { calls += 1; return 'must not run'; },
    complete: async () => {} });
  try {
    await new Promise(resolve => setTimeout(resolve, 500));
    assert.deepEqual(claimedIds, [id]);
    assert.equal(calls, 0, 'the owned request was composed despite the lost claim');
  } finally { stop(); fs.rmSync(root, { recursive: true, force: true }); }
});

test('transport-unavailable releases the claim so the hook can take the row', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-claim-release-'));
  const id = 'a'.repeat(32);
  const dir = path.join(root, id);
  fs.mkdirSync(dir);
  fs.writeFileSync(path.join(dir, 'request.json'), JSON.stringify({
    id, owner_id: 'local-user', state: 'pending',
    created_at: Date.now() / 1000, expires_at: Date.now() / 1000 + 60 }));
  const released = [];
  let calls = 0;
  const stop = startResidentInbox({ root, pollMs: 10, logger: { info() {}, warn() {} },
    transportUnavailableCooldownMs: 50,
    claim: async () => true,
    release: async (root, row) => { released.push(row.id); },
    answer: async () => {
      calls += 1;
      if (calls === 1) throw Object.assign(new Error('MUSE_TRANSPORT_UNAVAILABLE: test pool down'), { code: 3 });
      return 'answered on retry';
    },
    complete: async () => {} });
  try {
    const start = Date.now();
    while (calls < 2 && Date.now() - start < 5000) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(calls, 2, 'the request was not retried after the cooldown');
    assert.ok(released.includes(id), 'the claim was not released on transport failure');
  } finally { stop(); fs.rmSync(root, { recursive: true, force: true }); }
});
