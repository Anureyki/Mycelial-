# 🍄 Mycelial — A Sovereign Agent Operating Platform

**Mycelial** is a self-hosted, agent-based operating system for autonomous intelligence, running on one machine you own. It decouples **infrastructure** (services) from **intelligence** (agents).

The organising rule is one sentence: **the orchestrator practises no domain.** It decides *which agent*; the agent decides everything after that.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-stdio-brightgreen)](https://modelcontextprotocol.io)

📊 **[Visual system map](docs/system-map.html)** — the request path, the stores, and what is actually exposed, drawn from live state.

---

## What makes it different

| Property | What it means |
|----------|---------------|
| **Sovereign** | Your data, your models, your hardware. An external API is optional, never a dependency. |
| **Domain-owned** | Every agent declares its own vocabulary, picks its own capabilities, and words its own results. Adding a capability to an agent requires **no edit to the orchestrator**. |
| **Model-agnostic** | Agents request a *capability* (`vision`/`reasoning`/`synthesis`/`code`), never a vendor. Zero model names in `agents/` or `core/`. |
| **Evidence-first** | Reference material is retrieved by exact headword or citation, never bag-of-words similarity. Measured outcomes outrank documentation. |
| **State-carrying** | Facts, provenance, integrity, uncertainty and findings stay attached as information moves between ingestion, storage, retrieval, agents and output. Missing state is never silently promoted to certainty. |
| **Honest about absence** | *Nothing found*, *not checked*, *incomplete*, *conflicting* and *verified clear* are five distinct states, not one. `undetermined`, `unscorable` and `unknown` are real verdicts rather than fields nobody filled in — and `unknown` is never read as `complete`. |
| **Supervised narrowly** | Nothing runs on a timer. Healing is on demand and covers five core agents. |

---

## How a request actually flows

```text
                         Phone / browser
                               │  HTTPS + basic auth
                         nginx  :8443                 ← the only MycOS door on the LAN
                               │
                         Anansi :8081                 storyteller — declares 0 terms
                               │  process_request
                               ▼
                         Boss   :8000                 routes to a domain · holds no vocabulary
                               │
                    ┌──────────┴──────────┐
                    │  routing_terms      │           "who claims this request?"
                    ▼                     ▼
        ┌───────────────────┐   ┌───────────────────┐
        │  grow_agent :9009 │   │  legal_agent :9011│   … and 7 more domain agents
        │  95 terms · CLAIMS│   │  34 terms · no    │
        └─────────┬─────────┘   └───────────────────┘
                  │  ingest → answer → describe
                  ▼
        the agent picks its own capabilities
        (drawdown · dosing · stage · care · cadence)
                  │
                  └──────────► facts back to Anansi, which narrates
                               without naming an agent or a task

        Security :9010  ── check_guard on every inbound /execute (fails open)
        Registry :8004  ── discovery: who exists, at what URL
```

Boss never picks a capability. It asks who claims the request, hands the prompt over whole, and the domain agent chooses among its own tools.

**Why it is shaped this way.** Boss once held ~600 lines choosing between one agent's capabilities and ~170 more composing prose for a domain it does not practise. Every gap had the same shape: a new ability was reachable from exactly one branch of the router, and the question got phrased another way. `"how long until it falls to 238"` reached a code model and came back as a physics free-fall problem; `DWC` came back as *"Direct Water Cooker"*.

---

## Five verbs every agent inherits

Defined once in `core/base_agent.py`, so a domain agent that implements them becomes
routable, answerable and narratable **by starting up** — no edit to the orchestrator.

That is the contract. It is **not yet enforced**: inheriting the verbs is not the same as
implementing them, and an agent can start up, register, and still have nothing to say. Which
agents actually satisfy it is reported by `tools/check_inherited.py`, and closing the gap is
Phase 0 in [`DEPLOYMENT_PROGRESS.md`](DEPLOYMENT_PROGRESS.md).

| Task | What the agent answers | What it replaced |
|------|------------------------|------------------|
| `routing_terms` | The words that claim a request for me | A keyword list inside Boss. Grow appends the names of the plants it is currently tracking, so registering a plant makes questions about it route from that moment on |
| `answer` | I take the prompt whole and pick my own capabilities | Boss's intent patterns and facet ordering |
| `ingest` | Anything recordable in the raw input, captured before anything slow runs | Nothing — Boss cannot do this; it does not know what a reading looks like |
| `describe` | I put my own result into words | 171 lines of Boss writing domain prose |
| `refer_finding` / `receive_finding` | Hand another domain something I noticed but do not own | Nothing. The default records the finding and **says it only recorded it** |

Boss distinguishes *claims nothing* from *did not answer*: a silent agent is retried in 30 s, a deliberate abstainer is not.

---

## Agents

Term counts are what each agent declares for itself at runtime.

| Agent | Port | Terms | Owns |
|-------|------|-------|------|
| **Grow** | 9009 | 101 | Plants, reservoir chemistry, stages, germination, dosing, photo assessment |
| **Maintenance** | 8003 | 42 | The machine itself — RAM, disk, reclaim, log hygiene |
| **Legal** | 9011 | 63 | Instruments, statutes, dockets, authorities, CourtListener, the deadline and action registers |
| **Coding** | 8001 | 26 | Code, tests, lint, tracebacks, repo operations |
| **Accounting** | 9012 | 50 | Ledger, equitable interest and control, instruments, what a charge actually is |
| **Security** | 9010 | 13 | Guards, authorisation, kill switch, findings |
| **Analyzer** | 9006 | 12 | Outcomes, patterns, recommendations |
| **Trust** | 9013 | 10 | Trusts, beneficiaries, fiduciary roles |
| **PQA** | 9007 | 8 | Public web search fallback |
| **Boss** | 8000 | **0** | Orchestration only — routing, thresholds, escalation |
| **Anansi** | 8081 | **0** | Narration, both directions. Names no agents, no tasks |
| **Hermes** | 8002 | **0** | Memory transport and policy. A broker; it does not interpret |

The three zeros are deliberate. None of them owns a domain, so none should claim a request.

**On demand, not at boot:** `ag_agent` (agriculture department head, 9015) and `quantum_agent` (9014) are implemented but deliberately absent from `start_all.sh`.

---

## A live matter: registers, not a chat log

Three registers carry a matter, and each is built around what it REFUSES.

| Register | Holds | Refuses |
|----------|-------|---------|
| `deadlines` (Legal) | statutory periods | to compute a period whose authority it cannot open in the corpus |
| `actions` (Legal) | steps somebody must take | to close one without a reference to the thing that proves it was taken |
| `classify_charge` (Accounting) | what a charge actually is | to read the answer off the code someone posted it under |

**A deadline is never recalled.** `add_deadline` looks the citation up and refuses
if it is not there — every other error in this system is correctable afterwards,
and a limitation period is not.

**An action is not done until something shows it was done.** `evidence_expected`
is required when the action is *opened*, not decided at the end. Where an
authority accepts several methods, `evidence_alternatives` carries all of them:
listing only the strictest reads as a requirement and tells the reader to do more
than the law asks.

**A ledger code is a claim.** `classify_charge` answers two questions the posted
label answers as one — what economic event occurred, and whether it may be billed
to this party — and a fault-bearing code with only alleged causation comes back
`posted_label_unsupported`. It was checked against facts adverse to its own
principal before being trusted; an instrument that only agrees with its owner is a
confirmation engine.

**Spoken facts are captured, and reported is not evidenced.** A reading stated in
conversation is logged and the reply is the receipt — every stored field echoed
back, so a misparse is visible in the seconds when it is still cheap to void. A
step reported to Legal moves to `in_progress` and never to `done`: the statement
is the assertion, the receipt is the proof, and the register exists because those
are different states of the world.

---

## Platform services

| Service | Port | Purpose |
|---------|------|---------|
| Registry | 8004 | Agent discovery and registration |
| Inference | 8005 | Capability-routed inference. `GET /capabilities` shows what each resolves to and why anything was skipped |
| Model | 8006 | Dynamic model selection |
| Memory | 8007 | SQLite versioned storage |
| Policy | 8008 | Decision engine |
| Logging | 8009 | Structured audit log |
| Training | 8010 | Training job management |
| Evaluation | 8011 | Model evaluation |
| Data Engineering | 8012 | Dataset preprocessing |
| Service Manager | 8014 | Process supervision — **on demand only** (`POST /heal`) |
| Tool Service | 8015 | MCP gateway (11 servers over stdio) |
| Provenance | 8016 | Artifact lineage and origin classification. Corpus integrity is a property of the section itself (`core/source_integrity.py`), stamped at ingest and carried to the reader — not something this service verifies after the fact |
| Federated Learning | 8017 | Flower server lifecycle + FedAvg (gRPC on 9092) |

---

## Memory: three stores, deliberately separate

| Store | Size | Holds | Retrieved by |
|-------|------|-------|--------------|
| **Memory Service** `:8007` | ~640 KB | Evidence and state — readings, notes, findings, system records | Exact key, via Hermes |
| **Logging / Audit** `:8009` | ~26 MB | Operational execution history. Every `/execute`, every guard decision | Never by a domain agent |
| **Graph** | ~96 KB | Relationships between entities and projects | Query |
| `reference/<agent>/` | ~10 MB | Codified rules — Black's 1910, FRCP, Delaware Statutory Trust Act, Chandler 1912, Securities Acts, Reg S-X/S-K | **Exact headword or citation** |
| `knowledge_base/<agent>/` | 63 files | This principal's own documents, lessons, working notes | CAG similarity |

Domain memory stays small because only *findings* go in it. The operational log is forty times larger and holds none of the reasoning.

**Why reference is never searched by similarity:** the CAG cache scores `len(overlap)/len(query_tokens)` with no stopword filter, so a long passage of boilerplate outranks a short passage that is exactly on point — measured on a real case at 0.040 against 0.030. A definition reaches the model because the instrument uses that word; a section reaches it because the document cites it.

Ingest a licensed PDF into a citation-addressable index with `tools/ingest_pdf.py` (`--treatise` for works without numbered sections — keys by printed page and indexes the authorities each passage cites).

---

## Guards and supervision

Every inbound `/execute` passes through `AgentBase.check_guard()`, which asks the Security Agent (9010) to authorise it. Deny rules live in `config/guards.json` (denylist — no matching rule means allowed).

- **Edit rules:** change `config/guards.json`, then `curl -X POST localhost:9010/execute -d '{"task":"reload_guards","args":{}}'` — no restart.
- **Kill switch:** `touch state/LOCKED` denies everything until removed.
- **Fails open:** if the Security Agent is unreachable the request is allowed and a warning is logged — an outage must not halt the swarm.

**Supervision is on demand and narrow.** `POST localhost:8014/heal` checks five core agents — `anansi`, `boss_agent`, `coding_agent`, `maintenance_agent`, `security_agent` — and restarts only the ones actually down. `GET /scope` says what it will touch; every other agent id is refused.

Two rules keep it from becoming the problem it solves:

- **Health is read from the port**, never from its own bookkeeping.
- **A restart is abandoned after three failures in ten minutes**, with a reason and a log hint. Before this rule: `ag_agent` 484 restarts, `quantum_agent` 485, `maintenance_agent` 488.

---

## Network exposure

Every agent binds `127.0.0.1`. Only the proxy faces the network.

| Port | Serves | Protection |
|------|--------|------------|
| **8443** | Webapp + `/execute` proxy to Anansi | TLS 1.3, basic auth, 25 MB body cap |
| ~~8090~~ | ~~`python3 -m http.server` serving the webapp~~ | ✅ retired 2026-08-29 |
| 9081 | `socat` → Anansi (legacy) | ⚠️ none — plaintext, **awaiting one sudo command** |

The nginx instance runs **unprivileged** as the same user as the agents, with every path it writes under `state/`, so it needs no sudo and does not touch a system nginx.

8090 is gone: `start_all.sh` no longer starts it, and `webapp/serve.sh` binds loopback instead of `0.0.0.0`. 9081 is a systemd unit (`anansi-forward.service`, `Restart=always`), so it cannot be retired without root — run `deploy/systemd/retire_anansi_forward.sh` to disable the unit and drop its ufw rule. That is the last item in the hardening phase.

---

## Quick start

```bash
./start_all.sh                      # brings up all agents + services + TLS proxy, then health-checks every port

# talk to it
curl -X POST http://localhost:8081/execute \
  -H 'Content-Type: application/json' \
  -d '{"task":"process_request","args":["how is the grow going"]}'

# heal anything that is down (nothing runs on a timer)
curl -X POST localhost:8014/heal

# stop everything
pkill -f "registry_service|memory/service|inference/service|boss_agent|anansi|grow_agent"
```

Day-to-day health: ask Anansi `"system status"`, or open the Dashboard tab in `webapp/`.

---

## MCP integration

Servers run over stdio, managed by the Tool Service. Configured in `config/mcp.d/` (11 servers) — `vestige` (cross-session memory), `filesystem`, `git`, `searxng`, `courtlistener`, `puppeteer`, `grounded-docs`, `open-websearch`, `a2asearch`, `secure-execute`, `sentry`. Reload with `curl -X POST localhost:8015/reload`.

---

## Where things stand

Ordered work lives in **`DEPLOYMENT_PROGRESS.md`**, in dependency order — **deployment is the last phase**, not one in the middle.

The current phase is **integrity and contract hardening**, not deployment. The
architecture above describes contracts the platform is built around; an audit in
August 2026 found several of them stated but unenforced — a capability that
dispatched and was never declared, a corpus section that recorded itself
incomplete and reached the reader silent, an exception that occurred and was
swallowed. Enforcing what is already claimed comes before adding to it.

- ✅ system foundation — systemd cleanup · Docker packaging · A2A read amplification (22.04s → 1.05s)
- ◐ **0** integrity and contract audit — the defect register, fixed by class rather than
  one at a time. Corpus integrity is now a stamped property; the reachability and
  silent-failure classes are counted and open.
- ◐ **1** agents capturing spoken facts — Grow logs a reading stated in conversation and
  echoes a receipt; Legal records a reported step as reported. Physical facts (a light
  height, a medium swap) still reach the record only through Claude.
- ⬜ **2–3** retention policy · conversations that persist
- ⬜ **4** identity and authorization (DID / verifiable claims)
- ◐ **5** harden network exposure — TLS + auth live; two legacy plaintext listeners still open
- ⬜ **hardware track** — provision a dedicated device, migrate, cut over *(a constraint, not a phase)*

**Known ceiling:** 7 GB RAM with YOLO + ViT + a vision model + ~20 processes is genuinely tight; a 3B synthesis model OOM-killed most of the stack on its first run. This is the strongest concrete argument for the dedicated-hardware phase.

**Blocked on data, not code:** no public cannabis leaf-health dataset exists, so grower-supplied images are the only route. The training loop — source candidates, human review, download with provenance, count toward a threshold — works end to end; `services/vision/dataset_inventory.py` is deliberately refusal-biased, because under ~100 images/class a fine-tune scores well in training and fails on real photos.

Build history is in [`CHANGELOG.md`](CHANGELOG.md). Working principles and the rules behind these shapes are in [`CLAUDE.md`](CLAUDE.md).

---

**Your Mycelial is alive. Go build something beautiful.** 🍄
