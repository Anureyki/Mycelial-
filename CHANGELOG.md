# Changelog

Build history for Mycelial, moved out of `README.md` on 2026-08-27. The README
had become a session-by-session changelog, which meant the thing a reader hits
first was a retrospective rather than a description of the system. Entries are
newest-last, as they were written, and are left unedited - including the ones
later work has overtaken.

For what the system *is* now, see `README.md` and `docs/system-map.html`.
For what is planned, see `DEPLOYMENT_PROGRESS.md`.

---

## Build log

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

### 2026-08-29 — Legal reasons in uniform sections, not one state's citations

A 12-slide AI-generated carousel walking an auto receivable from an Arizona
dealer to a national bank was offered as teaching material. The framework in it
is sound — a financed transaction is a *sequence* of legal events, and confusing
the stages is the standard error in this area. But every state citation in it
is Arizona's, and the principal operates in Texas and expects to move.

So the framework was taken structurally and the jurisdiction was made a variable:

- **`reference/legal_agent/transaction_layers.json`** — 12 stages, federal layer
  separated from state layer, reasoning in **uniform** UCC section numbers.
  `applies` and `caution` fields carry this system's own corrections, which are
  not in the original: Reg T reaches only broker-dealer securities credit, Reg U
  needs *both* purpose credit and margin stock, securitization operates on the
  pool and not on the obligor's contract, and § 25b made preemption rule-by-rule
  rather than categorical.
- **`reference/legal_agent/jurisdictions.json`** — 51 jurisdictions. UCC 9-203
  is `A.R.S. § 47-9203` in Arizona, `Tex. Bus. & Com. Code § 9.203` in Texas,
  `Cal. Com. Code § 9203` in California and `Fla. Stat. § 679.2031` in Florida.
  Four states, four renumbering conventions — which is why this is a **verified
  table and not a format string**. Those four were confirmed against the states'
  own published text; the other 47 carry candidates labelled unverified, and a
  projected citation says it was projected.
- **`cite_in_jurisdiction`, `transaction_layers`, `set/get_operating_jurisdiction`**
  — the state layer resolves against a stored operating record that merges
  rather than rebuilds, so updating a residential state cannot erase a business
  one.

Florida is the reason for the caution: naive templating turns 9-203 into
`679.203`, which is a different provision. The real answer is `679.2031`.

**Federal corpus, which does not vary by state:** 12 CFR Parts 1002 (Reg B),
1026 (Reg Z), 220 (Reg T), 221 (Reg U), 7 and 34 (OCC) — 1,283 sections from
eCFR, public domain. Legal's corpus goes from 8 works / 1,014 sections to
14 works / 2,297 sections and 5,654 subject terms. `§ 221.125`, the section the
carousel actually cited, now resolves out of the corpus.

Arizona law was deliberately **not** ingested. It is one state's enactment of
provisions the agent already reasons about uniformly, and holding it would
invite citing Arizona at a Texas problem.

Two fixes fell out:

- `--agent legal_agent` wrote to `reference/legal_agent/` while the agent read
  `reference/legal/`, so six freshly ingested parts were invisible. Standardised
  on the agent id, matching what accounting already used.
- `refer_finding` was a method but never a dispatched task, while
  `receive_finding` was — an agent could accept a referral but nothing could ask
  one to make one. Half a pipeline that `CLAUDE.md` documents in both
  directions. Stage 6 (funding the dealer is a separate event from the buyer's
  obligation) now reaches Accounting through it.

### 2026-08-29 — A corpus every agent can reach, and a third cross-domain direction

Two open items from the capability sweep, plus what they turned out to be
symptoms of.

**Accounting's corpus was unreachable — and so was everyone else's.** Its
2,108 sections of the Securities Acts and Reg S-X/S-K had no subject index, and
underneath that a worse fault: `accounting_agent` had no corpus loader at all.
Its `lookup` went cache → web → model and never opened the books it owned. The
identical bug had been found and fixed in the Legal Agent weeks earlier — *in
the Legal Agent*, where it could not help anybody else.

