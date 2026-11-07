import { useEffect } from 'react';
import { useCurrentFrame, useVideoConfig } from 'remotion';

/** Opt-in frame review checks actual laid-out text after bundled fonts load. */
export function DocumentaryLayoutAudit({ enabled = false }) {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  useEffect(() => {
    if (!enabled) return;
    const failures = [];
    const measurements = [];
    const media = [];
    const graphics = [];
    const canvas = document.querySelector('[data-documentary-canvas]')?.getBoundingClientRect() || {left: 0, top: 0, right: width, bottom: height};
    for (const element of document.querySelectorAll('[data-documentary-text]')) {
      let visible = true;
      for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) {
        const style = getComputedStyle(ancestor);
        if (Number(style.opacity) < .1 || style.display === 'none' || style.visibility === 'hidden') visible = false;
      }
      if (!visible) continue;
      const bounds = element.getBoundingClientRect();
      // Browser review uses an exact composition viewport, without player scaling.
      const outside = bounds.left < canvas.left - 1 || bounds.top < canvas.top - 1 || bounds.right > canvas.right + 1 || bounds.bottom > canvas.bottom + 1;
      const overflow = element.scrollWidth > element.clientWidth + 2 || element.scrollHeight > element.clientHeight + 2;
      const canvasWidth = Math.max(1, canvas.right - canvas.left);
      const canvasHeight = Math.max(1, canvas.bottom - canvas.top);
      const style = getComputedStyle(element);
      measurements.push({ text: element.textContent, kind: element.dataset.documentaryText,
        x: +((bounds.left - canvas.left) / canvasWidth * 100).toFixed(2),
        y: +((bounds.top - canvas.top) / canvasHeight * 100).toFixed(2),
        width: +(bounds.width / canvasWidth * 100).toFixed(2),
        height: +(bounds.height / canvasHeight * 100).toFixed(2),
        color: style.color, background: style.backgroundColor, outside, overflow });
      if (outside || overflow) failures.push({ text: element.textContent, kind: element.dataset.documentaryText, outside, overflow,
        box: [element.clientWidth, element.clientHeight], content: [element.scrollWidth, element.scrollHeight] });
    }
    for (const element of document.querySelectorAll('img')) {
      if (!element.complete || !element.naturalWidth || !element.naturalHeight) continue;
      let opacity = 1;
      let hidden = false;
      for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) {
        const style = getComputedStyle(ancestor);
        opacity *= Number(style.opacity);
        if (style.display === 'none' || style.visibility === 'hidden') hidden = true;
      }
      const box = element.getBoundingClientRect();
      if (hidden || opacity < .001 || box.width < 1 || box.height < 1) continue;
      media.push({ src: element.getAttribute('src'), opacity, filter: getComputedStyle(element).filter,
        x: box.x, y: box.y, width: box.width, height: box.height });
    }
    const canvasWidth = Math.max(1, canvas.right - canvas.left);
    const canvasHeight = Math.max(1, canvas.bottom - canvas.top);
    const visibleAt = (element) => {
      let opacity = 1;
      for (let node = element; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        opacity *= Number(style.opacity);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
      }
      return opacity >= .1;
    };
    const clippedGlyph = (box, element, root) => {
      let result = {left:box.left,right:box.right,top:box.top,bottom:box.bottom};
      for (let node=element; node; node=node.parentElement) {
        const style = getComputedStyle(node);
        const parent = node.getBoundingClientRect();
        if (['hidden','clip'].includes(style.overflowX)) {
          result.left=Math.max(result.left,parent.left); result.right=Math.min(result.right,parent.right);
        }
        if (['hidden','clip'].includes(style.overflowY)) {
          result.top=Math.max(result.top,parent.top); result.bottom=Math.min(result.bottom,parent.bottom);
        }
        const mask = String(style.clipPath || '').match(/^inset\(([^)]+)\)$/);
        if (mask) {
          const raw=mask[1].split(/\s+round\s+/)[0].trim().split(/\s+/);
          if (raw.length >= 1 && raw.length <= 4 && raw.every(value => /^-?[\d.]+(?:px|%)?$/.test(value))) {
            const values=raw.length===1 ? [raw[0],raw[0],raw[0],raw[0]] : raw.length===2 ? [raw[0],raw[1],raw[0],raw[1]] : raw.length===3 ? [raw[0],raw[1],raw[2],raw[1]] : raw;
            const amount=(value,length) => parseFloat(value)*(value.endsWith('%') ? length/100 : 1);
            result.top=Math.max(result.top,parent.top+amount(values[0],parent.height));
            result.right=Math.min(result.right,parent.right-amount(values[1],parent.width));
            result.bottom=Math.min(result.bottom,parent.bottom-amount(values[2],parent.height));
            result.left=Math.max(result.left,parent.left+amount(values[3],parent.width));
          }
        }
        if (node===root) break;
      }
      return result.right>result.left && result.bottom>result.top ? result : null;
    };
    for (const root of document.querySelectorAll('[data-documentary-graphic]')) {
      const category = root.dataset.documentaryGraphic;
      if (!['explanatory', 'emphasis'].includes(category)) continue;
      const rects = [];
      let largestFont = 0;
      // Measure glyphs and meaningful marks, never the full-screen wrapper or
      // its large background. This prevents a tiny corner title passing as a
      // full-screen graphic. Range respects the loaded font and wrapping.
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let text = walker.nextNode(); text; text = walker.nextNode()) {
        if (!text.textContent.trim() || !visibleAt(text.parentElement)) continue;
        const range = document.createRange();
        range.selectNodeContents(text);
        for (const box of range.getClientRects()) if (box.width > 1 && box.height > 1) {
          const clipped=clippedGlyph(box,text.parentElement,root);
          if (clipped) rects.push(clipped);
        }
        largestFont = Math.max(largestFont, parseFloat(getComputedStyle(text.parentElement).fontSize) || 0);
      }
      if (category === 'explanatory') {
        for (const mark of root.querySelectorAll('img, canvas, svg text, svg circle, svg ellipse, svg rect, svg path, svg polygon')) {
          if (!visibleAt(mark)) continue;
          const box = mark.getBoundingClientRect();
          const area = box.width * box.height / (canvasWidth * canvasHeight);
          if (area > .001 && area < .95) { const clipped=clippedGlyph(box,mark,root); if (clipped) rects.push(clipped); }
        }
      }
      const clipped = rects.map((box) => ({
        left: Math.max(canvas.left, box.left), right: Math.min(canvas.right, box.right),
        top: Math.max(canvas.top, box.top), bottom: Math.min(canvas.bottom, box.bottom),
      })).filter((box) => box.right > box.left && box.bottom > box.top);
      // Union occupied rectangles using a sweep; large empty gaps between
      // labels do not increase the visible graphic's area.
      const edges = [...new Set(clipped.flatMap(box => [box.left, box.right]))].sort((a,b) => a-b);
      let occupiedArea = 0;
      for (let i=0; i<edges.length-1; i++) {
        const left = edges[i], right = edges[i+1];
        const spans = clipped.filter(box => box.left < right && box.right > left).map(box => [box.top,box.bottom]).sort((a,b) => a[0]-b[0]);
        let end = -Infinity, covered = 0;
        for (const [top,bottom] of spans) { covered += Math.max(0,bottom-Math.max(top,end)); end = Math.max(end,bottom); }
        occupiedArea += (right-left) * covered;
      }
      const areaRatio = occupiedArea / (canvasWidth * canvasHeight);
      const prominent = category !== 'emphasis' || largestFont / canvasHeight >= .07;
      const layoutFingerprint = JSON.stringify([category, root.dataset.graphicFamily,
        Math.round(largestFont/canvasHeight*20), clipped.slice(0,32).map(box => [
          Math.round((box.left-canvas.left)/canvasWidth*20), Math.round((box.top-canvas.top)/canvasHeight*20),
          Math.round((box.right-box.left)/canvasWidth*20), Math.round((box.bottom-box.top)/canvasHeight*20),
        ]).sort((a,b) => a.join(',').localeCompare(b.join(',')))]);
      graphics.push({ layout_fingerprint: layoutFingerprint, category, family: root.dataset.graphicFamily, variant: root.dataset.graphicVariant,
        scene_index: Number(root.dataset.graphicScene), start_seconds: Number(root.dataset.graphicStart), end_seconds: Number(root.dataset.graphicEnd), area_ratio: +areaRatio.toFixed(5),
        font_height_ratio: +(largestFont/canvasHeight).toFixed(5), visible: visibleAt(root) && prominent && areaRatio >= .025 });
    }
    console.log('VIDZEO_LAYOUT_AUDIT ' + JSON.stringify({ frame, failures, measurements, media, graphics_audit_version: 1, graphics_manifest_signature: document.querySelector('[data-documentary-canvas]')?.dataset.graphicReviewSignature || '', graphics }));
  }, [enabled, frame, width, height]);
  return null;
}
