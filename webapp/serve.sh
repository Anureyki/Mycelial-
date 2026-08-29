#!/usr/bin/env bash
# Local development server for the Mycelial PWA.
#
# This binds LOOPBACK ONLY. It used to default to `--bind 0.0.0.0` on 8090,
# which put the webapp on the LAN unauthenticated and in the clear - one of
# the two plaintext listeners retired in the hardening phase.
#
# To reach the webapp from a phone, use the TLS proxy instead:
#     https://<host>:8443/     (TLS 1.3 + basic auth)
# started by start_all.sh from config/nginx/mycelial.conf.
cd "$(dirname "$0")" || exit 1
PORT="${1:-8090}"
echo "Serving Mycelial webapp on http://127.0.0.1:${PORT} (loopback only)"
echo "For LAN access use the TLS proxy on :8443, not this script."
python3 -m http.server "$PORT" --bind 127.0.0.1
