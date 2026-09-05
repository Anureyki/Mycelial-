#!/usr/bin/env python3
"""Exercise every inherited capability against every running agent.

The reasoning layer is universal and the domain boundary is per agent, which
means a change to `core/base_agent.py` reaches all of them at once. That is the
design and it is also the risk: a helper added to the base and tested against
one agent looks finished while being broken everywhere else.

That is not hypothetical. `_unwrap_value` and `_uid` were both called from
shared `core/` code and defined on NO base class - they worked only because Grow
and Maintenance each happened to define their own copy, and the other twelve
agents would have raised AttributeError the first time any shared code ran on
them. Nothing reported it, because an inherited verb that nobody had called on a
given agent is indistinguishable from one that works.

So this CALLS them. Not imports, not greps - a real request over the wire to
every agent that is up.

Probes are chosen to be side-effect free by being DELIBERATELY INVALID. A verb
that rejects bad input with its own guard message has proved three things at
once: the method exists, it is reachable through dispatch, and its guard runs.
A verb that is missing returns "Unknown task"; a verb that is broken returns a
traceback or an AttributeError. Those are all distinguishable, and none of them
writes anything.
"""
import json
import glob
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (task, args, what a HEALTHY agent should say). `expect` is a substring match
# against the response; None means "any structured response without a crash".
PROBES = [
    ("routing_terms", {}, None),
    ("describe", {"task": "", "payload": {}}, None),
    ("corpus_currency", {}, None),
    ("list_differentials", {}, None),
    ("open_differential", {}, "observation"),
    ("add_hypothesis", {}, "differential id"),
    ("weigh_evidence", {"id": "differential_nope"}, "No differential"),
    ("assess_differential", {"id": "differential_nope"}, "No differential"),
    ("decide_differential", {"id": "differential_nope"}, "No differential"),
    ("record_differential_outcome", {"id": "differential_nope"}, "No differential"),
    ("retract_stance", {"id": "differential_nope"}, "No differential"),
    ("set_discriminator", {"id": "differential_nope"}, "No differential"),
    ("case_get", {}, None),
    ("case_list", {}, None),
    ("receive_finding", {"kind": "_probe", "payload": {}}, None),
]

# A crash leaks through as one of these. They are the actual finding - an agent
# answering "Unknown task" merely lacks the verb, which may be deliberate.
CRASH_MARKERS = ("Traceback", "AttributeError", "NameError", "TypeError",
                 "has no attribute", "not defined", "Internal Server Error")


