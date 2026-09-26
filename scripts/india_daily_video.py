"""Build licensed assets and Remotion props for the India daily-news video.

All network costs are zero: public RSS, OpenRouter's free router, Pexels'
free API, and local Windows SAPI narration. Publisher lead images remain
reference-only; the renderer uses Pexels-licensed video footage.

Stages (mirrors pipeline_defs/india-in-last-24hr.yaml):
  assets  -> per-card Pexels clips + SAPI narration + boundary GeoJSON
  edit    -> concat per-card WAVs into one narration.wav, frame timing
  compose -> render-props.json for the IndiaDailyNews Remotion scene
             (--render also invokes `npx remotion render`)

Usage:
    python scripts/india_daily_video.py [--project projects/india-in-last-24hr]
    python scripts/india_daily_video.py --project ... --render
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.tool_registry import ToolRegistry, registry  # noqa: E402

MAP_URL = "https://raw.githubusercontent.com/geohacker/india/master/state/india_state.geojson"
MAP_LICENSE = "CC BY 4.0 (geohacker/india repository); boundaries derived from GADM"
MAP_SOURCE = "https://github.com/geohacker/india"
VOICE_NAME = "Microsoft Zira Desktop"
FPS = 30
RENDER_WIDTH = 720
RENDER_HEIGHT = 1280
# Map bleed: the reference frames keep the whole country in frame (only a
# slight push-in), so the highlighted state is always readable in place.
MAP_ZOOM = 1.12
# Where a story's state is nudged to on screen: the open map band under the
# headline bar. The pan is clamped so the country never leaves the frame.
FOCUS_X = 360
FOCUS_Y = 760
PAN_LIMIT_X = 120
PAN_LIMIT_Y = 150
# Last-resort stock query when a story-specific search has no portrait match.
FALLBACK_VISUAL_QUERY = "India city skyline"
# Silence pads around each card in the assembled narration track.
PAD_BEFORE_SECONDS = 0.25
PAD_BETWEEN_SECONDS = 0.45

FFMPEG = REPO_ROOT / "bin" / "ffmpeg.exe"
FFPROBE = REPO_ROOT / "bin" / "ffprobe.exe"


def _ffmpeg() -> str | None:
    if FFMPEG.is_file():
        return str(FFMPEG)
    return shutil.which("ffmpeg")


def _ffprobe() -> str | None:
    if FFPROBE.is_file():
        return str(FFPROBE)
    return shutil.which("ffprobe")


_UNSAFE_QUERY_TERMS = {
    "reuters", "associated press", "ap photo", "afp", "getty images",
    "pti", "indian express", "times of india", "hindustan times", "ndtv",
    "the hindu", "india today", "news18", "firstpost", "business standard",
    "livemint", "deccan herald", "bengaluru metro", "metro pink line",
    "sana", "vijayan", "shane warne", "drowned", "gay drama",
}


def _force_utf8_console() -> None:
    """Make stdout/stderr UTF-8 so valid news text can never crash a print.

    A Windows console defaults to cp1252, which cannot encode characters that
    appear in ordinary rewritten copy (U+2011 non-breaking hyphen, curly
    quotes, em dashes). Printing one of those raises UnicodeEncodeError and
    kills the run after the work has already succeeded.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def sanitize_visual_query(query: str) -> str:
    """Reduce a news query to broad, license-safe Indian B-roll terms."""
    value = re.sub(r"[^a-zA-Z0-9 +-]", " ", query or "").lower()
    safe: list[str] = []
    for token in value.split():
        if token in _UNSAFE_QUERY_TERMS or any(
            term in token for term in ("photographer", "photo", "reuters")
        ):
            continue
        if token not in safe:
            safe.append(token)
    if not safe:
        return FALLBACK_VISUAL_QUERY
    result = " ".join(safe[:8])
    return result if "india" in result.lower() else f"{result} India"


