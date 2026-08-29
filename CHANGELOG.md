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
