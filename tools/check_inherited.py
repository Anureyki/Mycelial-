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


if __name__ == "__main__":
    sys.exit(main())
