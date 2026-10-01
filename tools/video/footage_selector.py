"""Footage selector — picks and trims real clips per style.

Breaking: 1 continuous 15-20s clip (middle 18s of the longest input).
Trending: 4-6 jump cuts of 3-5s (middle 4s of each input, concatenated).

Delegates all ffmpeg work to the existing VideoTrimmer tool — no ffmpeg
flags are reimplemented here. Inputs must be real downloaded files.
"""

from __future__ import annotations

import subprocess
import time
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

BREAKING_TARGET_SECONDS = 18.0
TRENDING_CUT_SECONDS = 4.0


def _probe_duration(path: Path) -> float | None:
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            stderr=subprocess.STDOUT, text=True, timeout=15,
        )
        return float(out.strip())
    except Exception:
        return None


class FootageSelector(BaseTool):
    name = "footage_selector"
    version = "0.1.0"
    tier = ToolTier.SOURCE
    capability = "clip_acquisition"
    provider = "openmontage"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["cmd:ffmpeg"]
    install_instructions = (
        "Install FFmpeg: https://ffmpeg.org/download.html\n"
        "Windows: winget install FFmpeg\n"
        "macOS: brew install ffmpeg\n"
        "Linux: sudo apt install ffmpeg"
    )
    agent_skills = ["ffmpeg"]

    capabilities = ["select_breaking_clip", "select_trending_montage"]
    supports = {"breaking_single_clip": True, "trending_jump_cuts": True}
    best_for = [
        "trimming one continuous breaking clip (15-20s)",
        "building a 4-6x jump-cut trending montage (3-5s each)",
    ]
    not_good_for = ["AI b-roll (breaking forbids it)", "semantic ranking (use clip_search)"]
    fallback_tools = ["video_trimmer"]

    input_schema = {
        "type": "object",
        "required": ["clips", "style", "output_dir"],
        "properties": {
            "clips": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["path"],
                    "properties": {
                        "path": {"type": "string"},
                        "duration": {"type": "number"},
                        "source_url": {"type": "string"},
                    },
                },
            },
            "style": {"type": "string", "enum": ["breaking", "trending"]},
            "output_dir": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=1024, vram_mb=0, disk_mb=2000, network_required=False
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["FFmpeg error"])
    idempotency_key_fields = ["clips", "style"]
    side_effects = ["writes trimmed clip(s) to output_dir"]
    user_visible_verification = ["Play each selected clip and confirm it matches the story"]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        from tools.video.video_trimmer import VideoTrimmer

        start = time.time()
        style = inputs.get("style")
        if style not in ("breaking", "trending"):
            return ToolResult(success=False, error=f"Unknown style: {style!r}")
        raw_clips = inputs.get("clips") or []
        if not raw_clips:
            return ToolResult(success=False, error="clips[] is empty")
        output_dir = Path(inputs.get("output_dir") or "assets/selected")
        output_dir.mkdir(parents=True, exist_ok=True)

        existing: list[dict[str, Any]] = []
        for clip in raw_clips:
            path = Path(clip.get("path", ""))
            if not path.is_file():
                continue
            duration = clip.get("duration") or _probe_duration(path)
            existing.append({"path": path, "duration": duration, "source_url": clip.get("source_url", "")})
        if not existing:
            return ToolResult(success=False, error="No input clip files exist on disk")

        trimmer = VideoTrimmer()
        if style == "breaking":
            return self._select_breaking(trimmer, existing, output_dir, start)
        return self._select_trending(trimmer, existing, output_dir, start)

    def _select_breaking(self, trimmer: Any, clips: list[dict], output_dir: Path, start: float) -> ToolResult:
        clips_sorted = sorted(clips, key=lambda c: c["duration"] or 0, reverse=True)
        best = clips_sorted[0]
        duration = best["duration"] or BREAKING_TARGET_SECONDS
        take = min(max(duration, 15.0), 20.0) if duration >= 15.0 else duration
        if duration <= 0:
            return ToolResult(success=False, error=f"Cannot probe duration: {best['path']}")
        start_s = max(0.0, (duration - take) / 2)
        out_path = output_dir / f"breaking_{best['path'].stem}.mp4"
        result = trimmer.execute({
            "operation": "cut",
            "input_path": str(best["path"]),
            "output_path": str(out_path),
            "start_seconds": round(start_s, 2),
            "end_seconds": round(start_s + take, 2),
            "codec": "libx264",
        })
        if not result.success:
            return ToolResult(success=False, error=f"Breaking trim failed: {result.error}")
        return ToolResult(
            success=True,
            data={
                "style": "breaking",
                "selected": [{
                    "path": str(out_path), "start_seconds": round(start_s, 2),
                    "duration": round(take, 2), "source_path": str(best["path"]),
                    "source_url": best["source_url"],
                }],
                "output": str(out_path),
            },
            artifacts=[str(out_path)],
            duration_seconds=round(time.time() - start, 2),
        )

    def _select_trending(self, trimmer: Any, clips: list[dict], output_dir: Path, start: float) -> ToolResult:
        picks = clips[:6]
        if len(picks) < 4:
            picks = (picks * 4)[:4]
        segments: list[dict[str, Any]] = []
        cut_paths: list[str] = []
        for i, clip in enumerate(picks[:6]):
            duration = clip["duration"] or 0
            take = min(TRENDING_CUT_SECONDS, duration) if duration > 0 else TRENDING_CUT_SECONDS
            if duration and duration <= take:
                start_s = 0.0
            elif duration:
                start_s = max(0.0, (duration - take) / 2)
            else:
                start_s = 0.0
            out_path = output_dir / f"trending_cut_{i:02d}_{clip['path'].stem}.mp4"
            result = trimmer.execute({
                "operation": "cut",
                "input_path": str(clip["path"]),
                "output_path": str(out_path),
                "start_seconds": round(start_s, 2),
                "end_seconds": round(start_s + take, 2),
                "codec": "libx264",
            })
            if not result.success:
                continue
            cut_paths.append(str(out_path))
            segments.append({
                "path": str(out_path), "start_seconds": round(start_s, 2),
                "duration": round(take, 2), "source_path": str(clip["path"]),
                "source_url": clip["source_url"],
            })
        if len(cut_paths) < 2:
            return ToolResult(success=False, error="Trending needs >=2 successful cuts")
        montage_path = output_dir / "trending_montage.mp4"
        concat = trimmer.execute({
            "operation": "concat",
            "segments": [{"input_path": p} for p in cut_paths],
            "output_path": str(montage_path),
        })
        if not concat.success:
            return ToolResult(success=False, error=f"Trending concat failed: {concat.error}")
        return ToolResult(
            success=True,
            data={"style": "trending", "selected": segments,
                  "cut_count": len(segments), "output": str(montage_path)},
            artifacts=[str(montage_path), *cut_paths],
            duration_seconds=round(time.time() - start, 2),
        )
