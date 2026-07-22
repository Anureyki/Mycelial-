#!/bin/bash
# pi-hole-allow.sh – Remove domain from Pi‑hole blocklist
# Usage: pi-hole-allow.sh <domain>

DOMAIN="$1"

if [[ -z "$DOMAIN" ]]; then
    printf "ERROR: No domain specified.\n"
    exit 1
fi

if ! docker ps --filter "name=pihole-unbound" --filter "status=running" --format "{{.Names}}" | grep -q "pihole-unbound"; then
    printf "ERROR: Pi‑hole container not running.\n"
    exit 1
fi

OUTPUT=$(docker exec pihole-unbound pihole allow "$DOMAIN" 2>&1)
if echo "$OUTPUT" | grep -q "Removed"; then
    printf "OK: Domain %s removed from blocklist.\n" "$DOMAIN"
    printf "$(date -Iseconds) | security_agent | PI_HOLE_ALLOW | %s\n" "$DOMAIN" >> ~/mycelial/logs/audit.log
    exit 0
else
    printf "ERROR: Failed to remove %s. Output: %s\n" "$DOMAIN" "$OUTPUT"
    exit 1
fi
