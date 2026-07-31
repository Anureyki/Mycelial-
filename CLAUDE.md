# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Mycelial is a self-hosted, agent-based system that splits **infrastructure** (`services/`) from **intelligence** (`agents/`). Platform services are stateless Flask HTTP microservices on fixed ports (registry, memory, policy, inference, logging, etc.). Agents are Flask processes too, but each wraps `core/base_agent.py:AgentBase` and talks to other agents via A2A (a JSON-RPC 2.0 envelope over HTTP `POST /execute`) and/or MQTT.

There is no build step, no test suite, no linter config, and no `requirements.txt`/`pyproject.toml` in the repo — don't assume any of these exist; check before proposing a command that relies on them.

## Critical: hardcoded `~/mycelial` paths

Every service and agent (`core/base_agent.py`, `services/service_manager/service.py`, `services/agent/service.py`, the `.py` agent files) computes its data/log/config paths from `BASE = os.path.expanduser("~/mycelial")`, **not** from the location of the script or repo root. For any of this to run correctly, the repo must live at `~/mycelial`, or `$HOME` must be set so it resolves there. `chat.py` and `myc` additionally hardcode `/home/anureyki/AgTechAI/venv/bin/python` and `/home/anureyki/mycelial/...` — paths specific to the original developer's machine — so those two entry points are not portable as-is.

## Architecture

### Platform services (`services/<name>/service.py` or `services/registry/registry_service.py`)
Each is an independent Flask app bound to a fixed port (see table below), reading/writing its own state under `~/mycelial/state/`. They call each other over plain HTTP (e.g. agents log to `http://localhost:8009/log`, look up peers via `http://localhost:8004/execute`). `services/service_manager/service.py` (8014) is the process supervisor: it starts/stops/restarts agent processes by shelling out to `agents/<agent_id>/<agent_id>.py`, using the `port` field from `config/agent_configs/<agent_id>.json`, and polls `/health` every 30s to relaunch dead agents. `services/agent/service.py` (8013) is the agent *definition* service — it CRUDs `config/agent_configs/*.json` and can generate a brand-new stub agent script from a template (`generate_agent_script`) for any config that has no matching file (its `/sync` endpoint calls this "reconciling orphans").

