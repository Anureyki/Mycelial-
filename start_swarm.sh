#!/bin/bash
cd ~/mycelial
source venv/bin/activate

# Start Registry Service
nohup python3 agents/registry_service.py > logs/registry.log 2>&1 &
echo "Registry Service started (port 8004)"

# Start Hermes
nohup python3 agents/hermes_interface.py > logs/hermes.log 2>&1 &
echo "Hermes started (port 8002)"

# Start CodingAgent
nohup python3 agents/boss_agent/codingagent.py > logs/coding.log 2>&1 &
echo "CodingAgent started (port 8001)"

# Start Boss
nohup python3 agents/boss_agent/boss_agent.py > logs/boss.log 2>&1 &
echo "Boss started (port 8000)"

# Start Anansi (interface)
nohup python3 agents/Anansi.py > logs/anansi.log 2>&1 &
echo "Anansi started (port 8081)"

echo "All agents started."
