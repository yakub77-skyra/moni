"""Paced live smoke check: ONE search per platform, counts only, no downloads.

ENFORCES the warmed gate (social.warmed must be true). Respects the active
window unless --ignore-window is passed explicitly by a human. Zero ladder or
pipeline side effects.

Windows fallback: python scripts/social_smoke.py [--ignore-window]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.news import _social_safety as safety


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Social smoke (paced, counts only)")
    parser.add_argument("--ignore-window", action="store_true",
                        help="Human override: run outside the IST active window")
    parser.add_argument("--query", default="India news")
    args = parser.parse_args(argv)

    cfg = safety.load_config()
    if not safety.is_warmed(cfg):
        print("UNAVAILABLE: social.warmed=false. Warm the burner 7-10 days manually,")
        print("then rerun make social-setup. Applies to make social-smoke too.")
        return 2
    if not args.ignore_window and not safety.within_window(cfg=cfg):
        window = cfg.get("window", {})
        print(f"UNAVAILABLE: outside active window {window.get('start')}-{window.get('end')}"
              " Asia/Kolkata; zero calls made. Pass --ignore-window as a human to override.")
        return 2

    rc = 0
    # Instagram: one paced hashtag read via the tool (mock-free, live).
    try:
        from tools.news.instagram_reel_search import InstagramReelSearch

        tool = InstagramReelSearch()
        result = tool.execute({"keywords": args.query, "max_results": 3})
        if result.success:
            print(f"instagram: OK count={result.data.get('count', 0)} (no downloads)")
        else:
            print(f"instagram: {result.error}")
            rc |= 1
    except Exception as exc:  # never crash the smoke runner
        print(f"instagram: ERROR {exc}")
        rc |= 1

    # X: one paced search via the tool (live).
    try:
        from tools.news.x_video_search import XVideoSearch

        tool = XVideoSearch()
        result = tool.execute({"keywords": args.query, "max_results": 3})
        if result.success:
            print(f"x: OK count={result.data.get('count', 0)} (no downloads)")
        else:
            print(f"x: {result.error}")
            rc |= 1
    except Exception as exc:
        print(f"x: ERROR {exc}")
        rc |= 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
