#!/bin/bash
# crontab_add.sh – Add a cron job
# Usage: crontab_add.sh "<schedule>" "<command>"

SCHEDULE="$1"
COMMAND="$2"

if [[ -z "$SCHEDULE" ]] || [[ -z "$COMMAND" ]]; then
    printf "ERROR: crontab_add requires schedule and command.\n"
    exit 1
fi

# Add to crontab if not already present
(crontab -l 2>/dev/null; echo "$SCHEDULE $COMMAND") | crontab -
printf "OK: Cron job added: %s %s\n" "$SCHEDULE" "$COMMAND"
printf "$(date -Iseconds) | coding_agent | CRONTAB_ADD | %s %s\n" "$SCHEDULE" "$COMMAND" >> ~/mycelial/logs/audit.log
exit 0
