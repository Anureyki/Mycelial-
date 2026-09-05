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

## The map

`docs/system-map.html` is the architecture drawn from live state - the request
path, the five inherited capabilities, the roster with declared term counts,
the three stores, and what is actually exposed. Open it in a browser. When the
shape of the system changes, that file and `README.md` change with it; both
describe what IS, while `CHANGELOG.md` holds what happened and
`DEPLOYMENT_PROGRESS.md` holds what is planned.

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

**The personality is a policy layer, not code.** It lives in
`config/anansi_voice.json` and is applied by `agents/anansi/voice.py`. It used
to be constants and a method inside the agent, which meant the voice could not
change without editing the agent - and a voice edit that can reach an agent is
one refactor away from being an authority edit. Editing the config takes effect
on the next telling with no restart.

**Voice cannot touch evidence, and that is enforced rather than intended.**
Every number, date, unit, currency amount and citation in the payload is
extracted before the telling and checked after it. If one is lost, altered or
invented, the telling is **discarded** and the plain text ships. Verified
against a deliberately hostile config: an opener asserting "All 7 of your
readings are perfect" was refused, because the 7 was not in the payload.
Nothing here calls a model - every fabrication this system has produced came
from giving a small model a gap and room to fill it.

**Seven registers control how much personality a situation gets**, most serious
match winning: `low_stakes` 1.0, `technical` 0.6, `security` 0.4, `legal` and
`financial` 0.35, `sensitive` 0.25, `safety_critical` 0.1. Anansi is allowed to
be funny. He is not allowed to be careless.

**An opener that asserts a state must be earned.** "All quiet" and "Everything
is sitting where it should" are findings, not decoration, and Anansi has no
authority to make one. They were being applied by default to anything
unclassified - including a contestable $550 obligation - so the default is now
no opener at all, and `steady` requires the payload itself to say nothing is
needed.

**The trickster names contradictions, and only real ones.**
`narrate_contradiction` takes a claim and an observation and refuses when either
is missing: a contradiction with one side absent is not a contradiction, it is a
guess. *"The registry said the Security Agent was active on port 9010. Port 9010
answered nothing. Between a record and a measurement, the measurement wins."*

**Naming departments depends on what the subject is.** Relaying a domain answer,
Anansi names no agent - the person asked about their reservoir, not the org
chart. Explaining MycOS itself, the departments *are* the story and hiding them
makes it unintelligible.

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

The loader lives in `core/base_agent.py`, so **every** agent inherits it and
announces its corpus at startup. It used to live only in the Legal Agent, which
meant the identical bug survived everywhere else: `accounting_agent` held 2,108
sections of the Securities Acts and Reg S-X/S-K while its `lookup` went cache →
web → model and never opened the books it owned. Nothing reported the mismatch
because the load was lazy, so an agent with an unreachable corpus looked exactly
like an agent with none.

A fix made in one agent for a fault that lives in the base class is not a fix;
it is a second place for the bug to hide. An agent that holds a corpus but never
calls `lookup_reference` now logs a warning at boot naming the sections nothing
can reach.

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
| Accounting | Exchange Act, Reg S-X/S-K, ASC, IFRS | EDGAR filings and SEC comment letters, and the **Internal Revenue Manual** - what the agency will actually do about a set of books |
| Grow | Product labels, published guidelines | Measured ppm, pH, and observed plant response |

**Guidance is not authority, and where it is shelved decides how it is
weighed.** The IRM is held by *Accounting*, never Legal. It directs IRS
personnel, confers no rights on taxpayers, and courts have held it lacks the
force of a regulation - but Legal's corpus is authority, and the claim pipeline
weighs whatever it can open as potentially *governing*. Agency guidance shelved
there would be scored as though it governs, and nothing downstream could tell.
In Accounting it is the live column: 26 U.S.C. and 26 CFR are the floor, and
the IRM is how the floor gets administered against real books.

