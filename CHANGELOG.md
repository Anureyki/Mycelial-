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

### 2026-08-29 — Grow could not see symptoms on new growth

The grower did exactly what `evaluate_leaf` asked - turned the leaves over,
looked underneath, found nothing - and the answer got WORSE. Reporting "no
webbing, no specks, no frass" fell through every pattern into the generic
"disease, pest, airflow obstruction, or severe damage signal detected".

Two gaps behind it:

- **`margin_burn` matched "burnt tips" and "crispy tips" but not "necrotic
  tips", "dead tips" or "brown tips"** - the same leaf described in ordinary
  words matched nothing.
- **No pattern existed for symptoms on NEW growth at all.** The `interveinal`
  entry already states the rule - mobile nutrients pull from the oldest growth
  first, immobile ones show on the newest - and nothing could detect which was
  happening. That is the diagnostically loaded half of the distinction, so
  `new_growth_distress` now carries it: pale, hooked, twisted or dying new
  growth, and the tell that old fan leaves stay dark.

Its candidates are immobile-nutrient shortfall (calcium above all), root-zone
uptake failure (warm or under-oxygenated water, pH outside the band, which
starves new growth while the reservoir reads full), and light too close. Its
settling check separates them by WHERE, by lifting the roots, and by pH and
water temperature rather than ppm alone.

Also recorded this session: ppm corrected to 687-700 (logged at 687, the lower
bound, with the range in the note), reservoir down to 13 L, pellets wet with new
roots emerging, and `current_plant` given the species it was missing while both
other plants had one.

### 2026-08-29 — A fabricated humidity reading, and a pest question already answered

**`validate_environment_targets` was inventing measurements.** Asked about
humidity it replied "our current reading of 80% is within this range" while the
humidity field on the latest reading was `null`. It also answered the water
temperature and light questions with the ppm figure, and called pH 5.92
"slightly outside" a 5.8-6.2 band that contains it.

Cause: every metric's prompt received all four readings and the instruction
"does our current reading fall in that range". A model handed a null and asked
whether the reading is in range will supply a reading. Now each metric receives
only its own value; where there is none the prompt says so and asks for the
range alone - and the assessment is discarded regardless of what the model
writes, because the instruction is not the guarantee.

**A negative pest inspection is now evidence that persists.** The grower turned
the leaves over, found nothing, and said plainly not to be asked again. A
`pest_inspection` note recording that is read by `evaluate_leaf`, which strips
the mite-and-thrip sentences out of its differential rather than printing the
whole procedure and appending a correction after it. Printing "turn the leaf
over and look for webbing" and then saying to ignore it still asks the question.

Recorded alongside: pH 5.92, ppm 693, water 21.7-22.2C logged at the high end
because the warm end is what matters for dissolved oxygen. `evaluate_reservoir`
puts 22.2C outside its 18-22C band and calls it a root-health parameter rather
than comfort - while the aggregate still reports "stable, no reservoir change
needed" at 9/10. That masking is noted and not yet fixed.

### 2026-08-29 — Six of seven rejected training images were ours to reject

The grower reported that some accepts in the Training tab came back saying the
image could not be downloaded. 25 candidates had downloaded cleanly and 7 had
failed, for three reasons - and two of the three were our fault, not the
internet's.

- **`not an image (application/octet-stream)`, 3 candidates.** The fetcher
  trusted the `Content-Type` header, which a great many CDNs set to
  octet-stream for a perfectly good JPEG. It now sniffs the leading bytes -
  JPEG, PNG, WebP, GIF, AVIF - because the bytes cannot lie about their own
  format and the header routinely does. Same rule as reading health from the
  port rather than the registry.
- **`image larger than 8MB`, 3 candidates.** For a training set a large
  photograph is MORE useful, not less: it downscales, and the detail is the
  point. Raised to 40MB. One recovered file is a 23.9MB PNG.
- **`HTTP 403`, 1 candidate.** Requests now carry a browser User-Agent and the
  source page as Referer, which defeats ordinary hotlink protection. This one
  still 403s, so it is genuinely blocked rather than mishandled.

Re-running the seven recovered six. The set goes from 25 to 31 images across
all ten labels.

Also, the message. A failed accept said "Accepted, but the image could not be
downloaded" - leading with the word that reads as success. It now leads with
NOT COUNTED and says plainly that the accept was recorded, no file reached the
label folder, and there is nothing to redo.

Noted and not fixed: 3 of the 31 files are byte-identical duplicates, so
distinct candidate records can point at the same image.

### 2026-08-29 — The training set refuses duplicates, and refuses contradictions louder

Two candidate records from two search runs pointed at the same image and both
were downloaded. A duplicate inside one label is wasted count - the campaign
reads 6 examples where the model will only ever see 3, so the threshold is
reached on paper before it is reached in fact.

The same image under TWO DIFFERENT labels is worse than waste. Identical pixels
taught as `nutrient_burn` and as `thrips` teach a model that the feature does
not discriminate, which is exactly how a classifier acquires false positives and
false negatives. That case is refused as a LABEL CONFLICT and named as one.

Detection runs on the bytes before the file is written: sha256 for an exact
re-download, and a 64-bit average hash for the same photograph resized or
recompressed - which is what image search actually serves, one picture at four
sizes from four hosts. Fingerprints are cached by path+mtime+size so an accept
does not re-hash the whole set.

Verified on four cases: identical bytes same label caught, identical bytes
across labels flagged as a conflict, a half-size 70%-quality re-encode caught by
perceptual hash, and a genuinely different image correctly allowed - no false
positive.

Three existing duplicates removed; the set is 28 files and 28 unique.

A duplicate is also no longer reported as a download failure. It said "the image
could not be downloaded: already in the set", which sends the grower looking for
a network problem that does not exist. It now says the image downloaded fine and
was deliberately kept out, with statuses `duplicate` and `label_conflict` to
match.

### 2026-08-29 — Plants hold measurements; the agent holds understanding

