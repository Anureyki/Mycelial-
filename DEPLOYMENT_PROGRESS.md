# Mycelial — Roadmap and Progress

Ordered work for MycOS. It began as the "move off the daily-driver machine"
plan, which is why the deployment phases carry the most detail.
Full plan/rationale: `/home/anureyki/.claude/plans/based-on-the-system-tidy-valiant.md`

## Order

**Deployment is last.** Provisioning a device and cutting over to it is the
step that ends the sequence, not one in the middle - everything shipped to a
dedicated machine should already be correct, lean and authorized. Phases were
appended in the order they were discovered rather than the order they should
run, which put retention and maintenance work *after* cutover. Renumbered
2026-08-24 to reflect dependency instead of discovery.

| # | Phase | Status |
|---|-------|--------|
| 0 | Remove orphaned crash-looping systemd units | ✅ done |
| 1 | Containerize for portability | ✅ done (smoke-tested) |
| 2 | Retention: what to keep, and on what evidence | not started |
| 3 | Reduce A2A read amplification inside a single answer | not started |
| 4 | Grow captures spoken facts itself | not started |
| 5 | Identity and authorization (DID / verifiable claims) | not started |
| 6 | Harden network exposure | not started |
| 7 | Provision dedicated device | not started |
| 8 | Migrate and cut over | not started |
| — | *Deferred track:* multi-tenancy | not scheduled |

2-4 are internal: they make the system cheaper, leaner and more truthful about
what it knows, and none of them need hardware. 5 must precede any exposure -
multi-user without strong identity is an apartment block with one key on the
front door. 6-8 are the deployment chain, in dependency order. Multi-tenancy
is a rewrite of the trust model rather than a deployment step, so it is a
deferred track and not a numbered phase.

## Phase 0 — Remove orphaned crash-looping systemd units ✅ DONE
Five stale units (`mycelial-boss_agent`, `mycelial-codingagent`,
`mycelial-security_agent`, `mycelial-datagatherer`, `mycelial-dashboard`)
were leftover from the `AgNetworking/mycelial-snapshot` era — their
`ExecStart` pointed at flat file paths like `agents/boss_agent.py` that no
longer exist after the repo moved to per-agent subdirectories
(`agents/boss_agent/boss_agent.py`). They had racked up 22,000+ restarts
before being caught. All 5 are now confirmed **inactive**, all unit files
and symlinks removed from `/etc/systemd/system/`. No further action needed.

## Phase 1 — Containerize for portability ✅ DONE (smoke-tested)
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

## Phase 2 — Retention: decide what to keep, and on what evidence — NOT STARTED

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
## Phase 3 — Reduce A2A read amplification inside a single answer — NOT STARTED

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

## Phase 4 — Grow captures spoken facts itself — NOT STARTED

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

## Phase 5 — Identity and authorization (DID / verifiable claims) — NOT STARTED

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

## Phase 6 — Harden network exposure — NOT STARTED
Reverse proxy + TLS + auth in front of Anansi only; wire Security Agent
(9010) to actually gate requests to Boss. Checked `core/base_agent.py`:
every agent (including Anansi) has *always* bound to `127.0.0.1` in
committed code — that's not an in-flight change, it's the existing
baseline, which is why the Docker `socat` forwarder was the right fix
rather than a workaround for someone else's WIP. No reverse-proxy/TLS/auth
code exists yet anywhere in the repo. Still fully unstarted.

## Phase 7 — Provision dedicated device — NOT STARTED
User decision pending: mini PC (Intel N100/N305 class, 16-32GB RAM, no GPU
needed) recommended in the plan. Not yet purchased/chosen.

## Phase 8 — Migrate and cut over — NOT STARTED
Blocked on Phase 7.

---

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