Every work therefore carries `authority_class` - `federal_statute`,
`state_statute`, `regulation`, `court_rules`, `agency_guidance`, `treatise` -
alongside `authority_class_basis` saying how that was determined. For a statute
or regulation the title *is* the citation and fixes the class definitionally;
anything else must be read, or stay `unknown`.

**Acquiring law is one command.** `tools/ingest_law.py` fetches and ingests any
CFR part, U.S. Code title, or IRM part from the official source. It is
deliberately fetch-on-demand rather than a bulk mirror: the corpus is retrieved
by exact citation, so an unread title costs disk, boot time and index size
while contributing nothing to an answer. The full CFR plus the full U.S. Code
is several million sections, and this machine has 7 GB of RAM. The corpus grows
toward what this principal actually works on.

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

**A dictionary from 1910 is a floor, not a boundary.** Black's 2nd edition is in
the corpus because its term expired, not because it is current. A doctrine it
does not carry, or carries differently, is not thereby unsupported - `laches`
gets a sentence there and two elements in the doctrine index, and terms coined
after 1910 are simply absent. The absence of a headword is a fact about the
1910 edition and about nothing else; the question is then answered from statute,
regulation or case law, and where those disagree with the dictionary they win.
The same reasoning as *lived data outranks documentation*, applied to the age of
a reference work.

**A standing field is set from the content, never from the wrapper.** `stance`,
`source`, `evidence_kind`, `species`, `confidence` - every field whose job is to
carry how much weight something deserves must be filled by reading the thing.
Filling one from a title, a channel, a filename or a vendor is worse than
leaving it blank: a blank field is a known gap, while a guessed one launders an
assumption into metadata the reasoning layer trusts.

The failure, in full: a 110-minute talk was ingested and tagged `advocacy` from
its title and channel name. Read, it argues the opposite of what the tag
implied - that private ordering is *a permission the law grants, not an
exemption from it*, written expressly against the "declare a status and become
exempt" error, citing scholarship and disclaiming its own authority. It was
commentary. The tag would have told Legal to discount the one document in the
corpus that draws the distinction carefully.

If the content has not been read, the honest value is `unknown`.

### Verifiable state outranks narrative

The Registry said the Security Agent was `active`. The port said nothing was
listening. The system believed the port.

That is the whole philosophy, and it generalises past process supervision. An
administrative claim about state - a registry row, a title on a document, a
statute quoted confidently, a label on a bottle - is a claim. What can be
observed is evidence. Where they diverge, the observation wins and **the
divergence is itself the finding**.

Applied to law, this becomes a pipeline rather than a lookup:

```
CLAIM -> SOURCE -> EVIDENCE -> OBSERVATION -> ANALYSIS -> CONCLUSION -> CONFIDENCE
```

`core/claim_assessment.py` implements it. The default conclusion is
**`unsupported`**, and a claim earns anything better only by having each of ten
prerequisites answered with something checkable. Three rules make it a test
rather than a ratification:

- **A citation is not an authority until the text is in hand.** `claim_cite`
  decides `located_in_corpus` by *looking*, never by the caller asserting it.
  A provision the agent cannot open cannot support anything.
- **Reproducibility is a first-class axis.** "Can an independent person run the
  same procedure and get the same result?" A claim with no specified procedure
  is `untestable`, which is reported as a reason for suspicion. Fifty thousand
  repetitions of a theory are not one reproduction of it.
- **`asserted_by` is recorded and never scored.** The principal's own claims run
  the identical gauntlet as a stranger's. Verified: the same statement scored
  `unsupported / 0.0` whether attributed to an Instagram reel or to the
  principal. A pipeline that validates its owner faster than a stranger is a
  confirmation engine with extra steps.

**Rights are not one thing.** Collapsing every question into "who owns it" is
the standard error, because Article 9 is not an ownership machine. The ontology
separates `ownership`, `possession`, `control`, `custody`, `security_interest`,
`priority`, `authority` and `enforcement_right`, so the agent can say *ownership
is established but the claimed control is not* rather than blurring them.

