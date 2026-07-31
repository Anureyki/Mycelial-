#!/bin/bash
# start_all.sh – Starts all Mycelial services and agents in the correct order

set -e

cd ~/mycelial
source venv/bin/activate

# Create logs directory
mkdir -p logs

# ----------------------------------------------------------------------
# 1. Platform Services
# ----------------------------------------------------------------------
echo "🚀 Starting platform services..."

nohup python3 services/registry/registry_service.py > logs/registry.log 2>&1 &
echo "  ✅ Registry Service (port 8004)"

nohup python3 services/memory/service.py > logs/memory.log 2>&1 &
echo "  ✅ Memory Service (port 8007)"

nohup python3 services/policy/service.py > logs/policy.log 2>&1 &
echo "  ✅ Policy Service (port 8008)"

nohup python3 services/logging_auditing/service.py > logs/logging.log 2>&1 &
echo "  ✅ Logging Service (port 8009)"

nohup python3 services/inference/service.py > logs/inference.log 2>&1 &
echo "  ✅ Inference Service (port 8005)"

nohup python3 services/model/service.py > logs/model.log 2>&1 &
echo "  ✅ Model Service (port 8006)"

nohup python3 services/training/service.py > logs/training.log 2>&1 &
echo "  ✅ Training Service (port 8010)"

nohup python3 services/evaluation/service.py > logs/evaluation.log 2>&1 &
echo "  ✅ Evaluation Service (port 8011)"

nohup python3 services/data_engineering/service.py > logs/data_engineering.log 2>&1 &
echo "  ✅ Data Engineering Service (port 8012)"

# Agent Service is intentionally disabled – it generated stubs
# nohup python3 services/agent/service.py > logs/agent_service.log 2>&1 &
# echo "  ⚠️  Agent Service (disabled)"

nohup python3 services/service_manager/service.py > logs/service_manager.log 2>&1 &
echo "  ✅ Service Manager (port 8014)"

nohup python3 services/tool/service.py > logs/tool.log 2>&1 &
echo "  ✅ Tool Service (port 8015)"

# Wait a moment for services to initialize
sleep 5

# ----------------------------------------------------------------------
# 2. Core Agents
# ----------------------------------------------------------------------
echo ""
echo "🤖 Starting core agents..."

python3 -m agents.boss_agent.boss_agent &
echo "  ✅ Boss Agent (port 8000)"

python3 -m agents.coding_agent.coding_agent &
echo "  ✅ Coding Agent (port 8001)"

python3 -m agents.hermes.hermes_interface &
echo "  ✅ Hermes Agent (port 8002)"

python3 -m agents.maintenance_agent.maintenance_agent &
echo "  ✅ Maintenance Agent (port 8003)"

python3 -m agents.anansi.Anansi &
echo "  ✅ Anansi Interface (port 8081)"

# Optional: Analyzer Agent (if it exists)
if [ -f "agents/analyzer_agent/analyzer_agent.py" ]; then
    python3 -m agents.analyzer_agent.analyzer_agent &
    echo "  ✅ Analyzer Agent (port 9006)"
fi

# Optional: Grow Agent (if it exists)
if [ -f "agents/grow_agent/grow_agent.py" ]; then
    python3 -m agents.grow_agent.grow_agent &
    echo "  ✅ Grow Agent (port 9009)"
fi

# Optional: Legal Agent (if it exists)
if [ -f "agents/legal_agent/legal_agent.py" ]; then
    python3 -m agents.legal_agent.legal_agent &
    echo "  ✅ Legal Agent (port 9011)"
fi

# Optional: Accounting Agent (if it exists)
if [ -f "agents/accounting_agent/accounting_agent.py" ]; then
    python3 -m agents.accounting_agent.accounting_agent &
    echo "  ✅ Accounting Agent (port 9012)"
fi

# Optional: Trust Agent (if it exists)
if [ -f "agents/trust_agent/trust_agent.py" ]; then
    python3 -m agents.trust_agent.trust_agent &
    echo "  ✅ Trust Agent (port 9013)"
fi

# Optional: Security Agent (if it exists) - needed for update_graph token auth
if [ -f "agents/security_agent/security_agent.py" ]; then
    python3 -m agents.security_agent.security_agent &
    echo "  ✅ Security Agent (port 9010)"
fi

# ----------------------------------------------------------------------
# 3. Health Check Summary
# ----------------------------------------------------------------------
sleep 3
echo ""
echo "📋 Health check summary:"
for port in 8004 8007 8008 8009 8005 8006 8010 8011 8012 8014 8015 8000 8001 8002 8003 8081 9006 9009 9010 9011 9012 9013; do
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:$port/health | grep -q 200; then
        echo "  ✅ Port $port is healthy"
    else
        echo "  ❌ Port $port is NOT responding"
    fi
done

echo ""
echo "🎉 All services and agents started."
echo "   Interact via Anansi: curl -X POST http://localhost:8081/execute -H 'Content-Type: application/json' -d '{\"task\":\"process_request\",\"args\":[\"SUTEN XAAT RAA Xephri!\"]}'"
echo "   Grow Agent: curl -X POST http://localhost:9009/execute -H 'Content-Type: application/json' -d '{\"task\":\"log_reading\",\"args\":{\"ph\":5.9,\"ppm\":366,\"temp\":23.4,\"humidity\":68,\"stage\":\"seedling\"}}'"
