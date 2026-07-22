#!/bin/bash
# Interactive approval prompt on login
if [ -f ~/mycelial/agents/boss_agent.py ]; then
    ~/mycelial/agents/boss_agent.py --task interactive_pending
fi
