#!/usr/bin/env python3
"""The feedback loop, as a build gate.

    python3 tools/check_eval.py

Four things, and the fourth is the one that rots silently:

  1. Every security decision path EMITS. A path that stops emitting produces
     no record, and a missing record looks exactly like a decision that was
     never made.
  2. Every record carries a reason, and allowed records carry provenance that
     RESOLVES. A denial without a reason teaches avoidance, not understanding.
  3. The chain is intact. Append-only is enforced by evidence, not intent.
  4. NO MODEL CODE PATH WRITES TO THE HARNESS. Agents and core may call
     emit(); only tools/eval_harness.py may write a record. A component that
     wrote its own security record would be grading itself on the one question
     that matters - whether it let something through.
"""
import ast
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


# Paths that MUST emit. Removing one is the regression this gate exists for.
REQUIRED_EMITTERS = {
    "core/acl.py": ("acl_denied", "acl_allowed"),
    "core/sandbox/signing.py": ("artifact_rejected", "artifact_signed"),
}


def main():
    print("\n  1. every security decision path emits")
    # AST, NOT GREP. The first version of this checked that the string
    # "acl_denied" appeared in the file, and a regression test renamed the CALL
    # from _observe( to _noop( - leaving the string in place. The gate passed
    # while the emit path was gone, which is the exact failure it exists to
    # catch. A string in a source file is not a call.
    for rel, kinds in REQUIRED_EMITTERS.items():
        src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        tree = ast.parse(src)
        # Which functions actually call emit(), directly or via a local wrapper
        emit_wrappers = {"emit"}
        for _ in range(3):                    # resolve one wrapper at a time
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        nm = (sub.func.attr if isinstance(sub.func, ast.Attribute)
                              else getattr(sub.func, "id", ""))
                        if nm in emit_wrappers:
                            emit_wrappers.add(node.name)
        reaches_emit = emit_wrappers - {"emit"}
        ck(f"{rel} has a live path to emit()", bool(reaches_emit),
           f"functions reaching emit: {sorted(reaches_emit)[:4]}")
        # And the event kind must be an argument to an actual call, not a
        # bare string sitting anywhere in the file.
        called_with = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                nm = (node.func.attr if isinstance(node.func, ast.Attribute)
                      else getattr(node.func, "id", ""))
                if nm not in emit_wrappers:
                    continue
                for arg in list(node.args) + [k.value for k in node.keywords]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        called_with.add(arg.value)
                    elif isinstance(arg, ast.IfExp):
                        for side in (arg.body, arg.orelse):
                            if isinstance(side, ast.Constant):
                                called_with.add(side.value)
        for k in kinds:
            ck(f"{rel} passes {k} to a real call", k in called_with,
               "a path that stops emitting produces no record, and a missing "
               "record looks like a decision that never happened")

    print("\n  1b. LIVE ROUND TRIP - static analysis cannot see a dead path")
    # THE STATIC CHECK WAS NOT ENOUGH, PROVEN. Removing the emit import left
    # the call written, so the AST still saw it - and _observe swallows
    # exceptions, so the path was dead and silent while the gate passed. The
    # only check that catches that is making a real decision and looking for
    # the record.
    import importlib, tempfile, shutil
    import core.security_events as se
    tmp = tempfile.mkdtemp()
    orig_spool = se.SPOOL
    try:
        se.SPOOL = tmp
        from core.acl import check as acl_check
        acl_check("trust_agent", "credential:COURTLISTENER_API_TOKEN", "read")
        from core.sandbox.signing import sign, verify as sig_verify
        import copy as _copy
        art = sign("legal_agent", {"probe": 1})
        bad = _copy.deepcopy(art); bad["envelope"]["body"]["probe"] = 2
        sig_verify(bad)
        emitted = []
        for f in os.listdir(tmp):
            with open(os.path.join(tmp, f), encoding="utf-8") as fh:
                emitted += [__import__("json").loads(l) for l in fh if l.strip()]
        kinds = {e.get("event_type") for e in emitted}
        ck("a real ACL denial actually emits", "acl_denied" in kinds,
           f"observed {sorted(kinds)}")
        ck("a real forged artifact actually emits",
           "artifact_rejected" in kinds, f"observed {sorted(kinds)}")
        ck("every emitted event carries a reason",
           all((e.get("reason") or "").strip() for e in emitted),
           f"{len(emitted)} event(s)")
        # KEEP WHAT THE ROUND TRIP PRODUCED. Section 3 used to read the
        # machine's own record store, which lives under datasets/ and is
        # gitignored - so on a fresh checkout it is EMPTY and "there are
        # records to check" could only ever fail. The gate was asserting a
        # property of this machine's history rather than of the code, and it
        # failed on the runner for exactly that reason.
        #
        # Feeding it the events this check just made is both self-contained and
        # strictly stronger: it proves emit -> ingest -> record end to end,
        # instead of inspecting whatever happened to be lying around.
        from tools.eval_harness import ingest as _ingest
        fresh_records = tempfile.mkdtemp()
        _ingest(spool=tmp, records=fresh_records, quarantine=True)
    finally:
        se.SPOOL = orig_spool
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n  2. no model code path writes a record")
    # WRITES, NOT MENTIONS. The first version flagged any file containing the
    # string "security_eval", which caught the training and evaluation job
    # managers for PASSING THE PATH to a read-only scorer - a false positive
    # that would have taught everyone to route around the gate. Naming the
    # store is not touching it; opening it for write is.
    WRITE_FNS = {"open", "write_text", "write_bytes", "copy", "copy2",
                 "copyfile", "copytree", "move", "replace", "rename", "unlink",
                 "remove", "rmtree", "mkdir", "makedirs"}
    bad = []
    for dirpath, dirnames, files in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in {".git", "venv", "__pycache__", "node_modules"}]
        for f in files:
            if not f.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, f), ROOT)
            if rel in ("tools/eval_harness.py", "tools/check_eval.py"):
                continue
            try:
                tree = ast.parse(open(os.path.join(dirpath, f),
                                      encoding="utf-8", errors="replace").read())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                nm = (node.func.attr if isinstance(node.func, ast.Attribute)
                      else getattr(node.func, "id", ""))
                if nm not in WRITE_FNS:
                    continue
                strs = [a.value for a in ast.walk(node)
                        if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                # os.open with O_WRONLY, or open(..., "w"/"a")
                writes = (nm != "open") or any(
                    m in strs for m in ("w", "a", "wb", "ab", "w+", "a+"))
                if writes and any("security_eval" in x or "_head.json" in x
                                  for x in strs):
                    bad.append(f"{rel}:{node.lineno} {nm}()")
    ck("only the harness WRITES to the record store", not bad, str(bad[:4]))

    from core.security_events import SPOOL
    ck("agent code writes to the SPOOL, not the records",
       "security_events" in SPOOL and "security_eval" not in SPOOL,
       f"spool={os.path.basename(SPOOL)}")

    print("\n  2b. no build gate may feed the collection spool")
    # THE LEAK THIS CLOSES, AND WHY IT NEEDS A GATE OF ITS OWN.
    #
    # check_acl, check_sandbox and check_drift all make REAL security decisions
    # - they have to, or they prove nothing - and all three were emitting into
    # the live spool. The harness ingested them as usable training pairs
    # because nothing distinguished a decision defending the system from one a
    # test asked for. 452 of 1,405 records arrived in one night of CI runs and
    # the top six scenarios, gate fixtures verbatim, were 21.3% of everything a
    # student would train on.
    #
    # A fix applied to three files today is undone by the fourth file written
    # tomorrow, so the rule is asserted rather than remembered: any gate that
    # can reach emit() must declare itself test-origin.
    import re as _re
    leaky = []
    for f in sorted(os.listdir(os.path.join(ROOT, "tools"))):
        if not (f.startswith("check_") and f.endswith(".py")):
            continue
        if f == "check_eval.py":
            continue          # redirects se.SPOOL directly, checked above
        src = open(os.path.join(ROOT, "tools", f), encoding="utf-8",
                   errors="replace").read()
        reaches = _re.search(r"^\s*(from|import)\s+core\b", src, _re.M)
        if reaches and "MYCELIAL_EVENT_ORIGIN" not in src:
            leaky.append(f)
    ck("every gate importing core declares test origin", not leaky,
       str(leaky) or "none may emit into the collection spool")

    print("\n  3. records are complete")
    sys.argv = ["x"]
    from tools.eval_harness import read_records, verify, pairs, MIN_REASON_CHARS
    recs = read_records(records=fresh_records)
    ck("the round trip produced records to check", bool(recs),
       f"{len(recs)} record(s) from this run's own events")
    missing_reason = [r["seq"] for r in recs if not (r.get("reason") or "").strip()]
    ck("every record carries a reason", not missing_reason, str(missing_reason[:6]))
    no_hash = [r["seq"] for r in recs if not r.get("record_hash")]
    ck("every record carries its own hash", not no_hash, str(no_hash[:6]))
    bad_pos = [r["seq"] for r in recs
               if r.get("label") == "positive" and r.get("usable_as_pair")
               and not r.get("provenance_verified")]
    ck("no positive example without resolving provenance", not bad_pos,
       str(bad_pos[:6]))
    thin = [r["seq"] for r in recs if r.get("usable_as_pair")
            and r.get("label") == "negative"
            and len((r.get("reason") or "")) < MIN_REASON_CHARS]
    ck("no negative example without a usable reason", not thin, str(thin[:6]))

    print("\n  4. the chain is intact (append-only, proven)")
    ok, probs = verify(records=fresh_records)
    ck("hash chain verifies on records built by this run", ok,
       "; ".join(probs[:2]))
    # AND ON THE REAL STORE TOO, WHEN THERE IS ONE. A clean checkout has no
    # record store and must not fail for it; a machine that HAS been running
    # has one, and silent corruption there is exactly what an append-only
    # chain exists to catch. So the real store is checked when present and
    # reported as absent when not - "nothing to check" and "checked and fine"
    # are different findings and this says which.
    from tools.eval_harness import RECORDS
    if os.path.isdir(RECORDS) and read_records():
        ok2, probs2 = verify()
        ck("hash chain verifies on this machine's own store", ok2,
           "; ".join(probs2[:2]))
    else:
        print("    ----  this machine has no record store yet - nothing to "
              "verify, which is not the same as verified")

    p = pairs(records=fresh_records)
    print(f"\n       {len(recs)} record(s) -> {len(p)} usable pair(s) "
          f"({sum(1 for x in p if x['label']=='negative')} negative, "
          f"{sum(1 for x in p if x['label']=='positive')} positive)")

    print()
    if fails:
        print(f"  {len(fails)} FAILURE(S): {fails}")
        return 1
    print("  the feedback loop holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
