const fs = require('fs');
const path = require('path');
const os = require('os');
const {spawnSync} = require('child_process');
const assert = require('assert');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vidnets-scan-check-'));
try {
  const file = path.join(root, 'sample.js');
  const scan = () => spawnSync(process.execPath, [path.resolve('scripts/security-scan.js'), '--root', root], {encoding:'utf8'});
  fs.writeFileSync(file, 'const FILE_TOKEN = "remotion-file:"; const DELAY_RENDER_RETRY_TOKEN = "- Rendering the frame will be retried.";');
  assert.equal(scan().status, 0, 'Framework messages are not credentials');
  fs.writeFileSync(file, 'const options = {"token": "test-credential-literal"};');
  assert.equal(scan().status, 1, 'Actual token fields must block the package');
  fs.writeFileSync(file, 'const password = "test-credential-literal";');
  assert.equal(scan().status, 1, 'Actual password fields must block the package');
  fs.writeFileSync(file, 'const API_TOKEN = "test-credential-literal";');
  assert.equal(scan().status, 1, 'Prefixed credential names must remain protected');
  fs.writeFileSync(file, 'const FILE_TOKEN = "test-credential-literal";');
  assert.equal(scan().status, 1, 'A framework name with an unexpected value must not bypass the scan');
  fs.writeFileSync(file, 'const example = {"topicToken": "${token}"};');
  assert.equal(scan().status, 0, 'Exact diagnostic placeholders are not literal credentials');
  console.log('Security scanner literal checks passed');
} finally {
  fs.unlinkSync(path.join(root, 'sample.js')); fs.rmdirSync(root);
}
