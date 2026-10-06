"""Tests for the real-footage contract (no placeholder substitution).

Regression cover for the bug where CI substituted an ffmpeg `testsrc2`
colour-bar pattern for every reel and the run still reported success:

  * scripts/footage_ladder.py  — search queries come from the job's own
    headline, and a job with no acquired footage FAILS the run.
  * scripts/render_reels_ci.py — `stage` refuses a non-live footage report.

No network: yt_dlp, YtdlpDownloader, and FootageSelector are all mocked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import footage_ladder  # noqa: E402
import render_reels_ci  # noqa: E402


BREAKING_JOB = {
    "job_id": "20261002-breaking-delhi-hc-yamuna",
    "style": "breaking",
    "source_item": {
        "title": "Delhi HC orders status report on Yamuna flood-plain encroachments",
        "summary": "The court directed authorities to file a report.",
        "outlet": "Example News",
    },
}

TRENDING_JOB = {
    "job_id": "20261002-trending-mumbai-dancing-dog",
    "style": "trending",
    "source_item": {
        "title": "Mumbai station 'dancing dog' video crosses 2 million views",
        "summary": "A viral video crossed two million views.",
        "outlet": "Example News",
    },
}


class TestQueriesComeFromTheHeadline:
    def test_keywords_drop_generic_news_words(self):
        keys = footage_ladder.headline_keywords(
            "Delhi HC orders status report on the Yamuna flood plain")
        assert "Yamuna" in keys
        assert "the" not in keys
        assert "on" not in keys

    def test_breaking_query_uses_this_jobs_headline_not_a_fixture(self):
        queries = footage_ladder.build_queries(BREAKING_JOB)
        assert "Yamuna" in queries[0]
        # the old hardcoded fixture queries must not reappear
        assert not any("dancing dog" in q for q in queries)

    def test_outlet_adds_a_second_query(self):
        job = {"job_id": "x", "style": "trending",
               "source_item": {"title": "Mumbai station dancing dog video crosses 2 million views",
                               "outlet": "NDTV"}}
        queries = footage_ladder.build_queries(job)
        assert any(q.startswith("NDTV") for q in queries)

    def test_placeholder_outlet_is_skipped(self):
        # Fixture outlets like "Example News" can never match YouTube — the
        # regression that made every fixture run search for a fake outlet.
        queries = footage_ladder.build_queries(TRENDING_JOB)
        assert not any("Example News" in q for q in queries)

    def test_long_headline_gets_graduated_fallback_queries(self):
        # 12-word fixture headline: full phrase is unmatchable, but the
        # short/minimal fallbacks keep THIS story's leading keywords.
        queries = footage_ladder.build_queries(TRENDING_JOB)
        assert len(queries) >= 3
        assert queries[0].endswith("viral video")
        assert "Mumbai station dancing" in queries[-1]

    def test_headline_with_no_usable_words_yields_no_queries(self):
        job = {"job_id": "x", "style": "breaking",
               "source_item": {"title": "the of and"}}
        assert footage_ladder.build_queries(job) == []


class TestNoFootageMeansFailure:
    def test_missing_import_fails_loudly(self, tmp_path, monkeypatch):
        """yt-dlp absent must raise with install guidance, not fall back."""
        monkeypatch.setitem(sys.modules, "yt_dlp", None)
        with pytest.raises(RuntimeError, match="yt-dlp is not importable"):
            footage_ladder.resolve_watch_urls(["anything"])

    def test_entry_with_no_clips_is_marked_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            footage_ladder, "build_queries", lambda job: ["query"])
        monkeypatch.setattr(
            footage_ladder, "resolve_watch_urls", lambda q, **k: [])
        entry = footage_ladder.acquire_clips(BREAKING_JOB, tmp_path)
        assert entry["footage_source"] == "missing"
        assert entry["clips"] == []
        assert entry["ladder"]

    def test_all_downloads_skipped_is_marked_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(footage_ladder, "build_queries", lambda job: ["q"])
        monkeypatch.setattr(
            footage_ladder, "resolve_watch_urls", lambda q, **k: ["u"])
        skipped = mock.Mock()
        skipped.success = False
        skipped.data = {"items": [{"url": "u", "status": "SKIPPED",
                                   "reason": "blocked"}],
                        "report": ["SKIPPED: u (blocked)"]}
        monkeypatch.setattr(footage_ladder, "YtdlpDownloader",
                            mock.Mock(return_value=mock.Mock(execute=lambda i: skipped)))
        entry = footage_ladder.acquire_clips(BREAKING_JOB, tmp_path)
        assert entry["footage_source"] == "missing"
        assert entry["clips"] == []

    def test_main_exits_nonzero_when_any_job_has_no_footage(self, tmp_path):
        routes = tmp_path / "routes.json"
        routes.write_text(json.dumps({"jobs": [BREAKING_JOB, TRENDING_JOB]}),
                          encoding="utf-8")
        out = tmp_path / "out"
        out.mkdir()
        monkey = mock.Mock(return_value={
            "job_id": BREAKING_JOB["job_id"], "style": "breaking",
            "footage_source": "missing", "clips": [], "ladder": ["nope"]})
        with mock.patch.object(footage_ladder, "acquire_clips", monkey):
            code = footage_ladder.main(
                ["--routes", str(routes), "--out", str(out)])
        assert code == 1
        # the report is still written so the failure can be diagnosed
        report = json.loads((out / "footage_report.json").read_text(encoding="utf-8"))
        assert report[BREAKING_JOB["job_id"]]["footage_source"] == "missing"

    def test_download_block_hint_names_proxy_not_queries(
            self, tmp_path, capsys):
        """SKIPPED ladders mean IP block: hint must say proxy, not coverage."""
        routes = tmp_path / "routes.json"
        routes.write_text(json.dumps({"mode": "fixture", "jobs": [BREAKING_JOB]}),
                          encoding="utf-8")
        out = tmp_path / "out"
        out.mkdir()
        blocked = {
            "style": "breaking", "footage_source": "missing", "clips": [],
            "ladder": ["SKIPPED: https://www.youtube.com/watch?v=x "
                       "(Sign in to confirm you're not a bot); "
                       "route=direct (GitHub runner IP); "
                       "clients tried: web_safari, android, ios, mweb"]}

        with mock.patch.object(footage_ladder, "acquire_clips",
                               mock.Mock(return_value=blocked)):
            code = footage_ladder.main(
                ["--routes", str(routes), "--out", str(out)])
        assert code == 1
        err = capsys.readouterr().err
        assert "YTDLP_PROXY_URL" in err
        assert "IP block" in err

    def test_search_miss_hint_names_queries_not_proxy(self, tmp_path, capsys):
        """Empty ladders mean coverage miss: hint must say queries."""
        routes = tmp_path / "routes.json"
        routes.write_text(json.dumps({"mode": "fixture", "jobs": [BREAKING_JOB]}),
                          encoding="utf-8")
        out = tmp_path / "out"
        out.mkdir()
        no_results = {
            "style": "breaking", "footage_source": "missing", "clips": [],
            "ladder": ["search returned no results for ['q1', 'q2'] "
                       "(tried 2 graduated querie(s))"]}
        with mock.patch.object(footage_ladder, "acquire_clips",
                               mock.Mock(return_value=no_results)):
            code = footage_ladder.main(
                ["--routes", str(routes), "--out", str(out)])
        assert code == 1
        err = capsys.readouterr().err
        assert "graduated" in err
        assert "IP block" not in err

    def test_main_exits_zero_when_all_jobs_get_live_footage(self, tmp_path):
        routes = tmp_path / "routes.json"
        routes.write_text(json.dumps({"jobs": [BREAKING_JOB]}),
                          encoding="utf-8")
        out = tmp_path / "out"
        out.mkdir()
        clip = out / "breaking_clip.mp4"
        clip.write_bytes(b"x")
        with mock.patch.object(footage_ladder, "acquire_clips", mock.Mock(
            return_value={"style": "breaking", "footage_source": "live",
                          "clips": [str(clip)], "ladder": []})), \
             mock.patch.object(footage_ladder, "_probe_seconds", lambda p: 18.0):
            code = footage_ladder.main(
                ["--routes", str(routes), "--out", str(out)])
        assert code == 0
        report = json.loads((out / "footage_report.json").read_text(encoding="utf-8"))
        assert report[BREAKING_JOB["job_id"]]["footage_source"] == "live"


class TestShortBreakingClipIsRejected:
    def test_clip_shorter_than_the_reel_is_reported(self, tmp_path, monkeypatch):
        clip = tmp_path / "short.mp4"
        clip.write_bytes(b"x")
        monkeypatch.setattr(footage_ladder, "_probe_seconds", lambda p: 6.0)
        reason = footage_ladder.check_breaking_duration(
            "breaking", [str(clip)], {"duration_in_frames": 540})
        assert reason and "frozen frame" in reason

    def test_full_length_clip_passes(self, tmp_path, monkeypatch):
        clip = tmp_path / "ok.mp4"
        clip.write_bytes(b"x")
        monkeypatch.setattr(footage_ladder, "_probe_seconds", lambda p: 18.0)
        assert footage_ladder.check_breaking_duration(
            "breaking", [str(clip)], {"duration_in_frames": 540}) is None

    def test_trending_multi_clip_is_not_length_checked(self, tmp_path, monkeypatch):
        monkey = mock.Mock()
        monkeypatch.setattr(footage_ladder, "_probe_seconds", monkey)
        assert footage_ladder.check_breaking_duration(
            "trending", ["a.mp4", "b.mp4"], {"duration_in_frames": 480}) is None
        assert not monkey.called

    def test_short_clip_makes_the_run_fail_and_still_writes_the_report(
            self, tmp_path, monkeypatch):
        routes = tmp_path / "routes.json"
        routes.write_text(json.dumps({"jobs": [BREAKING_JOB]}), encoding="utf-8")
        out = tmp_path / "out"
        out.mkdir()
        clip = out / "short.mp4"
        clip.write_bytes(b"x")
        with mock.patch.object(footage_ladder, "acquire_clips", mock.Mock(
            return_value={"style": "breaking", "footage_source": "live",
                          "clips": [str(clip)], "ladder": []})), \
             mock.patch.object(footage_ladder, "_probe_seconds", lambda p: 6.0):
            code = footage_ladder.main(["--routes", str(routes), "--out", str(out)])
        assert code == 1
        # the diagnostic report must survive the failure
        report = json.loads((out / "footage_report.json").read_text(encoding="utf-8"))
        entry = report[BREAKING_JOB["job_id"]]
        assert entry["footage_source"] == "missing"
        assert any("frozen frame" in line for line in entry["ladder"])


class TestStageRefusesPlaceholderFootage:
    def test_placeholder_source_is_rejected(self):
        with pytest.raises(ValueError, match="not 'live'"):
            render_reels_ci.assert_real_footage("job-1", {
                "footage_source": "placeholder",
                "clips": ["out/daily/footage/placeholder_breaking.mp4"],
            })

    def test_missing_source_key_is_rejected(self):
        with pytest.raises(ValueError, match="not 'live'"):
            render_reels_ci.assert_real_footage("job-1", {"clips": ["a.mp4"]})

    def test_empty_clip_list_is_rejected(self):
        with pytest.raises(ValueError, match="clips"):
            render_reels_ci.assert_real_footage("job-1", {
                "footage_source": "live", "clips": []})

    def test_placeholder_filename_under_live_source_is_rejected(self):
        with pytest.raises(ValueError, match="placeholder"):
            render_reels_ci.assert_real_footage("job-1", {
                "footage_source": "live",
                "clips": ["out/daily/footage/placeholder_breaking.mp4"],
            })

    def test_real_footage_is_returned(self):
        clips = ["out/daily/selected_breaking/breaking_abc.mp4"]
        assert render_reels_ci.assert_real_footage(
            "job-1", {"footage_source": "live", "clips": clips}) == clips


class TestSummaryTrimming:
    def test_short_summary_untouched(self):
        assert render_reels_ci.trim_summary("Short summary") == "Short summary"

    def test_long_summary_trims_on_a_word_boundary(self):
        text = "word " * 60
        out = render_reels_ci.trim_summary(text)
        assert len(out) <= render_reels_ci.MAX_SUBHEAD_CHARS + 1
        assert not out.endswith("wor…")
        assert out.endswith("…")