Asked whether the system knew GSC2 was in 1 L of plain DI water, the answer was
no - and worse. `current_stage`, `germination_date`, `current_strain` and
`current_nutrients` were stored under GLOBAL keys with no plant in them, so
asking for gsc_auto_2 returned current_plant's veg stage, its germination date
and its full nutrient mix. A seedling in plain water reported Cal-Mag 8.6 and
FloraGro 15.5, and anything reasoning from that was reasoning about the wrong
plant.

Those fields are per-plant now, read from the record the system already keeps
rather than a second namespace, and a plant with no value of its own reports
the gap instead of borrowing one.

**The first fix for lessons was wrong and the grower caught it.** It had plant
two reading plant one's notes - the same coupling wearing a different hat, with
data still flowing plant to plant. The reasoner is the AGENT. A lesson is
something Grow Agent learned, stored against the agent with an explicit
`applies_to`, and applied to whatever plant is in front of it. Nothing crosses
between plants in either direction.

`applies_to.species` is mandatory: unscoped knowledge gets applied to a tomato.
Filters refuse to generalise across species, across system types, and across
stages a lesson was not written for. Verified - the DWC plant gets all three
cannabis lessons, the LWC seedling gets two and not the DWC-specific one, and
the aloe gets only its own, with no mention of cannabis, ppm or Cal-Mag
reaching it.

Two matching bugs found by that test. The default plant carries no species in
the per-plant index, so the agent applied none of its own knowledge to the very
plant that taught it; species now falls back to the system record. And exact
matching on `system_type` denied the DWC plant its DWC lesson, because the
system is recorded as `top_fed_dwc` - matched by containment now, which still
keeps lwc and dwc apart.

Three lessons recorded from the first grow: ramp the feed from the start, on
DI or distilled water Cal-Mag is not optional, and in top-fed clay pebbles
nothing buffers a dose so raise ppm in small steps.

### 2026-08-29 — A dose the dropper cannot draw is not a dose

The agent was answering with figures like Cal-Mag 1.3, FloraGro 2.4,
FloraBloom 0.9. The grower's dropper is graduated in 0.25 mL steps to a 1 mL
maximum, so none of those can be drawn. The grower rounds by eye, the recorded
recipe stops matching what is actually in the reservoir, and every later
calculation scales from a figure that was never in the water.

The instrument is recorded on the plant now, and every dose is expressed in
steps it can measure, as draws:

    Cal-Mag     calculated 1.3  -> draw 1.25  (1 x 1.0 mL + 0.25 mL)
    FloraBloom  calculated 0.9  -> draw 1.0   (1 x 1.0 mL)
                rounded up by 0.1 mL, 11% of the calculated figure

The rounding is never hidden. Above 10% it says which way it moved and by how
much, because on a 1 mL dose a single 0.25 step is 25% and that is a real
change to a plant, not a presentation detail. A real dose is never rounded away
to zero either - the smallest measurable step is the floor.

**And recommend_feed can now start a plant from nothing.** It scaled the
current recipe by a stage multiplier, so a fresh plant whose recipe is all
zeros got zeros back - an empty answer for the one case that most needs one.
It bootstraps from the product's OWN label guidance in inventory, converting
mL/gal to the actual reservoir. GSC2 in 1 L of DI water gets Cal-Mag 0.5 mL,
from the label's 2 mL/gal for seedlings, and the answer says plainly that
FloraGro, FloraMicro and FloraBloom have no label figure recorded, so
confidence is low. Nothing is invented to fill those.

Naming: an earlier draft called the rounding "quantise", which in a system that
also runs a quantum_agent reads as a domain claim it is not making. It is
`_to_measurable_dose` now.

### 2026-08-29 — I broke the dashboard, and the SENSORS card never loaded

Three of the four dashboard cards were wrong, and two of them were showing a
third card's answer.

**System and Progress filled with Grow's refusal.** Adding capability names to
the routing briefs improved intent resolution generally - and made grow_agent
claim anything containing "status", because it declares a `get_status`
capability. Domain routing sits ABOVE Boss's own status and progress branches,
so those branches were never reached, and both cards read "that does not name
one of the plants I track".

Orchestration questions are settled before anything is offered to a domain now.
A department cannot own a question about the departments; that is not domain
vocabulary in the orchestrator, it is the orchestrator recognising its own
subject.

**The Grow card said everything twice.** Each blocker already carries its own
"clears when" inline, and the `when` facet restated all of them as a trailing
summary - so the same two conditions appeared once attached to the blocker that
produced them and once stranded at the end with no context. 1,186 characters
down to 968, with the conditions stated once.

**And a dangling sentence.** Anansi rewrote "Not yet - 2 thing(s) in the way"
as "There are 2 of them", which only reads correctly directly after the opener
that gave "them" a referent. On the dashboard it landed straight after a ppm
figure. It is self-contained now: "There are 2 things in the way."

### 2026-08-29 — Equity, reachable by the name of the doctrine

Legal held Pomeroy (1886) and Maitland (1916) and could not find "unjust
enrichment", "constructive trust" or "promissory estoppel". "Clean hands" and
"specific performance" reached Pomeroy; "laches" and "estoppel" got only a
Black's 1910 sentence; the restitution cluster fell through to a nearest-headword
guess. The doctrines were on the shelf and not addressable.

`reference/legal_agent/equity_doctrines.json` - 30 doctrines indexed by their own
names across four families: 11 maxims, 5 in the estoppel family, 6 in
restitution, 8 remedies, defences and related doctrines.

Each entry carries ELEMENTS and, more usefully, how it differs from its
neighbour - because the neighbouring doctrine is what a real argument turns on:

- equitable estoppel runs on a representation of EXISTING FACT and is a shield;
  promissory estoppel runs on a PROMISE about future conduct and is a sword.
- a constructive trust is a REMEDY imposed against the holder's will; a
  resulting trust arises by PRESUMED INTENTION.
- clean hands looks BACKWARD at the claimant's conduct and bars relief; he who
  seeks equity must do equity looks FORWARD and conditions it.
- laches needs delay AND prejudice, and is not a limitation period.
- every equitable interest here ends at a bona fide purchaser for value without
  notice, which is why notice does so much work in equity.

