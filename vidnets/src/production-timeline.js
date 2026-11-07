/* Shared editor/Remotion track contract. All timing is film-global frames. */
const VidzeoTimeline = (() => {
 const definitions = [['media','video','Media'],['graphics','graphics','Graphics'],['copy','text','Approved text'],['camera','camera','Camera'],['transitions','transition','Transitions'],['narration','audio','Narration'],['captions','text','Captions'],['music','audio','Music'],['sfx','audio','Sound effects']];
 const clone = v => JSON.parse(JSON.stringify(v));
 function importTracks(manifest) {
  const fps = Number(manifest.fps || 30), source = manifest.editing_timeline?.tracks || {};
  const sceneRows = (manifest.scenes || []).map((s,i)=>({scene_index:i,start_frame:Math.round(Number(s.start||0)*fps),duration_frames:Number(s.duration_in_frames)||Math.round(Number(s.duration||1)*fps)}));
  return definitions.map(([id,type,name]) => ({id,type,name,locked:false,visible:manifest.editor_timeline?.tracks?.[id]?.enabled!==false,muted:false,clips:(manifest.editor_timeline?.tracks?.[id]?.clips || source[id] || derive(id,manifest,sceneRows,fps)).map((row,i)=>({id:`production_${id}_${i}`,type,name:row.text || row.component || row.kind || row.sfx_id || name,sceneIndex:row.scene_index,trackOffset:Number(row.start_frame||0)/fps,start:Number(row.source_start_frame||0)/fps,end:Number(row.source_start_frame||0)/fps+Math.max(1,Number(row.duration_frames)||Math.round(Number(row.duration||row.duration_seconds||(id==='music'?manifest.duration:.8)||.8)*fps))/fps,productionPayload:clone(row)}))}));
 }
 function derive(id,m,rows,fps) {
  if(id==='music') return m.audio?.music ? [{...m.audio.music,start_frame:0,duration_frames:m.duration_in_frames||Math.round(m.duration*fps)}]:[];
  return rows.flatMap(row=>{const s=m.scenes[row.scene_index];
   if(id==='copy') return s.editorial_contract?.approved&&s.editorial_contract.display_text?[{...row,text:s.editorial_contract.display_text,start_frame:row.start_frame+Number(s.copy_window?.start_frame||0),duration_frames:s.copy_window?.duration_frames||Math.min(row.duration_frames,Math.round(2*fps))}]:[];
   if(id==='sfx') return (s.sound_cues||[]).map(c=>({...c,scene_index:row.scene_index,start_frame:row.start_frame+Math.round(Number(c.time_local||0)*fps),duration_frames:Math.round(Number(c.duration||c.duration_seconds||.8)*fps)}));
   return [{...row,component:s.primary_visual?.type,text:id==='narration'?s.narration:undefined}];
  });
 }
 function exportTracks(manifest,tracks) {
  const result=clone(manifest),fps=Number(result.fps||30);
  result.editor_timeline={version:1,clock:'film_global_frames',fps,tracks:Object.fromEntries(tracks.filter(t=>definitions.some(d=>d[0]===t.id)).map(t=>[t.id,{enabled:t.visible!==false&&!t.muted,clips:t.clips.map(c=>({...clone(c.productionPayload||{}),scene_index:c.sceneIndex,start_frame:Math.max(0,Math.round(c.trackOffset*fps)),duration_frames:Math.max(1,Math.round((c.end-c.start)*fps)),source_start_frame:Math.max(0,Math.round(c.start*fps))}))}]))};
  return result;
 }
 function active(track,index,frame) {return !track || (track.enabled!==false && track.clips.some(c=>(c.scene_index===undefined||c.scene_index===index)&&frame>=c.start_frame&&frame<c.start_frame+c.duration_frames));}
 function sceneAtFrame(scene,index,frame,manifest) {
  const t=manifest.editor_timeline?.tracks;if(!t)return scene;
  const result={...scene,editor_track_visibility:Object.fromEntries(['media','graphics','copy','camera','transitions','captions'].map(id=>[id,active(t[id],index,frame)]))};
  if(!result.editor_track_visibility.camera){result.camera={motion:'static',disabled:true};result.motion={...(scene.motion||{}),camera:'static'};}
  const mediaClip=t.media?.enabled!==false&&t.media?.clips.filter(c=>c.scene_index===index&&frame>=c.start_frame&&frame<c.start_frame+c.duration_frames).at(-1);
  if(mediaClip)result.editor_media_window={start_frame:Math.max(0,mediaClip.start_frame-Number(scene.from||0)),duration_frames:mediaClip.duration_frames,source_start_frame:Number(mediaClip.source_start_frame||0)};
  if(mediaClip?.asset && mediaClip.media_type){result.media={...scene.media,src:mediaClip.asset,path:mediaClip.asset,type:mediaClip.media_type||scene.media?.type,shots:undefined,source_start:0};result.primary_visual={...scene.primary_visual,asset:result.media};}
  const graphicClip=t.graphics?.enabled!==false&&t.graphics?.clips.find(c=>c.scene_index===index&&frame>=c.start_frame&&frame<c.start_frame+c.duration_frames);
  if(graphicClip)result.editor_graphic_window={start_frame:Math.max(0,graphicClip.start_frame-Number(scene.from||0)),duration_frames:graphicClip.duration_frames};
  if(t.copy){const c=t.copy.enabled!==false&&t.copy.clips.find(c=>c.scene_index===index&&frame>=c.start_frame&&frame<c.start_frame+c.duration_frames);if(c)result.copy_window={start_frame:c.start_frame-Number(scene.from||Math.round(Number(scene.start||0)*manifest.fps)),duration_frames:c.duration_frames};}
  const captionClip=t.captions?.enabled!==false&&t.captions?.clips.find(c=>c.scene_index===index&&frame>=c.start_frame&&frame<c.start_frame+c.duration_frames);
  if(captionClip)result.editor_caption_source_seconds=(frame-captionClip.start_frame+Number(captionClip.source_start_frame||0))/manifest.fps+Number(scene.start||0);
  if(t.sfx)result.sound_cues=t.sfx.enabled===false?[]:t.sfx.clips.filter(c=>c.scene_index===index).map(c=>({...c,time_local:(c.start_frame-Number(scene.from||0))/manifest.fps,duration:c.duration_frames/manifest.fps,trim_start_seconds:Number(c.trim_start_seconds||0)+Number(c.source_start_frame||0)/manifest.fps}));
  return result;
 }
 return {definitions,importTracks,exportTracks,active,sceneAtFrame};
})();
if(typeof window!=='undefined')window.VidzeoTimeline=VidzeoTimeline;
if(typeof module!=='undefined')module.exports=VidzeoTimeline;
