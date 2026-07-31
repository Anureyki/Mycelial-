#!/bin/bash
cd ~/mycelial
source venv/bin/activate

# Ensure logs directory exists
mkdir -p logs

# ---------- Platform Services ----------
echo "Starting platform services..."

nohup python3 services/registry/registry_service.py > logs/registry.log 2>&1 &
echo "Registry Service started (port 8004)"

nohup python3 services/memory/service.py > logs/memory.log 2>&1 &
echo "Memory Service started (port 8007)"

nohup python3 services/policy/service.py > logs/policy.log 2>&1 &
echo "Policy Service started (port 8008)"

nohup python3 services/logging_auditing/service.py > logs/logging.log 2>&1 &
echo "Logging Service started (port 8009)"

nohup python3 services/inference/service.py > logs/inference.log 2>&1 &
echo "Inference Service started (port 8005)"

nohup python3 services/model/service.py > logs/model.log 2>&1 &
echo "Model Service started (port 8006)"

nohup python3 services/training/service.py > logs/training.log 2>&1 &
echo "Training Service started (port 8010)"

nohup python3 services/evaluation/service.py > logs/evaluation.log 2>&1 &
echo "Evaluation Service started (port 8011)"

nohup python3 services/data_engineering/service.py > logs/data_engineering.log 2>&1 &
echo "Data Engineering Service started (port 8012)"

nohup python3 services/agent/service.py > logs/agent_service.log 2>&1 &
echo "Agent Service started (port 8013)"

nohup python3 services/service_manager/service.py > logs/service_manager.log 2>&1 &
echo "Service Manager started (port 8014)"

nohup python3 services/tool/service.py > logs/tool.log 2>&1 &
echo "Tool Service started (port 8015)"

# Wait for services to initialize
sleep 5

# ---------- Sync and start agents ----------
echo "Syncing agent configs and starting agents..."
curl -s -X POST "http://localhost:8013/sync?create_config=true" > /dev/null

echo "All services and agents started."