`authority_class: doctrine_summary`, and the source field says plainly that these
are authored summaries written to make each doctrine reachable, not authority.
The Restatement (Third) of Restitution is named as the best modern anchor for
the restitution cluster and deliberately NOT included: it is ALI-copyrighted,
the same reason the model UCC is absent.

Also fixed: `lookup` returned a dictionary headword and stopped, so for every
term Black's happens to carry - most of them - the fuller doctrinal entry was
unreachable. It returns both now. The definition says what the words mean; the
entry says what the doctrine requires.

### 2026-08-29 — Paolozzi, and the difference between a term and an assurance

A trust-law handout supplied by the principal, built on *Paolozzi v.
Commissioner*, 23 T.C. 182 (1954). The case is routinely cited backwards: the
taxpayer did not win because the trust protected her assets, she won by
**proving it did not**. Massachusetts creditors could reach the maximum the
trustees could distribute, so she had retained beneficial enjoyment and the 1938
gift was incomplete.

`reference/legal_agent/self_settled_trust_problem.json` - 25 entries: the
doctrine, the five equitable principles from the handout, and thirteen
authorities each carrying its own standing (binding SCOTUS, controlling state,
persuasive federal, limiting, secondary, statutory text).

The distinction the principal paused on, and the reason the file exists: at
execution the trustees TOLD her they would pay income on request unless they
believed she was acting under compulsion. That is the operative logic of a
modern anti-duress clause sitting in a 1938 Massachusetts trust as a
CONVERSATION rather than a PROVISION. The instrument held absolute discretion,
an accumulation power, remainders over and a spendthrift clause - and no duress
term at all. **A drafted clause binds the trustee and can be construed; an oral
assurance binds nobody.** The understanding became evidence of retained
enjoyment rather than protection from it.

Also recorded: creditors reach the CEILING of the power, not the record of its
exercise - non-exercise is no defence, and the operative fact is capacity rather
than conduct. And that a duress provision protects the res while exposing the
person, since equity acts in personam and the settlor is the one in the
courtroom - FTC v. Affordable Media, In re Lawrence, In re Huber, Toni 1 Trust
v. Wacker.

**26 CFR Part 20** ingested (205 sections), so Treas. Reg. 20.2036-1(c) -
retained enjoyment includes express or implied understandings - is now readable
rather than merely cited. Legal's corpus: 18 works, 1,729 sections, 6,528
subject terms.

Cross-domain, through the referral pipeline rather than by copying: Accounting
received the creditor-access/transfer-tax symmetry, because valuing a position
by what the holder can actually obtain is its frame. Trust received the
term-versus-assurance test, because instrument construction is its frame. Both
recorded and both said plainly they have no handler for it yet.

And a corpus rule into CLAUDE.md: **a dictionary from 1910 is a floor, not a
boundary.** Black's 2nd is held because its term expired, not because it is
current. A doctrine it lacks or states differently is not thereby unsupported -
the absence of a headword is a fact about the 1910 edition and nothing else.

### 2026-08-30 — Trust reads an instrument; the corpus splits by whose question it is

**`assess_instrument`** in the Trust Agent. The corpus could say what a
self-settled trust IS and nothing could test one. This walks an instrument's
terms and reports, clause by clause, what is drafted, what is merely asserted,
and what follows. Run against Paolozzi's own instrument as the findings describe
it, it returns: self-settled `established`, discretion-as-shelter `refuted`,
spendthrift-effective `refuted`, and the oral assurance `refuted` with the
reason that it is not a term, binds nobody, and is worse than useless because
Treas. Reg. 20.2036-1(c) reaches implied understandings.

It never returns "protected". The strongest thing it says is which features are
present and what each does and does not do, because the question a court answers
is not whether a document contains reassuring words.

**The corpus now splits by whose question it is**, on the principal's division:
Legal keeps contracts and courts; Trust takes trusts and estates; Accounting
takes tax; and equity maxims, equitable doctrine and trust doctrine backed by
case law live in `reference/_shared/`, which all three read and none owns. Three
copies would be three sources of truth and two would drift - the same reason a
case is one object.

Moved: Pomeroy, Maitland, the equity doctrine index and the self-settled trust
file to `_shared`; the Delaware Statutory Trust Act and Chandler's *Express
Trusts* to Trust. **Texas Trust Code** (Tex. Prop. Code ch. 111-117, 183
sections, none failed) ingested into Trust - the law that actually governs a
trust sited here, as against the UTC, which is a ULC model and copyrighted.
Legal 16 works, Trust 7, Accounting 8.

Four bugs found while recording one feed event - 2.0 ml Cal-Mag Plus into GSC2's
1 L LWC:

- **A false-success of my own.** I printed "recorded" without reading the
  response. The write had been refused and I reported it as done.
- **The duplicate guard compared two absences.** `prior.per_liter == per_liter`
  with both `None` is `True`, so the first real feed into a plant with an empty
  baseline was refused as "identical to the recipe already in force".
- **`set_current_nutrients` takes nutrient names as top-level args**, so a
  caller passing them under a `nutrients` key created a nutrient literally
  called "nutrients" holding a dict. It accepts both shapes now.
- **`note` and `allow_duplicate` were being stored as nutrients** - one holding
  a sentence, one holding 1.0 - and fed into per-litre arithmetic as doses.

And `get_status` reported a real recipe as a gap, because nutrients have their
own per-plant namespace and it was looking on the plant record.

### 2026-08-30 — Substance over form is the IRS's doctrine too

The principal's observation: the IRS invokes substance over form, so Accounting
needs it as much as Legal does. It is in `reference/_shared/` for that reason -
Legal argues these doctrines, Trust drafts the instruments they test, and
Accounting meets them in an examination.

Twelve entries. The ones that change how a structure should be read:

- **Economic substance is CODIFIED** at 26 U.S.C. s 7701(o), and the test is
  CONJUNCTIVE: the transaction must change the taxpayer's economic position in
  a meaningful way apart from tax, AND there must be a substantial non-tax
  purpose. Failing either fails the doctrine. The penalty is STRICT LIABILITY -
  s 6664(c)(2) removes the reasonable-cause defence, so no opinion letter cures
  a transaction that lacks substance.
