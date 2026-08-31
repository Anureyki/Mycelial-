# Mycelial — Roadmap and Progress

Ordered work for MycOS. It began as the "move off the daily-driver machine"
plan, which is why the deployment phases carry the most detail.
Full plan/rationale: `/home/anureyki/.claude/plans/based-on-the-system-tidy-valiant.md`

## Order

**Reordered 2026-08-30 by capability and necessity**, at the principal's
instruction. Completed phases are no longer numbered - they sit under
*Completed* below, and the active list is only what is left. `was` keeps the old
number so anything written before today can still be traced.

**Hardware is not a phase.** Provisioning a device and cutting over to it were
numbered as though they were work to be sequenced against everything else, and
they are not - they are a **constraint**. The principal's point: *"device
hardware is going to always be a hard coded thing that needs to be done. You
can't get around needing a new hardware."* Correct, and a constraint that cannot
be reordered does not belong in a list whose whole purpose is ordering. They are
a track that opens when a machine exists, and nothing above them waits on it.

So the numbered phases are now **only work that can be done on the hardware that
is already here**, ordered by necessity:

**0 — what is already built and already wrong.** Added 2026-08-31 after an audit
the principal asked for. It sits before 1 because it is not new capability: it is
a register of defects the system is paying for today, on a live housing matter
and a live grow. Nothing in it needs hardware and nothing above it waits on it -
but building capability on top of 82 undiscoverable tasks and ~125 silent
failures makes both harder to find.

**1-2 — what the system cannot do, and that costs something every day.** Both
are the same seam: information crossing between the principal and the system. A
lollipop and a leaf removal reported on 2026-08-21 were still missing from the
record on 2026-08-30, because the only path from a spoken fact to an agent runs
through a human relaying it by hand. And a question Anansi cannot yet answer is
a dead end rather than something that arrives later. Neither needs hardware, and
both are being paid for now.

**3-4 — cleanup, and finishing what is nearly finished.** Retention decides what
is worth carrying to a new machine, so doing it after a migration means
migrating what would have been deleted. Hardening is one `sudo` command from
complete and should not sit at 95% indefinitely.

**5 — the prerequisite for exposure.** Identity must precede any multi-user or
public surface. Multi-user without strong identity is an apartment block with
one key on the front door.

**Hardware track — opens when a machine exists.** Everything shipped to it
should already be correct, lean and authorized, which is what 1-5 are for.

**And RAM is the test for whether software work can go first.** The principal:
*"anything software related, if the RAM allows, can be done ahead of hardware."*
So every numbered phase carries whether it fits in what is here - **7 GB total,
about 4 GB free** - and all five do. None of them is waiting on a machine.

The one that could stop being true is Phase 1. Capturing a spoken fact
deterministically - the grower says a number and a unit and it is written - costs
nothing. Doing it by handing every conversational turn to a language model is a
different phase with a different budget, and on 4 GB free it would mean the
1.5B model already loaded, which is the model that has produced every
fabrication this system has made. If Phase 1 starts to need a bigger model to
work, that is the signal it has become hardware-blocked, and it should be said
out loud rather than discovered by watching it get worse.

| # | Phase | Status | Fits current RAM? | was |
|---|-------|--------|-------------------|-----|
| 1 | Agents capture spoken facts themselves | not started | yes, if capture stays deterministic | Phase 4 |
| 2 | Conversations that persist, and answers that arrive | not started | yes - a table and a queue | Phase 9 |
| 3 | Retention: decide what to keep, and on what evidence | not started | yes, and it REDUCES the footprint | Phase 2 |
| 4 | Harden network exposure | ◐ one sudo command remaining | yes - one command | Phase 6 |
| 5 | Identity and authorization (DID / verifiable claims) | not started | yes - crypto, no model | Phase 5 |
| — | *Hardware track:* provision dedicated device | blocked on hardware | n/a | Phase 7 |
| — | *Hardware track:* migrate and cut over | blocked on hardware | n/a | Phase 8 |
| — | *Deferred track:* multi-tenancy | not scheduled | unknown - not designed | — |
| — | *Design track:* Anansi as a spider on a web | idea, evolving | yes - phone GPU, not this box | — |

---

## Phase 0 — Defect register: audit findings, fix by class — NOT STARTED

**Opened 2026-08-31 at the principal's instruction**, after he pointed out that
wrapping `lookup_reference` to attach source integrity was *"the same bug one
layer down"* — a boundary fix that normalises a symptom rather than tracing where
the data is lost. He was right about the method even where the diagnosis differed:
the index-builder leak predated the wrapper, but a safe default at the boundary is
perfect camouflage for a leaky pipe, and it was found by testing rather than by
the design forcing it into view.

**Numbered 0 because it is not new capability.** Everything here is already built
and already wrong, so it is being paid for now, in a system whose principal is
using it on a live housing matter and a live grow.

### The findings, measured not estimated

| # | Defect | Count | Class |
|---|--------|-------|-------|
| 0.1 | Tasks that dispatch but are **not declared** — invisible to routing and discovery | **82** | reachability |
| 0.2 | `except: pass` — a failure that leaves no trace | **~90** | false success |
| 0.3 | Bare `except:` — swallows `KeyboardInterrupt` and `SystemExit` too | **35** | false success |
| 0.4 | Statutory sections recording themselves **truncated**, awaiting re-ingest | **711** | source integrity |
| 0.5 | Sections with **no** integrity record — `unknown`, which is not `complete` | **~15,000** | source integrity |
| 0.6 | Agents with no `answer()` — a question routes to them and dies | **5 of 9** | inversion |
| 0.7 | Agents with no `describe()` — capability runs, says nothing | **6 of 9** | inversion |
| 0.8 | Declared capabilities that do not dispatch | **1** | reachability |
| 0.9 | Unused imports / unused variables | **48** | hygiene |

### 0.1 — 82 tasks nothing can find

The reverse of a dead capability, and harder to see: the task works perfectly
when called by name, and no router, dashboard or peer agent knows it exists.

```
grow_agent          45     acquire_plant, amend_grow_system, assess_vpd, ...
anansi              12     actions, deadlines, grow_snapshot, phase_status, ...
boss_agent          14     ingest_document, pending_decisions, progress_recap, ...
maintenance_agent    6     assess_updates, phase_status, recent_changes, ...
legal_agent          4     citation_lookup, definition, matter_state, check_filing
pqa_agent            1     fetch_page
```

