#!/bin/bash
# pi-hole-block.sh – Add domain to Pi‑hole blocklist using `pihole deny`
# Usage: pi-hole-block.sh <domain> [reason]

DOMAIN="$1"
REASON="${2:-No reason provided}"

if [[ -z "$DOMAIN" ]]; then
    printf "ERROR: No domain specified.\n"
    exit 1
fi

# Check if Pi‑hole container is running
if ! docker ps --filter "name=pihole-unbound" --filter "status=running" --format "{{.Names}}" | grep -q "pihole-unbound"; then
    printf "ERROR: Pi‑hole container 'pihole-unbound' is not running.\n"
    exit 1
fi

# Check if domain is already blocked (via gravity database)
if docker exec pihole-unbound pihole deny "$DOMAIN" 2>&1 | grep -q "already"; then
    printf "WARNING: Domain %s already blocked.\n" "$DOMAIN"
    exit 0
fi

# Add domain via `pihole deny`
OUTPUT=$(docker exec pihole-unbound pihole deny "$DOMAIN" 2>&1)
if echo "$OUTPUT" | grep -q "Added"; then
    printf "OK: Domain %s added to Pi‑hole blocklist.\n" "$DOMAIN"
    printf "$(date -Iseconds) | security_agent | PI_HOLE_BLOCK | %s | %s\n" "$DOMAIN" "$REASON" >> ~/mycelial/logs/audit.log
    exit 0
else
    printf "ERROR: Failed to add domain %s. Output: %s\n" "$DOMAIN" "$OUTPUT"
    exit 1
fi
