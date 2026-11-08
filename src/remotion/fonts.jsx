import React, {useEffect, useRef, useState} from 'react';
import {continueRender, delayRender, staticFile} from 'remotion';

const fonts = [
  ['Anton', 'Anton-Regular.ttf', 400, 'normal'],
  ['Bebas Neue', 'BebasNeue-Regular.ttf', 400, 'normal'],
  ['Montserrat', 'Montserrat-Bold.ttf', 700, 'normal'],
  ['Poppins', 'Poppins-Regular.ttf', 400, 'normal'],
  ['Open Sans', 'OpenSans-Bold.ttf', 700, 'normal'],
  ['Roboto', 'Roboto-Bold.ttf', 700, 'normal'],
  ['Playfair Display', 'PlayfairDisplay-Bold.ttf', 700, 'italic'],
  ['Cinzel', 'Cinzel-Bold.ttf', 700, 'normal'],
  ['Orbitron', 'Orbitron-Bold.ttf', 700, 'normal'],
  ['Caveat', 'Caveat-Bold.ttf', 700, 'normal'],
  ['Creepster', 'Creepster-Regular.ttf', 400, 'normal'],
  ['Nosifer', 'Nosifer-Regular.ttf', 400, 'normal'],
  // Professional documentary fonts
  ['Inter', 'Inter-Regular.ttf', 400, 'normal'],
  ['Inter', 'Inter-Medium.ttf', 500, 'normal'],
  ['Inter', 'Inter-SemiBold.ttf', 600, 'normal'],
  ['Inter', 'Inter-Bold.ttf', 700, 'normal'],
  ['Inter', 'Inter-Italic.ttf', 400, 'italic'],
  ['Oswald', 'Oswald-Regular.ttf', 400, 'normal'],
  ['Oswald', 'Oswald-Medium.ttf', 500, 'normal'],
  ['Oswald', 'Oswald-SemiBold.ttf', 600, 'normal'],
  ['Oswald', 'Oswald-Bold.ttf', 700, 'normal'],
  ['Archivo Black', 'ArchivoBlack-Regular.ttf', 900, 'normal'],
  ['Barlow Condensed', 'BarlowCondensed-SemiBold.ttf', 600, 'normal'],
  ['Barlow Condensed', 'BarlowCondensed-Bold.ttf', 700, 'normal'],
  ['DM Sans', 'DMSans-Regular.ttf', 400, 'normal'],
  ['DM Sans', 'DMSans-Medium.ttf', 500, 'normal'],
  ['DM Sans', 'DMSans-Bold.ttf', 700, 'normal'],
  ['Plus Jakarta Sans', 'PlusJakartaSans-Regular.ttf', 400, 'normal'],
  ['Plus Jakarta Sans', 'PlusJakartaSans-Medium.ttf', 500, 'normal'],
  ['Plus Jakarta Sans', 'PlusJakartaSans-Bold.ttf', 700, 'normal'],
  ['Lora', 'Lora-Regular.ttf', 400, 'normal'],
  ['Lora', 'Lora-Bold.ttf', 700, 'normal'],
  ['Lora', 'Lora-Italic.ttf', 400, 'italic'],
  ['JetBrains Mono', 'JetBrainsMono-Regular.ttf', 400, 'normal'],
  ['JetBrains Mono', 'JetBrainsMono-Bold.ttf', 700, 'normal'],
];

export function DocumentaryFontFaces() {
  const css = fonts.map(([family, file, weight, style]) =>
    `@font-face{font-family:"${family}";src:url("${staticFile(`font-library/${file}`)}") format("truetype");font-style:${style};font-weight:${weight};font-display:block;}`
  ).join('\n');
  return <style>{css}</style>;
}

// Text measurement and final rendering must see the same bundled faces.
// Keeping the composition behind this short readiness gate prevents Chrome
// from laying out a headline with a system fallback and swapping it after the
// frame has already been captured by the renderer.
export function useDocumentaryFontsReady() {
  const [readyToMeasure, setReadyToMeasure] = useState(false);
  const handle = useRef(null);
  if (handle.current === null) {
    handle.current = delayRender('Loading bundled documentary fonts');
  }
  useEffect(() => {
    let active = true;
    const loads = fonts.map(([family, _file, weight, style]) => `${style} ${weight} 16px "${family}"`);
    const ready = typeof document !== 'undefined' && document.fonts
      ? Promise.all(loads.map((font) => document.fonts.load(font))).then(() => document.fonts.ready)
      : Promise.resolve();
    ready.catch(() => undefined).finally(() => {
      if (active) setReadyToMeasure(true);
    });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (readyToMeasure) continueRender(handle.current);
  }, [readyToMeasure]);
  return readyToMeasure;
}
