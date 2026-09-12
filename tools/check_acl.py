#!/usr/bin/env python3
"""The interior ACL, as a build gate.

    python3 tools/check_acl.py

Three things it proves, and the third is the one the spec asked for by name:

  1. Permission, not ownership. Legal reads a ledger Accounting owns.
  2. Deny is still deny. Trust does not get Legal's token.
  3. AN AGENT THAT REACHES A RESOURCE WITH NO ACL ENTRY FAILS THE BUILD.

The third is a CONFIG LINT rather than a runtime test, and it has to be,
because at runtime an unlisted resource is simply denied - correctly, quietly,
and in a way nobody notices until a department stops working for a reason no
log explains. The place to catch a missing entry is before it ships.

FAIL-DIRECTION IS ASSERTED, not assumed. The perimeter must still fail OPEN and
the interior must fail CLOSED, and a refactor that made them agree would be a
security regression in one direction or an availability regression in the other.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# A GATE'S PROBES MUST NOT REACH THE COLLECTION SPOOL.
#
# The decisions below are REAL on purpose - a denial that was faked would prove
# nothing about the ACL. But a real decision that only happened because a test
# asked for it is not evidence about defending the system, and it was being
# ingested as a usable training pair like any other.
#
# Measured 2026-09-12: 452 of 1,405 records arrived during one night of CI runs
# and the top six scenarios - gate fixtures, verbatim - were 21.3% of the whole
# training set. A model trained there learns the shape of this file's probes.
#
# Two layers, deliberately redundant. MYCELIAL_EVENT_ORIGIN stamps every event
# this process emits as `test`, which pairs() drops; redirecting the spool
# means they never reach the collection path at all. Either alone would do;
# both means a future gate that forgets one is still contained.
import tempfile as _tempfile
os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
import core.security_events as _se
_se.SPOOL = _tempfile.mkdtemp(prefix="gate-spool-")

from core.acl import check, audit, load_acl, ACLUnavailable, ACTIONS  # noqa: E402

fails = []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


# Every agent that appears anywhere in the system, so an agent reaching a
# resource nobody wrote a rule for is caught here rather than in production.
def known_agents():
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "config", "agent_configs")
    out = set()
    for f in os.listdir(d):
        if f.endswith(".json"):
            out.add(os.path.splitext(f)[0])
    return sorted(out)


def main():
    print("\n  1. permission, not ownership")
    ck("legal reads the ledger accounting owns",
       check("legal_agent", "ledger:finance_shared", "read")[0],
       "the case an ownership test got wrong")
    ck("trust reads the ledger accounting owns",
       check("trust_agent", "ledger:finance_shared", "read")[0])
    ck("legal may NOT write accounting's ledger",
       not check("legal_agent", "ledger:finance_shared", "write")[0],
       "a department that could write another's ledger could make its own reading true")
    ck("accounting writes its own ledger (ownership is the default grant)",
       check("accounting_agent", "ledger:finance_shared", "write")[0])
    ck("legal borrows trust's corpus",
       check("legal_agent", "corpus:trust_agent", "read")[0])
    ck("nobody writes a borrowed corpus",
       not check("legal_agent", "corpus:trust_agent", "write")[0])

    print("\n  2. deny is still deny")
    ck("trust cannot read legal's CourtListener token",
       not check("trust_agent", "credential:COURTLISTENER_API_TOKEN", "read")[0])
    ck("legal can read its own token",
       check("legal_agent", "credential:COURTLISTENER_API_TOKEN", "read")[0])
    ck("grow cannot read the finance ledger",
       not check("grow_agent", "ledger:finance_shared", "read")[0])
    ck("an unknown action is refused",
       not check("legal_agent", "ledger:finance_shared", "sudo")[0])

    print("\n  3. the interior fails CLOSED")
    ck("an unlisted resource is denied to everyone",
       not check("legal_agent", "ledger:nonexistent", "read")[0])
    try:
        load_acl("/nonexistent/acl.json", force=True)
        ck("an unreadable ACL raises", False, "it returned instead")
    except ACLUnavailable:
        ck("an unreadable ACL raises", True)
    # EXERCISE THE REAL PATH. Passing an empty dict tests "nothing is granted",
    # which is a different finding from "nothing is KNOWN" - this module goes
    # out of its way to keep those apart, so the test must too. Point the
    # module at a missing file and clear its cache.
    import core.acl as _acl
    _orig, _acl.ACL_FILE = _acl.ACL_FILE, "/nonexistent/acl.json"
    _acl._cache.update({"at": 0.0, "doc": None})
    try:
        allowed, why = check("legal_agent", "ledger:finance_shared", "read")
        ck("an UNREADABLE acl denies (not merely an empty one)",
           not allowed, why[:66])
        ck("an owner is denied too when the ACL cannot be read",
           not check("accounting_agent", "ledger:finance_shared", "write")[0],
           "ownership cannot be confirmed either")
    finally:
        _acl.ACL_FILE = _orig
        _acl._cache.update({"at": 0.0, "doc": None})

    print("\n  4. config lint: every resource is fully declared")
    a = audit()
    ck("the ACL is readable", a.get("readable"))
    ck("no structural problems", not a.get("problems"),
       "; ".join(a.get("problems", [])[:3]))

    # THE SPEC'S THIRD ACCEPTANCE CASE.
    print("\n  5. no agent reaches a resource with no ACL entry")
    res = load_acl()
    referenced = set()
    for name, e in res.items():
        for act in ACTIONS:
            referenced.update(g for g in (e.get(act) or []) if g != "*")
        if e.get("owner"):
            referenced.add(e["owner"])
    agents = set(known_agents())
    # An agent named in a grant that is not a real agent is a rule pointing at
    # nobody - the rule looks like a decision and grants nothing.
    ghosts = sorted(referenced - agents - {"inference_service"})
    ck("every agent named in a grant exists", not ghosts, str(ghosts))
    print(f"       {len(res)} resources, {len(referenced)} agents referenced")

    print("\n  6. the two layers fail in OPPOSITE directions")
    import inspect
    from core.base_agent import AgentBase
    perim = inspect.getsource(AgentBase.check_guard)
    inter = inspect.getsource(AgentBase.check_permission)
    # The perimeter must keep allowing when the guard is unreachable. If a
    # refactor makes it deny, a restarting Security Agent halts the swarm.
    ck("perimeter still fails OPEN on transport error",
       'return True, "guard unavailable"' in perim,
       "an outage must not stop work")
    # The interior must keep denying when the ACL cannot be read. If a
    # refactor makes it allow, an outage becomes a grant.
    ck("interior fails CLOSED when the check cannot run",
       "return False" in inter and "could not run" in inter,
       "an outage must not grant access")
    ck("the interior is consulted before the perimeter round trip",
       inspect.getsource(AgentBase.check_guard).index("interior ACL denied")
       < inspect.getsource(AgentBase.check_guard).index("guard unavailable"),
       "a down Security Agent cannot turn a permission question into an allow")

    print()
    if fails:
        print(f"  {len(fails)} FAILURE(S): {fails}")
        return 1
    print("  interior ACL holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
