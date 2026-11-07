const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const {spawnSync} = require('child_process');
const asar = require('@electron/asar');
const root = path.resolve(__dirname, '..');
const exe = path.join(root, 'dist-build-cache/final-installer/Vidnets v1.exe');
const output = path.join(root, 'artifacts/single_api_release_20261006/extracted-installer');
const seven = path.join(root, 'node_modules/7zip-bin/win/x64/7za.exe');
const names = ['server/app.py', 'server/config.py', 'server/services/generation_request.py',
  'server/services/generation_cost_policy.py', 'server/services/generation_registry.py',
  'server/services/structured_response_cache.py', 'server/services/openrouter_llm.py',
  'server/services/openrouter_usage.py', 'server/services/queue_manager.py',
  'server/services/bounded_http.py', 'server/services/jev_decision_service.py',
  'server/services/research_ledger.py', 'server/services/research_search_worker.py',
  'server/services/web_image/universal_scraper.py',
  'server/services/editorial_director/director.py', 'server/services/visual_intelligence/vision_client.py',
  '.env.defaults', 'public/update-build.json'];
function run(args) {
  const result = spawnSync(seven, args, {cwd:root, encoding:'utf8', windowsHide:true});
  if (result.status !== 0) throw new Error('Installer archive check failed: '+result.stderr);
  return result.stdout;
}
function hash(bytes) {return crypto.createHash('sha256').update(bytes).digest('hex');}
const record = {};
try {
  fs.mkdirSync(output, {recursive:true});
  record.archiveVerified = run(['t',exe]).includes('Everything is Ok');
  if (!record.archiveVerified) throw new Error('Archive verification did not confirm integrity');
  run(['x',exe,'-o'+output,'-y','resources/app.asar',...names.map(name=>'resources/'+name)]);
  record.files = names.map(name=>{
    const bytes=fs.readFileSync(path.join(output,'resources',name));
    return {path:name,sha256:hash(bytes),matchesSource:bytes.equals(fs.readFileSync(path.join(root,name)))};
  });
  const extractedAsar=path.join(output,'resources/app.asar');
  record.asar = ['main.js','src/index.html','src/renderer.js','src/index.css'].map(name=>{
    const bytes=asar.extractFile(extractedAsar,name);
    return {path:name,sha256:hash(bytes),matchesSource:bytes.equals(fs.readFileSync(path.join(root,name)))};
  });
  if (![...record.files,...record.asar].every(row=>row.matchesSource)) throw new Error('Installer contains stale code');
  record.buildId=JSON.parse(fs.readFileSync(path.join(output,'resources/public/update-build.json'),'utf8')).buildId;
  record.installer={bytes:fs.statSync(exe).size,sha256:hash(fs.readFileSync(exe))};
  record.success=true;
} catch (error) {record.error=error.message;process.exitCode=1;}
fs.writeFileSync(path.join(root,'artifacts/single_api_release_20261006/installer-verification.json'),JSON.stringify(record,null,2));
console.log(JSON.stringify({success:record.success,buildId:record.buildId,installer:record.installer,error:record.error}));
