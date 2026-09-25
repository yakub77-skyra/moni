"""India daily-news RSS scraper (public feeds, Indian outlets only).

Polls public RSS/Atom endpoints (no auth, no paywall bypass), keeps items
from the last N hours, dedupes by URL, ranks with outlet diversity, and
optionally extracts article body + lead image. Headline/summary reuse with
a source link is fair-use-compatible. Lead images are REFERENCE ONLY and
must be re-licensed via Pexels/Pixabay/Unsplash or regenerated -- never
burn publisher photos into the render.
"""

from __future__ import annotations

import html as html_lib
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

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

from tools.news.state_keywords import INDIA_NEWS_FEEDS, STATE_KEYWORDS


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# XML-illegal control chars + stray bare ampersands are the two things that
# most often break publisher feeds. Sanitizing lets us keep outlets whose
# feeds are sloppy instead of dropping them.
_INVALID_XML_RE = re.compile(r"[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD]")
_BARE_AMP_RE = re.compile(r"&(?!(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);)")


def _sanitize_xml(payload: bytes) -> bytes:
    text = payload.decode("utf-8", errors="replace")
    text = _INVALID_XML_RE.sub("", text)
    text = _BARE_AMP_RE.sub("&amp;", text)
    return text.encode("utf-8")


_ITEM_BLOCK_RE = re.compile(r"(?is)<item\b[^>]*>(.*?)</item>|<entry\b[^>]*>(.*?)</entry>")
_FIELD_RE = {
    "title": re.compile(r"(?is)<title\b[^>]*>(.*?)</title>"),
    "description": re.compile(r"(?is)<description\b[^>]*>(.*?)</description>"),
    "summary": re.compile(r"(?is)<summary\b[^>]*>(.*?)</summary>"),
    "pubDate": re.compile(r"(?is)<pubDate\b[^>]*>(.*?)</pubDate>"),
    "published": re.compile(r"(?is)<published\b[^>]*>(.*?)</published>"),
    "updated": re.compile(r"(?is)<updated\b[^>]*>(.*?)</updated>"),
    "link_plain": re.compile(r"(?is)<link\b[^>]*>(.*?)</link>"),
    "link_href": re.compile(r"(?is)<link\b[^>]*href=[\"']([^\"']+)[\"']"),
    "guid": re.compile(r"(?is)<guid\b[^>]*>(.*?)</guid>"),
    "media": re.compile(r"(?is)<(?:media:content|media:thumbnail|enclosure)\b[^>]*url=[\"']([^\"']+)[\"']"),
}


def _field(block: str, name: str) -> str:
    match = _FIELD_RE[name].search(block)
    return _clean(match.group(1)) if match else ""


def _parse_feed_lenient(payload: bytes) -> list[dict[str, Any]]:
    """Regex fallback for feeds whose XML is too broken for ElementTree.

    firstpost.in and scroll.in both ship feeds with unescaped tokens that
    ElementTree rejects outright; dropping two major outlets for that is
    worse than parsing them by hand.
    """
    text = payload.decode("utf-8", errors="replace")
    items: list[dict[str, Any]] = []
    for match in _ITEM_BLOCK_RE.finditer(text):
        block = match.group(1) or match.group(2) or ""
        title = _field(block, "title")
        link = (
            _field(block, "link_plain")
            or (_FIELD_RE["link_href"].search(block).group(1) if _FIELD_RE["link_href"].search(block) else "")
            or _field(block, "guid")
        )
        if not title or not link:
            continue
        items.append({
            "title": title,
            "summary": (_field(block, "description") or _field(block, "summary"))[:800],
            "url": html_lib.unescape(link),
            "published": (
                _field(block, "pubDate")
                or _field(block, "published")
                or _field(block, "updated")
            ),
            "image": (
                _FIELD_RE["media"].search(block).group(1)
                if _FIELD_RE["media"].search(block)
                else ""
            ),
        })
    return items

