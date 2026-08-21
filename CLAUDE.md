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
- **Check health (startup/infra level):** `./start_all.sh` (it runs a per-port health check at the end)
- **Check health (status board):** ask Anansi "system status" for a plain-language recap of every agent's health and active projects, or open the Dashboard tab in `webapp/` for the same as live cards (System / Grow / Progress). This is the day-to-day way to check on things - `startup_health.sh` (a dead script pointing at a pre-refactor flat file path) was removed in favor of it.
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
| Provenance | 8016 | Artifact lineage and origin classification |
| Federated Learning | 8017 | Flower server lifecycle + FedAvg (gRPC on 9092) |

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

## Working principle: agents are the students

Claude is the **master teacher** on this platform; the agents are the students.
Domain work belongs to the domain agent.

- **Ask the agent, don't compute for it.** For anything agricultural, query Grow
  Agent and relay what it returns. Same for Legal, Accounting, Maintenance in
  their domains.
- **If the agent can't do it yet, build the capability** - that is the teaching.
  Substituting your own arithmetic leaves the agent exactly as capable as it was.
- **Intervene only on a real error** in its math or algorithm, and then fix the
  algorithm rather than papering over it with a hand-computed number.
- **On disagreement the agent's derivation wins**, unless its algorithm is
  demonstrably wrong - it is the one carrying reasoning and history.

The failure this prevents is two sources of truth. A Cal-Mag dose was once quoted
as 9.0 ml (hand estimate) and 8.7 ml (the agent's derivation) in consecutive
messages; the hand figure was the wrong one and had no reasoning attached.

Models underneath are interchangeable by design (see `config/model_routing.json`)
- the point of accruing capability in the agent rather than in a conversation is
that it survives a model swap, a fine-tune, or a replacement model entirely.

## Ownership model: who owns what

Each agent owns the reasoning for its domain. Nothing else reasons on its behalf,
and it does not reason outside its domain. This is the same rule for every agent,
not a Grow-specific arrangement.

| Layer | Owns |
|-------|------|
| **Domain agents** (Grow, Legal, Accounting, Maintenance, Security, Coding, Analyzer) | Reasoning, assessment and recommendations inside their own domain |
| **Hermes** | Memory transport and policy enforcement. A broker - it does not interpret, summarise or consolidate domain records |
| **Memory Service** | Evidence and state |
| **Logging Service** | Operational execution history. Kept separate from domain memory |
| **Boss** | Orchestration, routing, cross-domain coordination and threshold escalation |
| **Anansi** | Narration and translation |

### Anansi is a storyteller, not a dispatcher

Named for the trickster-storyteller, and the role is the same: translate between
the user's world and the system's. Two directions -

- **Inward:** turn what a person says into what the orchestration layer needs.
- **Outward:** tell the story of what happened, in plain language, without naming
  agents or tasks. "Your reservoir pH has been drifting - I'd adjust it today,"
  never "Grow Agent reported a warning."

### Not every message is a task

Some inputs are simply conversation. Those do not need an agent, a task, or a
memory write. A quick SearXNG lookup is often the whole answer - for example when
a factual claim in conversation is worth checking. Routing a chat turn to a
domain agent because it contains a keyword is a failure mode, not thoroughness.

The test is whether the subject is **domain-specific**. If it is, it belongs to
that domain's agent. If it is not, answer it and move on without persisting
anything.

### Cross-domain findings

A domain agent may surface something that belongs to another domain - Legal
spotting a payment obligation inside a contract that Accounting needs, or
Accounting seeing a disbursement that implies a legal instrument. The finding
travels agent-to-agent as a minimal structured payload (never a raw document
dump), and Boss gates on thresholds rather than mediating the consultation
itself. See `recommend_purchase` in `agents/grow_agent/grow_agent.py` for the
established shape.

### Hardware is behind an authorization boundary

"The agent believes this should happen" and "the system is authorised to make it
happen" are different states. Recommendations flow through Boss for
authorisation before reaching any control service or device. No agent holds
direct actuation authority.

## Guards (replaces the retired `hooks/`)

Every inbound `/execute` passes through `AgentBase.check_guard()`, which asks the Security Agent (9010) to authorize it. Deny rules live in `config/guards.json` (denylist — no matching rule means allowed).

- **Edit rules:** change `config/guards.json`, then `curl -X POST localhost:9010/execute -d '{"task":"reload_guards","args":{}}'` — no restart needed.
- **Kill switch:** `touch state/LOCKED` denies everything until removed.
- **Fails open:** if the Security Agent is unreachable the request is allowed and a warning is logged — an outage must not halt the swarm. Only an explicit `allowed: false` denies.
- Denials return HTTP 403 and a `GUARD_DENY` row in the audit log.

## Federated Learning

```bash
curl -X POST localhost:8017/start -d '{"rounds":3,"min_clients":2}'   # Flower gRPC on 9092
python3 services/federated/client/fl_client.py --mode synth --node-id node1
curl localhost:8017/status          # round progress while running
curl localhost:8017/rounds          # per-round aggregated metrics
curl -X POST localhost:8017/stop
```
Use `--mode real` to train on `~/grower-node/sensor_data/*.csv`. To deploy a client to a remote node, ship `services/federated/client/fl_client.py` **and** `services/federated/model.py` together.

## On-demand agents (not in `start_all.sh`)

Department heads wake on demand rather than at boot, pending the wake-word/UX layer:
```bash
python3 -m agents.ag_agent.agriculture_agent &     # agriculture dept head, port 9015
```
`ag_agent` aggregates its department (roster in `config/departments.json`) and reviews FL results via `review_improvements`. `quantum_agent` (9014) is likewise implemented but unstarted.

## Environment Variables
- `ANTHROPIC_API_KEY` – for Claude models (optional)
- `SENTRY_ACCESS_TOKEN` – for Sentry MCP (optional)
- `COURTLISTENER_API_TOKEN` – for CourtListener MCP, used by the Legal Agent (optional)
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
