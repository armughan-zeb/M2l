import React from 'react';
import { AbsoluteFill } from 'remotion';
import { MissingSceneMotionGraphic } from './MissingSceneMotionGraphic';

/** A single broken scene must not take down the film's remaining timeline. */
export class SceneRenderBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error) {
    const sceneIndex = this.props.scene?.scene_index ?? 'unknown';
    console.error(`[Remotion] Scene ${sceneIndex} failed; rendering its motion-graphic recovery plate.`, error);
  }

  render() {
    if (this.state.failed) {
      return <AbsoluteFill>
        <MissingSceneMotionGraphic
          scene={this.props.scene}
          visualDNA={this.props.visualDNA}
          theme={this.props.theme}
        />
      </AbsoluteFill>;
    }
    return this.props.children;
  }
}

