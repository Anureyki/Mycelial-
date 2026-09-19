# Roster: what runs, and how many of it

Every module MycOS starts, what port it holds, and whether it is meant to be
up all the time or woken on demand. Written to answer one question without
reading four files: **is this thing supposed to be running right now?**

This file is the operational census. What each agent *owns* is in `README.md`
and `docs/system-map.html`; what each one *is for* is in `CLAUDE.md`. This
file deliberately does not repeat term counts or capability lists, because a
number copied into a fourth place is a number that drifts in a fourth place.

Verified against the repository on 2026-09-19: `start_all.sh`,
`config/agent_configs/*.json`, `tools/check_singleton.py`, and the `port=`
argument each agent and service passes at construction.

---

## The three states

| State | Meaning | `down` is |
|-------|---------|-----------|
| **always-on** | `start_all.sh` launches it; exactly one process, forever | a fault |
| **on-demand** | implemented, deliberately absent from `start_all.sh` | its normal state |
| **disabled** | present in the tree, not launched by anything | expected |

There is no fourth state for "running twice". One process per agent is the
rule (`tools/check_singleton.py`), and the reason is not tidiness: MQTT is
connected in `AgentBase.__init__` *before* the HTTP server binds, so the
duplicate that loses the port is still on the bus and invisible over HTTP. On
Grow that means a second subscriber to `mycelial/sensor/+/reading`,
double-counting into a record whose uptake figures are differences between
consecutive rows.

---

## Agents — always-on (12)

Each is launched by `start_all.sh` §2 and health-checked at the end of it.

| Agent | Port | Module | Config |
|-------|------|--------|--------|
| Security | 9010 | `agents.security_agent.security_agent` | `security_agent.json` |
| Boss | 8000 | `agents.boss_agent.boss_agent` | `boss_agent.json` |
| Coding | 8001 | `agents.coding_agent.coding_agent` | `coding_agent.json` |
| Hermes | 8002 | `agents.hermes.hermes_interface` | `hermes.json` |
| Maintenance | 8003 | `agents.maintenance_agent.maintenance_agent` | `maintenance_agent.json` |
| Anansi | 8081 | `agents.anansi.Anansi` | `anansi.json` |
| Analyzer | 9006 | `agents.analyzer_agent.analyzer_agent` | **none** |
| PQA | 9007 | `agents.pqa_agent.pqa_agent` | **none** |
| Grow | 9009 | `agents.grow_agent.grow_agent` | `grow_agent.json` |
| Legal | 9011 | `agents.legal_agent.legal_agent` | `legal_agent.json` |
| Accounting | 9012 | `agents.accounting_agent.accounting_agent` | `accounting_agent.json` |
| Trust | 9013 | `agents.trust_agent.trust_agent` | `trust_agent.json` |

Security is started first and given two seconds, because `check_guard` asks it
to authorise every inbound `/execute`. Guard checks fail open, so a late start
is not fatal — but until it answers, no guard rule is being enforced.

## Agents — on-demand (3)

Implemented, never started at boot. `down` is a fact about them, not a fault.

| Agent | Port | Start it with | Config |
|-------|------|---------------|--------|
| Trading | 9016 | `python3 -m agents.trading_agent.trading_agent &` | `trading_agent.json` |
| Quantum | 9014 | `python3 -m agents.quantum_agent.quantum_agent &` | **none** |
| Agriculture (dept head) | 9015 | `python3 -m agents.ag_agent.agriculture_agent &` | **none** |

`ag_agent` aggregates the agriculture department; its roster is
`config/departments.json`, where `grow_agent` is the only active member.

Trading has no execution verbs. See CLAUDE.md, *Capital actuation* — the
exception to the hardware authorisation boundary is scoped by direction, not
by trust.

---

## Services — always-on (13)

All launched by `start_all.sh` §1, five seconds before the agents.

