import React from 'react';
import { AbsoluteFill, Img, Sequence, interpolate, staticFile, useCurrentFrame, useVideoConfig } from 'remotion';
import { AnimatedMap } from './AnimatedMap';
import { readableInk } from './TextCard';
import { SignedBars } from './SignedBars';
import { ExplanatoryDiagram } from './ExplanatoryDiagram';

const safePercent = (value, fallback = 0) => Math.max(0, Math.min(100, Number.isFinite(Number(value)) ? Number(value) : fallback));
const safeColor = (value, fallback = '#F4F0E6') => /^#[0-9a-f]{6}$/i.test(String(value || '')) ? value : fallback;
const safeNumber = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const shotSource = (shot) => String(shot?.src || shot?.path || shot?.asset_path || '');
const videoAsset = /\.(?:mp4|webm|mov|mkv|avi|m4v)(?:[?#]|$)/i;

function safeStaticAsset(assetId) {
  if (!assetId || typeof assetId !== 'string') return '';
  const trimmed = assetId.trim();
  if (!trimmed || trimmed.includes('..') || /^(?:https?:|data:|blob:|file:)/i.test(trimmed)) return trimmed;
  if (!trimmed.includes('/') && !trimmed.includes('\\') && !trimmed.includes('.')) return '';
  if (/\.(?:wav|mp3|ogg|aac|m4a|flac)(?:[?#]|$)/i.test(trimmed)) return '';
  return staticFile(trimmed.replace(/^\/+/, ''));
}

function progressAt(frame, start, duration) {
  const local = frame - start;
  const reveal = Math.min(Math.max(1, duration), 18);
  return interpolate(local, [0, reveal], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
}

function shapeStyle(shape) {
  if (shape === 'circle') return { borderRadius: '50%' };
  if (shape === 'arch') return { borderRadius: '48% 48% 12px 12px' };
  if (shape === 'hexagon') return { clipPath: 'polygon(8% 0, 92% 0, 100% 50%, 92% 100%, 8% 100%, 0 50%)' };
  if (shape === 'rounded') return { borderRadius: 18 };
  return {};
}

function layerStyle(layer, frame, fps) {
  const start = Math.max(0, safeNumber(layer.start_frame));
  const duration = Math.max(1, safeNumber(layer.duration_frames, fps));
  const progress = progressAt(frame, start, duration);

  const motion = String(layer.motion || 'none');
  const translate = motion === 'rise' ? `translate3d(0, ${(1 - progress) * 28}px, 0)`
    : motion === 'slide_left' ? `translate3d(${(1 - progress) * 42}px, 0, 0)`
      : motion === 'slide_right' ? `translate3d(${(1 - progress) * -42}px, 0, 0)`
        : motion === 'scale' ? `scale(${0.88 + progress * 0.12})`
          : motion === 'pulse' ? `scale(${1 + Math.sin(progress * Math.PI) * 0.055})`
            : motion === 'parallax' ? `translate3d(${(1 - progress) * 18}px, ${(1 - progress) * 7}px, 0)` : undefined;
  const local = Math.max(0, frame - start);
  return {
    position: 'absolute',
    left: `${safePercent(layer.x)}%`, top: `${safePercent(layer.y)}%`,
    width: `${safePercent(layer.width)}%`, height: `${safePercent(layer.height)}%`,
    opacity: motion === 'fade' || motion === 'wipe' ? progress : 1,
    transform: translate,
    clipPath: motion === 'wipe' ? `inset(0 ${(1 - progress) * 100}% 0 0)` : undefined,
    transformOrigin: motion === 'draw' ? 'left center' : 'center',
    '--recipe-progress': progress,
    '--recipe-local-frame': local,
  };
}

function MediaMontage({ scene, layer, color }) {
  const shots = Array.isArray(scene?.media?.shots) ? scene.media.shots : [];
  const unique = [];
  const seen = new Set();
  for (const shot of shots) {
    const src = shotSource(shot);
    const rejected = ['rejected', 'failed', 'blocked'].includes(String(shot?.verification_status || shot?.status || '').toLowerCase());
    if (!src || rejected || videoAsset.test(src) || seen.has(src)) continue;
    seen.add(src);
    unique.push({ shot, src });
    if (unique.length === 4) break;
  }
  if (unique.length < 2) return null;
  const mask = shapeStyle(layer.shape || 'rounded');
  return <div style={{ width: '100%', height: '100%', display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gridTemplateRows: unique.length === 2 ? 'minmax(0, 1fr)' : 'repeat(2, minmax(0, 1fr))', gap: '1.4%', padding: '1.2%', boxSizing: 'border-box', background: 'rgba(8, 16, 22, .28)' }}>
    {unique.map(({ shot, src }, index) => <div key={`${src}-${index}`} style={{ position: 'relative', overflow: 'hidden', ...(unique.length === 3 && index === 0 ? { gridRow: 'span 2' } : {}), ...mask, border: `1px solid ${color}88`, background: '#111820' }}>
      <Img src={src} style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: `${safePercent(shot.focal_x, layer.focal_x ?? 50)}% ${safePercent(shot.focal_y, layer.focal_y ?? 50)}%` }} />
      <div style={{ position: 'absolute', inset: '62% 0 0', background: 'linear-gradient(transparent, rgba(4, 10, 14, .74))' }} />
      {shot.label && <div style={{ position: 'absolute', left: '4%', bottom: '4%', color: '#fff', font: '700 clamp(10px, .9vw, 17px) "DM Sans", sans-serif', textShadow: '0 1px 8px #000' }}>{String(shot.label).slice(0, 44)}</div>}
    </div>)}
  </div>;
}

function ChartLayer({ layer, color, textColor, frame }) {
  const data = Array.isArray(layer.data) ? layer.data.filter((item) => item && Number.isFinite(Number(item.value))) : [];
  if (data.length < 2) return null;
  const max = Math.max(1, ...data.map((item) => Number(item.value)));
  const progress = progressAt(frame, safeNumber(layer.start_frame), Math.max(1, safeNumber(layer.duration_frames, 30)));
  const vertical = layer.orientation === 'vertical';
  if (data.some((row) => Number(row.value) < 0)) {
    return <SignedBars rows={data.map((row) => ({ label: row.label, number: Number(row.value), value: Number(row.value).toLocaleString('en-US') }))} progress={progress} color={color} ink={textColor} />;
  }
  return <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: vertical ? 'column' : 'row', alignItems: vertical ? 'stretch' : 'flex-end', justifyContent: 'space-evenly', gap: '2%', padding: '2.5%', boxSizing: 'border-box', color: textColor, fontFamily: 'DM Sans, sans-serif' }}>
    {data.map((item, index) => {
      const rowProgress = Math.max(0, Math.min(1, progress * data.length - index * 0.7));
      const size = Math.max(0, Number(item.value) / max * 74) * rowProgress;
      const shown = Math.round(Number(item.value) * rowProgress);
      return <div key={`${item.label}-${index}`} style={{ flex: 1, minWidth: 0, height: '100%', display: 'flex', flexDirection: vertical ? 'row' : 'column', alignItems: 'center', justifyContent: 'flex-end', gap: 6 }}>
        <span style={{ font: '750 clamp(28px, 2.3vw, 44px) "JetBrains Mono", monospace', opacity: rowProgress }}>{shown.toLocaleString()}</span>
        <div style={{ width: vertical ? `${size}%` : '72%', height: vertical ? '72%' : `${size}%`, alignSelf: vertical ? 'flex-start' : 'center', background: index === 0 ? color : `${color}B8`, transformOrigin: vertical ? 'left center' : 'center bottom', borderRadius: 3 }} />
        <span style={{ maxWidth: '100%', textAlign: vertical ? 'left' : 'center', fontSize: 'clamp(22px, 1.7vw, 32px)', fontWeight: 650, overflowWrap: 'anywhere' }}>{String(item.label).slice(0, 36)}</span>
      </div>;
    })}
  </div>;
}

function TimelineLayer({ layer, color, textColor, frame }) {
  const rows = Array.isArray(layer.data) ? layer.data.slice(0, 8) : [];
  if (rows.length < 2) return null;
  const vertical = layer.orientation === 'vertical';
  const progress = progressAt(frame, safeNumber(layer.start_frame), Math.max(1, safeNumber(layer.duration_frames, 30)));
  return <div style={{ position: 'relative', width: '100%', height: '100%', display: 'flex', flexDirection: vertical ? 'column' : 'row', alignItems: 'center', justifyContent: 'space-around', color: textColor, fontFamily: 'DM Sans, sans-serif', padding: '3%', boxSizing: 'border-box' }}>
    <div style={{ position: 'absolute', [vertical ? 'left' : 'top']: '50%', [vertical ? 'top' : 'left']: '8%', [vertical ? 'width' : 'height']: '2px', [vertical ? 'height' : 'width']: '84%', background: `${color}77`, transform: vertical ? 'translateX(-50%)' : 'translateY(-50%)', transformOrigin: vertical ? 'center top' : 'left center', scale: vertical ? `1 ${progress}` : `${progress} 1` }} />
    {rows.map((item, index) => {
      const shown = Math.max(0, Math.min(1, progress * rows.length - index * 0.65));
      return <div key={`${item.label}-${index}`} style={{ zIndex: 1, display: 'flex', flexDirection: vertical ? 'row' : 'column', alignItems: 'center', gap: 8, maxWidth: vertical ? '85%' : `${Math.floor(86 / rows.length)}%`, opacity: shown, transform: `translateY(${(1 - shown) * 8}px)` }}>
        <span style={{ width: 13, height: 13, flex: '0 0 13px', borderRadius: '50%', background: color, boxShadow: `0 0 18px ${color}88`, border: '3px solid #101820' }} />
        <div style={{ textAlign: vertical ? 'left' : 'center', fontWeight: 700, fontSize: 'clamp(24px, 2vw, 38px)' }}>
          {item.value != null && <div style={{ color, font: '700 clamp(22px, 1.7vw, 32px) "JetBrains Mono", monospace' }}>{String(item.value)}</div>}
          <div>{String(item.label).slice(0, 42)}</div>
        </div>
      </div>;
    })}
  </div>;
}

function ComparisonLayer({ layer, color, textColor, frame }) {
  const rows = Array.isArray(layer.data) ? layer.data.slice(0, 2) : [];
  if (rows.length < 2 || rows.some((item) => !Number.isFinite(Number(item.value)))) return null;
  const max = Math.max(1, ...rows.map((item) => Number(item.value)));
  const progress = progressAt(frame, safeNumber(layer.start_frame), Math.max(1, safeNumber(layer.duration_frames, 30)));
  return <div style={{ width: '100%', height: '100%', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '3%', padding: '3%', boxSizing: 'border-box', alignItems: 'stretch', color: textColor, fontFamily: 'DM Sans, sans-serif' }}>
    {rows.map((item, index) => {
      const shown = Math.max(0, Math.min(1, progress * 2 - index * 0.55));
      const pct = Math.max(2, Number(item.value) / max * 100) * shown;
      return <div key={`${item.label}-${index}`} style={{ position: 'relative', display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', padding: '5%', overflow: 'hidden', background: 'linear-gradient(145deg, rgba(13,22,30,.96), rgba(22,34,42,.76))', borderTop: `2px solid ${index === 0 ? color : `${color}88`}`, ...shapeStyle(layer.shape || 'rounded') }}>
        <div style={{ position: 'absolute', left: 0, bottom: 0, width: '100%', height: `${pct}%`, background: `linear-gradient(0deg, ${color}45, ${color}08)`, transformOrigin: 'bottom', opacity: .8 }} />
        <strong style={{ zIndex: 1, font: '850 clamp(22px, 4.3vw, 82px) "Archivo Black", sans-serif', letterSpacing: '-.04em' }}>{Math.round(Number(item.value) * shown).toLocaleString()}</strong>
        <span style={{ zIndex: 1, marginTop: 8, fontSize: 'clamp(24px, 2vw, 38px)', fontWeight: 700 }}>{String(item.label).slice(0, 42)}</span>
      </div>;
    })}
  </div>;
}

function RecipeLayer({ layer, scene, theme, children, canvasWidth, canvasHeight }) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const discSize = Math.min((canvasWidth || width) * safePercent(layer.width) / 100, (canvasHeight || height) * safePercent(layer.height) / 100);
  const style = layerStyle(layer, frame, fps);
  const kind = String(layer.kind || '');
  const requestedColor = safeColor(layer.color, theme?.palette?.accent || '#F2C14E');
  const color = ['text', 'counter'].includes(kind)
    ? readableInk(requestedColor, theme?.palette?.background || '#101820') : requestedColor;
  const textColor = readableInk(theme?.palette?.primary, theme?.palette?.background || '#101820');
  const start = Math.max(0, safeNumber(layer.start_frame));
  const duration = Math.max(1, safeNumber(layer.duration_frames, fps));
  const progress = progressAt(frame, start, duration);

  // Semantic information holds after its reveal. Later layers must still
  // respect their narration-aligned start; only brief text expires locally.
  if (frame < start || (kind === 'text' && frame >= start + Math.min(duration, Math.round(2.4 * fps)))) return null;

  if (kind === 'group') return <div style={{ ...style, overflow: layer.clip ? 'hidden' : 'visible', ...(layer.clip ? shapeStyle(layer.shape) : {}) }}>{children}</div>;

  if (kind === 'map') {
    if (!scene?.map_shot?.enabled) return null;
    const locations = scene.map_shot.location_names || [];
    const primary = scene.primary_visual || {};
    const country = primary.props?.country || locations.find((name) => /^(?:japan|india|united states|united kingdom|belgium|china|russia|ukraine|france|germany)$/i.test(name)) || '';
    return <div style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}><AnimatedMap scene={scene} visualDNA={scene.visual_dna || {}} props={{ ...(primary.props || {}), map_shot: scene.map_shot, country, location: locations[locations.length - 1] || primary.props?.location }} /></div>;
  }

  let content = null;
  if (kind === 'path') {
    const points = Array.isArray(layer.points) ? layer.points.slice(0,32) : [];
    if (points.length < 2) return null;
    const contourLength = points.slice(1).reduce((length,p,i)=>length+Math.hypot(safePercent(p.x)-safePercent(points[i].x),safePercent(p.y)-safePercent(points[i].y)),0);
    const PathShape = layer.closed ? 'polygon' : 'polyline';
    const length = contourLength + (layer.closed ? Math.hypot(points[0].x-points[points.length-1].x,points[0].y-points[points.length-1].y) : 0);
    content = <svg width="100%" height="100%" viewBox="0 0 100 100" preserveAspectRatio="none" style={{ overflow: 'visible' }}><PathShape points={points.map((p)=>`${safePercent(p.x)},${safePercent(p.y)}`).join(' ')} fill={layer.fill_color ? safeColor(layer.fill_color,color) : 'none'} fillOpacity={progress} stroke={color} strokeWidth="1.2" strokeDasharray={progress >= .999 ? undefined : `${length} ${length}`} strokeDashoffset={length*(1-progress)} strokeLinecap="round" strokeLinejoin="round" /></svg>;
  }
  else if (kind === 'media_montage') content = <MediaMontage scene={scene} layer={layer} color={color} />;
  else if (kind === 'chart') content = <ChartLayer layer={layer} color={color} textColor={textColor} frame={frame} />;
  else if (kind === 'timeline') content = <TimelineLayer layer={layer} color={color} textColor={textColor} frame={frame} />;
  else if (kind === 'diagram') content = <Sequence from={Math.round(start)} layout="none"><ExplanatoryDiagram scene={{ ...scene, duration_in_frames: Math.max(1, Number(scene.duration_in_frames || Number(scene.duration || 5)*fps)-start), motion_cues: (scene.motion_cues || []).map((cue)=>({ ...cue, time_local: Math.max(0, Number(cue.time_local || 0)-start/fps) })) }} visualDNA={{ palette: { ...theme.palette, accent: color, primary: textColor }, typography: { display: theme.display_font, body: theme.font_family } }} props={{ links: layer.links, steps: (layer.data || []).map((row)=>({ ...row, title: row.label })), variant: String(layer.variant || scene.primary_visual?.variant || scene.graphic?.variant || 'step_by_step_process'), transparent_canvas: true }} /></Sequence>;
  else if (kind === 'comparison') content = <ComparisonLayer layer={layer} color={color} textColor={textColor} frame={frame} />;
  else if (kind === 'counter') {
    const target = safeNumber(layer.data?.[0]?.value, NaN);
    if (Number.isFinite(target)) content = <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', color, font: '900 clamp(38px, 9vw, 168px) "Archivo Black", sans-serif', letterSpacing: '-.055em' }}>{Math.round(target * progress).toLocaleString()}{layer.text ? <span style={{ marginLeft: '.12em', font: '700 clamp(14px, 2vw, 36px) "JetBrains Mono", monospace', letterSpacing: 0 }}>{layer.text}</span> : null}</div>;
  } else if (kind === 'image') {
    const assetId = String(layer.asset_id || '').replace(/\\/g, '/').replace(/^\/+/, '');
    const sceneMedia = scene?.media || {};
    const seenImages = new Set();
    const imageShots = (Array.isArray(sceneMedia.shots) ? sceneMedia.shots : []).filter((shot) => {
      const source = shotSource(shot);
      if (!source || videoAsset.test(source) || seenImages.has(source)
        || ['rejected','failed','blocked'].includes(shot.verification_status || shot.verification)) return false;
      seenImages.add(source);
      return true;
    });
    const primarySource = String(sceneMedia.src || sceneMedia.path || '');
    if (!imageShots.length && primarySource && !videoAsset.test(primarySource)) imageShots.push(sceneMedia);
    const selectedImage = layer.image_index !== undefined && layer.image_index !== null
      ? shotSource(imageShots[Number(layer.image_index)]) : '';
    const source = layer.image_index !== undefined && layer.image_index !== null ? selectedImage
      : assetId && !assetId.split('/').includes('..') && !assetId.includes('://')
      ? safeStaticAsset(assetId)
      : (!videoAsset.test(primarySource) ? primarySource : '') || shotSource(imageShots[0]);
    if (source) {
      content = <Img src={source} style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: `${safePercent(layer.focal_x, 50)}% ${safePercent(layer.focal_y, 50)}%`, ...shapeStyle(layer.shape) }} />;
    }
  } else if (kind === 'text') content = <div style={{ width: '100%', height: '100%', color, fontFamily: theme?.display_font || 'Archivo Black, Inter, sans-serif', fontWeight: 850, textShadow: '0 3px 4px rgba(0,0,0,.6), 0 10px 26px rgba(0,0,0,.5)', fontSize: 'clamp(22px, 4vw, 76px)', lineHeight: 1.02, letterSpacing: '-.04em', overflowWrap: 'break-word', display: 'flex', alignItems: 'center' }}>{layer.text}</div>;
  else if (kind === 'line') content = <div style={{ width: '100%', height: Math.max(2, safeNumber(layer.height) / 100 * 720), background: color, transformOrigin: 'left center', transform: `scaleX(${layer.motion === 'draw' ? progress : 1})` }} />;
  else if (kind === 'sphere') content = <div style={{width:'100%',height:'100%',display:'grid',placeItems:'center'}}><div style={{ width: discSize, height: discSize, borderRadius: '50%', background: `radial-gradient(circle at 28% 26%, #FFFFFF 0%, ${color} 23%, ${color}88 52%, #061019 86%)`, boxShadow: `inset -18px -12px 35px #0008, 0 0 45px ${color}30`, transform: `scale(${.92+.08*progress})`, opacity: progress }} /></div>;
  else if (kind === 'cone') content = <div style={{ width: '100%', height: '100%', clipPath: layer.orientation === 'vertical' ? 'polygon(50% 0, 100% 100%, 0 100%)' : 'polygon(0 50%, 100% 0, 100% 100%)', background: `linear-gradient(${layer.orientation === 'vertical' ? '180deg' : '90deg'}, ${color}66, ${color}08)`, transformOrigin: layer.orientation === 'vertical' ? 'top center' : 'left center', transform: layer.orientation === 'vertical' ? `scaleY(${progress})` : `scaleX(${progress})` }} />;
  else if (kind === 'arrow') content = <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{width:'100%',height:'100%',overflow:'visible',clipPath:layer.orientation === 'vertical' ? `inset(0 0 ${(1-progress)*100}% 0)` : `inset(0 ${(1-progress)*100}% 0 0)`}}><path d={layer.orientation === 'vertical' ? 'M50 0 L50 90 M35 75 L50 90 L65 75' : 'M0 50 L90 50 M75 35 L90 50 L75 65'} fill="none" stroke={color} strokeWidth="3" vectorEffect="non-scaling-stroke" /></svg>;
  else if (kind === 'circle') content = <div style={{width:'100%',height:'100%',display:'grid',placeItems:'center'}}><div style={{ width: discSize, height: discSize, border: `2px solid ${color}`, background: `${color}22`, borderRadius:'50%' }} /></div>;
  else if (kind === 'rect') content = <div style={{ width: '100%', height: '100%', background: `${color}22`, borderBottom: `2px solid ${color}`, ...shapeStyle(layer.shape) }} />;

  if (!content) return null;
  return <div style={{ ...style, ...(kind === 'circle' ? shapeStyle(layer.shape || 'circle') : {}) }}>{content}</div>;
}

