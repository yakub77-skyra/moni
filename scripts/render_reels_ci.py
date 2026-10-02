"""CI render bridge — stage footage into Remotion's public/ before rendering.

Why this exists: Remotion's bundler serves assets out of
``remotion-composer/public/``. A repo-relative path such as
``out/daily/footage/clip.mp4`` is resolved by ``staticFile()`` and 404s, because
it is interpreted as a public-dir-relative path that does not exist there.

The contract this module enforces (the only place props are written):

1. Every clip is COPIED into ``remotion-composer/public/footage/``.
2. Props carry ONLY the public-relative string, e.g. ``footage/clip.mp4``.
3. ``resolveAsset()`` (src/lib/resolveAsset.ts) then turns that into
   ``staticFile()``, which the dev server can actually serve.

Subcommands:
    stage    copy footage into public/footage and write the props JSON files
    render   run `npx remotion render` for each props file
    clean    remove public/footage (stale-file hygiene, before or after a run)

No rendering decision lives here: headlines, styles, and composition choice
stay with the pipeline and its director skills.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSER = REPO_ROOT / "remotion-composer"
PUBLIC_DIR = COMPOSER / "public"
STAGING_DIRNAME = "footage"
RENDER_CONCURRENCY = 2

# The subhead sits under the headline inside the white mask; past ~120 chars it
# collides with the video below, so it is trimmed at a word boundary.
MAX_SUBHEAD_CHARS = 120


def trim_summary(text: str, limit: int = MAX_SUBHEAD_CHARS) -> str:
    """Trim to `limit` chars without leaving a dangling partial word."""
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    cut = clean[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:-") + "…"

# Which composition consumes which prop key for its footage.
# (single clip vs list of clips) — the only structural difference between them.
COMPOSITIONS = {
    "BreakingNewsReel": {"prop_key": "videoSrc", "many": False, "output": "breaking.mp4"},
    "TrendingNewsReel": {"prop_key": "clips", "many": True, "output": "trending.mp4"},
}


def output_stem(composition: str) -> str:
    """Map a composition id to its output file stem (breaking / trending)."""
    return Path(COMPOSITIONS[composition]["output"]).stem


# Per-style render plan: composition, timing, and headline words to highlight.
# One entry per routed job, chosen by the style the router already assigned.
STYLE_PLAN = {
    "breaking": {"composition": "BreakingNewsReel", "duration_in_frames": 540,
                 "highlight_words": ["Yamuna", "14", "days"]},
    "trending": {"composition": "TrendingNewsReel", "duration_in_frames": 480,
                 "highlight_words": ["2", "million", "views"]},
}


def derive_plan(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pick one job per renderable style and attach its render parameters."""
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()
    for job in jobs:
        style = job.get("style")
        spec = STYLE_PLAN.get(style)
        if spec is None or style in seen:
            continue
        seen.add(style)
        plan.append({
            "composition": spec["composition"],
            "job_id": job["job_id"],
            "duration_in_frames": spec["duration_in_frames"],
            "highlight_words": spec["highlight_words"],
        })
    return plan


def staging_dir() -> Path:
    return PUBLIC_DIR / STAGING_DIRNAME


def public_rel(name: str) -> str:
    """The exact string a props file must carry for `name` inside staging_dir()."""
    return f"{STAGING_DIRNAME}/{name}"


def clean_staging() -> Path | None:
    """Remove the staging directory entirely. Returns the removed path, or None."""
    target = staging_dir()
    if target.is_dir():
        shutil.rmtree(target)
        return target
    return None


def _safe_name(raw: str) -> str:
    """Filesystem-safe stem that keeps the extension, for a staged copy."""
    name = Path(str(raw)).name or "clip.mp4"
    stem = "".join(c if (c.isalnum() or c in "-_") else "_" for c in Path(name).stem)
    suffix = Path(name).suffix or ".mp4"
    return f"{stem.strip('_') or 'clip'}{suffix}"


def assert_real_footage(job_id: str, entry: dict[str, Any]) -> list[str]:
    """Reject any footage report entry that is not real downloaded footage.

    A CI ladder bug once substituted an ffmpeg `testsrc2` colour-bar pattern for
    every reel and the run still reported success. Real news footage is
    mandatory (breaking_news_director.md: "AI b-roll is forbidden"), so this
    fails the stage instead of rendering test bars as a finished deliverable.
    """
    source = str(entry.get("footage_source", ""))
    clips = [str(c) for c in entry.get("clips", [])]
    if source != "live":
        raise ValueError(
            f"{job_id}: footage_source is {source!r}, not 'live'. Real news "
            f"footage is mandatory; synthetic placeholders are forbidden. "
            f"Ladder output: {entry.get('ladder')}"
        )
    if not clips:
        raise ValueError(f"{job_id}: footage_source is 'live' but clips[] is empty")
    for clip in clips:
        if Path(clip).stem.startswith("placeholder"):
            raise ValueError(
                f"{job_id}: {clip} looks like a synthetic placeholder. Real "
                f"downloaded footage only."
            )
    return clips


