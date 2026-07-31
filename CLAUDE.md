# Mycelial – Project Guide for Claude Code

## Overview
Mycelial is a self‑hosted, stateless, agent‑based operating system for autonomous intelligence. It decouples **infrastructure** (services) from **intelligence** (agents). Agents are autonomous workers that communicate via A2A (JSON‑RPC), use MCP tools, and store memory in Hermes.

**Key directories:**
- `agents/` – all agent implementations (boss, coding, hermes, maintenance, anansi, analyzer, grow, etc.)
- `services/` – platform services (registry, memory, inference, model, tool, training, evaluation, etc.)
- `core/` – base agent class and shared utilities
- `config/` – agent configs and MCP server configs
- `state/` – runtime state (registry, memory DB, processes)
- `logs/` – log files (rotated daily)
- `datasets/` – training/distillation datasets
- `weights/` – local model weights

## Essential Commands
- **Start everything:** `./start_all.sh`
- **Stop everything:** `pkill -f "registry_service|memory/service|policy/service|logging_auditing|inference/service|model/service|training|evaluation|data_engineering|service_manager|tool/service|boss_agent|coding_agent|hermes_interface|maintenance_agent|Anansi|analyzer_agent|grow_agent"`
- **Check health:** `./start_all.sh` (it runs a health check at the end)
- **Interact via Anansi:** `curl -X POST http://localhost:8081/execute -H "Content-Type: application/json" -d '{"task":"process_request","args":["Your question"]}'`
- **Talk to Coding Agent directly:** `curl -X POST http://localhost:8001/execute -H "Content-Type: application/json" -d '{"task":"reason","args":{"prompt":"Your prompt"}}'`

## Core Agents
| Agent | Port | Role | Capabilities |
|-------|------|------|--------------|
| Boss | 8000 | Orchestrator | Delegates tasks, routes requests, manages workflows |
| Coding | 8001 | Software Engineer | Reads/writes files, runs commands, lints, fixes code, evaluates codebase |
| Hermes | 8002 | Memory/Librarian | Stores/retrieves memories, searches documentation |
| Maintenance | 8003 | System Health | Disk checks, log cleaning, system updates, error monitoring |
| Anansi | 8081 | User Interface | Accepts natural language, routes to Boss |
| Analyzer | 9006 | Outcome Analysis | Scans logs, generates recommendations |
| Grow | 9009 | Gardener | Tracks plant growth stages, logs readings, suggests nutrient adjustments |

## Platform Services (Fixed Ports)
| Service | Port | Purpose |
|---------|------|---------|
| Registry | 8004 | Agent discovery and registration |
| Inference | 8005 | LLM reasoning (Ollama) |
| Model | 8006 | Dynamic model selection |
| Memory | 8007 | SQLite versioned storage |
| Policy | 8008 | Decision engine |
| Logging | 8009 | Structured audit logs |
| Training | 8010 | Training job management |
| Evaluation | 8011 | Model evaluation |
| Data Engineering | 8012 | Dataset preprocessing |
| Service Manager | 8014 | Process supervision, auto‑restart |
| Tool Service | 8015 | MCP gateway |

## MCP Integration
MCP servers are configured in `config/mcp.d/`. Currently active:
- `vestige` – cross‑session memory (FSRS)
- `filesystem` – file operations
- `git` – git operations
- `puppeteer` – browser automation
- `searxng` – web search
- `grounded-docs` – documentation search
- `sentry` – error monitoring (if token set)
- `e2b` – code sandbox (optional token)

## Common Development Tasks
- **Add a new agent:** Create a new directory under `agents/`, write a Python class inheriting from `AgentBase`, add a config in `config/agent_configs/` with `entry_point`, and restart.
- **Add a new MCP tool:** Create a JSON config in `config/mcp.d/` and reload the Tool Service: `curl -X POST http://localhost:8015/reload`.
- **Debug an agent:** Run it in the foreground (without `&`) to see tracebacks.
- **View logs:** `tail -f logs/<agent>.log` or `tail -f logs/<service>.log`.
- **Restart a single agent:** `pkill -f "agent_name" && python3 -m agents.<agent_name>.<agent_name> &`.

## Environment Variables
- `ANTHROPIC_API_KEY` – for Claude models (optional)
- `SENTRY_AUTH_TOKEN` – for Sentry MCP (optional)
- `INFERENCE_MODEL` – default model for Inference Service (default: `qwen2.5:1.5b`)
- `OLLAMA_KEEP_ALIVE` – keep model loaded in memory (default: 5m)

## Troubleshooting
- **500 errors:** Run the agent/service in foreground to see the traceback.
- **"No module named paho":** Ensure you're inside the venv (`source venv/bin/activate`).
- **Port conflicts:** Use `sudo lsof -i :<port>` to find and kill the process.
- **Hermes errors:** Check that Memory Service (port 8007) is running.

## Deployment
Mycelial runs on a single machine (or VM). To deploy to a cloud server (e.g., DigitalOcean):
- Clone the repo.
- Install dependencies (Ollama, Node.js, Python venv).
- Run `./start_all.sh`.
- Use `nginx` to proxy Anansi (port 8081) to a public domain with HTTPS.

## Current Status (July 2026)
- All core agents and services are functional.
- Distillation data collection is active.
- MCP integration is working.
- Grow Agent is tracking a real plant.
- Analyzer Agent generates recommendations from outcome logs.
- Self‑healing via Service Manager is active.

## Next Steps (as discussed)
- Stabilise core (ongoing).
- Add multi‑tenancy for productisation.
- Build a web dashboard.
- Train student models from distillation data.
- Deploy to DigitalOcean.