**Domains are allowed to disagree, and disagreement is not resolved by
outranking.** If Legal reads an instrument as establishing something and
Accounting's records do not bear it out, the claim becomes **`contested`** -
not `supported` with a quieter confidence number, which would bury the conflict
in a field nobody reads. Both readings are kept and the conflict is surfaced.
Accounting's default answer is `undetermined`, never `agrees`: an agent that
concurs when it has nothing to check is worse than one that abstains, because
the concurrence is indistinguishable from corroboration and is not
corroboration.

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
itself.

Two directions, both inherited from `core/base_agent.py`:

| Direction | Task | Example |
|-----------|------|---------|
| **Ask** another domain a question | direct A2A, e.g. `check_budget_constraint` | `recommend_purchase` asks Accounting whether a purchase fits, and gets back a constraint, never the ledger |
| **Borrow** another domain's authority | `ask_peer_corpus` | Accounting needs Reg Z, which lives in Legal's corpus. It asks rather than shelving a second copy |
| **Hand** another domain a finding | `refer_finding` / `receive_finding` | Grow reads an equipment invoice and refers the spend to Accounting, which logs it |

**A domain does not keep a copy of another domain's authority.** Accounting owns
ASC, IFRS and the reporting regulations; Legal owns the statutes, the CFR, the
state codes and the canons. A figure in the books is routinely governed by an
authority that lives in Legal's corpus - so Accounting *borrows* it and says so
in the provenance, because two copies of an authority is two sources of truth
and the one that drifts is always the copy.

`ask_peer_corpus` deliberately has **no keyword test** for whether something is
a legal question. Guessing a subject from vocabulary is the router failure this
architecture exists to prevent. It simply prefers a sibling's verified corpus
over an unverified web search, every time, and accepts only an answer whose
`source` says it came from a corpus - never the peer's cache, web fallback or
model output, which would launder an unverified answer across a domain
boundary.

`receive_finding` defaults to recording the finding and **saying that it only
recorded it** - an agent that silently dropped a referral would look identical
to one that filed it. An agent that owns the finding overrides it and acts:
Accounting turns an `equipment_purchase` into a real ledger entry.

**Only what the receiving domain needs crosses the boundary.** The Mars Hydro
order email carried a name, a street address and a phone number alongside the
price. A ledger entry needs the vendor, the amount, the date and a document
reference; personal data has no business in a transaction record just because
it appeared in the same screenshot. The sending agent decides what to send, and
sends the minimum.

A referral whose `amount` exceeds `REFERRAL_THRESHOLD` is flagged
`requires_signoff` for the principal rather than acted on quietly.

### A case is one object, shared

A real matter has evidence, obligations, participants and a history. Before
`core/case_manager.py`, each agent kept its own partial view - Legal a note,
Accounting a transaction, Hermes a memory - and nothing could answer *what is
the state of this case*, because there was no such thing.

The case lives in ONE namespace (`cases`) and every agent references it **by
id**. `store_own_memory` namespaces per agent (`agent_<id>`), which is exactly
the drift this avoids, so the case layer talks to Hermes directly with a shared
namespace. An agent that learns something appends an event; it does not keep a
copy.

| Frame | Owns |
|-------|------|
| **Legal** | **Elements, not laws.** `barrier`, `requested_accommodation`, `supporting_evidence`, `response`, `current_status` - each `established`, `insufficient_evidence`, `disputed`, `refuted` or `not_applicable` |
| **Accounting** | **Obligations, not bookkeeping.** What is owed, on what cadence, **who is authorised to pay it**, and whether a payment can be evidenced |
| **Hermes** | Transport and the shared namespace. It brokers; it does not interpret |
| **Trust** | Participants and fiduciary roles |

**"Insufficient evidence" is a real outcome, not a failure.** An element with
nothing attached is a named gap with the thing that would close it. A case tool
that only records wins tells its principal they are ready when they are not.
The same rule reaches Accounting: a payment with no evidence reference and a
payment by an unauthorised payor are both `contestable`, and both look
identical to good standing if all you keep is the amount.

