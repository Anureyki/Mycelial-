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
| Service Manager | 8014 | Process supervision, **on demand only** (`POST /heal`) |
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

### The orchestrator carries no domain skill

Boss routes to a **domain**. It never decides which of that domain's
capabilities to use, and it holds no domain vocabulary, parsing or wording of
its own. Everything it does is orchestration plus what it inherits from
`AgentBase`.

The failure this prevents is a router guessing on a subject it does not
practise. Boss once held ~600 lines choosing between Grow's capabilities and
~170 more composing horticulture prose, and every gap had the same shape: a new
ability in Grow was reachable from exactly one branch of the router, and the
grower phrased the question some other way. Adding a keyword fixed the sentence
and not the class - "how long until it falls to 238" was answered by a code
model as a physics free-fall problem, "DWC" as "Direct Water Cooker".

Three inversions carry this, all inherited from `core/base_agent.py` so they
apply to every agent, not just Grow:

| Task | Who answers | Replaces |
|------|-------------|----------|
| `routing_terms` | each agent declares the words that claim a request for it | a keyword list inside Boss |
| `answer(prompt)` | the domain agent picks its own capabilities | Boss's intent patterns and facet ordering |
| `describe(task, payload)` | the agent puts its own result into words | Boss formatting domain results |

An agent's declared vocabulary can include what no static list could know - Grow
adds the names of the plants it is currently tracking, so registering a plant
makes questions about it route correctly from that moment with no edit
anywhere. A new domain agent becomes routable by starting up.

Adding a capability to a domain agent must never require editing Boss. If it
does, the capability is in the wrong place.

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

### A fact spoken to Claude is not a fact the system holds

Until an agent can capture its own domain facts from conversation, **Claude is
the capture layer**, and anything the principal says about the grow must be
written into the agent's record in the same turn it is heard. A fact that lands
only in a conversation dies when that context does.

The failure this prevents, in full:

| When | What happened |
|------|---------------|
| 2026-08-21 | Principal: *"my water is about maybe two inches away from the bottom of the basket."* |
| same turn | Claude confirmed it: *"The 2-inch gap below the net pot is right."* |
| never | It was written to `grow_system_current_plant`. |
| 2026-08-23 | A volume measurement was analysed assuming the medium was **submerged**, concluding the reservoir could not be sized and the principal's measurement was distorted by displacement that does not exist. |

The principal had supplied exactly the fact that decided the answer, two days
earlier, and been agreed with. The system then contradicted them using an
assumption in place of it.

So: **hearing is not recording.** A spoken physical fact - a clearance, a pump
change, a light height, a medium swap - goes into the agent immediately via
`amend_grow_system` (which merges; `set_grow_system` rebuilds the record from
its arguments and silently drops every field not passed). The same applies to
every domain, not just Grow.

The corollary is that reasoning must read the record rather than assume around
it. `measure_working_volume` now takes `medium_contacts_water` from the system
record instead of defaulting - and a default in the calling layer will quietly
defeat that lookup, which is its own bug class.

### Lived data outranks documentation

Observed outcomes carry more authority than what is written down. A product
label, a spec sheet, a manual or a published guideline is **reference**: it is
generic, it may be out of date, and it was often written by someone with an
interest in the answer. What the system actually measured, and what actually
happened afterwards, is evidence.

This is not a licence to ignore documentation. Where the two agree, confidence
rises - two independent lines pointing the same way is worth more than either.
Where they disagree, the recorded outcome wins and the disagreement itself is
worth logging, because a documented figure that repeatedly fails against live
data is information about the document.

Concretely: Botanicare's Cal-Mag chart says 3-5 mL/gal in vegetative growth.
That is recorded as `label_guidance` in the inventory, never as the operating
rate. The rate this grow actually runs is derived from measured ppm, observed
leaf response, and the plant's own history. When the label and the observations
converged on Cal-Mag being under-dosed, that convergence was the finding - not
the label on its own.

The same applies to every domain. A statute as published, a contract as drafted,
a vendor's stated behaviour, a datasheet's rated tolerance - all reference.
What was observed to happen is the record.

### Every agent gets a reference corpus, and the corpus is the floor

Each domain agent carries its own body of rules, doctrine and vocabulary in
`reference/<agent>/`. The point is not that the agent can recite them. It is
that the principal does not have to be specialised in the domain, because the
agent is - and shows its reasoning, so the principal can check it.

**Two distinct stores, deliberately separate:**

| Store | Holds | Retrieval |
|-------|-------|-----------|
| `reference/<agent>/` | Codified rules, standards, dictionaries, canons | Exact lookup by term or citation |
| `knowledge_base/<agent>/` | This principal's own documents, lessons, working notes | CAG similarity search |

Reference material is looked up by **exact headword or citation**, never by
bag-of-words similarity. The CAG cache scores `len(overlap)/len(query_tokens)`
with no stopword filter, so a long passage of boilerplate outranks a short
passage that is exactly on point - measured on a real case at 0.040 against
0.030 - and it truncates any file at 200,000 characters. A dictionary or a
standards volume run through that would be mostly invisible and partly noise.
A definition reaches the model because the instrument uses that word; a section
reaches it because the document cites it.

**The written corpus is the floor, not the ceiling.** Codified rules are what
was agreed and published, which means they already lag practice. Actual
operation runs above them. So the corpus is a baseline to reason *from*, and the
agent's real job is the layer above it: comparing what is written against what
is observed to happen.

This is the same rule as **"Lived data outranks documentation"** above, applied
outside horticulture. A statute as published, a standard as drafted, a rated
tolerance - all reference. What was observed to happen is the record, and where
the two diverge, **the divergence is itself the finding** and is worth logging,
because a codified rule that repeatedly fails against live data is information
about the rule.

