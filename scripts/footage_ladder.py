"""Footage ladder — acquire REAL news footage for every rendered job.

This replaces an inline heredoc in .github/workflows/render-reels.yml that had
two defects:

  1. Its queries were hardcoded to the fixture headlines, so a daily scrape
     searched for "Yamuna flood plain" regardless of the actual story.
  2. When no footage was acquired it silently fell back to an ffmpeg
     `testsrc2` colour-bar pattern. The job still reported success, so a
     missing download produced a "finished" reel full of test bars instead of
     news footage.

Hard rule enforced here (see skills/pipelines/breaking_news_director.md and
AGENT_GUIDE.md "No Unilateral Substitutions"): there is no placeholder path.
If real footage cannot be acquired for a job, the run FAILS and says why.

Search queries are derived from each job's own headline. Downloading goes
through `ytdlp_downloader` (TikTok stays blocklisted) and trimming goes through
`footage_selector`, so both stay owned by the tools that already implement them.

Usage:
    python scripts/footage_ladder.py --routes out/daily/routes.json --out out/daily

Writes <out>/footage_report.json, consumed by scripts/render_reels_ci.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from render_reels_ci import STYLE_PLAN  # noqa: E402
from tools.video.footage_selector import FootageSelector  # noqa: E402
from tools.video.ytdlp_downloader import YtdlpDownloader  # noqa: E402

FPS = 30
MAX_RESOLUTION = "720p"

# Per-style search phrasing appended to the headline keywords. Breaking wants
# agency/newroom coverage; trending wants raw viral/UGC clips.
STYLE_QUERY_SUFFIX = {
    "breaking": "news footage",
    "trending": "viral video",
}

# Headline words too generic to search on — they match everything and return
# the same unrelated clip for every job.
STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "from", "by", "with", "as", "is", "are", "was", "were", "be", "been", "it",
    "its", "this", "that", "these", "those", "after", "before", "over", "under",
    "new", "says", "say", "said", "amid", "into", "about", "more", "than",
    "amid", "up", "down", "out", "off", "has", "have", "had", "will", "would",
    "can", "could", "may", "might", "you", "your", "we", "our", "they", "them",
})


def headline_keywords(title: str, limit: int = 12) -> list[str]:
    """Meaningful search terms from a headline, stopwords removed."""
    words = re.findall(r"[A-Za-z0-9']+", str(title or ""))
    kept = [w for w in words if w.lower() not in STOPWORDS and len(w) > 2]
    return kept[:limit]


def build_queries(job: dict[str, Any]) -> list[str]:
    """Search queries derived from THIS job's headline, not a fixture."""
    style = job.get("style", "")
    item = job.get("source_item", job)
    title = str(item.get("title", ""))
    outlet = str(item.get("outlet", "")).strip()
    keywords = headline_keywords(title)
    if not keywords:
        return []
    phrase = " ".join(keywords)
    suffix = STYLE_QUERY_SUFFIX.get(style, "")
    queries = [f"{phrase} {suffix}".strip()]
    if outlet:
        queries.append(f"{outlet} {phrase}")
    return queries


def resolve_watch_urls(queries: list[str], per_query: int = 4) -> list[str]:
    """Search YouTube and return concrete watch URLs (search-only, no download).

    Uses the yt_dlp package so this works wherever `pip install yt-dlp` ran,
    with no separate binary on PATH. Search traffic honours YTDLP_PROXY_URL so
    it leaves from your own IP instead of the GitHub datacenter IP.
    """
    try:
        import yt_dlp  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "yt-dlp is not importable. Install it: pip install yt-dlp\n"
            "For YouTube support a JS runtime (Deno) must also be on PATH."
        ) from exc

    urls: list[str] = []
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": True}
    proxy = os.environ.get("YTDLP_PROXY_URL", "").strip()
    if proxy:
        opts["proxy"] = proxy
        try:
            host = urllib.parse.urlparse(proxy).hostname or "proxy"
        except Exception:
            host = "proxy"
        print(f"youtube search route: {host}", flush=True)
    cookies_file = os.environ.get("YTDLP_COOKIES_FILE", "").strip()
    if cookies_file and Path(cookies_file).is_file():
        opts["cookiefile"] = cookies_file
    for query in queries:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch{per_query}:{query}", download=False)
        except Exception as exc:  # one bad query must not kill the ladder
            print(f"  search failed for {query!r}: {exc}", flush=True)
            continue
        for entry in (info or {}).get("entries") or []:
            video_id = entry.get("id") if isinstance(entry, dict) else None
            if video_id:
                urls.append(f"https://www.youtube.com/watch?v={video_id}")
    # de-duplicate, preserve order
    return list(dict.fromkeys(urls))