Grow is the worst: roughly half its surface is undiscoverable. Twelve of these
were fixed by hand on 2026-08-31 after `add_deadline` and `classify_charge` both
turned out to work and be invisible — which is the tell that this needs a rule,
not another round of hand-fixing. **A capability list assembled by hand will
drift from the dispatcher every time.** The fix is to derive the declaration from
the dispatch, or to fail the build when they disagree.

### 0.2 / 0.3 — ~125 places a failure leaves no trace

`grow_agent` alone has 32 `except: pass`. This is the mechanism behind every
false-success entry in `CLAUDE.md`'s table: the work does not happen, nothing is
raised, and the caller proceeds believing it did.

Not all are wrong — an optional enrichment that fails should not take down a
reading. But they are **indistinguishable** from the ones that are wrong, and
that is the actual defect. Each needs to become either a logged swallow with a
reason, or a raise.

### 0.4 / 0.5 — the corpus does not know what it holds

711 sections record themselves incomplete (backfilled 2026-08-31 from the one
thing that was knowable: storage at exactly the retired 4,000-character cap).
Roughly 15,000 more say nothing at all, and `unknown` is deliberately not
`complete` — a guessed `complete` would be worse than a blank, because the
reasoning layer trusts it.

Re-ingesting is mechanical but not free: it is a network fetch per work, and the
large CFR parts are the bulk of it — Reg S-X 247 sections, Reg Z 242, Reg B 35,
26 CFR 20 85, 38 CFR 1 35.

### 0.6 / 0.7 — five agents cannot answer a question

`answer()` and `describe()` are two of the three inversions `CLAUDE.md` says
carry the whole no-domain-knowledge-in-Boss design. Missing on **analyzer,
coding, pqa, security, trust**. A question routing correctly to one of them still
dies — verified live: *"what do I need to do for my housing case"* reached Legal
and returned nothing until `matter_state` was added on 2026-08-31.

### The rule this phase is really about

Every item above is a case of **a fact that exists and does not travel**. The
dispatcher knows a task exists and the registry does not. The corpus knows a
section was cut and the reader does not. The `except` knows something failed and
nobody does. The agent knows an answer and has no `describe()` to say it.

So the exit criterion is not "zero warnings". It is that **for each class, the
system can tell you the count** — the way `check_inherited.py` now reports
routing terms, inherited capabilities and corpus integrity on every run. A defect
that is counted is being managed. A defect that is only known to whoever last
read the file is not.

---

## Phase 1 — Grow captures spoken facts itself — NOT STARTED

*(was Phase 4)*

**Independent of every other phase. Small, and it removes a standing failure
mode rather than adding a feature.**

### The gap

Grow already captures one class of spoken input: `ingest()` recognises a
reservoir reading stated in passing ("19.7c 6.15ph 688ppm") and records it
before anything slow runs. It captures no other kind of fact.

Everything else the grower says about the physical system - a net pot
clearance, a pump change, a light height, a medium swap - reaches Claude and
stops there. Claude is currently the only path from a spoken fact to the
agent's record, and that path is a habit, not a mechanism.

### What it cost, concretely

On 2026-08-21 the grower said the water sits about two inches below the
basket. Claude agreed with it in the same turn and never wrote it down. On
2026-08-23 a volume measurement was analysed assuming the medium was submerged
- concluding the reservoir could not be sized and the grower's measurement was
distorted by displacement that does not exist. The grower had supplied the
deciding fact two days earlier and been agreed with.

### It is not a Grow problem. Worked example, 2026-08-31.

Renamed from *"Grow captures spoken facts itself"* after a session that made the
scope obvious. The principal, on the same failure in Legal: *"I should be able to
have this conversation with Anansi, and Legal should be able to pull the
respective solutions - just like how you did."*

Correct, and what "how you did" consisted of IS the specification. In one
exchange the principal said, in ordinary speech: the pest-control trip charge is
$54; it is posted on the ledger under DAMAGES; the landlord alleged the tenant
broke through the wall; the roaches come from the AC penetration, not the
plumbing. Four facts. What followed was mechanical, and none of it is reachable
through Anansi:

1. **Route each fact to the agent that owns it.** The $54 and its ledger caption
   are Accounting's. The tenant-causation allegation is Legal's.
2. **Pick the category that decides what it is worth.** `out_of_pocket`, not a
   general note - the category carries what evidence closes it.
3. **Notice what the fact IMPLIES against the corpus.** A landlord alleging
   tenant causation is not a complaint about tone; it is Tex. Prop. Code
   92.052(b), which extinguishes the repair duty outright if established. That
   connection is the whole value, and it came from having the statute open.
4. **Open a claim and test it.** The allegation scored `prerequisite_missing` at
   0.0 - no move-in inspection, no incident report, no photograph predating the
   tenancy, nothing in the file evidencing causation.
5. **Report the gap rather than a number.** The $54 was recorded as NOT YET
   KNOWN until the principal read it off the ledger, then updated.

Steps 1, 2 and 5 are mechanical and belong in the agents. Step 3 needs a domain
corpus open, which is exactly what Legal has. Step 4 already exists as
`claim_open` / `claim_evidence` / `claim_get` and is reachable by nothing a
person says out loud.

**So the phase is: every domain agent captures its own spoken facts, and Anansi
routes an ordinary sentence to the one that owns it.** Grow is the example
because it failed first, not because it is the only one - and Phase 2
(conversations that persist) is what lets the answer arrive after the capability
to give it exists.


### Likely shape (not designed yet)

Extend `ingest()` beyond readings: recognise statements of system fact and
route them to `amend_grow_system`, which already merges without clobbering.
The hard part is not extraction, it is **refusing to guess** - a
misremembered clearance written confidently into the record is worse than no
clearance at all, because the reasoning layer trusts the record. Anything
below confident extraction should be surfaced for confirmation, not stored.

### Do not start this by

Letting a model rewrite the system record freely. The record is what dosing
and stage reasoning read; it needs the same "assert the anchor before writing"
discipline as any other substitution.

## Phase 2 — Conversations that persist, and answers that arrive — NOT STARTED

