const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const Timeline = require('../src/production-timeline');
const file = path.resolve(__dirname,'../src/remotion/engine/SceneSchedule.js');
const fixture = new Module(file,module);
fixture._compile(require('esbuild').transformSync(fs.readFileSync(file,'utf8'),{format:'cjs'}).code,file);
const {sceneFrameWindow, activeSceneEntries} = fixture.exports;

test('frame scheduling preserves exact boundaries, overlaps and original editor indices', () => {
  const scenes = [{from:0,duration_in_frames:90},{from:75,duration_in_frames:60},
                  {from:135,duration_in_frames:30,scene_missing:true}];
  assert.deepEqual(activeSceneEntries(scenes,0).map(r=>r.index),[0]);
  assert.deepEqual(activeSceneEntries(scenes,74).map(r=>r.index),[0]);
  assert.deepEqual(activeSceneEntries(scenes,75).map(r=>r.index),[0,1]);
  assert.deepEqual(activeSceneEntries(scenes,90).map(r=>r.index),[1]);
  assert.deepEqual(activeSceneEntries(scenes,135).map(r=>r.index),[2]);
  assert.deepEqual(activeSceneEntries(scenes,165).map(r=>r.index),[]);
  assert.deepEqual(sceneFrameWindow({start:.1,duration:.2},30),{from:3,duration:6});
});

test('optimized editor preparation matches the visible legacy output at every frame', () => {
  const scenes = Array.from({length:60},(_,i)=>({from:i*90,start:i*3,duration_in_frames:96,
      scene_index:i,narration:`Scene ${i}`,media:{src:`scene-${i}.mp4`}}));
  const tracks = Object.fromEntries(['media','graphics','copy','camera','transitions','captions','sfx'].map(id=>[id,
    {enabled:id!=='copy',clips:scenes.map((s,i)=>({scene_index:i,start_frame:s.from,duration_frames:90,
      source_start_frame:i,asset:id==='media'?s.media.src:undefined,media_type:'video'}))}]));
  const manifest = {fps:30,editor_timeline:{tracks}};
  let prepared = 0;
  for (let frame=0;frame<5490;frame+=7) {
    const expected = scenes.map((s,i)=>Timeline.sceneAtFrame(s,i,frame,manifest)).filter(scene=>
      frame>=scene.from && frame<scene.from+scene.duration_in_frames);
    const actual = activeSceneEntries(scenes,frame).map(({scene,index})=> {
      prepared++; return Timeline.sceneAtFrame(scene,index,frame,manifest);
    });
    assert.deepEqual(actual,expected);
  }
  assert.ok(prepared < 1600,'prepare only the visible scene(s), including transition overlaps');
});

test('malformed schedules are left to existing render validation rather than hidden', () => {
  assert.equal(activeSceneEntries([{from:'bad',duration_in_frames:30}],0).length,1);
});
