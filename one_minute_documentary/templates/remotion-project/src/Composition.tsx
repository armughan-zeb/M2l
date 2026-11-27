import React from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  OffthreadVideo,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

const FONT = "Arial, Helvetica, sans-serif";
const ORANGE = "#ff7a1a";

// Change these two lines for a new topic. Basketball is the worked example.
const TOPIC_TAG = "BASKETBALL";
const HEADER_TEXT = "MORE THAN A GAME";

const fade = (local: number, dur: number) =>
  interpolate(local, [0, 15, dur - 15, dur], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

const Gradient: React.FC = () => (
  <div
    style={{
      position: "absolute",
      left: 0,
      right: 0,
      bottom: 0,
      height: 460,
      background:
        "linear-gradient(to top, rgba(0,0,0,0.82) 0%, rgba(0,0,0,0.45) 55%, rgba(0,0,0,0) 100%)",
    }}
  />
);

const Caption: React.FC<{
  title: string;
  sub?: string;
  align?: "left" | "center";
}> = ({ title, sub, align = "left" }) => (
  <div
    style={{
      position: "absolute",
      left: align === "center" ? 0 : 90,
      right: align === "center" ? 0 : 90,
      bottom: 110,
      textAlign: align,
      fontFamily: FONT,
      color: "#fff",
    }}
  >
    <div
      style={{
        display: "inline-block",
        background: ORANGE,
        color: "#111",
        fontWeight: 900,
        fontSize: 26,
        letterSpacing: 6,
        padding: "8px 18px",
        marginBottom: 18,
      }}
    >
      {TOPIC_TAG}
    </div>
    <div
      style={{
        fontSize: 84,
        fontWeight: 900,
        lineHeight: 1.02,
        letterSpacing: 1,
        textShadow: "0 4px 30px rgba(0,0,0,0.7)",
      }}
    >
      {title}
    </div>
    {sub ? (
      <div
        style={{
          marginTop: 16,
          fontSize: 34,
          fontWeight: 400,
          letterSpacing: 2,
          color: "#f2f2f2",
          textShadow: "0 2px 12px rgba(0,0,0,0.8)",
        }}
      >
        {sub}
      </div>
    ) : null}
  </div>
);

const Shot: React.FC<{
  from: number;
  dur: number;
  video?: string;
  image?: string;
  title: string;
  sub?: string;
  center?: boolean;
  zoomIn?: boolean;
}> = ({ from, dur, video, image, title, sub, center, zoomIn }) => {
  const frame = useCurrentFrame();
  const local = frame - from;
  const opacity = fade(local, dur);
  const scale = interpolate(local, [0, dur], zoomIn ? [1.0, 1.12] : [1.12, 1.0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <Sequence from={from} durationInFrames={dur}>
      <AbsoluteFill style={{ opacity, overflow: "hidden", background: "#000" }}>
        <div
          style={{
            width: "100%",
            height: "100%",
            transform: `scale(${scale})`,
          }}
        >
          {video ? (
            <OffthreadVideo
              src={staticFile(video)}
              style={{ width: "100%", height: "100%", objectFit: "cover" }}
            />
          ) : (
            <Img
              src={staticFile(image as string)}
              style={{ width: "100%", height: "100%", objectFit: "cover" }}
            />
          )}
        </div>
        <Gradient />
        <Caption title={title} sub={sub} align={center ? "center" : "left"} />
      </AbsoluteFill>
    </Sequence>
  );
};

export const Documentary60: React.FC = () => {
  const { durationInFrames } = useVideoConfig();
  const frame = useCurrentFrame();
  const progress = (frame / durationInFrames) * 100;
  return (
    <AbsoluteFill style={{ background: "#000", fontFamily: FONT }}>
      <Audio src={staticFile("audio/voiceover.mp3")} />

      {/* 0-8s Hook */}
      <Shot from={0} dur={120} video="footage/s1a-shot1.mp4" title="ONE BALL." sub="One court. A million dreams." zoomIn />
      <Shot from={120} dur={120} video="footage/s1b-shot2.mp4" title="ONE SHOT." sub="That sound. That swish." zoomIn />

      {/* 8-20s Origins */}
      <Shot from={240} dur={180} image="images/naismith-hd.jpg" title="1891 • SPRINGFIELD" sub="James Naismith. A peach basket. Thirteen rules." />
      <Shot from={420} dur={180} video="footage/s2b-dribble.mp4" title="FROM A PEACH BASKET" sub="A game the whole world would love." zoomIn />

      {/* 20-35s Rise */}
      <Shot from={600} dur={240} video="footage/s3a-game.mp4" title="FROM SCHOOL GYMS" sub="Speed. Skill. Spectacle." zoomIn />
      <Shot from={840} dur={210} video="footage/s3b-lebron.mp4" title="TO SOLD-OUT ARENAS" sub="Icons who turned a sport into a culture." zoomIn />

      {/* 35-48s Global game */}
      <Shot from={1050} dur={210} video="footage/s4a-warmup.mp4" title="EVERY CONTINENT" sub="Street courts at sunset. Packed arenas." zoomIn />
      <Shot from={1260} dur={180} video="footage/s4b-stock.mp4" title="ONE LANGUAGE" sub="The ball. The hoop. The moment." zoomIn />

      {/* 48-60s Closing */}
      <Shot from={1440} dur={240} video="footage/s5a-game.mp4" title="MORE THAN A GAME" sub="Discipline. Teamwork. Belief." zoomIn />
      <Shot from={1680} dur={120} image="images/dunk.jpg" title="THE NEXT SHOT IS YOURS" center />

      {/* Progress bar */}
      <div
        style={{
          position: "absolute",
          left: 0,
          bottom: 0,
          height: 10,
          width: `${progress}%`,
          background: ORANGE,
        }}
      />
      <div
        style={{
          position: "absolute",
          right: 36,
          top: 30,
          fontFamily: FONT,
          color: "rgba(255,255,255,0.85)",
          fontSize: 24,
          letterSpacing: 4,
          fontWeight: 700,
          textShadow: "0 2px 8px rgba(0,0,0,0.8)",
        }}
      >
        {HEADER_TEXT}
      </div>
    </AbsoluteFill>
  );
};
