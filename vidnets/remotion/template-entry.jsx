import React from 'react';
import { AbsoluteFill, Composition, getInputProps, registerRoot } from 'remotion';
import { GraphicTemplate } from '../src/remotion/templates';
import { mergeTheme } from '../src/remotion/theme';
import { DocumentaryFontFaces } from '../src/remotion/fonts';

/* Renders a single template on a transparent background for frame-sequence caching.
   Called by the template animation cache builder. */

function TemplateAnimation() {
  const { template_id = 'chapter-opener', props = {}, duration = 2.0, theme: themeOverrides = {} } = getInputProps() || {};
  const theme = mergeTheme({ 
    id: 'template_cache',
    palette: {
      background: '#0b1320', surface: '#16263b', primary: '#f4f0e6',
      accent: '#f2c14e', secondary: '#49a6d8', danger: '#ef6f6c',
      ...(themeOverrides.palette || {}),
    },
    font_family: themeOverrides.font_family || 'Poppins, Arial, sans-serif',
    display_font: themeOverrides.display_font || 'Bebas Neue, Arial, sans-serif',
    ...themeOverrides,
  });

  return (
    <AbsoluteFill style={{ background: 'transparent' }}>
      <DocumentaryFontFaces />
      <GraphicTemplate id={template_id} props={props} theme={theme} />
    </AbsoluteFill>
  );
}

function Root() {
  const input = getInputProps() || {};
  const fps = Number(input.fps || 30);
  const durationInFrames = Math.round(Number(input.duration || 2) * fps);

  return (
    <Composition
      id="TemplateAnimation"
      component={TemplateAnimation}
      durationInFrames={Math.max(1, durationInFrames)}
      fps={fps}
      width={Number(input.width || 1920)}
      height={Number(input.height || 1080)}
      defaultProps={input}
    />
  );
}

registerRoot(Root);