/** Interprets a constrained visual recipe; generated source code is never evaluated. */
export function DeclarativeRecipe({ recipe = {}, scene = {}, theme = {} }) {
  const { width, height } = useVideoConfig();
  const layers = Array.isArray(recipe.layers) ? recipe.layers.slice(0, 16) : [];
  if (!layers.length) return null;
  // Actor labels and declared connections must remain above the illustrated
  // objects; a large sphere cannot erase its smaller shadow's label.
  const ordered = [...layers.filter((layer)=>layer.kind!=='diagram'), ...layers.filter((layer)=>layer.kind==='diagram')];
  const renderChildren = (parentId='', w=width, h=height, depth=0) => depth > 4 ? null : ordered
    .filter((layer)=>String(layer.parent_id || '') === parentId)
    .map((layer,index)=><RecipeLayer key={layer.id || `${parentId}-${index}`} layer={layer} scene={scene} theme={theme} canvasWidth={w} canvasHeight={h}>
      {layer.kind === 'group' ? renderChildren(layer.id, w*safePercent(layer.width)/100, h*safePercent(layer.height)/100, depth+1) : null}
    </RecipeLayer>);
  return <AbsoluteFill style={{ pointerEvents: 'none', overflow: 'hidden' }}>
    {renderChildren()}
  </AbsoluteFill>;
}
