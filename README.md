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
| **Self‑healing** | Service Manager restarts failed agents, and a boot unit brings the stack back after a power cycle. |
| **MCP‑native** | Uses the Model Context Protocol for tools, memory, and search. |
| **Self‑improving** | Distillation pipeline collects data and trains smaller, specialized models. |
| **Model‑agnostic** | Agents request a *capability* (`vision`, `reasoning`, `synthesis`), never a vendor. Swapping the brain behind one is a config edit; an external API is optional, never a dependency. |

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
| **Grow** | 9009 | Gardener | Models the *system* as well as the plant (medium, water chemistry, lighting, capacity vs working volume); asks for what it's missing (`check_in`); reasons across readings, assessments and notes together (`assess_plant`); detects feed that stopped scaling, corrects ppm to target without needing volume, and separates uptake from concentration |

---

## ⚙️ Platform Services

| Service | Port | Purpose |
|---------|------|---------|
| Registry | 8004 | Agent discovery and registration |
| Inference | 8005 | Capability-routed inference (`vision`/`reasoning`/`synthesis`/`code`). Callers name a capability, not a vendor; `config/model_routing.json` resolves it local-first. `GET /capabilities` shows what each resolves to and why anything was skipped |
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

- ✅ **Inference is routed by capability, not vendor.** `grow_agent` had `"model": "claude-sonnet-5"` hardcoded, wiring a core capability to one company's API. Agents now request a capability (`vision`/`reasoning`/`synthesis`/`code`) and the Inference Service resolves it against `config/model_routing.json`. **Zero vendor or model names remain in `agents/` or `core/`.** Chains are local-first and each entry is checked for real usability — an Ollama model must actually be pulled, a cloud provider must have its `requires` env var set — so a missing backend degrades the capability *with a reason* instead of failing opaquely (`GET /capabilities`). When a purpose-trained local model exists, adopting it is one line in that file.
- ✅ **Local, API-free vision.** Added `run_ollama_vision` (Ollama's HTTP `/api/generate` accepts images; the CLI path used for text cannot — the old code's "image input requires a Claude model" was only true because nothing else had been wired). With `moondream` pulled, an uploaded cannabis photo is described locally in ~37 s on CPU with no external API. Hardened against two failure modes found in testing: moondream returns an **empty** completion (HTTP 200, `done_reason=stop`) for prompts containing apostrophes, dash-clauses, or meta-instructions — reproducible, not flaky — so the service retries once with punctuation flattened; and a bare truthiness check accepted `"!!!"` as a successful read, so degenerate output is now rejected rather than passed off as an answer.
- ✅ **Vision no longer invents diagnoses.** Both PlantVillage checkpoints carry 15 classes covering **pepper/potato/tomato only** (verified against `model.names`/`config.id2label`), so every cannabis photo was force-fit to the nearest tomato disease with plausible confidence. `fuse_observations` now takes the species and refuses to classify anything outside `SUPPORTED_SPECIES`. Two follow-on bugs fixed: the vision-escalation tier had **never once run** (`run_claude_inference(model, prompt)` was called with `image_path=`, raising `TypeError`, falling back to the wrong local read while narrating "I double-checked it more carefully"); and the honest refusal text itself tripped the symptom classifier, since "local **disease** models cover only…" contains a problem keyword. Also fixed negation handling — `"no brown slime or rot"` was scoring **critical** because the matcher saw "brown" and "rot" and ignored the "no", dragging reservoir evals to false warnings.
- ✅ **Cross-source reasoning (`assess_plant`).** Everything else in Grow Agent judges one thing in isolation — the vision model sees only pixels, the qualitative classifier sees one sentence, `evaluate_reservoir` scores numbers deterministically. Nothing ever looked at them together, so a conclusion that only emerges from the combination could never be reached. `assess_plant` gathers the full snapshot from memory (readings, stage targets, reservoir/leaf assessments, notes, optional fresh photo) and reasons across it in one pass via a new `synthesis` capability routed to a larger local model, since the 1.5B loses the thread on multi-section prompts. Verified: correctly identified under-feeding from 404 ppm against a 600–900 veg target, and pH 5.72 below the 5.8–6.2 band, matching the grower's own written diagnosis.
- ✅ **Cross-domain data-collection quest skill (`core/quest_manager.py`).** An agent often can't do part of its job because no labelled domain data exists, and collecting it is tedious enough that it never happens; this turns the gap into a progression loop (quests, progress toward a real threshold, XP/levels/streaks). It knows nothing about images — a campaign is "reach N verified examples per label" and the caller supplies the counter — so Legal (labelled clauses), Accounting (categorised transactions) and Maintenance (failure signatures) reuse it unchanged. Two invariants, encoded in `config/skills.json`: the threshold comes from what the downstream trainer actually needs (hitting 100 % must mean *trainable*), and automated candidates never count as progress until a human verifies them, or the game would reward bulk-importing noise. `config/skills.json` is the discovery layer, following the `departments.json` config-over-code convention.
- ✅ **What web search can and cannot check, drawn deliberately.** Search cannot validate an image — querying "cannabis nitrogen deficiency" returns descriptions of it whether or not the photo shows it, dressing wrong answers in citations. But pH/PPM/temp/humidity/light *are* published stage-and-medium-specific numbers: `validate_environment_targets` researches them and compares against both the hardcoded targets and the latest reading, and `verify_growth_stage` cross-checks tracked stage against strain timelines plus days-since-germination. `source_training_candidates` uses search to *propose* condition-specific images into a human review queue — never as training data until accepted. Required fixing `searxng_mcp.py`, which discarded every URL and returned only `results[0]` as a string: added `search_structured` (URLs, multiple results, image category) leaving `search` byte-identical so boss/pqa/legal keep working.
- ✅ Grow Agent also gained plain-language reading capture (Boss was dropping "388 ppm, 21.0c, 6.42 ph" into the generic reasoning fallback instead of logging it) and multi-plant hygiene fixes.
- ✅ **PWA no longer serves a stale shell forever.** The service worker was cache-first against a hardcoded `CACHE` name, and `activate` only evicts caches whose key *differs* — so any UI change shipped without also bumping that constant never reached an installed client. Now network-first with cache fallback; offline still works.