*(was Phase 9)*

Recorded 2026-08-30 at the principal's request, for the same reason Phase 3
existed to be found again: *"if we did not write this into the phases, I would
have forgotten... just continue architecting a different feature instead of
finding ways to move forward."*

### The gap

Asking Anansi something it cannot answer yet ends the exchange. The principal
waits, the capability gets built, and then they have to **come back and ask the
same question again** — the system never tells them the answer exists now.

Today's session is the example: *"What is legal tender"* returned *"the routing
is right and the capability is missing."* That was the correct answer and it was
a dead end. Legal's `answer()` was built twenty minutes later and nothing told
the person who asked.

Two separate things are missing, and they are worth separating because one is
much smaller than the other:

**9a. A question can outlive the request that asked it.** When an agent reports
a missing capability, the question should be *recorded as open* against the
agent that claimed it. When that agent later gains the capability, the open
question is retried and the answer posted into the chat unprompted. This does
not need conversation storage — it needs a queue of unanswered questions with
the agent and prompt that produced them.

**9b. Conversations persist at all.** There are no sessions: the chat is a
transcript in the browser that dies on reload, and nothing on the server knows
what was discussed. This is the larger piece and it is what makes 9a *feel* like
a conversation rather than a notification.

### Why it is not a performance-shaped phase

Phase 3 was arithmetic — measure, cache, measure again. This one changes what a
request IS: a thing that can be answered later, by a different process, into a
channel the asker is not currently waiting on. That is closer to Phase 5
(identity — *who* is the answer for) than to a performance fix, and it should
probably follow it.

### Do not start this by

Building chat history storage first. The history is the bigger half and the
smaller half is worth more: a queue of open questions retried when a capability
appears would have caught today's legal-tender case with no session storage at
all.

## Phase 3 — Retention: decide what to keep, and on what evidence — NOT STARTED

*(was Phase 2)*

**Independent of every other phase. Owner: Maintenance Agent, which already
runs `analyze_memory_usage` and `run_cleanup_routine` and is the only agent
whose domain is the machine itself.**

### What the stores actually look like (measured 2026-08-24)

| Store | Holds | Size |
|-------|-------|------|
| Memory Service (8007, via Hermes) | evidence and state | **0.6 MB, zero image rows** |
| Logging Service (8009) | operational history | `audit.log` 210 MB + `audit.db` 24 MB |
| `knowledge_base/grow_agent/photos` | 20 uploaded photos | 56 MB |

The separation is working as designed - domain memory is small and clean, and
Hermes is not the problem. The bloat was 24 log lines carrying base64 image
payloads, 196 MB of the 210 MB. Those are redacted at the source now
(`AgentBase.log` scrubs base64 runs and caps line length), so this phase is
about **policy**, not that leak.

### The question

Photos are the obvious candidate for deletion once a finding has been
extracted - the finding is what matters, not the pixels. But "already analysed"
is not the same as "safe to drop", and the system currently cannot tell the
difference:

- **18 of 21 leaf evaluations produced a finding.** Those photos are
  genuinely redundant with what was extracted.
- **3 could not be read at all** - the local disease models cover pepper,
  potato and tomato and carry no cannabis class, so no classification was made.
  Deleting those destroys the only copy of data nothing has extracted yet, and
  they become readable the moment a model that covers the species exists.

So the rule cannot be "delete after analysis". It has to be **delete once a
finding was actually produced, and keep anything the pipeline could not read.**

### Trained-on is a second, separate test

An artifact can also be dropped once it is represented in a dataset the
weights were trained on - the information survives in the model. That test
cannot be applied yet: `datasets/` is empty and `weights/` holds nothing, so
**nothing has been trained on anything**. Wire the test now and it silently
approves every deletion.

### Likely shape (not designed yet)

The Provenance Service (8016) already exists to track artifact lineage and
origin. It is the right place to answer "what was derived from this photo, and
does that derivation still stand" - which is exactly the retention question.
Maintenance should ask Provenance, not guess from filenames or mtimes.

Rotation is a separate, smaller gap worth fixing alongside: CLAUDE.md claims
logs are "rotated daily" and they are not - `audit.log` had entries going back
to 2026-08-13 in a single file, and every agent appends to that one file.

### Do not start this by

Deleting on age or size. Both are proxies for "probably worthless" and neither
is evidence. The three unreadable photos are among the oldest.

## Phase 4 — Harden network exposure — ◐ ONE SUDO COMMAND REMAINING

*(was Phase 6)*

Done:
- Unprivileged nginx on **8443** — TLS 1.3, basic auth, 25 MB body cap —
  serving the webapp and proxying `/execute` to Anansi. Runs as the same
  user as the agents with every write path under `state/`, so it needs no
  root and does not touch the system nginx. Config in
  `config/nginx/mycelial.conf`, started by `start_all.sh`.
- Security Agent (9010) gates every inbound `/execute` via
  `AgentBase.check_guard()`, denylist in `config/guards.json`, kill switch
  at `state/LOCKED`. Fails open by design — verified on 2026-08-29 when the
  agent was found down and the swarm was correctly still serving.
- **8090 retired 2026-08-29.** `start_all.sh` no longer starts
  `python3 -m http.server 8090 --bind 0.0.0.0`, and `webapp/serve.sh` now
  binds loopback. `mycelial.service`'s ExecStop pattern cleaned up with it.

Remaining — needs root, so it cannot be done from inside the stack:
- **9081** is `anansi-forward.service`, a systemd unit with
  `Restart=always` putting socat on `0.0.0.0:9081` in front of Anansi, plus
  a ufw rule opening it to the LAN. nginx already serves that exact
  endpoint authenticated on 8443, so it is a second unauthenticated door to
  the same place. Killing the process is not enough; the unit restarts it.
  Run: `deploy/systemd/retire_anansi_forward.sh`

  This does NOT affect `docker-entrypoint.sh`, which uses 9081 *inside* the
  container and publishes it to the host as loopback-only `127.0.0.1:8081`.

## Phase 5 — Identity and authorization (DID / verifiable claims) — NOT STARTED

*(was Phase 5)*

