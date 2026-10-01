"""Instagram reel search — trending viral reels sourcing (no TikTok).

Free-library path: instagrapi session-only operation (S2). The runtime never
reads IG_PASSWORD and never attempts a password login: it restores settings
JSON from IG_SESSION_PATH, verifies liveness with one cheap read call, and
operates session-only. Invalid/expired sessions return UNAVAILABLE with a
rerun-setup hint. Password login exists ONLY in scripts/social_setup.py.

Anti-ban: static fingerprint (S3), human pacing + caps (S4), circuit breaker
(S5), warm-up gate (S6), secret hygiene (S7), optional proxy (S8).
"""

from __future__ import annotations

import json
import os
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


def _default_ig_client_factory(proxy: str = ""):
    """Real instagrapi client factory (lazy import keeps registry safe)."""
    from instagrapi import Client

    kwargs: dict[str, Any] = {}
    if proxy:
        kwargs["proxy"] = proxy
    return Client(**kwargs)


def _tags_from_query(keywords: str, explicit: str = "") -> list[str]:
    if explicit:
        parts = [p.strip().lstrip("#") for p in explicit.replace(",", " ").split()]
    else:
        parts = [keywords.split()[0]] if keywords.split() else ["news"]
    tags: list[str] = []
    for part in parts:
        cleaned = "".join(c.lower() for c in part if c.isalnum())[:40]
        if cleaned and cleaned not in tags:
            tags.append(cleaned)
    return tags or ["news"]


def _media_age_hours(taken_at: Any) -> float:
    try:
        if taken_at is None:
            return float("inf")
        if isinstance(taken_at, (int, float)):
            taken = datetime.fromtimestamp(float(taken_at), tz=timezone.utc)
        elif isinstance(taken_at, datetime):
            taken = taken_at if taken_at.tzinfo else taken_at.replace(tzinfo=timezone.utc)
        else:
            taken = datetime.fromisoformat(str(taken_at).replace("Z", "+00:00"))
            if taken.tzinfo is None:
                taken = taken.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - taken).total_seconds() / 3600.0
    except Exception:
        return float("inf")


def _engagement(media: Any) -> int:
    try:
        likes = int(getattr(media, "like_count", 0) or 0)
    except (TypeError, ValueError):
        likes = 0
    try:
        plays = int(getattr(media, "play_count", 0) or 0)
    except (TypeError, ValueError):
        plays = 0
    try:
        views = int(getattr(media, "view_count", 0) or 0)
    except (TypeError, ValueError):
        views = 0
    return likes + plays + views


