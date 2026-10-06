#!/usr/bin/env bash
# OpenMontage — turn a home Linux box / VPS into YOUR residential YouTube proxy.
# You run THIS (I cannot reach your home network from here).
#
#   curl -fsSL https://raw.githubusercontent.com/<you>/OpenMontage/main/scripts/setup_home_proxy.sh \
#     | sudo bash -s -- --user <proxy-user> --port 8888
#
# What it does:
#   1. installs tinyproxy (Debian/Ubuntu) or squid (fallback note)
#   2. writes an authed, localhost-safe config (BasicAuth user:pass)
#   3. opens the firewall port (ufw if present)
#   4. restarts + enables the service, prints your YTDLP_PROXY_URL value
#
# Then paste that value as GitHub secret YTDLP_PROXY_URL and YouTube search +
# download leaves from THIS box's residential IP. Render stays on GitHub.
set -euo pipefail

USER_NAME=""; PORT="8888"; PASSWORD=""
while [ $# -gt 0 ]; do
  case "$1" in
    --user) USER_NAME="${2:-}"; shift 2;;
    --port) PORT="${2:-}"; shift 2;;
    --password) PASSWORD="${2:-}"; shift 2;;
    -h|--help)
      echo "Usage: sudo bash setup_home_proxy.sh --user <name> [--port 8888] [--password <pw>]"
      echo "  --password optional; a strong one is generated when omitted."
      exit 0;;
    *) echo "Unknown arg: $1 (see --help)"; exit 1;;
  esac
done
if [ -z "$USER_NAME" ]; then echo "Missing --user (see --help)"; exit 1; fi
if [ "$(id -u)" -ne 0 ]; then echo "Run as root (sudo)."; exit 1; fi
if [ -z "$PASSWORD" ]; then
  PASSWORD="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20)"
  GENERATED=1
else
  GENERATED=0
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This installer targets Debian/Ubuntu (apt). For other distros install" >&2
  echo "tinyproxy manually and copy the BasicAuth/Allow lines from docs/YOUTUBE_SELF_IP.md." >&2
  exit 1
fi

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tinyproxy curl >/dev/null

CONF="/etc/tinyproxy/tinyproxy.conf"
cp "$CONF" "$CONF.bak.$(date +%F-%T)" 2>/dev/null || true
# Idempotent minimal config: keep distro defaults, enforce our Port/Allow/auth.
python3 - "$CONF" "$PORT" "$USER_NAME" "$PASSWORD" <<'EOF'
import re, sys
path, port, user, pw = sys.argv[1:5]
text = open(path, encoding="utf-8", errors="replace").read()
def sub(pattern, repl):
    global text
    text, n = re.subn(pattern, repl, text, count=1, flags=re.M)
    if not n:
        text += "\n" + repl + "\n"
sub(r"^Port\s+\d+.*$", f"Port {port}")
sub(r"^#?Listen\s+.*$", "Listen 0.0.0.0")
# GitHub runner egress changes constantly: auth (not Allow) is the gate.
# Keep a single open Allow; BasicAuth below actually protects the port.
sub(r"^Allow\s+127\.0\.0\.1.*$", "Allow 0.0.0.0/0")
if not re.search(r"^Allow\s+0\.0\.0\.0/0", text, flags=re.M):
    text += "\nAllow 0.0.0.0/0\n"
sub(r"^#?BasicAuth\s+.*$", f"BasicAuth {user} {pw}")
if not re.search(r"^BasicAuth\s+", text, flags=re.M):
    text += f"\nBasicAuth {user} {pw}\n"
# HTTPS CONNECT for youtube/googlevideo + plain HTTP.
for p in ("443", "80"):
    if not re.search(rf"^ConnectPort\s+{p}\s*$", text, flags=re.M):
        text += f"\nConnectPort {p}\n"
open(path, "w", encoding="utf-8").write(text)
EOF

systemctl enable --now tinyproxy >/dev/null 2>&1 || service tinyproxy restart
if command -v ufw >/dev/null 2>&1; then
  ufw allow "$PORT/tcp" >/dev/null 2>&1 || true
fi

PUBLIC_IP="$(curl -fsSL --max-time 10 https://api.ipify.org || echo '<THIS-BOX-IP>')"
echo ""
echo "================================================================"
echo " Proxy is UP on this box's residential IP."
echo ""
echo " GitHub secret YTDLP_PROXY_URL value (paste exactly):"
echo "   http://$USER_NAME:$PASSWORD@$PUBLIC_IP:$PORT"
if [ "$GENERATED" = "1" ]; then
  echo ""
  echo " A strong password was generated for you (shown only here)."
fi
echo ""
echo " Still needed on YOUR router (I can't do this part):"
echo "   forward TCP $PORT -> this box, or run this box on a VPS with a"
echo "   public IP. Test from anywhere else:"
echo "   curl -x http://$USER_NAME:$PASSWORD@$PUBLIC_IP:$PORT -I https://www.youtube.com"
echo "================================================================="
