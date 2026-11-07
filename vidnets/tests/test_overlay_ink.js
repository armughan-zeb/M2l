const fs = require('fs');
const assert = require('assert');
(async () => {
  const code = fs.readFileSync('src/remotion/engine/DocumentaryTypography.js', 'utf8');
  const {documentaryOverlayInk} = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
  assert.equal(documentaryOverlayInk('#000000'), '#FFFFFF');
  assert.equal(documentaryOverlayInk('#101820'), '#FFFFFF');
  assert.equal(documentaryOverlayInk('#F5C344'), '#F5C344');
  assert.equal(documentaryOverlayInk('#FFFFFF', '#FFFFFF'), '#101820');
  assert.equal(documentaryOverlayInk('#000000', '#101820'), '#FFFFFF');
  assert.equal(documentaryOverlayInk('invalid'), '#FFFFFF');
  console.log('Overlay contrast checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