**Evidence never travels in the event envelope.** An event carries a type, a
case id, an actor and a **reference**. Boss routes on the type and refuses an
envelope that carries `evidence`, `content`, `document`, `text` or `body`. The
refusal is structural rather than a convention: an orchestrator that *can* read
domain content will eventually reason about it, and this one practises no
domain.

Event types are a closed set - `case_opened`, `document_added`,
`evidence_added`, `participant_added`, `element_updated`,
`obligation_recorded`, `payment_recorded`, `case_state_changed`,
`task_completed`, `note_added`. An unknown type routes nowhere, deliberately.

### Hardware is behind an authorization boundary

"The agent believes this should happen" and "the system is authorised to make it
happen" are different states. Recommendations flow through Boss for
authorisation before reaching any control service or device. No agent holds
direct actuation authority.

### Capital actuation, and the one exception to the rule above

Recorded before any Trading agent exists, because it is the thing every later
implementation choice gets checked against, and because once an exchange
credential is wired into an agent you are no longer designing a capability -
you are operating an authority system over real money.

An exchange API is a control service. So the rule above already answers the
easy version of the question: **an agent may not place an order.** What it does
not answer is the hard version, which arrives the moment a position needs
closing faster than a governance hop can complete. A stop that waits for
authorisation is not a stop.

**The exception is scoped by DIRECTION, never by trust.**

> An agent may act without authorisation if, and only if, every action
> available to it reduces exposure.

An agent that can only close, cancel, or halt is safe to hand unmediated
authority, because the worst a confused or compromised one can do is exit a
sound position - which costs money and cannot lose the bank. An agent that can
open, size or add never receives it, however reliable it has been. The test is
the capability surface, not the record.

This is the same rule the kill switch already runs on: `touch state/LOCKED`
needs no permission because stopping is the only thing it can do.

**Fail-closed is the default in code for anything touching capital, and policy
may only tighten it.** Not a lookup - a lookup needs a failure posture for the
thing that defines failure postures, which is a recursion, not an answer. The
swarm-wide guard fails OPEN by design and correctly: a Security Agent that is
restarting must not halt a grow reading. Capital inverts that default, and the
inversion is structural rather than configured, so an unreachable policy service
cannot open a gate. Opening is not an operation policy can perform.

The clearest statement of the principle came from outside this project: *a
position you cannot measure is a position you do not hold.* It is `unknown is
never complete`, one domain over.

**Supervision and authorisation are different edges.** A safety loop needs a
supervisor that can restart it, ping it and log it, and that cannot gate what it
decides. Service Manager already is exactly that - restart-only, scope-limited,
refusing every agent id outside its list. Boss is the authorisation path and
therefore must not sit anywhere in a safety loop's line, not even as a parent in
a diagram, because every other edge out of Boss is an authorisation edge and the
drawing will be read that way by whoever builds from it.

**Reconciliation belongs to Accounting, never to the desk that traded.** A
trading system that assembles its end-of-day report from what its own components
say they did is grading its own homework, and self-reported P&L is a claim like
any other. Accounting pulls the venue's record and diffs it against the claim;
where they disagree the result is `contested` and stays visible, and its default
remains `undetermined` rather than `agrees`. That is the closing edge of the
loop: Accounting authorises the financial state going in and evidences it coming
out.

**Regulator guidance is reference, not authority, for a private principal.**
FINRA's algorithmic-trading material is good engineering to borrow from -
pre-trade controls, financial thresholds, kill switches, post-trade
surveillance - and it binds member firms, which a person trading their own
account is not. It is shelved as `agency_guidance` in the live column, the same
way the IRM is, and for the same reason: the claim pipeline weighs whatever it
can open as potentially governing, and guidance filed as authority would be
scored as though it governed.

**What this does not settle,** and what has to be decided deliberately rather
than discovered: whether Trading is advisory - it recommends, a human fills - or
whether it holds narrow financial actuation authority with its own credential
scope, transaction limits, kill switch, audit trail and reconciliation
requirement, separate from general control-service authority. Both are coherent.
Drifting into the second by implementing the first is not.

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

