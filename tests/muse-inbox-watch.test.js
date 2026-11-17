const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { startResidentInbox, createInboxReadiness, pendingSummary } = require('../lib/museInbox');

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