**Comes BEFORE multi-tenancy, which is now a deferred track rather than a
numbered phase.** The ordering matters:
multi-user tenancy without strong identity and authorization is an apartment
block with one key on the front door. Tenancy answers "whose data is this";
authorization answers "what may this requester actually do". Building the second
on top of the first means retrofitting authority onto a system that already
assumes everyone who gets in belongs there.

### Correcting the premise: the foundation does not exist

A design note handed over described this foundation as already present. It is
not - `grep` across `agents/`, `core/`, `services/` and `config/` returns **zero**
matches for DID, SSI, verifiable credential, or ZKP. This phase is greenfield.

What actually exists today:

- `security_agent.authenticate` / `issue_token` — a bearer token in an
  **in-memory dict** (`self.tokens = {}  # persist later`). Every token is
  invalidated by a restart.
- `security_agent.authorize` — `action in self.policies.get(agent_id, [])`. A
  per-agent action allowlist keyed on a **self-reported** agent id.
- `check_guard` — a denylist that **fails open** on transport error, correct for
  a home swarm and inverted for anything multi-user.

So there is an authorization *shape* to build into, and no identity layer at all.

### The idea, stated plainly

The machine does not need to ask "what is your email". It can ask **what
authority can this requester cryptographically demonstrate**. A credential
carries scoped claims rather than an account:

```text
DID:       did:example:123...
Authority: manage_grow_system = true
           approve_device_control = true
           view_accounting = true
           view_legal = false
Scope:     this Mycelial instance
Validity:  currently valid
```

A zero-knowledge proof can establish that a required property holds without
disclosing the underlying material - which is the part that makes this more than
a password with better branding.

The same framework governs humans and agents. A human holds authority to approve
an action; an agent holds authority to perform a narrowly scoped one; a
specialist requests another specialist's capability; Boss coordinates; Security
verifies every requested action falls inside authority actually granted; and
Provenance records who or what authorized the resulting artifact and on what
evidence.

### Three layers, in order

**1. Foundation** — DID resolution, identity objects, credential verification,
persistent identity cache. Note the token store must become durable regardless;
an in-memory dict cannot carry identity across a restart.

**2. Authorization** — map verified claims to permissions; enforce in Security
Agent; authorize Boss requests; resource and action scopes; and record every
authorization decision through the Provenance Service, which already exists and
which **no agent currently calls**. This phase is its first real consumer.

`check_guard` must also invert for credential-bearing requests: fail open stays
correct for internal A2A (a Security Agent outage must not halt the swarm) and
becomes wrong the moment a request carries claimed authority.

**3. UX** — show what is being asked for, present verification status, request
approval, display what a credential grants, handle issuance and revocation, and
carry ZKP consent flows. The "Needs You" dashboard card is the natural surface;
it already aggregates pending decisions.

### Why this ordering is worth holding

Two users on one instance should be able to differ radically in authority - one
with grow read/write and device approval but no legal access, another with
accounting and legal read/write and no device authority. **Boss should never
infer that from conversation.** Security enforces it, and Boss simply refuses
what is not authorized.

That is also what turns cross-domain agent cooperation into a controlled system
rather than a swarm accumulating permission slips.

---

---

## Hardware track

Not numbered: a machine either exists or it does not, and no ordering decision changes that. Nothing in phases 1-5 waits on this.

### Provision dedicated device — blocked on hardware *(was Phase 7)*

*(was Phase 7)*
User decision pending: mini PC (Intel N100/N305 class, 16-32GB RAM, no GPU
needed) recommended in the plan. Not yet purchased/chosen.

### Migrate and cut over — blocked on hardware *(was Phase 8)*

*(was Phase 8)*
Blocked on Phase 7.

---

## Training-data loop — ✅ DONE 2026-08-25

Not numbered: it was asked for and finished in one pass, and does not gate
deployment. Recorded because it changed how the agent acquires knowledge.

A well-run grow cannot supply most of what a vision model needs. The grower
prevents pests, so their plants will never photograph a spider-mite
infestation - 9 of the campaign's 10 labels were unobtainable from their own
data by construction, and only `healthy` was.

Every piece already existed and nothing connected them: the campaign knew it
needed 10 labels, `source_training_candidates` could search the web,
`review_training_candidate` could accept. Three breaks, all fixed:

- **No driver.** Nothing ever decided to source. `advance_training_campaign`
  now reads which labels are short and goes and gets candidates - on demand,
  never on a timer.
- **No gate the grower could operate.** Candidates sat `awaiting_review` for
  days with no interface. A Training tab now shows each proposal with its
  image and source, and accepts or rejects it.
- **Accepting did nothing.** It set a status and printed "download the image
  yourself to have it counted". Accepting now fetches the image into the label
  folder with its provenance beside it, and says so if the fetch fails rather
  than reporting an accept that counted nothing.

And the counter had never worked: `from dataset_inventory import ...` was a
bare import that always raised, so `DATASET_TOOLS_AVAILABLE` was always False
and every label read 0 however many images were on disk. Campaign progress
could not move. It read as "nothing collected yet" rather than "the counter is
broken", which is the same failure shape as any other silent zero.

Invariants held throughout (`config/skills.json`): search proposes, a human
disposes; provenance travels with every accepted file; nothing counts until a
person decides.

## Deferred track — Multi-tenancy — NOT SCHEDULED

**Not a numbered phase.** It is a rewrite of the trust model, not a step on
the way to deployment, and it is deliberately deferred until the single-user
system is deployed and proven.

**Do not start this before Phase 5 (identity/authorization) is done, the single-user system is deployed (Phase 8), and it has been
running on its own device for a while.** It is written down here so it stops
occupying attention, not because it is next. Nothing above depends on it, and
starting it early would destabilise a system that currently works.

This phase only exists if Mycelial is turned into something other people run
(the DigitalOcean/product path). The on-premises personal instance —
NAS/mini-PC, one household — **never needs any of it**. Single-user is a
feature there, not a limitation.

### Why it is a rewrite of the trust model, not a deployment step

Three properties make the current system correct for localhost and unsafe on a
public IP:

1. **There is no tenant concept.** `user_id` is the string `"default_user"`,
   hardcoded in two places in `agents/anansi/Anansi.py`, and read by nothing.
2. **Guards fail open.** `AgentBase.check_guard()` returns *"allowing by
   default"* on any transport error and denies only on an explicit
   `allowed: false`. Correct for a home swarm (a Security Agent outage must not
   halt the fleet); exactly inverted for a service, where an auth failure must
   deny.
