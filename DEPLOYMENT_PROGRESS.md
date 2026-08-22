# Mycelial Dedicated-Device Migration — Progress

Tracking doc for the "move Mycelial off the daily-driver machine" work.
Full plan/rationale: `/home/anureyki/.claude/plans/based-on-the-system-tidy-valiant.md`

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
what would be Phase 2 (hardening) below. Left untouched to avoid collisions
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
- [ ] When ready to actually cut over host port 8081 to the container, stop the live tmux/`start_all.sh` instance first (they'll conflict on the port) — this is Phase 4, not needed until the dedicated device exists

## Phase 2 — Harden network exposure — NOT STARTED
Reverse proxy + TLS + auth in front of Anansi only; wire Security Agent
(9010) to actually gate requests to Boss. Checked `core/base_agent.py`:
every agent (including Anansi) has *always* bound to `127.0.0.1` in
committed code — that's not an in-flight change, it's the existing
baseline, which is why the Docker `socat` forwarder was the right fix
rather than a workaround for someone else's WIP. No reverse-proxy/TLS/auth
code exists yet anywhere in the repo. Still fully unstarted.

## Phase 3 — Provision dedicated device — NOT STARTED
User decision pending: mini PC (Intel N100/N305 class, 16-32GB RAM, no GPU
needed) recommended in the plan. Not yet purchased/chosen.

## Phase 4 — Migrate and cut over — NOT STARTED
Blocked on Phase 3.

---

---

## Phase 5 — Identity and authorization (DID / verifiable claims) — NOT STARTED

**Comes BEFORE multi-tenancy, which is now Phase 6.** The ordering matters:
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

## Phase 6 — Multi-tenancy — NOT STARTED, DELIBERATELY DEFERRED

**Do not start this before Phase 5 (identity/authorization) is done and the single-user system has been
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
reverse proxy (Phase 2), per-tenant rate limits and quotas, provisioning and
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
