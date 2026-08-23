#!/usr/bin/env python3
"""
Bug patterns observed in this codebase, for Coding Agent to scan against.

Every entry here is a bug that actually shipped and was found by hand. They are
recorded as patterns rather than as fixed instances because they cluster by
SHAPE, not by location - each one was found in several places once the shape was
known. That is the method worth repeating: when a bug is found, do not just fix
it, characterise it and search for the same shape elsewhere.

Each pattern carries:
  id           short slug
  severity     high  = wrong behaviour or silent data loss
               medium= degraded correctness, recoverable
               low   = hygiene, latent risk
  regex        what to grep for (a candidate, not a verdict)
  why          what actually goes wrong
  seen_in      a real instance, so the pattern is not abstract
  check        what a human/agent must confirm before calling it a bug

Matches are CANDIDATES. Most of these patterns have legitimate uses; the check
field is what separates a finding from a false positive. Reporting a match as a
confirmed defect without doing the check is itself one of the failure modes this
file exists to prevent.
"""

PATTERNS = [
    {
        "id": "second_granularity_key",
        "severity": "high",
        "regex": r'f"[a-z_]+_\{int\(time\.time\(\)\)\}"',
        "why": ("Record keys at one-second resolution collide when two records are written "
                "in the same second. The second write silently overwrites the first - no "
                "error, no log line, the record simply is not there later."),
        "seen_in": ("grow_agent log_reading: two readings taken minutes apart but logged "
                    "back-to-back collided and one was lost. Also boss request_/response_, "
                    "pqa search_, security audit_, maintenance telemetry_."),
        "check": "Can this run twice inside one second? Batch loops and rapid repeat calls both qualify.",
    },
    {
        "id": "id_computed_twice",
        "severity": "high",
        "regex": r'_uid\(\)|int\(time\.time\(\) \* 1_?0{3,}\)',
        "why": ("An id generated once for the record and again for its index entry produces "
                "two different values, so the index points at a key that does not exist. The "
                "record is written but unreachable. At second resolution the two calls "
                "usually agreed, which hid this until precision was increased."),
        "seen_in": ("grow_agent log_reading called _uid() for the record and again for the "
                    "index key; every reading logged was orphaned, and analyze_consumption "
                    "reported '0 readings carry volume' while three did."),
        "check": "Is the id generated once and reused, or called again later in the same block?",
    },
    {
        "id": "global_slot_in_multi_entity",
        "severity": "high",
        "regex": r'retrieve_own_memory\("current_|store_own_memory\("current_',
        "why": ("A single global key used after multi-entity support was added. Reads return "
                "the wrong entity's state; writes destroy another entity's record."),
        "seen_in": ("grow_agent stored current_nutrients globally, so setting a second "
                    "plant's recipe would have overwritten the first's. check_in read the "
                    "global stage and reported a day-zero seedling as 'veg'. Same shape "
                    "previously found in transition_stage."),
        "check": "Does this task accept an entity id? If so, does the key include it?",
    },
    {
        "id": "unreserved_arg_sweep",
        "severity": "high",
        "regex": r'for k,? ?v in args\.items\(\) if k not in',
        "why": ("Building a payload from 'every argument except a reserved set' silently "
                "absorbs any argument added later. New metadata fields become data."),
        "seen_in": ("grow_agent set_current_nutrients swept reason/decision/expected_effect "
                    "into the recipe when reasoning_context was added, so the feed read back "
                    "as 'Cal-Mag 8.6ml, confidence_note high...ml'."),
        "check": "Is the reserved set complete for every argument this task accepts today?",
    },
    {
        "id": "single_unwrap_of_a2a",
        "severity": "medium",
        "regex": r'send_a2a\([^)]*\)[\s\S]{0,120}?\.get\("result"\)',
        "why": ("send_a2a responses are wrapped twice - once by the HTTP /execute route and "
                "again by the handler's own return. A single .get('result') yields the inner "
                "envelope, not the value, and callers then silently fall back to a default."),
        "seen_in": ("boss_agent read current_stage through one unwrap, so every logged "
                    "reading defaulted to 'seedling' regardless of the plant's real stage."),
        "check": "Print the raw response. Is the value at result, or result.result?",
    },
    {
        "id": "keyword_match_without_negation",
        "severity": "high",
        "regex": r'any\(\s*k(?:eyword)? in (?:lowered|text|\w+\.lower\(\))',
        "why": ("Substring matching over prose ignores negation and context. 'no brown slime "
                "or rot' contains 'brown' and 'rot'; 'protect the plant from pests' contains "
                "'pests'. Both scored as problems."),
        "seen_in": ("grow_agent classified a healthy root description as critical, and "
                    "recommended 'intervention or removal' for a plant the vision model had "
                    "just called healthy. Also flipped a vegetative plant to flower on 'no "
                    "pistils, no calyx'."),
        "check": "Can the keyword appear inside a negated or protective clause?",
    },
    {
        "id": "conclusion_past_resolution",
        "severity": "medium",
        "regex": r'(?:pct|percent|_used|delta|diff)\s*=\s*\(?1?\s*-\s*',
        "why": ("A deterministic calculation returns a confident answer from inputs too "
                "coarse to support one. Arithmetic does not feel like guessing, so the false "
                "confidence is harder to notice than a model hallucinating."),
        "seen_in": ("grow_agent analyze_consumption reported 'balanced - uptake is "
                    "proportional' from two readings six hours apart differing by 0.1%, which "
                    "was measurement noise."),
        "check": "What is the input's precision, and is the observed change above it?",
    },
    {
        "id": "narrow_routing_vocabulary",
        "severity": "medium",
        "regex": r'any\(keyword in prompt\.lower\(\) for keyword in \(',
        "why": ("A short keyword list silently drops requests to a generic fallback, where a "
                "small model answers without domain state and invents something plausible."),
        "seen_in": ("boss_agent had six grow keywords; 'nutrition' is not a substring of "
                    "'nutrient' and 'dwc' was absent, so a question about the reservoir was "
                    "answered by a 1.5b model as 'Direct Water Cooker'."),
        "check": "List the words a user would actually use. Are synonyms and acronyms covered?",
    },
    {
        "id": "silent_no_op_matcher",
        "severity": "high",
        "regex": r'pgrep|pkill|subprocess\.run\(\[["\']pgrep',
        "why": ("A process matcher whose pattern does not match how processes are actually "
                "launched returns nothing and reports success. The operation appears to work "
                "and has never once done anything."),
        "seen_in": ("service_manager matched 'agents/<id>.py' while agents launch as "
                    "'python3 -m agents.X.X'. stop/restart were no-ops for the project's "
                    "entire life, while the README advertised self-healing."),
        "check": "Run the matcher against a live process list. Does it return the pid?",
    },
    {
        "id": "predictable_shared_tmp",
        "severity": "medium",
        "regex": r'["\']\/tmp\/[\w{}$.]+["\']|f["\']\/tmp\/',
        "why": ("A predictable path in a world-writable directory collides between concurrent "
                "callers and can be pre-created or symlinked by another local user."),
        "seen_in": "coding_agent wrote /tmp/code_{seconds}.py; two executions in one second ran each other's code.",
        "check": "Use tempfile.mkstemp in a private 0700 directory instead.",
    },
    {
        "id": "bare_except",
        "severity": "low",
        "regex": r'except\s*:\s*$|except Exception\s*:\s*$',
        "why": ("Swallows the real error and continues with a default, so a bug surfaces later "
                "as a wrong answer rather than a traceback. Several defects found in this "
                "codebase were hiding inside one."),
        "seen_in": "40 in grow_agent alone, 76 across agents.",
        "check": "Would the caller behave differently if it knew this failed?",
    },
    {
        "id": "stale_cache_no_version",
        "severity": "medium",
        "regex": r'const CACHE\s*=|caches\.match\(',
        "why": ("A cache-first service worker with a hardcoded version keeps serving old "
                "assets forever unless the version constant is also bumped, so shipped fixes "
                "never reach installed clients."),
        "seen_in": "webapp served a shell without the photo-upload button while the server had the correct file.",
        "check": "Is the strategy network-first, or is the version bumped on every shell change?",
    },
    {
        "id": "failure_relabelled_as_bad_output",
        "severity": "high",
        "regex": r'parse_error|_safe_parse_json|if not raw',
        "why": ("A call that never returned is reported as a call that returned something "
                "unusable. An empty string from a timeout and an empty string from a model "
                "that answered with nothing are indistinguishable downstream, so blame lands "
                "on output quality and the investigation goes to the prompt instead of the clock."),
        "seen_in": ("legal_agent reported parse_error on a real Supreme Court opinion. The model "
                    "had not answered badly, it had not answered at all: a 30-field JSON "
                    "extraction needs 400-600 tokens, and at the 6.3 tok/s this CPU actually "
                    "delivers that is ~90s against a 60s timeout. Both models extracted the "
                    "parties correctly once given time."),
        "check": "Can the caller tell 'no answer' from 'bad answer'? If both produce the same value, it cannot.",
    },
    {
        "id": "timeout_calibrated_for_other_hardware",
        "severity": "medium",
        "regex": r'timeout\s*=\s*(?:30|60|90)\b',
        "why": ("A timeout carried over from a machine with a GPU. Generation cost scales with "
                "tokens REQUESTED, not with prompt size, so the calls that break first are the "
                "ones asking for large structured output - exactly the valuable ones - while "
                "short prompts keep working and mask it."),
        "seen_in": ("legal/accounting/trust used timeout=60 for extractions needing ~90s of "
                    "generation on the deployment box, so structured extraction could never "
                    "complete."),
        "check": "Measure tok/s on the target hardware, multiply by expected output length. Is the timeout above that?",
    },
    {
        "id": "matcher_catches_the_matcher",
        "severity": "medium",
        "regex": r'pgrep -f|pkill -f',
        "why": ("A -f pattern matches full command lines, including the command doing the "
                "matching and any tool whose arguments contain the string. The result is either "
                "killing yourself or measuring the wrong process, and the wrong-process reading "
                "looks perfectly plausible."),
        "seen_in": ('pkill -f "inference/service" killed the shell that ran it. Separately, '
                    "pgrep -f agents.grow_agent returned the Claude session, whose cmdline "
                    "contained --add-dir .../agents/grow_agent, giving a 101MB reading for a "
                    "process actually using 867MB."),
        "check": "Would this pattern match the command line running it? Exclude self and known wrappers.",
    },
    {
        "id": "with_does_not_close",
        "severity": "high",
        "regex": r'with (?:get_db|sqlite3\.connect|self\._conn)\(',
        "why": ("`with` on a sqlite3 Connection manages the TRANSACTION, not the connection - it "
                "commits or rolls back and leaves the handle OPEN. Code that reads as though it "
                "closes leaks one file descriptor per request until the process hits its limit, "
                "then fails at something unrelated."),
        "seen_in": ("services/memory/service.py had every call site as `with get_db() as conn:` "
                    "and no .close() anywhere. At 1,019 open handles to memory.db against a 1,024 "
                    "limit it could no longer open its own database, and EVERY agent lost its "
                    "state at once while the data sat intact on disk. Surfaced only as "
                    "'OSError: Errno 24' in a log nobody was reading."),
        "check": "Does anything actually call .close()? Count open fds under load: ls /proc/PID/fd | wc -l",
    },
    {
        "id": "silent_no_op_edit",
        "severity": "high",
        "regex": r'\.replace\([\'"]|re\.sub\(',
        "why": ("str.replace on text that does not occur changes nothing and reports nothing. An "
                "edit that silently did not apply is worse than one that errors, because the "
                "author believes the change is in and moves on. Compounds when a half-applied "
                "edit leaves a body referencing a name the signature no longer defines."),
        "seen_in": ("Three times in one session. A hyphenation fix targeted a function in the "
                    "wrong file and did nothing twice. An edit to accounting_agent applied to the "
                    "BODY but not the SIGNATURE, because its docstring differed from its "
                    "siblings, leaving `temperature` referenced but undefined - every inference "
                    "call raised NameError."),
        "check": "Assert the anchor exists before writing. Then test the changed BEHAVIOUR, not that the file parses.",
    },
    {
        "id": "domain_default_masquerading_as_knowledge",
        "severity": "high",
        "regex": r'or ["\'](?:cannabis|dwc|hydro|seedling|veg)["\']|get\([^)]*,\s*["\'](?:cannabis|dwc|seedling)["\']\)',
        "why": ("A default that names a specific domain value is indistinguishable downstream "
                "from a recorded fact. Anything that reads it treats a guess as evidence."),
        "seen_in": ("_get_species_for_plant defaulted to 'cannabis' for any unknown plant, so an "
                    "aloe photo would have been described to the vision model as a 25-day-old "
                    "Girl Scout Cookies autoflower in deep water culture. Separately the "
                    "transplant advice assumed hydro-to-hydro and told a soil grower that roots "
                    "'move with the net pot and suffer almost no disturbance', which is false of "
                    "a soil root ball."),
        "check": "Would returning None be honest here? A default that asserts is worse than one that admits ignorance.",
    },
    {
        "id": "positional_identity",
        "severity": "medium",
        "regex": r'\[0\]\s*(?:#|$)|plant_?(?:one|1)\b|order\[\s*n\s*-\s*1\s*\]',
        "why": ("Using a POSITION as an identity breaks the moment the collection changes. "
                "History attached to 'the first one' silently reattaches to a different thing "
                "after a removal."),
        "seen_in": ("'plant one' was resolved against all tracked plants. After a harvest or "
                    "giving one away, the same phrase would mean a different plant while its "
                    "readings and recipes stayed with the old id. Fixed by making the id "
                    "permanent and the label computed over ACTIVE members only."),
        "check": "If an item is removed, does anything that referred to a position now refer to something else?",
    },
    {
        "id": "circular_inference",
        "severity": "high",
        "regex": r'estimated|inferred|approx',
        "why": ("A value derived FROM something cannot then be used as evidence ABOUT it. The "
                "conclusion is guaranteed and carries no information, but reads like a finding."),
        "seen_in": ("acquire_plant estimates a germination date from the stage a plant arrived "
                    "at. assess_stage then uses age to decide whether a stage is impossible - so "
                    "it would have 'confirmed' the stage using a date computed from that stage. "
                    "Now flagged as estimated and refused as grounds for a transition."),
        "check": "Where did this value come from? If it was derived from the thing it is now being used to judge, it proves nothing.",
    },
    {
        "id": "slow_work_inside_a_request",
        "severity": "high",
        "regex": r'timeout=(?:1[2-9]\d|[2-9]\d\d)|requests\.post\([^)]*timeout=\d{3}',
        "why": ("Work measured in tens of seconds inside a synchronous request will be killed by "
                "any real client - a phone browser drops it on screen lock or app switch - and "
                "the client reports failure while the server completes the job with nobody left "
                "to receive it."),
        "seen_in": ("Photo upload ran local vision plus a model escalation inside the HTTP "
                    "request: 21s measured for ONE photo, over a minute for three. The webapp "
                    "showed 'Request failed' while the assessment completed and checkpointed "
                    "server-side. Fixed by saving, answering at once, and running vision on a "
                    "background thread."),
        "check": "Time it with a real payload. Would a phone on a locked screen still be waiting?",
    },
    {
        "id": "one_answer_shape_for_every_question",
        "severity": "medium",
        "regex": r'return \{"result": text',
        "why": ("Routing to the right agent still leaves the question unanswered if every reply "
                "is the same summary. The user asked something specific and gets a status card, "
                "which reads as being ignored."),
        "seen_in": ("'when is my next nutrient upgrade', 'what is my ppm right now' and 'how is "
                    "plant one' all returned an identical paragraph about stage and feed. "
                    "Separately, asking about ONE plant returned a roundup of the whole garden."),
        "check": "Do two different questions to this branch produce two different answers?",
    },
    {
        "id": "suppressed_stderr_then_claimed_success",
        "severity": "high",
        "regex": r'2>\s*/dev/null.*&&|&&.*echo.*(pushed|done|success|ok)|capture_output=True',
        "why": ("Discarding stderr and then asserting the command worked turns a failure into a "
                "false claim. The caller believes the work is done, and every later decision "
                "rests on something that never happened."),
        "seen_in": ("`git push -q origin branch main 2>/dev/null && echo pushed` printed success "
                    "29 times while local main sat frozen and nothing reached the branch CI runs "
                    "on. Separately service_manager returned {\"success\": true} from /restart "
                    "while every start died on `source: not found` under /bin/sh."),
        "check": "Does anything verify the EFFECT - refs compared, pid serving the port, behaviour read back - or only the exit code?",
    },
]


def scan(root, patterns=None, include=(".py",), skip_dirs=("venv", ".git", "__pycache__", "node_modules", "backup")):
    """Return candidate matches. Deliberately returns candidates, not verdicts -
    every pattern here has legitimate uses and the `check` field is what
    separates a real finding from noise."""
    import os
    import re as _re
    pats = patterns or PATTERNS
    compiled = [(p, _re.compile(p["regex"])) for p in pats]
    findings = []
    self_name = os.path.basename(__file__)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
        for fn in filenames:
            if include and not fn.endswith(tuple(include)):
                continue
            # The catalog contains every pattern's regex as a literal, so it
            # matches itself on nearly all of them. Pure noise.
            if fn == self_name:
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "r", errors="ignore") as fh:
                    lines = fh.readlines()
            except Exception:
                continue
            for i, line in enumerate(lines, 1):
                for p, rx in compiled:
                    if rx.search(line):
                        findings.append({
                            "pattern": p["id"],
                            "severity": p["severity"],
                            "file": os.path.relpath(path, root),
                            "line": i,
                            "code": line.strip()[:140],
                            "why": p["why"],
                            "check": p["check"],
                        })
    return findings
