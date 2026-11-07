import React from 'react';
import { createRoot } from 'react-dom/client';
import { Player } from '@remotion/player';
import { DocumentaryComposition, getCompositionConfig } from './DocumentaryComposition';

let mounted = null;

export function mountPlayer(element, manifest, options = {}) {
  if (!element) throw new Error('A Remotion player element is required');
  const config = getCompositionConfig(manifest || {});
  if (mounted?.element === element) {
    mounted.root.render(<Player ref={mounted.playerRef} component={DocumentaryComposition} inputProps={{ manifest: manifest || {} }} durationInFrames={config.durationInFrames} fps={config.fps} compositionWidth={config.width} compositionHeight={config.height} controls loop autoPlay={Boolean(options.autoPlay)} style={{ width: '100%', height: '100%' }} />);
    return mounted;
  }
  const root = createRoot(element);
  const playerRef = React.createRef();
  root.render(<Player ref={playerRef} component={DocumentaryComposition} inputProps={{ manifest: manifest || {} }} durationInFrames={config.durationInFrames} fps={config.fps} compositionWidth={config.width} compositionHeight={config.height} controls loop autoPlay={Boolean(options.autoPlay)} style={{ width: '100%', height: '100%' }} />);
  mounted = {
    element,
    root,
    config,
    playerRef,
    seekTo(frame) { playerRef.current?.seekTo?.(Math.max(0, Math.round(frame))); },
    play() { return playerRef.current?.play?.(); },
    pause() { return playerRef.current?.pause?.(); },
  };
  return mounted;
}

export function unmountPlayer(element) {
  if (mounted && (!element || mounted.element === element)) {
    mounted.root.unmount();
    mounted = null;
  }
}

if (typeof window !== 'undefined') {
  window.VidzeoRemotion = { mountPlayer, unmountPlayer };
}
