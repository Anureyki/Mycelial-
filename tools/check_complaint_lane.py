#!/usr/bin/env python3
"""A complaint routes by where the conduct happened, tolls nothing, and is
worth something only once its response names an element.

    python3 tools/check_complaint_lane.py

Synthetic; temp store.
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
    from core import complaint_lane as C
    C.STORE = tempfile.mkdtemp()

    print("routing follows the conduct, never the easiest form")
    r = C.route("debt_collection", conduct_state="TX")
    names = [v["venue"] for v in r["venues"]]
    ck("Texas debt collection reaches the CFPB and the Texas venues",
       "cfpb" in names and "tx_ag" in names and r["state_venue_resolved"], str(names))
    ck("and the Texas venue names ch. 392, which reaches original creditors",
       any("392.403" in a for v in r["venues"] if v["venue"] == "tx_ag"
           for a in v["authority"]))
    r = C.route("debt_collection", conduct_state="CA")
    ck("California conduct does NOT route to the Texas AG",
       "tx_ag" not in [v["venue"] for v in r["venues"]])
    ck("and the other state's law is named, not asserted",
       "does not hold CA's" in r["note"] and not any(
           v["authority"] for v in r["venues"] if v["venue"] == "state_ag_other"))
    r = C.route("telemarketing_calls")
    ck("no conduct state -> federal venues only, state left unresolved",
       not r["state_venue_resolved"] and "guessing it" in r["note"])
    try:
        C.route("vibes")
        ck("an unknown subject is refused", False)
    except C.Refused:
        ck("an unknown subject is refused", True)

    print("a venue that does not reach the subject is refused")
    t = C.open_tracker("t1")
    for venue, subj in (("fcc", "credit_reporting"), ("va_oig", "debt_collection"),
                        ("tx_occc", "housing")):
        try:
            C.file_complaint(t, venue, subj, "2026-09-16")
            ck(f"{venue} refuses {subj}", False)
        except C.Refused:
            ck(f"{venue} refuses {subj}", True)
    try:
        C.file_complaint(t, "not_a_venue", "debt_collection", "2026-09-16")
        ck("an unknown venue is refused", False)
    except C.Refused:
        ck("an unknown venue is refused", True)

    print("a complaint tolls nothing, and says so on every record")
    t = C.open_tracker("t2")
    C.file_complaint(t, "cfpb", "credit_reporting", "2026-06-20", reference="R1",
                     conduct_state="TX", claim_id="fcra-001", claim_deadline="2028-06-20")
    rec = t["complaints"][0]
    ck("the tolling field is explicit", rec["tolling"].startswith("NONE")
       and "2028-06-20" in rec["tolling"])
    C.file_complaint(t, "ftc", "credit_reporting", "2026-06-20")
    ck("with no claim deadline it says to diary one separately",
       "diary it separately" in t["complaints"][1]["tolling"])

    print("the response is the point, and only once it names an element")
    C.record_response(t, 1, "2026-07-05", "Company says it verified against its system "
                      "of record and produced no account-level documents.",
                      bears_on="4_investigation_reasonable", doc_id="resp-1")
    s = C.summary(t, today="2026-09-16")
    row = next(r for r in s["rows"] if r["n"] == 1)
    ck("a response with an element attaches to it",
       row["response"] and row["bears_on"] == "4_investigation_reasonable")
    ck("and is cited when a document backs it",
       t["complaints"][0]["response"]["evidence_state"] == "cited")
    C.record_response(t, 2, "2026-07-06", "Form acknowledgement, nothing substantive.")
    s = C.summary(t, today="2026-09-16")
    ck("a response naming no element is warned about, not counted as evidence",
       any("attached to no element" in w for w in s["warnings"]))
    ck("and without a document it is stated_by_principal, not cited",
       t["complaints"][1]["response"]["evidence_state"] == "stated_by_principal")
    try:
        C.record_response(t, 1, "2026-07-05", "   ")
        ck("an empty response summary is refused", False)
    except C.Refused:
        ck("an empty response summary is refused", True)

    print("the tracker warns on the clock and on silence")
    t = C.open_tracker("t3")
    C.file_complaint(t, "cfpb", "credit_reporting", "2026-01-01", claim_id="x",
                     claim_deadline="2026-11-01")
    s = C.summary(t, today="2026-09-16")
    ck("a near deadline is warned, with 'does not toll it'",
       any("does not toll it" in w for w in s["warnings"]))
    ck("a complaint open past 60 days with no response is warned",
       any("days open with no response" in w for w in s["warnings"]))
    ck("history is append-only and keeps the filing",
       t["complaints"][0]["history"][0]["status"] == "filed")

    print("the store")
    p = C.save(t)
    ck("a tracker file is written 0600", (os.stat(p).st_mode & 0o777) == 0o600)
    ck("and reads back", C.load("t3")["tracker_id"] == "t3")
    try:
        bad = C.open_tracker("t4")
        C.file_complaint(bad, "cfpb", "credit_reporting", "2026-09-16",
                         about="account SSN 123-45-6789")
        C.save(bad)
        ck("a complaint carrying an identifier is refused before it is written", False)
    except C.Refused as exc:
        ck("a complaint carrying an identifier is refused before it is written",
           "9-digit" in str(exc) or "ssn" in str(exc).lower())

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails[:6]))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
