"""Free OpenRouter LLM scriptwriter (no new dependencies, stdlib HTTP)."""

from __future__ import annotations

import json
import os
import time
import urllib.request
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

DEFAULT_FREE_MODEL = "openrouter/free"

# Free-model fallback chain. OpenRouter rotates its "":free"" catalogue often
# (the retired meta-llama 70B free id started 404-ing), so on a 404 or a
# model-not-found error we walk this list. Every entry is $0.
FREE_MODEL_CHAIN = [
    "openrouter/free",
    "z-ai/glm-5.2:free",
    "qwen/qwen3.8-27b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-31b-it:free",
]

# Safety: only $0 models may run. A paid id is rejected BEFORE the API call
# so a typo can never produce a bill.
FREE_SUFFIXES = (":free", "/free")


def _is_free_model(model: str) -> bool:
    return bool(model) and model.endswith(FREE_SUFFIXES)


def _salvage_cards(text: str) -> list[dict]:
    """Pull card objects out of prose or truncated JSON.

    Free models sometimes wrap JSON in commentary or hit the token ceiling
    mid-array. Scanning balanced braces recovers whatever cards did arrive
    instead of failing the whole stage.
    """
    cards: list[dict] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    try:
                        candidate = json.loads(text[start:index + 1])
                    except ValueError:
                        candidate = None
                    if isinstance(candidate, dict) and "narration" in candidate:
                        cards.append(candidate)
                    start = -1
    return cards

def _local_fallback_card(story: dict[str, Any]) -> dict[str, Any]:
    """Build a conservative card from verified feed fields only.

    This is used when every zero-cost OpenRouter endpoint is rate-limited. It
    deliberately does not infer facts, quote sources, or invent names.
    """
    headline = str(story.get("headline") or "India news update").strip()
    summary = str(story.get("summary") or "").strip()
    state = str(story.get("state") or "India").strip()
    narration = summary or headline
    words = narration.split()
    if len(words) > 18:
        narration = " ".join(words[:18]).rstrip(".,;:") + "."
    return {
        "rank": story.get("rank"),
        "headline": headline,
        "narration": narration,
        "card_subtitle": f"Latest update from {state}",
        "visual_query": f"{state} India news context",
        "fallback": True,
    }


SYSTEM_PROMPT = (
    "You are the scriptwriter for 'India in Last 24hr', a 60-90 second "
    "vertical news roundup. Rules: rewrite every story in your own words "
    "(no copying headlines), keep each card to 12-18 spoken words, neutral "
    "factual tone, no invented facts, no quotes unless in the source body. "
    "Return STRICT JSON: cards with rank, headline, narration, "
    "card_subtitle, visual_query, one card per story in order."
)


class OpenRouterScriptwriter(BaseTool):
    name = "openrouter_scriptwriter"
    version = "0.1.0"
    tier = ToolTier.SOURCE
    capability = "scriptwriting"
    provider = "openrouter"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.API

    dependencies: list[str] = ["env:OPENROUTER_API_KEY"]
    install_instructions = "Set OPENROUTER_API_KEY in .env (free key, free models)."
    agent_skills: list[str] = []

    capabilities = ["write_news_script", "rewrite_headlines", "narration_copy"]
    supports = {"free_models": True, "requires_api_key": True}
    best_for = ["rewriting scraped India stories into 12-18 word card scripts"]
    not_good_for = ["offline use (needs OpenRouter network access)"]
    fallback_tools = []

    input_schema = {
        "type": "object",
        "required": ["stories"],
        "properties": {
            "stories": {"type": "array", "items": {"type": "object"}},
            "model": {"type": "string", "default": DEFAULT_FREE_MODEL},
            "request_timeout_seconds": {"type": "number", "default": 90},
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=10, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout"])
    idempotency_key_fields = ["stories", "model"]
    side_effects = ["calls OpenRouter chat-completions API"]
    user_visible_verification = ["Read each narration line for factual drift"]

    def get_status(self) -> ToolStatus:
        if os.environ.get("OPENROUTER_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def _call(self, stories: list, model: str, timeout: float) -> dict:
        if not _is_free_model(model):
            raise RuntimeError(
                f"Refusing paid model '{model}': only ':free'/'/free' allowed."
            )
        key = os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set in .env")
        compact = [{
            "rank": s.get("rank"), "outlet": s.get("outlet"),
            "headline": s.get("headline"), "summary": s.get("summary"),
            "state": s.get("state"), "source_url": s.get("source_url"),
            "body_text": (s.get("body_text") or "")[:1500],
        } for s in stories]
        body = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"stories": compact})},
            ],
            "temperature": 0.4,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://openmontage.video",
                "X-Title": "OpenMontage india-in-last-24hr",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        if self.get_status() != ToolStatus.AVAILABLE:
            return ToolResult(success=False, error=self.install_instructions)
        stories = inputs.get("stories") or []
        if not stories:
            return ToolResult(success=False, error="stories[] is empty")
        model = inputs.get("model") or DEFAULT_FREE_MODEL
        if not _is_free_model(model):
            return ToolResult(
                success=False,
                error=f"Refusing paid model '{model}': only free models allowed.",
            )
        timeout = float(inputs.get("request_timeout_seconds", 90))

        # Ask for one card per request. Free models frequently truncate a
        # five-card JSON array, while compact single-card JSON is reliable.
        cards: list[dict] = []
        errors: list[str] = []
        for story in stories:
            story_cards: list[dict] = []
            story_models: list[str] = []
            chain = [model] + [m for m in FREE_MODEL_CHAIN if m != model]
            for candidate in chain:
                try:
                    payload = self._call([story], candidate, timeout)
                    content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
                    text = content.strip()
                    if text.startswith("```"):
                        text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
                    try:
                        parsed = json.loads(text)
                    except ValueError:
                        parsed_cards = _salvage_cards(text)
                    else:
                        if isinstance(parsed, list):
                            parsed_cards = parsed
                        elif isinstance(parsed, dict):
                            parsed_cards = parsed.get("cards") or parsed.get("stories") or []
                        else:
                            parsed_cards = []
                    if len(parsed_cards) != 1 or not isinstance(parsed_cards[0], dict):
                        raise ValueError("expected one JSON card object")
                    story_cards = parsed_cards
                    story_models.append(candidate)
                    model = candidate
                    break
                except Exception as exc:
                    errors.append(f"rank {story.get('rank')}, {candidate}: {exc}")
            if len(story_cards) != 1:
                # Every attempted model was free by construction. A local
                # card keeps the render moving during free-tier rate limits.
                cards.append(_local_fallback_card(story))
                if errors:
                    errors[-1] = errors[-1] + " (using local factual fallback)"
                continue
            cards.append(story_cards[0])

        merged = []
        for story, card in zip(stories, cards):
            merged.append({
                "rank": story.get("rank"), "state": story.get("state"),
                "outlet": story.get("outlet"),
                "source_url": story.get("source_url"),
                "lead_image": story.get("lead_image", ""),
                "headline": card.get("headline", ""),
                "narration": card.get("narration", ""),
                "card_subtitle": card.get("card_subtitle", ""),
                "visual_query": card.get("visual_query", ""),
                "fallback": bool(card.get("fallback", False)),
            })
        artifacts: list[str] = []
        if inputs.get("output_path"):
            path = Path(inputs["output_path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(
                {"model": model, "cards": merged},
                indent=2, ensure_ascii=False), encoding="utf-8")
            artifacts.append(str(path))
        return ToolResult(
            success=True,
            data={"model": model, "cards": merged, "card_count": len(merged)},
            artifacts=artifacts,
            duration_seconds=round(time.time() - start, 2),
        )
