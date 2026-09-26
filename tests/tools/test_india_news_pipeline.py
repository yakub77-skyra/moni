"""Contracts for the zero-cost India daily-news source and render runner."""

from __future__ import annotations

import io
import json
import sys
import wave
from pathlib import Path
from unittest import mock

from scripts.india_daily_video import (
    FALLBACK_VISUAL_QUERY,
    _image_suffix,
    _media_duration,
    _run_assets,
    assemble_narration,
    build_render_props,
    card_timings,
    download_publisher_image,
    geojson_to_svg_paths,
    narrate_with_sapi,
    normalize_for_tts,
    sanitize_visual_query,
    sapi_powershell_script,
)
from tools.news.india_news_scraper import _is_india_related
from tools.news.openrouter_scriptwriter import OpenRouterScriptwriter


def test_foreign_incident_is_not_treated_as_india_news() -> None:
    assert not _is_india_related(
        "Hurricane Polo threatens Mexico's Pacific coast after strengthening",
        "The Category 5 storm is approaching Mexico.",
    )
    assert _is_india_related(
        "Indian rowers secure bronze at the Asian Games",
        "The Indian team won a medal in China.",
    )


def test_india_filter_rejects_foreign_only_even_from_indian_outlet() -> None:
    # Publisher origin is not an input: a foreign-centered incident carried
    # by an Indian outlet must still be rejected on its own text.
    assert not _is_india_related(
        "Pakistan election commission announces provincial poll dates",
        "Voting will be held across Punjab province next month.",
    )
    assert not _is_india_related(
        "Typhoon lashes Japan's southern islands, flights cancelled",
        "Thousands evacuated in Okinawa as the storm makes landfall.",
    )


def test_india_filter_accepts_domestic_story_without_place_name() -> None:
    # The single live filter must accept domestic copy that names no place:
    # the feeds polled are Indian outlets, so absence of foreign signals
    # means a domestic story. (The removed shadow definition required an
    # explicit India term and dropped stories like this one.)
    assert _is_india_related(
        "Sensex rallies 500 points on strong bank earnings",
        "Markets closed higher for the third straight session.",
    )


def test_india_filter_accepts_genuine_india_connection() -> None:
    assert _is_india_related(
        "India sends relief supplies after Nepal earthquake",
        "Indian Air Force aircraft carried tents and medicine to Kathmandu.",
    )


def test_india_filter_ignores_publisher_masthead_in_headline() -> None:
    # Hindustan Times appends "| HT India" to every RSS headline. That tag
    # supplies the word "india" to all of their items, which used to satisfy
    # the India-context check and let foreign stories through the filter.
    nepal = "Bagmati River Floods Low-Lying Areas As Landslides Hit Key Nepal Routes | HT India"
    assert not _is_india_related(nepal, "Landslides hit key routes in Nepal.")
    # A genuinely Indian story from the same outlet must still pass.
    assert _is_india_related(
        "Delhi High Court orders removal of deepfake images | HT India",
        "The court issued notices to social media platforms.",
    )
    # A long trailing clause is real headline text, not a masthead: keep it.
    assert _is_india_related(
        "Supreme Court seeks Centre response on plea filed by four petitioners",
        "The court asked the government to respond within four weeks.",
    )


def test_runners_force_utf8_console() -> None:
    # The CI runner is a cp1252 Windows console; valid news copy (U+2011,
    # curly quotes, em dash) crashed the runner's own print statements.
    from scripts import india_daily_roundup, india_daily_video

    for module in (india_daily_roundup, india_daily_video):
        assert hasattr(module, "_force_utf8_console")
        buffer = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        with mock.patch.object(module.sys, "stdout", buffer):
            module._force_utf8_console()
        assert buffer.encoding.lower().replace("-", "") == "utf8"
        # And the payload it exists for must encode cleanly.
        buffer.reconfigure(encoding="utf-8", errors="replace")
        buffer.write("Rupee \u20b9 \u2011 \u201cquoted\u201d \u2014 \u2026")
        buffer.flush()


