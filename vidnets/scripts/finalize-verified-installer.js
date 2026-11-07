// Resume the final compression/publication stage after a smoke-render retry.
// The package must already have passed the refresh checks; no runtime or
// source files are rebuilt or changed by this stage.
const fs=require('fs'), path=require('path'), crypto=require('crypto');
const {spawnSync}=require('child_process');
const verify=require('./build-installer');
const root=path.resolve(__dirname,'..'), cache=path.join(root,'dist-build-cache'),dist=path.join(root,'dist');
const unpacked=path.join(cache,'builder-output/win-unpacked');
const evidence=path.join(root,'artifacts/release_validation_20261006');
const config=path.join(cache,'final-installer-config.json');
const output=path.join(cache,'final-installer');
const digest=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function run(command,args,options={}) {
  const npm=command==='npx';
  const result=spawnSync(npm?'cmd.exe':command,npm?['/d','/s','/c',command,...args]:args,{cwd:root,stdio:'inherit',shell:false,...options});
  if(result.status!==0)throw Error(command+' failed: '+result.status);
}
try {
  const refresh=fs.readFileSync(path.join(evidence,'final-installer-build.log'),'utf8');
  const smoke=fs.readFileSync(path.join(evidence,'final-remotion-retry.log'),'utf8');
  if(!refresh.includes('Packaged Flask startup verified on port') || !smoke.includes('Packaged Remotion smoke render verified'))
    throw Error('Final runtime verification evidence is incomplete');
  const asar=path.join(unpacked,'resources/app.asar');
  if(fs.statSync(asar).mtimeMs>fs.statSync(path.join(evidence,'final-remotion-retry.log')).mtimeMs)
    throw Error('App was rebuilt after the verified smoke render');
  verify.verifyPackagedProductionFiles();
  const archive=require('@electron/asar');
  for(const file of ['src/renderer.js','src/index.css','src/index.html'])
    if(!fs.readFileSync(path.join(root,file)).equals(archive.extractFile(asar,file))) throw Error('Stale shell: '+file);
  verify.prunePackagedRuntimeState();
  run(process.execPath,['scripts/create-installer-build-config.js',path.relative(root,config),path.relative(root,output)]);
  run('npx',['electron-builder','--win','nsis','--x64','--prepackaged',path.relative(root,unpacked),'--publish','never','--config',path.relative(root,config)],
    {env:{...process.env,ELECTRON_BUILDER_COMPRESSION_LEVEL:'1'}});
  const built=path.join(output,'Vidnets v1.exe');
  if(fs.statSync(built).size<10*1024*1024)throw Error('Incomplete installer');
  const final=path.join(dist,'Vidnets v1.exe'),alias=path.join(dist,'Vidnets.exe');
  for(const file of [final,alias]) {
    if(path.dirname(path.resolve(file))!==dist)throw Error('Invalid distribution path');
    fs.rmSync(file,{force:true});
  }
  fs.copyFileSync(built,final);
  try{fs.linkSync(final,alias);}catch{fs.copyFileSync(final,alias);}
  for(const [source,target]of[['public/icon.ico','Vidnets-Studio-icon.ico'],['public/icon.png','Vidnets-Studio-logo.png']])fs.copyFileSync(path.join(root,source),path.join(dist,target));
  const version=JSON.parse(fs.readFileSync(path.join(root,'package.json'),'utf8')).version;
  const buildId=JSON.parse(fs.readFileSync(path.join(unpacked,'resources/public/update-build.json'),'utf8')).buildId;
  const files=['Vidnets v1.exe','Vidnets.exe','Vidnets-Studio-icon.ico','Vidnets-Studio-logo.png'].map(name=>({name,size:fs.statSync(path.join(dist,name)).size,sha256:digest(path.join(dist,name))}));
  const integrity={schemaVersion:1,product:'Vidnets Studio',version,buildId,generatedAt:new Date().toISOString(),algorithm:'SHA-256',selfContainedRuntime:true,files};
  fs.writeFileSync(path.join(dist,'distribution-integrity.json'),JSON.stringify(integrity,null,2)+'\n');
  fs.writeFileSync(path.join(dist,'SHA256SUMS.txt'),files.map(file=>file.sha256+'  '+file.name).join('\n')+'\n');
  for(const name of ['Vidnets v1.exe','Vidnets.exe'])fs.writeFileSync(path.join(dist,name+'.sha256'),files[0].sha256+' *'+name+'\n');
  fs.writeFileSync(path.join(dist,'distribution-build.txt'),[
    'Vidnets Studio distribution build',`Built: ${integrity.generatedAt}`,`Build: ${buildId}`,`Installer: ${final}`,
    `Size: ${files[0].size} bytes`,`SHA-256: ${files[0].sha256}`,
    'Bundled runtime, source equality, credential scan, backend startup and H.264 smoke export verified.',
    'Windows 10+ x64. Unsigned installer. Internet and configured providers required for generation.',
  ].join('\r\n'));
  console.log('FINAL RELEASE VERIFIED: '+final);
}catch(error){console.error(error);process.exitCode=1;}
