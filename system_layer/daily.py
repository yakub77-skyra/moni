"""Daily news entrypoint — route scraped items, scaffold jobs, print triggers.

One-click flow per the System Layer contract:
  1. Load news items (--input JSON) or scrape live (--scrape).
  2. Route each item via system_layer.router (exactly one style per job).
  3. Write job manifests to <project-dir>/manifests/.
  4. Print the OpenMontage CLI/Makefile trigger per routed job.

This script does NOT orchestrate pipeline stages, make creative decisions,
or render video. The agent drives each routed pipeline stage-by-stage using
its YAML manifest + director skill. Rendering happens via the normal
`video_compose` path (Remotion BreakingNewsReel / TrendingNewsReel /
IndiaDailyNews).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from system_layer.router import route_batch  # noqa: E402


def _load_items(args: argparse.Namespace) -> list[dict]:
    if args.input:
        return json.loads(Path(args.input).read_text(encoding="utf-8"))
    if args.scrape:
        from tools.news.india_news_scraper import IndiaNewsScraper

        result = IndiaNewsScraper().execute({
            "max_items": args.max_items,
            "lookback_hours": args.lookback_hours,
            "fetch_article_body": False,
        })
        if not result.success:
            raise RuntimeError(f"Scrape failed: {result.error}")
        return [
            {"title": s.get("headline", ""), "summary": s.get("summary", ""),
             "source_url": s.get("source_url", ""), "outlet": s.get("outlet", ""),
             "state": s.get("state", "")}
            for s in result.data.get("stories", [])
        ]
    raise RuntimeError("Provide --input <items.json> or --scrape")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Route daily news items to style pipelines")
    parser.add_argument("--input", default="", help="JSON file with [{title, summary, ...}]")
    parser.add_argument("--scrape", action="store_true", help="Scrape live via india_news_scraper")
    parser.add_argument("--max-items", type=int, default=5)
    parser.add_argument("--lookback-hours", type=float, default=24.0)
    parser.add_argument("--project-dir", default="")
    # Additive routing-mode label (fixture|daily). Recorded in routes.json;
    # no behavior change when omitted (defaults to daily).
    parser.add_argument("--mode", default="daily", choices=("daily", "fixture"))
    args = parser.parse_args(argv)

    items = _load_items(args)
    if not items:
        print("No news items to route.", flush=True)
        return 1
    manifests = route_batch(items)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    project_dir = Path(args.project_dir or f"projects/daily-{stamp}")
    manifests_dir = project_dir / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    for manifest in manifests:
        (manifests_dir / f"{manifest['job_id']}.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    (project_dir / "routes.json").write_text(
        json.dumps({"mode": args.mode, "jobs": manifests}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    counts: dict[str, int] = {}
    for manifest in manifests:
        counts[manifest["style"]] = counts.get(manifest["style"], 0) + 1
    print(f"Routed {len(manifests)} items -> {project_dir}: {counts}", flush=True)
    for manifest in manifests:
        print(
            f"  [{manifest['style']}] {manifest['job_id']}"
            f" pipeline={manifest['pipeline']}"
            f" composition={manifest['composition']}"
            f" conf={manifest['confidence']}",
            flush=True,
        )
    print("Next: run each routed pipeline stage-by-stage via its manifest + director skill.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
