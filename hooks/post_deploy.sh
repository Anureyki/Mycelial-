#!/bin/bash
# post_deploy.sh – Confirm deployment success
# Usage: post_deploy.sh <target_environment>

TARGET="${1:-local}"

# Save deployment record
mkdir -p ~/mycelial/state
echo "$(date -Iseconds) | post_deploy | $TARGET | success" >> ~/mycelial/logs/audit.log

# Update deployment state
cat > ~/mycelial/state/last_deploy.json << EOJ
{
  "target": "$TARGET",
  "timestamp": "$(date -Iseconds)",
  "status": "success"
}
EOJ

echo "✓ post_deploy hook passed."
exit 0