A fix made in one agent for a fault that lives in the base class is not a fix;
it is a second place for the bug to hide. So the loader moved to
`core/base_agent.py`, Legal now inherits it, and the audit that followed found
`trust_agent` carrying the same latent omission — a `lookup` that would ignore
any corpus dropped into `reference/trust_agent/`. Fixed before it had anything
to ignore.

The reason it survived so long is that the load was **lazy**: an agent with an
unreachable corpus logged exactly what an agent with no corpus logged, which is
nothing. Agents now announce their corpus at boot, and one holding sections it
never calls `lookup_reference` on says so loudly. Legal reports 1,353
citations / 5,190 subject terms / 4,204 authorities across 14 works; Accounting
1,350 / 3,194 across 3; Trust honestly reports zero.

Subject indexes were rebuilt for all three accounting works from the stored
sections — no re-ingest, and written atomically so a reader never catches a
half-written corpus.

**`ask_peer_corpus` — borrowing, not copying.** Accounting owns ASC, IFRS and
the reporting regulations. Legal owns the statutes, the CFR, the state codes.
A figure in the books is routinely governed by an authority in Legal's corpus,
and Accounting has no business shelving a second copy of it — two copies is two
sources of truth and the one that drifts is always the copy.

So Accounting asks Legal before it asks the open web. Verified end to end:
`lookup "§ 1026.2"` at Accounting returns Regulation Z out of Legal's corpus,
labelled `legal_agent corpus (cross-domain)` with a note saying Accounting
neither holds nor interpreted it. Its own material still resolves locally.

Two deliberate constraints: there is **no keyword test** for "is this a legal
question" — guessing a subject from vocabulary is the router failure this
architecture exists to prevent, so it simply prefers a sibling's verified corpus
over an unverified web search every time. And it accepts **only** an answer
whose `source` says *corpus* — never the peer's cache, web fallback or model
output, which would launder an unverified answer across a domain boundary where
the borrower cannot tell.

**Port 8090 retired.** `start_all.sh` no longer serves the webapp with
`http.server --bind 0.0.0.0`, `webapp/serve.sh` binds loopback, and
`mycelial.service`'s stop pattern was cleaned up with it. nginx on 8443 already
served the same webapp and proxied the same Anansi endpoints behind TLS 1.3 and
basic auth — verified before removing anything.

**Port 9081 could not be finished from inside the stack.** It is
`anansi-forward.service`, a systemd unit with `Restart=always` plus a ufw rule
opening it to the LAN, so killing the process accomplishes nothing.
`deploy/systemd/retire_anansi_forward.sh` does it in one privileged command.
Explicitly does not touch `docker-entrypoint.sh`, which uses 9081 *inside* the
container and publishes it to the host as loopback-only.

Also: the 13 platform services launched buffered, so their logs lagged behind
reality — `python3 -u` now, matching how the agents were already started.

### 2026-08-29 — Legal tests a claim instead of believing it

A claim that quotes a real statute is still a claim. The failure mode in this
area always has the same shape: a genuine provision is cited, the words in it
resemble the outcome somebody wanted, and the resemblance is treated as the
holding. `core/claim_assessment.py` walks one path and only one —
claim → source → evidence → observation → analysis → conclusion → confidence —
and the default conclusion is `unsupported` at every stage not actually filled.

Three rules make it a test rather than a ratification:

- **A citation is not an authority until the text is in hand.** `claim_cite`
  decides `located_in_corpus` by looking it up, never by the caller saying so.
- **Reproducibility is its own axis** — `reproduced` / `not_reproduced` /
  `untested` / `untestable`. A claim with no specified procedure is reported as
  uncheckable-by-anyone, which is a reason for suspicion rather than a neutral
  gap.
- **`asserted_by` is recorded and never scored.** Verified: the same statement
  returns `unsupported / 0.0` whether attributed to an Instagram reel or to the
  principal.

An eight-part rights ontology replaces the habit of collapsing everything into
ownership — `ownership`, `possession`, `control`, `custody`, `security_interest`,
`priority`, `authority`, `enforcement_right` — each naming the uniform provision
that defines it, so the agent can conclude *ownership is established but the
claimed control is not*.

