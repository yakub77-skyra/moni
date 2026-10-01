# breaking_news_director — Style 1 (BREAKING)

One story per job. One reference pack per job (`references/style1_breaking`).
Serious, emotional, factual. No clickbait. No AI b-roll. No TikTok.

## 7 stages

1. **research** — Call `x_video_search` (`<keywords> filter:videos`, last 24h)
   plus `india_news_scraper` for context. Keep exactly 1 urgent story with
   `source_url` + outlet. Widen lookback once (24h -> 48h) if empty. Never
   fabricate. Write `research_brief`.
2. **proposal** — Lock `render_runtime` (recommend remotion; present both
   runtimes per AGENT_GUIDE.md) and the single 15-20s continuous-clip
   treatment. Write `proposal_packet`.
3. **script** — Via `openrouter_scriptwriter`. Serious/emotional tone, neutral
   facts, headline rewritten in own words (3 lines max, caps, red highlight
   words noted separately). Narration fits one 15-20s clip. Write `script`.
4. **scene_plan** — One full-bleed scene for `BreakingNewsReel`: videoSrc +
   static headline block (BREAKING kicker, headline, subhead) + date badge
   (bottom-left) + watermark (bottom-right). Zero overlay animation.
   Write `scene_plan`.
5. **assets** — Download real footage with `ytdlp_downloader` (TikTok is
   blocklisted), select ONE 15-20s continuous clip with `footage_selector`,
   narrate with `piper_tts` (offline default). **Human gate:
   `human_approved=True` required before edit.** Write `asset_manifest` with
   provenance (provider, license, source URL) per asset.
6. **edit** — 720x1280 timeline matching `BreakingNewsReel` props. Cuts cover
   the narration exactly. Write `edit_decisions`.
7. **compose** — Via `video_compose` to the `BreakingNewsReel` composition.
   Verify 720x1280 + ffprobe. Write `render_report`.

## Rules

- Footage must be real scraped agency/X video. AI b-roll is forbidden.
- Backlot events log automatically via `BaseTool.execute()`; gate `assets`
  with `write_checkpoint(..., human_approved=True)`.
- Load ONLY `references/style1_breaking` for this style.

## Composition runtime contract (governance)

- `render_runtime` is locked at proposal
  (`proposal_packet.production_plan.render_runtime`) and carried through
  `edit_decisions` unchanged. A silent swap to another runtime is a CRITICAL
  governance violation.
- Present both composition runtimes to the user before locking: **remotion**
  (React composition for the `BreakingNewsReel` full-bleed clip + static
  headline block) and **hyperframes** (HTML/CSS/GSAP composition path).
  Recommend one with a reason tied to the brief, wait for explicit user
  approval, then log a `render_runtime_selection` decision in `decision_log`
  with both runtimes in `options_considered`.
- At compose, read `edit_decisions.render_runtime` and route via
  `video_compose`. If `render_runtime="hyperframes"`, route via the
  `video_compose` hyperframes path (lint and validate must pass before
  render); never silently fall back to Remotion. If the locked runtime is
  unavailable, escalate per AGENT_GUIDE.md and log a revised
  `render_runtime_selection` decision.
