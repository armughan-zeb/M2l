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

// Crime / mystery variant — Black Box / Vanished at Sea style.
// Dark navy palette, condensed type, mono evidence labels, hard cuts.
// Text cards appear ONLY at the opening and closing (and one key beat
// lower third each); every other shot carries just a small exhibit tag.

// Change these two lines for a new topic. Mary Celeste is the worked example.
const TOPIC_TAG = "MARY CELESTE";
const HEADER_TEXT = "VANISHED AT SEA";

const TITLE_FONT = "'Arial Narrow', 'Helvetica Neue', Arial, sans-serif";
const MONO_FONT = "'Courier New', Courier, monospace";
const INK = "#e8e6e1";
const STEEL = "#7fa8c9";
const RED = "#c8102e";
const PANEL = "rgba(7, 9, 13, 0.88)";

// SFX profile — mirrors template-config.json. The repo ships no real SFX
// files: set SFX_ENABLED = true and drop the files under public/audio/sfx/
// when real SFX are supplied. The voiceover level below comes from the
// profile, so the tense bed (when present) sits under the narration.
const SFX_ENABLED = false;
const SFX_PROFILE = {
  bed: { file: "audio/sfx/crime-bed-drone.mp3", gainDb: -24 },
  transition: { file: "audio/sfx/crime-tape-stop.mp3", gainDb: -12 },
  sting: { file: "audio/sfx/crime-heartbeat-sting.mp3", gainDb: -10 },
  card: { file: "audio/sfx/crime-paper-shuffle.mp3", gainDb: -16 },
  voiceoverVolume: 0.92,
};