# Hard-news signals: these are what a daily roundup should surface first.
HARD_NEWS_KEYWORDS = (
    "supreme court", "high court", "court", "verdict", "fir", "arrest", "cbi",
    "ed ", "probe", "parliament", "lok sabha", "rajya sabha", "bill", "minister",
    "government", "cabinet", "election", "poll", "assembly", "cm ", "chief minister",
    "governor", "army", "navy", "air force", "border", "defence", "security",
    "police", "crime", "rape", "murder", "scam", "fraud", "corruption", "tax",
    "gst", "rbi", "inflation", "gdp", "economy", "budget", "unemployment",
    "flood", "rain", "cyclone", "earthquake", "landslide", "fire", "accident",
    "strike", "protest", "bandh", "ban", "policy", "scheme", "reservation",
    "infrastructure", "metro", "railway", "highway", "power", "energy",
)

# Soft/lifestyle signals: demoted, and dropped entirely when hard news is
# plentiful, so the roundup does not lead with horoscopes and recipes.
SOFT_NEWS_KEYWORDS = (
    "horoscope", "astrology", "rashifal", "zodiac", "tarot", "numerology",
    "recipe", "food trends", "restaurant", "fashion", "beauty", "skincare",
    "celebrity", "bollywood", "entertainment", "hollywood", "movie",
    "film", "actor", "actress", "trailer", "web series", "box office",
    "lifestyle", "relationship", "dating", "wedding food", "haircut",
    "travel guide", "vacation", "weight loss", "diet", "gadget review",
    "phone review", "top 10", "best deals", "amazon sale", "flipkart sale",
    "cricket", "ipl", "asian games", "olympic", "sports", "football",
    "tennis", "badminton", "tournament", "match report",
)

# An Indian publisher may carry world news. Explicit foreign-country signals
# therefore take precedence over the source feed, while international stories
# that genuinely involve India remain eligible.
FOREIGN_LOCATION_KEYWORDS = (
    "mexico", "pakistan", "bangladesh", "nepal", "sri lanka", "china",
    "russia", "ukraine", "israel", "gaza", "palestine", "iran", "iraq",
    "syria", "lebanon", "turkey", "egypt", "libya", "sudan", "nigeria",
    "kenya", "ethiopia", "afghanistan", "myanmar", "bhutan", "maldives",
    "japan", "south korea", "north korea", "united states", "u.s.", "usa",
    "canada", "brazil", "argentina", "chile", "colombia", "peru",
    "venezuela", "france", "germany", "britain", "uk ", "england",
    "scotland", "wales", "ireland", "italy", "spain", "portugal",
    "netherlands", "belgium", "switzerland", "austria", "poland", "greece",
    "australia", "new zealand", "indonesia", "thailand", "vietnam",
    "philippines", "malaysia", "singapore", "south africa", "kenya",
    "hurricane", "typhoon", "european union", "white house", "pentagon",
)
INDIA_CONTEXT_KEYWORDS = (
    "india", "indian", "bharat", "new delhi", "delhi", "mumbai", "bengaluru",
    "bangalore", "chennai", "kolkata", "hyderabad", "pune", "ahmedabad",
    "jaipur", "lucknow", "patna", "bhopal", "thiruvananthapuram", "kerala",
    "karnataka", "maharashtra", "tamil nadu", "west bengal", "gujarat",
    "rajasthan", "uttar pradesh", "andhra pradesh", "telangana", "punjab",
    "haryana", "bihar", "odisha", "assam", "jharkhand", "uttarakhand",
    "himachal", "chhattisgarh", "jammu", "kashmir", "lakshadweep",
    "andaman", "ladakh", "goa", "supreme court of india", "lok sabha",
    "rajya sabha", "rbi", "indian rupees", "rupee", "crore", "lakh",
)


# "Punjab province" (or equivalent phrasing) means Pakistan's Punjab, not
# India's Punjab state — it must cancel a bare "punjab" India signal.
_PAKISTAN_PUNJAB_RE = re.compile(
    r"punjab\s+province|punjab\s*,\s*pakistan|pakistan'?s?\s+punjab"
)


