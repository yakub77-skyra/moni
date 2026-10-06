"""yt-dlp downloader — policy-enforcing footage acquisition.

Thin guard over yt-dlp: blocklists TikTok (banned in India), defaults to
`assets/raw/`, and returns a local file path for footage_selector.
Delegates the actual download to the `yt_dlp` Python package when present,
else the `yt-dlp` binary. No URL is ever fabricated.

IP-block escape hatch (GitHub Actions datacenter IPs get 403/429 from
YouTube): set a proxy pointing at your own network and ONLY YouTube/search
traffic goes through it — heavy Remotion/FFmpeg rendering stays on GitHub.
Env (also accepted as per-call inputs `proxy`, `cookies_file`,
`cookies_from_browser`):

  YTDLP_PROXY_URL=http://user:pass@your-home-ip:port
  YTDLP_COOKIES_FILE=/path/to/youtube-cookies.txt   (Netscape format)
  YTDLP_COOKIES_FROM_BROWSER=chrome                 (local runs only)
"""

from __future__ import annotations

import os
import random
import shutil
import time
import urllib.parse
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

BLOCKED_HOSTS = ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com", "m.tiktok.com")

# Player-client fallback matrix for HTTP 403 / rate-limit blocks (additive).
# On a block error the same URL is retried once per client in order.
PLAYER_CLIENTS = ("web_safari", "android", "ios", "mweb")


