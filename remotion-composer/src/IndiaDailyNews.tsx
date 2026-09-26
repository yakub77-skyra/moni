import {
  AbsoluteFill,
  Audio,
  Img,
  OffthreadVideo,
  Sequence,
  continueRender,
  delayRender,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import React, { useEffect, useState } from "react";
import { loadFont as loadPlayfair } from "@remotion/google-fonts/PlayfairDisplay";
import { resolveAsset } from "./lib/resolveAsset";

const { fontFamily: headlineFont } = loadPlayfair("normal", {
  weights: ["700"],
  subsets: ["latin"],
});

export interface IndiaDailyNewsCard {
  rank: number;
  state: string;
  outlet: string;
  outlet_name?: string;
  headline: string;
  narration: string;
  card_subtitle?: string;
  source_url: string;
  clipSrc: string;
  clipSourceUrl?: string;
  imageSrc?: string;
  imageCredit?: string;
  narrationSeconds: number;
  durationInFrames: number;
  fromFrame: number;
}

export interface IndiaDailyNewsSource {
  outlet: string;
  url: string;
}

export type IndiaDailyNewsProps = {
  cards: IndiaDailyNewsCard[];
  audioSrc: string;
  mapPathsSrc: string;
  sources: IndiaDailyNewsSource[];
  endCardFrames: number;
  durationInFrames: number;
};

interface MapData {
  paths: Record<string, string>;
  centroids: Record<string, [number, number]>;
  pans: Record<string, [number, number]>;
  outline: string;
}

const NEON = "#C6FF00";
const STATE_GREEN = "#2FD75D";
const BADGE_BROWN = "#7A2E12";
const CARD_TOP = 110;
const CARD_HEIGHT = 330;
const CARD_BOTTOM = CARD_TOP + CARD_HEIGHT;

// ---------------------------------------------------------------------------
// State-name matching: card.state comes from STATE_KEYWORDS, path keys come
// from the geohacker GeoJSON NAME_1 field. Normalize both sides and cover
// the known spelling variants.
// ---------------------------------------------------------------------------
const STATE_ALIASES: Record<string, string> = {
  nctdelhi: "delhi",
  nationalcapitalterritoryofdelhi: "delhi",
  jammukashmir: "jammuandkashmir",
  orissa: "odisha",
  uttaranchal: "uttarakhand",
  pondicherry: "india",
};

const normalizeState = (name: string): string => {
  const flat = name.toLowerCase().replace(/[^a-z]/g, "");
  return STATE_ALIASES[flat] ?? flat;
};

const FOCUS_X = 360;
const FOCUS_Y = 760;

const findState = (
  map: MapData,
  state: string,
): { path: string; centroid: [number, number]; pan: [number, number] } | null => {
  const want = normalizeState(state);
  for (const [key, path] of Object.entries(map.paths)) {
    if (normalizeState(key) === want) {
      return {
        path,
        centroid: map.centroids[key] ?? [FOCUS_X, FOCUS_Y],
        pan: map.pans?.[key] ?? [0, 0],
      };
    }
  }
  return null;
};

// ---------------------------------------------------------------------------
// One map card: dark India, highlighted state, rank badge, neon connector,
// dashed media card, white headline bar, watermark.
// ---------------------------------------------------------------------------
const MapCard: React.FC<{
  card: IndiaDailyNewsCard;
  map: MapData;
}> = ({ card, map }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const match = findState(map, card.state);
  // The map pans so the story's state lands in the open band below the
  // headline bar; the badge then sits just below that focus point so the
  // highlighted state reads around it, as in the reference frames.
  const [panX, panY] = match?.pan ?? [0, 0];
  // The badge tracks the (panned) state centroid, nudged below it so the
  // highlighted shape reads around the badge instead of hiding under it.
  const badgeX = match
    ? Math.min(630, Math.max(90, match.centroid[0] + panX))
    : FOCUS_X;
  const badgeY = match
    ? Math.min(
        1090,
        Math.max(CARD_BOTTOM + 190, match.centroid[1] + panY + 70),
      )
    : FOCUS_Y;
  const barTop = CARD_BOTTOM - 44;

  const badgeScale = spring({
    frame,
    fps,
    config: { damping: 12, stiffness: 160, mass: 0.9 },
  });
  const connectorP = interpolate(frame, [6, 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const cardIn = interpolate(frame, [0, 12], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const cardRise = interpolate(cardIn, [0, 1], [26, 0]);
  const headlineIn = interpolate(frame, [8, 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const zoom = 1 + 0.03 * (frame / Math.max(1, durationInFrames));
  const progress = interpolate(frame, [0, durationInFrames], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const fadeOut = interpolate(
    frame,
    [durationInFrames - 8, durationInFrames],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  const midX = (badgeX + 360) / 2 + 60;
  const connectorD = `M${badgeX},${badgeY - 46} Q${midX},${(badgeY - 46 + barTop) / 2} 360,${barTop}`;

  return (
    <AbsoluteFill style={{ opacity: fadeOut, backgroundColor: "#0A0C0F" }}>
      {/* Map layer — panned per story so the highlighted state is never
          hidden behind the media card or the headline bar */}
      <AbsoluteFill
        style={{
          transform: `translate(${panX}px, ${panY}px) scale(${zoom})`,
          transformOrigin: "50% 50%",
        }}
      >
        <svg
          viewBox="0 0 720 1280"
          width="100%"
          height="100%"
          preserveAspectRatio="xMidYMid slice"
        >
          {/* Country silhouette: fill-only (adjacent state fills merge with
              no internal seams) + drop-shadow glows only at the outer
              coastline, matching the reference frames */}
          <path
            d={map.outline}
            fill="#16191F"
            stroke="#16191F"
            strokeWidth={1}
            style={{ filter: "drop-shadow(0 0 5px rgba(255,255,255,0.85))" }}
          />
          {match ? (
            <path
              d={match.path}
              fill={STATE_GREEN}
              fillOpacity={0.9}
              stroke="#FFFFFF"
              strokeWidth={3}
              style={{ filter: "drop-shadow(0 0 10px rgba(47,215,93,0.75))" }}
            />
          ) : null}
        </svg>
      </AbsoluteFill>

      {/* Media card */}
      <div
        style={{
          position: "absolute",
          left: 48,
          top: CARD_TOP + cardRise,
          width: 624,
          height: CARD_HEIGHT,
          opacity: cardIn,
          border: `5px dashed ${NEON}`,
          borderRadius: 6,
          overflow: "hidden",
          backgroundColor: "#000",
          boxShadow: `0 0 24px rgba(198,255,0,0.35)`,
        }}
      >
        {card.imageSrc ? (
          // The real news photograph, with a slow push so a still does not
          // read as a frozen slideshow frame.
          <Img
            src={resolveAsset(card.imageSrc)}
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              transform: `scale(${1.06 + 0.1 * progress}) translateX(${
                -1.2 + 2.4 * progress
              }%)`,
            }}
          />
        ) : card.clipSrc ? (
          // OffthreadVideo (not <Video loop>) on purpose: it is frame-accurate
          // and deterministic. A clip shorter than its card simply holds its
          // last frame rather than looping, which keeps renders reproducible.
          <OffthreadVideo
            src={resolveAsset(card.clipSrc)}
            muted
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        ) : null}
      </div>

      {/* Neon connector: under the bar so the segment crossing it hides
          behind the white bar and meets its top edge cleanly */}
      <svg
        viewBox="0 0 720 1280"
        width="100%"
        height="100%"
        style={{ position: "absolute", inset: 0 }}
      >
        <path
          d={connectorD}
          fill="none"
          stroke={NEON}
          strokeWidth={6}
          strokeLinecap="round"
          pathLength={1}
          strokeDasharray={1}
          strokeDashoffset={1 - connectorP}
          style={{ filter: `drop-shadow(0 0 8px ${NEON})` }}
        />
      </svg>

      {/* Headline bar */}
      <div
        style={{
          position: "absolute",
          left: 62,
          top: CARD_BOTTOM - 44,
          width: 596,
          opacity: headlineIn,
          transform: `translateY(${(1 - headlineIn) * 20}px)`,
          backgroundColor: "#FFFFFF",
          padding: "16px 20px",
        }}
      >
        <div
          style={{
            color: "#111",
            fontSize: 29,
            lineHeight: 1.25,
            fontFamily: `${headlineFont}, Georgia, serif`,
            fontWeight: 700,
            display: "-webkit-box",
            WebkitLineClamp: 3,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {card.headline}
        </div>
        <div
          style={{
            marginTop: 8,
            color: "#444",
            fontSize: 20,
            fontFamily: "system-ui, sans-serif",
          }}
        >
          {card.outlet_name || card.outlet} • {card.state}
        </div>
        {card.imageCredit ? (
          // Attribution for the publisher's photograph. Crediting the source
          // on the card (not only on the end card) is what a reposting news
          // account owes the outlet.
          <div
            style={{
              marginTop: 4,
              color: "#777",
              fontSize: 16,
              fontFamily: "system-ui, sans-serif",
            }}
          >
            Photo: {card.imageCredit}
          </div>
        ) : null}
      </div>

      {/* Rank badge (topmost: never buried under the bar) */}
      <div
        style={{
          position: "absolute",
          left: badgeX - 46,
          top: badgeY - 46,
          width: 92,
          height: 92,
          borderRadius: 46,
          backgroundColor: "#FFFFFF",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          transform: `scale(${badgeScale})`,
          boxShadow: "0 4px 24px rgba(0,0,0,0.5)",
        }}
      >
        <span
          style={{
            color: BADGE_BROWN,
            fontSize: 56,
            fontWeight: 800,
            fontFamily: "system-ui, sans-serif",
            lineHeight: 1,
          }}
        >
          {card.rank}
        </span>
      </div>

      {/* Watermark */}
      <div
        style={{
          position: "absolute",
          left: 48,
          bottom: 64,
          color: "#FFFFFF",
          fontSize: 24,
          fontWeight: 700,
          fontFamily: "system-ui, sans-serif",
          textShadow: "0 2px 8px rgba(0,0,0,0.8)",
        }}
      >
        @INDIAINLAST24HR
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// Source end card: every outlet + URL (manifest success criterion).
// ---------------------------------------------------------------------------
const SourceEndCard: React.FC<{ sources: IndiaDailyNewsSource[] }> = ({
  sources,
}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 10], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#0A0C0F",
        opacity,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        padding: "0 56px",
      }}
    >
      <div
        style={{
          color: NEON,
          fontSize: 34,
          fontWeight: 800,
          fontFamily: "system-ui, sans-serif",
          letterSpacing: 4,
          marginBottom: 28,
        }}
      >
        SOURCES
      </div>
      {sources.map((source, index) => (
        <div key={index} style={{ marginBottom: 22 }}>
          <div
            style={{
              color: "#FFF",
              fontSize: 28,
              fontWeight: 700,
              fontFamily: "system-ui, sans-serif",
            }}
          >
            {index + 1}. {source.outlet}
          </div>
          <div
            style={{
              color: "#9AA0A6",
              fontSize: 19,
              fontFamily: "system-ui, sans-serif",
              wordBreak: "break-all",
            }}
          >
            {source.url}
          </div>
        </div>
      ))}
      <div
        style={{
          marginTop: 36,
          color: "#FFF",
          fontSize: 24,
          fontWeight: 700,
          fontFamily: "system-ui, sans-serif",
        }}
      >
        @INDIAINLAST24HR • follow for daily news
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// Root composition component.
// ---------------------------------------------------------------------------
export const IndiaDailyNews: React.FC<IndiaDailyNewsProps> = (props) => {
  const { cards, audioSrc, mapPathsSrc, sources, endCardFrames } = props;
  const [map, setMap] = useState<MapData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [handle] = useState(() => delayRender("india-map-load"));

  useEffect(() => {
    let cancelled = false;
    fetch(staticFile(mapPathsSrc))
      .then((response) => {
        if (!response.ok) {
          throw new Error(`map fetch failed: HTTP ${response.status}`);
        }
        return response.json();
      })
      .then((data: MapData) => {
        if (!cancelled) {
          setMap(data);
          continueRender(handle);
        }
      })
      .catch((error: Error) => {
        if (cancelled) {
          return;
        }
        // Release the render and show the reason on the frame. Throwing here
        // instead would leave delayRender pending and Remotion would only
        // report an opaque 30s timeout.
        setLoadError(error.message);
        continueRender(handle);
      });
    return () => {
      cancelled = true;
    };
  }, [mapPathsSrc, handle]);

  if (loadError) {
    return (
      <AbsoluteFill
        style={{
          backgroundColor: "#0A0C0F",
          color: "#FF6B6B",
          padding: 48,
          fontFamily: "system-ui, sans-serif",
          fontSize: 30,
          lineHeight: 1.4,
        }}
      >
        <div style={{ color: NEON, fontWeight: 800, marginBottom: 16 }}>
          MAP LOAD FAILED
        </div>
        <div>{loadError}</div>
        <div style={{ color: "#9AA0A6", fontSize: 22, marginTop: 16 }}>
          mapPathsSrc: {mapPathsSrc}
        </div>
      </AbsoluteFill>
    );
  }

  if (!map) {
    return null;
  }

  const storyFrames = cards.reduce(
    (total, card) => total + card.durationInFrames,
    0,
  );

  return (
    <AbsoluteFill style={{ backgroundColor: "#0A0C0F" }}>
      {audioSrc ? <Audio src={resolveAsset(audioSrc)} /> : null}
      {cards.map((card) => (
        <Sequence
          key={card.rank}
          from={card.fromFrame}
          durationInFrames={card.durationInFrames}
          name={`card-${card.rank}`}
        >
          <MapCard card={card} map={map} />
        </Sequence>
      ))}
      <Sequence
        from={storyFrames}
        durationInFrames={endCardFrames}
        name="sources"
      >
        <SourceEndCard sources={sources} />
      </Sequence>
    </AbsoluteFill>
  );
};
