import {
  AbsoluteFill,
  Audio,
  CalculateMetadataFunction,
  OffthreadVideo,
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

export type BreakingNewsReelProps = {
  videoSrc: string;
  headline: string;
  highlightWords?: string[];
  subhead?: string;
  dateText?: string;
  watermark?: string;
  audioSrc?: string;
  durationInFrames?: number;
};

const isHot = (word: string, highlights: Set<string>): boolean => {
  const clean = word.replace(/[^A-Z0-9]/g, "");
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
  const words = headline.toUpperCase().split(/\s+/);
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
      <div style={{ fontSize: 38, fontWeight: 800, lineHeight: 1 }}> {parts[0]}</div>
      <div style={{ fontSize: 20, fontWeight: 800, lineHeight: 1.1 }}>{parts.slice(1).join(" ")}</div>
    </>
  );
};

// Static overlay by design: ZERO keyframe/spring animation on text.
// Only the background video moves. This keeps breaking news readable.
export const BreakingNewsReel: React.FC<BreakingNewsReelProps> = ({
  videoSrc,
  headline,
  highlightWords = [],
  subhead = "",
  dateText = "",
  watermark = "@DAILYNEWS",
  audioSrc = "",
}) => {
  return (
    <AbsoluteFill style={{ backgroundColor: "#000000" }}>
      {videoSrc ? (
        <OffthreadVideo
          src={resolveAsset(videoSrc)}
          muted={Boolean(audioSrc)}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      ) : null}
      {audioSrc ? <Audio src={resolveAsset(audioSrc)} /> : null}

      {/* White top mask, solid to ~27%, fully faded by ~36% of frame height */}
      <AbsoluteFill
        style={{
          background:
            "linear-gradient(to bottom, rgba(255,255,255,1) 0%, rgba(255,255,255,1) 23.5%, rgba(255,255,255,0) 31.5%)",
        }}
      />

      {/* Static headline block */}
      <div
        style={{
          position: "absolute",
          top: 47,
          left: 40,
          right: 40,
          textAlign: "center",
        }}
      >
        <div
          style={{
            color: "#111111",
            fontFamily: `${kickerFont}, system-ui, sans-serif`,
            fontSize: 164,
            letterSpacing: 2,
            lineHeight: 1,
          }}
        >
          BREAKING
        </div>
        <div
          style={{
            marginTop: 2,
            color: "#111111",
            fontFamily: `${headlineFont}, ${kickerFont}, system-ui, sans-serif`,
            fontSize: 34,
            lineHeight: 1.15,
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
              marginTop: 0,
              color: "#1A1A1A",
              fontSize: 22,
              letterSpacing: 0.5,
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

      {/* Date badge bottom-left: solid red, stacked day + month */}
      {dateText ? (
        <div
          style={{
            position: "absolute",
            left: 64,
            bottom: 65,
            width: 54,
            backgroundColor: "#E11D2E",
            color: "#FFFFFF",
            fontFamily: "system-ui, sans-serif",
            textAlign: "center",
            padding: "10px 4px",
          }}
        >
          {renderDateBadge(dateText)}
        </div>
      ) : null}

      {/* Watermark bottom-right */}
      <div
        style={{
          position: "absolute",
          right: 40,
          bottom: 70,
          color: "#FFFFFF",
          fontSize: 21,
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

export const calculateBreakingNewsMetadata: CalculateMetadataFunction<BreakingNewsReelProps> =
  async ({ props }) => {
    return { durationInFrames: Math.max(30, props.durationInFrames ?? 30 * 20) };
  };