def _is_blocked(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return False
    return any(host == b or host.endswith("." + b) for b in BLOCKED_HOSTS)


def _looks_like_block(exc: Exception) -> bool:
    """True for HTTP 403 / 429-style blocks worth a player-client retry."""
    text = f"{type(exc).__name__}: {exc}".lower()
    return (
        "403" in text
        or "forbidden" in text
        or "429" in text
        or "too many requests" in text
        or "rate limit" in text
        or "rate-limit" in text
    )


def _client_backoff(attempt: int) -> float:
    """3-8s jittered backoff, growing slightly per attempt."""
    return random.uniform(3.0, 8.0) + attempt


def _resolve_proxy(explicit: str | None = None) -> str | None:
    """Proxy URL for YouTube/search traffic (your own IP, not GitHub's).

    Precedence: explicit per-call `proxy` input > YTDLP_PROXY_URL env >
    HTTPS_PROXY/HTTP_PROXY env (honoured so a runner-level proxy also works).
    Empty/blank values mean "no proxy" (current GitHub-direct behaviour).
    """
    for candidate in (
        explicit,
        os.environ.get("YTDLP_PROXY_URL"),
        os.environ.get("HTTPS_PROXY"),
        os.environ.get("HTTP_PROXY"),
        os.environ.get("https_proxy"),
        os.environ.get("http_proxy"),
    ):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return None


def _resolve_cookies_file(explicit: str | None = None) -> str | None:
    """Netscape cookies file for authenticated YouTube reads, if configured."""
    candidate = explicit or os.environ.get("YTDLP_COOKIES_FILE")
    if candidate and str(candidate).strip() and Path(str(candidate).strip()).is_file():
        return str(candidate).strip()
    return None


def _resolve_cookies_from_browser(explicit: str | None = None) -> str | None:
    """Browser name for --cookies-from-browser (local runs only, never CI)."""
    candidate = explicit or os.environ.get("YTDLP_COOKIES_FROM_BROWSER")
    if candidate and str(candidate).strip():
        return str(candidate).strip()
    return None


class YtdlpDownloader(BaseTool):
    name = "ytdlp_downloader"
    version = "0.2.0"
    tier = ToolTier.SOURCE
    capability = "clip_acquisition"
    provider = "yt-dlp"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["python:yt_dlp"]
    install_instructions = (
        "Install yt-dlp: pip install yt-dlp\n"
        "For YouTube support, also install Deno (JS runtime): https://deno.land/#installation\n"
        "IP-blocked on GitHub Actions (403/429)? Route YouTube via your own IP:\n"
        "  YTDLP_PROXY_URL=http://user:pass@your-home-ip:port  (only YouTube/search uses it)"
    )
    agent_skills = ["video-download"]

    capabilities = ["download_video_url", "fetch_reel_footage", "fetch_trailer_footage"]
    supports = {"tiktok_blocked": True, "default_dir": "assets/raw",
                "player_client_retry": True, "batch_urls": True,
                "proxy": True, "cookies_file": True, "cookies_from_browser": True}
    best_for = [
        "downloading IG Reels / YT Shorts / trailer URLs found by search tools",
        "acquiring real footage for breaking and trending reels",
    ]
    not_good_for = [
        "TikTok URLs (blocklisted — banned in India)",
        "DRM-protected content",
        "semantic ranking (use clip_search)",
    ]
    fallback_tools = ["video_downloader", "direct_clip_search"]

    input_schema = {
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "description": "Video/reel/shorts/trailer URL"},
            "urls": {"type": "array", "items": {"type": "string"},
                     "description": "Optional batch: per-item SKIPPED with reason, never aborts"},
            "output_dir": {"type": "string", "default": "assets/raw"},
            "max_resolution": {"type": "string", "default": "720p"},
            "proxy": {"type": "string",
                      "description": "Optional proxy URL for this call (overrides "
                                     "YTDLP_PROXY_URL). Routes YouTube/search via your "
                                     "own IP instead of the GitHub datacenter IP."},
            "cookies_file": {"type": "string",
                             "description": "Optional Netscape cookies file for "
                                            "authenticated YouTube reads (overrides "
                                            "YTDLP_COOKIES_FILE)."},
            "cookies_from_browser": {"type": "string",
                                     "description": "Optional browser name for "
                                                    "yt-dlp --cookies-from-browser "
                                                    "(local runs only, never CI)."},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=2000, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout"])
    idempotency_key_fields = ["url", "max_resolution"]
    side_effects = ["downloads media file to output_dir"]
    user_visible_verification = ["Play the downloaded file and confirm it matches the story"]

    def get_status(self) -> ToolStatus:
        try:
            __import__("yt_dlp")
            return ToolStatus.AVAILABLE
        except ImportError:
            pass
        return ToolStatus.AVAILABLE if shutil.which("yt-dlp") else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        urls = inputs.get("urls")
        if isinstance(urls, list) and urls:
            return self._execute_batch([str(u) for u in urls], inputs, start)
        url = str(inputs.get("url", "")).strip()
        if not url:
            return ToolResult(success=False, error="url is empty")
        if _is_blocked(url):
            return ToolResult(
                success=False,
                error=f"Blocked host (TikTok is banned in India): {url}",
            )
        return self._download_one(url, inputs, start)

    def _execute_batch(self, urls: list[str], inputs: dict[str, Any], start: float) -> ToolResult:
        """Batch mode (additive): per-item SKIPPED with reason, never aborts."""
        items: list[dict[str, Any]] = []
        artifacts: list[str] = []
        for url in urls:
            if _is_blocked(url):
                items.append({"url": url, "status": "SKIPPED",
                              "reason": "Blocked host (TikTok is banned in India)"})
                continue
            try:
                result = self._download_one(url, inputs, time.time())
            except Exception as exc:  # never abort the batch
                result = ToolResult(success=False, error=f"SKIPPED: {exc}")
            if result.success:
                items.append({"url": url, "status": "downloaded",
                              **result.data})
                artifacts.extend(result.artifacts)
            else:
                items.append({"url": url, "status": "SKIPPED",
                              "reason": result.error or "unknown"})
        downloaded = sum(1 for i in items if i["status"] == "downloaded")
        skipped = len(items) - downloaded
        report = [f"{i['status']}: {i['url']}" + (f" ({i.get('reason', '')})" if i["status"] == "SKIPPED" else "")
                  for i in items]
        return ToolResult(
            success=downloaded > 0,
            data={"items": items, "downloaded": downloaded, "skipped": skipped,
                  "report": report},
            artifacts=artifacts,
            duration_seconds=round(time.time() - start, 2),
            error=None if downloaded > 0 else "all items SKIPPED: " + "; ".join(report),
        )

    def _format(self, height: int) -> str:
        return f"bv*[height<={height}]+ba/b[height<={height}]/b/best"

    def _redact_proxy(self, proxy: str | None) -> str:
        """Proxy host for logs — never leak user:pass credentials."""
        if not proxy:
            return "direct (GitHub runner IP)"
        try:
            parsed = urllib.parse.urlparse(proxy)
            host = parsed.hostname or "proxy"
            port = f":{parsed.port}" if parsed.port else ""
            return f"{host}{port}"
        except Exception:
            return "proxy"

    def _download_one(self, url: str, inputs: dict[str, Any], start: float) -> ToolResult:
        output_dir = Path(inputs.get("output_dir") or "assets/raw")
        output_dir.mkdir(parents=True, exist_ok=True)
        height = {"360p": 360, "480p": 480, "720p": 720, "1080p": 1080}.get(
            str(inputs.get("max_resolution", "720p")), 720
        )
        # Your-own-IP routing: only YouTube/search traffic uses the proxy.
        proxy = _resolve_proxy(inputs.get("proxy"))
        cookies_file = _resolve_cookies_file(inputs.get("cookies_file"))
        cookies_browser = _resolve_cookies_from_browser(inputs.get("cookies_from_browser"))
        route = self._redact_proxy(proxy)

        try:
            import yt_dlp  # type: ignore
        except ImportError:
            yt_dlp = None  # type: ignore

        if yt_dlp is not None:
            template = str(output_dir / "%(id)s.%(ext)s")
            try:
                return self._download_via_package(
                    yt_dlp, url, height, template, start,
                    proxy=proxy, cookies_file=cookies_file,
                    cookies_browser=cookies_browser, route=route)
            except Exception as exc:
                if not _looks_like_block(exc):
                    return ToolResult(success=False, error=f"yt-dlp download failed: {exc}")
                last: Exception = exc
                for attempt, client in enumerate(PLAYER_CLIENTS):
                    time.sleep(_client_backoff(attempt))
                    try:
                        return self._download_via_package(
                            yt_dlp, url, height, template, start, player_client=client,
                            proxy=proxy, cookies_file=cookies_file,
                            cookies_browser=cookies_browser, route=route)
                    except Exception as retry_exc:
                        last = retry_exc
                        if not _looks_like_block(retry_exc):
                            break
                tried = ", ".join(PLAYER_CLIENTS)
                hint = "" if proxy else (
                    "; IP-blocked on GitHub Actions? Set YTDLP_PROXY_URL to route "
                    "YouTube via your own IP")
                return ToolResult(
                    success=False,
                    error=f"SKIPPED: yt-dlp blocked for {url} ({last}); "
                          f"route={route}; clients tried: {tried}{hint}",
                )

        binary = shutil.which("yt-dlp")
        if not binary:
            return ToolResult(success=False, error=self.install_instructions)
        out_template = str(output_dir / "%(id)s.%(ext)s")
        attempts = [None, *PLAYER_CLIENTS]
        last_err = ""
        for attempt, client in enumerate(attempts):
            if attempt > 0:
                time.sleep(_client_backoff(attempt - 1))
            try:
                self._download_via_binary(binary, url, height, out_template, client,
                                          proxy=proxy, cookies_file=cookies_file,
                                          cookies_browser=cookies_browser)
                break
            except Exception as exc:
                last_err = str(exc)
                if not _looks_like_block(exc):
                    return ToolResult(success=False, error=f"yt-dlp binary failed: {exc}")
        else:
            hint = "" if proxy else (
                "; IP-blocked on GitHub Actions? Set YTDLP_PROXY_URL to route "
                "YouTube via your own IP")
            return ToolResult(
                success=False,
                error=f"SKIPPED: yt-dlp binary blocked for {url} ({last_err}); "
                      f"route={route}{hint}",
            )
        files = sorted(output_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return ToolResult(success=False, error="yt-dlp finished but no file appeared")
        return ToolResult(
            success=True,
            data={"video_path": str(files[0]), "source_url": url, "route": route},
            artifacts=[str(files[0])],
            duration_seconds=round(time.time() - start, 2),
        )

    def _download_via_package(self, yt_dlp: Any, url: str, height: int,
                              template: str, start: float,
                              player_client: str | None = None,
                              proxy: str | None = None,
                              cookies_file: str | None = None,
                              cookies_browser: str | None = None,
                              route: str = "") -> ToolResult:
        opts: dict[str, Any] = {
            "format": self._format(height),
            "outtmpl": template,
            "quiet": True,
            "no_warnings": True,
        }
        if proxy:
            opts["proxy"] = proxy
        if cookies_file:
            opts["cookiefile"] = cookies_file
        if cookies_browser:
            opts["cookiesfrombrowser"] = (cookies_browser,)
        if player_client:
            opts["extractor_args"] = {"youtube": {"player_client": [player_client]}}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info))
        return ToolResult(
            success=True,
            data={
                "video_path": str(path),
                "title": (info or {}).get("title", ""),
                "duration": (info or {}).get("duration"),
                "uploader": (info or {}).get("uploader", ""),
                "source_url": url,
                "route": route or self._redact_proxy(proxy),
                **({"player_client": player_client} if player_client else {}),
            },
            artifacts=[str(path)],
            duration_seconds=round(time.time() - start, 2),
        )

    def _download_via_binary(self, binary: str, url: str, height: int,
                             out_template: str, player_client: str | None,
                             proxy: str | None = None,
                             cookies_file: str | None = None,
                             cookies_browser: str | None = None) -> None:
        args = [
            binary, "-f", self._format(height),
            "-o", out_template, "--print", "after_move:filepath",
        ]
        if proxy:
            args += ["--proxy", proxy]
        if cookies_file:
            args += ["--cookies", cookies_file]
        if cookies_browser:
            args += ["--cookies-from-browser", cookies_browser]
        if player_client:
            args += ["--extractor-args", f"youtube:player_client={player_client}"]
        args.append(url)
        self.run_command(args)
