"""Unit tests for the yt-dlp player-client retry matrix (mocked, no network).

Covers: 403 -> retry across player clients with jittered backoff; persistent
blocks -> SKIPPED with reason; non-block errors -> immediate fail (no retry);
batch mode marks SKIPPED per item and never aborts; TikTok stays blocklisted.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.video.ytdlp_downloader import (
    PLAYER_CLIENTS,
    YtdlpDownloader,
    _client_backoff,
    _looks_like_block,
)


class BlockError(Exception):
    pass


def _install_fake_yt_dlp(monkeypatch, script):
    """Install a stub yt_dlp module whose extract_info follows `script`.

    script: list where each entry is either an Exception to raise or a dict
    info payload to return. Records every YoutubeDL opts dict in `seen`.
    """
    seen: list[dict] = []
    calls = {"n": 0}

    class FakeYDL:
        def __init__(self, opts):
            seen.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True):
            idx = calls["n"]
            calls["n"] += 1
            action = script[min(idx, len(script) - 1)]
            if isinstance(action, Exception):
                raise action
            return action

        def prepare_filename(self, info):
            return f"/tmp/fake_{info.get('id', 'x')}.mp4"

    module = types.ModuleType("yt_dlp")
    module.YoutubeDL = FakeYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    return seen, calls


def _block(msg="HTTP Error 403: Forbidden"):
    return BlockError(msg)


def test_block_classifier() -> None:
    assert _looks_like_block(BlockError("HTTP Error 403: Forbidden"))
    assert _looks_like_block(BlockError("HTTP Error 429: Too Many Requests"))
    assert _looks_like_block(RuntimeError("rate-limit exceeded"))
    assert not _looks_like_block(ValueError("boom"))
    assert not _looks_like_block(RuntimeError("network unreachable"))


def test_backoff_in_jitter_window(monkeypatch) -> None:
    monkeypatch.setattr("tools.video.ytdlp_downloader.random.uniform",
                        lambda a, b: 5.0)
    assert _client_backoff(0) == 5.0
    assert _client_backoff(2) == 7.0


def test_403_retries_with_player_client_rotation(tmp_path, monkeypatch) -> None:
    seen, _ = _install_fake_yt_dlp(monkeypatch, [
        _block(), _block(),
        {"id": "ok1", "title": "t", "duration": 10, "uploader": "u"},
    ])
    monkeypatch.setattr("tools.video.ytdlp_downloader.random.uniform",
                        lambda a, b: 3.0)
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    tool = YtdlpDownloader()
    result = tool.execute({"url": "https://youtube.com/watch?v=ok1",
                           "output_dir": str(tmp_path)})
    assert result.success
    assert result.data["player_client"] == "android"  # 2nd client after 2 blocks
    clients = [o.get("extractor_args", {}).get("youtube", {}).get("player_client", [None])[0]
               for o in seen]
    assert clients[0] is None  # plain attempt first
    assert clients[1] == PLAYER_CLIENTS[0] == "web_safari"
    assert clients[2] == PLAYER_CLIENTS[1] == "android"
    assert len(sleeps) == 2 and all(3.0 <= s <= 9.0 for s in sleeps)


def test_persistent_block_marks_skipped(tmp_path, monkeypatch) -> None:
    seen, _ = _install_fake_yt_dlp(monkeypatch, [_block()])
    monkeypatch.setattr("tools.video.ytdlp_downloader.random.uniform",
                        lambda a, b: 3.0)
    monkeypatch.setattr("time.sleep", lambda s: None)

    tool = YtdlpDownloader()
    result = tool.execute({"url": "https://youtube.com/watch?v=nope",
                           "output_dir": str(tmp_path)})
    assert not result.success
    assert "SKIPPED" in (result.error or "")
    assert "web_safari" in (result.error or "")  # clients-tried recorded
    assert len(seen) == 1 + len(PLAYER_CLIENTS)  # plain + full matrix


def test_non_block_error_fails_immediately(tmp_path, monkeypatch) -> None:
    seen, _ = _install_fake_yt_dlp(monkeypatch, [ValueError("boom")])
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    tool = YtdlpDownloader()
    result = tool.execute({"url": "https://youtube.com/watch?v=bad",
                           "output_dir": str(tmp_path)})
    assert not result.success
    assert "boom" in (result.error or "")
    assert sleeps == [] and len(seen) == 1  # zero retries


def test_batch_continues_past_skipped(tmp_path, monkeypatch) -> None:
    class RouterYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True):
            if "always403" in url:
                raise BlockError("HTTP Error 403: Forbidden")
            return {"id": "good", "title": "t", "duration": 5, "uploader": "u"}

        def prepare_filename(self, info):
            return "/tmp/fake_good.mp4"

    module = types.ModuleType("yt_dlp")
    module.YoutubeDL = RouterYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    monkeypatch.setattr("tools.video.ytdlp_downloader.random.uniform",
                        lambda a, b: 3.0)
    monkeypatch.setattr("time.sleep", lambda s: None)

    tool = YtdlpDownloader()
    result = tool.execute({
        "url": "https://youtube.com/watch?v=good",
        "urls": ["https://youtube.com/watch?v=always403",
                 "https://youtube.com/watch?v=good"],
        "output_dir": str(tmp_path),
    })
    assert result.success  # batch never aborts while one item downloads
    assert result.data["downloaded"] == 1
    assert result.data["skipped"] == 1
    assert result.data["items"][0]["status"] == "SKIPPED"
    assert result.data["items"][1]["status"] == "downloaded"
    assert len(result.data["report"]) == 2


def test_tiktok_still_blocklisted(tmp_path) -> None:
    tool = YtdlpDownloader()
    result = tool.execute({"url": "https://www.tiktok.com/@x/video/123",
                           "output_dir": str(tmp_path)})
    assert not result.success
    assert "Blocked host" in (result.error or "")


def test_proxy_from_env_reaches_yt_dlp_opts(tmp_path, monkeypatch) -> None:
    for var in ("YTDLP_PROXY_URL", "HTTPS_PROXY", "HTTP_PROXY",
                "https_proxy", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    seen, _ = _install_fake_yt_dlp(monkeypatch, [
        {"id": "px1", "title": "t", "duration": 10, "uploader": "u"},
    ])
    monkeypatch.setattr("tools.video.ytdlp_downloader.random.uniform",
                        lambda a, b: 3.0)
    monkeypatch.setenv("YTDLP_PROXY_URL", "http://user:pass@home-ip:3128")
    tool = YtdlpDownloader()
    result = tool.execute({"url": "https://youtube.com/watch?v=px1",
                           "output_dir": str(tmp_path)})
    assert result.success
    assert seen[0].get("proxy") == "http://user:pass@home-ip:3128"
    # logs must carry the route but the result data must not leak creds
    assert result.data["route"] == "home-ip:3128"
    assert "pass" not in result.data["route"]


def test_explicit_proxy_beats_env_and_blank_means_direct(tmp_path, monkeypatch) -> None:
    for var in ("YTDLP_PROXY_URL", "HTTPS_PROXY", "HTTP_PROXY",
                "https_proxy", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    seen, _ = _install_fake_yt_dlp(monkeypatch, [
        {"id": "a", "title": "t", "duration": 5, "uploader": "u"},
        {"id": "b", "title": "t", "duration": 5, "uploader": "u"},
    ])
    monkeypatch.setattr("tools.video.ytdlp_downloader.random.uniform",
                        lambda a, b: 3.0)
    monkeypatch.setenv("YTDLP_PROXY_URL", "http://env:env@env-host:3128")
    tool = YtdlpDownloader()
    first = tool.execute({"url": "https://youtube.com/watch?v=a",
                          "output_dir": str(tmp_path),
                          "proxy": "http://me:secret@mine:8888"})
    assert first.success and seen[0].get("proxy") == "http://me:secret@mine:8888"
    monkeypatch.delenv("YTDLP_PROXY_URL")
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    second = tool.execute({"url": "https://youtube.com/watch?v=b",
                           "output_dir": str(tmp_path)})
    assert second.success and "proxy" not in seen[1]


def test_cookies_file_flows_into_opts(tmp_path, monkeypatch) -> None:
    for var in ("YTDLP_PROXY_URL", "YTDLP_COOKIES_FILE"):
        monkeypatch.delenv(var, raising=False)
    seen, _ = _install_fake_yt_dlp(monkeypatch, [
        {"id": "c", "title": "t", "duration": 5, "uploader": "u"},
    ])
    monkeypatch.setattr("tools.video.ytdlp_downloader.random.uniform",
                        lambda a, b: 3.0)
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n")
    tool = YtdlpDownloader()
    result = tool.execute({"url": "https://youtube.com/watch?v=c",
                           "output_dir": str(tmp_path),
                           "cookies_file": str(jar)})
    assert result.success
    assert seen[0].get("cookiefile") == str(jar)


def test_block_error_without_proxy_points_at_proxy_fix(tmp_path, monkeypatch) -> None:
    for var in ("YTDLP_PROXY_URL", "HTTPS_PROXY", "HTTP_PROXY",
                "https_proxy", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    _install_fake_yt_dlp(monkeypatch, [_block()])
    monkeypatch.setattr("tools.video.ytdlp_downloader.random.uniform",
                        lambda a, b: 3.0)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.delenv("YTDLP_PROXY_URL", raising=False)
    tool = YtdlpDownloader()
    result = tool.execute({"url": "https://youtube.com/watch?v=nope",
                           "output_dir": str(tmp_path)})
    assert not result.success
    assert "YTDLP_PROXY_URL" in (result.error or "")
    assert "route=direct" in (result.error or "")