def normalize_for_tts(text: str) -> str:
    """Fold typographic characters SAPI mispronounces (or skips) to ASCII.

    Rewritten news copy is full of non-breaking hyphens, curly quotes, em
    dashes and the rupee sign. Left alone, a synthesiser may read them as
    nothing at all, silently dropping words from the narration.
    """
    replacements = {
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
        "\u2014": " - ", "\u2015": " - ", "\u2212": "-",
        "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
        "\u201c": '"', "\u201d": '"', "\u201e": '"',
        "\u2026": "...", "\u00a0": " ", "\u200b": "",
        "\u20b9": " rupees ", "\u20bd": " euros ",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def sapi_powershell_script() -> str:
    """The PowerShell that drives System.Speech, with a voice fallback.

    Runner images do not guarantee "Microsoft Zira Desktop" exists, and
    SelectVoice throws on a missing name, so fall back to the first enabled
    voice and report which one was actually used.
    """
    return (
        "Add-Type -AssemblyName System.Speech; "
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate=1; "
        "$name=$env:INDIA_NEWS_VOICE; "
        "try { $s.SelectVoice($name) } catch { "
        "  $v=$s.GetInstalledVoices() | Where-Object { $_.Enabled } | "
        "Select-Object -First 1; "
        "  if ($v) { $s.SelectVoice($v.VoiceInfo.Name); $name=$v.VoiceInfo.Name } "
        "  else { $name='' } "
        "}; "
        "if ($name) { Write-Output ('SAPI_VOICE=' + $name) }; "
        "$s.SetOutputToWaveFile($env:INDIA_NEWS_OUTPUT); "
        "$s.Speak($env:INDIA_NEWS_TEXT); $s.Dispose()"
    )


def narrate_with_sapi(
    text: str,
    output_path: Path,
    run: Callable[[list[str], dict[str, str]], Any] | None = None,
) -> dict[str, Any]:
    """Create WAV narration with the local Windows SAPI voice (no API charge)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command_run = run or (
        lambda command, env: subprocess.run(
            command, env=env, check=True, capture_output=True, text=True
        )
    )
    spoken = normalize_for_tts(text)
    env = os.environ.copy()
    env["INDIA_NEWS_TEXT"] = spoken
    env["INDIA_NEWS_OUTPUT"] = str(output_path)
    env["INDIA_NEWS_VOICE"] = VOICE_NAME
    completed = command_run(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            sapi_powershell_script(),
        ],
        env,
    )
    if not output_path.is_file() or output_path.stat().st_size < 44:
        raise RuntimeError(
            f"SAPI did not create a WAV file: {output_path} "
            "(no usable Windows speech voice on this machine?)"
        )
    voice = VOICE_NAME
    stdout = getattr(completed, "stdout", "") or ""
    for line in str(stdout).splitlines():
        if line.strip().startswith("SAPI_VOICE="):
            voice = line.split("=", 1)[1].strip() or VOICE_NAME
    return {"provider": "windows_sapi", "voice": voice, "cost_usd": 0.0}


def _media_duration(path: Path) -> float | None:
    """Duration in seconds via ffprobe, or None when it is unavailable.

    Best effort on purpose: the only caller records an informational
    provenance field that nothing downstream reads, so a missing ffprobe
    must not be able to fail the run. (Do not reintroduce a hard failure
    here, and do not hand-roll an MP4 parser for it -- real stock clips are
    fragmented MP4, whose duration lives in moof/trun, not mvhd.)
    """
    probe = _ffprobe()
    if probe is None:
        return None
    try:
        result = subprocess.run(
            [
                probe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None


def _wav_duration(path: Path) -> float:
    """Read a WAV duration from its header (no subprocess needed)."""
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate() or 1)


def _concat_wavs(
    audio_paths: list[Path],
    output_path: Path,
    pad_before: float,
    pad_between: float,
) -> dict[str, Any] | None:
    """Pure-stdlib WAV concat with silence pads. Returns None if formats differ.

    SAPI emits one consistent PCM format per machine, so this covers the real
    case and removes ffmpeg as a hard requirement for the audio stage.
    """
    params: tuple[int, int, int] | None = None
    payload: list[bytes] = []
    segments: list[dict[str, Any]] = []
    cursor = pad_before
    for index, path in enumerate(audio_paths):
        with wave.open(str(path), "rb") as handle:
            current = (
                handle.getnchannels(), handle.getsampwidth(), handle.getframerate()
            )
            if params is None:
                params = current
            elif current != params:
                return None
            frames = handle.readframes(handle.getnframes())
        if params is None:
            return None
        rate, width, channels = params[2], params[1], params[0]
        frame_size = width * channels
        # One leading pad before the first segment, one gap between segments;
        # the trailing pad is appended once after the last one.
        lead = pad_before if index == 0 else pad_between
        payload.append(b"\x00" * (int(round(lead * rate)) * frame_size))
        payload.append(frames)
        duration = len(frames) / float(rate * frame_size)
        segments.append({
            "path": path.name,
            "start": round(cursor, 3),
            "duration": round(duration, 3),
        })
        cursor += duration + pad_between
    if params is None:
        return None
    tail = b"\x00" * (int(round(pad_before * params[2])) * params[1] * params[0])
    with wave.open(str(output_path), "wb") as handle:
        handle.setnchannels(params[0])
        handle.setsampwidth(params[1])
        handle.setframerate(params[2])
        handle.writeframes(b"".join(payload) + tail)
    return {
        "duration": _wav_duration(output_path),
        "segments": segments,
        "method": "python-wave",
    }


def assemble_narration(
    audio_paths: list[Path],
    output_path: Path,
    pad_before: float = PAD_BEFORE_SECONDS,
    pad_between: float = PAD_BETWEEN_SECONDS,
) -> dict[str, Any]:
    """Concat per-card WAVs into ONE narration track with silence pads.

    Returns per-card offsets so frame timing can follow the real audio:
    {"duration", "segments": [{"path", "start", "duration"}], "method"}.
    """
    if not audio_paths:
        raise ValueError("assemble_narration needs at least one audio file")
    for path in audio_paths:
        if not path.is_file():
            raise FileNotFoundError(f"missing narration segment: {path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = _ffmpeg()
    if ffmpeg is None:
        # No ffmpeg on this machine (bare CI runner, minimal container).
        result = _concat_wavs(audio_paths, output_path, pad_before, pad_between)
        if result is not None:
            return result
        raise RuntimeError(
            "ffmpeg not found and the narration segments are not a single "
            "WAV format, so they cannot be joined without it "
            "(install ffmpeg or make SAPI emit one format)"
        )

    inputs: list[str] = []
    filters: list[str] = []
    for index in range(len(audio_paths)):
        inputs += ["-i", str(audio_paths[index])]
    # Build: [i:a]adelay=pad|pad, then concat with generated silences.
    segment_labels: list[str] = []
    cursor = pad_before
    segments: list[dict[str, Any]] = []
    for index, path in enumerate(audio_paths):
        duration = _wav_duration(path)
        delay_ms = int(round(cursor * 1000))
        filters.append(
            f"[{index}:a]adelay={delay_ms}|{delay_ms},apad=whole_dur={duration:.3f}[s{index}]"
        )
        segment_labels.append(f"[s{index}]")
        segments.append({
            "path": audio_paths[index].name,
            "start": round(cursor, 3),
            "duration": round(duration, 3),
        })
        cursor += duration + pad_between
    total = cursor - pad_between + pad_before
    filter_graph = ";".join(filters) + ";" + "".join(segment_labels)
    filter_graph += f"amix=inputs={len(audio_paths)}:normalize=0:duration=longest,"
    filter_graph += f"apad=whole_dur={total:.3f},atrim=0:{total:.3f}[mix]"
    result = subprocess.run(
        [ffmpeg, "-y", *inputs,
         "-filter_complex", filter_graph, "-map", "[mix]",
         "-ar", "44100", "-ac", "2", str(output_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not output_path.is_file():
        raise RuntimeError(f"ffmpeg concat failed: {result.stderr[-1500:]}")
    actual = _media_duration(output_path)
    return {
        "duration": round(actual, 3),
        "segments": segments,
        "method": "ffmpeg",
    }


def card_timings(
    segments: list[dict[str, Any]], total_seconds: float, fps: int = FPS
) -> list[dict[str, int]]:
    """Frame spans that keep every card locked to the assembled narration.

    A card owns its speech PLUS the silence that follows it, so card N's
    speech starts exactly at card N's first frame and the last card owns the
    trailing pad. Using speech length alone would let the pads accumulate and
    desync narration from picture, or let audio outlive the final card.
    """
    timings: list[dict[str, int]] = []
    for index, segment in enumerate(segments):
        if index + 1 < len(segments):
            end = segments[index + 1]["start"]
        else:
            end = total_seconds
        timings.append({
            "fromFrame": int(round(segment["start"] * fps)),
            "durationInFrames": max(fps, int(round(end * fps)) - int(round(segment["start"] * fps))),
        })
    return timings


def build_render_props(
    cards: list[dict[str, Any]], audio_src: str
) -> dict[str, Any]:
    """Build serializable timing/media props for the dedicated Remotion scene.

    Contract (locked): one sequence per card driven by the real per-card
    narration timing, plus ONE assembled narration track at `audioSrc`.
    `audio_src` is the public/-relative path of the assembled narration.wav.
    A card may carry `narration_offset` (its speech start in the assembled
    track) and `audio_total_seconds` (the assembled track length); when the
    last card has it, frame spans are pinned to the real audio so picture and
    narration can neither drift nor let audio outlive the final card.
    """
    end_card_frames = FPS * 4
    offsets = [card.get("narration_offset") for card in cards]
    locked = bool(cards) and all(offset is not None for offset in offsets)
    timed_cards: list[dict[str, Any]] = []
    cursor = 0
    for position, source in enumerate(cards):
        card = dict(source)
        seconds = float(
            source.get("narration_seconds")
            or source.get("audio_duration")
            or 3.0
        )
        if locked:
            start = int(round(float(offsets[position]) * FPS))
            if position + 1 < len(cards):
                end_seconds = float(offsets[position + 1])
            else:
                end_seconds = float(
                    source.get("audio_total_seconds")
                    or (float(offsets[position]) + seconds)
                )
            duration = max(FPS, int(round(end_seconds * FPS)) - start)
        else:
            duration = max(FPS, int(round(seconds * FPS)))
            start = cursor
        card["durationInFrames"] = duration
        card["fromFrame"] = start
        card["narrationSeconds"] = seconds
        card["clipSrc"] = source.get("clip_src") or source.get("clip_path") or ""
        card["clipSourceUrl"] = source.get("clip_source_url") or source.get(
            "visual_source", ""
        )
        card.pop("lead_image", None)
        timed_cards.append(card)
        # In locked mode `start` carries the pad, so the end of the last card
        # is start+duration; in free mode start is the running cursor.
        cursor = start + duration
    return {
        "width": RENDER_WIDTH,
        "height": RENDER_HEIGHT,
        "fps": FPS,
        "cards": timed_cards,
        "audioSrc": audio_src,
        "sources": [
            {"outlet": card.get("outlet", ""), "url": card.get("source_url", "")}
            for card in timed_cards
        ],
        "endCardFrames": end_card_frames,
        "durationInFrames": cursor + end_card_frames,
        "boundaryAttribution": {
            "license": MAP_LICENSE,
            "source": MAP_SOURCE,
        },
    }


def _download_map(path: Path) -> None:
    if path.is_file():
        return
    import urllib.request
    with urllib.request.urlopen(MAP_URL, timeout=120) as response:  # noqa: S310
        path.write_bytes(response.read())


_GEO_STATE_ALIASES = {
    "nct delhi": "Delhi",
    "national capital territory of delhi": "Delhi",
    "jammu & kashmir": "Jammu and Kashmir",
    "jammu and kashmir ": "Jammu and Kashmir",
    "orissa": "Odisha",
    "uttaranchal": "Uttarakhand",
    # Tiny UTs with no STATE_KEYWORDS entry: map to None so they join the
    # outline only instead of masquerading as a highlightable state.
    "pondicherry": None,
    "chandigarh": None,
    "dadra and nagar haveli": None,
    "daman and diu": None,
    "lakshadweep": None,
    "andaman and nicobar": None,
    "andaman & nicobar islands": None,
}


def _geo_state_name(properties: dict[str, Any]) -> str:
    for key in ("NAME_1", "name", "NAME", "State", "state", "st_nm"):
        value = properties.get(key)
        if value:
            text = str(value).strip()
            lowered = text.lower()
            if lowered in _GEO_STATE_ALIASES:
                resolved = _GEO_STATE_ALIASES[lowered]
                return resolved if resolved else ""
            return text
    return ""


def geojson_to_svg_paths(
    geojson: dict[str, Any],
    width: int = RENDER_WIDTH,
    height: int = RENDER_HEIGHT,
    pad: int = 24,
) -> dict[str, Any]:
    """Project state polygons to SVG path strings (equirectangular fit).

    Runs at asset time so the Remotion scene renders plain path data with
    no runtime fetch and no geo dependency. Returns {"paths", "centroids",
    "outline"}; centroids are bbox centers in the same 720x1280 space and
    drive the rank-badge placement.
    """
    rings: list[list[tuple[float, float]]] = []

    def collect(geometry: dict[str, Any]) -> None:
        kind = geometry.get("type")
        coords = geometry.get("coordinates") or []
        if kind == "Polygon":
            rings.extend(coords)
        elif kind == "MultiPolygon":
            for polygon in coords:
                rings.extend(polygon)

    for feature in geojson.get("features", []):
        collect(feature.get("geometry") or {})

    lon_lat = [(pt[0], pt[1]) for ring in rings for pt in ring]
    if not lon_lat:
        raise ValueError("GeoJSON has no polygon coordinates")
    lon0 = sum(lon for lon, _ in lon_lat) / len(lon_lat)
    projected: list[tuple[float, float]] = []
    for lon, lat in lon_lat:
        x = math.radians(lon - lon0)
        y = math.log(max(0.001, math.tan(math.pi / 4 + math.radians(lat) / 2)))
        projected.append((x, y))
    xs = [p[0] for p in projected]
    ys = [p[1] for p in projected]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    scale = min(
        (width - 2 * pad) / max(1e-9, max_x - min_x),
        (height - 2 * pad) / max(1e-9, max_y - min_y),
    )
    off_x = pad - min_x * scale + ((width - 2 * pad) - (max_x - min_x) * scale) / 2
    # Note the negation: projected points use -y (north up), so the offset
    # anchors on max_y, not min_y.
    off_y = pad + max_y * scale + ((height - 2 * pad) - (max_y - min_y) * scale) / 2

    def pixel(lon: float, lat: float) -> tuple[float, float]:
        x = math.radians(lon - lon0)
        y = math.log(max(0.001, math.tan(math.pi / 4 + math.radians(lat) / 2)))
        px = x * scale + off_x
        py = -y * scale + off_y
        return (
            RENDER_WIDTH / 2 + (px - RENDER_WIDTH / 2) * MAP_ZOOM,
            RENDER_HEIGHT / 2 + (py - RENDER_HEIGHT / 2) * MAP_ZOOM,
        )

    def project_ring(ring: list) -> str:
        parts = [f"{pixel(lon, lat)[0]:.1f},{pixel(lon, lat)[1]:.1f}" for lon, lat in ring]
        return "M" + "L".join(parts) + "Z"

    paths: dict[str, str] = {}
    centroids: dict[str, list[float]] = {}
    pans: dict[str, list[float]] = {}
    outline: list[str] = []
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry") or {}
        kind = geometry.get("type")
        coords = geometry.get("coordinates") or []
        state_paths: list[str] = []
        state_xs: list[float] = []
        state_ys: list[float] = []
        if kind == "Polygon":
            rings = coords
        elif kind == "MultiPolygon":
            rings = [ring for polygon in coords for ring in polygon]
        else:
            rings = []
        for ring in rings:
            state_paths.append(project_ring(ring))
            for lon, lat in ring:
                sx, sy = pixel(lon, lat)
                state_xs.append(sx)
                state_ys.append(sy)
        text = "".join(state_paths)
        outline.append(text)
        name = _geo_state_name(feature.get("properties") or {})
        if name and name not in paths:
            paths[name] = text
            if state_xs:
                cx = round((min(state_xs) + max(state_xs)) / 2, 1)
                cy = round((min(state_ys) + max(state_ys)) / 2, 1)
                centroids[name] = [cx, cy]
                # Per-state pan: slide the map so the story's state sits in the
                # open band BELOW the headline bar. Without it, a northern
                # state (Delhi, y~406) hides behind the bar and the card's
                # headline element -- the highlighted state -- never renders.
                pans[name] = [
                    round(max(-PAN_LIMIT_X, min(PAN_LIMIT_X, FOCUS_X - cx)), 1),
                    round(max(-PAN_LIMIT_Y, min(PAN_LIMIT_Y, FOCUS_Y - cy)), 1),
                ]
    return {
        "paths": paths,
        "centroids": centroids,
        "pans": pans,
        "outline": "".join(outline),
    }


def _run_assets(
    cards_path: Path, project: Path, tool_registry: ToolRegistry
) -> Path:
    payload = json.loads(cards_path.read_text(encoding="utf-8"))
    cards = payload.get("cards", payload) if isinstance(payload, dict) else payload
    media_dir = project / "public" / "india-daily-news"
    media_dir.mkdir(parents=True, exist_ok=True)
    pexels = tool_registry.get("pexels_video")
    if pexels is None:
        raise RuntimeError("pexels_video is not registered")

    enriched: list[dict[str, Any]] = []
    for source_card in cards:
        card = dict(source_card)
        rank = int(card["rank"])
        query = sanitize_visual_query(
            card.get("visual_query") or card.get("headline", "")
        )
        clip_name = f"clip-{rank:02d}.mp4"
        clip_path = media_dir / clip_name
        if not clip_path.is_file():
            result = pexels.execute({
                "query": query,
                "per_page": 5,
                "orientation": "portrait",
                "min_duration": 6,
                "preferred_quality": "hd",
                "output_path": str(clip_path),
            })
            if not result.success:
                # A narrow story query often has no portrait HD match. Retry
                # once with generic Indian B-roll before giving up on the shot.
                result = pexels.execute({
                    "query": FALLBACK_VISUAL_QUERY,
                    "per_page": 5,
                    "orientation": "portrait",
                    "min_duration": 6,
                    "preferred_quality": "hd",
                    "output_path": str(clip_path),
                })
            if result.success:
                card["visual_license"] = result.data.get(
                    "license", "Pexels License"
                )
                card["visual_source"] = result.data.get("pexels_url", "")
            else:
                # Last resort: ship the card without footage. The scene renders
                # an empty dashed frame, which beats losing the whole episode.
                card["visual_license"] = "none"
                card["visual_source"] = ""
                card["visual_missing_reason"] = str(result.error)[:200]
                print(f"WARN rank {rank}: no stock clip ({result.error})")
        card.setdefault("visual_license", "Pexels License")
        if clip_path.is_file():
            card["clip_src"] = f"india-daily-news/{clip_name}"
            # Informational provenance only: never let a probe failure stop the run.
            clip_duration = _media_duration(clip_path)
            if clip_duration is not None:
                card["clip_duration"] = round(clip_duration, 3)
        else:
            card["clip_src"] = ""

        audio_name = f"audio-{rank:02d}.wav"
        audio_path = media_dir / audio_name
        if not audio_path.is_file():
            card["narration_provider"] = narrate_with_sapi(
                card.get("narration") or card.get("headline", "India news update"),
                audio_path,
            )
        card["audio_src"] = f"india-daily-news/{audio_name}"
        card["audio_duration"] = _wav_duration(audio_path)
        enriched.append(card)

    # One assembled narration track: the single audio contract of the scene.
    narration_path = media_dir / "narration.wav"
    assembly = assemble_narration(
        [media_dir / f"audio-{int(card['rank']):02d}.wav" for card in enriched],
        narration_path,
    )
    for card, segment in zip(enriched, assembly["segments"]):
        card["narration_seconds"] = segment["duration"]
        card["narration_offset"] = segment["start"]
    enriched[-1]["audio_total_seconds"] = assembly["duration"]

    map_path = media_dir / "india-states.geojson"
    _download_map(map_path)
    (media_dir / "BOUNDARIES-LICENSE.txt").write_text(
        "State boundaries: geohacker/india, MIT repository, GADM-derived.\n"
        "Source: https://github.com/geohacker/india\n",
        encoding="utf-8",
    )
    geojson = json.loads(map_path.read_text(encoding="utf-8"))
    paths_path = media_dir / "state-paths.json"
    paths_path.write_text(
        json.dumps(
            geojson_to_svg_paths(geojson), ensure_ascii=False
        ),
        encoding="utf-8",
    )
    props = build_render_props(enriched, "india-daily-news/narration.wav")
    props["mapPathsSrc"] = "india-daily-news/state-paths.json"
    # Do not assume the roundup stage already created artifacts/.
    props_dir = project / "artifacts"
    props_dir.mkdir(parents=True, exist_ok=True)
    props_path = props_dir / "render-props.json"
    props_path.write_text(
        json.dumps(props, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"ASSETS READY: {props_path}")
    print(f"NARRATION: {narration_path} ({assembly['duration']}s)")
    return props_path


def _render(props_path: Path, project: Path) -> Path:
    """Stage media into the composer public dir and render IndiaDailyNews."""
    composer = REPO_ROOT / "remotion-composer"
    composer_public = composer / "public" / "india-daily-news"
    if composer_public.exists():
        shutil.rmtree(composer_public)
    shutil.copytree(project / "public" / "india-daily-news", composer_public)
    renders = project / "renders"
    renders.mkdir(parents=True, exist_ok=True)
    output = (renders / "india-daily.mp4").resolve()
    props_arg = props_path.resolve()
    npx = shutil.which("npx")
    if not npx:
        raise RuntimeError("npx not found on PATH; install Node.js to render")
    # Every core: the render is CPU-bound on per-frame headless Chrome, and
    # Remotion's default concurrency leaves cores idle on a small box.
    concurrency = max(1, (os.cpu_count() or 2) - 1)
    cmd = [
        npx, "remotion", "render",
        str(composer / "src" / "index.tsx"),
        "IndiaDailyNews",
        str(output),
        f"--props={props_arg}",
        f"--public-dir={composer / 'public'}",
        f"--concurrency={concurrency}",
    ]
    completed = subprocess.run(cmd, check=False, cwd=composer,
                               capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        tail = "\n".join(detail.splitlines()[-25:])
        raise RuntimeError(f"Remotion render failed:\n{tail}")
    return output


def main() -> int:
    _force_utf8_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project", type=Path,
        default=REPO_ROOT / "projects" / "india-in-last-24hr",
    )
    parser.add_argument(
        "--cards", type=Path, default=None,
        help="cards.json path (default: <project>/artifacts/cards.json)",
    )
    parser.add_argument(
        "--render", action="store_true",
        help="also render IndiaDailyNews via Remotion into <project>/renders/",
    )
    args = parser.parse_args()

    _load_env(REPO_ROOT / ".env")
    registry.discover()
    cards_path = args.cards or (args.project / "artifacts" / "cards.json")
    if not cards_path.is_file():
        print(f"ERROR: cards not found: {cards_path}")
        print("Run scripts/india_daily_roundup.py first (source + script stages).")
        return 1
    props_path = _run_assets(cards_path, args.project, registry)
    if args.render:
        output = _render(props_path, args.project)
        print(f"RENDER READY: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
