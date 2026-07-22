#!/bin/bash
# update-gravity.sh – Update Pi‑hole Gravity (DNS blocklist)
# Usage: update-gravity.sh

# Check if Pi‑hole container is running
if ! docker ps --filter "name=pihole-unbound" --filter "status=running" --format "{{.Names}}" | grep -q "pihole-unbound"; then
    printf "ERROR: Pi‑hole container 'pihole-unbound' is not running.\n"
    exit 1
fi

# Run gravity update (sudo may be required if user not in docker group)
if sudo docker exec pihole-unbound pihole -g; then
    printf "OK: Pi‑hole gravity updated successfully.\n"
    printf "$(date -Iseconds) | coding_agent | GRAVITY_UPDATE | success\n" >> ~/mycelial/logs/audit.log
    exit 0
else
    printf "ERROR: Pi‑hole gravity update failed.\n"
    printf "$(date -Iseconds) | coding_agent | GRAVITY_UPDATE | failed\n" >> ~/mycelial/logs/audit.log
    exit 1
fi
