#!/usr/bin/env python3
"""Regulator complaints: where one goes, and what comes back as evidence.

    from core.complaint_lane import route, file_complaint, record_response

WHAT A COMPLAINT IS, AND IS NOT. It is a regulator's intake. It is not a
claim, it does not create a private right, and - this is the one that costs
people cases - IT DOES NOT TOLL ANY LIMITATIONS PERIOD. The § 1681p and
§ 1658 clocks keep running while a complaint sits in a queue, so this
module carries the open claim's deadline on every complaint record and says
so out loud. A complaint filed six weeks before a clock dies is not
progress.

WHAT IT IS WORTH is the response. A company answering a regulator in
writing, on the record, explaining what it did and why, is the best
evidence most people will ever get for free - and it is exactly what the
furnisher sheet's element 4 (was the investigation reasonable) and the
TCPA lane's consent question need. So a response is not filed away: it is
attached to an open claim sheet as evidence, with the element it bears on.

ROUTING IS BY WHERE THE CONDUCT HAPPENED AND WHAT IT WAS, never by which
form is easiest to find. A California intake page is the wrong venue for
Texas conduct by a Texas business; the routing table below is keyed on
subject and on the two states that actually matter to this principal, and
anything else comes back as `venue_unknown` with what would settle it
rather than a guess.

AUTHORITY vs PROCESS. Where a venue rests on a statute this shelf can
open, the statute is named. Where the venue's behaviour is its own
published intake practice - response windows, portals - that is recorded
as `process_note` and marked NOT law, because an agency's service standard
is not a duty anyone can enforce and quoting it as one is the same error
as shelving the IRM beside the Code.
"""
import hashlib
import json
import os
import re
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "private", "complaints")

SUBJECTS = ("debt_collection", "credit_reporting", "telemarketing_calls",
            "bank_or_payments", "deceptive_practice", "housing", "va_benefits")

STATUSES = ("drafted", "filed", "acknowledged", "company_responded",
            "closed_no_action", "closed_resolved", "referred_out", "abandoned")

# Each venue: what it reaches, the authority it rests on where there is one,
# and its own published intake practice marked as NOT law.
VENUES = {
    "cfpb": {
        "name": "Consumer Financial Protection Bureau",
        "reaches": ("debt_collection", "credit_reporting", "bank_or_payments"),
        "authority": ["12 U.S.C. 5531", "15 U.S.C. 1692", "15 U.S.C. 1681"],
        "process_note": ("The Bureau forwards the complaint to the company and publishes a "
                         "response window in its own materials. That window is the agency's "
                         "service standard, NOT a statutory duty - nothing here treats a "
                         "missed one as a violation."),
        "why_it_is_worth_filing": ("The company answers in writing, on the record, and that "
                                   "answer is evidence for the element it addresses."),
        "geography": "federal"},
    "ftc": {
        "name": "Federal Trade Commission (ReportFraud / IdentityTheft.gov)",
        "reaches": ("deceptive_practice", "credit_reporting", "telemarketing_calls"),
        "authority": ["15 U.S.C. 45"],
        "process_note": ("The FTC does not resolve individual complaints. For identity "
                         "theft its report IS the document 15 U.S.C. 1681c-2 requires to "
                         "trigger a block - that is the reason to file, not a hope of "
                         "enforcement."),
        "why_it_is_worth_filing": ("An FTC identity theft report is a statutory prerequisite "
                                   "to the 4-business-day block under 1681c-2."),
        "geography": "federal"},
    "fcc": {
        "name": "Federal Communications Commission",
        "reaches": ("telemarketing_calls",),
        "authority": ["47 U.S.C. 227", "47 CFR 64.1200"],
        "process_note": ("The FCC enforces; the forfeitures it imposes are the government's "
                         "money and never the complainant's. Filing does not create or "
                         "preserve a private 227(b)(3) claim."),
        "why_it_is_worth_filing": "It puts the call pattern on a federal record.",
        "geography": "federal"},
    "tx_ag": {
        "name": "Texas Attorney General, Consumer Protection Division",
        "reaches": ("debt_collection", "deceptive_practice", "credit_reporting",
                    "bank_or_payments", "telemarketing_calls"),
        "authority": ["Tex. Bus. & Com. Code 17.46", "Tex. Bus. & Com. Code 17.50",
                      "Tex. Fin. Code 392.301", "Tex. Fin. Code 392.403"],
        "process_note": ("The Division mediates and may sue in the State's name. It does "
                         "not represent the complainant."),
        "why_it_is_worth_filing": ("Tex. Fin. Code ch. 392 reaches ORIGINAL CREDITORS "
                                   "collecting their own debts, which the FDCPA generally "
                                   "does not after Henson - and 392.403 carries a private "
                                   "right the complaint record can support."),
        "geography": "TX"},
    "tx_occc": {
        "name": "Texas Office of Consumer Credit Commissioner",
        "reaches": ("debt_collection", "bank_or_payments"),
        "authority": ["Tex. Fin. Code 392.101"],
        "process_note": ("Licenses and examines; third-party debt collectors operating in "
                         "Texas must file a bond with the Secretary of State."),
        "why_it_is_worth_filing": "It reaches the licence and the bond, which a lawsuit does not.",
        "geography": "TX"},
    "state_ag_other": {
        "name": "The attorney general of the state where the conduct occurred",
        "reaches": SUBJECTS,
        "authority": [],
        "process_note": ("Each state's consumer statute differs. Nothing here asserts what "
                         "another state's law provides - that is a corpus this shelf does "
                         "not hold."),
        "why_it_is_worth_filing": "Conduct is answerable where it happened.",
        "geography": "other"},
    "va_oig": {
        "name": "VA Office of Inspector General",
        "reaches": ("va_benefits",),
        "authority": ["38 U.S.C. 5301"],
        "process_note": "Investigates; it does not adjudicate a benefit claim.",
        "why_it_is_worth_filing": "Misuse of benefit funds by a fiduciary or a program is its subject.",
        "geography": "federal"},
}