3. **`/execute` is unauthenticated.** Anyone who can reach the port can run any
   task on any agent. The Security Agent already has `authenticate` /
   `issue_token` / `authorize`, but no inbound path calls them.

### Concrete scope

**Storage isolation.** `services/memory/service.py` keys on
`UNIQUE(namespace, key)` where namespace is `agent_<agent_id>`, built in exactly
**4 places** in `core/base_agent.py`. That centralisation is the good news — the
storage layer becomes `tenant_<id>_agent_<agent_id>` at those 4 sites.

The bad news is **20 global index-list keys** that would silently collide, one
tenant overwriting another's:

```
action_index  case_index  evidence_index  filing_index  instrument_index
leaf_eval_index  matter_index  notebook_index  note_index  plant_index
reading_index  relationship_index  reminder_index  reservoir_eval_index
session_log_index  stage_eval_index  telemetry_index  training_candidate_index
transaction_index  vision_correction_index
```

These are per-agent singletons today. Namespacing fixes them for free *if and
only if* the tenant id reaches `store_own_memory` — see threading below.

**Threading tenant context.** `store_own_memory(key, value)` takes no request
context, and agents are long-lived processes serving many requests. Two options:

- Thread `tenant_id` through every `handle_task` signature — invasive, touches
  every agent, high chance of a missed call site defaulting to the wrong tenant.
- **Preferred:** a `contextvars.ContextVar` set in the `/execute` handler in
  `core/base_agent.py` and read by the 4 namespace sites. ~10 lines, no agent
  changes for the storage path. Must fail closed if unset, or a bug silently
  writes into a shared namespace.

**Other stores that are single-tenant today and need the same treatment:**
- `core/graph_manager.py` — one `state/graph.db` (nodes, edges, relationships)
- `core/provenance_manager.py` — one provenance DB and lineage chain
- `state/security_findings.json`, `state/processes.json`
- `knowledge_base/<agent>/` CAG directories, and on-disk media such as
  `knowledge_base/grow_agent/photos/` and `training/`

**Auth, inverted.** `check_guard` must fail *closed* when the caller is a
tenant request (while staying fail-open for internal A2A, or the swarm
deadlocks on a Security Agent blip). Token validation on `/execute`, wired to
the Security Agent's existing `issue_token`/`authenticate`.

**Also required before exposure, and not otherwise in any phase:** TLS and a
reverse proxy (Phase 6), per-tenant rate limits and quotas, provisioning and
onboarding, OTA updates, and fleet observability — `logs/` is local files per
process today.

### What this breaks

Every existing memory row lives in an un-namespaced key. A migration must
either rewrite them into a default tenant or the system loses all history —
grow readings, notes, the KAG graph, provenance lineage. Write and test that
migration **before** touching the namespace format, not after.

### Cheaper thing to do first

A voice/Alexa-style device pointed at the **local** instance over the LAN needs
none of this. `voice_listener.py` exists and is wired to nothing; the webapp
already proves the thin-client pattern, and the socat forwarder already exposes
Anansi to the LAN. That de-risks the whole UX question without touching the
trust model.

---

## Completed

Kept for the record; no longer in the active list.

### Remove orphaned crash-looping systemd units ✅ DONE — done *(was Phase 0)*
Five stale units (`mycelial-boss_agent`, `mycelial-codingagent`,
`mycelial-security_agent`, `mycelial-datagatherer`, `mycelial-dashboard`)
were leftover from the `AgNetworking/mycelial-snapshot` era — their
`ExecStart` pointed at flat file paths like `agents/boss_agent.py` that no
longer exist after the repo moved to per-agent subdirectories
(`agents/boss_agent/boss_agent.py`). They had racked up 22,000+ restarts
before being caught. All 5 are now confirmed **inactive**, all unit files
and symlinks removed from `/etc/systemd/system/`. No further action needed.

### Containerize for portability ✅ DONE (smoke-tested) — done *(was Phase 1)*
New files added to this repo (all untracked/uncommitted so far — not yet
`git add`ed or committed):
- `Dockerfile` — single image containing the full stack + Ollama CLI
- `docker-entrypoint.sh` — starts `ollama serve`, then `start_all.sh`, then a `socat` forwarder for Anansi, then tails logs to keep the container alive
- `docker-compose.yml` — publishes **only** Anansi to `127.0.0.1:8081` on the host (via the in-container forwarder on 9081); everything else stays inside the container, matching how `core/base_agent.py` already talks to peers over `localhost`
- `requirements.txt` — generated via `pip freeze` from the live venv (93 packages, no torch/tensorflow)
- `.dockerignore`, `.env.example` — `.env` itself is gitignored (added that line to `.gitignore`)
- One-line edit to `start_all.sh`: `source venv/bin/activate` now only runs `if [ -f venv/bin/activate ]` — backward compatible, still works on the host, lets the container skip it since deps are installed globally there

**Deliberate scope decision:** the original plan sketch implied one
container per agent/service. That was dropped after checking
`core/base_agent.py` — inter-service calls are hardcoded to
`http://localhost:PORT`, and `services/inference/service.py` shells out to
the `ollama` CLI binary directly (not HTTP). Splitting into per-service
containers would mean rewriting networking across ~30 files, too risky to
do blind. Current approach is a single container running the whole stack
(mirrors `start_all.sh` as-is), which still delivers the portability win —
`git clone && docker compose up` reproduces the platform anywhere — without
touching live service code. Per-service splitting is a valid future
follow-up, not part of this pass.

**Also found (not yet acted on):** this repo already has ~15 files with
uncommitted, in-flight changes unrelated to this containerization work —
notably most services already flipped from `0.0.0.0` to `127.0.0.1` binds
(`git diff --stat` shows the list). That looks like independent progress on
what is now Phase 6 (hardening) below. Left untouched to avoid collisions
— check `git status`/`git diff` before assuming it's stale.

