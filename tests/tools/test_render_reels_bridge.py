"""Tests for the CI render bridge's asset-path contract.

Regression guarded: Remotion 404 while downloading
``http://localhost:3000/public/out/daily/footage/...`` — a repo-relative
filesystem path reaching Remotion and being treated as a public-dir asset.
Props must carry only ``footage/<name>`` after staging into
``remotion-composer/public/footage/``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import render_reels_ci as bridge


@pytest.fixture()
def staging(tmp_path, monkeypatch):
    """Point the bridge's staging dir at a temp public/ for isolation."""
    public = tmp_path / "public"
    monkeypatch.setattr(bridge, "PUBLIC_DIR", public)
    return public / "footage"


def _make_clip(path: Path, name: str = "clip.mp4") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 32)
    return path


def test_stage_copies_clip_and_returns_public_relative_path(staging, tmp_path) -> None:
    src = _make_clip(tmp_path / "out" / "daily" / "footage" / "breaking_KGZfN-363QY.mp4",
                     "breaking_KGZfN-363QY.mp4")

    staged = bridge.stage_clips([str(src)])

    assert staged == ["footage/breaking_KGZfN-363QY.mp4"]
    # The file really exists where staticFile() will look for it.
    assert (staging / "breaking_KGZfN-363QY.mp4").is_file()
    # The original download location is untouched.
    assert src.is_file()


def test_props_never_carry_a_repo_relative_path(staging, tmp_path) -> None:
    src = _make_clip(tmp_path / "out" / "daily" / "footage" / "clip.mp4")
    staged = bridge.stage_clips([str(src)])

    props = bridge.build_props(
        "BreakingNewsReel", staged,
        {"source_item": {"title": "Delhi HC orders status report",
                         "summary": "Delhi High Court asked for a report."}},
        duration_in_frames=540, highlight_words=["Delhi"], watermark="@INDIAINLAST24HR",
    )

    assert props["videoSrc"] == "footage/clip.mp4"
    assert "out/daily" not in json.dumps(props)


def test_trending_props_carry_at_most_six_relative_clips(staging, tmp_path) -> None:
    clips = []
    for i in range(9):
        clips.append(str(_make_clip(tmp_path / f"clip{i}.mp4", f"clip{i}.mp4")))
    staged = bridge.stage_clips(clips)

    props = bridge.build_props(
        "TrendingNewsReel", staged,
        {"source_item": {"title": "Dancing dog goes viral", "summary": "2M views."}},
        duration_in_frames=480, highlight_words=["2"], watermark="@X",
    )

    assert len(props["clips"]) == 6
    assert all(c.startswith("footage/") for c in props["clips"])


def test_duplicate_source_is_staged_once_but_referenced_twice(staging, tmp_path) -> None:
    # One source reused across several hard cuts is legitimate.
    src = _make_clip(tmp_path / "trending_cut_00_eyoMZwdjbOE.mp4", "trending_cut_00_eyoMZwdjbOE.mp4")
    staged = bridge.stage_clips([str(src)] * 4)

    assert staged == ["footage/trending_cut_00_eyoMZwdjbOE.mp4"] * 4
    assert len(list(staging.glob("*.mp4"))) == 1


def test_build_props_rejects_filesystem_paths() -> None:
    # The exact CI failure shape must be impossible to reach the render.
    with pytest.raises(ValueError, match="not public-relative"):
        bridge.build_props(
            "BreakingNewsReel",
            ["out/daily/footage/breaking_KGZfN-363QY.mp4"],
            {"source_item": {"title": "t", "summary": "s"}},
            duration_in_frames=540, highlight_words=[], watermark="@X",
        )


def test_build_props_allows_remote_urls() -> None:
    # Remote assets are served directly and remain valid.
    bridge._assert_public_relative({"videoSrc": "https://cdn.example/x.mp4"})
    bridge._assert_public_relative({"clips": ["footage/a.mp4", "https://cdn.example/b.mp4"]})


def test_stale_files_are_cleared_before_staging(staging, tmp_path) -> None:
    stale = staging / "leftover_from_last_run.mp4"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"stale")

    bridge.clean_staging()
    src = _make_clip(tmp_path / "new.mp4", "new.mp4")
    bridge.stage_clips([str(src)])

    assert not stale.exists()
    assert (staging / "new.mp4").is_file()


def test_clean_removes_whole_staging_dir(staging) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "x.mp4").write_bytes(b"x")

    removed = bridge.clean_staging()

    assert removed == staging
    assert not staging.exists()
    assert bridge.clean_staging() is None  # idempotent