def stage_clips(clips: list[str]) -> list[str]:
    """Copy each clip into public/footage and return public-relative paths.

    Duplicate sources are copied once and referenced repeatedly (a trending reel
    may legitimately stage the same source for more than one hard cut).
    """
    if not clips:
        raise ValueError("no clips to stage")
    staging_dir().mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    copied: dict[str, str] = {}
    for clip in clips:
        source = Path(str(clip))
        if not source.is_file():
            raise FileNotFoundError(f"footage source does not exist: {source}")
        key = str(source.resolve())
        if key not in copied:
            name = _safe_name(source.name)
            shutil.copy2(source, staging_dir() / name)
            copied[key] = name
        staged.append(public_rel(copied[key]))
    return staged


def build_props(composition: str, staged_clips: list[str],
                item: dict[str, Any], *, duration_in_frames: int,
                highlight_words: list[str], watermark: str) -> dict[str, Any]:
    """Assemble the props payload, asserting the pathing contract holds."""
    spec = COMPOSITIONS[composition]
    source_item = item.get("source_item", item)
    headline = str(source_item.get("title", ""))
    props: dict[str, Any] = {
        "headline": headline,
        "highlightWords": highlight_words,
        "subhead": trim_summary(source_item.get("summary", "")),
        "dateText": datetime.now().strftime("%d %b %Y"),
        "watermark": watermark,
        "audioSrc": "",
        "durationInFrames": duration_in_frames,
    }
    if spec["many"]:
        props[spec["prop_key"]] = list(staged_clips[:6])
    else:
        props[spec["prop_key"]] = staged_clips[0]
    _assert_public_relative(props)
    return props


def _assert_public_relative(props: dict[str, Any]) -> None:
    """Fail fast if any media prop is not a public-relative `footage/...` path.

    This is the exact regression that produced the CI 404: a repo-relative
    filesystem path reaching Remotion and being served as a missing public file.
    """
    values: list[str] = []
    for key in ("videoSrc", "clips", "audioSrc"):
        raw = props.get(key)
        if isinstance(raw, str) and raw:
            values.append(raw)
        elif isinstance(raw, list):
            values.extend(str(v) for v in raw if v)
    for value in values:
        if value.startswith(("http://", "https://", "data:")):
            continue  # remote assets are served directly and are valid
        if value.startswith(f"{STAGING_DIRNAME}/"):
            continue
        raise ValueError(
            f"asset path {value!r} is not public-relative. It must start with "
            f"{STAGING_DIRNAME!r} so staticFile() can resolve it, or be an "
            f"absolute http(s)/data URL."
        )


def write_props(out_dir: Path, composition: str, props: dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{composition}.props.json"
    path.write_text(json.dumps(props, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def render_composition(composition: str, prop_file: Path, output: Path,
                       *, log: Any = None) -> Path:
    """Invoke npx remotion render with the verified --concurrency flag."""
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "npx", "remotion", "render", "src/index.tsx", composition,
            str(output.resolve()), "--props", str(prop_file.resolve()),
            f"--concurrency={RENDER_CONCURRENCY}",
        ],
        cwd=str(COMPOSER),
        check=True,
    )
    if log:
        log(f"rendered {output.name} from {composition}")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("clean", help="remove public/footage")
    stage = sub.add_parser("stage", help="stage footage and write props JSON")
    stage.add_argument("--report", required=True, help="footage_report.json")
    stage.add_argument("--routes", required=True, help="routes.json")
    stage.add_argument("--out", required=True, help="out dir, e.g. out/daily")
    stage.add_argument("--plan", default="",
                       help="Optional JSON list overriding the derived plan")
    render_p = sub.add_parser("render", help="render every props file in --out")
    render_p.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    def log(msg: str) -> None:
        print(msg, flush=True)

    if args.command == "clean":
        removed = clean_staging()
        log(f"cleaned {removed}" if removed else "nothing to clean")
        return 0

    if args.command == "stage":
        # Stale-first: a previous run's file must never satisfy this run's path.
        clean_staging()
        out_dir = Path(args.out)
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        routes = json.loads(Path(args.routes).read_text(encoding="utf-8"))
        jobs = routes["jobs"] if isinstance(routes, dict) else routes
        by_id = {j["job_id"]: j for j in jobs}
        plan = (
            json.loads(Path(args.plan).read_text(encoding="utf-8"))
            if args.plan else derive_plan(jobs)
        )
        for entry in plan:
            composition = entry["composition"]
            job_id = entry["job_id"]
            clips = assert_real_footage(job_id, report.get(job_id, {}))
            staged = stage_clips(clips)
            props = build_props(
                composition, staged, by_id[job_id],
                duration_in_frames=int(entry["duration_in_frames"]),
                highlight_words=entry.get("highlight_words", []),
                watermark=entry.get("watermark", "@INDIAINLAST24HR"),
            )
            prop_file = write_props(out_dir, composition, props)
            log(f"{composition}: staged {staged} -> {prop_file}")
        return 0

    if args.command == "render":
        out_dir = Path(args.out)
        for composition in COMPOSITIONS:
            prop_file = out_dir / f"{composition}.props.json"
            if not prop_file.is_file():
                log(f"skip {composition}: no props file")
                continue
            render_composition(
                composition, prop_file, out_dir / f"{output_stem(composition)}.mp4",
                log=log,
            )
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
