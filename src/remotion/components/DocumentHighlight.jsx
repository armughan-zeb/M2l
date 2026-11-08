import React from 'react';
import { AbsoluteFill, Img, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { selectDocumentaryHeadline, cleanDocumentaryHighlight } from '../engine/TextSanitizer';
import { cueProgress, safeFadeOutRange, safeRevealRange } from '../engine/DirectorMotion';

const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
const modernFont = (value, fallback) => {
  const requested = clean(value);
  return requested && !/playfair|cinzel|lora|georgia|times|serif|courier|typewriter|impact/i.test(requested)
    ? requested
    : fallback;
};

/** Source/evidence annotation, composed as open typography over the footage. */
export function DocumentHighlight({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const requestedVariant = String(props.variant || scene.primary_visual?.variant || 'yellow_marker_highlight').toLowerCase();
  const capture = props.source_capture || props.sourceCapture || {};
  const directed = scene.director_treatment_enabled === true || Object.prototype.hasOwnProperty.call(scene.text || {}, 'overlay_policy');
  const verifiedPassage = capture.verification_status === 'exact_passage_verified' && Boolean(capture.exact_excerpt);
  const hasCaptureRecord = Boolean(capture.url || capture.domain || capture.article_title);
  const hasExplicitCopy = ['display_text', '_display_text', 'highlight', 'highlightedSentence', 'paragraph', 'text', 'title'].some((key) => Object.prototype.hasOwnProperty.call(props, key));
  const sourceText = verifiedPassage
    ? clean(capture.exact_excerpt)
    : (hasCaptureRecord ? '' : (hasExplicitCopy
      ? clean(props.highlight || props.highlightedSentence || props.paragraph || props.text || props.display_text)
      : clean(scene.narration)));
  const headline = directed ? '' : hasCaptureRecord
    ? clean(capture.article_title || props.title || '').toUpperCase()
    : (selectDocumentaryHeadline({ ...props, highlight: sourceText, title: props.title }, scene, 4) || 'SCRIPT PASSAGE').toUpperCase();
  const detail = verifiedPassage
    ? sourceText
    : (hasCaptureRecord ? '' : cleanDocumentaryHighlight(sourceText, 10));
  // Prefer the resolved local path: packaged jobs store captures in the
  // writable user-data output directory and image_url is only its Flask route.
  const imageSrc = capture.image_path || capture.image_url || '';
  const showSourceImage = capture.status === 'verified_capture' && verifiedPassage && imageSrc;
  const containsRedaction = /\[(?:REDACTED|CENSORED|CLASSIFIED)\]|\b(?:redacted|censored)\b/i.test(sourceText);
  const variant = requestedVariant === 'declassified_redaction' && !containsRedaction
    ? 'yellow_marker_highlight'
    : requestedVariant;
  const sceneProgress = Math.max(0, Math.min(1, frame / Math.max(1, sceneDuration - 1)));
  const citationTitle = clean(capture.publication || capture.domain || props.source || '');
  const palette = visualDNA.palette || {};
  const accent = palette.accent || '#FACC15';
  const display = modernFont(visualDNA.typography?.display, 'Archivo Black, Inter, sans-serif');
  const body = modernFont(visualDNA.typography?.body, 'DM Sans, Inter, sans-serif');
  const enter = spring({ frame, fps, config: { damping: 15, mass: .36, stiffness: 175 } });
  const reveal = directed
    ? cueProgress(scene, 'source', frame, fps, .04, .55)
    : interpolate(frame, safeRevealRange(sceneDuration, Math.round(fps * .36)), [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const exit = interpolate(frame, safeFadeOutRange(sceneDuration, Math.round(fps * .32)), [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const label = hasCaptureRecord ? citationTitle : clean(props.source || props.documentType || props.kicker || '');

  if ((!sourceText && !hasCaptureRecord) || (directed && !showSourceImage)) return null;

  return (
    <AbsoluteFill style={{ pointerEvents: 'none' }}>
      {showSourceImage && <div style={{ position: 'absolute', right: variant === 'report_page_pan' ? 0 : '3%', top: variant === 'report_page_pan' ? 0 : '9%', width: variant === 'report_page_pan' ? '100%' : '54%', height: variant === 'report_page_pan' ? '100%' : '82%', opacity: enter * exit, transform: `translateY(${variant === 'report_page_pan' ? 0 : (1 - enter) * 12}px)`, overflow: 'hidden', filter: 'drop-shadow(0 12px 36px rgba(0,0,0,.42))', maskImage: variant === 'report_page_pan' ? undefined : 'linear-gradient(90deg, transparent 0%, black 7%, black 100%)', WebkitMaskImage: variant === 'report_page_pan' ? undefined : 'linear-gradient(90deg, transparent 0%, black 7%, black 100%)' }}>
        <Img src={imageSrc} style={{ width: '100%', height: '100%', objectFit: variant === 'report_page_pan' ? 'cover' : 'cover', objectPosition: variant === 'report_page_pan' ? `center ${18 + sceneProgress * 60}%` : 'center 48%' }} />
        {variant === 'report_page_pan' && <AbsoluteFill style={{ background: 'linear-gradient(90deg, rgba(5,11,17,.78), rgba(5,11,17,.30) 58%, rgba(5,11,17,.05))' }} />}
        {variant === 'magnifier_zoom_lens' && <div style={{ position: 'absolute', right: '7%', top: '37%', width: '31%', aspectRatio: '1', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 25, borderRadius: '50%', border: `5px solid ${accent}`, background: 'rgba(248,247,242,.96)', color: '#111820', boxShadow: '0 10px 32px rgba(0,0,0,.45)', overflow: 'hidden', fontFamily: body, fontSize: 18, fontWeight: 750, lineHeight: 1.18, textAlign: 'center' }}>{sourceText.slice(0, 140)}</div>}
      </div>}
      <div style={{ position: 'absolute', left: '8%', top: variant === 'report_page_pan' ? '18%' : '18%', width: variant === 'report_page_pan' ? '41%' : showSourceImage ? '33%' : '82%', maxWidth: 1220, opacity: enter * exit, transform: `translateY(${(1 - enter) * 22}px)`, color: palette.primary || '#FFFFFF', filter: 'drop-shadow(0 3px 14px rgba(0,0,0,.6))' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: accent, fontFamily: body, fontSize: 12, fontWeight: 800, letterSpacing: '.18em', textTransform: 'uppercase' }}>
          <span style={{ width: 42, height: 2, background: accent, transformOrigin: 'left', transform: `scaleX(${reveal})` }} />{label}
        </div>
        {headline && <div style={{ position: 'relative', display: 'inline-block', maxWidth: '94%', marginTop: 16, fontFamily: display, fontSize: headline.length > 34 ? 54 : 72, fontWeight: 900, lineHeight: .95, letterSpacing: '-.06em', overflowWrap: 'anywhere' }}>
          <span style={{ position: 'relative', zIndex: 1 }}>{headline}</span>
          <span style={{ position: 'absolute', left: 0, right: 0, bottom: '-.06em', height: 6, background: accent, opacity: .92, transformOrigin: 'left', transform: `scaleX(${reveal})` }} />
        </div>}
        {detail && <div style={{ maxWidth: 760, marginTop: 19, color: 'rgba(255,255,255,.9)', fontFamily: body, fontSize: detail.length > 180 ? 23 : 28, fontWeight: 650, lineHeight: 1.27, borderLeft: `4px solid ${accent}`, padding: variant === 'declassified_redaction' ? '12px 18px' : '0 0 0 22px', background: variant === 'declassified_redaction' ? 'rgba(4,7,10,.9)' : 'transparent', clipPath: `inset(0 ${(1 - reveal) * 100}% 0 0)` }}>
          <span style={variant === 'yellow_marker_highlight' ? { color: palette.background || '#101820', background: `linear-gradient(transparent 18%, ${accent} 18%, ${accent} 92%, transparent 92%)`, padding: '0 3px', boxDecorationBreak: 'clone', WebkitBoxDecorationBreak: 'clone' } : undefined}>{detail}</span>
        </div>}
        {hasCaptureRecord && <div style={{ maxWidth: 760, marginTop: 18, color: 'rgba(255,255,255,.58)', fontFamily: body, fontSize: 12, lineHeight: 1.35 }}>{clean(capture.article_title || '')}{capture.domain ? ` · ${clean(capture.domain)}` : ''}</div>}
      </div>
    </AbsoluteFill>
  );
}
