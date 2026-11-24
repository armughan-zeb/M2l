const { test } = require('node:test');
const assert = require('node:assert/strict');
const { renderBrowserSession } = require('../scripts/render-remotion-manifest');

// Regression (GTA VI ef306926, Nov 16, 2026): the section render reused
// the selection browser via browserSession.get(), which returned the
// cached browser object without checking whether it was still alive.
// After the per-section render-mode guard killed that browser's Chrome
// tree, renderMedia hung on its dead pages at 0 frames until the 300 s
// watchdog killed the attempt. get() must reopen a disconnected browser
// instead of handing the dead one back.
function fakeBrowser(connected) {
  return {
    connected,
    isConnected() { return this.connected; },
    async close() { this.connected = false; },
  };
}

test('browser session reuses a live browser', async () => {
  let opens = 0;
  const browser = fakeBrowser(true);
  const session = renderBrowserSession(async () => { opens += 1; return browser; });
  assert.equal(await session.get({}), browser);
  assert.equal(await session.get({}), browser);
  assert.equal(opens, 1);
});

test('browser session reopens a disconnected browser instead of reusing it', async () => {
  let opens = 0;
  const dead = fakeBrowser(false);
  const live = fakeBrowser(true);
  const session = renderBrowserSession(async () => { opens += 1; return opens === 1 ? dead : live; });
  assert.equal(await session.get({}), dead);
  assert.equal(await session.get({}), live);
  assert.equal(opens, 2);
  assert.equal(await session.get({}), live);
  assert.equal(opens, 2);
});

test('browser session reuses a browser without isConnected', async () => {
  let opens = 0;
  const legacy = { async close() {} };
  const session = renderBrowserSession(async () => { opens += 1; return legacy; });
  assert.equal(await session.get({}), legacy);
  assert.equal(await session.get({}), legacy);
  assert.equal(opens, 1);
});

test('browser session close drops the cached browser so the next get reopens', async () => {
  let opens = 0;
  const first = fakeBrowser(true);
  const second = fakeBrowser(true);
  const session = renderBrowserSession(async () => { opens += 1; return opens === 1 ? first : second; });
  assert.equal(await session.get({}), first);
  await session.close();
  assert.equal(first.connected, false);
  assert.equal(await session.get({}), second);
  assert.equal(opens, 2);
});
