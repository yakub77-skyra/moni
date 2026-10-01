# Social Scraping Safety — burner playbook (instagrapi + twscrape)

Free-library path only. No paid calls are possible from these tools.

## Accounts

- Use BURNER accounts only — never your main Instagram or X login.
- Create the burner on a phone, add a profile photo + bio, follow a few
  public news/outlet accounts, browse normally for a few days first.
- Instagram password is used ONCE by human-run `make social-setup` for the
  first login + settings dump. Runtime tools are session-only and never read
  `IG_PASSWORD`.
- X credentials live in the human-managed twscrape pool DB
  (`SOCIAL_X_DB_PATH`), never in `.env`. Tools never add/login accounts.

## Warm-up (7–10 days, manual)

1. Days 1–3: log in, browse feed/reels, search 2–3 hashtags, no bulk actions.
2. Days 4–7: a few hashtag searches per day, open reels, stay inside
   08:00–23:00 Asia/Kolkata.
3. Days 8–10: run `make social-smoke` (ONE paced search per platform).
4. Only then set `warmed: true` in `config/social_scraping.yaml`.

`social.warmed` defaults to `false` → tools return UNAVAILABLE with a
checklist. Applies to `make social-smoke` too. Only interactive
`make social-setup` is exempt.

## Caps (human pacing)

- Between-call sleep: random 2.5–6.5s (automatic).
- Per run: 5 searches / 10 URL extractions max.
- Per IST day: IG 20 / X 30, HALVED without a proxy.
- Proxies: set `IG_PROXY_URL` / `X_PROXY_URL` (all calls route through them).
  Residential/mobile proxies are safest; datacenter IPs burn faster.

## Active window

- Default 08:00–23:00 Asia/Kolkata. Outside → UNAVAILABLE with ZERO calls.
- Document your scheduler inside the window (e.g. 09:00 IST cron).

## On challenge / block

- Instagram `ChallengeRequired` / `CheckpointRequired` / `LoginRequired` /
  `TwoFactorRequired` / `SentryBlock` / auth-429, or X auth failures:
  → breaker trips until next IST midnight (`projects/social/breaker.json`),
  → tool returns DEGRADED, ladder skips to yt-dlp agency / shorts / trailer.
- ZERO retries on auth blocks. Rate-429/transient: max 2 retries
  (backoff 5s/25s + jitter), then DEGRADED.
- On any challenge: STOP 24–48h, never retry in a loop, complete the
  challenge on the phone, then manual clear:
  delete `projects/social/breaker.json` (or just that platform's key)
  and rerun `make social-setup` if the session expired.

## Ladders (unchanged)

- Breaking: yt-dlp agency → `x_video_search` → skip.
- Trending: `instagram_reel_search` → yt-dlp shorts → trailer → skip.

## Hygiene

- Sessions (`*session*.json`), pool DBs (`*.db`), `accounts.txt`, proxy
  lists, and `projects/social/` state are gitignored. `.env` is gitignored.
- Logs/events/checkpoints/artifacts are sanitized (passwords, cookies,
  tokens, proxy creds redacted).
- READ-ONLY: search/get endpoints only. Never like/follow/comment/post/DM.
- NO TIKTOK (blocklist stays in `ytdlp_downloader`).
