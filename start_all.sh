#!/bin/bash
# start_all.sh – Starts all Mycelial services and agents in the correct order

set -e

cd ~/mycelial
if [ -f venv/bin/activate ]; then
    source venv/bin/activate
fi

# Load secrets/config from .env (see .env.example) so MCP servers like
# Sentry and CourtListener pick up their tokens via os.environ - the
# services/tool/service.py subprocess env is built from os.environ.copy().
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Create logs directory
mkdir -p logs

# ----------------------------------------------------------------------
# 1. Platform Services
# ----------------------------------------------------------------------
echo "🚀 Starting platform services..."

nohup python3 -u services/registry/registry_service.py > logs/registry.log 2>&1 &
echo "  ✅ Registry Service (port 8004)"

nohup python3 -u services/memory/service.py > logs/memory.log 2>&1 &
echo "  ✅ Memory Service (port 8007)"

nohup python3 -u services/policy/service.py > logs/policy.log 2>&1 &
echo "  ✅ Policy Service (port 8008)"

nohup python3 -u services/logging_auditing/service.py > logs/logging.log 2>&1 &
echo "  ✅ Logging Service (port 8009)"

nohup python3 -u services/inference/service.py > logs/inference.log 2>&1 &
echo "  ✅ Inference Service (port 8005)"

nohup python3 -u services/model/service.py > logs/model.log 2>&1 &
echo "  ✅ Model Service (port 8006)"

nohup python3 -u services/training/service.py > logs/training.log 2>&1 &
echo "  ✅ Training Service (port 8010)"

nohup python3 -u services/evaluation/service.py > logs/evaluation.log 2>&1 &
echo "  ✅ Evaluation Service (port 8011)"

nohup python3 -u services/data_engineering/service.py > logs/data_engineering.log 2>&1 &
echo "  ✅ Data Engineering Service (port 8012)"

# Agent Service is intentionally disabled – it generated stubs
# nohup python3 services/agent/service.py > logs/agent_service.log 2>&1 &
# echo "  ⚠️  Agent Service (disabled)"

nohup python3 -u services/service_manager/service.py > logs/service_manager.log 2>&1 &
echo "  ✅ Service Manager (port 8014)"

nohup python3 -u services/tool/service.py > logs/tool.log 2>&1 &
echo "  ✅ Tool Service (port 8015)"

nohup python3 -u services/provenance/service.py > logs/provenance.log 2>&1 &
echo "  ✅ Provenance Service (port 8016)"

nohup python3 -u services/federated/service.py > logs/federated.log 2>&1 &
echo "  ✅ Federated Learning Service (port 8017)"

# Wait a moment for services to initialize
sleep 5

# ----------------------------------------------------------------------
# 2. Core Agents
# ----------------------------------------------------------------------
echo ""
echo "🤖 Starting core agents..."

# Security Agent goes first: core/base_agent asks it to authorize every inbound
# task via check_guard. Guard checks fail open, so a late start is not fatal -
# but until it is up, no guard rules are being enforced.
if [ -f "agents/security_agent/security_agent.py" ]; then
    python3 -u -m agents.security_agent.security_agent >> logs/security_agent.log 2>&1 &
    echo "  ✅ Security Agent (port 9010)"
    sleep 2
fi

python3 -u -m agents.boss_agent.boss_agent >> logs/boss_agent.log 2>&1 &
echo "  ✅ Boss Agent (port 8000)"

python3 -u -m agents.coding_agent.coding_agent >> logs/coding_agent.log 2>&1 &
echo "  ✅ Coding Agent (port 8001)"

python3 -u -m agents.hermes.hermes_interface >> logs/hermes.log 2>&1 &
echo "  ✅ Hermes Agent (port 8002)"

python3 -u -m agents.maintenance_agent.maintenance_agent >> logs/maintenance_agent.log 2>&1 &
echo "  ✅ Maintenance Agent (port 8003)"

python3 -u -m agents.anansi.Anansi >> logs/anansi.log 2>&1 &
echo "  ✅ Anansi Interface (port 8081)"

# Optional: Analyzer Agent (if it exists)
if [ -f "agents/analyzer_agent/analyzer_agent.py" ]; then
    python3 -u -m agents.analyzer_agent.analyzer_agent >> logs/analyzer_agent.log 2>&1 &
    echo "  ✅ Analyzer Agent (port 9006)"
fi

# Optional: Grow Agent (if it exists)
if [ -f "agents/grow_agent/grow_agent.py" ]; then
    python3 -u -m agents.grow_agent.grow_agent >> logs/grow_agent.log 2>&1 &
    echo "  ✅ Grow Agent (port 9009)"