def test_stage_missing_source_fails_loudly(staging, tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        bridge.stage_clips([str(tmp_path / "nope.mp4")])


def test_stage_command_writes_props_files(tmp_path, monkeypatch) -> None:
    public = tmp_path / "public"
    monkeypatch.setattr(bridge, "PUBLIC_DIR", public)
    out = tmp_path / "out"
    clip = _make_clip(out / "footage" / "breaking_KGZfN-363QY.mp4",
                      "breaking_KGZfN-363QY.mp4")
    jobs = [
        {"job_id": "job-b", "style": "breaking",
         "source_item": {"title": "Delhi HC orders report", "summary": "Court asked."}},
        {"job_id": "job-t", "style": "trending",
         "source_item": {"title": "Dancing dog goes viral", "summary": "2M views."}},
    ]
    (out / "routes.json").write_text(json.dumps({"mode": "fixture", "jobs": jobs}))
    (out / "footage_report.json").write_text(json.dumps({
        "job-b": {"footage_source": "live", "clips": [str(clip)]},
        "job-t": {"footage_source": "live", "clips": [str(clip)] * 4},
    }))

    rc = bridge.main(["stage", "--report", str(out / "footage_report.json"),
                      "--routes", str(out / "routes.json"), "--out", str(out)])

    assert rc == 0
    breaking = json.loads((out / "BreakingNewsReel.props.json").read_text())
    trending = json.loads((out / "TrendingNewsReel.props.json").read_text())
    assert breaking["videoSrc"] == "footage/breaking_KGZfN-363QY.mp4"
    assert trending["clips"] == ["footage/breaking_KGZfN-363QY.mp4"] * 4
    assert (public / "footage" / "breaking_KGZfN-363QY.mp4").is_file()


def test_stage_command_refuses_a_placeholder_footage_report(tmp_path, monkeypatch) -> None:
    """The exact report shape that shipped colour bars must not stage."""
    public = tmp_path / "public"
    monkeypatch.setattr(bridge, "PUBLIC_DIR", public)
    out = tmp_path / "out"
    clip = _make_clip(out / "footage" / "placeholder_breaking.mp4",
                      "placeholder_breaking.mp4")
    jobs = [{"job_id": "job-b", "style": "breaking",
             "source_item": {"title": "Delhi HC orders report", "summary": "s"}}]
    (out / "routes.json").write_text(json.dumps({"mode": "fixture", "jobs": jobs}))
    (out / "footage_report.json").write_text(json.dumps({
        "job-b": {"footage_source": "placeholder", "clips": [str(clip)],
                  "ladder": ["SKIPPED: ytsearch1:... (Install yt-dlp)"]},
    }))

    with pytest.raises(ValueError, match="not 'live'"):
        bridge.main(["stage", "--report", str(out / "footage_report.json"),
                     "--routes", str(out / "routes.json"), "--out", str(out)])

    assert not (out / "BreakingNewsReel.props.json").exists()


def test_derive_plan_covers_only_renderable_styles() -> None:
    plan = bridge.derive_plan([
        {"job_id": "a", "style": "breaking"},
        {"job_id": "b", "style": "breaking"},
        {"job_id": "c", "style": "trending"},
        {"job_id": "d", "style": "india_daily"},
    ])
    styles = [e["composition"] for e in plan]
    assert styles == ["BreakingNewsReel", "TrendingNewsReel"]  # one each, no india_daily


def test_staging_dir_is_gitignored() -> None:
    tracked = subprocess.run(
        ["git", "check-ignore", "-q", "remotion-composer/public/footage/x.mp4"],
        cwd=str(ROOT), capture_output=True,
    )
    assert tracked.returncode == 0, "public/footage must stay out of git"


def test_workflow_stages_before_render() -> None:
    wf = (ROOT / ".github" / "workflows" / "render-reels.yml").read_text(encoding="utf-8")
    assert "render_reels_ci.py stage" in wf
    assert "render_reels_ci.py render" in wf
    assert "render_reels_ci.py clean" in wf
    stage_at = wf.index("render_reels_ci.py stage")
    render_at = wf.index("render_reels_ci.py render")
    assert stage_at < render_at, "staging must happen before rendering"
    # npm cache must not key on the staged dir.
    assert "cache-dependency-path: remotion-composer/package-lock.json" in wf
    assert "cache-dependency-path: remotion-composer/public" not in wf