def test_clip_duration_probe_never_fails_the_run(tmp_path: Path) -> None:
    # clip_duration is informational provenance that nothing downstream reads,
    # so a missing ffprobe must yield None rather than raise. (Real stock
    # clips are fragmented MP4, so hand-rolling a duration parser here would
    # be a large, fragile addition for a field nobody consumes.)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"not really an mp4")

    with mock.patch("scripts.india_daily_video._ffprobe", return_value=None):
        assert _media_duration(clip) is None
    with mock.patch("scripts.india_daily_video._ffprobe", return_value=None):
        # and the probe helper itself is importable/removed
        assert not hasattr(sys.modules["scripts.india_daily_video"], "_mp4_duration")


def test_assemble_narration_works_without_ffmpeg(tmp_path: Path) -> None:
    first = tmp_path / "audio-01.wav"
    second = tmp_path / "audio-02.wav"
    _write_silence_wav(first, 2.0)
    _write_silence_wav(second, 3.0)
    output = tmp_path / "narration.wav"

    with mock.patch("scripts.india_daily_video._ffmpeg", return_value=None):
        assembly = assemble_narration([first, second], output)

    assert assembly["method"] == "python-wave"
    assert [s["duration"] for s in assembly["segments"]] == [2.0, 3.0]
    assert assembly["segments"][1]["start"] == 0.25 + 2.0 + 0.45
    assert abs(assembly["duration"] - (0.25 + 2.0 + 0.45 + 3.0 + 0.25)) <= 0.02


def test_normalize_for_tts_folds_typographic_characters() -> None:
    # Unfolded, SAPI silently drops words: a non-breaking hyphen reads as
    # nothing at all.
    folded = normalize_for_tts(
        "Floods \u2011 hit routes \u2014 the \u201cnext\u201d day \u20b93"
    )
    for char in "\u2011\u2014\u201c\u201d\u20b9":
        assert char not in folded
    assert " - " in folded
    assert "rupees" in folded


def test_sapi_falls_back_when_preferred_voice_is_absent() -> None:
    recorded: dict[str, str] = {}

    def fake_run(command: list[str], env: dict[str, str]) -> object:
        recorded.update(env)
        with wave.open(env["INDIA_NEWS_OUTPUT"], "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00" * 800)

        class Result:
            stdout = "SAPI_VOICE=Microsoft David Desktop"

        return Result()

    meta = narrate_with_sapi("Floods \u2011 hit routes", Path("unused"), run=fake_run)

    # The PowerShell must try a fallback voice, not trust SelectVoice blindly.
    script = sapi_powershell_script()
    assert "GetInstalledVoices" in script
    assert "try { $s.SelectVoice($name) } catch" in script
    assert meta["voice"] == "Microsoft David Desktop"


def test_missing_stock_clip_degrades_instead_of_failing(
    tmp_path: Path,
) -> None:
    # A narrow story query with no portrait HD match must not lose the episode.
    from tools.base_tool import ToolResult

    calls: list[str] = []

    class FakePexels:
        def execute(self, params):
            calls.append(params["query"])
            return ToolResult(success=False, error="no results")

    cards = [{
        "rank": 1, "state": "Kerala", "outlet": "X", "headline": "h",
        "narration": "n", "source_url": "https://e/1",
        "visual_query": "Kerala something very specific",
    }]
    cards_path = tmp_path / "cards.json"
    cards_path.write_text(
        json.dumps({"cards": cards}), encoding="utf-8"
    )

    class FakeRegistry:
        def get(self, name):
            assert name == "pexels_video"
            return FakePexels()

    with mock.patch("scripts.india_daily_video.narrate_with_sapi") as narrate:
        def fake_narrate(text, path, run=None):
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(b"\x00\x00" * 16000)
            return {"provider": "fake", "voice": "fake", "cost_usd": 0.0}

        narrate.side_effect = fake_narrate

        def fake_download(path):
            # A real minimal FeatureCollection, so the projection step runs.
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({
                    "type": "FeatureCollection",
                    "features": [{
                        "type": "Feature",
                        "properties": {"NAME_1": "Kerala"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[
                                [74.8, 8.1], [77.5, 8.1], [77.5, 12.9],
                                [74.8, 12.9], [74.8, 8.1],
                            ]],
                        },
                    }],
                }),
                encoding="utf-8",
            )

        with mock.patch(
            "scripts.india_daily_video._download_map", side_effect=fake_download
        ):
            props_path = _run_assets(cards_path, tmp_path, FakeRegistry())

    props = json.loads(props_path.read_text(encoding="utf-8"))
    # One retry with the generic query, then a clip-less card -- not an raise.
    assert calls == [
        sanitize_visual_query("Kerala something very specific"),
        FALLBACK_VISUAL_QUERY,
    ]
    assert props["cards"][0]["clip_src"] == ""
    assert props["cards"][0]["visual_license"] == "none"
    assert props["cards"][0]["visual_missing_reason"]
    assert props["durationInFrames"] > 0