class Refused(ValueError):
    pass


def _iso(d, what):
    if d is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d)):
        raise Refused(f"{what}: date must be YYYY-MM-DD, got {d!r}")
    return str(d)


def route(subject, conduct_state=None, business_state=None):
    """Where this goes. -> {venues, why, refused}. Never guesses a venue.

    Conduct location governs. A complaint filed with the attorney general of
    a state that has nothing to do with the conduct is an intake that goes
    nowhere, and finding that form first is not a reason."""
    if subject not in SUBJECTS:
        raise Refused(f"subject {subject!r} is not one of {SUBJECTS}")
    where = (conduct_state or business_state or "").strip().upper() or None
    federal = [k for k, v in VENUES.items()
               if v["geography"] == "federal" and subject in v["reaches"]]
    state, note = [], None
    if where == "TX":
        state = [k for k, v in VENUES.items()
                 if v["geography"] == "TX" and subject in v["reaches"]]
    elif where:
        state = ["state_ag_other"]
        note = (f"The conduct is in {where}. This shelf holds Texas consumer law and "
                f"federal law; it does not hold {where}'s, so the state venue is named "
                f"and its law is NOT asserted.")
    else:
        note = ("No conduct state given. Federal venues are named; the state venue is "
                "not, because it follows where the conduct happened and guessing it "
                "sends the complaint nowhere.")
    return {
        "subject": subject, "conduct_state": conduct_state, "business_state": business_state,
        "venues": [{"venue": k, **{kk: VENUES[k][kk] for kk in
                                   ("name", "authority", "process_note",
                                    "why_it_is_worth_filing")}}
                   for k in federal + state],
        "state_venue_resolved": bool(state),
        "note": note,
        "rule": ("Conduct location governs. A complaint is a regulator's intake, not a "
                 "claim, and it tolls nothing."),
    }


def file_complaint(tracker, venue, subject, filed_on, reference=None, about=None,
                   conduct_state=None, claim_id=None, claim_deadline=None):
    """Record one filing. -> the tracker."""
    if venue not in VENUES:
        raise Refused(f"venue {venue!r} is not one of {sorted(VENUES)}")
    if subject not in SUBJECTS:
        raise Refused(f"subject {subject!r} is not one of {SUBJECTS}")
    if subject not in VENUES[venue]["reaches"] and venue != "state_ag_other":
        raise Refused(f"{VENUES[venue]['name']} does not reach {subject!r}. "
                      f"It reaches {VENUES[venue]['reaches']}.")
    rec = {
        "n": len(tracker["complaints"]) + 1,
        "venue": venue, "venue_name": VENUES[venue]["name"],
        "subject": subject, "filed_on": _iso(filed_on, "filed_on"),
        "reference": reference, "about": about, "conduct_state": conduct_state,
        "status": "filed", "history": [{"on": _iso(filed_on, "filed_on"), "status": "filed"}],
        "response": None,
        "linked_claim": claim_id,
        "claim_deadline": _iso(claim_deadline, "claim_deadline"),
        "tolling": ("NONE. This complaint does not pause any limitations period. "
                    + (f"The linked claim's deadline remains {claim_deadline}."
                       if claim_deadline else
                       "No claim deadline is on this record - diary it separately.")),
    }
    tracker["complaints"].append(rec)
    return tracker


