#!/usr/bin/env python3
"""The custody ledger audits the chain and never argues the law; two agents
produce the same bytes; unverified claims are flagged, not asserted.

    python3 tools/check_custody.py

Every fixture is synthetic; the store is a temp directory.
"""
import os
import sys
import tempfile

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def case():
    return {
        "case_id": "synthetic-shelter-001", "veteran": "Test Veteran",
        "custodian": "Example Shelter",
        "instruments": {"dd214": {"kind": "dd214", "purpose": "verify_service"},
                        "award_letter": {"kind": "award_letter", "purpose": "verify_income"}},
        "events": [
            {"kind": "issued", "instrument": "dd214", "date": "2015-03-01", "by": "DoD",
             "evidence": "stated_by_principal"},
            {"kind": "transmitted", "instrument": "dd214", "date": "2026-06-02", "form": "original",
             "evidence": "stated_by_principal"},
            {"kind": "received", "instrument": "dd214", "date": "2026-06-02", "by": "Example Shelter",
             "evidence": "cited", "ref": "intake receipt p.1"},
            {"kind": "transmitted", "instrument": "award_letter", "date": "2026-06-02", "form": "copy",
             "evidence": "stated_by_principal"},
            {"kind": "received", "instrument": "award_letter", "date": "2026-06-02",
             "by": "Example Shelter", "evidence": "cited", "ref": "intake receipt p.1"},
            {"kind": "enabled", "instrument": "award_letter", "date": "2026-07-01", "amount": "1200.00",
             "payee": "Example Shelter", "evidence": "cited", "ref": "VA check 0001"},
            {"kind": "enabled", "instrument": "award_letter", "date": "2026-08-01", "amount": "1200.00",
             "payee": "Example Shelter", "evidence": "cited", "ref": "VA check 0002"},
            {"kind": "credited", "instrument": "award_letter", "date": "2026-07-03",
             "by": "Example Shelter", "amount": "1200.00", "booked_as": "program revenue",
             "evidence": "cited", "ref": "shelter ledger line 44"},
            {"kind": "deducted", "instrument": "award_letter", "date": "2026-07-31",
             "by": "Example Shelter", "amount": "650.00", "service": "room and board July",
             "evidence": "cited", "ref": "invoice J-07"},
            {"kind": "deducted", "instrument": "award_letter", "date": "2026-08-31",
             "by": "Example Shelter", "amount": "650.00", "service": "room and board August",
             "evidence": "cited", "ref": "invoice J-08"},
            {"kind": "deducted", "instrument": "award_letter", "date": "2026-08-31",
             "by": "Example Shelter", "amount": "400.00", "service": "case management fee",
             "evidence": "unknown"},
        ]}


def main():
    from core import instrument_custody as ic

    print("the acceptance case: a DD-214 and an award letter received by a shelter")
    L = ic.ledger(case())
    ck("custody chain for both instruments", set(L["chains"]) == {"dd214", "award_letter"})
    ck("the checks the documents enabled are summed: $2,400.00", L["enabled"]["total"] == "$2,400.00")
    ck("evidenced services deducted: $1,300.00", L["deductions"]["evidenced_total"] == "$1,300.00")
    ck("the unverified fee is flagged and NOT deducted",
       L["deductions"]["unverified_total"] == "$400.00" and L["surplus_owed_back"] == "$1,100.00")
    f = {(x["finding"], x["instrument"]) for x in L["flags"]}
    ck("the original DD-214 received for verification is a sold instrument",
       ("sold_instrument", "dd214") in f)
    ck("received with no credit posted is a missing ledger entry",
       ("missing_ledger_entry", "dd214") in f)
    ck("a credit booked as the custodian's revenue is conversion AND still a missing entry",
       ("conversion", "award_letter") in f and ("missing_ledger_entry", "award_letter") in f
       and L["chains"]["award_letter"]["status"]["credited"] == "booked_as_custodian_revenue")
    ck("neither credited nor returned -> the binary demand, undecided",
       ("binary_demand", "dd214") in f and
       all("not decided" in x["detail"] for x in L["flags"] if x["finding"] == "binary_demand"))
    ck("every finding carries the rule's evidence state, never a conclusion of law",
       all(x["basis"] == "stated_by_principal" and x["rule"] for x in L["flags"]))
    ck("the output is a ledger: no 'therefore', 'liable', 'breach' in the text",
       not any(w in L["text"].lower() for w in ("therefore", "liable", "is in breach")))

    print("determinism")
    c2 = case()
    c2["events"] = list(reversed(c2["events"]))
    ck("events in reverse order -> same bytes", ic.ledger(c2)["sha256"] == L["sha256"])
    c3 = case()
    c3["instruments"] = dict(reversed(list(c3["instruments"].items())))
    ck("instruments in another order -> same bytes", ic.ledger(c3)["sha256"] == L["sha256"])

    print("refusals")
    def refuses(mut, name):
        c = case()
        mut(c)
        try:
            ic.ledger(c)
            ck(name, False)
        except ic.Refused as exc:
            ck(name, True, str(exc)[:70])
    refuses(lambda c: c["events"].append({"kind": "lost", "instrument": "dd214", "date": "2026-06-03"}),
            "an event outside the lifecycle is refused")
    refuses(lambda c: c["events"].append({"kind": "received", "instrument": "sgli",
                                          "date": "2026-06-03", "by": "x"}),
            "an event on an undeclared instrument is refused")
    refuses(lambda c: c["events"].append({"kind": "enabled", "instrument": "dd214",
                                          "date": "2026-06-03", "amount": "lots"}),
            "an amount that is not a number is refused")
    refuses(lambda c: c["events"].append({"kind": "transmitted", "instrument": "dd214",
                                          "date": "2026-06-03", "form": "fax"}),
            "a transmission form outside original|copy|attestation is refused")
    refuses(lambda c: c["instruments"].update({"voucher": {"kind": "voucher", "value": 100}}),
            "an instrument with a value and no maturity is refused")
    refuses(lambda c: c["events"].append({"kind": "received", "instrument": "dd214",
                                          "date": "June 2", "by": "x"}),
            "a date that is not YYYY-MM-DD is refused")

    print("the store")
    tmp = tempfile.mkdtemp()
    ic.STORE = tmp
    try:
        ic.add_event("synthetic-002", {"kind": "received", "instrument": "dd214",
                                       "date": "2026-06-02", "by": "Example Shelter",
                                       "ref": "file 123-45-6789"},
                     custodian="Example Shelter", instruments={"dd214": {"kind": "dd214"}})
        ck("an event carrying an identifier is refused before it is written", False)
    except Exception as exc:
        ck("an event carrying an identifier is refused before it is written",
           "9-digit" in str(exc) or "ssn" in str(exc).lower())
    ck("and nothing was written", not os.listdir(tmp))
    ic.add_event("synthetic-002", {"kind": "received", "instrument": "dd214",
                                   "date": "2026-06-02", "by": "Example Shelter", "evidence": "cited"},
                 custodian="Example Shelter", instruments={"dd214": {"kind": "dd214",
                                                                     "purpose": "verify_service"}})
    p = os.path.join(tmp, "synthetic-002.json")
    ck("a case file is written 0600", os.path.exists(p) and (os.stat(p).st_mode & 0o777) == 0o600)
    ck("the stored case produces a ledger", ic.ledger(ic.load_case("synthetic-002"))["case_id"] == "synthetic-002")

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails[:6]))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
