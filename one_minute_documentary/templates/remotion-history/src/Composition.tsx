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

// History / expedition variant — Iron Road / Lost Expeditions style.
// Warm archival palette, serif type, sepia grade, slow ken-burns pan.
// Text cards appear ONLY as era plates at the opening and closing (plus one
// parchment caption on a key beat per section); other shots carry just a
// small plate tag.

// Change these two lines for a new topic. The Iron Road is the worked example.
const TOPIC_TAG = "IRON ROAD";
const HEADER_TEXT = "THE RAILWAY THAT BOUND A CONTINENT";

const SERIF_FONT = "Georgia, 'Times New Roman', serif";
const PARCHMENT = "#e8dcc0";
const SEPIA = "#6b4a2b";
const BRASS = "#b98a2f";
const INK = "#2a2118";
const PLATE_BG = "rgba(232, 220, 192, 0.94)";

// SFX profile — mirrors template-config.json. The repo ships no real SFX
// files: set SFX_ENABLED = true and drop the files under public/audio/sfx/
// when real SFX are supplied. The voiceover level below comes from the
// profile, so the strings bed (when present) sits under the narration.
const SFX_ENABLED = false;
const SFX_PROFILE = {
  bed: { file: "audio/sfx/history-bed-strings.mp3", gainDb: -26 },
  transition: { file: "audio/sfx/history-paper-rustle.mp3", gainDb: -18 },
  ambience: { file: "audio/sfx/history-steam-train.mp3", gainDb: -22 },
  card: { file: "audio/sfx/history-era-chime.mp3", gainDb: -14 },
  voiceoverVolume: 1.0,
};

