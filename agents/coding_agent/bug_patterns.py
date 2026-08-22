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