- **The doctrine is asymmetric.** The Commissioner may generally assert
  substance over form against a taxpayer; a taxpayer who chose a form is usually
  held to it. Drafting a structure and then arguing its substance differs is the
  weaker side of this doctrine.
- **Gregory v. Helvering carries both halves**, and the second is usually
  dropped: a taxpayer's legal right to decrease taxes by permitted means is not
  doubted. The doctrine polices transactions without substance; it does not tax
  people for choosing an efficient lawful route.
- **Frank Lyon** is the limiting case for when the doctrine is over-read.

**Control is allocated by the instrument, and the assessor now tests it.**
`assess_instrument` walks the grantor-trust powers of 26 U.S.C. ss 674-677 -
power to control beneficial enjoyment, administrative powers, power to revoke,
income for the grantor or spouse. Given a trust marked irrevocable whose settlor
kept a power of appointment and the right to substitute assets, it returns
`grantor_trust_status: established` and names the two sections: income is taxed
to the grantor however irrevocable the deed says it is. Where a power is simply
not stated it returns `insufficient_evidence` and names what would close it,
rather than assuming absence.

**And administration is evidence.** Treas. Reg. 20.2036-1(c) reaches express OR
IMPLIED understandings, and an implied understanding is proved by conduct - so a
well-drafted instrument administered as the settlor's chequebook is a
self-settled arrangement whatever the deed says.

### 2026-08-30 — Precatory language, and administration as the place duties live

Two corrections from the principal, both worth taking as stated.

**These doctrines are about ADMINISTRATION, not creation.** Creating a trust
settles what it is; administering it is where the fiduciary obligations operate
and where nearly every dispute happens. Paolozzi turned on administration - the
CEILING of what the trustees could pay, and what they in fact told and gave the
beneficiary. A deed can be impeccable and the administration fatal. Recorded as
its own entry so the framing does not drift back.

**Precatory language** - the term for the failure, and the sharpest addition
here. Words of WISH, HOPE, DESIRE, REQUEST or RECOMMENDATION impose no
enforceable duty and create no trust; a trustee who disregards them breaches
nothing. Mandatory words - shall, must, is directed to, on condition that - do.
This is the oral-assurance failure moved INSIDE the document, and it reads as
stronger for being written down.

`assess_instrument` now sorts every supplied clause into mandatory, precatory,
mixed, or undetermined. Given "It is my wish that the trustee provide for the
education and support of my daughter" beside "The trustee shall distribute
income quarterly", it marks the first refuted and the second established.

Also recorded in Trust: **testamentary trust** (a will that converts to a trust
at death, and therefore passes through probate); **no hole in the instrument** -
a gap is not neutral, something fills it, and drafting silence hands the
decision to a default rule or a court; **appointment of a receiver** as the
preservation remedy when a fiduciary will not complete the duty, sitting among
the Texas remedies at Tex. Prop. Code s 114.008; **duty to account and inform**,
which is what makes every other duty enforceable; and **independent judgment** -
a trustee who rubber-stamps the settlor has held the pen for someone else.

And into the shared corpus, **the public trust doctrine** with Illinois Central
R.R. v. Illinois, 146 U.S. 387 (1892) - a genuine trust doctrine where the
trustee is a government and the beneficiary is the public. Reachable now from
Legal, Trust and Accounting alike.

Trust's corpus: 9 works, 511 sections.

### 2026-08-30 — The limitation was mine: one question asked of every sentence