### Build history / bugs hit and fixed so far
1. `curl ... ollama.com/install.sh | sh` failed — needed `zstd` installed first (not in `python:3.14-slim`). Fixed: added `zstd` to the `apt-get install` list.
2. `start_all.sh` does `cd ~/mycelial`, but root's `$HOME` is `/root` while the app was at `/app` → `cd: /root/mycelial: No such file or directory`. Fixed: changed `WORKDIR` to `/root/mycelial` (not `/app`) so `~/mycelial` resolves correctly without touching `start_all.sh` further. Updated `docker-entrypoint.sh` and the volume mount paths in `docker-compose.yml` to match.
3. Anansi didn't respond on the published host port even though the container was up and platform services all passed their internal health checks. Root cause: `core/base_agent.py` has an **uncommitted, in-flight change** (not made by this work) that flipped every agent's Flask bind from `host="0.0.0.0"` to `host="127.0.0.1"` — including Anansi, the one agent meant to be reachable from outside. Docker's port publishing NATs to the container's external interface, which can't reach a loopback-only socket. Rather than touch that file (someone else's in-progress hardening), added a tiny `socat` forwarder inside the container: `socat TCP-LISTEN:9081 -> 127.0.0.1:8081`, published as `127.0.0.1:8081:9081` in compose. (First attempt tried forwarding on the *same* port 8081 on the wildcard address — the kernel refused with "Address already in use" even though Anansi was only on 127.0.0.1:8081; had to use a distinct port, 9081, for the forwarder.)