def advance(tracker, n, status, on, note=""):
    """Move one complaint along. The history is append-only."""
    if status not in STATUSES:
        raise Refused(f"status {status!r} is not one of {STATUSES}")
    rec = next((c for c in tracker["complaints"] if c["n"] == n), None)
    if rec is None:
        raise Refused(f"no complaint {n} on this tracker")
    rec["status"] = status
    rec["history"].append({"on": _iso(on, "status date"), "status": status,
                           "note": str(note)})
    return tracker


def record_response(tracker, n, received_on, summary, bears_on=None, claim_id=None,
                    doc_id=None):
    """The response, and the element it bears on. THIS is the point.

    `bears_on` names an element of an open claim sheet - the response is
    evidence, and evidence attaches to an element or it is just paper."""
    rec = next((c for c in tracker["complaints"] if c["n"] == n), None)
    if rec is None:
        raise Refused(f"no complaint {n} on this tracker")
    if not str(summary or "").strip():
        raise Refused("a response needs a summary of what the company actually said")
    rec["response"] = {
        "received_on": _iso(received_on, "received_on"), "summary": str(summary),
        "doc_id": doc_id, "bears_on": bears_on,
        "claim_id": claim_id or rec.get("linked_claim"),
        "evidence_state": "cited" if doc_id else "stated_by_principal",
        "note": ("Attach this to the claim sheet with its element. A company's written "
                 "account of what it did is evidence about what it did." if bears_on else
                 "No element named. It is on the record and bears on nothing yet - say "
                 "which element it answers."),
    }
    rec["status"] = "company_responded"
    rec["history"].append({"on": _iso(received_on, "received_on"),
                           "status": "company_responded"})
    return tracker


def open_tracker(tracker_id, principal=None):
    if not tracker_id:
        raise Refused("a tracker needs an id")
    return {"tracker_id": str(tracker_id), "principal": principal, "complaints": []}


def summary(tracker, today=None):
    """What is open, what came back, and what clock is running out."""
    now = today or date.today().isoformat()
    rows, warnings = [], []
    for c in tracker["complaints"]:
        age = None
        if c.get("filed_on"):
            age = (date.fromisoformat(now) - date.fromisoformat(c["filed_on"])).days
        row = {"n": c["n"], "venue": c["venue_name"], "subject": c["subject"],
               "filed_on": c["filed_on"], "days_open": age, "status": c["status"],
               "reference": c.get("reference"),
               "response": bool(c.get("response")),
               "bears_on": (c.get("response") or {}).get("bears_on")}
        if c.get("claim_deadline"):
            left = (date.fromisoformat(c["claim_deadline"]) - date.fromisoformat(now)).days
            row["claim_deadline"] = c["claim_deadline"]
            row["claim_days_left"] = left
            if left < 180:
                warnings.append(
                    f"complaint {c['n']}: the linked claim's deadline is {c['claim_deadline']}, "
                    f"{left} days away, and this complaint does not toll it")
        if c["status"] in ("filed", "acknowledged") and age is not None and age > 60:
            warnings.append(f"complaint {c['n']}: {age} days open with no response recorded")
        if c.get("response") and not (c["response"].get("bears_on")):
            warnings.append(f"complaint {c['n']}: a response is on file and attached to no "
                            f"element - it is evidence only once it names one")
        rows.append(row)
    out = {"tracker_id": tracker["tracker_id"], "today": now, "rows": rows,
           "open": sum(1 for r in rows if r["status"] in ("drafted", "filed", "acknowledged")),
           "responded": sum(1 for r in rows if r["response"]),
           "warnings": warnings,
           "rule": ("A complaint is a regulator's intake, not a claim, and it tolls "
                    "nothing. Its value is the response, once that response is attached "
                    "to an element."),
           "disclaimer": "A tracker, not legal advice and not a filing."}
    out["sha256"] = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return out


def _path(tracker_id):
    tid = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(tracker_id)).strip("_")
    if not tid:
        raise Refused("tracker_id is empty after sanitising")
    return os.path.join(STORE, tid + ".json")


def save(tracker):
    from core.fs_boundary import ensure_dir
    from core.asset_registry import guard_fields
    flat = {f"complaint.{i}": json.dumps(c, sort_keys=True)
            for i, c in enumerate(tracker.get("complaints") or [])}
    try:
        guard_fields(flat)
    except ValueError as exc:
        raise Refused(str(exc))
    ensure_dir(STORE, 0o700)
    p = _path(tracker["tracker_id"])
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(tracker, fh, indent=2, sort_keys=True)
    return p


def load(tracker_id):
    p = _path(tracker_id)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)
