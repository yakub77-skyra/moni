"""Contracts for the zero-cost India daily-news source and render runner."""

from __future__ import annotations

import json
import wave
from pathlib import Path

from scripts.india_daily_video import (
    assemble_narration,
    build_render_props,
    card_timings,
    geojson_to_svg_paths,
    narrate_with_sapi,
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

    assert set(result) == {"paths", "centroids", "outline"}
    assert result["paths"]["Karnataka"].startswith("M")
    assert result["paths"]["Karnataka"].endswith("Z")
    x, y = result["centroids"]["Karnataka"]
    assert 24 <= x <= 696 and 24 <= y <= 1256
    assert result["outline"] == result["paths"]["Karnataka"]


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