### Verified working (smoke-tested via `docker run`, then `docker compose config`/`build`)
- Image builds clean: `mycelial:latest`, ~2.45GB.
- All 12 agents (boss, coding, hermes, maintenance, anansi, analyzer, grow, legal, accounting, trust, security, pqa) and all platform services start inside the container and pass their internal `/health` checks — confirmed via `/proc/<pid>/cmdline` and the per-service log files.
- Ollama server starts inside the container (no models pulled yet — that's expected, not part of this test).
- Anansi is reachable from the **host** through the published port and returns `HTTP 200` on `/health`.
- `docker compose config` and `docker compose build` both succeed against the compose file as committed here.
- A POST to `/execute` (`process_request`) through the published port didn't return a result within 30s — almost certainly because no Ollama model is pulled and no `ANTHROPIC_API_KEY` was set for this throwaway test container (no `.env` was used). Not investigated further since it's not a packaging/networking issue — the same path (health check) that exercises the identical socat→Anansi hop already round-trips fine. Re-test with a real `.env` and a pulled model before trusting end-to-end reasoning.
- Test container (`mycelial-test`) was removed after each test; no stray containers left running; `.env` created only transiently for the compose validation and deleted after.

### Remaining before calling Phase 1 fully closed
- [ ] Re-test `/execute` with a real `.env` (`cp .env.example .env`, fill in `ANTHROPIC_API_KEY` or pull an Ollama model) to confirm actual reasoning works end-to-end, not just health checks
- [x] Decide whether to `git add`/commit the new Docker files — done. Committed and pushed alongside the platform-service `0.0.0.0`→`127.0.0.1` bind hardening (that turned out to be exactly the 11 platform services in `services/*/service.py` — mechanical, consistent, self-contained; `docker-compose.yml` already only exposes Anansi via the in-container `socat` forwarder regardless). Two *other* uncommitted diff clusters found in the same `git status` sweep were reviewed and deliberately left out of this commit as unrelated: (1) `core/graph_manager.py` + `core/schemas.py` — an in-progress KAG relationship-archive table, orthogonal to deployment; (2) four new `config/agent_cards/*.json` files — runtime-generated agent cards, not authored code.
- [ ] When ready to actually cut over host port 8081 to the container, stop the live tmux/`start_all.sh` instance first (they'll conflict on the port) — this is Phase 8, not needed until the dedicated device exists

### Reduce A2A read amplification inside a single answer — done *(was Phase 3)*

**Measured before:** "how is my plant" produced 282 audited events in 22.04s —
137 memory reads, 141 guard checks, for 5 calls of actual work. 87 of the 137
reads returned data already fetched inside the same request; 108 were individual
`reading_*` keys pulled one at a time.

**Measured after: 22 events, 1.05s.**

| | before | after |
|---|---|---|
| wall clock | 22.04s | **1.05s** |
| audited events | 282 | **22** |
| memory reads | 137 | **15** |
| batched reads | 0 | **1** (replacing ~108) |
| guard checks | 141 | **2** |

Three fixes, all in `core/base_agent.py` so every agent inherits them:

1. **Request-scoped read cache.** Keyed per inbound request and destroyed when
   it ends — a cache that outlived the request would serve a stale reading to
   the next question, and dosing off a stale volume is the failure this project
   keeps finding. Writes invalidate their key, so a read-after-write inside one
   request cannot return the pre-write value.
2. **`retrieve_many`** on Hermes, `retrieve_own_memories` on the base. One round
   trip for many keys. Falls back to individual reads if the batch verb is
   unavailable — a performance fix that can lose data is not one.
3. **Guard decision cache, ALLOW only, 30s.** Denials are re-evaluated every
   time so a removed rule takes effect at once, and `state/LOCKED` is checked
   from local disk on every call before the cache is consulted — verified: the
   kill switch still returns 403 instantly.

**A regression was introduced and caught by checking correctness before
performance.** The batch returned entries one nesting level shallower than the
single read, so `_unwrap_value` looked too deep, every reading read as absent,
and the grow reported having no readings at all. A faster path that returns a
different shape is not a faster path — it is a second API nobody was told about.

**Independent of the deployment chain (Phases 6-8) and of identity (Phase 5).
Comes before the deferred multi-tenancy track, which would multiply it by the
number of tenants.** Nothing is broken today; this is cost, not correctness.

### The observation

Measured 2026-08-23 from a live call graph, last 500 log lines per agent, while
answering ordinary grow questions:

| Call | Count |
|------|-------|
| `grow_agent -> hermes (retrieve_memory)` | 166 |
| `hermes -> security_agent (check_guard)` | 229 |
| `grow_agent -> security_agent (check_guard)` | 7 |

One question can cost well over a hundred memory round trips. `answer()` picks
several of its own capabilities, and each one re-reads the plant record, the
reading index and the readings independently over A2A. Every one of those
reads is a JSON-RPC POST that Hermes then re-authorizes against the Security
Agent, so the guard traffic is larger than the memory traffic it protects.

### Why it is worth doing, and why not yet

It is not a correctness bug and no answer is wrong because of it. It is the
reason reasoning feels slow on this hardware (i5-4570T, 4 threads, no GPU),
and it is the first thing that will hurt under load - a second tenant doubles
it, a probe reporting hourly multiplies it again.

### Likely shape (not designed yet)

- A per-request read cache inside `answer()`, so the capabilities it calls
  share one read of the plant record and one of the readings rather than each
  fetching their own.
- Decide whether an agent reading **its own** memory needs a full guard round
  trip per read, or whether the guard belongs at the request boundary. That is
  a security decision, not a performance one, and must not be made casually -
  `check_guard` failing open already means an outage does not halt the swarm.

### Do not start this by

Caching across requests, or holding state in the agent between calls. The
platform is stateless by design and that property is worth more than the
round trips. The cache should live for one `answer()` and die with it.

### Training-data loop: source, review, count — done 2026-08-25

---

## Design track — Anansi as a spider on a web

**Not a phase.** Recorded 2026-08-31 because the principal expects it to change:
*"the idea might evolve later."* This is the shape of the idea and the one
constraint that must survive whatever it becomes.

### Where it came from

An Instagram build by `reznikov_engineering` - a particle humanoid with motion
tracking, gesture response, an "alive" background and sound. The principal
wanted something similar and immediately made it specific: *"a humanoid spider,
eight legs, spider legs obviously. But in the background... I would do a web."*

### It is a humanoid arachnid, and the skin is not the mechanic

Anansi IS the spider - the West African trickster-storyteller the agent is named
for - and the principal is specific that the form is **arachnid**, not a spider
and not a human: *"like the mythical beings, arachnids. Half spider, half
human."* Every other implementation of this idea is a glowing humanoid head
because there is nothing else for it to be. This one has a form that was already
true.

**Skins come later, and the architecture has to anticipate them now.** The
principal: *"you could turn it into a spider or you could have it utilize a
humanoid arachnid skin, but regardless of what's visually reaching into the web
domains, it is always going to be a spider leg pulling on a strand."*

That is the same separation this codebase already makes for the voice.
`config/anansi_voice.json` is a policy layer applied by `agents/anansi/voice.py`,
and the split exists because *a voice edit that can reach an agent is one
refactor away from being an authority edit*. The visual needs the identical
boundary:

| Layer | Holds | Changeable by |
|-------|-------|---------------|
| **Skin** | body, palette, particle style, how arachnid or how human | a config file, no code |
| **Mechanic** | a leg reaches into a region and pulls a strand | code, and only with the constraint below |

A skin that could change WHICH strand is touched, or make an unreached answer
look reached, is a skin with authority. Keep it unable to.

### The globe: what is outside the web

The principal's addition, and it lands on a distinction the system already
enforces in text:

> *"there'll be like this world or globe in a corner somewhere on top of the web
> - every time it does a generalized PQA internet search it'll touch that world
> that's wrapped in its web, or even pull on that string that brings the world
> into its hands."*

**The web is what the system holds. The globe is what it reaches for.** That is
exactly the line Legal's `answer()` draws: the corpus is authority, the public
web is *discovery*, and the difference is carried in the `source` field -
`corpus` versus `web_unverified` - so nothing downstream can mistake one for the
other. Asked "what is legal tender" with Title 31 absent, it went to the web,
returned the citation, and said in as many words: *"This paragraph is an
unverified web result and is NOT authority; it is a pointer to where the
authority lives."*

So the globe sits **outside the web and tethered to it by a single strand** -
reachable, not part of it. A leg touching a domain strand and a leg pulling the
globe in must not look the same, because they are not the same kind of knowing.
When a search finds a citation and that citation is then ingested, the strand
that pulled the globe becomes a strand of the web: acquisition, drawn.

### The web is the domain space; the legs are the reach

The principal's design, and it is better than "one leg per department":

> *"if there's some information that's needed, you'll see the spider leg go into
> the background, touch a certain portion of the web. This might be Grow's
> department or domain of the web. This might be Legal's, this might be
> Accounting."*

A leg extends into a region and touches a strand. The region is the department,
the strand is what within it - a citation lookup and a case-element assessment
are different strands of Legal, and they should not look the same.

Two properties fall out of that for free:

- **It shows when nothing was reached.** An answer given with no leg extended is
  Anansi answering from nothing. A glowing head cannot show that; it looks
  equally confident either way. This is the visual form of the same distinction
  the codebase already insists on - a check that found nothing must be
  distinguishable from a check that found the thing to be fine.
- **It is drawn from real traffic.** The interaction graph already knows which
  agents were consulted for a request and with what task. The legs are that
  data, not an animation loop. `hermes -> security_agent` at 13,407 calls is one
  strand worn smooth.

### Strands form BETWEEN domains, and legs do not stay home

The principal's extension, and it is what makes this a web rather than a wheel:

> *"cross connecting domains together with the web, to show two domains working
> together. Or a domain agent searching the globe... the legs don't always have
> to stay on the domain. When one agent talks to another, that's an interweb
> connection. It creates a spider tunnel effect between the two parties."*

Every one of those already exists as traffic, and each has a distinct meaning:

| What happens | Already implemented as | Drawn as |
|---|---|---|
| Anansi routes a request to a department | Boss `_domain_for` | leg from the body into a region |
| One domain borrows another's authority | `ask_peer_corpus` | **tunnel** between two regions |
| One domain hands another a finding | `refer_finding` / `receive_finding` | tunnel, directional |
| Several agents work one request at once | ordinary A2A fan-out | several legs at once |
| A domain reaches the public web itself | `search_public` from inside an agent | leg from a REGION to the globe, not from the body |

That last row is the one worth being careful about. When Legal answered *what is
legal tender* tonight it searched the web **itself** - the reach did not come
back through Anansi. So a strand can leave a domain region and touch the globe
without the body being involved, and drawing it as though Anansi went looking
would misreport who did the work.

**The hard part is that most traffic is not collaboration.** `hermes ->
security_agent` ran 13,407 times in two days, and `grow_agent -> hermes` 12,668.
Those are an agent reading its own notes and paying a guard check for it - the
read amplification Phase 3 was about. If every A2A call draws a tunnel, the web
is a solid sheet and nothing is visible.

So the tunnels are the **domain-to-domain** verbs, which are few and always
meaningful: `ask_peer_corpus`, `refer_finding`, `receive_finding`, and the shared
`case_*` events. Accounting borrowing Reg Z from Legal's corpus is a tunnel worth
drawing. Grow reading its own reservoir volume is not, however many times it
does it.

That distinction is not a rendering optimisation. It is the difference between a
picture of departments cooperating and a picture of infrastructure breathing.

Measured over 48h on 2026-08-31, out of **37,701** completed tasks:

```
   4   refer_finding
  99   receive_finding
 222   case_*  (13 case_add_evidence, 5 case_set_element, ...)
   0   ask_peer_corpus
```

**Roughly 325 tunnels against 37,701 calls - under one percent.** That is the
argument for the whole design: the web stays legible precisely because
collaboration is rare, and the rare thing is the interesting thing. Drawing all
37,701 would produce a sheet; drawing 325 produces a story.

`ask_peer_corpus` at zero is itself worth noticing - the verb exists and nothing
has used it in two days. Whether that is because no question needed a sibling's
authority or because the agents do not reach for it is a real question, and a
strand that never appears in the picture is how it would get asked.

### Two graphs, and each draws a different part

The principal: *"all the KAG is is a visual layer. The visuals can change."* Half
right, and the half that is not decides what gets built. The **rendering** is a
visual layer and is entirely swappable - web, node-link, table, whatever the
creator's vision and the installed capabilities allow. The **graph** is not. It
is a data store: `state/graph.db` holds that the Housing Authority may pay the
$791 and the principal may not, and that remains true with every renderer
deleted.

There are two graphs, and the useful thing is that they map onto two different
parts of the spider:

| | Source | Holds | Draws as |
|---|---|---|---|
| **Knowledge graph** | `state/graph.db` | what RELATES to what - case, parties, instruments, obligations | **the web** - its regions and strands |
| **Interaction graph** | `state/audit.db` | who TALKED to whom, just now | **the legs** - what is reaching |

Standing structure versus live motion. The web exists whether or not anything is
happening and changes slowly, when a case gains a participant or a corpus gains
a section. The legs exist only while something is being answered.

Which means the web is not a backdrop - **it is the knowledge graph, drawn**. A
department holding nothing looks sparse because it IS sparse, and that is
information rather than a rendering flaw.

### What the case actually holds

The principal, correcting a projection that had stopped at parties and amounts:
*"it's not just Housing Authority - Housing Authority, VA, me benefiting from the
interaction, contracted party, the corporation renting a space from, and then
there's the instruments on the ledgers."*

Right, and the graph now carries it: **21 nodes, 21 edges** - 1 case, 6 parties,
3 obligations, **11 instruments**, each with what it DOES rather than merely that
it exists:

```
GOVERNS         Residential Lease Contract - 7333 Potranco Rd Apt 5201
EVIDENCES       Resident Ledger, Villas at Costa Brava, unit 05-05201
APPOINTS        Fiduciary appointment letter
AUTHORISES      FHCST Client Authorization Form, signed 8/28/2026
ESTABLISHES     VA disability rating decision
REQUESTS_UNDER  Formal Request for Reasonable Accommodation
DIRECTS         Instruction to Texas Fair Housing: proceed to a HUD complaint
```

A lease is what CREATES an obligation; a resident ledger is what EVIDENCES
payment against it; an authorization letter is what MAKES a representative one.
Those are relationships, and a graph that omits them is a contact list. Only
instrument kinds cross - an email is correspondence, and copying every document
in would put the case file into a second store.

**And the projection immediately found something a list never would.** The case
carries `principal` as a participant and `Anthony Hanlan` as an authorised
payor - one human, two nodes, because the two facts arrived by different paths.
It is **reported, not merged**: deciding two names are one person is an identity
judgement and belongs to the principal, not to a projection script. The same
rule as `contested` in the claim pipeline - surface the conflict, do not resolve
it quietly.

### The constraint that must survive any redesign

**The visual is derived from the register and the payload, never chosen for
effect.** `config/anansi_voice.json` already carries seven registers with
numbers - `low_stakes 1.0`, `technical 0.6`, `security 0.4`, `legal` and
`financial 0.35`, `sensitive 0.25`, `safety_critical 0.1` - and
`Voice.register_for()` already decides how much personality the WORDS get. The
same call drives the particles, so the spider physically cannot look relaxed
about a contested claim: at 0.1 it goes still and sparse because the words did.

The failure this prevents is exact and has already happened once in text. The
voice layer discards a telling if a number is lost or invented - an opener
asserting *"All 7 of your readings are perfect"* was refused because the 7 was
not in the payload. **A visual can tell that lie in a form the guard cannot
catch, because nobody fact-checks a glow.** An avatar reaching confidently into
a strand while the payload says `insufficient_evidence` is that same opener,
rendered.

So: leg extension comes from what was actually consulted, and manner comes from
the register. Neither is an art direction decision - which is precisely what the
skin layer above IS, and why the two must not share a file.

**Three things the visual must be able to distinguish**, because the reasoning
layer already does and a picture that blurs them is worse than no picture:

| The system did | Should look like |
|----------------|------------------|
| answered from its own corpus or records | a leg into a domain strand |
| answered from a public search | a leg pulling the globe, visibly outside the web |
| answered from nothing | **no leg extended at all** |

The third is the one no other build of this can do. A glowing head narrating
from nothing looks exactly like a glowing head narrating from evidence.

### Honest cost

The reference implementation is motion tracking, gesture recognition, particle
systems and sound - substantial work, and it competes with Phases 1-5, all of
which are being paid for daily. This is a *want*, and wants are legitimate; it
should not quietly become the next thing while a lollipop from nine days ago is
still the kind of fact that goes unrecorded.

Two practical notes: it is Canvas or WebGL inside the PWA that already exists,
so no new stack - and it renders on the phone's GPU, which means the 7 GB
no-GPU box is not the constraint here for once.

### Do not start this by

Building the particle system first. The renderer is the part that is easy to
find tutorials for and the part that is worth the least. Start by deciding what
a leg reaching means and where that data comes from - if a leg can move for a
reason nobody can point at, the whole thing is decoration with extra steps.
