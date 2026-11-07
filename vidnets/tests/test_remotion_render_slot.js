const test = require('node:test');
const assert = require('node:assert/strict');
const { acquireRenderLease } = require('../scripts/render-remotion-manifest');

test('a second renderer waits for the exclusive slot and reports progress', async () => {
  const first = await acquireRenderLease(0);
  const port = first.address().port;
  let notices = 0;
  const release = setTimeout(() => first.close(), 100);
  try {
    const second = await acquireRenderLease(port, { waitMs: 1000, retryMs: 20, onWait: () => notices++ });
    assert.ok(notices > 0);
    await new Promise(resolve => second.close(resolve));
  } finally {
    clearTimeout(release);
    if (first.listening) first.close();
  }
});

test('render-slot waiting remains bounded and does not create a second active renderer', async () => {
  const first = await acquireRenderLease(0);
  try {
    await assert.rejects(acquireRenderLease(first.address().port, { waitMs: 50, retryMs: 10 }),
      error => error.code === 'VIDZEO_RENDER_BUSY');
    assert.ok(first.listening);
  } finally { await new Promise(resolve => first.close(resolve)); }
});
