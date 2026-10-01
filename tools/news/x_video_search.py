"""X video search — recent video posts for breaking-news sourcing.

Free-library path: twscrape with a human-managed account pool (S2). The tool
never adds or logins accounts; the DB file comes from SOCIAL_X_DB_PATH and is
wired explicitly to the twscrape pool parameter (verified: API(pool=...)).
Async twscrape calls are bridged sync-safe (asyncio.run, or a dedicated thread
when a loop is already running).

Anti-ban: human pacing + caps (S4), circuit breaker (S5), warm-up gate (S6),
secret hygiene (S7), optional proxy (S8).
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)
from tools.news import _social_safety as safety


def _default_x_client_factory(pool: str, proxy: str = ""):
    """Real twscrape client factory (lazy import keeps registry safe)."""
    from twscrape import API

    kwargs: dict[str, Any] = {"pool": pool}
    if proxy:
        kwargs["proxy"] = proxy
    return API(**kwargs)


def _run_async_collect(coro_factory: Callable[[], Any]) -> list[Any]:
    """Run an async-generator collection to a list, sync-safe.

    Uses asyncio.run normally; when a loop is already running in this thread,
    runs the coroutine in a dedicated thread with its own loop (S-spec).
    """
    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False
    if not running:
        return asyncio.run(coro_factory())
    out: dict[str, Any] = {}
    def _target() -> None:
        try:
            out["items"] = asyncio.run(coro_factory())
        except Exception as exc:  # propagate to caller
            out["error"] = exc
    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=120)
    if "error" in out:
        raise out["error"]
    return out.get("items", [])


def _best_mp4_variant(tweet: Any) -> str:
    """Highest-bitrate mp4 variant from VERIFIED twscrape media fields."""
    best_url = ""
    best_bitrate = -1
    try:
        videos = getattr(getattr(tweet, "media", None), "videos", []) or []
    except Exception:
        return ""
    for video in videos:
        for variant in getattr(video, "variants", []) or []:
            ctype = str(getattr(variant, "contentType", "") or "").lower()
            url = str(getattr(variant, "url", "") or "")
            if "mp4" not in ctype or not url:
                continue
            try:
                bitrate = int(getattr(variant, "bitrate", 0) or 0)
            except (TypeError, ValueError):
                bitrate = 0
            if bitrate > best_bitrate:
                best_bitrate = bitrate
                best_url = url
    return best_url


def _tweet_age_hours(date: Any) -> float:
    try:
        if isinstance(date, (int, float)):
            taken = datetime.fromtimestamp(float(date), tz=timezone.utc)
        elif isinstance(date, datetime):
            taken = date if date.tzinfo else date.replace(tzinfo=timezone.utc)
        else:
            taken = datetime.fromisoformat(str(date).replace("Z", "+00:00"))
            if taken.tzinfo is None:
                taken = taken.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - taken).total_seconds() / 3600.0
    except Exception:
        return float("inf")


class XVideoSearch(BaseTool):
    name = "x_video_search"
    version = "0.2.0"
    tier = ToolTier.SOURCE
    capability = "news_scrape"
    provider = "x"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.API

    dependencies = ["file:SOCIAL_X_DB_PATH"]
    install_instructions = (
        "Free twscrape path (no paid API). Human-run `make social-setup` adds "
        "accounts to the pool DB; the tool only reads it. Missing library: "
        "pip install -r requirements.txt (twscrape)."
    )
    agent_skills: list[str] = []

    capabilities = ["search_x_videos", "fetch_breaking_footage_leads"]
    supports = {"filter_videos": True, "lookback_hours": True, "free": True}
    best_for = [
        "finding real agency/eyewitness video posts for breaking stories",
        "sourcing X video URLs to download via ytdlp_downloader",
    ]
    not_good_for = [
        "offline use (needs X pool + network)",
        "trending entertainment (use instagram_reel_search)",
    ]
    fallback_tools = ["india_news_scraper"]

    # Contract frozen: keywords/max_results/lookback_hours/output_path unchanged.
    input_schema = {
        "type": "object",
        "required": ["keywords"],
        "properties": {
            "keywords": {"type": "string"},
            "max_results": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100},
            "lookback_hours": {"type": "number", "default": 24, "minimum": 1, "maximum": 168},
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=10, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout", "rate_limit"])
    idempotency_key_fields = ["keywords", "max_results", "lookback_hours"]
    side_effects = ["calls X via twscrape pool (read-only search)"]
    user_visible_verification = ["Open each tweet URL and confirm the video matches the story"]

    def __init__(self, client_factory: Callable[..., Any] | None = None) -> None:
        self._client_factory = client_factory or _default_x_client_factory

    def _db_path(self) -> Path:
        return Path(os.environ.get("SOCIAL_X_DB_PATH", "projects/social/twscrape_accounts.db"))

    def get_status(self) -> ToolStatus:
        try:
            __import__("twscrape")
        except ImportError:
            return ToolStatus.UNAVAILABLE
        if not self._db_path().is_file():
            return ToolStatus.UNAVAILABLE
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def _collect(self, api: Any, query: str, limit: int) -> list[Any]:
        async def _gather() -> list[Any]:
            items: list[Any] = []
            # VERIFIED async API: api.search(q, limit) yields Tweet objects.
            async for tweet in api.search(query, limit=limit):
                items.append(tweet)
                if len(items) >= limit * 2:
                    break
            return items
        return _run_async_collect(_gather)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        cfg = safety.load_config()
        ok, reason = safety.preflight("x", cfg)
        if not ok:
            return ToolResult(success=False, error=safety.sanitize_str(reason))

        try:
            __import__("twscrape")
        except ImportError:
            return ToolResult(
                success=False,
                error="UNAVAILABLE: twscrape not installed. pip install -r requirements.txt",
            )

        keywords = str(inputs.get("keywords", "")).strip()
        if not keywords:
            return ToolResult(success=False, error="keywords is empty")
        max_results = max(1, min(int(inputs.get("max_results", 10)), 100))
        lookback = float(inputs.get("lookback_hours", 24))
        db_path = self._db_path()
        if not db_path.is_file():
            return ToolResult(
                success=False,
                error="UNAVAILABLE: X pool DB missing - rerun make social-setup",
            )

        min_faves = int(cfg.get("min_faves", 5))
        max_age = float(cfg.get("max_age_hours", {}).get("x", 24))
        lookback = min(lookback, max_age) if max_age else lookback
        query = f"{keywords} filter:videos min_faves:{min_faves}"
        proxy = safety.resolve_proxy("x")
        try:
            api = self._client_factory(str(db_path), proxy) if proxy else self._client_factory(str(db_path))
        except TypeError:
            api = self._client_factory(str(db_path))

        budget = safety.RunBudget(cfg)
        if not budget.take_search():
            return ToolResult(success=False, error="per-run search cap reached")
        safety.pace(cfg)

        try:
            tweets = self._collect(api, query, max_results * 2)
        except Exception as exc:
            if safety.is_auth_block(exc):
                safety.trip_breaker("x", f"{type(exc).__name__}: {exc}", cfg)
                return ToolResult(
                    success=False,
                    error=safety.sanitize_str(f"DEGRADED: auth block ({type(exc).__name__}); ladder skips"),
                )
            if safety.is_rate_limit(exc):
                safety.trip_breaker("x", f"rate limited: {exc}", cfg)
                return ToolResult(success=False, error="DEGRADED: rate limited; ladder skips")
            return ToolResult(success=False, error=safety.sanitize_str(f"X search failed: {exc}"))

        used = safety.load_used_ids(cfg)
        rows: list[dict[str, Any]] = []
        for tweet in tweets:
            if _tweet_age_hours(getattr(tweet, "date", None)) > lookback:
                continue
            video_url = _best_mp4_variant(tweet)
            if not video_url:
                continue
            if not budget.take_extraction():
                break
            tid = str(getattr(tweet, "id_str", "") or getattr(tweet, "id", ""))
            if not tid or tid in used:
                continue
            user = getattr(tweet, "user", None)
            author = getattr(user, "username", "") if user else ""
            author_id = getattr(user, "id_str", "") if user else ""
            text = getattr(tweet, "rawContent", "") or ""
            try:
                created = getattr(tweet, "date", None)
                created_at = created.isoformat() if hasattr(created, "isoformat") else str(created or "")
            except Exception:
                created_at = ""
            try:
                likes = int(getattr(tweet, "likeCount", 0) or 0)
            except (TypeError, ValueError):
                likes = 0
            url = getattr(tweet, "url", "") or (f"https://x.com/i/status/{tid}" if tid else "")
            metrics = {
                "like_count": likes,
                "retweet_count": getattr(tweet, "retweetCount", 0),
                "view_count": getattr(tweet, "viewCount", None),
            }
            rows.append({
                # Legacy X-API-shaped fields (contract frozen).
                "id": tid,
                "text": text,
                "created_at": created_at,
                "author_id": author_id,
                "metrics": metrics,
                "url": url,
                # Additive unified lead fields.
                "source": "x",
                "video_url": video_url,
                "title": text[:200] or f"X video {tid}",
                "author": str(author),
                "posted_at": created_at,
                "engagement": likes,
            })
            used.add(tid)
            if len(rows) >= max_results:
                break

        rows.sort(key=lambda r: (r.get("engagement", 0), r.get("posted_at", "")), reverse=True)
        rows = rows[:max_results]
        if rows:
            safety.mark_used([r["id"] for r in rows], cfg)
            safety.record_daily_use("x", len(rows), cfg)

        artifacts: list[str] = []
        if inputs.get("output_path"):
            path = Path(inputs["output_path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(safety.sanitize({"items": rows}), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            artifacts.append(str(path))

        return ToolResult(
            success=True,
            data=safety.sanitize({"items": rows, "count": len(rows), "query": query}),
            artifacts=artifacts,
            duration_seconds=round(time.time() - start, 2),
        )
