#!/usr/bin/env python3
"""The drift monitor, as a build gate.

    python3 tools/check_drift.py

A monitor that has never fired is indistinguishable from a monitor that cannot
fire, so this proves each detector against a synthetic case and proves the
quiet case stays quiet. The false-positive test is not a formality: the first
version of this monitor alerted on a clean system twice, and an alert that
fires on normal operation is an alert somebody turns off.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

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

from core.drift_monitor import scan, THRESHOLDS  # noqa: E402

fails = []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def rec(seq, agent, resource, decision, event="acl_denied", label="negative",
        usable=True, prov=None):
    return {"seq": seq, "observed_at": datetime.now(timezone.utc).isoformat(),
            "event_type": event, "agent": agent, "resource": resource,
            "action": "read", "decision": decision, "reason": "x" * 20,
            "provenance_event": prov, "provenance_verified": bool(prov),
            "label": label, "usable_as_pair": usable, "why": "t",
            "source_sha256": str(seq), "prev_hash": "0" * 64,
            "record_hash": "0" * 64}


def run(records, quarantine=()):
    d = tempfile.mkdtemp()
    try:
        with open(os.path.join(d, "records-t.jsonl"), "w") as fh:
            fh.write("\n".join(json.dumps(r) for r in records))
        if quarantine:
            with open(os.path.join(d, "_quarantine.jsonl"), "w") as fh:
                fh.write("\n".join(json.dumps(r) for r in quarantine))
        return scan(records=d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    legit = [rec(i, "mycos_core", "corpus:grow_agent", "allowed",
                 "acl_allowed", "positive", True, f"p{i}") for i in range(1, 61)]

    print("\n  the quiet cases must stay quiet")
    ck("a model using its legitimate grants does not alert",
       not run(legit)["alerts"],
       "an alert on normal operation is an alert somebody turns off")
    normal = ([rec(i, "legal_agent", "corpus:trust_agent", "allowed",
                   "acl_allowed", "positive", True, f"p{i}") for i in range(1, 40)]
              + [rec(40 + i, "legal_agent", "credential:SOLANA_RPC_URL", "denied")
                 for i in range(1, 6)])
    ck("an ordinary agent being denied a few times does not alert",
       not run(normal)["alerts"])

    print("\n  each detector fires on its own case")
    probe = legit + [rec(60 + i, "mycos_core", f"credential:SECRET_{i}", "denied")
                     for i in range(1, 25)]
    kinds = {a["kind"] for a in run(probe)["alerts"]}
    ck("requesting resources outside its ACL alerts", "outside_acl" in kinds,
       str(sorted(kinds)))

    inj = legit[:20] + [rec(100 + i, "mycos_core", "ledger:finance_shared",
                            "allowed", "acl_allowed", "positive", True, None)
                        for i in range(1, 10)]
    weak = [rec(200 + i, "mycos_core", "ledger:finance_shared", "allowed",
                "acl_allowed", "positive", False, None) for i in range(1, 12)]
    kinds = {a["kind"] for a in run(inj, weak)["alerts"]}
    ck("self-authored positives alert", "self_authored_positives" in kinds,
       str(sorted(kinds)))

    print("\n  it detects, it does not adjudicate")
    out = run(probe)
    ck("every alert carries its numbers",
       all(a.get("detail") for a in out["alerts"]))
    ck("every alert disclaims being a verdict",
       all("not_a_verdict" in a for a in out["alerts"]))
    ck("no alert carries an action to take",
       not any(k in json.dumps(a).lower()
               for a in out["alerts"]
               for k in ("revoke", "disable", "halt_agent", "quarantine_agent")),
       "a monitor that acted on its own findings would be an authority "
       "nobody granted")

    print("\n  thresholds are declared, not buried")
    ck("thresholds are readable", bool(THRESHOLDS), f"{len(THRESHOLDS)} declared")

    print()
    if fails:
        print(f"  {len(fails)} FAILURE(S): {fails}")
        return 1
    print("  the drift monitor holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