def test_image_format_detected_by_magic_not_content_type() -> None:
    # Hindustan Times serves a valid PNG as application/octet-stream, and
    # NDTV serves WebP, so a Content-Type check would drop real images.
    assert _image_suffix(b"\xff\xd8\xff\xe0" + b"\x00" * 32) == ".jpg"
    assert _image_suffix(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32) == ".png"
    assert _image_suffix(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ".webp"
    assert _image_suffix(b"GIF89a" + b"\x00" * 32) == ".gif"
    # A RIFF container that is not WebP must not be accepted as an image.
    assert _image_suffix(b"RIFF\x00\x00\x00\x00WAVEfmt ") is None
    assert _image_suffix(b"<html>not an image at all</html>") is None


def test_publisher_image_download_writes_detected_extension(
    tmp_path: Path,
) -> None:
    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2048
    captured: dict = {}

    class FakeResponse:
        headers: dict = {}

        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        captured["headers"] = dict(request.headers)
        return FakeResponse()

    with mock.patch("urllib.request.urlopen", fake_urlopen):
        name = download_publisher_image(
            "https://x.test/a.png", tmp_path / "photo-01",
            referer="https://x.test/story",
        )

    assert name == "photo-01.png"
    assert (tmp_path / "photo-01.png").read_bytes() == payload
    # NDTV 403s without the Sec-Fetch-* set a real <img> sends.
    assert captured["headers"].get("Sec-fetch-dest") == "image"


def test_publisher_image_download_returns_none_on_failure(
    tmp_path: Path,
) -> None:
    def boom(request, timeout=None):
        raise OSError("HTTP Error 403: Forbidden")

    with mock.patch("urllib.request.urlopen", boom):
        assert download_publisher_image(
            "https://x.test/a.jpg", tmp_path / "photo-02"
        ) is None
    # Nothing half-written left behind.
    assert list(tmp_path.iterdir()) == []


def _fake_narration(text, path, run=None):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 16000)
    return {"provider": "fake", "voice": "fake", "cost_usd": 0.0}


def _fake_map_download(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"NAME_1": "Kerala"},
            "geometry": {"type": "Polygon", "coordinates": [[
                [74.8, 8.1], [77.5, 8.1], [77.5, 12.9], [74.8, 12.9], [74.8, 8.1],
            ]]},
        }],
    }), encoding="utf-8")


def test_publisher_photo_is_preferred_over_stock(tmp_path: Path) -> None:
    from tools.base_tool import ToolResult

    class PexelsMustNotRun:
        def execute(self, params):
            raise AssertionError("stock must not be fetched when photo exists")

    card = {
        "rank": 1, "state": "Goa", "outlet": "ndtv", "headline": "h",
        "narration": "n", "source_url": "https://ndtv.test/story",
        "visual_query": "Goa police", "lead_image": "https://cdn.test/a.webp",
    }
    cards_path = tmp_path / "cards.json"
    cards_path.write_text(json.dumps({"cards": [card]}), encoding="utf-8")

    class Registry:
        def get(self, name):
            assert name == "pexels_video"
            return PexelsMustNotRun()

    with mock.patch(
        "scripts.india_daily_video.download_publisher_image",
        return_value="photo-01.webp",
    ), mock.patch(
        "scripts.india_daily_video.narrate_with_sapi", side_effect=_fake_narration
    ), mock.patch(
        "scripts.india_daily_video._download_map", side_effect=_fake_map_download
    ):
        props = json.loads(
            _run_assets(cards_path, tmp_path, Registry()).read_text(encoding="utf-8")
        )

    got = props["cards"][0]
    assert got["imageSrc"] == "india-daily-news/photo-01.webp"
    # Attribution must name the outlet recognisably, not the feed slug.
    assert got["imageCredit"] == "NDTV"
    assert got["outlet_name"] == "NDTV"
    assert got["clipSrc"] == ""
    # The raw CDN URL never reaches the render props.
    assert "lead_image" not in got
    assert got["visual_license"] == "publisher-lead-image (credited)"