Flagged a limitation and the principal asked the right question back - can the
agent not parse this itself, from what it already knows? It can. The flaw was
that `assess_instrument` asked ONE question of every clause ("does this
protect?"), so a plain grant of discretion came back `refuted` - technically
true, since there is no duty in it, and misleading, because it is a power rather
than a failed promise.

An instrument contains at least six kinds of clause and each takes a different
question. `classify_clause` now sorts them: **prohibition, condition, power,
duty, recital, precatory** - and the state follows the kind rather than one
blanket test.

The change that matters most: a precatory clause is only a FAILURE when someone
is leaning on it. A letter-of-wishes clause quoted for context returns
`not_applicable`; the same wording marked `relied_on_as_protection` returns
`refuted` and is the only thing in the exposure list. That is the distinction
the earlier version could not draw, and it is why it needed the caller to
pre-select which sentences mattered.

A power now carries its own follow-up instead of a verdict: *who holds this? if
the settlor holds it, test it against ss 674-677.* That routes a discretion
clause to the grantor-trust question, which is the question it actually raises.

Ordering mattered in two places, both found by testing. "No interest of a
beneficiary shall be subject to anticipation" read as a duty rather than a
prohibition because the gap in the pattern was twenty characters and the phrase
is twenty-five. And a whereas-clause containing the word "desires" led with
precatory, because the general test ran before the unambiguous one - a recital
is a recital even when it contains a wish.

### 2026-08-30 — Document intake: hand it a lease, get back whose clauses are whose

`core/document_intake.py` and `ingest_document` on Boss. The purpose is not
filing. A lease, an email chain and a trust deed all contain OBLIGATIONS - who
must do what, by when, and what follows if they do not - and until those are
addressable one at a time nobody can check whether the other side is adhering.

Three deterministic stages. EXTRACT via pypdf, tesseract, or a plain read.
SEGMENT by the document's OWN numbering where it has one - articles, decimals,
lettered paragraphs - because the numbering is the citation and "para 7.1" has
to reach the same clause every time. ROUTE each clause to the departments with
an interest, and one clause belonging to three departments is the normal case.

Boss does the routing and nothing else: the referral carries clause REFERENCES,
never clause text. An orchestrator that ships document content to a department
is one refactor away from reasoning about it.

**Orientation was the difference between working and looking like it worked.**
The first lease photograph OCR'd sideways into "4 e vee Me LUE Ne ON ee ee" and
reported 37 clauses and ZERO obligations - worse than failing, because it looks
like a result. Tesseract's own OSD pass reports the rotation, and where OSD is
not confident all four rotations are scored on how word-like the output is and
the best wins. Same photograph after correction: legibility 0.997, 8 clauses
segmented by the lease's own lettering, and interest in all three departments -
6 clauses to Accounting, 4 to Legal, 1 to Trust. Below a legibility of 0.35 the
result carries a warning that the clauses are unreliable.

A false FAILURE fixed on the way: the first run reported `stored_to_case: false`
while the document was sitting in the case as `doc_57e161969b6f`. It reports the
document id now. A false failure sends someone re-uploading a file that is
already filed.

Known limit, and it matters: OCR reads structure well and NUMBERS badly - the
$1,250.00 base rent came through as "$7125". Clause routing and obligation
detection can be trusted from a photograph; a figure cannot, and must be read
from the document itself before anything depends on it.

### 2026-08-30 — SSN collection: the authority chain, and where the provision actually lives

The principal set out the chain - Privacy Act, EO 9397, DoDI 1000.30 - and it is
correct. One technical point sharpens it considerably.

**The SSN provisions are not in 5 U.S.C. 552a.** Section 7 of the Privacy Act of
1974, Pub. L. 93-579, 88 Stat. 1896, was NEVER CODIFIED; it appears as a
statutory note. Searching the section text for "social security" returns
nothing, and the provision is nonetheless law. That is why the argument gets
waved away by people who look in the obvious place.

What s 7 actually does: (a)(1) makes it unlawful for a federal, state or LOCAL
agency to deny any right, benefit or privilege for refusing to disclose an SSN;
(a)(2) excepts disclosure required by federal statute and pre-1975 systems; and
(b) requires the agency to state whether disclosure is MANDATORY OR VOLUNTARY,
BY WHAT STATUTORY AUTHORITY, and TO WHAT USES. An agency without authority
cannot give that notice truthfully, so the notice duty and the authority
requirement collapse into a single test.

**EO 9397 no longer directs anything.** EO 13478 (2008) amended it to remove the
direction to use the SSN. An agency answering "EO 9397" is citing an order that
since 2008 permits rather than requires - and an executive order was never the
"statutory authority" s 7(b) asks for.

Recorded with two deliberate limits. **DoDI 1000.30 is marked NOT VERIFIED** -
the quoted clause and the 2012 date come from the principal's account and this
system has not read the instruction. A paraphrase of a regulation is not the
regulation. And a caution on remedy: 5 U.S.C. 552a(g) attaches to specified
agency failures with a wilfulness standard, while s 7 creates duties without
spelling out its own damages remedy and courts have divided on a bare notice
failure. A missing justification memo is a real auditable compliance gap and
good leverage with an agency or an IG; it is not automatically a damages claim.

Also recorded: s 7 binds AGENCIES. A landlord or employer demanding an SSN is
not reached by it at all, so the argument has to be aimed at the right party.

5 U.S.C. 552a ingested from Cornell LII. Two false successes on the way: govinfo
bulk USC returned HTTP 200 with an error page as the body, twice, and I checked
the status rather than the content - the exact error this system exists to hunt.
Legal: 19 works, 1,718 sections.

### 2026-08-30 — Does this agent still hold the law? A currency check, not a new agent

The principal asked for something that notices when a statute is amended,
recodified, or about to take effect, across federal and every operating state.
Built as an inherited capability rather than an agent: currency is not a domain.
Legal, Trust and Accounting all carry statute and all go stale identically, so
`corpus_currency` lives in `core/` and every agent has it.

Each work is checked against THE SOURCE THAT PUBLISHES IT. The eCFR exposes a
per-part version feed, so the check is exact: 12 CFR Part 1002 last amended
2026-07-21, Part 1026 on 2026-03-01, 26 CFR Part 20 on 2026-07-24 - all newer
than nothing this system holds, so all current.

**Granularity was the difference between useful and useless.** The first version
asked for the TITLE's amendment date. Title 12 is amended most weeks, so nine
works came back stale at once and the check meant nothing. Asking the part-level
feed instead: nine current, none stale, four honestly unknown.

**"unknown" is never a clean bill of health.** The U.S. Code has no per-section
feed wired up, and most state statutes have no version API at all. Those return
`unknown` with the reason, because a checker that silently reports "current"
when it could not check is a false success wearing a timestamp. The state entry
also carries the recodification warning: Texas moved its Securities Act from
Art. 581 to Gov't Code ch. 4001 in 2019, so a citation can be wrong without a
word of the text changing.

**And the check found its first real thing immediately.** DoDI 1000.30 was
recorded from the principal's account as a 2012 instruction. It has **Change 2,
dated 30 November 2022** - amended twice since issue, so a bare 2012 citation is
incomplete. Recorded, along with the fact that this system could NOT retrieve
the instruction: esd.whs.mil, dodcio.defense.gov, hqmc.marines.mil and
mcieast.marines.mil all return HTTP 403 to automated requests, directly and
through a fetch tool. The issue date, the Change 2 date and the supersession of
DTM 07-015 are verified from public secondary sources; the operative wording is
not, and is marked so.

### 2026-08-30 — DoDI 1000.30, read in full

The principal supplied the PDF that every .mil host refused to serve. Ingested:
28 pages, 63 paragraphs by the instruction's own numbering, held in Legal. The
unverified marker is removed - the wording below is verbatim.

Cover facts, and two correct a secondary source: **August 1, 2012, INCORPORATING
CHANGE 2, November 30, 2022**, and the office of primary responsibility is now
**ATSD(PCLT)** - Privacy, Civil Liberties, and Transparency - not USD(P&R) as at
issue. Authority on the cover is DoDD 5148.11 and a 1 September 2021 DepSecDef
memorandum. It incorporates and cancels DTM 07-015.

The clause the principal quoted is exact: *"If upon review, it is determined
that no authority or legal requirement for the use of the SSN exists, its
collection and use should cease until such authority can be obtained."*

Three lines found on reading do more work than that one:

- *"Any uses of the SSN not provided for in this Instruction are considered to
  be unnecessary and SHALL BE ELIMINATED."* Mandatory, not precatory, and it
  reverses the burden - a use is unnecessary unless the instruction provides
  for it.
- *"The requirement for the use of the SSN provided by Executive Order 9397 has
  been ELIMINATED."* DoD says this itself. An agency answering "EO 9397" is
  citing an authority its own department has disclaimed - and the instruction
  adds that EO 9397 may support an interim measure while use is being
  eliminated but "may not by itself be used to justify continued use."
- *"Ease of use or unwillingness to change are not acceptable justifications"*,
  and claims of "operational necessity" *"shall be closely scrutinized."*

Recorded with its scope, because the strength of the wording invites overreach:
this instruction binds DoD COMPONENTS, confers no rights on the public, and is
not the source of the obligation. Cite it to show a DoD component failed its own
mandatory standard - not as a statute a private party sues on.

Two tools behaved correctly on the way. `ingest_pdf` refused the document with
zero sections rather than storing an unusable blob, because DoD paragraph
numbering is not a citation format it knows. `document_intake` segmented it
cleanly by that numbering instead - which is what it was built for.
Legal: 20 works, 1,727 sections.

### 2026-08-30 — Does DoDI 1000.30 reach the VA? No - and 38 CFR 1.575 does

Asked whether the VA must adhere to DoDI 1000.30 and produce a Sample SSN
Justification Memorandum. The instruction's own applicability clause answers it:
it applies to OSD, the Military Departments, the Joint Staff, the Combatant
Commands, IG DoD, the Defense Agencies, the DoD Field Activities "and all other
organizational entities within the Department of Defense". The VA is a separate
Cabinet department and is not among them. The justification memorandum is a DoD
artifact; the VA owes no such document.

The underlying obligation does reach the VA, because Privacy Act s 7 binds every
federal agency - and the VA has implemented it in its own regulation. **38 CFR
Part 1 ingested** so Legal holds it.

**38 CFR 1.575** is the analogue and is far more specific than the DoD
instruction in the ways that matter to a veteran:

- (a) mirrors s 7(a)(1) - no right, benefit or privilege denied for refusing.
- (b) BUT disclosure IS mandatory for compensation or pension under chapters 11,
  13 and 15 of title 38, on authority of section 4 of Pub. L. 97-365. So for VA
  disability compensation the demand is lawful, and an argument that it is not
  will fail on the face of the regulation.
- (c) the notice duty: VA must state whether disclosure is voluntary or
  mandatory, cite the authority, and list the uses.
- (d) **the auditable one.** A document VA sends BY MAIL may not carry a full
  SSN. It must be truncated to the last four digits - and where truncation is
  not feasible, three named officials must JOINTLY determine it is necessary,
  the document must be listed on a PUBLICLY AVAILABLE website (the Complete
  Social Security Number Mailed Documents Listing), and no portion may be
  visible on the outside of the mailing. Amended 87 FR 53381, 31 August 2022.

That last provision is checkable rather than arguable: either the document type
is on the published list or it is not.

### 2026-08-30 — Training images cannot be attributed to a plant, and now they say why they were kept

The grower worried that a Cal-Mag reference image accepted through the Training
tab might have been filed against GSC1 or GSC2. Checked: it had not, and it
cannot be. **A candidate record carries no plant_id field at all**, and no note
in the grow history references a training file. The separation is structural
rather than lucky.

But the worry was reasonable, because the reassurance was nowhere in the record.
`review_training_candidate` accepted only a candidate id and a decision - there
was no field for the reviewer's reason, so a grower typing "this is internet
reference material, nothing to do with my plants" had it discarded silently. An
annotation that vanishes is its own small false success. Accepting now records
`reviewer_note`, `reviewed_by`, and `depicts: reference material from the web,
not this grow`, and all three travel into the provenance file beside the image.

**And a file can now be supplied by hand.** Two hosts - advancednutrients.com and
rocketseeds.com - return HTTP 403 to every automated request, so those examples
were simply lost. `review_training_candidate` takes a `local_file`, applies the
SAME format sniff and the SAME duplicate check as an automatic fetch, and records
the original source_url and image_url alongside `acquired: supplied by the
grower by hand` and `why_not_fetched: HTTP 403`. A hand-supplied file gets no
easier ride - a duplicate or a non-image damages the set identically however it
arrived.

The Cal-Mag image the grower saved is in: `calmag_deficiency` now holds 4, the
set is 31 files and 31 unique.

### 2026-08-30 — Two counters that looked like one

The grower asked what clicking Accept actually adds, and the honest answer was
that it depends which number you were watching.

There are two, and they mean different things. **reviews_done** counts the act
of reviewing and moves on every click, accept or reject, because rejecting noise
is as valuable as accepting a good example. **per_label have/need** counts
images actually on disk and moves only when a file lands - not when the host
blocked the download, and not when the image was already in the set.

Both going up together read as one number going up, so an accept that achieved
nothing looked identical to one that worked. `training_quest_status` now says
which is which, and gives the gap: **56 reviews, 31 images, 25 that produced
nothing.**

That gap is itself a finding, and the status now says so rather than leaving it
to be inferred: 25 of 56 is the SEARCH returning duplicates and blocked hosts,
not the grower reviewing badly. Clicking through more of the same queries will
not fix it. The warning fires above 40%.

Verified against disk: `have` totals 31 and there are 31 files, label by label.
The counter is not an accounting of intentions.

### 2026-08-30 — A pattern that matched, was discarded, and returned "healthy"

The grower asked whether an evenly paling plant was the light or the nutrients.
Grow answered about stippling, which was not the question, and on a second
attempt returned **productive, high confidence** for a plant visibly losing
colour.

Two faults, and the second is the worse one.

**No pattern for whole-canopy paling.** The classifier knew bottom-up (mobile
nutrient, oldest leaves first) and top-first (immobile, or uptake failing) and
had nothing for ALL OVER - which is a different question with a different
answer. `whole_canopy_pale` now carries it: even paling usually means total feed
strength is behind DEMAND, and the commonest reason demand jumps is a light
upgrade. Light burn does the opposite - it bleaches whatever is nearest the
fixture and spares the shaded lower canopy - so even paling top to bottom is
evidence AGAINST the light.

**And `_negation_aware_hit` was silently suppressing matches that span a comma.**
It splits text on `[.;,]` and then looks for the whole matched span inside a
single clause. "whole plant is losing its green colour, paling evenly" matched
its pattern, crossed one comma, and was then discarded as if it had never
matched. This affects EVERY pattern, not the new one - any match crossing a
comma has always been thrown away. Negation is now judged on the clause where
the match BEGINS. Verified that "no stippling, no webbing" still correctly
matches nothing.

The grower then supplied 769 ppm, 21.2C, 5.94 - and ppm had RISEN from 693 with
no feed added to that reservoir. The drawdown test only contemplated falling or
flat. It now reads the direction: falling fast means eating and short; flat
means not eating, so look at the root zone and pH; RISING with no feed added
means water is leaving faster than nutrient - transpiration concentrating what
is left, which is what a plant under a brighter light does. That is not hunger,
and dosing a reservoir that is already concentrating is how a pale plant becomes
a burnt one. Top up with plain water to the mark, then re-measure.

### 2026-08-30 — A reading taken earlier is still a reading taken earlier

The grower reported the water level down from above 15 L to about 12.5 L while
ppm rose 693 to 769 with no feed added - the mass-balance question that decides
whether the plant is eating or the reservoir is just concentrating.

`analyze_consumption` could not see it. Readings are compared only where they
carry a VOLUME, and today's had none, so it fell back to a window from 22-23
August and correctly reported `below_resolution` on that. Logging the volumes
then produced a worse failure: both entries got a "now" timestamp seconds apart
and the analysis reported a ZERO-HOUR window. The guard was right both times;
the data was wrong.

`log_reading` now takes `taken_at`. A reading measured at 05:00 and written down
at 12:39 records 05:00 as its timestamp, keeps `recorded_at` for when it was
entered, and sets `backfilled: true` so it is visible as a backfill rather than
passing as live.

With that, the window reads 7.7 hours and the answer is still a refusal -
correctly. Uptake is a slow signal, a reservoir this size moves a few percent a
day, and a sub-24h window on a sight tube read by eye cannot separate uptake
from measurement error. The tool is right to decline.

Two duplicate readings created while debugging this were de-indexed. Noting it
because the record is evidence: entries made to test a tool are not observations
of a plant, and leaving them in would have skewed every later comparison.

### 2026-08-30 — Carry the volume forward; ask for it when it is actually needed

The grower's correction: *"If it needs me to take the volume to get an accurate
answer, it needs to ask me for it when it needs it. Not every time... the volume
doesn't change that quickly."* Right, and the previous design had it backwards.

Demanding a level with every ppm and pH is the wrong trade. Those take seconds
with a meter, the level barely moves between them, and insisting on it means it
gets skipped - which is exactly what happened, leaving nothing with a volume at
all and the analysis falling back to a week-old window.

`log_reading` now CARRIES THE LAST KNOWN LEVEL FORWARD, and marks it
`volume_source: carried forward` with the date it was actually measured. A ppm
stays interpretable without asking for anything, and nothing downstream can
mistake an assumption for an observation.

`analyze_consumption` uses MEASURED volumes only, because a carried level is
unchanged by definition - including one guarantees "water moved 0%" and makes
the comparison answer itself. It also dragged the window to 0.1h by pairing a
fresh meter reading against the last real measurement.

And when it genuinely needs a level it now ASKS, once, naming what it has:
"the last measured level was 12.5 L on 2026-08-30... ppm, pH and temperature do
not need it and are not being asked for."

With measured-only readings the window reads 7.7h and the verdict is still a
correct refusal. The tool was never the problem; the demand was in the wrong
place.

### 2026-08-30 — The pump was off, and nothing could read that as a cause

The grower reported three things while asking whether evaporation should
concentrate the reservoir: salt building up on the lid where spray droplets dry,
and - buried at the end - that the air pump driving the top feed had been OFF
for about two hours after the last reading, leaving the top layer of clay
pellets dry.

That last one is the material fact and nothing in the classifier could see it.
`delivery_interruption` now carries it, matched BEFORE the feed-strength
patterns because the timing of an interruption outranks any inference drawn from
a leaf.

Why it is a different failure and not a variation of underfeeding: this system
records `medium_contacts_water: False`. The spray is the ONLY route to the roots
in the medium, so a stopped pump is not a reduced feed - it is no feed at all to
that root mass, and clay pebbles hold almost no water to buffer the gap. Roots
that dry do not recover; the plant regrows below the damage and pales while it
does. No dose fixes that, which is exactly the wrong conclusion the
feed-strength reading would have led to.

Its settling test is timing rather than appearance: put the interruption and the
symptom on one line, check how far down the medium dried, and check whether the
pump is undersized or the line blocked - because an interruption that happened
once by accident happens again by wear.

The salt deposits are recorded as physical confirmation of the concentration
mechanism: water leaving as vapour while dissolved salts stay behind, which is
the same reason the reservoir fell from above 15 L to 12.5 L while ppm rose from
693 to 769 with no feed added.

### 2026-08-30 — A top-up is a measurement, and the reservoir was never 12.5 L

Topping up is a chore. It is also a **controlled dilution**, and dissolved mass
is conserved across it: nothing but water went in, so the salt that was in
solution stayed in solution. That makes the pre-top-up volume - the one number
this grow has only ever eyeballed against a moulded mark - solvable from two ppm
readings that were being taken anyway.

`reconcile_topup` solves it. On today's event (769 ppm -> 667 ppm, filled to the
15 L mark) it returns **13.0 L before the pour, 2.0 L added**, against 12.5 L on
record. It quotes a range rather than the bare quotient because the answer is a
ratio of two meter readings and both tolerances propagate into it.

