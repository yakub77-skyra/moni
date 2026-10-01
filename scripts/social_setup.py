"""Human-run first-time social setup (interactive only).

- Instagram: first instagrapi login with IG_USERNAME + IG_PASSWORD (setup-only),
  then settings dump to IG_SESSION_PATH. Prints challenge hints including
  "complete on phone then rerun". Exempt from active-window and warm-up gates.
- X/twscrape: passthrough to `twscrape` CLI for human-managed pool accounts.
- Never imported by tools; never runs in CI.

Windows fallback: python scripts/social_setup.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _prompt(text: str, secret: bool = False) -> str:
    if secret:
        import getpass

        return getpass.getpass(text).strip()
    return input(text).strip()


def setup_instagram() -> int:
    print("== Instagram (instagrapi, burner account, human-only) ==")
    username = os.environ.get("IG_USERNAME") or _prompt("IG username (burner): ")
    password = os.environ.get("IG_PASSWORD") or _prompt("IG password (setup-only, never stored): ", secret=True)
    session_path = Path(os.environ.get("IG_SESSION_PATH", "projects/social/ig_session.json"))
    proxy = os.environ.get("IG_PROXY_URL", "").strip()
    if not username or not password:
        print("  [skip] username/password not provided; Instagram setup skipped.")
        return 0
    try:
        from instagrapi import Client
    except ImportError:
        print("  [fail] instagrapi not installed. Run: pip install -r requirements.txt")
        return 1
    kwargs: dict = {}
    if proxy:
        kwargs["proxy"] = proxy
        print(f"  proxy: set (redacted)")
    client = Client(**kwargs)
    try:
        client.login(username, password)
    except Exception as exc:
        name = type(exc).__name__
        print(f"  [challenge] login raised {name}: {str(exc)[:300]}")
        print("  Hints: complete the challenge on your phone (approve / code),")
        print("  wait 24-48h after any block and NEVER hammer retries,")
        print("  then rerun: make social-setup")
        return 1
    session_path.parent.mkdir(parents=True, exist_ok=True)
    client.dump_settings(str(session_path))
    print(f"  [ok] session dumped to {session_path}")
    print("  Next: warm the burner manually 7-10 days (browse/search only,")
    print("  stay under caps), then set warmed: true in config/social_scraping.yaml.")
    print("  Schedule runs inside 08:00-23:00 Asia/Kolkata (e.g. 09:00 IST).")
    return 0


def setup_x(passthrough: list[str]) -> int:
    print("== X (twscrape, human-managed pool) ==")
    db_path = os.environ.get("SOCIAL_X_DB_PATH", "projects/social/twscrape_accounts.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    if passthrough:
        cmd = ["twscrape", *passthrough]
    else:
        print(f"  Pool DB: {db_path}")
        print("  Add accounts manually, e.g.:")
        print(f"  twscrape add_account <username> <password> <email> <email_password>")
        print("  Then verify: twscrape accounts")
        print("  (Pass extra args after -- to forward to twscrape, e.g.")
        print("   make social-setup TWSCAPE_ARGS='accounts')")
        cmd = ["twscrape", "accounts"]
    print(f"  $ {' '.join(cmd)} (TWSCRAPE_DB={db_path})")
    env = dict(os.environ)
    # twscrape resolves the pool via its own config/--db-file; expose path for clarity.
    env["TWSCRAPE_DB"] = db_path
    try:
        completed = subprocess.run(cmd, env=env)  # noqa: S603
        return completed.returncode
    except FileNotFoundError:
        print("  [fail] twscrape CLI not found. Run: pip install -r requirements.txt")
        return 1


def main(argv: list[str]) -> int:
    print("Social setup checklist (human-only, interactive):")
    print("  1. Use BURNER accounts only, never your main.")
    print("  2. Instagram: first login here, then warm 7-10 days manually.")
    print("  3. X: accounts are human-managed in the pool DB, never in .env.")
    print("  4. On any challenge: stop 24-48h, complete on phone, rerun.")
    print("  5. Schedule inside 08:00-23:00 Asia/Kolkata (e.g. 09:00 IST).")
    print("")
    only = set(a for a in argv if a in ("instagram", "x"))
    passthrough = [a for a in argv if not a.startswith("-") and a not in ("instagram", "x")]
    # Allow: python scripts/social_setup.py -- accounts
    if "--" in argv:
        passthrough = argv[argv.index("--") + 1 :]
    rc = 0
    if not only or "instagram" in only:
        rc |= setup_instagram()
    if not only or "x" in only:
        rc |= setup_x(passthrough)
    print("")
    print("Done. Manual breaker clear: delete projects/social/breaker.json")
    print("(or just that platform's key) after the 24-48h stop.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