class InstagramReelSearch(BaseTool):
    name = "instagram_reel_search"
    version = "0.2.0"
    tier = ToolTier.SOURCE
    capability = "news_scrape"
    provider = "instagram"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.API

    dependencies = ["env:IG_USERNAME", "file:IG_SESSION_PATH"]
    install_instructions = (
        "Free instagrapi path (no paid API). Run `make social-setup` once as a "
        "human to log in and dump the session, then keep the burner warmed. "
        "Missing library: pip install -r requirements.txt (instagrapi). "
        "Expired session: rerun make social-setup."
    )
    agent_skills: list[str] = []

    capabilities = ["search_trending_reels", "fetch_viral_reel_leads"]
    supports = {"reels_only": True, "free": True}
    best_for = [
        "finding viral IG reels for trending stories",
        "sourcing reel permalinks to download via ytdlp_downloader",
    ]
    not_good_for = [
        "offline use (needs Instagram session + network)",
        "breaking hard news (use x_video_search)",
        "TikTok (banned in India; blocklisted in ytdlp_downloader)",
    ]
    fallback_tools = ["direct_clip_search"]

    # Contract frozen: keywords/max_results/output_path unchanged (additive only).
    input_schema = {
        "type": "object",
        "required": ["keywords"],
        "properties": {
            "keywords": {"type": "string"},
            "max_results": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
            "output_path": {"type": "string"},
            "hashtags": {"type": "string", "description": "Optional space/comma separated tags"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=10, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout", "rate_limit"])
    idempotency_key_fields = ["keywords", "max_results"]
    side_effects = ["calls Instagram via instagrapi session (read-only search)"]
    user_visible_verification = ["Open each permalink and confirm the reel matches the story"]

    def __init__(self, client_factory: Callable[..., Any] | None = None) -> None:
        self._client_factory = client_factory or _default_ig_client_factory

    def get_status(self) -> ToolStatus:
        try:
            __import__("instagrapi")
        except ImportError:
            return ToolStatus.UNAVAILABLE
        session_path = os.environ.get("IG_SESSION_PATH", "projects/social/ig_session.json")
        if not Path(session_path).is_file():
            return ToolStatus.UNAVAILABLE
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def _session_path(self) -> Path:
        return Path(os.environ.get("IG_SESSION_PATH", "projects/social/ig_session.json"))

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        cfg = safety.load_config()
        ok, reason = safety.preflight("instagram", cfg)
        if not ok:
            return ToolResult(success=False, error=safety.sanitize_str(reason))

        try:
            __import__("instagrapi")
        except ImportError:
            return ToolResult(
                success=False,
                error="UNAVAILABLE: instagrapi not installed. pip install -r requirements.txt",
            )

        keywords = str(inputs.get("keywords", "")).strip()
        if not keywords:
            return ToolResult(success=False, error="keywords is empty")
        max_results = max(1, min(int(inputs.get("max_results", 10)), 50))
        session_path = self._session_path()
        if not session_path.is_file():
            return ToolResult(
                success=False,
                error="UNAVAILABLE: session expired - rerun make social-setup",
            )

        proxy = safety.resolve_proxy("instagram")
        try:
            client = self._client_factory(proxy) if proxy else self._client_factory()
        except TypeError:
            client = self._client_factory()

        # S2: restore persisted settings (static fingerprint, S3); never regenerate.
        try:
            client.load_settings(str(session_path))
        except Exception:
            return ToolResult(
                success=False,
                error="UNAVAILABLE: session expired - rerun make social-setup",
            )

        tags = _tags_from_query(keywords, str(inputs.get("hashtags", "") or ""))
        budget = safety.RunBudget(cfg)
        max_age = float(cfg.get("max_age_hours", {}).get("instagram", 72))
        min_faves = int(cfg.get("min_faves", 5))

        # S2 liveness: one cheap read call. Any auth block -> breaker + DEGRADED.
        try:
            client.search_hashtags(tags[0])
        except Exception as exc:
            if safety.is_auth_block(exc):
                safety.trip_breaker("instagram", f"{type(exc).__name__}: {exc}", cfg)
                return ToolResult(
                    success=False,
                    error=safety.sanitize_str(f"DEGRADED: auth block ({type(exc).__name__}); ladder skips"),
                )
            if safety.is_rate_limit(exc):
                safety.trip_breaker("instagram", f"rate limited on liveness: {exc}", cfg)
                return ToolResult(success=False, error="DEGRADED: rate limited; ladder skips")
            return ToolResult(
                success=False,
                error="UNAVAILABLE: session expired - rerun make social-setup",
            )

        collected: list[Any] = []
        for tag in tags[:5]:
            if not budget.take_search():
                break
            if len(collected) >= max_results * 3:
                break
            safety.pace(cfg)
            medias: list[Any] = []
            last_exc: Exception | None = None
            for _attempt in range(3):  # 1 try + max 2 retries (S5)
                try:
                    # VERIFIED clip/reel endpoint (introspected on instagrapi 3.0.16).
                    medias = client.hashtag_medias_reels_v1(tag, amount=max_results) or []
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if safety.is_auth_block(exc):
                        safety.trip_breaker("instagram", f"{type(exc).__name__}: {exc}", cfg)
                        return ToolResult(
                            success=False,
                            error=safety.sanitize_str(f"DEGRADED: auth block ({type(exc).__name__}); ladder skips"),
                        )
                    if not safety.is_rate_limit(exc):
                        break
                    if _attempt < 2:
                        for delay in safety.retry_delays()[_attempt:_attempt + 1]:
                            time.sleep(delay)
            if last_exc is not None and safety.is_rate_limit(last_exc):
                safety.trip_breaker("instagram", f"rate limited: {last_exc}", cfg)
                break
            if last_exc is not None:
                continue
            collected.extend(medias)

        used = safety.load_used_ids(cfg)
        rows: list[dict[str, Any]] = []
        for media in collected:
            video_url = getattr(media, "video_url", None)
            if not video_url:
                continue  # video type filter: reels/clips only
            if _media_age_hours(getattr(media, "taken_at", None)) > max_age:
                continue
            engagement = _engagement(media)
            if engagement < min_faves:
                continue
            media_id = str(getattr(media, "id", "") or getattr(media, "pk", ""))
            if not media_id or media_id in used:
                continue
            taken = getattr(media, "taken_at", None)
            try:
                posted = taken.isoformat() if hasattr(taken, "isoformat") else str(taken or "")
            except Exception:
                posted = ""
            user = getattr(media, "user", None)
            author = getattr(user, "username", "") if user else ""
            caption = getattr(media, "caption_text", "") or ""
            code = getattr(media, "code", "") or ""
            permalink = f"https://www.instagram.com/reel/{code}/" if code else ""
            like_count = getattr(media, "like_count", 0) or 0
            rows.append({
                # Legacy Graph-API-shaped fields (contract frozen).
                "id": media_id,
                "caption": str(caption)[:800],
                "media_type": "REELS",
                "permalink": permalink,
                "timestamp": posted,
                "like_count": like_count,
                # Additive unified lead fields for ladders/footage.
                "source": "instagram",
                "url": str(video_url),
                "title": str(caption)[:200] or f"Instagram reel {code}",
                "author": str(author),
                "posted_at": posted,
                "engagement": engagement,
            })
            used.add(media_id)
            if len(rows) >= max_results * 2:
                break

        # Rank recency x engagement, then cut to top-N and dedupe persist.
        def _rank_key(row: dict[str, Any]) -> tuple:
            try:
                ts = datetime.fromisoformat(row["posted_at"].replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, KeyError, AttributeError):
                ts = datetime.min.replace(tzinfo=timezone.utc)
            return (ts.timestamp() / 3600.0) * (1 + row.get("engagement", 0))

        rows.sort(key=_rank_key, reverse=True)
        rows = rows[:max_results]
        if rows:
            safety.mark_used([r["id"] for r in rows], cfg)
            safety.record_daily_use("instagram", len(rows), cfg)
            try:
                client.dump_settings(str(session_path))
            except Exception:
                pass

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
            data=safety.sanitize({"items": rows, "count": len(rows), "keywords": keywords}),
            artifacts=artifacts,
            duration_seconds=round(time.time() - start, 2),
        )