// Hard cut: 6-frame dip in/out instead of the standard template's 15-frame fade.
const cut = (local: number, dur: number) =>
  interpolate(local, [0, 6, dur - 6, dur], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

const Vignette: React.FC = () => (
  <div
    style={{
      position: "absolute",
      inset: 0,
      background:
        "radial-gradient(ellipse at center, rgba(0,0,0,0) 55%, rgba(0,0,0,0.55) 100%)",
    }}
  />
);

const ExhibitTag: React.FC<{ label: string }> = ({ label }) => (
  <div
    style={{
      position: "absolute",
      left: 48,
      top: 40,
      display: "flex",
      alignItems: "center",
      gap: 12,
      fontFamily: MONO_FONT,
      color: INK,
      fontSize: 22,
      letterSpacing: 3,
      textShadow: "0 2px 8px rgba(0,0,0,0.9)",
    }}
  >
    <span
      style={{ width: 14, height: 14, background: RED, display: "inline-block" }}
    />
    {label}
  </div>
);

// Evidence lower third — used on key beats only, never on every scene.
const EvidenceCard: React.FC<{ kicker: string; title: string; sub?: string }> = ({
  kicker,
  title,
  sub,
}) => (
  <div
    style={{
      position: "absolute",
      left: 90,
      bottom: 110,
      maxWidth: 1300,
      background: PANEL,
      borderLeft: `8px solid ${RED}`,
      padding: "22px 30px",
      fontFamily: TITLE_FONT,
      color: INK,
    }}
  >
    <div
      style={{
        fontFamily: MONO_FONT,
        fontSize: 22,
        letterSpacing: 4,
        color: STEEL,
        marginBottom: 10,
      }}
    >
      {kicker}
    </div>
    <div
      style={{
        fontSize: 68,
        fontWeight: 900,
        lineHeight: 1.0,
        letterSpacing: 1,
        textTransform: "uppercase",
      }}
    >
      {title}
    </div>
    {sub ? (
      <div
        style={{
          marginTop: 12,
          fontFamily: MONO_FONT,
          fontSize: 26,
          letterSpacing: 1,
          color: "#cfccc4",
        }}
      >
        {sub}
      </div>
    ) : null}
  </div>
);

// Full-screen case-file title card — opening and closing only.
const TitleCard: React.FC<{ from: number; dur: number; kicker: string; title: string; sub?: string }> = ({
  from,
  dur,
  kicker,
  title,
  sub,
}) => {
  const frame = useCurrentFrame();
  const local = frame - from;
  const opacity = cut(local, dur);
  return (
    <Sequence from={from} durationInFrames={dur}>
      <AbsoluteFill style={{ opacity, background: "#07090d", fontFamily: TITLE_FONT }}>
        <div
          style={{
            position: "absolute",
            left: 120,
            right: 120,
            top: 0,
            bottom: 0,
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
          }}
        >
          <div style={{ height: 6, background: RED, width: 220, marginBottom: 36 }} />
          <div
            style={{
              fontFamily: MONO_FONT,
              fontSize: 28,
              letterSpacing: 8,
              color: STEEL,
              marginBottom: 24,
            }}
          >
            {kicker}
          </div>
          <div
            style={{
              fontSize: 120,
              fontWeight: 900,
              lineHeight: 0.98,
              letterSpacing: 1,
              color: INK,
              textTransform: "uppercase",
            }}
          >
            {title}
          </div>
          {sub ? (
            <div
              style={{
                marginTop: 28,
                fontFamily: MONO_FONT,
                fontSize: 30,
                letterSpacing: 2,
                color: "#b9b6ad",
              }}
            >
              {sub}
            </div>
          ) : null}
          <div style={{ height: 2, background: "#2a3340", width: "100%", marginTop: 48 }} />
          <div
            style={{
              marginTop: 18,
              fontFamily: MONO_FONT,
              fontSize: 22,
              letterSpacing: 4,
              color: "#6d7684",
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
  exhibit: string;
  video?: string;
  image?: string;
  card?: { kicker: string; title: string; sub?: string };
  zoomIn?: boolean;
}> = ({ from, dur, exhibit, video, image, card, zoomIn }) => {
  const frame = useCurrentFrame();
  const local = frame - from;
  const opacity = cut(local, dur);
  // Slow push-in only; no drift — the camera holds still, like evidence.
  const scale = interpolate(local, [0, dur], zoomIn ? [1.0, 1.08] : [1.08, 1.0], {
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
            // Cold grade: desaturate and cool the footage.
            filter: "saturate(0.75) brightness(0.92)",
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
        <Vignette />
        <ExhibitTag label={exhibit} />
        {card ? <EvidenceCard kicker={card.kicker} title={card.title} sub={card.sub} /> : null}
      </AbsoluteFill>
    </Sequence>
  );
};

export const Documentary60: React.FC = () => {
  const { durationInFrames } = useVideoConfig();
  const frame = useCurrentFrame();
  const progress = (frame / durationInFrames) * 100;
  const seconds = Math.floor(frame / 30);
  const timecode = `00:${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(
    seconds % 60
  ).padStart(2, "0")}`;
  return (
    <AbsoluteFill style={{ background: "#000", fontFamily: TITLE_FONT }}>
      <Audio src={staticFile("audio/voiceover.mp3")} volume={SFX_PROFILE.voiceoverVolume} />
      {SFX_ENABLED ? (
        <>
          <Audio src={staticFile(SFX_PROFILE.bed.file)} volume={0.5} />
        </>
      ) : null}

      {/* 0-8s Hook: title card, then the ship */}
      <TitleCard from={0} dur={150} kicker="CASE FILE 1872 — ATLANTIC OCEAN" title="The Mary Celeste" sub="A seaworthy ship. A crew of ten. No one aboard." />
      <Shot from={150} dur={90} video="footage/s1b-shot2.mp4" exhibit="EXHIBIT 01 — 04 DEC 1872" card={{ kicker: "EVIDENCE 01 — THE FINDING", title: "A ship with no crew", sub: "Found adrift. Sails set. Cargo intact." }} zoomIn />

      {/* 8-20s Background */}
      <Shot from={240} dur={180} image="images/logbook.jpg" exhibit="EXHIBIT 02 — CAPTAIN'S LOG" />
      <Shot from={420} dur={180} video="footage/s2b-shot4.mp4" exhibit="EXHIBIT 03 — NEW YORK HARBOUR" card={{ kicker: "EVIDENCE 02 — THE CREW", title: "Ten souls aboard", sub: "Captain Briggs. His wife. His daughter. Seven sailors." }} zoomIn />

      {/* 20-35s Development */}
      <Shot from={600} dur={240} video="footage/s3a-shot5.mp4" exhibit="EXHIBIT 04 — THE ATLANTIC" zoomIn />
      <Shot from={840} dur={210} video="footage/s3b-shot6.mp4" exhibit="EXHIBIT 05 — BELOW DECK" card={{ kicker: "EVIDENCE 03 — THE CABIN", title: "No sign of struggle", sub: "Beds unmade. A meal half-eaten. One lifeboat gone." }} zoomIn />

      {/* 35-48s Impact */}
      <Shot from={1050} dur={210} video="footage/s4a-shot7.mp4" exhibit="EXHIBIT 06 — THE SEARCH" zoomIn />
      <Shot from={1260} dur={180} video="footage/s4b-shot8.mp4" exhibit="EXHIBIT 07 — THE LAST ENTRY" card={{ kicker: "EVIDENCE 04 — 25 NOV 1872", title: "The log just stops", sub: "Nine days before the ship was found." }} zoomIn />

      {/* 48-60s Closing: last shot, then the case file closes */}
      <Shot from={1440} dur={210} video="footage/s5a-shot9.mp4" exhibit="EXHIBIT 08 — OPEN WATER" zoomIn />
      <TitleCard from={1650} dur={150} kicker="CASE STATUS — UNSOLVED" title="Vanished at sea" sub="No answers. Only questions." />

      {/* Progress bar + timecode */}
      <div
        style={{
          position: "absolute",
          left: 0,
          bottom: 0,
          height: 6,
          width: `${progress}%`,
          background: RED,
        }}
      />
      <div
        style={{
          position: "absolute",
          right: 36,
          top: 30,
          fontFamily: MONO_FONT,
          color: "rgba(232,230,225,0.85)",
          fontSize: 24,
          letterSpacing: 4,
          fontWeight: 700,
          textShadow: "0 2px 8px rgba(0,0,0,0.8)",
        }}
      >
        {HEADER_TEXT} — {timecode}
      </div>
    </AbsoluteFill>
  );
};
