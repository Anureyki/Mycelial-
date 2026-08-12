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