fi

# Optional: Legal Agent (if it exists)
if [ -f "agents/legal_agent/legal_agent.py" ]; then
    python3 -u -m agents.legal_agent.legal_agent >> logs/legal_agent.log 2>&1 &
    echo "  ✅ Legal Agent (port 9011)"
fi

# Optional: Accounting Agent (if it exists)
if [ -f "agents/accounting_agent/accounting_agent.py" ]; then
    python3 -u -m agents.accounting_agent.accounting_agent >> logs/accounting_agent.log 2>&1 &
    echo "  ✅ Accounting Agent (port 9012)"
fi

# Optional: Trust Agent (if it exists)
if [ -f "agents/trust_agent/trust_agent.py" ]; then
    python3 -u -m agents.trust_agent.trust_agent >> logs/trust_agent.log 2>&1 &
    echo "  ✅ Trust Agent (port 9013)"
fi

# Department heads (ag_agent, and future domain heads) are deliberately NOT
# started here. They are meant to wake on demand once the wake-word / UX layer
# exists. Start one manually when you need it:
#   python3 -m agents.ag_agent.agriculture_agent &

# Optional: PQA Agent (if it exists) - public web search/browse via SearXNG + Puppeteer
if [ -f "agents/pqa_agent/pqa_agent.py" ]; then
    python3 -u -m agents.pqa_agent.pqa_agent >> logs/pqa_agent.log 2>&1 &
    echo "  ✅ PQA Agent (port 9007)"
fi

# ----------------------------------------------------------------------
# 3. Health Check Summary
# ----------------------------------------------------------------------
sleep 3
echo ""
echo "📋 Health check summary:"

# ----------------------------------------------------------------------
# The web client is served by nginx on 8443 over TLS with basic auth - see
# config/nginx/mycelial.conf. It used to be served here by
# `python3 -m http.server 8090 --bind 0.0.0.0`, which put the PWA on the LAN
# in the clear alongside a socat forwarder exposing Anansi on 9081. Both are
# retired: one authenticated TLS listener replaces two unauthenticated
# plaintext ones.
# ----------------------------------------------------------------------

for port in 8004 8007 8008 8009 8005 8006 8010 8011 8012 8014 8015 8016 8017 8000 8001 8002 8003 8081 9006 9007 9009 9010 9011 9012 9013; do
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:$port/health | grep -q 200; then
        echo "  ✅ Port $port is healthy"
    else
        echo "  ❌ Port $port is NOT responding"
    fi
done

echo ""
# ----------------------------------------------------------------------
# TLS reverse proxy (Phase 6) - the only thing meant to face the LAN.
# ----------------------------------------------------------------------
# Unprivileged nginx on 8443, serving the webapp and proxying /execute to
# Anansi behind TLS and basic auth. Everything it writes lives under state/,
# so this needs no sudo and does not touch the system nginx on :80.
if [ -f config/nginx/mycelial.conf ] && command -v nginx >/dev/null 2>&1; then
    if [ -f state/nginx/nginx.pid ] && kill -0 "$(cat state/nginx/nginx.pid)" 2>/dev/null; then
        echo "  ✅ TLS proxy already running (port 8443)"
    elif nginx -t -c "$PWD/config/nginx/mycelial.conf" -p "$PWD" >/dev/null 2>&1; then
        nginx -c "$PWD/config/nginx/mycelial.conf" -p "$PWD" && \
            echo "  ✅ TLS proxy (port 8443, https)" || \
            echo "  ⚠️  TLS proxy failed to start - see logs/nginx_error.log"
    else
        echo "  ⚠️  TLS proxy config invalid - see: nginx -t -c $PWD/config/nginx/mycelial.conf -p $PWD"
    fi
fi

echo "🎉 All services and agents started."
echo "   Web client: https://localhost:8443/  (TLS + basic auth - the only LAN door)"
echo "   Interact via Anansi: curl -X POST http://localhost:8081/execute -H 'Content-Type: application/json' -d '{\"task\":\"process_request\",\"args\":[\"SUTEN XAAT RAA Xephri!\"]}'"
echo "   Grow Agent: curl -X POST http://localhost:9009/execute -H 'Content-Type: application/json' -d '{\"task\":\"log_reading\",\"args\":{\"ph\":5.9,\"ppm\":366,\"temp\":23.4,\"humidity\":68,\"stage\":\"seedling\"}}'"
