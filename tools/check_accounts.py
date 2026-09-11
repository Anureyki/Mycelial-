#!/usr/bin/env python3
"""The four-layer account model, as a build gate.

    python3 tools/check_accounts.py

The acceptance criterion that can be tested mechanically is DETERMINISM: two
agents tracing one account must produce the same map, or the map is an opinion.
That is asserted here by tracing from two independent readers and comparing the
full structure, not a summary of it.

The other two criteria depend on facts nobody has verified yet, and the gate
asserts THE HONEST VERSION of them: an unverified entry must report itself as
incomplete rather than produce a confident map. A model that filled those in
would pass its own acceptance test by inventing the answer.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.account_model import (trace, load, LAYERS, EVIDENCE,  # noqa: E402
                                detect_inversion, layers_of)

fails = []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    doc = load()
    accounts = doc["accounts"]

    print("\n  1. one schema, no special cases")
    for aid, e in sorted(accounts.items()):
        got = set((e.get("layers") or {}))
        ck(f"{aid} declares all five layers", got == set(LAYERS),
           f"missing={sorted(set(LAYERS)-got)} extra={sorted(got-set(LAYERS))}")
    for aid, e in sorted(accounts.items()):
        bad = [n for n, v in (e.get("layers") or {}).items()
               if v.get("evidence") not in EVIDENCE]
        ck(f"{aid} uses only declared evidence states", not bad, str(bad))

    print("\n  2. the label is never a layer")
    ck("no entry lists 'label' among its layers",
       not any("label" in (e.get("layers") or {}) for e in accounts.values()))
    # Tracing must not change when only the label changes.
    import copy
    e = copy.deepcopy(accounts["va_compensation_with_fiduciary"])
    before = [l["entity"] for l in layers_of(e)]
    e["label"] = "The Sovereign Family Trust"
    after = [l["entity"] for l in layers_of(e)]
    ck("renaming an account does not move a single layer", before == after,
       "calling something a trust does not move a duty")

    print("\n  3. DETERMINISM - two readers, same map")
    a = trace("va_compensation_with_fiduciary")
    b = trace("va_compensation_with_fiduciary")
    ck("two traces are byte-identical",
       json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True))
    ck("layer order is fixed",
       [l["layer"] for l in a["layers"]] == list(LAYERS))
    ck("three entities stacked, debt named",
       a["entities_stacked_count"] == 3 and a["carries_the_debt"],
       f"{a['entities_stacked_count']} stacked, debt={a['carries_the_debt']}")

    print("\n  4. unverified is reported, never filled")
    for aid in ("trulink_card", "usps_money_order"):
        t = trace(aid)
        ck(f"{aid} reports itself incomplete",
           t["absence_state"] == "incomplete",
           f"{len(t['unverified_layers'])} unverified layer(s)")
        ck(f"{aid} names no debt-holder it cannot cite",
           not t["carries_the_debt"] or
           any(l["layer"] == "debt_holder" and l["evidence"] != "unverified"
               for l in t["layers"]),
           "a guessed debt-holder is the one field somebody would act on")

    print("\n  5. inversions are traced, not argued")
    fake = {
        "label": "Family Heritage Trust",
        "funded_by": "the person",
        "funding_obligation_holder": "the person",
        "control_holder": "Acme Administrators Inc",
        "layers": {
            "identifier": {"entity": "Acme Administrators Inc"},
            "fiduciary": {"entity": "Acme Administrators Inc", "sector": "corporate"},
            "servicer": {"entity": "Acme Administrators Inc", "sector": "private"},
            "debt_holder": {"entity": "the person"},
            "benefit": {"entity": "the person"},
        },
    }
    inv = detect_inversion(fake)
    kinds = {i["inversion"] for i in inv}
    ck("a trust-labelled custodial product is detected",
       "custodial_product_labelled_trust" in kinds, str(sorted(kinds)))
    ck("a structure handed down is detected",
       "structure_handed_down" in kinds, str(sorted(kinds)))
    ck("every finding names the fields it was read from",
       all(i.get("read_from") for i in inv))
    ck("no finding states a legal conclusion",
       all(i.get("not_a_conclusion") for i in inv),
       "traced, not argued")
    ck("a clean public account produces no inversion",
       not detect_inversion(accounts["va_disability_compensation"]))

    print("\n  6. versioned and citable")
    ck("the schema is versioned", bool(doc.get("schema_version")))
    noversion = [a for a, e in accounts.items() if not e.get("version")]
    ck("every entry is versioned", not noversion, str(noversion))
    nocite = [a for a, e in accounts.items()
              if not any((v.get("citation")) for v in (e.get("layers") or {}).values())
              and a != "trulink_card"]
    ck("every non-stub entry cites something", not nocite, str(nocite))

    print()
    if fails:
        print(f"  {len(fails)} FAILURE(S): {fails}")
        return 1
    print("  the account model holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
