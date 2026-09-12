#!/usr/bin/env python3
"""No agent may authorise money, and nothing moves on `unknown`.

    python3 tools/check_financial_authority.py

PHASE 0 OF THE FINANCIAL PROGRAMME, ASSERTED. The policy is only worth what it
refuses under pressure, and the pressure here is ordinary: a later commit adds
a verb, widens a default, or lets a config grant `execute` for one convenient
case. Every check below is a thing that would look reasonable in a diff.

The financial ladder is the one place in this system where a wrong `allow` is
not recoverable. A misrouted grow reading can be voided. A payment cannot.
"""
import os
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    from core.financial_authority import (VERBS, HUMAN_ONLY, EFFECT, may,
                                          transferability, may_transfer,
                                          MAY_MOVE, TRANSFERABILITY, NotAVerb)

    print("\n  1. the ladder is closed and ordered by consequence")
    ck("every verb declares its effect", set(EFFECT) == set(VERBS),
       str(sorted(set(VERBS) ^ set(EFFECT))))
    ck("only execute and verify reach outside the machine",
       {v for v, e in EFFECT.items() if e["leaves_machine"]} == {"execute", "verify"},
       "prepare must produce a draft that stays here")
    ck("prepare does not leave the machine",
       not EFFECT["prepare"]["leaves_machine"],
       "a draft nobody sent is recoverable by deleting a file")
    try:
        may("accounting_agent", "wire", "asset:x")
        ck("an unknown verb is refused", False, "it was accepted")
    except NotAVerb:
        ck("an unknown verb is refused", True, "closed set, like the event types")

    print("\n  2. no agent holds authorize or execute - ever")
    ck("the reserved set is exactly authorize and execute",
       set(HUMAN_ONLY) == {"authorize", "execute"}, str(sorted(HUMAN_ONLY)))
    # Every agent in the ACL, against both reserved verbs, on its OWN resource.
    import json
    acl = json.load(open(os.path.join(ROOT, "config", "resource_acl.json"),
                         encoding="utf-8"))
    resources = acl.get("resources") or {}
    granted = []
    for res, rec in resources.items():
        owner = rec.get("owner")
        if not owner:
            continue
        for verb in HUMAN_ONLY:
            ok, _ = may(owner, verb, res)
            if ok:
                granted.append(f"{owner}:{verb}:{res}")
    ck("not one owner can authorize or execute its own resource",
       not granted, str(granted[:3]) or f"checked {len(resources)} resource(s)")
    # And it must not be reachable by handing in a permissive ACL either.
    ok, why = may("anyone", "execute", "asset:x",
                  acl_check=lambda *a, **k: (True, "a wide-open ACL"))
    ck("an all-permitting ACL still cannot grant execute", not ok, why[:60])

    print("\n  3. unknown is never permission to move")
    ck("MAY_MOVE excludes unknown and not_transferable",
       "unknown" not in MAY_MOVE and "not_transferable" not in MAY_MOVE,
       str(MAY_MOVE))
    ck("every declared state is a known state",
       set(MAY_MOVE) <= set(TRANSFERABILITY))
    t = transferability("no_such_account_anywhere")
    ck("an unrecorded asset is blocking, not permitted",
       t["state"] == "unknown" and t["blocking"],
       "an asset nobody recorded cannot be cleared for transfer")
    t = transferability("usps_money_order")
    ck("an untraced assignment chain is blocking", t["blocking"],
       f"state={t['state']}")

    print("\n  4. the VA entitlement cannot be moved into an entity")
    # THE ONE THE PROGRAMME WOULD OTHERWISE HIT AT PHASE 8. 38 U.S.C.
    # 5301(a)(1): payments of benefits due or to become due "shall not be
    # assignable except to the extent specifically authorized by law".
    # va_disability_compensation is the GENERIC statutory entry and stays in
    # the public schema; va_compensation_with_fiduciary describes the
    # principal's own appointment and lives in the gitignored overlay. The
    # statute is the same either way, so the always-present entry carries the
    # assertion and the personal one is checked when it is there. A gate that
    # needed private data to test a public rule would be untestable by anyone
    # but him.
    from core.account_model import load as _load_accounts
    _present = set((_load_accounts().get("accounts") or {}))
    for acct in [a for a in ("va_disability_compensation",
                             "va_compensation_with_fiduciary")
                 if a in _present]:
        t = transferability(acct)
        ck(f"{acct} is not_transferable",
           t["state"] == "not_transferable" and t["blocking"], t["state"])
        ck(f"{acct} refusal carries the statute",
           bool(t.get("citation")) and "5301" in str(t.get("citation")),
           str(t.get("citation")))
        allowed, why = may_transfer(acct, "MYCOS Holdings LLC")
        ck(f"{acct} refuses a transfer to an entity", not allowed, why[:64])

    print("\n  5. may_transfer never performs a transfer")
    # Even where nothing forbids it. This function answers whether the door is
    # closed; opening it is an `execute`, and `execute` is a person's act.
    allowed, why = may_transfer("va_disability_compensation", "X")
    ck("it returns False even when it is not refusing structurally",
       not allowed)
    src = open(os.path.join(ROOT, "core", "financial_authority.py"),
               encoding="utf-8").read()
    ck("the module writes nothing and calls nothing outward",
       "requests" not in src and "urllib" not in src
       and "store_own_memory" not in src and "subprocess" not in src,
       "a policy layer that could act is not a policy layer")

    print()
    if fails:
        print(f"  {len(fails)} FAILURE(S): {fails}")
        return 1
    print("  the financial ladder holds: agents advise and prepare, people act")
    return 0


if __name__ == "__main__":
    sys.exit(main())