const fade = (local: number, dur: number) =>
  interpolate(local, [0, 20, dur - 20, dur], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

// Archival plate frame: brass double border, like a mounted photograph.
const PlateFrame: React.FC = () => (
  <>
    <div
      style={{
        position: "absolute",
        inset: 28,
        border: `2px solid ${BRASS}`,
        opacity: 0.75,
        pointerEvents: "none",
      }}
    />
    <div
      style={{
        position: "absolute",
        inset: 36,
        border: "1px solid rgba(185,138,47,0.45)",
        pointerEvents: "none",
      }}
    />
  </>
);

// Warm sepia wash over the footage.
const SepiaWash: React.FC = () => (
  <div
    style={{
      position: "absolute",
      inset: 0,
      background: "rgba(107, 74, 43, 0.16)",
      pointerEvents: "none",
    }}
  />
);

const PlateTag: React.FC<{ label: string }> = ({ label }) => (
  <div
    style={{
      position: "absolute",
      left: 64,
      top: 52,
      fontFamily: SERIF_FONT,
      color: PARCHMENT,
      fontSize: 24,
      letterSpacing: 5,
      textTransform: "uppercase",
      textShadow: "0 2px 10px rgba(0,0,0,0.9)",
    }}
  >
    {label}
  </div>
);

// Parchment caption plate — used on key beats only, never on every scene.
const CaptionPlate: React.FC<{ title: string; sub?: string }> = ({ title, sub }) => (
  <div
    style={{
      position: "absolute",
      left: 90,
      bottom: 110,
      maxWidth: 1250,
      background: PLATE_BG,
      borderTop: `5px solid ${BRASS}`,
      padding: "24px 34px",
      fontFamily: SERIF_FONT,
      color: INK,
    }}
  >
    <div
      style={{
        fontSize: 58,
        fontWeight: 700,
        lineHeight: 1.05,
        letterSpacing: 1,
      }}
    >
      {title}
    </div>
    {sub ? (
      <div
        style={{
          marginTop: 12,
          fontSize: 28,
          fontStyle: "italic",
          color: SEPIA,
        }}
      >
        {sub}
      </div>
    ) : null}
  </div>
);

// Full-screen era plate — opening and closing only.
const EraCard: React.FC<{ from: number; dur: number; era: string; title: string; sub?: string }> = ({
  from,
  dur,
  era,
  title,
  sub,
}) => {
  const frame = useCurrentFrame();
  const local = frame - from;
  const opacity = fade(local, dur);
  return (
    <Sequence from={from} durationInFrames={dur}>
      <AbsoluteFill style={{ opacity, background: PARCHMENT, fontFamily: SERIF_FONT }}>
        <div
          style={{
            position: "absolute",
            inset: 48,
            border: `3px solid ${INK}`,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            textAlign: "center",
            padding: "0 140px",
          }}
        >
          <div
            style={{
              position: "absolute",
              inset: 12,
              border: `1px solid ${BRASS}`,
              pointerEvents: "none",
            }}
          />
          <div
            style={{
              fontSize: 26,
              letterSpacing: 10,
              textTransform: "uppercase",
              color: SEPIA,
              marginBottom: 28,
            }}
          >
            {era}
          </div>
          <div
            style={{
              width: 180,
              height: 3,
              background: BRASS,
              marginBottom: 28,
            }}
          />
          <div
            style={{
              fontSize: 104,
              fontWeight: 700,
              lineHeight: 1.02,
              color: INK,
            }}
          >
            {title}
          </div>
          {sub ? (
            <div
              style={{
                marginTop: 26,
                fontSize: 30,
                fontStyle: "italic",
                color: SEPIA,
              }}
            >
              {sub}
            </div>
          ) : null}
          <div
            style={{
              marginTop: 44,
              fontSize: 22,
              letterSpacing: 6,
              textTransform: "uppercase",
              color: "#7a6a4f",
            }}
          >
            {TOPIC_TAG}
          </div>
        </div>
      </AbsoluteFill>
    </Sequence>
  );
};

const Shot: React.FC<{
  from: number;
  dur: number;
  plate: string;
  video?: string;
  image?: string;
  caption?: { title: string; sub?: string };
}> = ({ from, dur, plate, video, image, caption }) => {
  const frame = useCurrentFrame();
  const local = frame - from;
  const opacity = fade(local, dur);
  // Ken-burns pan: slow zoom plus a gentle horizontal drift.
  const scale = interpolate(local, [0, dur], [1.08, 1.16], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const translateX = interpolate(local, [0, dur], [-26, 26], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <Sequence from={from} durationInFrames={dur}>
      <AbsoluteFill style={{ opacity, overflow: "hidden", background: "#171208" }}>
        <div
          style={{
            width: "100%",
            height: "100%",
            transform: `scale(${scale}) translateX(${translateX}px)`,
            // Sepia grade on the media itself.
            filter: "sepia(0.35) contrast(0.95) brightness(0.98)",
          }}
        >
          {video ? (
            <OffthreadVideo
              src={staticFile(video)}
              muted
              style={{ width: "100%", height: "100%", objectFit: "cover" }}
            />
          ) : (
            <Img
              src={staticFile(image as string)}
              style={{ width: "100%", height: "100%", objectFit: "cover" }}
            />
          )}
        </div>
        <SepiaWash />
        <PlateFrame />
        <PlateTag label={plate} />
        {caption ? <CaptionPlate title={caption.title} sub={caption.sub} /> : null}
      </AbsoluteFill>
    </Sequence>
  );
};

export const Documentary60: React.FC = () => {
  const { durationInFrames } = useVideoConfig();
  const frame = useCurrentFrame();
  const progress = (frame / durationInFrames) * 100;
  return (
    <AbsoluteFill style={{ background: "#171208", fontFamily: SERIF_FONT }}>
      <Audio src={staticFile("audio/voiceover.mp3")} volume={SFX_PROFILE.voiceoverVolume} />
      {SFX_ENABLED ? (
        <>
          <Audio src={staticFile(SFX_PROFILE.bed.file)} volume={0.5} />
          <Audio src={staticFile(SFX_PROFILE.ambience.file)} volume={0.4} />
        </>
      ) : null}

      {/* 0-8s Hook: era plate, then the line */}
      <EraCard from={0} dur={150} era="An expedition in steel — 1863–1869" title="The Iron Road" sub="Two crews. One continent. A race across America." />
      <Shot from={150} dur={90} video="footage/s1b-shot2.mp4" plate="Plate I — The Line" caption={{ title: "Two crews. One continent.", sub: "East met West, and a nation followed." }} />

      {/* 8-20s Background */}
      <Shot from={240} dur={180} image="images/golden-spike.jpg" plate="Plate II — Promontory, 1869" />
      <Shot from={420} dur={180} video="footage/s2b-shot4.mp4" plate="Plate III — The Surveyors" caption={{ title: "Through Sierra granite", sub: "Fifteen tunnels, carved by hand and black powder." }} />

      {/* 20-35s Development */}
      <Shot from={600} dur={240} video="footage/s3a-shot5.mp4" plate="Plate IV — The Grade" />
      <Shot from={840} dur={210} video="footage/s3b-shot6.mp4" plate="Plate V — The Crews" caption={{ title: "Ten miles of track a day", sub: "Irish gangs from the east, Chinese gangs from the west." }} />

      {/* 35-48s Impact */}
      <Shot from={1050} dur={210} video="footage/s4a-shot7.mp4" plate="Plate VI — The Crossing" />
      <Shot from={1260} dur={180} video="footage/s4b-shot8.mp4" plate="Plate VII — The Towns" caption={{ title: "A nation, joined", sub: "A journey of months became a journey of days." }} />

      {/* 48-60s Closing: last shot, then the era plate closes */}
      <Shot from={1440} dur={210} video="footage/s5a-shot9.mp4" plate="Plate VIII — The Road West" />
      <EraCard from={1650} dur={150} era="Promontory, Utah — May 10, 1869" title="The Golden Spike" sub="The last spike. The first crossing." />

      {/* Progress bar */}
      <div
        style={{
          position: "absolute",
          left: 0,
          bottom: 0,
          height: 8,
          width: `${progress}%`,
          background: BRASS,
        }}
      />
      {/* Header sits above the footage only — the era plates (frames 0-149
          and 1650-1799) carry their own titles, and the header would cross
          the plate's top border. */}
      {frame >= 150 && frame < 1650 ? (
        <div
          style={{
            position: "absolute",
            right: 48,
            top: 44,
            fontFamily: SERIF_FONT,
            color: "rgba(232,220,192,0.9)",
            fontSize: 24,
            letterSpacing: 5,
            textTransform: "uppercase",
            textShadow: "0 2px 8px rgba(0,0,0,0.8)",
          }}
        >
          {HEADER_TEXT}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};