This also answers what a single ppm reading structurally cannot. ppm rising says
the ratio moved; it never says whether water left or nutrient did. With volume
known on both sides, dissolved MASS can be compared across readings, and mass
falls only when the plant actually takes nutrient up.

Four things void the arithmetic and are refused rather than absorbed: nutrients
poured in alongside the water (mass was ADDED, so the equation no longer
describes the event), a top-up that did not dilute (the water was not what it
was believed to be, or the reservoir had not mixed), source water quoted
stronger than the mixed result, and no volume anchor at all. Each returns a
named classification - `not_applicable`, `contradicted`, `insufficient_evidence`
- never a number that would look measured while being wrong.

**Two standing volume fields, and only one was being written.** The system
record held `typical_working_liters` AND `reservoir_liters`, both live:
`reservoir_liters` is what the dosing path reads. Writing the new volume to one
of them left the record holding 15.0 and 12.5 simultaneously, and every dose
would have been computed against the smaller - roughly 17% short. Both are
written now. `reservoir_volume_l` was removed: an invented field with zero code
references, which is the same failure in its other form - a number that looks
authoritative and reaches nothing.

The carry-forward that spares the grower re-measuring every reading had no way
to know a top-up moved the water, so it kept handing the stale figure forward.
The event that moved the water now writes the new value, as `measured`.