Each domain has a live source that supplies exactly that:

| Domain | Codified (floor) | Live (how it actually operates) |
|--------|------------------|--------------------------------|
| Legal | U.S. Code, CFR, canons of construction, Black's | CourtListener - how courts actually rule and what dockets actually do |
| Accounting | Exchange Act, Reg S-X/S-K, ASC, IFRS | EDGAR filings and SEC comment letters - where the regulator actually pushed back |
| Grow | Product labels, published guidelines | Measured ppm, pH, and observed plant response |

**Copyright constrains what can be shipped.** Statutes, regulations, court
opinions and government works are public domain. FASB's ASC and the IFRS
standards are not, and neither are current editions of Black's - which is why
the corpus uses Black's 2nd edition (1910), whose term has expired. Where an
edition is too recent to be free, the principal supplies their own licensed
copy; `tools/ingest_pdf.py` ingests any PDF into a citation-addressable index.
Where a source is authored rather than quoted, it says so in its own `source`
field. Nothing is presented to a model as authority without its provenance
attached - the failure this prevents is a placeholder file defining `custodian`
with an invented meaning and being read as reference.

### Domain focus (recorded intent, not yet built)

- **Accounting** should surface **equitable interest and control**, not debit
  versus credit. Cash in and out is bookkeeping. What matters is who holds
  beneficial interest in what, and who controls it.
- **Legal** should scrutinise whether an instrument actually operates in the
  principal's favour - whether it was negotiated properly, whether it holds up
  against current law, and whether the principal's equitable interest as a
  party is preserved. A signed contract is not automatically a lawful one, and
  execution is not the same as enforceability.
- Cross-domain: a court order, judgment or instrument establishing an amount or
  an equitable interest is exactly the kind of finding one agent should flag to
  the other.

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

## Supervision is on demand, and narrow

Service Manager does not run a restart loop. `POST localhost:8014/heal` checks
the core agents and restarts only the ones that are actually down; `GET /scope`
says what it will touch.

It supervises five agents - **anansi, boss_agent, coding_agent,
maintenance_agent, security_agent** - and refuses every other agent id on
`/start`, `/stop`, `/restart` and `/heal` alike. An on-demand agent is not
broken when it is down; it is off, which is its normal state.

Two rules keep it from becoming the problem it is meant to solve:

- **Health is read from the port**, never from its own bookkeeping. It used to
  check only agents it believed were running, so one it had lost track of was
  never checked while one it wrongly believed alive was restarted forever.
- **A restart abandoned after three failures in ten minutes**, with a reason
  and a log hint. A supervisor that retries forever turns one broken agent into
  a machine that cannot be diagnosed, because the logs fill with its own
  restarts. Before this: ag_agent 484 restarts, quantum_agent 485,
  maintenance_agent 488.

Starting an agent waits for its port to answer before reporting success -
`{"success": true}` used to mean only that `Popen` did not raise.

## False success is the failure mode to hunt

A command that reports success while doing nothing is worse than one that
errors, because the caller believes the work is done and moves on. Every such
report becomes a claim that was never true - which is how a system starts
hallucinating about itself.

This shape has appeared at every layer of MycOS, in one day:

| Where | What reported success | What actually happened |
|-------|----------------------|------------------------|
| Editing code | `str.replace()` on text that does not occur | file unchanged, no error |
| `service_manager` | `{"success": true}` from `/restart` | `/bin/sh` has no `source`; start died, only stop ran |
| Process control | `pkill -f` / `pgrep -f` with a pattern matching nothing | "restarted" a process that never stopped |
| Publishing | `git push ... 2>/dev/null && echo pushed` | 29 commits sat unpushed to `main` |
| An agent's own output | `productive / high confidence` on a photo | no local model covered the species; nothing was assessed |

The last row is the point: this is not only a tooling problem. **An agent that
finds nothing and reports health is committing the same error as a push that
fails quietly.** Absence of a detected problem is not evidence of its absence.

### The rules

1. **Never suppress stderr on a command whose success you are about to claim.**
   No `2>/dev/null` in front of an assertion that the thing worked.
2. **Verify the effect, not the exit code.** A push is confirmed by comparing
   local and remote refs, an edit by reading back the changed behaviour, a
   restart by the new pid serving the port. Exit status is the weakest evidence
   available.
3. **Assert anchors before writing.** A text substitution that cannot find its
   target must raise, not return the original.
4. **A check that found nothing must say so**, distinctly from a check that
   found the thing to be fine. `undetermined` and `unscorable` exist for this
   reason and are not filler verdicts.
5. **Trust the machine that does not get tired.** CI ran on every commit for a
   day, flagged two real `NameError`s, and was ignored in favour of hand-testing
   the paths that happened to come to mind. A red check outranks a claim of
   "verified working".

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
- `OLLAMA_KEEP_ALIVE` – how long Ollama keeps a model resident after a request
  (default: `60s`). Sent per-request by the Inference Service, so this is
  authoritative regardless of how the Ollama daemon was started. Raise to `5m`
  to trade RAM for latency; `0` unloads immediately after every call.
- `VISION_IDLE_RELEASE_SECONDS` – idle window before the perception pipeline drops
  its YOLO/ViT weights (default: `180`)
- `VISION_TIMEOUT` – timeout for a perception subprocess call (default: `300`)

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
- Service Manager heals on demand (`POST localhost:8014/heal`); nothing runs on a timer.

## Next Steps (as discussed)
- Stabilise core (ongoing).
- Add multi‑tenancy for productisation.
- Build a web dashboard.
- Train student models from distillation data.
- Deploy to DigitalOcean.
