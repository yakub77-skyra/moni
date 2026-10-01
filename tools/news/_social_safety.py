"""Shared anti-ban guardrails for free-library social scraping.

Single ownership point for S1-S12 safety rules used by instagram_reel_search
(free instagrapi path) and x_video_search (free twscrape path):

- human pacing + per-run / daily caps
- IST active window (default 08:00-23:00 Asia/Kolkata)
- circuit breaker (DEGRADED until next IST midnight, ladder skips)
- warm-up gate (social.warmed)
- cross-day dedupe (used_media_ids.json)
- proxy resolution (IG_PROXY_URL / X_PROXY_URL)
- secret sanitizer (passwords/cookies/tokens/proxy creds)
- feature flags (social.enabled per platform)

No network calls here. No TikTok. Password-free: this module never reads
IG_PASSWORD; only scripts/social_setup.py (human-run) may use it.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "social_scraping.yaml"
DEFAULT_STATE_DIR = REPO_ROOT / "projects" / "social"

# Read-only allowlist audit: any of these substrings in tool source means a
# write call slipped in (S1). Search endpoints only.
BANNED_WRITE_CALLS = (
    ".like(", ".unlike(", "media_like", "user_follow", ".follow(",
    ".unfollow(", ".comment(", "media_comment", ".post(", "media_post",
    "direct_send", "direct_answer", ".send_direct", "story_view",
    "add_account", "login_burner", "account_login",
)

# Secret keys redacted from logs / events / checkpoints / artifacts (S7).
_SECRET_KEY_RE = re.compile(
    r"(password|passwd|pwd|cookie|token|bearer|sessionid|csrftoken|ds_user|proxy.*pass|api_?key)",
    re.IGNORECASE,
)
_CREDENTIAL_VALUE_RE = re.compile(
    r"(IG_PASSWORD\s*=\s*\S+)|(Bearer\s+\S+)|(password\s*[:=]\s*\S+)",
    re.IGNORECASE,
)

IST_OFFSET = timedelta(hours=5, minutes=30)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ist_now(now: datetime | None = None) -> datetime:
    """Current time in Asia/Kolkata. Zoneinfo when available, else +5:30."""
    now = now or _utcnow()
    try:
        from zoneinfo import ZoneInfo

        return now.astimezone(ZoneInfo("Asia/Kolkata"))
    except Exception:
        return (now + IST_OFFSET).replace(tzinfo=None)


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load config/social_scraping.yaml merged over built-in defaults."""
    defaults: dict[str, Any] = {
        "delays": {"min_seconds": 2.5, "max_seconds": 6.5},
        "per_run_caps": {"searches": 5, "extractions": 10},
        "daily_caps": {"instagram": 20, "x": 30, "halved_without_proxy": True},
        "window": {"start": "08:00", "end": "23:00", "timezone": "Asia/Kolkata"},
        "warmed": False,
        "enabled": {"instagram": True, "x": True},
        "min_faves": 5,
        "max_age_hours": {"instagram": 72, "x": 24},
        "state_dir": "projects/social",
    }
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.is_file():
        return defaults
    try:
        import yaml  # type: ignore

        with open(cfg_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return defaults
    merged = dict(defaults)
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def state_dir(cfg: dict[str, Any] | None = None) -> Path:
    raw = (cfg or {}).get("state_dir", "projects/social")
    p = Path(raw)
    return p if p.is_absolute() else REPO_ROOT / raw


def is_enabled(platform: str, cfg: dict[str, Any] | None = None) -> bool:
    return bool((cfg or load_config()).get("enabled", {}).get(platform, True))


def is_warmed(cfg: dict[str, Any] | None = None) -> bool:
    return bool((cfg or load_config()).get("warmed", False))


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


def within_window(now: datetime | None = None, cfg: dict[str, Any] | None = None) -> bool:
    """True when IST local time is inside [start, end)."""
    cfg = cfg or load_config()
    window = cfg.get("window", {})
    try:
        start_h, start_m = _parse_hhmm(str(window.get("start", "08:00")))
        end_h, end_m = _parse_hhmm(str(window.get("end", "23:00")))
    except ValueError:
        return True
    local = ist_now(now)
    cur = (local.hour, local.minute)
    return (start_h, start_m) <= cur < (end_h, end_m)


def resolve_proxy(platform: str) -> str:
    """Proxy URL for a platform, or '' when direct (S8)."""
    if platform == "instagram":
        return (os.environ.get("IG_PROXY_URL") or "").strip()
    if platform == "x":
        return (os.environ.get("X_PROXY_URL") or "").strip()
    return ""


def daily_cap(platform: str, cfg: dict[str, Any] | None = None) -> int:
    cfg = cfg or load_config()
    caps = cfg.get("daily_caps", {})
    base = int(caps.get(platform, 20))
    if caps.get("halved_without_proxy", True) and not resolve_proxy(platform):
        return max(1, base // 2)
    return base


def _counts_path(cfg: dict[str, Any] | None = None) -> Path:
    return state_dir(cfg) / "daily_counts.json"


def _read_counts(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    path = _counts_path(cfg)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def check_daily_cap(platform: str, cfg: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Check IST-day usage against the (possibly proxy-halved) daily cap."""
    cfg = cfg or load_config()
    today = ist_now().strftime("%Y-%m-%d")
    counts = _read_counts(cfg)
    used = int(counts.get(today, {}).get(platform, 0))
    cap = daily_cap(platform, cfg)
    if used >= cap:
        return False, f"daily cap reached for {platform} ({used}/{cap} IST {today})"
    return True, ""


def record_daily_use(platform: str, n: int = 1, cfg: dict[str, Any] | None = None) -> None:
    cfg = cfg or load_config()
    path = _counts_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = _read_counts(cfg)
    today = ist_now().strftime("%Y-%m-%d")
    day = counts.get(today, {})
    day[platform] = int(day.get(platform, 0)) + n
    counts[today] = day
    path.write_text(json.dumps(counts, indent=2), encoding="utf-8")


def _breaker_path(cfg: dict[str, Any] | None = None) -> Path:
    return state_dir(cfg) / "breaker.json"


def next_ist_midnight_utc(now: datetime | None = None) -> datetime:
    local = ist_now(now)
    nxt = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return (nxt - IST_OFFSET).replace(tzinfo=timezone.utc)


def is_breaker_tripped(platform: str, cfg: dict[str, Any] | None = None,
                       now: datetime | None = None) -> tuple[bool, str]:
    path = _breaker_path(cfg)
    if not path.is_file():
        return False, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, ""
    entry = data.get(platform)
    if not entry:
        return False, ""
    until_raw = entry.get("until", "")
    try:
        until = datetime.fromisoformat(until_raw)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
    except ValueError:
        return False, ""
    current = now or _utcnow()
    if current >= until:
        return False, ""
    return True, str(entry.get("reason", "breaker tripped"))


def trip_breaker(platform: str, reason: str, cfg: dict[str, Any] | None = None) -> Path:
    """Trip the breaker until next IST midnight. Ladder must skip; zero retries."""
    cfg = cfg or load_config()
    path = _breaker_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    data[platform] = {
        "until": next_ist_midnight_utc().isoformat(),
        "reason": sanitize_str(reason)[:300],
        "tripped_at": _utcnow().isoformat(),
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def breaker_clear_hint() -> str:
    return (
        "Breaker tripped (auth/challenge block). Stop for 24-48h, complete any "
        "challenge on the phone, then delete projects/social/breaker.json "
        "(or just that platform's key) and rerun make social-setup."
    )


def _dedupe_path(cfg: dict[str, Any] | None = None) -> Path:
    return state_dir(cfg) / "used_media_ids.json"


def load_used_ids(cfg: dict[str, Any] | None = None) -> set[str]:
    path = _dedupe_path(cfg)
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(map(str, data if isinstance(data, list) else []))
    except (OSError, json.JSONDecodeError):
        return set()


def is_used(media_id: str, cfg: dict[str, Any] | None = None) -> bool:
    return str(media_id) in load_used_ids(cfg)


def mark_used(media_ids: list[str], cfg: dict[str, Any] | None = None) -> None:
    cfg = cfg or load_config()
    path = _dedupe_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = load_used_ids(cfg)
    seen.update(map(str, media_ids))
    path.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")


def pace(cfg: dict[str, Any] | None = None,
         sleep_fn: Callable[[float], None] | None = None) -> float:
    """Human pacing sleep (S4). Returns seconds slept. Inject sleep_fn in tests."""
    cfg = cfg or load_config()
    delays = cfg.get("delays", {})
    lo = float(delays.get("min_seconds", 2.5))
    hi = float(delays.get("max_seconds", 6.5))
    seconds = random.uniform(min(lo, hi), max(lo, hi))
    (sleep_fn or time.sleep)(seconds)
    return seconds


class RunBudget:
    """Per-run caps: 5 searches / 10 URL extractions (S4)."""

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        caps = (cfg or load_config()).get("per_run_caps", {})
        self.max_searches = int(caps.get("searches", 5))
        self.max_extractions = int(caps.get("extractions", 10))
        self.searches = 0
        self.extractions = 0

    def take_search(self) -> bool:
        if self.searches >= self.max_searches:
            return False
        self.searches += 1
        return True

    def take_extraction(self) -> bool:
        if self.extractions >= self.max_extractions:
            return False
        self.extractions += 1
        return True


def sanitize_str(text: str) -> str:
    """Redact passwords/cookies/tokens/proxy creds (S7)."""
    if not isinstance(text, str):
        text = str(text)
    redacted = _CREDENTIAL_VALUE_RE.sub("[REDACTED]", text)
    # Redact proxy-userinfo: scheme://user:pass@host
    redacted = re.sub(r"(://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", redacted)
    return redacted


def sanitize(obj: Any) -> Any:
    """Recursively redact secret keys/values for logs, events, checkpoints."""
    if isinstance(obj, dict):
        clean: dict[str, Any] = {}
        for key, value in obj.items():
            safe_key = sanitize_str(key) if isinstance(key, str) else key
            if isinstance(key, str) and _SECRET_KEY_RE.search(key):
                clean[safe_key] = "[REDACTED]"
            else:
                clean[safe_key] = sanitize(value)
        return clean
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, str):
        if _SECRET_KEY_RE.search(obj) and len(obj) < 120:
            return "[REDACTED]"
        return sanitize_str(obj)
    return obj


def assert_readonly_source(path: str | Path) -> None:
    """Fail if a tool source contains write-side social calls (S1)."""
    source = Path(path).read_text(encoding="utf-8")
    hits = [b for b in BANNED_WRITE_CALLS if b in source]
    if hits:
        raise AssertionError(f"write calls in read-only tool {path}: {hits}")


def assert_password_free_source(path: str | Path) -> None:
    """Fail if runtime tool source *uses* IG_PASSWORD (S2).

    Docstring mentions ("never reads IG_PASSWORD") are allowed; only real
    env access counts as use. Skips this helper's own definition lines.
    """
    key = "IG_" + "PASSWORD"  # avoid self-match on this helper's source
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "re.search" in line or "assert_password" in line or "never reads" in line:
            continue
        if key in line and ("environ" in line or "getenv" in line):
            raise AssertionError(f"IG_PASSWORD used in runtime tool {path}: {line.strip()[:120]}")


def preflight(platform: str, cfg: dict[str, Any] | None = None,
              now: datetime | None = None) -> tuple[bool, str]:
    """Ordered gate check with ZERO network calls. Returns (ok, reason)."""
    cfg = cfg or load_config()
    if not is_enabled(platform, cfg):
        return False, f"{platform} disabled by social.enabled flag"
    if not is_warmed(cfg):
        return False, (
            "social not warmed (social.warmed=false). Warm the burner 7-10 days "
            "manually, then rerun make social-setup."
        )
    if not within_window(now, cfg):
        window = cfg.get("window", {})
        return False, (
            f"outside active window {window.get('start')}-{window.get('end')} "
            "Asia/Kolkata; zero calls made"
        )
    tripped, reason = is_breaker_tripped(platform, cfg, now)
    if tripped:
        return False, f"breaker tripped: {reason}. {breaker_clear_hint()}"
    ok, reason = check_daily_cap(platform, cfg)
    if not ok:
        return False, reason
    return True, ""


_AUTH_BLOCK_HINTS = (
    "challengerequired", "checkpointrequired", "loginrequired",
    "clientloginrequired", "twofactorrequired", "sentryblock",
)


def is_auth_block(exc: BaseException) -> bool:
    name = type(exc).__name__.lower().replace("_", "")
    if any(h in name for h in _AUTH_BLOCK_HINTS):
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    if "401" in text or "403" in text:
        return True
    if "429" in text and ("auth" in text or "login" in text or "challenge" in text):
        return True
    return False


def is_rate_limit(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    if any(h in name for h in ("ratelimit", "throttled", "pleasewaitfewminutes")):
        return True
    return "429" in f"{type(exc).__name__} {exc}"


def retry_delays() -> list[float]:
    """S5: max 2 retries, backoff 5s/25s + jitter."""
    return [5.0 + random.uniform(0, 1.0), 25.0 + random.uniform(0, 2.0)]
