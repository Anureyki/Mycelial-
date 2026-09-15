#!/usr/bin/env python3
"""The § 1681s-2(b) sheet refuses: no bureau notice, no standing, dead clock.

    python3 tools/check_fcra_furnisher.py

Every fact pattern is synthetic; the store is a temp directory. The lane is
locked until this file passes, which is the point of locking it.
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


def main():
    from core import fcra_furnisher as F
    F.STORE = tempfile.mkdtemp()

    print("the lane test: no CRA dispute is not this lane")
    c = F.open_claim("t-a", "Consumer v. Furnisher",
                     violation_date="2026-01-10", discovery_date="2026-01-15")
    F.set_bureau_notice(c, False)
    r = F.assess(c, today="2026-09-15")
    ck("refuses the lane when no dispute reached the furnisher through a CRA",
       r.get("lane_refused", {}).get("verdict") == "use_1681e_b_or_1681i_or_no_claim")
    ck("and names 1681s-2(a) having no private right",
       "no_1681s2a_private_right" in r["killer_defenses"])
    ck("and points at where it might live",
       any("1681e(b)" in w for w in r["lane_refused"]["where_it_might_live"]))
    ck("never cite_ready", r["cite_ready"] is False)
    c2 = F.open_claim("t-a2", "Direct dispute", reg="12 C.F.R. § 1022.43",
                      violation_date="2026-01-10", discovery_date="2026-01-15")
    F.set_bureau_notice(c2, False)
    r2 = F.assess(c2, today="2026-09-15")
    ck("a qualifying direct dispute under 1022.43 is not lane-refused",
       "lane_refused" not in r2)

    print("standing is blocking, after Ramirez")
    c = F.open_claim("t-b", "Consumer v. Furnisher",
                     violation_date="2026-06-11", discovery_date="2026-06-20")
    F.set_bureau_notice(c, True, doc_id="results-letter", date_="2026-06-11")
    for el, doc in (("1_cra_received_dispute", "dispute"),
                    ("4_investigation_reasonable", "depo"),
                    ("5_result_reported_back", "acdv"),
                    ("6_inaccuracy_or_incompleteness", "report"),
                    ("7_willful_or_negligent", "acdv"),
                    ("9_remedy", "denial")):
        F.add_evidence(c, doc, "2026-07-01", "synthetic", el)
    r = F.assess(c, today="2026-09-15")
    ck("every element but publication proved is still contested",
       r["status"] == "contested" and r["elements_unproved"] == ["8_publication_to_third_party"])
    F.set_publication(c, recipient="Equifax internal file", recipient_kind="internal")
    r = F.assess(c, today="2026-09-15")
    ck("'it sat at the bureau' FAILS standing, not unknown",
       r["blocking"]["standing_publication"] == "failed" and "no_standing" in r["killer_defenses"])
    ck("and says what would close it",
       "lender" in r["elements"]["8_publication_to_third_party"]["what_would_close_it"])
    F.set_publication(c, recipient="Example Mortgage Co", recipient_kind="lender",
                      doc_id="denial-letter", date_="2026-07-15")
    r = F.assess(c, today="2026-09-15")
    ck("a named lender with a document proves it and clears the block",
       r["blocking"]["standing_publication"] == "proved" and r["cite_ready"] is True)
    F.set_publication(c, recipient="A Cousin", recipient_kind="relative", doc_id="note")
    r = F.assess(c, today="2026-09-15")
    ck("an unrecognised recipient is not proved and not refused - it is read",
       r["blocking"]["standing_publication"] == "unknown"
       and "not one of the usual recipients" in
       r["elements"]["8_publication_to_third_party"]["what_would_close_it"])

    print("the two clocks of 1681p")
    s = F.limitations("2026-06-11", "2026-06-20", today="2026-09-15")
    ck("takes the earlier of the two", s["deadline"] == "2028-06-20"
       and s["governed_by"] == "discovery", s["deadline"])
    s = F.limitations("2019-06-11", "2026-06-20", today="2026-09-15")
    ck("the outer clock governs when it is earlier",
       s["deadline"] == "2024-06-11" and s["governed_by"] == "outer" and s["state"] == "expired")
    s = F.limitations(None, None)
    ck("no dates -> no clock, said plainly", s["state"] == "unknown" and s["deadline"] is None)
    s = F.limitations("2026-06-11", None, today="2026-09-15")
    ck("one clock reports itself incomplete", "incomplete" in s)
    c["violation_date"], c["discovery_date"] = "2019-06-11", "2019-06-20"
    F.set_publication(c, recipient="Example Mortgage Co", recipient_kind="lender",
                      doc_id="denial-letter", date_="2026-07-15")
    r = F.assess(c, today="2026-09-15")
    ck("an otherwise complete sheet with a dead clock is contested",
       r["status"] == "contested" and "sol" in r["killer_defenses"])

    print("the sheet refuses to invent")
    for bad, why in ((lambda: F.open_claim("", "x"), "no claim_id"),
                     (lambda: F.open_claim("x", ""), "no caption"),
                     (lambda: F.open_claim("x", "y", circuit="99th"), "unknown circuit"),
                     (lambda: F.open_claim("x", "y", violation_date="June 11"), "bad date"),
                     (lambda: F.add_evidence(F.open_claim("x", "y"), "d", "2026-01-01",
                                             "p", "10_not_an_element"), "unknown element")):
        try:
            bad()
            ck(f"refuses: {why}", False)
        except F.Refused:
            ck(f"refuses: {why}", True)

    print("binding is not persuasive")
    auth = F.authorities(resolver=None)
    g = {a["citation"]: a for a in auth["authorities"]}
    ck("Gorman is carried as persuasive in Texas",
       g["Gorman v. Wolpoff & Abramson"]["weight_in_texas"] == "persuasive")
    ck("the Supreme Court cases are binding",
       all(g[k]["weight_in_texas"] == "binding" for k in
           ("TransUnion LLC v. Ramirez", "Spokeo, Inc. v. Robins",
            "Safeco Insurance Co. of America v. Burr")))
    ck("the missing Fifth Circuit case is a DECLARED gap, not silence",
       auth["gaps"] and auth["gaps"][0]["state"] == "not_found"
       and "Fifth Circuit" in auth["gaps"][0]["wanted"])

    print("the store")
    c = F.open_claim("t-store", "Consumer v. Furnisher")
    p = F.save(F.assess(c))
    ck("a claim file is written 0600", (os.stat(p).st_mode & 0o777) == 0o600)
    ck("and reads back", F.load("t-store")["claim_id"] == "t-store")
    try:
        bad = F.open_claim("t-id", "Consumer v. Furnisher")
        F.add_evidence(bad, "d", "2026-01-01", "SSN 123-45-6789 on the tradeline",
                       "6_inaccuracy_or_incompleteness")
        F.save(bad)
        ck("evidence carrying an identifier is refused before it is written", False)
    except F.Refused as exc:
        ck("evidence carrying an identifier is refused before it is written",
           "9-digit" in str(exc) or "ssn" in str(exc).lower())

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails[:6]))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