`log_water_change` accepts `liters_added` / `volume_liters` / `liters`, not only
`volume` - a top-up is described by what went in, a full change by what the
reservoir now holds, and accepting one name made the other look like a missing
field.

### 2026-08-30 — Withdraw a reading without erasing it

Readings get entered twice, entered against the wrong volume, or entered while
something downstream is being debugged - all three happened to this grow's
series today. Any of them quietly poisons the derived figures, because uptake
and mass balance are DIFFERENCES between consecutive readings, so one bad row
corrupts both intervals touching it and nothing fails loudly.

`void_reading` withdraws a row from analysis and keeps it on disk. Kept, because
the fact that a wrong number was once recorded is itself history and deleting it
would make the series look like it had always been clean. Excluded, because
`_get_readings_for_plant` feeds every consumption and mass-balance calculation.

Voiding **requires a reason**. A row withdrawn without one is indistinguishable
from a row withdrawn because it was inconvenient, and a series that can be
silently trimmed is not evidence.

Applied to today: three duplicate/debug rows voided, and the pre-top-up reading
re-logged with the volume the dilution measured (13.0 L) in place of the 12.5 L
it had carried forward.

### 2026-08-30 — The dashboard was asking questions when it wanted state

Two cards went through the narration path, and it answered them as questions.
The Grow card asked *"how is my plant"* and got back an argument about whether
to raise feed strength - reasoning from an earlier conversation, at the moment
the grower wanted to see the numbers. The Progress card asked *"catch me up"*
and got a paragraph assembled from three session-log entries.