| Service | Port | Module |
|---------|------|--------|
| Registry | 8004 | `services/registry/registry_service.py` |
| Inference | 8005 | `services/inference/service.py` |
| Model | 8006 | `services/model/service.py` |
| Memory | 8007 | `services/memory/service.py` |
| Policy | 8008 | `services/policy/service.py` |
| Logging / Auditing | 8009 | `services/logging_auditing/service.py` |
| Training | 8010 | `services/training/service.py` |
| Evaluation | 8011 | `services/evaluation/service.py` |
| Data Engineering | 8012 | `services/data_engineering/service.py` |
| Service Manager | 8014 | `services/service_manager/service.py` |
| Tool (MCP gateway) | 8015 | `services/tool/service.py` |
| Provenance | 8016 | `services/provenance/service.py` |
| Federated Learning | 8017 | `services/federated/service.py` |

**Service Manager is always-on but acts only on demand.** It runs no restart
loop. `POST /heal` checks and restarts; `GET /scope` says what it will touch.
Its scope is five agents — `anansi`, `boss_agent`, `coding_agent`,
`maintenance_agent`, `security_agent` — and it refuses every other agent id on
`/start`, `/stop`, `/restart` and `/heal`. That is the whole list; nothing
supervises the other seven always-on agents.

**Federated Learning holds 8017 always and 9092 only while training.** The
Flower gRPC server on 9092 starts on `POST /start` and stops on `POST /stop`.

## Services — not launched

| Path | State | Why |
|------|-------|-----|
| `services/agent/service.py.disabled` | disabled | generated stubs; commented out in `start_all.sh` |
| `services/devices/drivers/` | library | device HAL, imported not served |
| `services/mcp_servers/` | subprocess | launched by Tool Service from `config/mcp.d/` |
| `services/vision/` | subprocess | perception pipeline, invoked per call (`VISION_IDLE_RELEASE_SECONDS`) |

## Not a module, still a listener

| Port | What | Started by |
|------|------|------------|
| 8443 | nginx TLS + basic auth, the only LAN door | `start_all.sh` §3, if `nginx` is present |
| 1883 | MQTT broker, connected by every `AgentBase` | external to this repo |

---

## The count

**25 modules always-on: 13 services + 12 agents.** That is the number
`start_all.sh`'s health-check loop probes and the number
`tools/check_singleton.py` should find exactly one process of each.

---

## Four disagreements found while writing this

Recorded rather than smoothed over, because each one is a place where a
reader would get a different answer depending on which file they opened.

1. **`CLAUDE.md`'s Core Agents table omits PQA (9007) and lists Trading (9016)
   as though it were core.** PQA is started by `start_all.sh` on every boot and
   health-checked; Trading is started by nothing. Both entries are backwards.
   `README.md` and `docs/system-map.html` have PQA right, and `README.md`'s
   on-demand line omits Trading.

2. **`start_all.sh`'s `MYCOS_PORTS` guards the wrong port.** It lists 9016 and
   not 9007 — so the double-start refusal does not notice a running PQA agent,
   and `--restart` does not wait for 9007 to clear. That is precisely the hole
   the guard was written to close, for the one agent nobody has been counting.
   The health-check loop twenty lines below has the opposite list (9007 in,
   9016 out), which is the correct one.

3. **`tools/check_singleton.py`'s `EXPECTED` has 11 entries, not 12** — PQA is
   missing. The tool that exists to prove one-process-per-agent does not look
   at one of the agents.

4. **Analyzer, PQA and Quantum have no `config/agent_configs/` entry, while
   Trading — which never starts — has one.** `CLAUDE.md` says that directory is
   authoritative for ports. For three agents there is nothing there to be
   authoritative, and their ports live only in a constructor argument.

None of these is a wrong answer at runtime today. Each is a *true thing that
stopped travelling* between one file and the next — which this repo already
treats as the worse failure, because everything the reader can see is accurate.
