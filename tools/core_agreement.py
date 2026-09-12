#!/usr/bin/env python3
"""Does Mycos Core agree with the ACL? Measured, on every real rule.

    python3 tools/core_agreement.py

THE FIRST THING TO DO WITH A NEWLY CONNECTED MODEL IS NOT TRUST IT. Core now
answers questions; this asks it every permission question the ACL actually
declares and scores it against the ACL's own answer. The ACL is the ground
truth here because it IS the rule - it is not a second opinion, it is the
thing the model was trained to imitate.

NOTHING IS ENFORCED AND NOTHING IS RECORDED AS A DECISION. This reads the ACL,
asks Core, and prints a table. Core cannot grant or refuse anything through
this path or any other - see core/core_client.py, whose return has no boolean
in it for exactly that reason.

WHY PER-CLASS AND NOT ACCURACY. The ACL denies far more combinations than it
allows, so a model that denies everything scores well on plain accuracy and is
useless. The number that matters is whether it gets ALLOWS right, and the
promoted checkpoint's own held-out recall says it manages 0.42 of them.

IT DECLARES ITSELF TEST ORIGIN. Every acl.check() below is a real decision and
emits a real event. Two commits ago the build gates were found poisoning the
training set with exactly this shape - 452 records in one night, gate fixtures
making up a fifth of everything a student would train on. A tool that measured
the model by feeding the model would be the same fault with better manners.
"""
import itertools
import json
import os
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.acl import check as acl_check          # noqa: E402
from core.core_client import ask, model          # noqa: E402

ACL = os.path.join(ROOT, "config", "resource_acl.json")
ACTIONS = ("read", "write", "execute")


def main():
    m = model()
    if not m.get("available"):
        print(f"  Core is not reachable: {m.get('why')}")
        print("  Start it:  /home/anureyki/mycelial/venv/bin/python3 "
              "inference/serve.py --port 8018   (in mycelial-core)")
        return 2
    mm = m["model"]
    print(f"\n  checkpoint : {mm['checkpoint']}")
    hm = mm.get("heldout_metrics") or {}
    print(f"  its own held-out scores: decision {hm.get('decision_accuracy')} "
          f"register {hm.get('register_accuracy')} "
          f"balanced {hm.get('balanced_accuracy')}")
    print(f"  per-class recall it reports: {hm.get('per_class_recall')}")

    with open(ACL, encoding="utf-8") as fh:
        doc = json.load(fh)
    resources = doc.get("resources") or {}
    agents = sorted({a for r in resources.values()
                     for key in ("read", "write", "execute")
                     for a in (r.get(key) or [])} |
                    {r.get("owner") for r in resources.values() if r.get("owner")})
    if not (resources and agents):
        print("  no resources or agents in the ACL - nothing to compare")
        return 1

    rows = []
    for res, agent, action in itertools.product(sorted(resources), agents, ACTIONS):
        allowed, why = acl_check(agent, res, action)
        truth = "allowed" if allowed else "denied"
        r = ask(agent, action, res)
        if not r.get("available"):
            print(f"  Core stopped answering: {r.get('why')}")
            return 2
        o = r["opinion"]
        rows.append({"agent": agent, "action": action, "resource": res,
                     "truth": truth, "core": o["decision"],
                     "register": o["register"],
                     "confidence": o["confidence"]})

    total = len(rows)
    agree = sum(1 for x in rows if x["truth"] == x["core"])
    per = {}
    for cls in ("allowed", "denied"):
        sub = [x for x in rows if x["truth"] == cls]
        hit = sum(1 for x in sub if x["core"] == cls)
        per[cls] = (hit, len(sub))

    print(f"\n  {total} real permission questions from config/resource_acl.json")
    print(f"  agreement with the ACL : {agree}/{total} = {agree/total:.1%}")
    for cls, (hit, n) in per.items():
        if n:
            print(f"    the ACL says {cls:8} {n:4} time(s) - Core agrees "
                  f"{hit:4} = {hit/n:.1%}")
        else:
            print(f"    the ACL says {cls:8}    0 times - nothing to score")

    wrong_allows = [x for x in rows if x["truth"] == "allowed" and x["core"] == "denied"]
    wrong_denies = [x for x in rows if x["truth"] == "denied" and x["core"] == "allowed"]
    print(f"\n  would have WRONGLY REFUSED {len(wrong_allows)} legitimate "
          f"request(s) - the failure that breaks work")
    for x in wrong_allows[:5]:
        print(f"    {x['agent']} {x['action']} {x['resource'][:44]} "
              f"(conf {x['confidence']})")
    print(f"  would have WRONGLY ALLOWED {len(wrong_denies)} request(s) "
          f"- the failure that leaks")
    for x in wrong_denies[:5]:
        print(f"    {x['agent']} {x['action']} {x['resource'][:44]} "
              f"(conf {x['confidence']})")

    print(f"\n  ADVISORY. Nothing above was enforced and no access changed. "
          f"This is how far the model is from the rule, which is the only "
          f"honest thing to do with it at {agree/total:.0%}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