### Anansi owns the channels; the domains own the memory

The principal's design, in his own two corrections. First: agents do not each
grow an outbound channel - **Anansi is the interface layer, so it owns every way
of reaching him and nothing else does.** Second, and the one that shapes it:
*"Anansi is not necessarily the one that's remembering. The domains are
remembering their task. But it can be the interface that interacts with me on
different levels."*

So `notify` **holds no queue**. Grow remembers what is due, Legal remembers what
runs out, and Anansi keeps no copy of either - a copy is a second source of
truth and the copy is always the one that drifts. It is handed something and it
delivers it.

**"Different levels" keys off the voice registers already there.** How serious a
situation is already has a number - `low_stakes` 1.0 down to `safety_critical`
0.1 - so channel is chosen from that scale rather than a second one nobody
maintains. A grow reminder at 1.0 waits on the dashboard; a legal deadline at
0.35 also takes email.

**Courier, not narrator.** `verbatim` is the whole distinction. A domain
document - a notice Legal drafted - goes out unchanged, because Anansi narrating
legal text would be Anansi practising law, the one thing this agent must never
do. It tells the story of what happened; it does not write the instrument.

**Three tiers, and only the first is automatic:**

| Tier | Example | Authority |
|------|---------|-----------|
| Tell the principal | a reminder, a deadline | automatic |
| Draft a document | Legal writes it, nothing leaves | automatic, stays local |
| **Send to a third party** | a landlord, HUD, a regulator | **refused** - needs his explicit sign-off |

The third is refused structurally, not by convention. An agent that can post a
statutory notice on someone's behalf can post the wrong one, and a misdirected
notice or a premature filing is not correctable afterwards. This is the same
boundary hardware sits behind, for the same reason.

**An unconfigured channel reports `sent: false` with the reason.** It does not
fall back to the dashboard while claiming the email went. A notification that
fails quietly is worse than one never attempted, because the domain believes he
was told.

## Absence and unreachability are different findings

Before concluding a capability is missing, determine whether it is merely
unreachable. The two look identical from the outside and have opposite fixes:
one means build it, the other means declare it.

There are **five** states, not two, and the last two are the ones that hide:

| State | What it means | Fix | How it is found |
|-------|---------------|-----|-----------------|
| **absent** | nothing implements it | build it | **partial** - a domain agent with no `reference/<id>/` warns at boot; nothing else checks for absence |
| **working** | implemented, declared, routable | nothing | - |
| **undeclared** | implemented and dispatching, invisible to whatever reads the declaration | declare it | `check_declared_matches_dispatched` and `check_declaration_sites_agree` |
| **inert knowledge** | the information is held and **no verb reasons with it** | give it a verb | **partial** - the boot warning covers corpora only |
| **unstructured fact** | a verb exists, runs, and **cannot find the fact because it lives in prose** | give the fact a field | **none** |

**The last two look identical and are not.** Both read as "the system held it and
could not reason with it", so the doctrine's own test applies: they have
different fixes. Grow loaded 15 corpus sections that no verb ever called - `give
it a verb` was right. The halo observation had a verb, which ran, and looked, and
could not find the answer because it sat in a sentence: three attempts to mine it
out of note text matched an equipment description, then a note referring back to
an earlier one, then finally the agent's own question *"whether any spot has a
yellow halo"* read as a report that one was present. Applying `give it a verb`
there yields nothing, because the verb was already there. **A discrete
observation belongs in a field. Prose is where facts go to become ambiguous.**

**The `How it is found` column is deliberately honest about its gaps.** A state
with no detector is a state the system lives in without knowing, and two of the
five have none or nearly none. Grow ran for months with no reference corpus and
nothing said a word; the principal found the capability drift by noticing one
stale config by eye. An empty cell here is a piece of work, not a formatting
choice - and it is worth more on the page than a table that looks complete.

