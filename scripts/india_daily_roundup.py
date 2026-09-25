"""Daily India news roundup — source + script stages (free tools only).

Runs the two zero-cost stages of the india-in-last-24hr pipeline:
  1. india_news_scraper  -> 5 fresh stories from Indian RSS outlets
  2. openrouter_scriptwriter -> rewritten 12-18 word card copy (":free" model)

Writes artifacts into the project workspace so the later stages (assets,
edit, compose) can pick them up. Nothing here costs money: free RSS, free
OpenRouter models, no video generation.

Usage:
    python scripts/india_daily_roundup.py [--stories 5] [--lookback 24]
                                          [--project projects/india-in-last-24hr]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.tool_registry import registry  # noqa: E402

FREE_MODEL = "openrouter/free"


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


def main() -> int:
    _force_utf8_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stories", type=int, default=5)
    parser.add_argument("--lookback", type=float, default=24.0)
    parser.add_argument(
        "--project",
        type=Path,
        default=REPO_ROOT / "projects" / "india-in-last-24hr",
    )
    parser.add_argument("--model", default=FREE_MODEL)
    args = parser.parse_args()

    _load_env(REPO_ROOT / ".env")

    if not args.model.endswith((":free", "/free")):
        print(f"ERROR: refusing paid model {args.model!r}; free models only.")
        return 2

    registry.discover()
    out_dir = args.project / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- Stage: idea (source stories) ----
    scraper = registry.get("india_news_scraper")
    stories_path = out_dir / "stories.json"
    result = scraper.execute({
        "max_items": args.stories,
        "lookback_hours": args.lookback,
        "fetch_article_body": True,
        "request_timeout_seconds": 25,
        "output_path": str(stories_path),
    })
    if not result.success:
        print("SCRAPE FAILED:", result.error)
        return 1
    stories = result.data["stories"]
    print(f"== Source: {len(stories)} stories (lookback {args.lookback}h)")
    for story in stories:
        print(f"   {story['rank']}. [{story['state']}] {story['outlet']}")
        print(f"      {story['headline'][:95]}")
        print(f"      {story['source_url'][:95]}")
    if result.data.get("feed_errors"):
        print(f"   feed warnings: {len(result.data['feed_errors'])}")

    # ---- Stage: script (free OpenRouter rewrite) ----
    writer = registry.get("openrouter_scriptwriter")
    cards_path = out_dir / "cards.json"
    script = writer.execute({
        "stories": stories,
        "model": args.model,
        "output_path": str(cards_path),
    })
    if not script.success:
        print("SCRIPT FAILED:", script.error)
        print(f"(stories kept at {stories_path})")
        return 1
    print(f"\n== Script: {script.data['card_count']} cards via {script.data['model']}")
    for card in script.data["cards"]:
        words = len(card["narration"].split())
        print(f"   {card['rank']}. [{card['state']}] ({words}w) {card['narration'][:95]}")
        print(f"      visual: {card['visual_query'][:95]}")

    print(f"\n== Artifacts")
    print(f"   stories: {stories_path}")
    print(f"   cards:   {cards_path}")
    print(f"   total cost: $0.00")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
