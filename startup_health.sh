#!/bin/bash
# Run Boss health check on login (non‑interactive)
if [ -f ~/mycelial/agents/boss_agent.py ]; then
    ~/mycelial/agents/boss_agent.py --task health_check >> ~/mycelial/logs/login_health.log 2>&1
fi
