#!/bin/bash
# crontab_remove.sh – Remove a cron job by pattern
# Usage: crontab_remove.sh "<pattern>"

PATTERN="$1"

if [[ -z "$PATTERN" ]]; then
    printf "ERROR: crontab_remove requires a pattern.\n"
    exit 1
fi

# Remove lines matching pattern
crontab -l 2>/dev/null | grep -v "$PATTERN" | crontab -
printf "OK: Removed cron jobs matching pattern: %s\n" "$PATTERN"
printf "$(date -Iseconds) | coding_agent | CRONTAB_REMOVE | %s\n" "$PATTERN" >> ~/mycelial/logs/audit.log
exit 0
