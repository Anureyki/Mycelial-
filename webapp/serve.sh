#!/usr/bin/env bash
# Serves the Mycelial PWA over the LAN so it can be added to a phone's home screen.
cd "$(dirname "$0")" || exit 1
PORT="${1:-8090}"
echo "Serving Mycelial webapp on http://0.0.0.0:${PORT}"
python3 -m http.server "$PORT" --bind 0.0.0.0
