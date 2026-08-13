# 🍄 Mycelial – A Sovereign Agent Operating Platform

**Mycelial** is a self‑hosted, stateless, agent‑based operating system for autonomous intelligence. It decouples **infrastructure** (services) from **intelligence** (agents), allowing you to build, deploy, and scale a swarm of specialized workers that communicate, reason, and act.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Stateless-brightgreen)](https://modelcontextprotocol.io)

---

## 🧠 What Makes Mycelial Different

| Feature | What it means |
|---------|---------------|
| **Sovereign** | You own your data, models, and infrastructure. No cloud lock‑in. |
| **Stateless** | Built for horizontal scaling – no session affinity required. |
| **Modular** | Each agent and service has a single responsibility. |
| **Self‑healing** | Service Manager restarts failed agents automatically. |
| **MCP‑native** | Uses the Model Context Protocol for tools, memory, and search. |
| **Self‑improving** | Distillation pipeline collects data and trains smaller, specialized models. |

---

## 🏗️ Architecture Overview

```text
                Human
                  │
             Anansi Interface
                  │
              Boss Agent
                  │
 ──────────────────────────────────
  Agent-to-Agent (A2A) – JSON-RPC
 ──────────────────────────────────
                  │
  Coding       Hermes       Maintenance
  (Software)   (Memory)     (Health)
                  │
 ──────────────────────────────────
      Platform Services Layer
 ──────────────────────────────────

Registry  │  Policy     │  Model
Inference │  Memory     │  Logging
Training  │  Evaluation │  Data Eng.
Agent     │  Service Mgr│  Tool (MCP)

──────────────────────────────────
Runtime Infrastructure
──────────────────────────────────
SQLite       MQTT          Filesystem
Ollama       Ubuntu        Local Sandbox
```

---

## 🤖 Core Agents

| Agent | Port | Role | Capabilities |
|-------|------|------|--------------|
| **Boss** | 8000 | Orchestrator | Delegates tasks, routes requests, manages workflows, maintains the CAG/KAG relationship graph |
| **Coding** | 8001 | Software Engineer | Reads/writes files, runs commands, lints, fixes code, evaluates codebase |
| **Hermes** | 8002 | Memory/Librarian | Stores/retrieves memories, searches documentation |
| **Maintenance** | 8003 | System Health | Disk checks, log cleaning, system updates, error monitoring |
| **Anansi** | 8081 | User Interface | Accepts natural language, routes to Boss |
| **Analyzer** | 9006 | Outcome Analysis | Scans logs, generates recommendations |
| **PQA** | 9007 | Public Query | Public web search fallback, used by other agents when local knowledge is insufficient |
| **Security** | 9010 | Security | Authenticates, authorizes, audits, issues tokens, tracks security findings (`flag_finding`/`list_findings`/`resolve_finding`) |
| **Legal** | 9011 | Legal | Parses contracts, models legal relationships, CourtListener case-law lookups |
| **Accounting** | 9012 | Accounting | Parses financial instruments, assesses tax liability, tracks account balances |
| **Trust** | 9013 | Trust/Estate | Parses trust documents, models trust relationships |
| **Grow** | 9009 | Gardener | Tracks plant growth stages, logs readings, suggests nutrient adjustments |

---

## ⚙️ Platform Services

| Service | Port | Purpose |
|---------|------|---------|
| Registry | 8004 | Agent discovery and registration |
| Inference | 8005 | LLM reasoning (local Ollama models + cloud Claude models) |
| Model | 8006 | Dynamic model selection |
| Memory | 8007 | SQLite versioned storage |
| Policy | 8008 | Decision engine |
| Logging | 8009 | Structured audit logs |
| Training | 8010 | Training job management |
| Evaluation | 8011 | Model evaluation |
| Data Engineering | 8012 | Dataset preprocessing |
| Agent Service | 8013 | *(disabled – stub generator)* |
| Service Manager | 8014 | Process supervision, auto‑restart |
| Tool Service | 8015 | MCP gateway |
| Provenance Service | 8016 | Records who/what created, modified, reviewed, or orchestrated an artifact; artifact lineage, origin classification, integrity verification |
| Federated Learning | 8017 | Flower server lifecycle, FedAvg aggregation, round metrics (gRPC on 9092) |

---

## 🔌 MCP Integration

Mycelial uses the **Model Context Protocol** to connect agents to external tools. All MCP servers run over stdio and are managed by the Tool Service.

| MCP Server | Purpose | Status |
|------------|---------|--------|
| `vestige` | Cross‑session memory (FSRS) | ✅ Active |
| `filesystem` | File operations | ✅ Active |
| `git` | Git operations | ✅ Active |
| `puppeteer` | Browser automation | ✅ Configured |
| `searxng` | Web search | ✅ Active |
| `open-websearch` | Web search (DuckDuckGo backend) | ✅ Configured |
| `grounded-docs` | Documentation search | ⚠️ Needs indexing |
| `courtlistener` | Case‑law lookups (used by Legal Agent) | ✅ Active |
| `a2asearch` | Agent‑to‑agent search/discovery | ✅ Configured |
| `secure-execute` | Sandboxed code execution | ✅ Configured |
| `sentry` | Error monitoring | ✅ Configured |
| `e2b` | Code sandbox | 🔄 Token optional |

---

## 📌 Current State

- ✅ 12 agents running and communicating via A2A, including domain agents for legal, accounting, trust/estate, and security.
- ✅ CAG + KAG layer: per‑agent knowledge caches plus a Boss‑maintained relationship graph.
- ✅ PQA public web search fallback wired across agents.
- ✅ CourtListener case‑law integration in the Legal Agent.
- ✅ Inference Service supports local Ollama models and cloud Claude models.
- ✅ CI workflow (compile + lint checks) on every push/PR.
- ✅ MCP integration with 12 servers.
- ✅ Distillation data collection active.
- ✅ Code evaluation and fixing.
- ✅ Web search and browsing.
- ✅ Memory and logging.
- ✅ Self‑healing via Service Manager.
- ✅ Basic status dashboard (`webapp/`) — live System/Grow/Progress cards; this is the primary day‑to‑day health check now (see `CLAUDE.md`).
- ✅ Docker packaging (Phase 1) built and smoke‑tested — all 12 agents and platform services pass health checks in‑container; not yet cut over to a live deployment.
- ✅ Security Agent now tracks findings persistently (`state/security_findings.json`) via `flag_finding`/`list_findings`/`resolve_finding` — any agent (or a code review) can flag an issue once instead of it being rediscovered on the next audit. All 7 findings from the last audit are now resolved (0 open).
- 🔄 `quantum_agent` now runs real circuits on qiskit's `BasicSimulator` (`run_circuit`, `bell_state`, `random_bits`), on its own port (9014, no collisions) — implemented but not yet started/registered or added to `start_all.sh`.
- ✅ `agents/ag_agent/agriculture_agent.py` is a real, standalone DQN reinforcement-learning agent for grow-room climate control (torch, checkpointed at `models/dqn_model.pth`) — invoked directly via CLI (`--task dqn_train`/`dqn_decide`), not wired into the AgentBase/A2A framework. Its unused AgentBase stub wrapper (`ag_agent.py`, empty capabilities) was removed.
- 🗑️ Removed five dead stub agents that were never implemented beyond boilerplate or a planning doc: `dgta_agent`, `study_agent`, `trustee_agent`, `source_agent`, `source_monitor`. This also resolved the port collision between `dgta_agent` and Security (both hardcoded 9010).
- ✅ Fixed a live bug the audit turned up: crontab was running `agents/source_monitor.py` hourly/every 5 min against a file that no longer exists (broken since the 2026‑06‑21 refactor, silently failing for ~2 months per `logs/source_monitor.log`). The recovered backup copy (`backup_20260621_053644/agents/source_monitor.py`) turned out to be itself incomplete — missing imports and the `monitor` task entirely — so restoring it verbatim wouldn't have worked. Removed the two broken crontab entries instead; historical data in `state/source_monitor/` left in place in case this gets rebuilt. Full details in the resolved Security Agent finding.
- 🗑️ Removed `scripts/generate_agent.py` — the legacy, unused generator (raw FastAPI templates, incompatible with the current Flask+`AgentBase` architecture) that produced the dead stub-agent graveyard cleaned up above. Its port_map's `datagatherer`/`agriculture_agent` entries matched the deleted `dgta_agent`/`ag_agent` `.md` files exactly, confirming it as the origin.
- ✅ Cleaned up the last dangling references to the deleted `dgta_agent`: `chat.py`'s `/agents` list now shows the real 12 registered agent IDs (also fixed a stale `codingagent` typo), `hooks/post_search.sh` no longer hardcodes an agent name into its audit-log line (takes it as an argument now), and `analyzer_agent.md`'s example now references a real agent.
- 🔄 New Provenance Service (port 8016, `core/provenance_schemas.py` + `core/provenance_manager.py`) records how artifacts are created/modified/reviewed/orchestrated by human and agent actors: SHA‑256 artifact hashing, parent‑child lineage (modifications are new child artifact_ids, not in‑place overwrites — the service rejects attempts to change a recorded artifact's hash), origin classification (`HUMAN`/`AI_GENERATED`/`AI_ASSISTED`/`AI_MODIFIED`/`HUMAN_MODIFIED_AI`/`MULTI_AGENT`/`AI_ORCHESTRATED`/`UNKNOWN`, always derived from events, never set manually), and integrity verification (`UNVERIFIED`/`RECORDED`/`VERIFIED`/`INVALID`). `AgentBase.record_provenance_event()` gives any agent a one-call way to emit events. Functionally tested end-to-end (lineage chains, all classification branches, the hash-conflict guard, all four verification states) — this is the **foundation layer only**: no visual seal, no Anansi presentation integration, no Git/GitHub integration, no automated test suite, and no agents have been instrumented to actually call it yet from their business logic. All deliberately deferred to a follow-up pass.

- 🗑️ **Retired `hooks/` entirely (30 shell scripts) and moved its guard logic into the Security Agent.** The whole directory was dead: the only wiring was `core/base_agent.py`'s `run_pre_hook`/`run_post_hook`, which read `pre_hook`/`post_hook` from each agent card — `null` in all 14 cards, so the seam had never fired. Two scripts still pointed at `~/AgTechAI`, the project's pre-rename directory. Replacements: a tokenless `check_guard` task on the Security Agent (denylist in `config/guards.json`, hot-reloadable via `reload_guards`, deliberately **not** reusing `authorize`'s allowlist — that would have denied the 7 agents missing from it), called by `base_agent` on every inbound `/execute` and returning 403 on deny. Guard checks **fail open** on transport error so a Security Agent outage can't halt the swarm. The `state/LOCKED` kill switch that `pre_action.sh` wrote but no Python ever read is now genuinely enforced. `quarantine.sh` and `eliminate.sh` became Security Agent tasks; `eliminate` replaces the old interactive `read -r` double-confirm with an approval file in `state/pending_requests/` and **refuses to delete until a human flips it to `approved`**. Post-action shell appends to `audit.log` became structured `log_to_audit` rows plus a `task.completed` event on the MQTT bus.
- 🗑️ Deleted the dead-code backlog the hooks audit surfaced: `backup_20260621_053644/` (34 files), `boss_agent.py.bak3`, `agents/coding_agent/skills/` (nothing ever loaded it), `codingagent.md`/`.json` (no agent has that `agent_id`, so the card was unloadable), `databases/vector_db.py`, `export_to_obsidian.py`, `voice_listener_simple.py`, `duckduckgo_mcp.py`, `agent-service-template`, `login_prompt.sh`, `PROJECT_MANIFEST.json`, `start_swarm.sh`/`restart_swarm.sh`, and a stray committed `Downloads/` sync artifact. Also removed `chat.py` and `myc` — both were fully broken, invoking `/home/anureyki/AgTechAI/venv/bin/python`, which hasn't existed since the rename; `webapp/` and direct curl to Anansi already cover interaction.
- ✅ **New Federated Learning Service (port 8017)**, replacing `hooks/fl_orchestrator.sh`/`fl_train.sh` and the 5-line `agents/boss_agent/fl_server.py` (whose contents were duplicated twice in one file and which bound `0.0.0.0:8081` — colliding with Anansi). Flower gRPC now runs on **9092**. `POST /start`, `/stop`, `GET /status`, `/clients`, `/rounds`, `/rounds/<n>/metrics`, `/history`. The Flower server runs as a subprocess, not a thread, because `start_server()` blocks until all rounds finish and offers no graceful mid-run stop. `MycelialFedAvg` writes each round to `state/federated/rounds.json` atomically as it completes, so progress is readable while training runs. The salvaged `TinyTransformer`, synthetic data generator, and Laplace DP noise carried over from `models/transformer/fl_client.py`; the Flower wiring was rewritten against the current API (`start_numpy_client` → `start_client(...to_client())`) and the client gained the `evaluate()` method it was missing. Verified end-to-end: 3 rounds × 2 clients, FedAvg aggregating, distributed loss falling 0.2088 → 0.2031 → 0.2005.
- 🔄 **`ag_agent` is now the agriculture department head** (port 9015 — 9014 is `quantum_agent`), converted from a standalone argparse script into a real `AgentBase` agent. It aggregates across its department rather than growing anything itself: `department_status`, `aggregate_readings`, `list_members`, and `review_improvements`, which reads the FL service's rounds and turns them into recommendations (flat accuracy, declining accuracy, unstable spread, rounds run with too few clients). The roster lives in `config/departments.json` — `grow_agent` active, `bee_agent` and `aquaponics_agent` declared but inactive — so adding a member is a config change, not a code change. The DQN (`models/dqn_model.pth`) carried over as `dqn_train`/`dqn_decide`. **Deliberately not in `start_all.sh`**: department heads are meant to wake on demand once the wake-word/UX layer exists.

---

## 🔮 Roadmap

- Train the first student model from distillation data.
- Implement model fallback (student first, teacher fallback).
- Add Apify MCP server for web scraping.
- Extend the existing status dashboard (`webapp/`) beyond the System/Grow/Progress cards.
- Finish wiring `voice_listener.py` into Anansi's request path — the listener exists standalone but isn't integrated yet.
- If source monitoring is wanted again, rebuild it properly (the old implementation was already incomplete before it broke) rather than resurrecting `backup_20260621_053644/agents/source_monitor.py` as-is.
- Wire `agriculture_agent.py`'s DQN logic into the AgentBase/A2A framework (or decide it's fine staying a standalone CLI script), and decide whether to start/register `quantum_agent`.
- Deployment: Phase 1 (Docker packaging) is built and smoke‑tested and pushed, along with hardening every platform service to a localhost-only bind. Remaining: Phase 2 (reverse proxy, TLS, and gating Boss requests through the Security Agent — not started, no code exists for it), Phase 3 (provision a dedicated device — blocked on a hardware purchase decision), Phase 4 (cut over, blocked on Phase 3). See `DEPLOYMENT_PROGRESS.md`.
- KAG relationship archive (`core/graph_manager.py`'s `relationships` table, added via `ingest_relationship()`) is write-only — nothing reads it back. `get_entity_relationships`/`get_project_relationships` still only query the old nodes/edges tables; there's no `get_relationship_by_id` or a wired Boss task to expose one. Doesn't break anything as-is (purely additive), but needs a read path — likely a new `update_graph`/`query_graph` action in `boss_agent.py` plus the corresponding `GraphManager` method — before it's actually useful.

---

**Your Mycelial is alive. Go build something beautiful.** 🍄