def _is_india_related(title: str, summary: str = "") -> bool:
    """Reject foreign-only incidents from Indian publisher feeds.

    Publisher location is not story location. A title explicitly centered on
    a foreign place/event is rejected unless its own text supplies a clear
    India, Indian, or domestic-policy connection.
    """
    hay = f"{title} {summary}".lower()
    india_hits = [key for key in INDIA_CONTEXT_KEYWORDS if key in hay]
    if _PAKISTAN_PUNJAB_RE.search(hay):
        # Pakistan's Punjab province is not India's Punjab state.
        india_hits = [hit for hit in india_hits if hit != "punjab"]
    if india_hits:
        return True
    return not any(key in hay for key in FOREIGN_LOCATION_KEYWORDS)


def _clean(raw: str) -> str:
    return _WS_RE.sub(" ", html_lib.unescape(_TAG_RE.sub(" ", raw or ""))).strip()


def _score_story(title: str, summary: str) -> int:
    """Rank stories: hard news up, soft/lifestyle down."""
    hay = f"{title} {summary}".lower()
    score = 0
    score += 3 * sum(1 for k in HARD_NEWS_KEYWORDS if k in hay)
    score -= 4 * sum(1 for k in SOFT_NEWS_KEYWORDS if k in hay)
    return score


def _categorize(title: str, summary: str) -> str:
    hay = f"{title} {summary}".lower()
    if any(k in hay for k in SOFT_NEWS_KEYWORDS):
        return "soft"
    if any(k in hay for k in HARD_NEWS_KEYWORDS):
        return "hard_news"
    return "general"