### Agents (`agents/<agent_id>/`)
Every agent subclasses `AgentBase` (`core/base_agent.py`), which on construction: loads or creates a JSON "agent card" in `config/agent_cards/` (gitignored, created at runtime — don't expect it to exist in a fresh checkout), registers itself with the Registry Service (10 retries with exponential backoff, falling back to writing `state/registry.json` directly if the service is down), connects to MQTT (`mycelial/agent/<id>/{in,out}`), and starts a Flask server exposing `POST /execute` and `GET /health`. `handle_task(task, args, sender)` is the method subclasses override to implement behavior; the base class's default just echoes "not implemented". `send_a2a(target, task, args)` looks the target up via the registry and POSTs a JSON-RPC envelope to its `/execute`. `run_pre_hook`/`run_post_hook` shell out to scripts named in the agent's card (`pre_hook`/`post_hook` fields) — the actual hook scripts live in `hooks/` (e.g. `pre_edit.sh`, `post_commit.sh`, `pre_train.sh`).

### Real implementation vs. auto-generated stub — read this before editing an agent
Most files at `agents/<agent_id>/<agent_id>.py` (matching the folder name) are **32-line stubs** produced by `generate_agent_script` in `services/agent/service.py` — they have empty `capabilities=[]` and a `handle_task` that just says "executed by `<agent_id>`" with a `# Add your custom logic here` placeholder. This is true for `boss_agent`, `coding_agent`, `maintenance_agent`, `security_agent`, `legal_agent`, `pqa_agent`, `dgta_agent`, `analyzer_agent`, `quantum_agent`, `study_agent`, `source_agent`, `source_monitor`, `skills`, `ag_agent`, and the lowercase `agents/anansi/anansi.py` / `agents/hermes/hermes.py`.

The **real, hand-written** implementations live under different filenames in the same directories, with their own hardcoded ports that do *not* match the sibling stub or the `config/agent_configs/*.json` port:
- `agents/anansi/Anansi.py` (capital A) — port `8081`, role `interface`, capability `process_request`. This is the one referenced by the README's "Anansi" port.
- `agents/hermes/hermes_interface.py` — port `8002`, role `memory`, capabilities `store_memory`/`retrieve_memory`/`knowledge_search`/`update_memory`/`forget_memory`/`pin_memory`, talks to the Memory Service (8007) and Policy Service (8008).
- `agents/boss_agent/fl_server.py`, `agents/ag_agent/agriculture_agent.py` — additional hand-written logic (federated-learning server, DQN agriculture agent using `models/dqn_model.pth`) alongside the stub `boss_agent.py`/`ag_agent.py`.
- `agents/boss_agent/codingagent.md` is documentation, not the coding agent's code — `agents/coding_agent/coding_agent.py` is still the stub.

Before assuming an agent "does" something, check whether you're looking at the stub or the real file. `config/agent_configs/hermes.json` is the only hand-authored config (real capabilities); the rest are marked `"description": "Auto-generated from orphan"` with empty capabilities.

### Ports actually implemented
Platform services (from `app.run(port=...)` in each `services/*/service.py`):

| Service | Port |
|---|---|
| Registry | 8004 |
| Inference | 8005 |
| Model | 8006 |
| Memory | 8007 |
| Policy | 8008 |
| Logging/Auditing | 8009 |
| Training | 8010 |
| Evaluation | 8011 |
| Data Engineering | 8012 |
| Agent (definitions) | 8013 |
| Service Manager | 8014 |

There is **no** Tool Service (8015) or MCP gateway in this codebase — no `config/mcp.d/`, no `services/tool*` — despite older docs/README describing one. Treat MCP integration as unimplemented/roadmap, not current state.

Agents (port hardcoded in each agent's own `__init__`, which is authoritative over the config JSON): Anansi `8081`, Hermes `8002`, and the rest are 9000-series stub ports assigned when generated (`coding_agent` 9000, `skills` 9001, `boss_agent` 9002, `security_agent` 9003, `source_monitor` 9004, `source_agent` 9005, `pqa_agent` 9006, `ag_agent` 9007, `anansi` (stub) 9008, `quantum_agent` 9009, `dgta_agent` 9010, `maintenance_agent` 9011, `legal_agent` 9012, `hermes` (stub) 9013, `study_agent` 9014, `analyzer_agent` 9015).

## Running things

There is no verified single "start everything" command in this snapshot. `start_swarm.sh` and `myc` predate the current directory layout and point at paths that no longer exist (e.g. `agents/registry_service.py`, `agents/hermes_interface.py`, `agents/boss_agent/codingagent.py`, `agents/Anansi.py` at the top level) — don't rely on them without fixing the paths first. `restart_swarm.sh` only works once the Agent Service (8013) and Service Manager (8014) are already running, since it just calls their `/sync` and `/restart_all` HTTP endpoints.

To start something directly, run its real file with `python3` from `~/mycelial` (backgrounded with `nohup ... &`, logging to `logs/<name>.log`), e.g.:
```
python3 services/registry/registry_service.py &
python3 services/memory/service.py &
python3 services/service_manager/service.py &
python3 agents/hermes/hermes_interface.py &
python3 agents/anansi/Anansi.py &
```
Debug a single agent/service by running it in the foreground (no `&`) to see the traceback directly; check `logs/<name>.log` otherwise.

## Config and state conventions

- `config/agent_configs/<id>.json` — declarative agent metadata (`port`, `capabilities`, `role`) read by Service Manager/Agent Service. Mostly auto-generated stubs; edit `capabilities` here if you want the Registry to advertise them, but it won't add behavior — that still has to be written into the agent's `handle_task`.
- `config/agent_cards/` — created at runtime by `AgentBase`, not checked in (gitignored). Holds the same shape as the above plus `pre_hook`/`post_hook` paths and MQTT topic names.
- `config/policies.json` — memory retention rules per namespace (`pin`/`ttl_days` for `conversation`, `legal`, `contract`, `model_checkpoint`, `project_memory`), the model-routing table (`general` → `qwen2.5:1.5b`, `coding` → `deepseek-coder:1.3b`, `legal` → `qwen2.5:7b`), and training/evaluation defaults.
- `state/` — runtime JSON registries (`registry.json`, per-agent `.bak` snapshots), `source_monitor/` file hashes and reports, `searches/` query snapshots, `updates/` package-update summaries. Most of this is gitignored (see `.gitignore`) but some sample files are currently committed.
- `hooks/*.sh` — lifecycle scripts referenced by agent cards' `pre_hook`/`post_hook`, plus standalone ops scripts (`fl_orchestrator.sh`, `emergency_rollback.sh`, `quarantine.sh`, `pi-hole-*.sh`, `crontab_*.sh`).

## Environment variables

- `ANTHROPIC_API_KEY` – for Claude models (optional)
- `SENTRY_AUTH_TOKEN` – for Sentry MCP (optional; no Sentry integration is actually wired into `services/` currently)
- `INFERENCE_MODEL` – default model for the Inference Service (default: `qwen2.5:1.5b`)
- `OLLAMA_KEEP_ALIVE` – how long Ollama keeps a model loaded (default: `5m`)
