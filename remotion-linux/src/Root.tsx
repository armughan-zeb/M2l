import React from 'react';
import {Composition, getInputProps} from 'remotion';
// Bundled Vidnets Remotion composition — the repo's own motion-graphics engine
// (kinetic type, stat cards, timelines, maps, captions, film grain, vignette).
// @ts-ignore — .jsx source inside the repo, bundled by Remotion's esbuild/webpack.
import {DocumentaryComposition, getCompositionConfig} from '../../vidnets/src/remotion/DocumentaryComposition';
import defaultManifest from './manifest.json';

export const RemotionRoot: React.FC = () => {
  const input = (getInputProps() || {}) as {manifest?: Record<string, unknown>};
  const manifest = (input.manifest && typeof input.manifest === 'object' ? input.manifest : defaultManifest) as Record<string, unknown>;
  const config = getCompositionConfig(manifest);
  return (
    <Composition
      id={config.id}
      component={DocumentaryComposition}
      durationInFrames={config.durationInFrames}
      fps={config.fps}
      width={config.width}
      height={config.height}
      defaultProps={{manifest}}
    />
  );
};
