import React from 'react';
import { AbsoluteFill, Easing, Img, OffthreadVideo, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { documentaryTypography } from '../engine/DocumentaryTypography';
import { cueStartFrame, safeFadeOutRange, spokenPhraseFrame } from '../engine/DirectorMotion';
import { MissingSceneMotionGraphic } from './MissingSceneMotionGraphic';
import { resolveMediaKind } from '../engine/MediaKind';
import { isRecoveryMedia } from '../engine/RecoveryMedia';
import { MediaShotSequence } from './MediaShotSequence';
import { SmartImage } from './SmartImage';

const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
const hasDiagramGlyph = (label) => /aircraft|airplane|jetliner|\bplane\b|flight|airport|\bport\b|harbou?r|\bdock\b|cargo|container|ship|vessel|boat|parachut|passenger|crew|people|worker|person|witness|ransom|cash|money|dollar|payment|document|note|letter|record|evidence|report|charg|supercharger|batter|powerwall|megapack|storage|solar|photovoltaic|software|update|interface|code|computer|electric|energy|power|signal|vehicle|\bcar\b|motor|engine|turbine|gear|machine|mechanism/i.test(clean(label));

// A diagram's actors must be recognizable and tied to its narrated labels.
// Unknown actors receive an ordered step badge, never a fake object model.
function DiagramGlyph({ label, x, y, accent, ink, index, size = 80 }) {
  const text = clean(label).toLowerCase();
  let shape = null;
  if (/aircraft|airplane|jetliner|plane\b|flight|airport/.test(text)) shape = <path d="M46 9L54 9L57 40L91 61L91 68L57 56L55 79L68 88L68 94L50 88L32 94L32 88L45 79L43 56L9 68L9 61L43 40Z" fill={ink} />;
  else if (/\bport\b|harbou?r|\bdock\b/.test(text)) shape = <><path d="M22 83V17H81M22 32L73 17M70 18V57" fill="none" stroke={ink} strokeWidth="5"/><rect x="57" y="60" width="27" height="19" fill={accent}/><path d="M8 88H92" stroke={accent} strokeWidth="4"/></>;
  else if (/cargo|container/.test(text)) shape = <><rect x="12" y="32" width="76" height="45" rx="2" fill="none" stroke={ink} strokeWidth="4"/>{[24,37,50,63,76].map(cx=><path key={cx} d={`M${cx} 37V72`} stroke={accent} strokeWidth="3"/>)}</>;
  else if (/ship|vessel|boat/.test(text)) shape = <><path d="M10 61H90L75 84H28Z" fill={ink}/><path d="M23 57V35H60V57M63 57V22H77V57" fill="none" stroke={accent} strokeWidth="5"/><path d="M8 91Q18 82 28 91T48 91T68 91T88 91" fill="none" stroke={accent} strokeWidth="3"/></>;
  else if (/parachut/.test(text)) shape = <><path d="M12 44A38 38 0 0 1 88 44Q69 30 50 44Q31 30 12 44Z" fill={accent}/><path d="M12 44L45 82M88 44L55 82M50 44V82" stroke={ink} strokeWidth="3"/><circle cx="50" cy="85" r="6" fill={ink}/></>;
  else if (/passenger|crew|people|worker|person|witness/.test(text)) shape = <>{[27,50,73].map((cx,i)=><g key={i}><circle cx={cx} cy={i===1?23:30} r="9" fill={i===1?accent:ink}/><path d={`M${cx-12} 79V${i===1?43:50}Q${cx} ${i===1?31:38} ${cx+12} ${i===1?43:50}V79`} fill={i===1?accent:ink}/></g>)}</>;
  else if (/ransom|cash|money|dollar|payment/.test(text)) shape = <><rect x="9" y="30" width="82" height="45" rx="3" fill="none" stroke={ink} strokeWidth="4"/><circle cx="50" cy="52" r="14" fill={accent}/><path d="M20 43V38H28M80 62V67H72" stroke={accent} fill="none" strokeWidth="3"/></>;
  else if (/document|note|letter|record|evidence|report/.test(text)) shape = <><path d="M24 12H65L80 28V89H24Z" fill="none" stroke={ink} strokeWidth="4"/><path d="M65 12V28H80M36 43H68M36 55H68M36 67H60" fill="none" stroke={accent} strokeWidth="4"/></>;
  else if (/charg|supercharger/.test(text)) shape = <><path d="M24 88V12H66V88Z" fill="none" stroke={ink} strokeWidth="4"/><path d="M66 31H77V64Q88 80 90 63V24" fill="none" stroke={accent} strokeWidth="4"/><path d="M48 25L35 48H48L39 68L57 41H44Z" fill={accent}/><path d="M18 90H73" stroke={ink} strokeWidth="4"/></>;
  else if (/batter|powerwall|megapack|storage/.test(text)) shape = <><rect x="17" y="18" width="66" height="68" rx="6" fill="none" stroke={ink} strokeWidth="4"/><path d="M37 18V9H63V18" fill="none" stroke={ink} strokeWidth="4"/>{[29,46,63].map(cy=><rect key={cy} x="29" y={cy} width="42" height="11" fill={accent} opacity={.35+(cy-29)/70}/>)}<path d="M40 82H60" stroke={accent} strokeWidth="3"/></>;
  else if (/solar|photovoltaic/.test(text)) shape = <><path d="M19 30H81L93 73H7Z" fill="none" stroke={ink} strokeWidth="4"/><path d="M35 30L29 73M65 30L71 73M13 52H87M50 30V73M50 73V89M30 89H70" stroke={accent} strokeWidth="3"/><circle cx="83" cy="12" r="8" fill={accent}/></>;
  else if (/software|update|interface|code|computer/.test(text)) shape = <><rect x="8" y="17" width="84" height="62" rx="4" fill="none" stroke={ink} strokeWidth="4"/><path d="M8 32H92M34 44L23 55L34 66M66 44L77 55L66 66M56 42L44 68M40 88H60M50 79V88" fill="none" stroke={accent} strokeWidth="3"/></>;
  else if (/electric|energy|power|signal/.test(text)) shape = <path d="M57 6L19 54H45L33 94L81 39H55Z" fill={accent} stroke={ink} strokeWidth="2"/>;
  else if (/vehicle|car\b|motor/.test(text)) shape = <><path d="M11 66V49L25 43L35 24H69L81 43L91 49V66Z" fill="none" stroke={ink} strokeWidth="4"/><path d="M30 43H74M48 25V43" stroke={accent} strokeWidth="3"/><circle cx="28" cy="68" r="10" fill={accent}/><circle cx="74" cy="68" r="10" fill={accent}/></>;
  else if (/engine|turbine|gear|machine|mechanism/.test(text)) shape = <><circle cx="50" cy="50" r="28" fill="none" stroke={ink} strokeWidth="8"/>{[0,45,90,135].map(angle=><path key={angle} d="M50 12V25M50 75V88" transform={`rotate(${angle} 50 50)`} stroke={accent} strokeWidth="9"/>)}<circle cx="50" cy="50" r="9" fill={accent}/></>;
  // Unsupported concepts retain their grounded label. A crossed-out box
  // reads as missing artwork and contributes no explanation over footage.
  if (!shape) return null;
  return <svg x={x-size/2} y={y-size/2} width={size} height={size} viewBox="0 0 100 100">{shape}</svg>;
}
export function diagramLayout(variant, count) {
  if (/hub|network|radial/.test(variant)) return { nodes: [[50,45], ...[[20,24],[80,24],[50,78]].slice(0,count-1)], links: Array.from({length: count-1},(_,i)=>[0,i+1]) };
  if (/hierarch|tree/.test(variant)) return { nodes: [[50,27], ...Array.from({length:count-1},(_,i)=>[20+i*60/Math.max(1,count-2),65])], links: Array.from({length:count-1},(_,i)=>[0,i+1]) };
  if (/cause|effect/.test(variant)) return {
    kind: 'cause_effect',
    nodes: Array.from({length:count},(_,i)=>[14+i*72/Math.max(1,count-1),50]),
    links: Array.from({length:count-1},(_,i)=>[i,i+1]),
  };
  if (/step|process/.test(variant)) return {
    kind: 'process',
    nodes: Array.from({length:count},(_,i)=>[23,18+i*64/Math.max(1,count-1)]),
    links: Array.from({length:count-1},(_,i)=>[i,i+1]),
  };
  return { kind: 'network', nodes: Array.from({length:count},(_,i)=>[16+i*68/Math.max(1,count-1),i%2 ? 62 : 36]), links: Array.from({length:count-1},(_,i)=>[i,i+1]) };
}

/** Narrated actors + relationship paths, assembled in space rather than card rows. */
export function ExplanatoryDiagram({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();
  const source = clean(scene.narration).toLowerCase();
  const directed = scene.director_treatment_enabled === true;
  const copyContract = props.node_copy_contract || scene.primary_visual?.props?.node_copy_contract || {};
  const approvedLabels = new Set(['llm', 'llm_scene_repair', 'system_clause_extractor', 'narrated_physical_relationship'].includes(copyContract.source) && copyContract.approved === true
    ? (copyContract.nodes || []).filter((node) => clean(node.label) && clean(node.source_span)
      && source.includes(clean(node.source_span).toLowerCase())
      && clean(node.source_span).toLowerCase().includes(clean(node.label).toLowerCase()))
      .map((node) => clean(node.label).toLowerCase()) : []);
  const steps = (Array.isArray(props.steps) ? props.steps : []).map((s)=>{
    // Director steps commonly arrive with a concise `desc`; treat it as a
    // candidate actor label, then keep the existing exact narration/provenance
    // checks below as the authority for whether it may be rendered.
    const label = clean(s.label || s.title || s.desc);
    const evidence = clean(s.source_span || s.description || s.desc);
    return { label: directed ? (approvedLabels.has(label.toLowerCase()) ? label : '')
      : (label && source.includes(label.toLowerCase()) ? label : evidence), evidence, x: s.x, y: s.y, labelY: s.label_y, hideMarker: s.hide_marker === true };
  })
    .filter((s)=>s.label && s.label.split(' ').length<=20 && (!source || source.includes(s.label.toLowerCase()))).slice(0,4);
  if (steps.length < 2 || props.no_data === true) {
    const sourceMedia = scene.media || scene.primary_visual?.asset || {};
    if (sourceMedia.src || sourceMedia.path) return <SmartImage scene={scene} visualDNA={visualDNA} props={{ variant: 'full_bleed_cinematic' }} asset={sourceMedia} />;
    return <MissingSceneMotionGraphic scene={scene} visualDNA={visualDNA} />;
  }
  const variant = String(props.variant || scene.primary_visual?.variant || 'step_by_step_process');
  const layout = diagramLayout(variant,steps.length);
  layout.kind = layout.kind || (/hierarch|tree/.test(variant) ? 'hierarchy' : /hub|network|radial/.test(variant) ? 'network' : 'process');
  steps.forEach((step,i)=>{
    if (Number.isFinite(Number(step.x)) && Number.isFinite(Number(step.y))) layout.nodes[i]=[(Number(step.x)-6)/.88,(Number(step.y)-9)/.75];
  });
  // A process grammar is usually vertical, but directors can supply horizontal
  // coordinates for a short cause → result beat. Keep labels attached to their
  // own node in that case instead of stacking both labels in the vertical lane.
  const nodeX = layout.nodes.map(([x])=>x);
  const nodeY = layout.nodes.map(([,y])=>y);
  const horizontalProcess = layout.kind === 'process'
    && Math.max(...nodeY) - Math.min(...nodeY) < 12
    && Math.max(...nodeX) - Math.min(...nodeX) > 30;
  if (Array.isArray(props.links) && props.links.length > 0) {
    const links = props.links.map((link)=>[steps.findIndex((step)=>step.label === link.from),steps.findIndex((step)=>step.label === link.to)])
      .filter(([a,b])=>a>=0 && b>=0 && a!==b);
    layout.links = links;
  }
  const palette = visualDNA.palette || {};
  const accent = palette.accent || '#E8B75C';
  const ink = palette.primary || '#F5F3EA';
  const fonts = documentaryTypography(visualDNA);
  const duration = Math.max(1,Number(scene.duration_in_frames || Number(scene.duration || 5)*fps));
  const start = cueStartFrame(scene,'steps',fps,0);
  const starts = steps.map((s,i)=> {
    const spoken = spokenPhraseFrame(scene, s.label, fps);
    if (spoken !== null) return spoken;
    const cue = (scene.motion_cues || []).find((c)=>clean(c.anchor_text).toLowerCase().includes(s.label.toLowerCase()) && Number.isFinite(Number(c.time_local)));
    return cue ? Math.round(Number(cue.time_local)*fps) : Math.min(Math.max(0,duration-Math.round(.9*fps)),start+Math.round(i*.52*fps));
  });
  const enter = (at, length=.38)=>interpolate(frame,[at,at+Math.max(1,Math.round(length*fps))],[0,1],{easing:Easing.out(Easing.cubic),extrapolateLeft:'clamp',extrapolateRight:'clamp'});
  const copyWindow = scene.copy_window || {};
  const highlightActive = Number(copyWindow.duration_frames) > 0 && frame >= Number(copyWindow.start_frame || 0)
    && frame < Number(copyWindow.start_frame || 0) + Number(copyWindow.duration_frames);
  const highlighted = clean(scene.text?.display_text || scene.display_text).toLowerCase();
  const exit = interpolate(frame,safeFadeOutRange(duration,Math.round(.18*fps)),[1,0],{extrapolateLeft:'clamp',extrapolateRight:'clamp'});
  const canvasIn = enter(Math.max(0, Math.min(...starts)-3), .18);
  const media = scene.media || scene.primary_visual?.asset || {};
  const contextSrc = media.src || media.path || '';
  const standalone = !contextSrc || isRecoveryMedia(media, contextSrc);
  // Whole clauses are narration, not diagram actors. Without an explicitly
  // directed relationship, present a legible illustrated summary instead of
  // drawing arbitrary links between sentences.
  if (standalone && (
      steps.every(step => !hasDiagramGlyph(step.label))
      || (steps.every(step => step.label.split(' ').length >= 5)
          && !(Array.isArray(props.links) && props.links.length)))) {
    return <MissingSceneMotionGraphic scene={scene} visualDNA={visualDNA}/>;
  }
  const transparentCanvas = props.transparent_canvas === true && !standalone;
  const hasContext = Boolean(contextSrc) && !standalone && !transparentCanvas;
  const contextOut = hasContext ? enter(Math.min(...starts) + Math.round(.15*fps), .4) : 1;
  const s=width/1920;
  const glyphSize = steps.length <= 2 ? 180 : steps.length === 3 ? 145 : 110;
  const shadowOnly = variant === 'shadow_projection';
  const lightLabel = shadowOnly ? 'sunlight' : 'sun';
  if (variant === 'energy_conversion' && ['fuel', 'engine', 'motion'].every(actor => steps.some(step => step.label.toLowerCase() === actor))) {
    const id = `conversion-${scene.scene_index || 0}`;
    const rotation = Math.max(0, frame) / fps * 130;
    const piston = 22 * Math.sin(rotation * Math.PI / 180);
    const reveal = enter(0, .45);
    const labels = ['fuel', 'engine', 'motion'].map(actor => steps.find(step => step.label.toLowerCase() === actor).label);
    // A conceptual cutaway explains the relationship. It is not a model-specific
    // blueprint, a measured performance graph, or a claim about engine geometry.
    return <AbsoluteFill data-energy-conversion="true" style={{background: palette.background || '#101820', opacity: exit}}>
      <svg viewBox="0 0 1000 560" style={{width:'100%',height:'100%'}}>
        <defs>
          <linearGradient id={`${id}-metal`}><stop stopColor="#b8c3c9"/><stop offset=".45" stopColor="#e1e7e8"/><stop offset="1" stopColor="#596b75"/></linearGradient>
          <radialGradient id={`${id}-heat`}><stop stopColor={accent}/><stop offset="1" stopColor={accent} stopOpacity="0"/></radialGradient>
        </defs>
        <g opacity={.35 + .65*reveal}>
          <path d="M175 192C157 219 119 252 119 284a56 56 0 0 0 112 0c0-32-38-65-56-92Z" fill={accent}/>
          <path d="M150 278q-6 30 18 34" stroke={ink} strokeWidth="7" fill="none" opacity=".65"/>
          <path d="M405 169h190v182H405Z" fill="#1b2932" stroke="#8f9fa6" strokeWidth="5"/>
          <rect x="425" y="190" width="150" height="125" rx="4" fill="#0b141c"/>
          <ellipse cx="500" cy="225" rx="67" ry="42" fill={`url(#${id}-heat)`} opacity={.5+.25*Math.sin(rotation*Math.PI/180)}/>
          <g transform={`translate(0 ${piston})`}>
            <rect x="437" y="261" width="126" height="30" rx="3" fill={`url(#${id}-metal)`}/>
            <path d="M439 269h122M439 282h122" stroke="#30444e" strokeWidth="3"/>
          </g>
          <path d={`M500 ${291+piston}L${500+28*Math.cos(rotation*Math.PI/180)} ${366+28*Math.sin(rotation*Math.PI/180)}`} stroke="#cad4d8" strokeWidth="13"/>
          <circle cx="500" cy="366" r="37" fill="#1b2932" stroke="#8f9fa6" strokeWidth="6"/>
          <circle cx={500+28*Math.cos(rotation*Math.PI/180)} cy={366+28*Math.sin(rotation*Math.PI/180)} r="8" fill={accent}/>
          <g transform={`rotate(${rotation} 825 278)`}>
            <circle cx="825" cy="278" r="76" fill="#1b2932" stroke="#d3dadd" strokeWidth="9"/>
            {[0,60,120].map(angle=><path key={angle} d="M825 214v128" transform={`rotate(${angle} 825 278)`} stroke="#8fa1aa" strokeWidth="8"/>)}
            <circle cx="825" cy="278" r="18" fill={accent}/>
          </g>
          {[['M259 278H370','M353 267L370 278L353 289'],['M630 278H718','M701 267L718 278L701 289']].map((paths,i)=><g key={i} opacity={.4+.6*enter(Math.round((.15+i*.3)*fps),.45)}>
            <path d={paths[0]} stroke={accent} strokeWidth="4" pathLength="1" strokeDasharray="1" strokeDashoffset={1-reveal}/>
            <path d={paths[1]} stroke={accent} strokeWidth="4" fill="none"/>
          </g>)}
          {labels.map((label,i)=><text key={label} x={[175,500,825][i]} y="447" textAnchor="middle" fill={ink} fontFamily={fonts.display} fontSize="32" fontWeight="800">{label}</text>)}
        </g>
      </svg>
    </AbsoluteFill>;
  }
  if (['eclipse_alignment','shadow_projection'].includes(variant) && [lightLabel,'moon','earth'].every(actor => steps.some(step => step.label.toLowerCase() === actor))) {
    const id = `alignment-${scene.scene_index || 0}`;
    const revealAt = spokenPhraseFrame(scene, 'passes between', fps) ?? Math.round(.9 * fps);
    const shadowReveal = enter(Math.min(revealAt, duration - Math.round(1.2 * fps)), .65);
    // A stable schematic: motion explains occlusion; the camera never shakes.
    return <AbsoluteFill style={{ background: palette.background || '#090D13', color: ink, opacity: exit }}>
      <svg viewBox="0 0 1000 560" style={{ width:'100%', height:'100%' }}>
        <defs>
          <radialGradient id={`${id}-sun`}><stop stopColor="#FFFEE0"/><stop offset=".65" stopColor="#F6D06F"/><stop offset="1" stopColor="#AC632B"/></radialGradient>
          <radialGradient id={`${id}-moon`} cx="30%" cy="30%"><stop stopColor="#E5E4DD"/><stop offset=".65" stopColor="#72767B"/><stop offset="1" stopColor="#20252E"/></radialGradient>
          <radialGradient id={`${id}-earth`} cx="28%" cy="30%"><stop stopColor="#85BCCE"/><stop offset=".6" stopColor="#346477"/><stop offset="1" stopColor="#0C2232"/></radialGradient>
        </defs>
        {[208,244,280,316,352].map(y => <path key={y} d={`M${shadowOnly ? 80 : 215} ${y}H785`} stroke="#F6D06F" strokeWidth="1.5" opacity={.24*enter(0,.4)} pathLength="1" strokeDasharray="1" strokeDashoffset={1-enter(0,.6)}/>) }
        <path d="M482 251L785 219L785 341L482 309Z" fill={palette.surface || '#15242D'} opacity={shadowReveal*.9}/>
        <path d="M482 251L785 272L785 288L482 309Z" fill="#04070C" opacity={shadowReveal}/>
        {[...(!shadowOnly ? [{actor:'sun',x:150,r:80,at:0}] : []),{actor:'moon',x:470,r:30,at:.25},{actor:'earth',x:825,r:70,at:.5}].map(body => <g key={body.actor} opacity={enter(Math.round(body.at*fps),.4)}>
          <circle cx={body.x} cy="280" r={body.r} fill={`url(#${id}-${body.actor})`}/>
          {body.actor==='earth' && <path d="M802 227Q829 223 840 241L824 260L840 277L820 302L799 290L806 263L787 253Z" fill="#6B9878" opacity=".7"/>}
        </g>)}
      </svg>
      {steps.map(step => <div data-documentary-text="diagram-actor" key={step.label} style={{ position:'absolute', left:`${({sun:15,sunlight:15,moon:47,earth:82.5})[step.label.toLowerCase()]}%`, top:'29%', transform:'translateX(-50%)', fontFamily:fonts.display, fontSize:42*s, fontWeight:900, textShadow:'0 2px 8px #000' }}>{step.label}</div>)}
      {props.illustrative_not_to_scale && <div style={{position:'absolute',bottom:'14%',left:'8%',fontFamily:fonts.mono,fontSize:15*s,color:palette.secondary || '#AFB9BE'}}>SCHEMATIC · NOT TO SCALE</div>}
    </AbsoluteFill>;
  }
  return <AbsoluteFill style={{ overflow:'hidden', opacity:exit, color:ink }}>
    {hasContext && contextOut < 1 && <AbsoluteFill style={{opacity:1-contextOut}}>
      <MediaShotSequence scene={scene} media={media}>{({scene: activeScene, media: activeMedia}) => {
        const src = activeMedia.src || activeMedia.path || '';
        if (isRecoveryMedia(activeMedia, src)) return <MissingSceneMotionGraphic scene={activeScene} visualDNA={visualDNA} backgroundOnly copyOwnedElsewhere />;
        return resolveMediaKind(activeMedia, src) === 'video'
          ? <OffthreadVideo src={src} muted startFrom={Math.max(0,Math.round(Number(activeMedia.source_start || 0)*fps))} style={{width:'100%',height:'100%',objectFit:'contain'}} />
          : <Img src={src} style={{width:'100%',height:'100%',objectFit:'contain'}} />;
      }}</MediaShotSequence>
    </AbsoluteFill>}
    {!transparentCanvas && <AbsoluteFill style={{opacity:hasContext ? contextOut : 1, background:`radial-gradient(ellipse at 55% 48%, ${palette.surface || '#19242A'}, ${palette.background || '#09131B'} 80%)`}} />}
    <AbsoluteFill style={{opacity:canvasIn}}>
    <svg viewBox="0 0 1000 560" preserveAspectRatio="none" style={{position:'absolute',inset:'9% 6% 16%',width:'88%',height:'75%',overflow:'visible'}}>
      <defs><marker id={`arrow-${scene.scene_index || 0}`} markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto"><path d="M0 0L5 2.5L0 5" fill="none" stroke={accent} strokeWidth="1" /></marker></defs>
      {layout.links.map(([a,b],i)=>{
        const [ax,ay]=layout.nodes[a], [bx,by]=layout.nodes[b];
        const reveal=enter(Math.max(starts[a], starts[b]),.55);
        const x1=ax*10, y1=ay*5.6, x2=bx*10, y2=by*5.6;
        const path=layout.kind==='hierarchy'
          ? `M ${x1} ${y1} L ${x1} ${(y1+y2)/2} L ${x2} ${(y1+y2)/2} L ${x2} ${y2}`
          : layout.kind==='process'
            ? (horizontalProcess ? `M ${x1+22} ${y1} L ${x2-22} ${y2}` : `M ${x1} ${y1+22} L ${x2} ${y2-22}`)
            : layout.kind==='cause_effect'
               ? `M ${x1+glyphSize*.48} ${y1} C ${(x1+x2)/2-8} ${y1-4}, ${(x1+x2)/2+8} ${y2+4}, ${x2-glyphSize*.48} ${y2}`
              : `M ${x1} ${y1} C ${(ax+(bx-ax)*.45)*10} ${ay*5.6}, ${(ax+(bx-ax)*.55)*10} ${by*5.6}, ${x2} ${y2}`;
        return <g key={i}><path d={path} fill="none" stroke={ink} strokeOpacity=".12" strokeWidth="1.4"/><path d={path} fill="none" stroke={accent} strokeWidth="2.8" pathLength="1" strokeDasharray="1" strokeDashoffset={1-reveal} opacity={reveal} markerEnd={`url(#arrow-${scene.scene_index || 0})`}/></g>;
      })}
      {layout.nodes.map(([x,y],i)=>{if(steps[i].hideMarker)return null;const reveal=enter(starts[i]);const cx=x*10,cy=y*5.6;return <g key={i} opacity={standalone ? .22 + .78 * reveal : reveal}>
        <DiagramGlyph label={steps[i].label} x={cx} y={cy} accent={accent} ink={ink} index={i} size={glyphSize} />
      </g>;})}
    </svg>
    {steps.map((step,i)=>{
      const [x,y]=layout.nodes[i]; const reveal=enter(starts[i]);
      if (highlightActive && clean(step.label).toLowerCase() === highlighted) return null;
      const customLabelY = step.labelY !== undefined && Number.isFinite(Number(step.labelY));
      const lower=customLabelY || y>55;
      const process=layout.kind==='process' && !horizontalProcess;
      const cause=layout.kind==='cause_effect';
      const labelWidth=process ? 49 : cause ? Math.min(28,82/steps.length) : steps.length === 1 ? 56 : 30;
      const labelLeft=process ? 30 : Math.max(labelWidth/2+2,Math.min(98-labelWidth/2,6+x*.88));
      const glyphHalfHeight = glyphSize / 560 * 75 / 2;
      const labelTop=process ? 9+y*.75 : customLabelY ? Number(step.labelY) : 9+y*.75+(lower?-glyphHalfHeight-3:glyphHalfHeight+3);
      const labelTransform=process ? `translateY(-50%) translateY(${(1-reveal)*12}px)` : `translate(-50%, ${lower ? '-100%' : '0%'}) translateY(${(1-reveal)*16}px)`;
      return <div key={`${i}-${step.label}`} style={{
        position:'absolute',left:`${labelLeft}%`,top:`${labelTop}%`,width:process?'max-content':`${labelWidth}%`,maxWidth:`${labelWidth}%`,
        transform:labelTransform,opacity:reveal,textAlign:process?'left':'center',
        fontFamily:fonts.editorial || fonts.display,fontSize:(standalone ? (step.label.length>60?38:step.label.length>35?48:64) : (step.label.length>60?27:step.label.length>35?34:44))*s,
        fontWeight:800,lineHeight:1.12,letterSpacing:'-.025em',textWrap:'balance',overflowWrap:'break-word',
        textShadow:'0 2px 2px rgba(0,0,0,.85), 0 6px 16px rgba(0,0,0,.75)',
        ...(process ? {padding:'10px 16px',background:'linear-gradient(90deg, rgba(5,13,19,.92), rgba(5,13,19,.24))',borderLeft:`3px solid ${accent}`} : {}),
        ...(cause ? {padding:'0 5px 8px',borderBottom:`1px solid ${accent}`} : {}),
        ...(!process&&!cause&&layout.kind==='hierarchy' ? {padding:'8px 10px',background:'rgba(5,13,19,.70)',borderTop:`2px solid ${accent}`} : {}),
      }}>{step.label}</div>;
    })}
    </AbsoluteFill>
  </AbsoluteFill>;
}
