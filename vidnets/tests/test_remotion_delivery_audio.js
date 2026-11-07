const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { finalizeMp4Audio } = require('../scripts/render-remotion-manifest');

test('section delivery copies video and encodes continuous AAC audio once', async () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'vidnets-aac-delivery-'));
  const file = path.join(folder, 'export.mp4');
  fs.writeFileSync(file, 'original export');
  try {
    let calls = 0;
    await finalizeMp4Audio(file, async ({ args }) => {
      calls++;
      assert.equal(args[args.indexOf('-c:v') + 1], 'copy');
      assert.equal(args[args.indexOf('-c:a') + 1], 'aac');
      assert.equal(args[args.indexOf('-movflags') + 1], '+faststart');
      fs.writeFileSync(args.at(-1), Buffer.alloc(5000, 1));
    });
    assert.equal(calls, 1);
    assert.equal(fs.statSync(file).size, 5000);
    assert.equal(fs.existsSync(`${file}.compatible.mp4`), false);
  } finally { fs.rmSync(folder, { recursive: true, force: true }); }
});

test('audio export failure preserves the original output', async () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'vidnets-aac-failure-'));
  const file = path.join(folder, 'export.mp4');
  fs.writeFileSync(file, 'original export');
  try {
    await assert.rejects(finalizeMp4Audio(file, async ({ args }) => {
      fs.writeFileSync(args.at(-1), 'incomplete');
      throw new Error('disk failure');
    }), /disk failure/);
    assert.equal(fs.readFileSync(file, 'utf8'), 'original export');
    assert.equal(fs.existsSync(`${file}.compatible.mp4`), false);
  } finally { fs.rmSync(folder, { recursive: true, force: true }); }
});
