const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/renderer.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../src/index.html'), 'utf8');
test('ordinary low memory does not display the retired blocking RAM banner', () => {
  assert(!source.includes('function memoryWarningPresentation('));
  assert(!html.includes('id="memoryWarning"'));
});
test('a genuinely paused renderer still has actionable queue status and retry', () => {
  assert(source.includes('Waiting for free RAM'));
  assert(source.includes('resumeQueueItem('));
  assert(source.includes('Retry</button>'));
});
