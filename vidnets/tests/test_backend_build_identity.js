const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const source = fs.readFileSync(path.join(__dirname, '../main.js'), 'utf8');
const body = source.match(/function isCurrentPipelineBackend\(payload\) \{[\s\S]*?\n\}/)[0];
const root = path.resolve(__dirname, '../fake-resource-root');
const context = vm.createContext({path, EXPECTED_PIPELINE_BUILD_ID: 'pipeline',
  getPackagedBuildId: () => 'new-build', getResourcePath: () => root});
vm.runInContext(body, context);
const status = {ready: true, pipeline_build_id: 'pipeline', resource_root: root,
  runtime_build: {release_build_id: 'new-build', restart_required: false}};
assert.equal(context.isCurrentPipelineBackend(status), true);
assert.equal(context.isCurrentPipelineBackend({...status, resource_root: root + '-old'}), false);
assert.equal(context.isCurrentPipelineBackend({...status, runtime_build: {release_build_id: 'old-build'}}), false);
assert.equal(context.isCurrentPipelineBackend({...status, runtime_build: {release_build_id: 'new-build', restart_required: true}}), false);
assert.equal(context.isCurrentPipelineBackend({...status, runtime_build: {}}), false);
console.log('Backend identity checks passed: exact build, resource folder, restart state, and legacy rejection.');