Legal and Accounting are allowed to disagree. `claim_corroborate` asks;
Accounting's `assess_assertion` answers from its own records and defaults to
`undetermined`, never `agrees` — an agent that concurs with nothing to check is
worse than one that abstains. An unresolved conflict makes the claim
**`contested`**.

That last state came out of testing this module against itself: a fully
established claim plus an active cross-domain disagreement still concluded
`supported`, with the conflict demoted to a quieter confidence number. That is
forced consensus by another route, and it is exactly what this was built to
prevent. Fixed before commit.

First real run, on the UCC control claim recorded earlier: `unsupported`,
confidence `0.0` — every provision it rests on (9-322, 9-327, 9-104) came back
`located_in_corpus: false`. Which surfaced a genuine gap rather than a verdict:
**Legal reasons about uniform UCC sections but has never read one.** The text is
not in the corpus. Recorded as the next thing to close, not papered over.

### 2026-08-29 — Legal reads the statute; Accounting reads the manual

The claim pipeline's first run returned `unsupported` on the UCC control claim
because every provision it rested on — 9-322, 9-327, 9-104 — was absent from
the corpus. Legal reasoned about uniform section numbers all morning and had
never read one. Closed.

**Tex. Bus. & Com. Code ch. 9** — 141 of 142 sections (9.521 failed), 655
subject terms. Texas because Texas is the operating jurisdiction on record.
The *model* UCC is ALI/ULC copyrighted; a state's **enactment** is public domain
(edicts of government — *Georgia v. Public.Resource.Org*, 590 U.S. 255 (2020)),
and the enactment is also the text that actually governs here. So this is the
correct source, not a workaround for the copyright.

`claim_cite` now resolves a uniform section through the operating jurisdiction
before concluding an authority is missing — otherwise the agent owns the text
and still reports it cannot find it. `9-322` → `Tex. Bus. & Com. Code § 9.322`
→ statute text in hand. The same claim moved from `unsupported / 0.0` to
`prerequisite_missing`: it now has authority and still has ten questions to
answer, which is the pipeline behaving correctly rather than being satisfied.

And `cite_in_jurisdiction` now lets the corpus promote a citation to *verified*.
The jurisdiction table had four states checked against their published text;
holding the enactment under that exact key is stronger proof than any external
check, so all 141 Texas sections are now verified rather than projected.
Verifiable state outranks the table's own bookkeeping.

**`tools/ingest_law.py`** acquires any CFR part, U.S. Code title or IRM part
from the official source in one command. Deliberately fetch-on-demand rather
than a bulk mirror: the corpus is retrieved by exact citation, so an unread
title costs disk, boot and index size while contributing nothing. The full CFR
plus the full Code is several million sections on a 7 GB machine.

**Internal Revenue Manual Part 5** (Collecting Process) — 20 MB, 10,233
sections — went to **Accounting, not Legal**, and that placement is a safety
property rather than tidiness. The IRM is not law: it binds IRS personnel and
confers no rights. Legal's corpus is *authority*, and the claim pipeline weighs
whatever it can open as potentially governing — guidance shelved there would be
scored as though it governs, and nothing downstream could tell. In Accounting it
is the live column: the Code and 26 CFR are the floor, the IRM is how the floor
is administered against real books.

Every work now carries `authority_class` and `authority_class_basis`. Writing
that field caught the exact error CLAUDE.md warns about — the first pass derived
class from the title and returned `unknown` for the Delaware Statutory Trust
Act. Corrected, with the basis recorded: for a statute or regulation the title
*is* the citation and fixes the class; anything else must be read or stay
unknown.

Two ingest bugs fixed on the way. `fetch_irm` matched chapter URLs on the
un-padded part number, so it retrieved a 12 KB index page with no law in it —
and `ingest_pdf.py` correctly refused to store the result rather than filing an
unusable blob, which is how it was caught. The section splitter now recognises
IRM dotted citations (`5.1.1.4.2`), requiring three or more components so it
cannot swallow CFR citations like `210.1-01`.

