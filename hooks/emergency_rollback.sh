#!/bin/bash
# emergency_rollback.sh – Revert to last known good state
# Usage: emergency_rollback.sh <reason>

REASON="${1:-unknown}"

echo "⚠️ EMERGENCY ROLLBACK triggered. Reason: $REASON"

# Save a recovery point (if Git repo)
if [[ -d ~/AgTechAI/.git ]]; then
    cd ~/AgTechAI
    git stash 2>/dev/null
    git reset --hard HEAD~1 2>/dev/null || echo "No previous commit to rollback to."
    echo "Rolled back to previous Git commit."
fi

# Restore state from last known good backup (if exists)
if [[ -f ~/mycelial/state/backup.json ]]; then
    cp ~/mycelial/state/backup.json ~/mycelial/state/agriculture.json
    echo "Restored state from backup."
fi

# Notify owner
echo "EMERGENCY ROLLBACK: $REASON" >> ~/mycelial/logs/audit.log

# Lock further actions until reviewed
touch ~/mycelial/state/LOCKED
echo "System locked until owner reviews."

exit 0
