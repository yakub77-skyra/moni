# OpenMontage — use your own IP for YouTube (fix GitHub 403/429 blocks)

GitHub Actions runners live on datacenter IPs, which YouTube aggressively
rate-limits (HTTP 403/429, "Sign in to confirm you're not a bot"). This repo
now routes **only YouTube search + download** through a proxy on **your** IP.
Heavy work (Remotion render, FFmpeg encode, probes, LUFS) still runs on GitHub.

## 1. Expose a proxy on your own IP (one-time, ~10 min)

Pick one — anything that gives yt-dlp an HTTP(S) proxy on a residential/home IP:

| Option | When to use | Quick start |
|---|---|---|
| **SSH -D (zero install)** | Your home PC/server has SSH + internet | `ssh -D 1080 -N -f user@home-box` then proxy is `socks5://127.0.0.1:1080` — but CI can't reach localhost, so pair with a reverse tunnel or use option 2/3 |
| **tinyproxy on home box / VPS** | Simplest stable HTTP proxy | `sudo apt install tinyproxy`, set `Port 8888`, `Allow <github-runner-nat>` or user/pass, `systemctl restart tinyproxy` → `http://user:pass@<your-ip>:8888` |
| **squid on home box / VPS** | You want auth + logs | `sudo apt install squid`, add `http_port 3128`, `http_access allow`, auth via `htpasswd` → `http://user:pass@<your-ip>:3128` |
| **Commercial residential proxy** | No always-on home box | Any provider giving `http://user:pass@host:port` — paste it as-is |

Requirements: the proxy host must be reachable from the public internet
(port-forward 3128/8888 on your router or use a small VPS), and use
`user:pass@` auth so strangers can't ride your IP.

## 2. Give the workflow your proxy (no code change)

- **Repo secret (recommended):** Settings → Secrets and variables → Actions →
  New repository secret → name `YTDLP_PROXY_URL`, value
  `http://user:pass@your-home-ip:3128`. Every `render-reels` run uses it.
- **Per-run override:** Actions → render-reels → Run workflow → fill
  `ytdlp_proxy_url` (beats the secret for that run only).
- **Local runs:** copy `.env.example` → `.env` and set `YTDLP_PROXY_URL` there.

Leave it blank and behaviour is unchanged (direct GitHub IP, may get blocked).

## 3. YouTube cookies (recommended: cookies-only, no proxy needed)

1. In Chrome, log in to YouTube with a throwaway account (not your main),
   watch 1-2 videos, then export via `Get cookies.txt LOCALLY` → saves a
   `youtube.cookies.txt` (Netscape format, starts with
   `# Netscape HTTP Cookie File`).
2. **CI:** GitHub repo → Settings → Secrets and variables → Actions → New
   repository secret → name `YTDLP_COOKIES`, value = paste the **whole file
   content**. That is all — the workflow auto-uses it every run, no per-run
   input. It validates the Netscape header and warns if the paste is wrong.
3. **Local:** save the file anywhere and set `YTDLP_COOKIES_FILE=/path/to.txt`
   (or `YTDLP_COOKIES_FROM_BROWSER=chrome` for local-only runs — never in CI).

Refresh the secret every 2-4 weeks or after a password change; expiry shows up
again as 403s.

## 4. Verify

- Workflow log shows `youtube route: proxy (your IP…)` and each download logs
  `route=<your-proxy-host>` (credentials never printed).
- `out/daily/footage_report.json` entries carry `"route": "<your-proxy-host>"`.
- Without a proxy, block errors now end with
  `Set YTDLP_PROXY_URL to route YouTube via your own IP`.

Security notes: proxy credentials live only in Actions secrets / local `.env`
(never committed); tool logs redact `user:pass` and print host only; cookies
file in CI is written to `/tmp` with `600` perms and discarded after the run.