Worse than being wrong, both went stale without looking stale: a narrated
sentence carries no timestamp, so output from hours ago reads exactly like
output from now.

Both now read structured state and render fields:

- `grow_snapshot` (Grow) returns strain, stage, day, the last reading with its
  age in hours, volume WITH ITS SOURCE, dissolved mass, and the most recent
  evaluation that found a problem. Volume shows whether it was measured or
  carried forward, because every dose is computed from it and the two are
  different kinds of number.
- `recent_changes` (Maintenance) returns the last N headlines from
  `CHANGELOG.md`, newest first - the file this project already treats as the
  record of what happened. Headlines only; the body of an entry explains why a
  change was made, which is not what a status card is for.

Three bugs surfaced while wiring it:

**`reservoir_temp` was being silently dropped.** `log_reading` read `temp` and
nothing else, so a full session of reservoir temperatures recorded null while
every write reported success. The alias is accepted now - but the general fix is
that **an unrecognised field is refused and nothing is saved**, naming the field
and listing what is accepted. A reading written with a field the agent does not
read looks complete while that measurement is quietly missing, which is the
false-success shape this project hunts.

**`open_concern` read the verdict from the wrong level.** Leaf evaluations nest
`classification` under `recommendation`; reading it from the top returned None
for every evaluation ever recorded, so the card showed no concern on a day with
several. A clean-looking dashboard produced by looking in the wrong place is
worse than an empty one.

**The snapshot bypassed `_plant_state`**, reading the memory key directly, and
returned "unknown" for a plant whose stage and strain are both recorded.

Markup: `.card-body` was a `<p>`, which cannot legally contain the lists these
cards render - browsers auto-close it. Now a `<div>`. The orphan
`.candidate-note` CSS was removed; it had zero references.

### 2026-08-30 — Progress means the roadmap, not a week of Grow bugfixes

The Progress card listed the last ten changelog headlines and they were almost
all plant work - a stopped pump, a reservoir volume, a voided reading. Real
work, and the wrong answer to *how is the system evolving*. The grower's reason
for asking is to avoid redoing something already done, and 110 domain commits
bury the handful that changed what the system can do.

**Scope is decided by where a change LANDED, not by what its subject says.**
Classifying commit subjects by keyword would be the same guessing this
architecture exists to avoid - "fix the reservoir volume field" reads domain and
touches core. So `recent_changes` reads git, takes the files each commit
touched, and classifies: `core/` and `services/` are **platform** (inherited by
every agent, so a change there changes all of them), `webapp/` and Anansi are
**interface**, `reference/` and the ingest tools are **corpus**, and anything
under one agent's own directory is that agent's domain. Domain-only commits are
excluded by default and **the number excluded is reported**, because silently
filtered history is indistinguishable from history that does not exist.

`docs` is dropped from the display: the changelog is updated with every change,
so it rides along on nearly everything and carries no signal.

**The phase tracker reads the roadmap rather than restating it.**
`phase_status` parses the table in `DEPLOYMENT_PROGRESS.md` - kept as a read of
that file because a duplicated status is one that drifts, and the copy that
drifts is always the one nothing edits.

**It caught a drift immediately, in the file itself.** The table said Phase 6
was `not started`. Its own section three hundred lines below recorded nginx TLS
on 8443, the Security Agent gating every inbound `/execute`, and the retirement
of port 8090 - with one sudo command outstanding. Exactly the "redo something
already done" this card exists to prevent, and it was sitting in the document
meant to prevent it.

So the table is now **checked against the section headings rather than trusted**.
Where they disagree the section wins - it is the part edited while work is
actually happening - and the conflict is surfaced on the card instead of one
side quietly winning. Verified by re-introducing the drift: the conflict is
reported and the corrected state still shows.
