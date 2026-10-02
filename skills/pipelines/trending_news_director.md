# trending_news_director — Style 2 (TRENDING)

One story per job. One reference pack per job (`references/style2_trending`).
Clickbait/viral tone. Headline ends with ` | WATCH`. No TikTok (banned in
India): IG Reels + YT Shorts + trailers ONLY.

## 7 stages

1. **research** — Call `instagram_reel_search` (viral reels) plus
   `direct_clip_search` for trailer/shorts leads, last 24h. Keep exactly 1
   viral story with `source_url` + outlet and 4-6 reel/shorts/trailer leads.
   Never fabricate. Write `research_brief`.
2. **proposal** — Lock `render_runtime` (recommend remotion; present both
   runtimes per AGENT_GUIDE.md) and the 4-6x jump-cut montage treatment
   (3-5s per cut, hard cuts). Write `proposal_packet`.
3. **script** — Via `openrouter_scriptwriter`. Clickbait/viral tone, headline
   rewritten in own words ending with ` | WATCH`. Narration fits 4-6 cuts of
   3-5s. Write `script`.
4. **scene_plan** — `FootageMontage` plan for `TrendingNewsReel`: 4-6 clip
   paths with hard cuts every 3-5s + static overlay (TRENDING kicker
   auto-fit, headline, subhead) + date badge + watermark. Zero overlay
   animation. Write `scene_plan`.
5. **assets** — Download with `ytdlp_downloader` (TikTok blocklisted),
   build the montage with `footage_selector` (style=trending), narrate with
   `piper_tts` (offline default). **Human gate: `human_approved=True`
   required before edit.** Write `asset_manifest` with provenance per asset.
6. **edit** — 720x900 timeline matching `TrendingNewsReel` props. Hard cuts
   every 3-5s, no transitions. Write `edit_decisions`.
7. **compose** — Via `video_compose` to the `TrendingNewsReel` composition.
   Verify 720x900 + ffprobe. Write `render_report`.

## Rules

- Trending footage = IG Reels + YT Shorts + trailers ONLY. TikTok is
  blocklisted in `ytdlp_downloader` and forbidden here.
- **No placeholder footage, ever.** Synthetic filler (test patterns, colour
  bars, stock stand-ins, repeated copies of one clip) is forbidden. If
  `ytdlp_downloader` or `footage_selector` cannot supply real clips, STOP and
  escalate per AGENT_GUIDE.md "Escalate Blockers Explicitly" — report what was
  attempted, what failed (auth / tool / rate limit), and the options. Do not
  render a reel with substituted footage and call it done.
- Backlot events log automatically via `BaseTool.execute()`; gate `assets`
  with `write_checkpoint(..., human_approved=True)`.
- Load ONLY `references/style2_trending` for this style.

## Composition runtime contract (governance)

- `render_runtime` is locked at proposal
  (`proposal_packet.production_plan.render_runtime`) and carried through
  `edit_decisions` unchanged. A silent swap to another runtime is a CRITICAL
  governance violation.
- Present both composition runtimes to the user before locking: **remotion**
  (React composition for the `TrendingNewsReel` footage montage + static
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
