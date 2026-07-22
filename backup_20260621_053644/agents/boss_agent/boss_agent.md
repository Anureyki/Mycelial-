---
agent_id: boss.mycelial
type: Orchestrator & Governance
capabilities:
  - fl_orchestrate
  - fl_orchestrate
  - show_pending
  - approve
  - deny
  - interactive_pending
  - interactive_pending
  - delegate
  - check_updates
  - health_check
  - set_autostart
  - fl_status
  - fl_start
  - fl_stop
  - fl_train
  - sync_all
  - check_desync
  - agent_status
  - discover
  - learn
  - security_recommend
  - show_pending
  - approve
  - deny
hooks: {}
permissions:
  - read: ~/mycelial/**
  - write: ~/mycelial/state/**
  - execute: ~/mycelial/agents/*.py
---
# Boss Agent – Mycelial Neural Network Controller

**Agent ID:** `boss.mycelial`  
**Type:** Orchestrator & Governance Agent  
**Status:** Active (Production)

---

## 🧠 Vision: The Mycelial Neural Network

The mycelial network is **not** a collection of independent tools – it is a **distributed neural network** where each agent functions as a **neuron**.

- Each agent (neuron) has a specific role (sensing, processing, acting).
- Agents communicate via **synaptic signals** – messages, tasks, and shared state.
- The network learns and adapts: successes and failures propagate through the system, strengthening or pruning connections.

The **Boss agent** acts as a **hub neuron** – it orchestrates high‑level goals, but the network is designed to eventually function without a single point of control.

---

## 🛡️ Human‑in‑the‑Loop Permission System

The Boss is the **gatekeeper** for all inter‑agent delegation and sensitive actions.

- **Agents request permission** before delegating tasks to other agents.
- **Boss prompts the human** (you) with the request details.
- **You approve or deny** – the decision is logged and cached for future use.

### Permission Workflow

1. Agent A wants Agent B to execute a task.
2. Agent A sends a permission request to Boss (via A2A or state file).
3. Boss logs the request and prompts you (interactive session) or stores it in a pending queue.
4. You respond with `yes` or `no` (or via `--task approve <id>` / `--task deny <id>`).
5. Boss relays the decision back to Agent A.
6. Agent A proceeds or cancels the delegation.

### Commands

- `--task show_pending` – view all pending permission requests.
- `--task approve <request_id>` – approve a specific request.
- `--task deny <request_id>` – deny a specific request.

### Default Policy (Non‑interactive Mode)

When the Boss is running without a terminal (e.g., cron or startup script):
- **Unknown requests are denied** (fail‑safe).
- **Previously approved tasks are allowed** (cached by task hash).

---

## 🔧 Capabilities (Current Tasks)

| Task | Description |
|------|-------------|
| `health_check` | Run full system status: updates, Pi‑hole, Docker, agents, autostart services. |
| `delegate` | Pass a task to another agent (subprocess or A2A, subject to permission). |
| `check_updates` | Trigger Data Gatherer to scan for system and blocklist updates. |
| `set_autostart` | Enable/disable autostart for a service (`fl_server`). |
| `fl_status` | Check if Federated Learning server is running. |
| `fl_start` | Start the FL server (with interactive autostart prompt). |
| `fl_stop` | Stop the FL server. |
| `fl_train` | Trigger an FL training round (mode and crop optional). |
| `check_desync` | Compare `.md` capabilities with `.py` tasks and report mismatches. |
| `sync_all` | Regenerate all agent `.py` scripts from their `.md` definitions. |
| `agent_status` | Print a summary of each agent's capabilities, last task, last run, and error count. |
| `discover` | Scan the network for new agents (A2A discovery via registry). |
| `learn` | Analyze outcomes and decide if regeneration or reconfiguration is needed. |
| `security_recommend` | Generate prioritized update recommendations based on vulnerability scans. |
| `show_pending` | List all pending permission requests. |
| `approve` | Approve a specific request (by ID). |
| `deny` | Deny a specific request (by ID). |

---

## 🕒 Autostart Management

Stored in `~/mycelial/state/boss_agent.json`. During `health_check`, the Boss starts any autostart‑enabled service that isn't running.

---

## 🔄 Self‑Evolution & Learning Loop

The Boss learns from task outcomes stored in `~/mycelial/knowledge/`. It can:

1. **Analyze outcomes** – track failures via `agent_status` and `check_desync`.
2. **Trigger regeneration** – if too many failures, run `sync_all` to refresh agent scripts.
3. **Suggest improvements** – (future) modify `.md` files based on patterns.

### Commands

- `~/mycelial/agents/boss_agent.py --task agent_status` – view all agents' capabilities and health.
- `~/mycelial/agents/boss_agent.py --task check_desync` – detect drift.
- `~/mycelial/agents/boss_agent.py --task sync_all` – bring all agents back in sync.
- `~/mycelial/agents/boss_agent.py --task discover` – scan for new agents via A2A.

---

## 🌐 A2A Protocol (Agent2Agent) – Integration Plan

We use the open **Agent2Agent (A2A)** standard (Linux Foundation) as the **synaptic communication protocol** between agents.

- **Agent Cards** – each agent publishes a card describing its capabilities (served via FastAPI).
- **JSON‑RPC 2.0** – the signal format for task delegation and response.
- **Discovery** – agents find each other via a shared registry (`~/mycelial/state/registry.json`).

### Delegation with Permission

When an agent wants to delegate a task to another, it:
1. Checks the registry to find the target.
2. Sends a permission request to the Boss (via A2A).
3. If the Boss approves (after your confirmation), the agent sends the actual task to the target.
4. The target executes and returns the result.

This ensures that no autonomous action happens without your explicit approval.

---

## 📂 State File

Location: `~/mycelial/state/boss_agent.json`

Tracks:
- `last_task` – most recent task executed.
- `last_run` – timestamp of last run.
- `autostart` – dictionary of service → boolean.
- `pending_requests` – list of pending permission requests.
- `approved_tasks` – cache of previously approved tasks (for non‑interactive mode).
- `errors` – list of recent errors.

---

## 📎 Related Files

- `~/mycelial/README.md` – Source of Truth
- `~/mycelial/hooks/*` – Validation and automation scripts
- `~/mycelial/agents/*.md` – Agent definitions
- `~/mycelial/agents/*.py` – Agent implementations (generated from `.md`)
- `~/mycelial/state/` – All agent state files
- `~/mycelial/logs/audit.log` – Central audit trail

---

## 🔄 Version History

| Date | Version | Change |
|------|---------|--------|
| 2026‑06‑16 | 0.8 | Added human‑in‑the‑loop permission system, `show_pending`, `approve`, `deny` tasks; updated capabilities list. |
| 2026‑06‑16 | 0.7 | Integrated neural network vision and A2A protocol; added `discover` and `learn`. |
| 2026‑06‑16 | 0.6 | Added autostart, desync check, sync all, agent status. |
| 2026‑06‑16 | 0.5 | Added `regenerate` and `learn`. |
| 2026‑06‑16 | 0.4 | Added Crontab Management. |
| 2026‑06‑16 | 0.3 | Added system maintenance & update justification. |
| 2026‑06‑16 | 0.2 | Added sub‑agent registry and communication protocol. |
| 2026‑06‑16 | 0.1 | Initial creation. |
- research
- searxng_logs
- query