def test_stock_used_only_when_publisher_photo_unavailable(
    tmp_path: Path,
) -> None:
    from tools.base_tool import ToolResult

    card = {
        "rank": 1, "state": "Goa", "outlet": "ndtv", "headline": "h",
        "narration": "n", "source_url": "https://ndtv.test/story",
        "visual_query": "Goa police", "lead_image": "https://cdn.test/a.webp",
    }
    cards_path = tmp_path / "cards.json"
    cards_path.write_text(json.dumps({"cards": [card]}), encoding="utf-8")

    class FakePexels:
        def __init__(self):
            self.queries = []

        def execute(self, params):
            self.queries.append(params["query"])
            Path(params["output_path"]).write_bytes(b"\x00" * 2048)
            return ToolResult(success=True, data={"license": "Pexels License"})

    pexels = FakePexels()

    class Registry:
        def get(self, name):
            return pexels

    with mock.patch(
        "scripts.india_daily_video.download_publisher_image", return_value=None
    ), mock.patch(
        "scripts.india_daily_video.narrate_with_sapi", side_effect=_fake_narration
    ), mock.patch(
        "scripts.india_daily_video._download_map", side_effect=_fake_map_download
    ):
        props = json.loads(
            _run_assets(cards_path, tmp_path, Registry()).read_text(encoding="utf-8")
        )

    got = props["cards"][0]
    assert got["imageSrc"] == ""
    assert got["imageCredit"] == ""
    assert got["clipSrc"] == "india-daily-news/clip-01.mp4"
    assert pexels.queries, "stock fallback should have been attempted"


def test_render_props_create_one_sequence_per_news_card() -> None:
    cards = [
        {
            "rank": rank,
            "state": "Delhi" if rank == 1 else "India",
            "outlet": "Example News",
            "headline": f"Verified headline {rank}",
            "narration": f"Verified narration {rank}",
            "card_subtitle": "Daily update",
            "source_url": f"https://example.com/{rank}",
            "visual_query": f"India city scene {rank}",
            "clip_path": f"E:/OpenMontage/projects/test/assets/clip-{rank}.mp4",
            "clip_source_url": f"https://pexels.com/video/{rank}",
            "narration_seconds": 2.0 + rank,
        }
        for rank in range(1, 6)
    ]

    props = build_render_props(cards, "E:/OpenMontage/projects/test/assets/narration.wav")

    assert len(props["cards"]) == 5
    assert props["cards"][0]["durationInFrames"] == 90
    assert props["cards"][-1]["durationInFrames"] == 210
    assert props["audioSrc"] == "E:/OpenMontage/projects/test/assets/narration.wav"
    assert props["durationInFrames"] == 120 + sum(card["durationInFrames"] for card in props["cards"])
    assert all("lead_image" not in card for card in props["cards"])
    # Single-contract: one end card sources list for the source end card.
    assert [s["outlet"] for s in props["sources"]] == ["Example News"] * 5
    assert props["sources"][0]["url"] == "https://example.com/1"
    assert props["endCardFrames"] == 120


def test_geo_projection_emits_scene_ready_paths() -> None:
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"NAME_1": "Karnataka"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [74.0, 12.0], [78.0, 12.0], [78.0, 18.0],
                        [74.0, 18.0], [74.0, 12.0],
                    ]],
                },
            }
        ],
    }

    result = geojson_to_svg_paths(geojson)

    assert set(result) == {"paths", "centroids", "pans", "outline"}
    assert result["paths"]["Karnataka"].startswith("M")
    assert result["paths"]["Karnataka"].endswith("Z")
    x, y = result["centroids"]["Karnataka"]
    assert 24 <= x <= 696 and 24 <= y <= 1256
    assert result["outline"] == result["paths"]["Karnataka"]
    # The pan must stay clamped: panning a whole zoomed country off-frame
    # would leave the card floating on empty background.
    pan_x, pan_y = result["pans"]["Karnataka"]
    assert abs(pan_x) <= 120 and abs(pan_y) <= 150