class IndiaNewsScraper(BaseTool):
    name = "india_news_scraper"
    version = "0.1.0"
    tier = ToolTier.SOURCE
    capability = "news_scrape"
    provider = "rss_india"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.API

    dependencies: list[str] = []
    install_instructions = (
        "No setup required. Stdlib urllib + xml against public Indian RSS. "
        "No API key needed."
    )
    agent_skills: list[str] = []

    capabilities = [
        "fetch_india_news",
        "scrape_rss_feeds",
        "rank_daily_stories",
        "detect_state",
        "extract_article_text",
    ]
    supports = {
        "outlets": sorted(INDIA_NEWS_FEEDS.keys()),
        "rss_only": True,
        "requires_api_key": False,
        "state_detection": True,
        "free": True,
    }
    best_for = [
        "daily India news roundup sourcing (last-24h, top stories first)",
        "feeding 5 map-card stories into the india-in-last-24hr pipeline",
    ]
    not_good_for = [
        "paywalled full text (RSS headline/summary + best-effort body)",
        "non-Indian outlets (out of scope by design)",
        "video clip extraction (publishers rarely expose video files)",
    ]
    fallback_tools = ["corpus_builder"]

    input_schema = {
        "type": "object",
        "properties": {
            "outlets": {"type": "array", "items": {"type": "string"}},
            "max_items": {"type": "integer", "default": 5, "minimum": 1, "maximum": 25},
            "lookback_hours": {"type": "number", "default": 24, "minimum": 1, "maximum": 72},
            "fetch_article_body": {"type": "boolean", "default": True},
            "request_timeout_seconds": {"type": "number", "default": 15, "minimum": 5, "maximum": 60},
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout"])
    idempotency_key_fields = ["outlets", "max_items", "lookback_hours"]
    side_effects = ["fetches public RSS feeds", "optionally downloads article pages"]
    user_visible_verification = [
        "Open each source_url and confirm headline + state match",
    ]

    _USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def _fetch(self, url: str, timeout: float) -> tuple[bytes, str]:
        req = urllib.request.Request(url, headers={"User-Agent": self._USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read(), resp.headers.get_content_charset() or "utf-8"

    @staticmethod
    def _parse_date(value: str) -> datetime | None:
        if not value:
            return None
        text = value.strip()
        try:
            dt = parsedate_to_datetime(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _parse_feed(self, payload: bytes, outlet: str) -> list[dict[str, Any]]:
        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            try:  # retry once with sloppy-feed sanitizing
                root = ET.fromstring(_sanitize_xml(payload))
            except ET.ParseError:  # last resort: regex extraction
                lenient = _parse_feed_lenient(payload)
                for item in lenient:
                    item["outlet"] = outlet
                    item["published"] = self._parse_date(item["published"])
                return [i for i in lenient if i["title"] and i["url"]]
        items: list[dict[str, Any]] = []
        for item in root.findall(".//item"):
            def _text(tag: str) -> str:
                el = item.find(tag)
                return _clean(el.text or "") if el is not None else ""

            link = _text("link")
            guid_el = item.find("guid")
            guid = _clean(guid_el.text or "") if guid_el is not None else ""
            image = ""
            for tag in (
                "{http://search.yahoo.com/mrss/}content",
                "{http://search.yahoo.com/mrss/}thumbnail",
                "enclosure",
            ):
                el = item.find(tag)
                if el is not None and el.attrib.get("url"):
                    image = el.attrib["url"]
                    break
            items.append({
                "outlet": outlet,
                "title": _text("title"),
                "summary": _text("description")[:800],
                "url": link or guid,
                "published": self._parse_date(_text("pubDate")),
                "image": image,
            })
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.findall(f".//{ns}entry"):
            def _atom(tag: str) -> str:
                el = entry.find(f"{ns}{tag}")
                return _clean(el.text or "") if el is not None else ""

            link = ""
            for el in entry.findall(f"{ns}link"):
                if el.attrib.get("rel", "alternate") == "alternate" and el.attrib.get("href"):
                    link = el.attrib["href"]
                    break
            items.append({
                "outlet": outlet,
                "title": _atom("title"),
                "summary": (_atom("summary") or _atom("content"))[:800],
                "url": link,
                "published": self._parse_date(_atom("published") or _atom("updated")),
                "image": "",
            })
        return [i for i in items if i["title"] and i["url"]]

    def _extract_article(self, url: str, timeout: float) -> dict[str, Any]:
        """Best-effort body + lead image from the article page."""
        try:
            payload, charset = self._fetch(url, timeout)
            html = payload.decode(charset, errors="ignore")
        except Exception as exc:
            return {"body": "", "lead_image": "", "error": str(exc)[:200]}
        lead = ""
        m = re.search(
            r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)",
            html,
            re.IGNORECASE,
        )
        if m:
            lead = urllib.parse.urljoin(url, html_lib.unescape(m.group(1)))
        cleaned = re.sub(
            r"(?is)<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>", " ", html
        )
        paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", cleaned)
        body_paras = [_clean(p) for p in paras]
        body_paras = [p for p in body_paras if len(p.split()) >= 8]
        return {"body": "\n\n".join(body_paras[:40])[:6000], "lead_image": lead, "error": ""}

    @staticmethod
    def _detect_state(haystack: str) -> str:
        from tools.news.state_keywords import STATE_KEYWORDS

        hay = (haystack or "").lower()
        for state, keywords in STATE_KEYWORDS.items():
            if any(k in hay for k in keywords):
                return state
        return "India"

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        outlets = inputs.get("outlets") or sorted(INDIA_NEWS_FEEDS.keys())
        unknown = [o for o in outlets if o not in INDIA_NEWS_FEEDS]
        if unknown:
            return ToolResult(
                success=False,
                error=f"Unknown outlets: {unknown}. Valid: {sorted(INDIA_NEWS_FEEDS)}",
            )
        max_items = int(inputs.get("max_items", 5))
        lookback = float(inputs.get("lookback_hours", 24))
        fetch_body = bool(inputs.get("fetch_article_body", True))
        timeout = float(inputs.get("request_timeout_seconds", 15))
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)

        collected: list[dict[str, Any]] = []
        feed_errors: list[str] = []
        for outlet in outlets:
            for feed_url in INDIA_NEWS_FEEDS[outlet]:
                try:
                    payload, _ = self._fetch(feed_url, timeout)
                    collected.extend(self._parse_feed(payload, outlet))
                except Exception as exc:
                    feed_errors.append(f"{outlet}: {feed_url} -> {exc!s}"[:220])

        epoch = datetime.min.replace(tzinfo=timezone.utc)
        collected.sort(
            key=lambda i: (i["published"] is not None, i["published"] or epoch),
            reverse=True,
        )
        seen: set[str] = set()
        fresh: list[dict[str, Any]] = []
        for item in collected:
            key = item["url"].split("?")[0].rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            if item["published"] is None or item["published"] >= cutoff:
                if _is_india_related(item["title"], item["summary"]):
                    fresh.append(item)

        # Prefer hard news; only fall back to soft/lifestyle when hard news
        # is scarce, so the roundup never leads with horoscopes.
        for item in fresh:
            item["_score"] = _score_story(item["title"], item["summary"])
            item["_category"] = _categorize(item["title"], item["summary"])
        hard = [i for i in fresh if i["_category"] == "hard_news"]
        general = [i for i in fresh if i["_category"] == "general"]
        soft = [i for i in fresh if i["_category"] == "soft"]
        pools = [hard, general, soft]

        # Rank inside each pool: strongest hard-news signal first, then recency.
        for pool in pools:
            pool.sort(
                key=lambda i: (
                    i["_score"],
                    i["published"] or epoch,
                ),
                reverse=True,
            )

        ranked: list[dict[str, Any]] = []
        used_outlets: set[str] = set()
        # Fill strictly in pool order: every hard-news story is placed before
        # any general one, and soft/lifestyle items only appear when the
        # harder pools cannot fill the slate.
        for pool in pools:
            if len(ranked) >= max_items:
                break
            for item in pool:  # pass 1: one story per outlet
                if len(ranked) >= max_items:
                    break
                if item["outlet"] in used_outlets or item in ranked:
                    continue
                ranked.append(item)
                used_outlets.add(item["outlet"])
            for item in pool:  # pass 2: backfill duplicate outlets
                if len(ranked) >= max_items:
                    break
                if item not in ranked:
                    ranked.append(item)
        ranked = ranked[:max_items]

        stories: list[dict[str, Any]] = []
        for rank, item in enumerate(ranked, start=1):
            body, lead_image, body_error = "", item.get("image", ""), ""
            if fetch_body:
                extracted = self._extract_article(item["url"], timeout)
                body = extracted["body"]
                lead_image = extracted["lead_image"] or lead_image
                body_error = extracted["error"]
            hay = f"{item['title']} {item['summary']} {body[:1200]}"
            stories.append({
                "rank": rank,
                "outlet": item["outlet"],
                "headline": item["title"],
                "summary": item["summary"],
                "source_url": item["url"],
                "published": item["published"].isoformat() if item["published"] else "",
                "state": self._detect_state(hay),
                "category": _categorize(item["title"], item["summary"]),
                "body_text": body,
                "lead_image": lead_image,
                "body_error": body_error,
            })

        artifacts: list[str] = []
        if inputs.get("output_path"):
            path = Path(inputs["output_path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            import json

            path.write_text(
                json.dumps(
                    {"stories": stories, "feed_errors": feed_errors,
                     "outlets_polled": outlets},
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            artifacts.append(str(path))

        return ToolResult(
            success=True,
            data={
                "stories": stories,
                "story_count": len(stories),
                "outlets_polled": outlets,
                "feed_errors": feed_errors,
                "note": "Headline/summary only; link source_url on the end card. "
                "Lead images are REFERENCE ONLY -- re-license via stock media.",
            },
            artifacts=artifacts,
            duration_seconds=round(time.time() - start, 2),
        )