def acquire_clips(job: dict[str, Any], raw_dir: Path) -> dict[str, Any]:
    """Download real footage for one job. Returns a footage_report entry.

    Never synthesizes footage: on failure the entry carries `footage_source:
    "missing"` with the reasons, and the caller fails the run.
    """
    job_id = job["job_id"]
    style = job.get("style", "")
    ladder: list[str] = []

    queries = build_queries(job)
    if not queries:
        return {
            "style": style,
            "footage_source": "missing",
            "clips": [],
            "ladder": [f"no searchable keywords in headline: "
                       f"{job.get('source_item', {}).get('title', '')!r}"],
        }

    watch_urls = resolve_watch_urls(queries)
    if not watch_urls:
        return {
            "style": style,
            "footage_source": "missing",
            "clips": [],
            "ladder": [f"search returned no results for {queries}"],
        }

    print(f"  {len(watch_urls)} candidate URL(s) for {queries[0]!r}", flush=True)
    download = YtdlpDownloader().execute({
        "url": watch_urls[0],
        "urls": watch_urls,
        "output_dir": str(raw_dir),
        "max_resolution": MAX_RESOLUTION,
    })
    ladder.extend(download.data.get("report", []) if download.data else [])

    downloaded = [
        {"path": item["video_path"],
         "duration": item.get("duration"),
         "source_url": item.get("source_url", "")}
        for item in (download.data or {}).get("items", [])
        if item.get("status") == "downloaded" and item.get("video_path")
    ]
    if not downloaded:
        return {
            "style": style,
            "footage_source": "missing",
            "clips": [],
            "ladder": ladder or ["every download was SKIPPED"],
        }

    selected = FootageSelector().execute({
        "clips": downloaded,
        "style": style,
        "output_dir": str(raw_dir.parent / f"selected_{style}"),
    })
    if not selected.success:
        ladder.append(f"footage_selector failed: {selected.error}")
        return {"style": style, "footage_source": "missing",
                "clips": [], "ladder": ladder}

    clips = [seg["path"] for seg in selected.data.get("selected", [])]
    return {
        "style": style,
        "footage_source": "live",
        "clips": clips,
        "ladder": ladder,
    }


def check_breaking_duration(style: str, clips: list[str],
                            plan: dict[str, Any]) -> str | None:
    """A single breaking clip must cover the whole reel, or Remotion freezes.

    footage_selector only guarantees 15-20s when the SOURCE is that long; a
    6-second clip yields a 6-second clip and the remaining ~12s of the reel
    holds on a frozen frame. That reads as a broken video, so reject it.

    Returns a reason string when the clip is too short, else None.
    """
    if style != "breaking" or len(clips) != 1:
        return None
    seconds = int(plan["duration_in_frames"]) / FPS
    actual = _probe_seconds(Path(clips[0]))
    if actual + 0.5 < seconds:
        return (
            f"breaking footage is {actual:.2f}s but the reel is {seconds:.2f}s — "
            f"the tail would be a frozen frame. Use a longer source clip."
        )
    return None


def _probe_seconds(path: Path) -> float:
    import subprocess
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            stderr=subprocess.STDOUT, text=True, timeout=15,
        )
        return float(out.strip())
    except Exception:
        return 0.0


