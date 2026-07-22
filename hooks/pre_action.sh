#!/bin/bash
# pre_action.sh – Global hook for ALL agents.
# Checks source of truth, state, and permissions before any action.

AGENT_NAME="${1:-unknown}"
STATE_FILE="$HOME/mycelial/state/${AGENT_NAME}.json"

# 1. Source of truth must exist
if [ ! -f ~/mycelial/README.md ]; then
    printf "FATAL: Source of truth (README.md) missing.\n"
    exit 1
fi

# 2. Create state file if missing
if [ ! -f "$STATE_FILE" ]; then
    printf "WARNING: State file for %s missing. Creating empty state.\n" "$AGENT_NAME"
    echo '{"last_task": null, "errors": []}' > "$STATE_FILE"
fi

# 3. Check if system is locked (emergency rollback state)
if [ -f ~/mycelial/state/LOCKED ]; then
    printf "FATAL: System is LOCKED. Review emergency_rollback.log\n"
    exit 1
fi

# 4. Check disk space
AVAIL=$(df -h ~ | awk 'NR==2 {print $4}')
printf "INFO: Available disk space: %s\n" "$AVAIL"

printf "OK: Pre-action checks passed for %s\n" "$AGENT_NAME"
exit 0