### 2026-08-29 — Anansi's voice becomes a policy layer that cannot reach the facts

The personality was constants and a method inside `Anansi.py`, written for one
grow. It could not change without editing the agent — and a voice edit that can
reach an agent is one refactor away from being an authority edit. Moved to
`config/anansi_voice.json` + `agents/anansi/voice.py`; the agent drops from 317
lines to 224 and hot-reloads the policy with no restart.

**The guarantee is enforced, not intended.** Every number, date, unit, currency
amount and citation is extracted before the telling and checked after. Lost,
altered or invented, and the telling is discarded and the plain text ships.
Tested against a deliberately hostile config — an opener reading "All 7 of your
readings are perfect" was refused because the 7 was not in the payload. Nothing
calls a model; there is no gap to fill.

Writing the check immediately caught two defects in the voice it replaced:

- **It was dropping facts.** The "Not yet - 2 thing(s) in the way" lead was
  discarded whenever an opener covered it, taking the count with it. A
  fact-bearing sentence is now never dropped.
- **It was making determinations.** The `steady` openers — "All quiet",
  "Everything is sitting where it should" — assert a state, and they were the
  default for anything unclassified. A contestable $550 obligation was narrated
  as everything sitting where it should. That is not a tone problem, it is
  Anansi exercising authority he does not have. `steady` must now be earned by
  the payload saying so; the default is no opener.

Seven registers scale personality by stakes, most serious match winning:
`low_stakes` 1.0 → `safety_critical` 0.1, with `sensitive` at 0.25 where the
trickster stands down entirely. `narrate_contradiction` gives the trickster its
actual job and refuses a one-sided contradiction, which is a guess rather than
a finding.

### 2026-08-29 — "How's the system today" reached a code model

Asked from a phone, that question came back as *"As a language model, I don't
have feelings, so I can't say how the system is today."* Three faults stacked.

**Boss's default was the code model.** Every request no agent claimed went to
`coding_agent`, whose model is `deepseek-coder`. That is the same route that
turned "how long until it falls to 238" into a physics free-fall problem and
"DWC" into "Direct Water Cooker" — the failures CLAUDE.md opens with, still
wired in as the fallback. A code model is a specialist; handing it everything
unclaimed is a misroute with a default attached. It now asks the Inference
Service for the `reasoning` capability, and if that returns assistant
boilerplate — "as a language model", "I don't have feelings" — the answer is
discarded in favour of saying plainly that nothing claimed the question.

**The status capability existed and was unreachable.** `_get_system_status()`
was gated behind six exact strings: "system status", "all agents", "how is
everything"… "How's the system today" matched none. Adding that sentence would
have fixed the sentence and not the class, so it now matches the *shape* — a
subject word plus a state word, or a standalone like "is anything broken".

**The answer was a roster dump.** It opened "12 of 12 registered agents are up:"
and then listed every internal agent id. Nobody asking how their system is doing
wants to read `pqa_agent`. It now leads with whether anything needs the grower
and names departments in human terms: *"Nothing needs you - all 12 departments
are answering"*, or *"1 of 12 is down and that needs you: the grow."*

Found because the grower asked from their phone and got the boilerplate, which
is the only reason any of it surfaced.

### 2026-08-29 — Routing resolves intent instead of counting keywords

The grower's objection, verbatim: *"I don't understand why it's trying to match
keywords... I should be able to speak plainly and it already understands my
intent."* Correct, and the shape-matching fix earlier the same day was still
keyword matching with a wider net — the mechanism was wrong, not the list.

`core/intent.py` resolves intent by asking the reasoning model which department
owns a request. **This is the only place in MycOS where a model decides
anything**, and three properties make it different in kind from letting one
generate a fact:

- **Closed set.** It chooses from the live registry and anything not in it is
  discarded as `UNCLEAR`. It cannot invent a department.
- **Recoverable.** A wrong route means the wrong department is asked and says so.
  Nobody is told something false — they are told the wrong thing was asked.
