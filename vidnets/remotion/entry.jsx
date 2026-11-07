import React from 'react';
import { Composition, getInputProps, registerRoot } from 'remotion';
import { DocumentaryComposition, getCompositionConfig } from '../src/remotion/DocumentaryComposition';

const demoManifest = {
  version: 1,
  fps: 30,
  width: 1920,
  height: 1080,
  duration: 8,
  duration_in_frames: 240,
  theme: { id: 'vox_clarity' },
  scenes: [
    { id: 'demo-1', scene_index: 0, start: 0, duration: 4, narration: 'A semantic scene becomes a designed documentary beat.', media: { type: 'image', src: '' }, graphic: { template_id: 'chapter-opener', props: { kicker: 'VIDZEO SEMANTIC EDIT', title: 'THE STORY STARTS HERE' } }, transition: { template_id: 'cut', duration: .2 }, camera: { motion: 'slow_push', scale: 1 }, sound_cues: [] },
    { id: 'demo-2', scene_index: 1, start: 4, duration: 4, narration: 'Dates, evidence, maps, counters, and sound stay attached to the narration.', media: { type: 'image', src: '' }, graphic: { template_id: 'map-route', props: { label: 'SEMANTIC ROUTE' } }, transition: { template_id: 'dissolve', duration: .24 }, camera: { motion: 'pan_left', scale: 1 }, sound_cues: [] }
  ]
};

function Root() {
  const input = getInputProps() || {};
  const manifest = input.manifest && Array.isArray(input.manifest.scenes)
    ? input.manifest
    : demoManifest;
  const config = getCompositionConfig(manifest);
  return <Composition id={config.id} component={DocumentaryComposition} durationInFrames={config.durationInFrames} fps={config.fps} width={config.width} height={config.height} defaultProps={{ manifest }} />;
}

registerRoot(Root);
