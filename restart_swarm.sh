#!/bin/bash
# restart_swarm.sh – Sync configs and restart all agents

echo "🔄 Syncing agent configs..."
curl -s -X POST "http://localhost:8013/sync?create_config=true" > /dev/null

echo "🛑 Restarting all agents..."
curl -s -X POST http://localhost:8014/restart_all > /dev/null

echo "✅ Swarm restarted."
