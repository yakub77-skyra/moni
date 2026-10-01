import {
  AbsoluteFill,
  Audio,
  CalculateMetadataFunction,
  OffthreadVideo,
  Sequence,
} from "remotion";
import { loadFont as loadAnton } from "@remotion/google-fonts/Anton";
import { loadFont as loadArchivoBlack } from "@remotion/google-fonts/ArchivoBlack";
import React from "react";
import { resolveAsset } from "./lib/resolveAsset";

const { fontFamily: kickerFont } = loadAnton("normal", {
  weights: ["400"],
  subsets: ["latin"],
});

const { fontFamily: headlineFont } = loadArchivoBlack("normal", {
  weights: ["400"],
  subsets: ["latin"],
});

export type TrendingNewsReelProps = {
  clips: string[];
  headline: string;
  highlightWords?: string[];
  subhead?: string;
  dateText?: string;
  watermark?: string;
  audioSrc?: string;
  durationInFrames?: number;
};

export const TRENDING_CUT_SECONDS = 4;

const withWatchSuffix = (headline: string): string => {
  const trimmed = headline.trim();
  if (/\|\s*WATCH\s*$/i.test(trimmed)) {
    return trimmed;
  }
  return `${trimmed} | WATCH`;
};

const isHot = (word: string, highlights: Set<string>): boolean => {
  const clean = word.replace(/[^A-Z0-9'|]/g, "");
  return highlights.has(clean.toLowerCase()) || highlights.has(word.toLowerCase());
};

// Split headline words into 3 balanced lines (greedy on char count), so the
// block always reads as the reference's even 3-line stack regardless of font
// metrics. Highlight runs are grouped per line, so a red box never splits.
const balanceLines = (words: string[], lines = 3): string[][] => {
  if (words.length === 0) {
    return [];
  }
  const total = words.join(" ").length;
  const target = total / lines;
  const out: string[][] = [];
  let current: string[] = [];
  let currentLen = 0;
  for (const word of words) {
    const add = current.length === 0 ? word.length : word.length + 1;
    if (current.length > 0 && currentLen + add > target + 2 && out.length < lines - 1) {
      out.push(current);
      current = [];
      currentLen = 0;
    }
    current.push(word);
    currentLen += current.length === 1 ? word.length : add;
  }
  if (current.length > 0) {
    out.push(current);
  }
  return out;
};

const renderHeadline = (headline: string, highlightWords: string[]): React.ReactNode => {
  const highlights = new Set(highlightWords.map((w) => w.toLowerCase()));
  const words = withWatchSuffix(headline).toUpperCase().split(/\s+/);
  const lines = balanceLines(words, 3);
  return lines.map((lineWords, li) => {
    const runs: { hot: boolean; text: string }[] = [];
    for (const word of lineWords) {
      const hot = isHot(word, highlights);
      const last = runs[runs.length - 1];
      if (last && last.hot === hot) {
        last.text += ` ${word}`;
      } else {
        runs.push({ hot, text: word });
      }
    }
    return (
      <div key={li}>
        {runs.map((run, i) => (
          <React.Fragment key={i}>
            {run.hot ? (
              <span
                style={{
                  backgroundColor: "#E11D2E",
                  color: "#FFFFFF",
                  padding: "0 24px",
                  boxShadow: "70px 0 0 #E11D2E",
                }}
              >
                {run.text}
              </span>
            ) : (
              <span>{run.text}</span>
            )}
            {i < runs.length - 1 ? " " : ""}
          </React.Fragment>
        ))}
      </div>
    );
  });
};

const renderDateBadge = (dateText: string): React.ReactNode => {
  const parts = dateText.trim().split(/\s+/);
  if (parts.length < 2) {
    return <span>{dateText}</span>;
  }
  return (
    <>
      <div style={{ fontSize: 40, fontWeight: 800, lineHeight: 1 }}>{parts[0]}</div>
      <div style={{ fontSize: 22, fontWeight: 800, lineHeight: 1.1 }}>{parts.slice(1).join(" ")}</div>
    </>
  );
};

// Same template family as BreakingNewsReel: white top mask, kicker +
// headline block, date badge, watermark. Static overlays (no keyframes);
// motion comes only from the FootageMontage hard cuts underneath.
export const FootageMontage: React.FC<{ clips: string[]; totalFrames: number }> = ({
  clips,
  totalFrames,
}) => {
  const count = Math.max(1, Math.min(6, clips.length));
  const perClip = Math.max(1, Math.floor(totalFrames / count));
  return (
    <AbsoluteFill>
      {clips.slice(0, 6).map((clip, i) => {
        const from = i * perClip;
        const duration =
          i === count - 1 ? Math.max(1, totalFrames - from) : perClip;
        return (
          <Sequence key={i} from={from} durationInFrames={duration} name={`ugc-${i}`}>
            <OffthreadVideo
              src={resolveAsset(clip)}
              muted
              style={{ width: "100%", height: "100%", objectFit: "cover" }}
            />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

export const TrendingNewsReel: React.FC<TrendingNewsReelProps> = ({
  clips,
  headline,
  highlightWords = [],
  subhead = "",
  dateText = "",
  watermark = "@DAILYNEWS",
  audioSrc = "",
  durationInFrames = 30 * 20,
}) => {
  return (
    <AbsoluteFill style={{ backgroundColor: "#000000" }}>
      <FootageMontage clips={clips} totalFrames={durationInFrames} />
      {audioSrc ? <Audio src={resolveAsset(audioSrc)} /> : null}

      {/* White top mask, solid to ~31%, fully faded by ~38% of frame height */}
      <AbsoluteFill
        style={{
          background:
            "linear-gradient(to bottom, rgba(255,255,255,1) 0%, rgba(255,255,255,1) 31%, rgba(255,255,255,0) 38%)",
        }}
      />

      {/* Static headline block. Kicker spans full width like the reference. */}
      <div
        style={{
          position: "absolute",
          top: 17,
          left: 24,
          right: 24,
          textAlign: "center",
        }}
      >
        <div
          style={{
            color: "#111111",
            fontFamily: `${kickerFont}, system-ui, sans-serif`,
            fontSize: 178,
            letterSpacing: 6,
            lineHeight: 1,
          }}
        >
          TRENDING
        </div>
        <div
          style={{
            marginTop: 0,
            color: "#111111",
            fontFamily: `${headlineFont}, ${kickerFont}, system-ui, sans-serif`,
            fontSize: 34,
            lineHeight: 1.0,
            letterSpacing: 1,
            textAlign: "center",
            overflow: "hidden",
          }}
        >
          {renderHeadline(headline, highlightWords)}
        </div>
        {subhead ? (
          <div
            style={{
              marginTop: -8,
              color: "#1A1A1A",
              fontSize: 22,
              lineHeight: 1.3,
              fontWeight: 600,
              fontFamily: "system-ui, sans-serif",
              textAlign: "center",
            }}
          >
            {subhead}
          </div>
        ) : null}
      </div>

      {/* Date badge bottom-left: solid red rounded box, stacked day + month */}
      {dateText ? (
        <div
          style={{
            position: "absolute",
            left: 22,
            bottom: 18,
            width: 64,
            backgroundColor: "#E11D2E",
            color: "#FFFFFF",
            fontFamily: "system-ui, sans-serif",
            textAlign: "center",
            padding: "10px 4px",
            borderRadius: 8,
          }}
        >
          {renderDateBadge(dateText)}
        </div>
      ) : null}

      {/* Watermark bottom-right */}
      <div
        style={{
          position: "absolute",
          right: 27,
          bottom: 15,
          color: "#FFFFFF",
          fontSize: 20,
          fontWeight: 600,
          fontFamily: "system-ui, sans-serif",
          textShadow: "0 2px 8px rgba(0,0,0,0.8)",
        }}
      >
        {watermark}
      </div>
    </AbsoluteFill>
  );
};

export const calculateTrendingNewsMetadata: CalculateMetadataFunction<TrendingNewsReelProps> =
  async ({ props }) => {
    const clips = Math.max(1, Math.min(6, (props.clips || []).length));
    const fallback = clips * TRENDING_CUT_SECONDS * 30;
    return { durationInFrames: Math.max(30, props.durationInFrames ?? fallback) };
  };
