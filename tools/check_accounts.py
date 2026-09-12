#!/usr/bin/env python3
"""The account model - six layers, a chain of interest and a money path -
as a build gate.

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

# A build gate is a test, and says so. Any event this process emits - now or
# after some future refactor gives this file an emit path it does not have
# today - is stamped `test` and can never become a training pair. Declared
# even where nothing emits yet, because the gate in check_eval.py asserts the
# declaration rather than the current call graph: remembering to add it later
# is exactly what nobody does.
os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"

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
    # THE PRIVATE OVERLAY IS ABSENT IN CI AND THAT IS CORRECT. The principal's
    # own entries live outside git because this repository is public; the
    # schema, the states and the rules are all here and all assertable without
    # them. So the personal cases run when the overlay is present - on his
    # machine, where it matters most - and are reported as SKIPPED, never as
    # passed, when it is not. A gate that silently asserted nothing would be
    # green for the wrong reason on every clean checkout.
    PERSONAL = ("va_compensation_with_fiduciary", "trulink_card",
                "deers_record", "military_pay_account")
    have_private = all(a in accounts for a in PERSONAL)

    def personal(name, fn):
        if not have_private:
            print(f"  SKIP  {name}   private overlay absent - not asserted, "
                  f"and not counted as passed")
            return
        fn()

    print("\n  1. one schema, no special cases")
    for aid, e in sorted(accounts.items()):
        got = set((e.get("layers") or {}))
        ck(f"{aid} declares all six layers", got == set(LAYERS),
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
    _label_case = ("va_compensation_with_fiduciary" if have_private
                   else "va_disability_compensation")
    e = copy.deepcopy(accounts[_label_case])
    before = [l["entity"] for l in layers_of(e)]
    e["label"] = "The Sovereign Family Trust"
    after = [l["entity"] for l in layers_of(e)]
    ck("renaming an account does not move a single layer", before == after,
       "calling something a trust does not move a duty")

    print("\n  3. DETERMINISM - two readers, same map")
    if not have_private:
        print("  SKIP  the determinism block uses a personal entry - "
              "private overlay absent")
        return _finish()
    a = trace("va_compensation_with_fiduciary")
    b = trace("va_compensation_with_fiduciary")
    ck("two traces are byte-identical",
       json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True))
    ck("layer order is fixed",
       [l["layer"] for l in a["layers"]] == list(LAYERS))
    # WAS "three entities stacked", and the status field correctly reduced it.
    # The VA appointment's status is unverified, so it does not COUNT as
    # binding - and the assertion had to change rather than the code, because
    # counting an appointment nobody confirmed would be the overstatement the
    # status field exists to prevent.
    # THREE AGAIN, and by evidence rather than by assumption. The appointment
    # certificate (VA Form 21P-555, 2025-01-30) arrived and settled the status
    # that had been `unknown`. This assertion has now been correct, then wrong,
    # then correct again - and each change was a document arriving, which is
    # what a status field is for.
    # FOUR SINCE 2026-09-12, AND THE CHANGE IS THE POINT. It was three while
    # the veteran appeared only under `benefit`, which distinct_entities
    # excludes. The `holder` layer surfaces them as a party who HOLDS A RIGHT
    # rather than only one who receives a benefit - and 38 U.S.C. 5301(a)(1)
    # makes that right non-assignable, which is a fact about this account that
    # the five-layer map had no place to put.
    ck("only BINDING entities are counted",
       a["entities_stacked_count"] == 4 and a["carries_the_debt"],
       f"{a['entities_stacked_count']} binding, debt={a['carries_the_debt']}")
    ck("nothing is left unverified on this account",
       not a.get("unverified_status"),
       str([u["layer"] for u in a.get("unverified_status") or []]))
    ck("a status backed by a document carries its date",
       any(l["layer"] == "fiduciary" and l.get("status_as_of")
           for l in a["layers"]),
       "active as of a certificate is not active today, and the map says which")

    print("\n  4. unverified is reported, never filled")
    for aid in [x for x in ("trulink_card", "usps_money_order") if x in accounts]:
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

    print("\n  5b. status: a revoked appointment is not a live one")
    from core.account_model import (LAYER_STATUS, BINDING, must_revoke,
                                    dead_layers)
    for aid, e in sorted(accounts.items()):
        bad = [n for n, v in (e.get("layers") or {}).items()
               if v.get("status") and v["status"] not in LAYER_STATUS]
        ck(f"{aid} uses only declared statuses", not bad, str(bad))
    ck("unknown is NOT binding",
       "unknown" not in BINDING,
       "a layer nobody checked must not read as in force")
    live = dict(accounts["va_compensation_with_fiduciary"])
    import copy
    revoked = copy.deepcopy(live)
    revoked["layers"]["fiduciary"]["status"] = "revoked"
    t_live = {l["layer"]: l for l in layers_of(live)}
    t_rev = {l["layer"]: l for l in layers_of(revoked)}
    ck("a revoked fiduciary stops binding",
       not t_rev["fiduciary"]["binding"])
    ck("a revoked layer is still REPORTED, not dropped",
       any(l["layer"] == "fiduciary" for l in dead_layers(revoked)),
       "somebody may still believe it is in force")
    active = copy.deepcopy(live)
    active["layers"]["fiduciary"]["status"] = "active"
    ck("an active appointment appears in must_revoke",
       any(r["layer"] == "fiduciary" for r in must_revoke(active)),
       "the Form 56 problem: appointment AND power of attorney both come off")
    # ON ITS OWN FIXTURE, NOT ON LIVE DATA. This asserted against the real VA
    # entry while that entry's status was unknown - and then a certificate
    # arrived, settled it to active, and the test failed for the best possible
    # reason. A gate whose expectation depends on data that can legitimately
    # improve is a gate that goes red when the system gets better.
    unconfirmed = copy.deepcopy(live)
    unconfirmed["layers"]["fiduciary"]["status"] = "unknown"
    ck("an unverified appointment does NOT appear in must_revoke",
       not any(r["layer"] == "fiduciary" for r in must_revoke(unconfirmed)),
       "pointing somebody at a revocation nobody confirmed is needed is worse "
       "than saying the status is unknown")


    print("\n  7. holder - the other side of the obligation")
    from core.account_model import (assignment_trail, holds_the_interest,
                                    chains, payment_path, CHAIN_KINDS)
    ck("holder is a declared layer", "holder" in LAYERS)
    ck("holder and debt_holder are different layers",
       LAYERS.index("holder") != LAYERS.index("debt_holder"))
    va = trace("va_disability_compensation")
    ck("a VA benefit names the veteran as holder",
       va["holds_the_interest"]["entity"] == "the veteran",
       "the obligor is the United States; the holder is not the same party")
    ck("a non-assignable benefit reports never_existed, not not_checked",
       va["assignment"]["state"] == "never_existed",
       "38 U.S.C. 5301(a)(1) - an empty chain here is a finding, not a gap")
    ck("that claim carries its citation",
       bool(va["assignment"]["citation"]))
    pp = trace("us_passport")
    ck("a document with no interest has no holder",
       pp["holds_the_interest"]["entity"] is None
       and pp["holds_the_interest"]["status"] == "never_existed")

    print("\n  8. the chain of interest, and every break in it")
    # ON FIXTURES, NOT ON LIVE DATA - the same rule section 5b learned when a
    # certificate arrived and turned a passing assertion red.
    base = {"layers": {n: {} for n in LAYERS}}
    def fixture(links, holder=None, originated=None, state=None, cite=None):
        e = {"layers": {n: {} for n in LAYERS}, "assignment": {"links": links}}
        if holder:     e["layers"]["holder"] = {"entity": holder, "status": "active"}
        if originated: e["originated_by"] = originated
        if state:      e["assignment"]["state"] = state
        if cite:       e["assignment"]["citation"] = cite
        return e

    L = lambda f, t, d=None: {"from": f, "to": t, "date": d, "evidence": "cited"}

    clean = fixture([L("Ancira Nissan", "NMAC", "2019-03-14"),
                     L("NMAC", "Westlake Portfolio", "2023-06-01")],
                    holder="Westlake Portfolio", originated="Ancira Nissan")
    t = assignment_trail(clean)
    ck("an unbroken, evidenced chain is verified_clear",
       t["state"] == "verified_clear" and not t["breaks"], str(t["breaks"]))

    gap = fixture([L("Ancira Nissan", "NMAC"), L("Some Other Bank", "Westlake")],
                  holder="Westlake")
    kinds = {b["kind"] for b in assignment_trail(gap)["breaks"]}
    ck("a chain break is named", "chain_break" in kinds, str(sorted(kinds)))

    dis = fixture([L("Ancira Nissan", "NMAC")], holder="Westlake Portfolio")
    kinds = {b["kind"] for b in assignment_trail(dis)["breaks"]}
    ck("a terminus that disagrees with the holder is named",
       "terminus_disagrees_with_holder" in kinds, str(sorted(kinds)))

    orig = fixture([L("Some Dealer", "NMAC")], holder="NMAC",
                   originated="Ancira Nissan")
    kinds = {b["kind"] for b in assignment_trail(orig)["breaks"]}
    ck("a chain that does not start at the originator is named",
       "origination_mismatch" in kinds, str(sorted(kinds)))

    back = fixture([L("A", "B", "2023-01-01"), L("B", "C", "2019-01-01")],
                   holder="C")
    kinds = {b["kind"] for b in assignment_trail(back)["breaks"]}
    ck("assignments dated out of order are named",
       "dates_out_of_order" in kinds, str(sorted(kinds)))

    lying = fixture([L("A", "B")], holder="B", state="never_existed",
                    cite="some statute")
    r = assignment_trail(lying)
    ck("an entry that declares no assignment while recording one is caught",
       r["state"] == "conflicting"
       and any(b["kind"] == "declared_state_disagrees" for b in r["breaks"]),
       "the record does not get to grade its own chain")

    unsourced_claim = fixture([], state="never_existed")
    r = assignment_trail(unsourced_claim)
    ck("never_existed without a citation falls back to not_checked",
       r["state"] == "not_checked",
       "an absence that strong is a claim about the law and needs a source")

    unev = fixture([{"from": "A", "to": "B", "evidence": "unverified"}],
                   holder="B")
    ck("an unevidenced link makes the chain incomplete, not clear",
       assignment_trail(unev)["state"] == "incomplete")

    print("\n  9. payment is not eligibility")
    e = {"layers": {n: {} for n in LAYERS}, "chains": [
        {"kind": "eligibility", "from": "DEERS", "to": "SGLI"},
        {"kind": "disbursement", "from": "Treasury", "to": "DFAS",
         "evidence": "cited", "citation": "x"},
        {"kind": "disbursement", "from": "DFAS", "to": "the member",
         "evidence": "cited", "citation": "x"},
        {"kind": "deduction", "from": "the member's basic pay", "to": "SGLI",
         "evidence": "cited", "citation": "38 U.S.C. 1969(a)(1)"},
    ]}
    pay = payment_path(e)
    ck("eligibility edges are kept OUT of the money path",
       all(x["kind"] != "eligibility" for x in pay["disbursement"] + pay["deduction"])
       and len(pay["eligibility"]) == 1,
       "a record system in the payment path makes a database look like a payer")
    ck("a complete, cited money path is verified_clear",
       pay["state"] == "verified_clear", pay["state"])
    ck("every chain kind is declared",
       all(x["kind"] in CHAIN_KINDS for x in chains(e)))
    broke = {"layers": {n: {} for n in LAYERS}, "chains": [
        {"kind": "disbursement", "from": "Treasury", "to": "DFAS",
         "evidence": "cited", "citation": "x"},
        {"kind": "disbursement", "from": "Somebody Else", "to": "the member",
         "evidence": "cited", "citation": "x"},
    ]}
    ck("a gap in the money path is named",
       any(b["kind"] == "disbursement_gap" for b in payment_path(broke)["breaks"]))
    said = {"layers": {n: {} for n in LAYERS}, "chains": [
        {"kind": "disbursement", "from": "Treasury", "to": "DFAS",
         "evidence": "stated_by_principal"}]}
    r = payment_path(said)
    ck("a path resting on the principal's word is incomplete, not clear",
       r["state"] == "incomplete" and r["edges_on_principal_statement"],
       "his account of his own affairs is a source, and is not corroboration")

    print("\n  6. versioned and citable")
    ck("the schema is versioned", bool(doc.get("schema_version")))
    noversion = [a for a, e in accounts.items() if not e.get("version")]
    ck("every entry is versioned", not noversion, str(noversion))
    # EITHER A CITATION OR A VISIBLE STATED_BY_PRINCIPAL, never neither.
    #
    # This used to demand a citation and carve out `trulink_card` by name. Then
    # two entries arrived built from the principal speaking - the only source
    # that exists for how his own accounts connect - and an exception list would
    # have grown by two. That is the wrong shape: it makes "we have no source"
    # and "the source is him" indistinguishable, and `asserted_by is recorded
    # and never scored` says they must not be. His statement is a source. It is
    # a different one, and the entry has to say which.
    unsourced = []
    for aid, e in accounts.items():
        vals = list((e.get("layers") or {}).values())
        if any(v.get("citation") for v in vals):
            continue
        if any(v.get("evidence") == "stated_by_principal" for v in vals):
            continue
        if all(v.get("evidence") == "unverified" for v in vals):
            continue          # a declared stub, which says so in _status
        unsourced.append(aid)
    ck("every entry is cited, stated by the principal, or a declared stub",
       not unsourced, str(unsourced))
    for aid in [x for x in ("deers_record", "military_pay_account") if x in accounts]:
        t = trace(aid)
        ck(f"{aid} does NOT report itself verified",
           t["absence_state"] != "verified_clear",
           f"{t['absence_state']}, on his statement: "
           f"{t['layers_on_principal_statement']}")

    return _finish()


def _finish():
    print()
    if fails:
        print(f"  {len(fails)} FAILURE(S): {fails}")
        return 1
    print("  the account model holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
