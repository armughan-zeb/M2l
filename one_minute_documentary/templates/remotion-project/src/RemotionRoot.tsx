import React from "react";
import { Composition } from "remotion";
import { Documentary60 } from "./Composition";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Documentary60"
      component={Documentary60}
      durationInFrames={1800}
      fps={30}
      width={1920}
      height={1080}
    />
  );
};
