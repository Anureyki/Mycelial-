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
| **Security** | 9010 | Security | Authenticates, authorizes, audits, issues tokens |
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
| `grounded-docs` | Documentation search | ⚠️ Needs indexing |
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
- ✅ MCP integration with 7+ servers.
- ✅ Distillation data collection active.
- ✅ Code evaluation and fixing.
- ✅ Web search and browsing.
- ✅ Memory and logging.
- ✅ Self‑healing via Service Manager.

---

## 🔮 Roadmap

- Train the first student model from distillation data.
- Implement model fallback (student first, teacher fallback).
- Add Apify MCP server for web scraping.
- Build a web dashboard for agent status.
- Enable voice input via Anansi.
- Deploy as a public API with authentication.

---

**Your Mycelial is alive. Go build something beautiful.** 🍄
