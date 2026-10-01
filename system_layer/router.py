"""System Layer router — routes scraped news items to one style pipeline.

Deterministic keywords first. LLM tie-break ONLY when confidence < 0.7.

Styles:
  breaking -> pipeline_defs/breaking-news.yaml (BreakingNewsReel)
  trending -> pipeline_defs/trending-news.yaml (TrendingNewsReel)
  india_daily -> pipeline_defs/india-in-last-24hr.yaml (IndiaDailyNews, Style 3)

One reference pack per job. The router emits exactly one style per item.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path(__file__).resolve().parent / "style_registry.yaml"
SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "schemas"
    / "news_job_manifest.schema.json"
)

# Deterministic signals. Breaking = hard/urgent real-world events.
# Trending = viral/entertainment/UGC-friendly topics. No TikTok anywhere.
BREAKING_KEYWORDS = (
    "breaking", "just in", "urgent", "alert", "explosion", "blast",
    "earthquake", "attack", "terror", "war ", "missile", "airstrike",
    "crash", "accident", "fire ", "flood", "cyclone", "landslide",
    "verdict", "arrest", "raid", "emergency", "shoot", "killed",
    "deaths", "resign", "coup", "impeach", "stampede", "collapse",
)

TRENDING_KEYWORDS = (
    "viral", "trending", "meme", "challenge", "trailer", "teaser",
    "box office", "song launch", "reel", "shorts", "big boss", "bigg boss",
    "celebrity", "influencer", "dance", "cover ", "reaction",
    "fashion week", "launch event", "first look", "poster out",
)

CONFIDENCE_THRESHOLD = 0.7


def _hits(text: str, keywords: tuple[str, ...]) -> int:
    hay = f" {text.lower()} "
    return sum(1 for k in keywords if k in hay)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "news").lower()).strip("-")
    return slug[:48] or "news"


def _load_registry() -> dict[str, Any]:
    import yaml

    with open(REGISTRY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _llm_tiebreak(item: dict[str, Any]) -> dict[str, Any] | None:
    """Zero-cost OpenRouter tie-break. Returns None when unavailable.

    Only called when deterministic confidence < 0.7. Requires
    OPENROUTER_API_KEY and a ':free'/'/free' model. Never raises.
    """
    import os
    import urllib.request

    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return None
    try:
        body = json.dumps({
            "model": "openrouter/free",
            "messages": [
                {"role": "system", "content": (
                    "Classify one India news item as breaking, trending, or "
                    "india_daily. breaking=urgent hard event. "
                    "trending=viral/entertainment. india_daily=anything else. "
                    "Return STRICT JSON: {\"style\": ..., \"confidence\": 0-1}."
                )},
                {"role": "user", "content": json.dumps({
                    "title": item.get("title", ""),
                    "summary": item.get("summary", "")[:500],
                })},
            ],
            "temperature": 0.0,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://openmontage.video",
                "X-Title": "OpenMontage style-router",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
        content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = json.loads(content.strip().strip("`"))
        style = str(parsed.get("style", "")).strip()
        conf = float(parsed.get("confidence", 0.0))
        if style in ("breaking", "trending", "india_daily"):
            return {"style": style, "confidence": min(max(conf, 0.0), 1.0)}
    except Exception:
        return None
    return None


def route_news_item(item: dict[str, Any]) -> dict[str, Any]:
    """Route one scraped news item to a style pipeline.

    Args:
        item: {title, summary?, source_url?, outlet?, state?}.

    Returns:
        Job manifest dict matching schemas/news_job_manifest.schema.json.
    """
    registry = _load_registry()
    styles = registry.get("styles", {})

    text = f"{item.get('title', '')} {item.get('summary', '')}"
    b_hits = _hits(text, BREAKING_KEYWORDS)
    t_hits = _hits(text, TRENDING_KEYWORDS)
    total = b_hits + t_hits

    if total == 0:
        style, confidence, reason = "india_daily", 0.6, "no style keywords; default to Style 3"
    elif b_hits > 0 and t_hits == 0:
        style, confidence, reason = "breaking", 0.9, f"breaking keywords matched: {b_hits}"
    elif t_hits > 0 and b_hits == 0:
        style, confidence, reason = "trending", 0.9, f"trending keywords matched: {t_hits}"
    else:
        winner = "breaking" if b_hits >= t_hits else "trending"
        confidence = max(b_hits, t_hits) / total
        reason = f"keyword split breaking={b_hits} trending={t_hits}"
        style = winner

    tiebreak_used = False
    if confidence < CONFIDENCE_THRESHOLD:
        llm = _llm_tiebreak(item)
        if llm is not None:
            style = llm["style"]
            confidence = llm["confidence"]
            reason = f"llm tie-break overrode deterministic ({reason})"
            tiebreak_used = True
        else:
            reason = f"{reason}; confidence below 0.7, no LLM key — keeping deterministic pick"

    entry = styles.get(style, styles.get("india_daily", {}))
    title = str(item.get("title", "news")).strip() or "news"
    manifest = {
        "job_id": f"{datetime.now(timezone.utc):%Y%m%d}-{style}-{_slug(title)}",
        "style": style,
        "pipeline": entry.get("pipeline", "india-in-last-24hr"),
        "composition": entry.get("composition", "IndiaDailyNews"),
        "reference_pack": entry.get("reference_pack", "references/style3_map"),
        "confidence": round(float(confidence), 3),
        "tiebreak_used": tiebreak_used,
        "reason": reason,
        "source_item": {
            "title": item.get("title", ""),
            "summary": item.get("summary", ""),
            "source_url": item.get("source_url", ""),
            "outlet": item.get("outlet", ""),
            "state": item.get("state", ""),
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    if SCHEMA_PATH.exists():
        import jsonschema

        with open(SCHEMA_PATH, encoding="utf-8") as f:
            jsonschema.validate(instance=manifest, schema=json.load(f))

    return manifest


def route_batch(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Route many items. Pure — no I/O except schema validation."""
    return [route_news_item(item) for item in items]
