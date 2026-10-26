#!/usr/bin/env python3
"""Build gate for ssa_form_demand: the principal's doctrine is enforced, not described.

Runs against a throwaway ledger (SSA_DEMAND_DIR), never the real one under
state/legal/ssa_form_demand/. Synthetic recipients only - "Example Bank" and
friends - so nothing here is a record about anyone.
"""
import os
import stat
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["SSA_DEMAND_DIR"] = tempfile.mkdtemp(prefix="ssa_demand_check_")
os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"

import core.ssa_form_demand as sfd  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return False
    except sfd.Refused:
        return True


# A stub shelf: every cite the template uses opens; overlay sections open under
# the right work titles.
SHELF = {"15 U.S.C. 1681b": "15 U.S.C. § 1681b", "42 U.S.C. 405b": "42 U.S.C. § 405b",
         "5 U.S.C. 552a": "5 U.S.C. § 552a",
         "§ 501.001": "Tex. Bus. & Com. Code Chapter 501 - Protection of SSNs",
         "§ 501.052": "Tex. Bus. & Com. Code Chapter 501 - Protection of SSNs",
         "§ 521.051": "Tex. Bus. & Com. Code Chapter 521",
         "§ 59.1-443.2": "Va. Code Ann. § 59.1-443.2"}


def stub(cite):
    t = SHELF.get(cite)
    return [{"title": t, "text": f"text of {cite}"}] if t else []


REQ = {"name": "Requester Example", "mailing_address": "PO Box 1, Example TX 00000"}
EV = [{"date": "2026-01-02", "context": "account application"}]

print("ssa_form_demand gate")
cyc = sfd.new_cycle("gate")["id"]

check("rule 6: a financial institution is refused until eCBSV applicability is stated",
      refused(sfd.draft, cyc, {"name": "Example Bank", "type": "financial_institution",
                               "state": "TX", "collection_events": EV}, REQ, stub))

d_fi = sfd.draft(cyc, {"name": "Example Bank", "type": "financial_institution", "state": "TX",
                       "collection_events": EV,
                       "ecbsv": {"applies": True, "consent_date": "2023-06-01"}},
                 REQ, stub, today="2026-10-26")
w = d_fi["ecbsv"]
check("rule 6: retention window computed against BOTH the statute and the instruction",
      w["statutory"]["years"] == 2 and w["instructed"]["years"] == 5
      and w["statutory"]["open"] is False and w["instructed"]["open"] is True, str(w))

d_fed = sfd.draft(cyc, {"name": "Example Agency", "type": "federal_agency", "state": "VA",
                        "collection_events": EV}, REQ, stub)
check("rule 5: a federal agency goes to the routine-use track",
      "federal_routine_use_track" in d_fed["flags"])

check("rule 1: SSA-521 never appears in a demand",
      refused(sfd.draft, cyc, {"name": "Example Co", "type": "private_entity", "state": "TX",
                               "collection_events": [{"context": "they sent SSA-521"}]}, REQ, stub))

check("held out: 15 U.S.C. 1681s-2 is in no drafted demand",
      all("1681s-2" not in d["text"] for d in (d_fi, d_fed)))

check("requirement One: the demand names the requester",
      refused(sfd.draft, cyc, {"name": "X", "type": "private_entity", "collection_events": EV},
              {"name": "", "mailing_address": ""}, stub)
      and "Requester Example" in d_fi["text"])

check("overlay: Texas 501.052 privacy-policy item is demanded",
      "privacy policy" in d_fi["text"].lower())

d_ca = sfd.draft(cyc, {"name": "Example CA Co", "type": "private_entity", "state": "CA",
                       "collection_events": EV}, REQ, stub)
check("overlay: California is listed as not on shelf, never asserted",
      d_ca["overlay"] and d_ca["overlay"][0]["opened"] is False
      and "1798.85" not in d_ca["text"])

check("citations: a cite the shelf cannot open is listed as unopened",
      "15 U.S.C. 1681b" in sfd.draft(cyc, {"name": "Y", "type": "private_entity",
                                            "collection_events": EV}, REQ, lambda c: [])
      ["unopened_citations"])

check("rule 7: no demand is marked sent without the cycle's written authorization",
      refused(sfd.record_sent, d_fi["id"], "2026-08-01", "certified mail"))
sfd.authorize_cycle(cyc, "signed note, gate", "2026-07-30")
sfd.record_sent(d_fi["id"], "2026-08-01", "certified mail")
sfd.record_sent(d_fed["id"], "2026-10-20", "certified mail")
sfd.record_sent(d_ca["id"], "2026-10-20", "certified mail")

st = {r["id"]: r for r in sfd.status(today="2026-10-26")["demands"]}
check("requirement Three: silence past 30 days escalates",
      any(e.startswith("ignored_") for e in st[d_fi["id"]]["escalation"]))
check("requirement Three: silence inside 30 days does not",
      st[d_fed["id"]]["escalation"] == [])

sfd.record_response(d_fed["id"], "2026-10-25", "declined", form_produced=False, refused=True)
sfd.record_response(d_ca["id"], "2026-10-25", "produced a form", form_produced=True,
                    stated_purpose="account opening", actual_use="marketing list")
st = {r["id"]: r for r in sfd.status(today="2026-10-26")["demands"]}
check("requirement Three: a refusal escalates", "refused" in st[d_fed["id"]]["escalation"])
check("requirement Three: a stated purpose that does not match the use escalates",
      "purpose_mismatch" in st[d_ca["id"]]["escalation"])
co = st[d_ca["id"]].get("complaint_outline") or {}
check("requirement Four: a mismatch drafts an FCRA 1681s complaint outline, filed nowhere",
      co.get("authority", "").startswith("15 U.S.C. 1681s") and co.get("status", "").startswith("draft")
      and "cfpb" in str(co.get("venues")).lower(), str(co)[:200])

check("a response to an unsent demand is refused",
      refused(sfd.record_response,
              sfd.draft(cyc, {"name": "Z", "type": "private_entity", "collection_events": EV},
                        REQ, stub)["id"], "2026-10-25", "x", form_produced=False))

rep = sfd.cycle_report(cyc, today="2026-10-26")
md = rep["markdown"]
check("requirement Six: one markdown file per cycle with the summary table at the top",
      md.index("| Recipient | Form demanded | Produced | Mismatch | Escalation |") < md.index("## Doctrine")
      and "| Example Bank |" in md)
check("the cycle file is owner-only",
      stat.S_IMODE(os.stat(rep["path"]).st_mode) == 0o600)
check("doctrine travels with every report, verbatim",
      all(x["text"] in md for x in sfd._cfg()["doctrine"]))

# Against Legal's REAL shelf: the Texas overlay must quote the SSN chapter, never
# the Transportation Code's own section 501.001 (certificate of title).
try:
    from agents.legal_agent.legal_agent import LegalAgent
    L = object.__new__(LegalAgent)
    L.agent_id = "legal_agent"
    L.log = lambda *a, **k: None
    ov = sfd._overlay("TX", L.lookup_reference)
    first = ov[0]
    check("real shelf: Texas 501.001 resolves to Bus. & Com. Code, not the Transportation Code",
          first["opened"] and "Bus. & Com." in (first.get("work") or ""), str(first)[:160])
    check("real shelf: 42 U.S.C. 405b opens", sfd._resolve(L.lookup_reference, "42 U.S.C. 405b")[0])
except Exception as exc:
    check("real shelf reachable", False, f"{type(exc).__name__}: {exc}")

print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'}")
sys.exit(1 if FAILS else 0)