- **No content crosses.** It returns an agent id. The model never sees,
  summarises or rewrites an answer.

The word count survives as the fallback for when inference is down, and both are
logged when they disagree, which is the only evidence for whether this is
actually better.

Two bugs in the first cut, neither the model's fault. Regex anchors leaked into
the prompt — `\bgaap\b` was shown as `bgaapb` — because backslashes were
stripped before the `\b`. And the departments were described by their first
fourteen *routing terms*, which were written to be matched rather than read:
grow_agent read as "stage, how old, taproot, cotyledon, photo" with nothing
about water or nutrients, so "my water is two inches below the net pot" routed
to **legal**. Describing departments by their capability names instead took it
from 1 of 6 correct to 5 of 6.

**The grow fallback is removed.** Any unmatched question used to go to
grow_agent before the generic model — a reasonable patch when word-counting was
all there was, and it carried a comment listing five real failures it fixed. It
had started causing them instead: "how much do I still owe on rent" routed
correctly to accounting, accounting had no answer, and this caught it and
replied about ppm and veg bands. A domain-specific default inside the
orchestrator is the violation this architecture exists to remove.

**A department that claims a question and cannot answer it now says so, and no
model speaks on its behalf.** "Is the disk filling up" was answered by a 1.5B
model with a numbered essay on why disks fill up, having no access to the disk.
Two findings that were being collapsed into one are now distinct: nobody
claiming a request is a routing gap, a department claiming it and having nothing
to say is a capability gap, and the person deserves to know which they hit —
it tells them whether to rephrase or to stop asking.

### 2026-08-29 — Accounting can answer for itself

The honest "the capability is missing" message did its job immediately: asked
"how much do I owe my rent based on the ledger", routing was correct and
Accounting stood there mute. It held two live obligations, eight evidenced
payments and every payor authorised — and implemented no `answer()`, so the
base returned nothing.

Built. Deterministic, reads the record, no model:

    Rent - resident portion: 459 monthly, payable by Anthony Hanlan. 4 payments
    recorded totalling 1898, covering 2026-06 to 2026-08. Rent - HAP voucher
    subsidy: 791 monthly, payable by Housing Authority (HAP). 4 payments
    totalling 3087, covering 2026-06 to 2026-08. Together that is 1250 a month
    across 2 obligations.

**And it refuses to state the number it was asked for.** An outstanding balance
needs the periods the tenancy covers and a starting position, and neither is in
the ledger — so the arithmetic would be an assumption wearing a decimal point.
In a rent dispute a confident wrong number is worse than an honest gap, so it
says which is missing and reports what it can stand behind instead: every
payment evidenced, every payor authorised.

The voided $1,450 obligation — fabricated demo data from 2026-08-28 — stays
excluded, as does its payment.

### 2026-08-29 — The lease arrives and Accounting can reconcile

Page 1 of the lease supplied the two facts `answer()` had named as missing: the
tenancy start (2026-05-04) and the periodic amount ($1,250.00 base rent). Also
prorated first period $1,138.06, security deposit $99.00, late fee 10% after the
5th, and a set of fee terms recorded with it.

**The base rent reconciles exactly with what was already on file.** $459
resident portion plus $791 HAP subsidy is $1,250 — two independent sources
agreeing, which is worth more than either alone. It also confirms the $1,450
obligation voided on 2026-08-28 as the fabrication it was.

`set_lease_terms` and `reconcile` built, and the arithmetic is done **in
Accounting**, not handed to it: charged 4,888.06 over four periods (prorated May
plus three full months), paid 4,985.00, leaving **96.94 in credit** as of
2026-08-29. `answer()` now reports it rather than declining.

Two things deliberately NOT folded in, because including them silently would be
the more dangerous error:

- **Animal rent $10.00/month** is listed on the lease as additional recurring
  rent. It is not in the reconciliation, because nothing in the record says
  whether it applies. If it does, four periods of it move the balance.
- An earlier reading of the rent ledger document put the position at **-332**.
  This reconciliation says -96.94. Two figures from two sources is a divergence
  and the divergence is the finding — it is recorded, not averaged away.