- ✅ **The system looks after itself.** A boot unit brings the whole stack up after a power cycle - previously only two `@reboot` cron lines ran (Registry, *duplicated*, racing for one port) and Anansi was not among them, so a reboot left the system silently gone. Service Manager's restart was a no-op for its entire existence: it matched `agents/<id>.py` while agents launch as `python3 -m agents.X.X`, so nothing was ever auto-restarted despite the claim. The webapp server was owned by nothing and died unnoticed in a memory-pressure kill.
- ✅ **The system can recall what it did.** Operational activity goes to the Logging Service; memory holds domain facts; nothing connected them, so the system recorded everything and could recall none of it - which is why a progress card sat two days stale while the audit log held the full record. `consolidate_audit` distils the trail into memory behind an incremental marker (first run folded 500 entries, second saw only the 2 new ones) and runs every 6h.
- ✅ **Grow Agent models the environment, not just the plant.** System type, medium, water source *and its buffering*, lighting schedule/wattage/mounting, equipment, and capacity as distinct from working volume - a "5 gallon" bucket run at 13L would otherwise be dosed 45% over. Registering a system emits what follows from it: airlift top-feeds stop while the reservoir still looks part full, distilled water has no carbonate buffering so pH swings at low EC, RO/distilled supplies no calcium or magnesium so Cal-Mag is required rather than optional.
- ✅ **Deterministic reasoning over recorded state**, extended: `analyze_consumption` separates the plant feeding from the solution concentrating (nutrient mass is volume x ppm, so falling ppm against falling volume means uptake outran water loss); `adjust_to_target_ppm` closes the gap from a measured reading without needing the volume at all, and reports the volume the reading implies; `recommend_feed` detects components that stopped scaling *by concentration* and applies a catch-up rather than multiplying the lag forward.
- ✅ **Events carry causal context.** Structured records captured measurements and nothing about why - reasoning lived only in free-text notes, invisible to anything reasoning over history. Domain events now carry `reason`, `expected_effect`, `confidence` and explicit `corrects`/`supersedes`, with an `evidence_kind` distinguishing fact / event / reasoning / note / assessment / **correction**. That last one is load-bearing: a recipe recorded as a correction is a mistake being undone, not a baseline, and treating it as one inverted a finding.
- ✅ `check_in` asks for what's missing for *this* plant's stage and system, with the reason each matters here - water temperature because roots sit in solution, pH more often because the source water is unbuffered, pistils rather than a date because an autoflower's feed weighting turns on the plant.
- ✅ Multi-plant tracking, per-plant stage/strain/system/history. Adding a second plant immediately exposed tasks still reading the single-plant slot - a day-zero seed was reported as "veg" and asked for PPM.
- ✅ Batch photo upload. The attach control had `capture="environment"`, which opens the camera and **hides the photo library entirely** on iOS/Android, and lacked `multiple` - both defects, not limits.
- 🔎 **Vision is honest about what it cannot do.** The PlantVillage checkpoints cover pepper/potato/tomato only, so cannabis photos were force-fit to the nearest tomato disease; out-of-scope species are now refused. Three failure modes are documented from live use: misclassification, missing a real feature (a visibly cupped leaf called "healthy"), and **confabulation** - inventing a purpose for observed equipment, which propagated into a wrong recommendation. Structured observations instead of keyword-matched prose is the outstanding fix.

---

## 🔮 Roadmap

- Train the first student model from distillation data.
- **Cannabis vision model — blocked on data, not code.** No public cannabis leaf-health image dataset exists (the Hub carries only trichome microscopy, strain listings, and lab analytes), so grower-supplied images are the only route. `services/vision/dataset_inventory.py` reports readiness and is deliberately refusal-biased: under ~100 images/class a fine-tune scores well in training and fails on real photos, turning an honest "I can't read this" back into a confident wrong answer. Collect via the quest campaign, then write the fine-tune script — not before.
- **Memory ceiling is real.** 7 GB RAM with YOLO + ViT + a vision model + 20-odd processes is genuinely tight; the 3B synthesis model OOM-killed most of the stack on its first run. `llava:7b` sits next in the vision chain (a config edit, no code change) but needs ~5 GB free. This is the strongest concrete argument for the dedicated-hardware phase in `DEPLOYMENT_PROGRESS.md`.
- Adopt the quest skill in Legal/Accounting/Maintenance — the adapters are declared as candidates in `config/skills.json`; each needs only a counter and a justified threshold.
- Record **units** on nutrient entries. `current_nutrients` stores `FloraMicro: 3.0` with no unit; ml/L vs ml/gal differ by 3.79×, which is the difference between a correct mix and a badly wrong one.
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
