#!/bin/bash
# post_commit.sh – Log commit and update state
# Usage: post_commit.sh <commit_hash>

HASH="$1"

if [[ -z "$HASH" ]]; then
    HASH=$(git rev-parse HEAD 2>/dev/null)
fi

if [[ -z "$HASH" ]]; then
    echo "WARNING: Could not detect commit hash. Logging partial."
    HASH="unknown"
fi

# Update state file
mkdir -p ~/mycelial/state
cat > ~/mycelial/state/last_commit.json << EOJ
{
  "commit": "$HASH",
  "timestamp": "$(date -Iseconds)",
  "author": "codingagent",
  "message": "$(git log -1 --pretty=%B 2>/dev/null | head -1)"
}
EOJ

echo "$(date -Iseconds) | post_commit | $HASH" >> ~/mycelial/logs/audit.log

echo "✓ post_commit hook passed. Commit logged."
exit 0