`undeclared` was measured at **82 capabilities** on 2026-08-31: every one worked
when called by name, and no router, dashboard or peer agent knew it existed. The
capability had not disappeared - *its description of itself had*. That is why the
failure propagates all the way up: dispatch succeeds, declaration omits it, the
registry cannot expose it, Anansi cannot route on it, and the principal asks the
system a question and is told, in effect, "I don't know."

**`tools/check_inherited.py` is the instrument for this.** It compares what the
agents actually inherited and wired up against what the architecture claims, and
reports the discrepancy - dead routing terms, inherited verbs that crash, corpus
sections recording themselves truncated. Declared reality on one side,
operational reality on the other, and the gap named out loud.

**`inert knowledge` is the subtle one and the doctrine catches it.** Asked *"what
can you already do about drying and curing"*, the system answered nothing, and
the elegant inference is `undeclared`. Checked, it is not: Grow has **zero**
drying verbs - the harvest track is planned and unstarted - and simultaneously
holds **six shelved sources** on drying and terpene retention plus three fields
on the plant record including `drying_temp_floor_f: 72`. The information is
there and nothing reasons with it.

So: **"I have this capability" and "I have information about this subject" are
different answers**, and a system that cannot say which one it means will be
rebuilt in the wrong layer.

The rule is the same one this file already runs twice, at a third altitude:

| Layer | Rule |
|-------|------|
| Legal | do not trust the claim - open the authority |
| Data | do not trust the object - check whether its state survived the hop |
| Architecture | do not assume the capability is missing - check whether it is reachable |

And the reason it is a rule rather than an instinct: reasoning to the right
framework and the wrong state is exactly what it prevents. The check is cheap.
The inference is free and sometimes wrong.

## State travels with the fact, or the fact is gone

Every serious defect this system has produced in the last week reduces to one
sentence: **the information existed, and something dropped it at a boundary.**

| Where | What existed | What arrived |
|-------|--------------|--------------|
| Corpus → reader | a section recording itself truncated | the text, silent about being half a provision |
| Dispatcher → registry | 82 working tasks | a capability list nothing could route to |
| Ingester → file | `body[:MAX_SECTION]` knowing it had cut | a stored string and no record of the cut |
| `except:` → caller | a failure with a reason | `pass` |
| Agent → user | an answer the agent could compute | no `describe()`, so nothing said |
| Index → lookup | `integrity` on the section | an entry rebuilt from a chosen handful of keys |

None of these is a wrong answer. Every one is a **true thing that stopped
travelling**, which is worse, because everything the caller can see is accurate.
A wrong passage presented as authority gets caught on review. A half passage
presented as whole does not.

So: **any field carrying provenance, integrity, uncertainty or a finding is part
of the payload, not decoration on it.** It survives every hop — ingestion,
storage, indexing, retrieval, A2A, narration — or the hop is a bug.

Three rules follow, and they are testable rather than aspirational:

1. **Record it where it is known, not where it is convenient.** The only code
   that can say whether a section was truncated is the code holding the full
   body at the moment it cuts. Reconstructing that later from string length is a
   guess about form standing in for a record of history — the same error as
   setting `authority_class` from a filename.
2. **A missing state is `unknown`, and `unknown` is never `complete`.** The
   default must be the honest one. Backfilling 15,000 sections as `complete` for
   a clean report would put a guess in the one field whose entire purpose is not
   to be one.
3. **Never infer at a boundary what should have been carried to it.** Wrapping
   an exit so nothing escapes unchecked is correct for a function with a dozen
   return paths, and it is **not** tracing. A safe default at a boundary is
   perfect camouflage for a leaky pipe: `source_integrity.pipe_check()` exists
   because *never recorded* and *dropped in transit* arrive looking identical
   and are opposite problems — the first means re-ingest, the second means fix
   the pipe.

The corollary for fixing: **fix the class, not the instance.** `_load_reference_docs`
builds entries in two places and only one was patched, so a section reached by
citation carried its integrity and the same section reached by subject term did
not — which one a caller got depended on how they happened to ask. A fix applied
to one of two identical sites is not a fix; it is a second place for the bug to
hide.

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
