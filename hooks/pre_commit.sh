#!/bin/bash
# pre_commit.sh – Validate before Git commit
# Usage: pre_commit.sh <commit_message>

MESSAGE="$1"

if [[ -z "$MESSAGE" ]]; then
    echo "ERROR: Commit message required."
    exit 1
fi

# Ensure message has at least 10 characters and includes a subject
if [[ ${#MESSAGE} -lt 10 ]]; then
    echo "ERROR: Commit message too short. Use format: 'feat: Add something'."
    exit 1
fi

# Check for secrets (simple patterns)
if grep -r -E "(password|key|secret|token|DID_PRIVATE)" . 2>/dev/null | grep -v ".git" | grep -v "pre_commit.sh" > /dev/null; then
    echo "WARNING: Potential secret found in repo. Review before committing."
    exit 1
fi

# Ensure all hooks pass (run them automatically)
for hook in ~/mycelial/hooks/pre_*.sh; do
    if [[ "$hook" != "$0" ]]; then
        bash "$hook" || exit 1
    fi
done

echo "✓ pre_commit hook passed."
exit 0
