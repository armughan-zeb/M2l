const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const {startLocalAssetServer, asLocalUrl} = require('../scripts/render-remotion-manifest');

function read(url, headers = {}, method = 'GET') {
  return new Promise((resolve, reject) => {
    const request = http.request(url, {method, headers}, response => {
      const buffers = [];
      response.on('data', part => buffers.push(part));
      response.on('error', reject);
      response.on('end', () => resolve({status:response.statusCode,headers:response.headers,body:Buffer.concat(buffers)}));
    });
    request.setTimeout(3000, () => request.destroy(new Error('Asset response stalled')));
    request.on('error', reject);
    request.end();
  });
}

test('render assets stream completely and support video byte seeking', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vidzeo-asset-transport-'));
  const file = path.join(directory, 'media with spaces-é.mp4');
  const bytes = Buffer.alloc(1024 * 1024 + 17);
  for (let i = 0; i < bytes.length; i++) bytes[i] = i % 251;
  fs.writeFileSync(file, bytes);
  const {server, baseUrl} = await startLocalAssetServer();
  try {
    const url = asLocalUrl(file, baseUrl);
    const full = await read(url);
    assert.equal(full.status,200);
    assert.deepEqual(full.body,bytes);
    const partial = await read(url,{Range:'bytes=1024-4095'});
    assert.equal(partial.status,206);
    assert.deepEqual(partial.body,bytes.subarray(1024,4096));
    const overshoot = await read(url,{Range:`bytes=${bytes.length-20}-${bytes.length+10000}`});
    assert.equal(overshoot.status,206);
    assert.deepEqual(overshoot.body,bytes.subarray(-20));
    const suffix = await read(url,{Range:'bytes=-32'});
    assert.deepEqual(suffix.body,bytes.subarray(-32));
    assert.equal((await read(url,{Range:`bytes=${bytes.length}-`})).status,416);
    const head = await read(url,{},'HEAD');
    assert.equal(Number(head.headers['content-length']),bytes.length);
    assert.equal(head.body.length,0);
  } finally {
    server.closeAllConnections?.();
    await new Promise(resolve => server.close(resolve));
    const relative = path.relative(path.resolve(os.tmpdir()),path.resolve(directory));
    if (relative && !relative.startsWith('..') && !path.isAbsolute(relative)) fs.rmSync(directory,{recursive:true,force:true});
  }
});