def agents():
    out = []
    for f in sorted(glob.glob(os.path.join(ROOT, "config", "agent_configs", "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if d.get("agent_id") and isinstance(d.get("port"), int):
            out.append((d["agent_id"], d["port"]))
    return out


def call(port, task, args, timeout=20):
    body = json.dumps({"task": task, "args": args,
                       "sender": "tool:check_inherited"}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/execute", body,
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def classify(status, text, expect):
    """A crash is a shape, not a word.

    Scanning the raw body for "Traceback" reported coding_agent as broken
    because its routing terms legitimately CONTAIN the word - it is the agent
    that reads stack traces. A detector that cannot tell an error from data
    about errors invents bugs, which is worse than missing them: the fix gets
    applied to working code.

    So the JSON is parsed first. A body that parses and carries a result is a
    successful response whatever words are inside it. Only a body that fails to
    parse (an HTML error page) or an error FIELD carrying a crash marker counts."""
    if status == 0:
        return "unreachable", text[:60]
    try:
        parsed = json.loads(text)
    except Exception:
        low = text.lower()
        marker = next((m for m in CRASH_MARKERS if m.lower() in low), "non-JSON response")
        return "CRASH", marker

    # Only an error field can carry a crash. Payload text never does.
    def errors(o):
        found = []
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "error" and isinstance(v, str):
                    found.append(v)
                else:
                    found += errors(v)
        elif isinstance(o, list):
            for v in o:
                found += errors(v)
        return found

    errs = errors(parsed)
    joined = " ".join(errs).lower()
    marker = next((m for m in CRASH_MARKERS if m.lower() in joined), None)
    if marker or status >= 500:
        return "CRASH", marker or f"HTTP {status}"
    if "unknown task" in joined:
        return "absent", ""
    if expect and expect.lower() not in json.dumps(parsed).lower():
        return "unexpected", json.dumps(parsed)[:70]
    return "ok", ""


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    rows, crashes, unreachable = [], [], []

    # BASE DRIFT FIRST, because it explains most "missing verb" results.
    # A change to core/base_agent.py reaches an agent only when that agent
    # restarts, so the swarm can be running two shared classes at once with
    # nothing saying so.
    on_disk = None
    try:
        import hashlib
        with open(os.path.join(ROOT, "core", "base_agent.py"), "rb") as fh:
            on_disk = hashlib.sha256(fh.read()).hexdigest()[:12]
    except Exception:
        pass
    versions = {}
    for aid, port in agents():
        if only and only != aid:
            continue
        s2, t2 = call(port, "base_version", {}, timeout=5)
        if s2 == 0:
            continue
        try:
            d = json.loads(t2)
            while isinstance(d, dict) and "result" in d and len(d) == 1:
                d = d["result"]
            versions[aid] = d.get("base_sha") if isinstance(d, dict) else None
        except Exception:
            versions[aid] = None
    stale = [a for a, v in versions.items() if v and on_disk and v != on_disk]
    unknown = [a for a, v in versions.items() if not v]
    if on_disk:
        print(f"base_agent.py on disk: {on_disk}")
        if stale:
            print(f"  RUNNING AN OLDER BASE: {', '.join(stale)}")
        if unknown:
            print(f"  cannot report a base version (predates base_version): "
                  f"{', '.join(unknown)}")
        if not stale and not unknown:
            print("  every running agent is on this base")
        print()

    for aid, port in agents():
        if only and only != aid:
            continue
        st, _ = call(port, "__health_probe__", {}, timeout=4)
        if st == 0:
            unreachable.append(aid)
            continue
        for task, args, expect in PROBES:
            s, t = call(port, task, args)
            verdict, detail = classify(s, t, expect)
            rows.append((aid, task, verdict, detail))
            if verdict == "CRASH":
                crashes.append((aid, task, detail))

    tasks = [p[0] for p in PROBES]
    seen = sorted({r[0] for r in rows})
    width = max((len(a) for a in seen), default=10)
    print(f"{'agent':<{width}}  " + "  ".join(f"{t[:11]:<11}" for t in tasks))
    for aid in seen:
        cells = []
        for task in tasks:
            v = next((r[2] for r in rows if r[0] == aid and r[1] == task), "-")
            cells.append({"ok": "ok", "absent": ".", "CRASH": "CRASH",
                          "unexpected": "differs", "unreachable": "?"}.get(v, v))
        print(f"{aid:<{width}}  " + "  ".join(f"{c:<11}" for c in cells))

    print(f"\nlegend: ok = verb ran and guarded correctly | . = not dispatched here "
          f"| differs = ran, wording differs | CRASH = broken")
    if unreachable:
        print(f"not running (not a failure): {', '.join(unreachable)}")
    if crashes:
        print(f"\n{len(crashes)} CRASH(es):")
        for aid, task, d in crashes:
            print(f"  {aid} / {task}: {d}")
        return 1
    print("\nno inherited capability crashed on any running agent")
    return 0


def check_routing_terms_are_regex():
    """A routing term containing a control character is a dead term.

    "\\bph\\b" written in a NON-raw Python string makes \\b a BACKSPACE, not a
    word boundary, so the pattern is backspace-p-h-backspace and matches
    nothing, ever. pH and EC - the two measurements this grow is steered by -
    were unroutable this way, and nothing reported it: an agent with a dead
    term looks exactly like an agent whose term simply did not match.
    """
    import json as _json
    import urllib.request as _u
    dead = {}
    for port in (8000, 8001, 8002, 8003, 8081, 9006, 9007, 9009, 9010, 9011, 9012, 9013):
        try:
            r = _u.Request(f"http://127.0.0.1:{port}/execute",
                           _json.dumps({"task": "routing_terms", "args": {},
                                        "sender": "check"}).encode(),
                           {"Content-Type": "application/json"})
            d = _json.load(_u.urlopen(r, timeout=10))
            n = 0
            while isinstance(d, dict) and "terms" not in d and "result" in d and n < 6:
                d = d["result"]
                n += 1
        except Exception:
            continue
        bad = [t for t in (d.get("terms") or []) + (d.get("owns") or [])
               if any(ord(ch) < 32 for ch in t)]
        if bad:
            dead[d.get("agent") or port] = [repr(x) for x in bad]
    if dead:
        print("DEAD ROUTING TERMS - control characters, these can never match:")
        for k, v in dead.items():
            print(f"  {k}: {', '.join(v)}")
        return 1
    print("no routing term contains a control character")
    return 0


def check_corpus_integrity():
    """Read the integrity each section RECORDED. Do not measure it here.

    This function used to find truncated statutes by looking for a stored
    length of exactly 4000 - the retired MAX_SECTION. That is validation from
    outside, inferring a fact about history from a coincidence of form, and it
    is the thing `core/source_integrity.py` exists to replace. A provision that
    happened to be 4000 characters long would have been condemned; one cut at
    any other cap would have passed unseen.

    So integrity is now stamped where it is known - by the ingester, at the
    moment it cuts or does not cut - and this only READS it. What it reports:

      truncated   the section says it is incomplete. Re-ingest it.
      unknown     nothing vouches for it. Not an error, and NOT a pass.
      complete    the ingester recorded storing the whole retrieved body.

    `unknown` does not fail the build. A corpus acquired before integrity
    existed is not thereby wrong, and failing on it would train someone to
    stamp `complete` to get green - which would put a guess in the one field
    whose entire purpose is to not be one.
    """
    import glob as _g
    import json as _j
    import os as _os
    import re as _re
    _sys_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    if _sys_root not in sys.path:
        sys.path.insert(0, _sys_root)
    from core import source_integrity as _si

    statutory = _re.compile(r"U\.?S\.?C\.?|C\.?F\.?R\.?|Prop\. Code|Bus\. & Com\.", _re.I)
    tally = {"complete": 0, "truncated": 0, "unknown": 0}
    bad = {}
    for f in _g.glob(_os.path.join(_sys_root, "reference", "*", "*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                d = _j.load(fh)
        except Exception:
            continue
        title = str(d.get("title") or "")
        if not statutory.search(title):
            continue
        for s in (d.get("sections") or []):
            st = _si.read(s)["state"]
            tally[st] = tally.get(st, 0) + 1
            if st == "truncated":
                bad[title] = bad.get(title, 0) + 1
    print(f"corpus integrity (statutory): {tally['complete']} complete, "
          f"{tally['truncated']} truncated, {tally['unknown']} unverified")
    if bad:
        print("  sections RECORDING themselves incomplete - re-ingest these:")
        for t, n in sorted(bad.items(), key=lambda kv: -kv[1])[:12]:
            print(f"    {n:>4}  {t}")
        return 1
    return 0



def check_declaration_sites_agree():
    """The THREE places a capability list lives, compared against each other.

    check_declared_matches_dispatched() compares the constructor against the
    dispatch table and reports the gap. It has never looked at the other two
    declarations, and there are two: config/agent_configs/<id>.json, which the
    Service Manager and the roster read, and config/agent_cards/<id>.json,
    written at registration and never rewritten afterwards.

    Measured the day this was added: ELEVEN OF ELEVEN agents disagreed with
    themselves. legal_agent declared 21 capabilities in its config and 73 in
    its constructor; grow_agent 9 against 48; anansi 1 against 18. Every one of
    those passed the existing check, because the existing check was reading a
    different file from the one the registry reads.

    That is the same fault the tool exists to catch, one layer out: a
    capability that works, is dispatched, and is invisible to whatever happens
    to consult the wrong list. Which list a caller gets depends on how it
    happened to ask - the exact shape CLAUDE.md calls out for
    _load_reference_docs, where a section carried its integrity by one lookup
    path and lost it by the other.
    """
    import glob
    import re as _re
    rows, drifted = [], 0
    for cf in sorted(glob.glob(os.path.join(ROOT, "config", "agent_configs", "*.json"))):
        aid = os.path.basename(cf)[:-5]
        try:
            cfg = set(json.load(open(cf)).get("capabilities") or [])
        except Exception:
            continue
        src = None
        for cand in (glob.glob(os.path.join(ROOT, "agents", "*", aid + ".py"))
                     + glob.glob(os.path.join(ROOT, "agents", aid, "*.py"))):
            try:
                t = open(cand).read()
            except Exception:
                continue
            if "capabilities=[" in t:
                src = t
                break
        if not src:
            continue
        seg = src[src.index("capabilities=["):]
        seg = seg[:seg.index("]") + 1]
        ctor = set(_re.findall(r'"([a-z_]+)"', seg))
        cardf = os.path.join(ROOT, "config", "agent_cards", aid + ".json")
        card = set()
        if os.path.exists(cardf):
            try:
                card = set(json.load(open(cardf)).get("capabilities") or [])
            except Exception:
                pass
        missing_from_cfg = sorted(ctor - cfg)
        missing_from_ctor = sorted(cfg - ctor)
        stale_card = sorted(ctor - card) if card else []
        if missing_from_cfg or missing_from_ctor or stale_card:
            drifted += 1
            rows.append((aid, len(cfg), len(ctor), len(card),
                         missing_from_cfg, missing_from_ctor, stale_card))

    print()
    print("declaration sites (constructor vs agent_configs vs agent_cards):")
    if not rows:
        print("  all agents agree with themselves")
        return 0
    print("  %d agent(s) disagree with themselves. The registry, the router and"
          % drifted)
    print("  the dashboard do not all read the same file.")
    print("  %-20s %4s %5s %5s  %s" % ("agent", "cfg", "ctor", "card", "not in config"))
    for aid, nc, nt, nk, mc, mt, sk in rows:
        note = ", ".join(mc[:4]) + (" ..." if len(mc) > 4 else "") if mc else "-"
        print("  %-20s %4d %5d %5d  %s" % (aid, nc, nt, nk, note))
    print("  A capability absent from agent_configs is invisible to whatever reads it,")
    print("  however well it dispatches.")
    return drifted


def check_declared_matches_dispatched():
    """Declared reality against operational reality, in both directions.

    THE GAP THAT LET 82 CAPABILITIES HIDE.

    On 2026-08-31 eighty-two tasks were found that dispatched perfectly when
    called by name and appeared in no agent's declared capability list - so no
    router, dashboard or peer agent knew they existed. They were found with a
    one-off script and the check was never added here, which meant the tool
    that is supposed to compare what the architecture CLAIMS against what it
    DOES had a hole in exactly the place the discrepancy lived.

    Two directions, and they are different faults:

      undeclared  dispatches, nobody can discover it. The capability did not
                  disappear - its description of itself did. Declare it.
      dead        declared, and the dispatcher answers "unknown task". The
                  registry is promising something that is not there. Remove
                  the claim or wire the verb.

    The dispatch side is read from SOURCE rather than probed, deliberately:
    probing a live agent runs the task, and running every declared capability
    to see whether it exists would log readings, send notifications and spend
    API quota. Reading the file is free and has no side effects.
    """
    import glob as _g
    import json as _j
    import os as _os
    import re as _re
    import urllib.request as _u

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    try:
        req = _u.Request("http://127.0.0.1:8004/execute",
                         _j.dumps({"task": "list_agents", "args": [],
                                   "sender": "check"}).encode(),
                         {"Content-Type": "application/json"})
        agents = _j.load(_u.urlopen(req, timeout=20)).get("result", [])
    except Exception as exc:
        print(f"declared-vs-dispatched: registry unreachable ({exc}); check skipped")
        return 0

    # Verbs every agent gets from the base class - never a per-agent omission.
    try:
        import sys as _s
        if root not in _s.path:
            _s.path.insert(0, root)
        from core.base_agent import CORE_CASE_TASKS
        inherited = set(CORE_CASE_TASKS)
    except Exception:
        inherited = set()
    inherited |= {"routing_terms", "describe", "answer", "corpus_currency",
                  "base_version", "ask_peer_corpus", "refer_finding",
                  "receive_finding", "health", "store_memory", "retrieve_memory",
                  "ask_principal", "open_questions", "answer_question",
                  "case_event_notice", "reason", "think"}

    undeclared, dead, checked = {}, {}, 0
    for a in agents:
        aid = a.get("agent_id")
        declared = set(a.get("capabilities") or [])
        if not aid or not declared:
            continue
        hits = _g.glob(_os.path.join(root, "agents", "*", "*.py"))
        src = ""
        for f in hits:
            base = _os.path.basename(f)[:-3]
            folder = _os.path.basename(_os.path.dirname(f))
            if aid in (base, folder) or aid.replace("_agent", "") in (base, folder) \
                    or base.lower() == aid.lower():
                try:
                    with open(f, encoding="utf-8") as fh:
                        src = fh.read()
                except Exception:
                    pass
                break
        if not src:
            continue
        checked += 1
        # BOTH DISPATCH FORMS, or the check reports a false absence.
        #
        # The first version matched only `task == "x"` and missed
        # `task in ("x", "y")`, so it announced that legal_agent declared
        # find_relationships and query_relationship without dispatching them -
        # a registry promising what is not there. They are dispatched, on one
        # line, in a tuple. Reporting a capability as missing when it exists is
        # the same fault as reporting a throttled search as case_not_found, in
        # the tool built to catch that fault.
        dispatched = set(_re.findall(r'task\s*==\s*"([a-z0-9_]+)"', src))
        for grp in _re.findall(r'task\s+in\s+\(([^)]*)\)', src):
            dispatched |= set(_re.findall(r'"([a-z0-9_]+)"', grp))
        gap_u = sorted(dispatched - declared - inherited)
        gap_u = [t for t in gap_u if not t.startswith(("case_", "claim_", "cag_"))]
        gap_d = sorted(declared - dispatched - inherited)
        gap_d = [t for t in gap_d if not t.startswith(("case_", "claim_", "cag_",
                                                       "refresh_cache", "query_cache",
                                                       "cache_"))]
        if gap_u:
            undeclared[aid] = gap_u
        if gap_d:
            dead[aid] = gap_d

    tu = sum(len(v) for v in undeclared.values())
    td = sum(len(v) for v in dead.values())
    print(f"declared vs dispatched ({checked} agents): {tu} undeclared, "
          f"{td} declared-but-not-dispatched")
    if undeclared:
        print("  UNDECLARED - works when called, invisible to routing and the dashboard:")
        for k, v in sorted(undeclared.items(), key=lambda kv: -len(kv[1])):
            print(f"    {k:<20} {len(v):>3}  {', '.join(v[:6])}"
                  + (" ..." if len(v) > 6 else ""))
    if dead:
        print("  DECLARED BUT NOT DISPATCHED - the registry promises what is not there:")
        for k, v in sorted(dead.items(), key=lambda kv: -len(kv[1])):
            print(f"    {k:<20} {len(v):>3}  {', '.join(v[:6])}"
                  + (" ..." if len(v) > 6 else ""))
    # Reported, never fatal: a capability list drifting is a finding to act on,
    # not a reason to fail a build that is otherwise sound.
    return 0

if __name__ == "__main__":
    # EVERY check runs, and the exit code is the worst of them. Appending a
    # second __main__ block silently replaced the first once - the inherited-
    # capability check would have stopped running while the script still
    # reported success, which is the shape this file exists to catch. The same
    # thing happened again in miniature: check_declaration_sites_agree was
    # added, computed into _rc5, and left out of the sys.exit line, so its
    # result was calculated and thrown away.
    #
    # --static runs only the checks that read files, for CI, where no agent is
    # listening. A check that needs a live swarm cannot gate a commit, and
    # pretending otherwise would make the pipeline red for the wrong reason.
    _static_only = "--static" in sys.argv

    if _static_only:
        print("static checks only (no live agents required)")
        _rc3 = check_corpus_integrity() or 0
        _rc5 = check_declaration_sites_agree() or 0
        # Corpus integrity is KNOWN DEBT - hundreds of sections recorded as
        # truncated at ingest - so it reports and does not gate. Declaration
        # drift is at zero as of this commit, so any drift is a regression
        # introduced by whatever is being committed, and that does gate.
        if _rc5:
            print(f"\nFAIL: {_rc5} agent(s) disagree with themselves about their "
                  f"capabilities.\n      Bring config/agent_configs/<id>.json into line with "
                  f"the constructor,\n      or remove the claim if nothing dispatches it.")
        sys.exit(1 if _rc5 else 0)

    _rc = main() or 0
    _rc2 = check_routing_terms_are_regex() or 0
    _rc3 = check_corpus_integrity() or 0
    _rc4 = check_declared_matches_dispatched() or 0
    _rc5 = check_declaration_sites_agree() or 0
    sys.exit(_rc or _rc2 or _rc3 or _rc4 or _rc5)
