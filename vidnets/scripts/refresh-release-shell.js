// Refresh a verified runtime after UI, editorial, retrieval and render fixes. Rebuild Electron's ASAR
// and its executable integrity resource together, then repeat all package
// checks and smoke renders. No dependency/runtime versions are changed.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const {spawnSync} = require('child_process');
const verify = require('./build-installer');
const root = path.resolve(__dirname, '..');
const cache = path.join(root, 'dist-build-cache');
const unpacked = path.join(cache, 'builder-output/win-unpacked');
const shell = path.join(cache, 'release-shell');
const installerOutput = path.join(cache, 'final-installer');
const dist = path.join(root, 'dist');

function run(command, args, options = {}) {
  const npm = ['npm','npx'].includes(command);
  const result = spawnSync(npm ? 'cmd.exe' : command, npm ? ['/d','/s','/c',command,...args] : args,
    {cwd:root,stdio:'inherit',shell:false,...options});
  if (result.status !== 0) throw new Error(command+' failed: '+result.status);
}
function below(target, parent) {
  const relative=path.relative(parent,path.resolve(target));
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('Invalid staging path');
  return path.resolve(target);
}
function hash(file) {return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');}
function mirror(source, destination) {
  const directory=fs.statSync(source).isDirectory();
  if (directory) {
    fs.mkdirSync(destination,{recursive:true});
    for (const entry of fs.readdirSync(source)) mirror(path.join(source,entry),path.join(destination,entry));
  } else fs.copyFileSync(source,destination);
}

try {
  const delivered = path.join(dist,'Vidnets/App');
  if (!fs.existsSync(path.join(unpacked,'resources/tools/.runtime-ready')) &&
      fs.existsSync(path.join(delivered,'resources/tools/.runtime-ready'))) {
    mirror(delivered,below(unpacked,cache));
  }
  if (!fs.existsSync(path.join(unpacked,'resources/app.asar')) || !fs.existsSync(path.join(unpacked,'resources/tools/.runtime-ready')))
    throw new Error('Run the runtime packaging stage before refreshing its shell');
  // Source/package checks below reject backend or runtime changes rather
  // than silently preserving stale code from an earlier build.
  verify.writeBuildStamp();
  const pkg=JSON.parse(fs.readFileSync(path.join(root,'package.json'),'utf8'));
  const config={...pkg.build,extraResources:[],directories:{...pkg.build.directories,output:path.relative(root,shell)}};
  const shellConfig=path.join(cache,'release-shell-config.json');
  fs.writeFileSync(shellConfig,JSON.stringify(config,null,2));
  run('npx',['electron-builder','--win','--x64','--dir','--publish','never','--config',path.relative(root,shellConfig)]);
  mirror(path.join(shell,'win-unpacked'),below(unpacked,cache));
  for (const name of ['.env.defaults','.env.example','src/index.html','src/landing.html','src/landing.js','src/renderer.js','src/index.css','server/app.py','server/config.py',
    'server/services/auth_service.py','admin-panel/index.html','admin-panel/admin.js','admin-panel/server.py',
    'server/services/generation_request.py','server/services/generation_cost_policy.py','server/services/generation_registry.py',
    'server/services/structured_response_cache.py','server/services/openrouter_llm.py','server/services/openrouter_usage.py',
    'server/services/queue_manager.py','server/services/bounded_http.py','server/services/jev_decision_service.py',
    'server/services/media_asset_policy.py','server/services/openrouter_tts_service.py','server/services/edge_tts_service.py','server/services/transcription_service.py',
    'server/services/youtube_worker_progress.py','server/services/build_info.py',
    'server/services/youtube_pool_renderer.py',
    'server/services/visual_intelligence/frame_extractor.py',
    'src/remotion/components/ExplanatoryDiagram.jsx','src/remotion-player.js',
    'src/remotion/components/MissingSceneMotionGraphic.jsx','src/remotion/components/RecoveryIllustration.jsx',
    'src/remotion/engine/RecoveryContent.js',
    'server/services/delivery_quality.py',
    'server/services/visual_requirement.py',
    'server/services/semantic_edit_engine.py',
    'server/services/research_ledger.py','server/services/research_search_worker.py',
    'server/services/web_image/universal_scraper.py',
    'server/services/visual_intelligence/vision_client.py','server/services/editorial_director/director.py',
    'server/services/delivery_budget.py','server/services/editing_profile.py','server/services/visual_intelligence/semantic_guard.py',
    'server/services/media_resolution_coordinator.py','server/services/pipeline_v2_media_adapter.py','server/services/youtube_pipeline_v2.py',
    'server/services/editorial_director/production_contract.py','server/services/editorial_director/script_analyzer.py','scripts/render-remotion-manifest.js',
    'scripts/remotion-section-join.js','public/update-build.json'])
    mirror(path.join(root,name),below(path.join(unpacked,'resources',name),unpacked));
  verify.prunePackagedRuntimeState();
  verify.editPackagedExecutableIcon();
  verify.verifyPackagedProductionFiles();
  verify.verifyNoPrivateSecretsInPackage();
  verify.verifyPackagedBackendSmoke();
  verify.verifyPackagedRemotionSmoke();
  verify.prunePackagedRuntimeState();
  const installerConfig=path.join(cache,'final-installer-config.json');
  run(process.execPath,['scripts/create-installer-build-config.js',path.relative(root,installerConfig),path.relative(root,installerOutput)]);
  run('npx',['electron-builder','--win','nsis','--x64','--prepackaged',path.relative(root,unpacked),'--publish','never','--config',path.relative(root,installerConfig)],
    {env:{...process.env,ELECTRON_BUILDER_COMPRESSION_LEVEL:'5'}});
  const built=path.join(installerOutput,'Vidnets v1.exe');
  if (fs.statSync(built).size < 10*1024*1024) throw new Error('Incomplete installer');
  const final=path.join(dist,'Vidnets v1.exe'), alias=path.join(dist,'Vidnets.exe');
  // An alias may be a hard link to the prior build. Replace both names before
  // publishing hashes so a second name cannot expose the stale executable.
  for (const file of [final,alias]) fs.rmSync(below(file,dist),{force:true});
  fs.copyFileSync(built,final);
  try {fs.linkSync(final,alias);} catch {fs.copyFileSync(final,alias);}
  for (const [source,target] of [['public/icon.ico','Vidnets-Studio-icon.ico'],['public/icon.png','Vidnets-Studio-logo.png']])
    fs.copyFileSync(path.join(root,source),path.join(dist,target));
  const integrity={schemaVersion:1,product:'Vidnets Studio',version:pkg.version,algorithm:'SHA-256',selfContainedRuntime:true,
    files:['Vidnets v1.exe','Vidnets.exe','Vidnets-Studio-icon.ico','Vidnets-Studio-logo.png'].map(name=>({name}))};
  integrity.buildId=JSON.parse(fs.readFileSync(path.join(root,'public/update-build.json'),'utf8')).buildId;
  integrity.generatedAt=new Date().toISOString();
  integrity.files=integrity.files.map(row=>({...row,size:fs.statSync(path.join(dist,row.name)).size,sha256:hash(path.join(dist,row.name))}));
  fs.writeFileSync(path.join(dist,'distribution-integrity.json'),JSON.stringify(integrity,null,2)+'\n');
  fs.writeFileSync(path.join(dist,'SHA256SUMS.txt'),integrity.files.map(row=>row.sha256+'  '+row.name).join('\n')+'\n');
  const checksum=hash(final);
  for (const name of ['Vidnets v1.exe','Vidnets.exe']) fs.writeFileSync(path.join(dist,name+'.sha256'),checksum+' *'+name+'\n');
  fs.writeFileSync(path.join(dist,'distribution-build.txt'),[
    'Vidnets Studio distribution build',`Built: ${integrity.generatedAt}`,`Build: ${integrity.buildId}`,
    `Installer: ${final}`,`Size: ${fs.statSync(final).size} bytes`,`SHA-256: ${checksum}`,
    'Verified bundled Python, Node, FFmpeg, Remotion browser, backend startup and video export.',
    'Windows 10+ x64. Unsigned installer. Internet and configured providers required for generation.',
  ].join('\r\n'));
  console.log('FINAL RELEASE VERIFIED: '+final);
} catch (error) {console.error(error);process.exitCode=1;}