def test_sapi_narration_uses_wav_and_zero_cost_local_voice(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command: list[str], env: dict[str, str]) -> None:
        calls.append((command, env))
        with wave.open(env["INDIA_NEWS_OUTPUT"], "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00" * 1600)  # 0.1s of silence

    audio_path = tmp_path / "card-01.wav"
    meta = narrate_with_sapi(
        "A factual sentence for local narration.", audio_path, run=fake_run
    )

    assert calls
    assert calls[0][0][0].lower().endswith("powershell.exe")
    assert calls[0][1]["INDIA_NEWS_OUTPUT"] == str(audio_path)
    assert calls[0][1]["INDIA_NEWS_VOICE"] == "Microsoft Zira Desktop"
    assert meta == {"provider": "windows_sapi", "voice": "Microsoft Zira Desktop", "cost_usd": 0.0}


def _write_silence_wav(path: Path, seconds: float) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * int(16000 * seconds))


def test_assembled_narration_sums_segments_plus_pads(tmp_path: Path) -> None:
    first = tmp_path / "audio-01.wav"
    second = tmp_path / "audio-02.wav"
    _write_silence_wav(first, 2.0)
    _write_silence_wav(second, 3.0)
    output = tmp_path / "narration.wav"

    assembly = assemble_narration([first, second], output)

    assert [s["duration"] for s in assembly["segments"]] == [2.0, 3.0]
    assert assembly["segments"][0]["start"] == 0.25
    assert assembly["segments"][1]["start"] == 0.25 + 2.0 + 0.45
    expected = 0.25 + 2.0 + 0.45 + 3.0 + 0.25
    assert assembly["duration"] == round(expected, 3)
    assert output.is_file()
    # Card frame timing must follow the assembled track, not the parts: each
    # card starts exactly on its speech and owns the pad that follows it.
    timings = card_timings(assembly["segments"], assembly["duration"])
    assert timings[0] == {"fromFrame": 8, "durationInFrames": 73}
    assert timings[1]["fromFrame"] == 81
    props = build_render_props(
        [
            {
                "rank": 1,
                "narration_seconds": assembly["segments"][0]["duration"],
                "narration_offset": assembly["segments"][0]["start"],
            },
            {
                "rank": 2,
                "narration_seconds": assembly["segments"][1]["duration"],
                "narration_offset": assembly["segments"][1]["start"],
                "audio_total_seconds": assembly["duration"],
            },
        ],
        "india-daily-news/narration.wav",
    )
    assert [c["fromFrame"] for c in props["cards"]] == [8, 81]
    story_frames = props["durationInFrames"] - props["endCardFrames"]
    # The picture must cover the whole assembled track (within one frame of
    # rounding): otherwise narration runs past the last card.
    assert abs(story_frames / 30 - assembly["duration"]) <= 1 / 30 + 0.01
    assert props["audioSrc"] == "india-daily-news/narration.wav"


def test_scriptwriter_requests_each_story_in_a_compact_free_call(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls: list[list[dict]] = []

    class Writer(OpenRouterScriptwriter):
        def _call(self, stories: list, model: str, timeout: float) -> dict:
            calls.append(stories)
            story = stories[0]
            card = {
                "rank": story["rank"],
                "headline": f"Rewritten rank {story['rank']}",
                "narration": "A compact factual news card from the verified source material.",
                "card_subtitle": "Verified daily news update",
                "visual_query": f"India news scene {story['rank']}",
            }
            return {"choices": [{"message": {"content": json.dumps([card])}}]}

    stories = [
        {
            "rank": rank,
            "outlet": "Example",
            "headline": f"Source headline {rank}",
            "summary": f"Source summary {rank}",
            "state": "India",
            "source_url": f"https://example.com/{rank}",
        }
        for rank in range(1, 6)
    ]

    result = Writer().execute({"stories": stories, "model": "openrouter/free"})

    assert result.success, result.error
    assert result.data["card_count"] == 5
    assert [call[0]["rank"] for call in calls] == [1, 2, 3, 4, 5]
    assert all(len(call) == 1 for call in calls)


def test_scriptwriter_rejects_paid_model_before_request(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    class Writer(OpenRouterScriptwriter):
        def _call(self, stories: list, model: str, timeout: float) -> dict:
            raise AssertionError("paid request must never be sent")

    result = Writer().execute({"stories": [{"rank": 1}], "model": "paid/model"})
    assert not result.success
    assert "Refusing paid model" in (result.error or "")