def check_toolchain() -> int:
    """Verify the footage toolchain can actually work. Fails fast, never degrades.

    Every failure mode here previously produced a successful run full of
    colour bars, because the ladder silently substituted placeholder footage.
    An unavailable tool must be a loud blocker instead.
    """
    import shutil

    from tools.tool_registry import registry

    registry.discover()
    for name in ("ytdlp_downloader", "footage_selector"):
        tool = registry._tools[name]
        status = tool.get_status().value
        if status != "available":
            print(f"FATAL: {name} reports {status}.", file=sys.stderr)
            print(tool.install_instructions, file=sys.stderr)
            return 1
        print(f"{name}: AVAILABLE")

    import yt_dlp
    print(f"yt_dlp version: {yt_dlp.version.__version__}")

    for binary in ("ffmpeg", "ffprobe"):
        found = shutil.which(binary)
        print(f"{binary}: {found}")
        if not found:
            print(f"FATAL: {binary} is not on PATH.", file=sys.stderr)
            return 1

    # YouTube extraction needs a JS runtime. Without it yt-dlp SKIPs every URL,
    # which is exactly how a "successful" run ended up with zero footage.
    js = shutil.which("deno") or shutil.which("node")
    print(f"js runtime (deno/node): {js}")
    if not js:
        print(
            "FATAL: no Deno or Node on PATH. yt-dlp cannot solve YouTube's JS "
            "challenge without a JS runtime, so every download would be SKIPPED.\n"
            "Install Deno: https://deno.land/#installation",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-toolchain", action="store_true",
        help="verify the footage toolchain and exit (no downloads)")
    parser.add_argument("--routes", help="routes.json from daily.py")
    parser.add_argument("--out", help="out dir, e.g. out/daily")
    args = parser.parse_args(argv)

    if args.check_toolchain:
        return check_toolchain()

    if not args.routes or not args.out:
        parser.error("--routes and --out are required unless --check-toolchain")

    out_dir = Path(args.out)
    raw_dir = out_dir / "footage"
    raw_dir.mkdir(parents=True, exist_ok=True)

    routes = json.loads(Path(args.routes).read_text(encoding="utf-8"))
    jobs = routes["jobs"] if isinstance(routes, dict) else routes

    report: dict[str, Any] = {}
    failures: list[str] = []

    # One job per renderable style — mirrors render_reels_ci.derive_plan.
    seen: set[str] = set()
    for job in jobs:
        style = job.get("style", "")
        if style not in STYLE_PLAN or style in seen:
            continue
        seen.add(style)
        job_id = job["job_id"]
        print(f"footage ladder: {job_id} (style={style})", flush=True)
        entry = acquire_clips(job, raw_dir)
        if entry["footage_source"] == "live" and entry["clips"]:
            too_short = check_breaking_duration(
                style, entry["clips"], STYLE_PLAN[style])
            if too_short:
                entry["footage_source"] = "missing"
                entry["clips"] = []
                entry["ladder"] = [*entry["ladder"], too_short]
        report[job_id] = entry
        if entry["footage_source"] != "live" or not entry["clips"]:
            failures.append(job_id)
            print(f"  NO USABLE FOOTAGE for {job_id}", flush=True)
            for line in entry["ladder"]:
                print(f"    {line}", flush=True)
            continue
        print(f"  live footage: {len(entry['clips'])} clip(s)", flush=True)

    (out_dir / "footage_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")

    if failures:
        print("\nFATAL: no real footage for: " + ", ".join(failures), flush=True)
        print(
            "Real news footage is mandatory for these reels "
            "(breaking_news_director.md: 'AI b-roll is forbidden').\n"
            "This run refuses to substitute synthetic placeholder footage.\n"
            "Likely causes: yt-dlp not installed, no Deno JS runtime for "
            "YouTube, or YouTube rate-limiting the runner.\n"
            "See the step log above for the per-URL SKIPPED reasons.",
            file=sys.stderr, flush=True,
        )
        return 1

    print(f"\nAll {len(report)} job(s) got real footage.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())