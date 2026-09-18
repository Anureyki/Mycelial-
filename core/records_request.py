#!/usr/bin/env python3
"""Somebody says a record exists. Find who holds it and ask them.

    from core.records_request import route, open_request, record_response

THE PRINCIPAL'S INSTRUCTION, 2026-09-18, and it corrects something this
system was doing wrong: "don't label anything as a theory, frivolous,
courts have found it... prove it. Go through the steps to get it proven."

He is right, and the reason he is right is already written into this
codebase. `standing_comes_from_content` says never set a standing field
from a title or a channel - read the thing, or record unknown. Calling a
claim frivolous because of the genre it arrived in is exactly that error,
and I made it. A claim that a record exists is not answered by me
asserting it does not. It is answered by asking the custodian who would
have it, and recording what they say.

SO THIS MODULE NEVER CLASSIFIES A CLAIM. It does one thing: turns "there
is a record of X" into "Y holds records of that kind, here is the
mechanism that compels or requests it, here is the deadline, here is what
came back." The output is a request and, later, a response.

THE ANSWER LANDS IN ONE OF THE SEVEN ABSENCE STATES, which
core/evidence_ingest.py already defines and which exist precisely so that
"nobody looked" and "we looked and it is not there" stop being the same
word:

    not_checked   nobody has asked yet
    not_found     the custodian searched and says no responsive record
    not_read      it arrived and nobody has read it
    unreadable    it arrived and cannot be read
    unknown       the custodian has not answered
    conflicting   two custodians answer differently
    not_applicable  this custodian does not hold that kind of record

`not_found` REQUIRES A CUSTODIAN AND A DATE. "No such record exists" with
nobody's name on it is not a finding, it is an opinion - which is the
whole of the correction above.

WHAT IT WILL NOT DO. It drafts requests; it sends nothing. That boundary
is the same one every other lane here runs on and it is not about this
subject: a misdirected request in the principal's name is not
correctable, and CLAUDE.md refuses the sending tier structurally.
"""
import hashlib
import json
import os
import re
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "private", "records")

ABSENCE = ("not_checked", "not_found", "not_read", "unreadable", "unknown",
           "conflicting", "not_applicable")

# What kind of thing is being looked for. Deliberately descriptive, never
# evaluative: "is there a bond on this case" is a question a clerk can
# answer, and whether the asker's theory about bonds is sound is a
# different question this module does not reach.
SUBJECTS = ("agency_record", "record_about_me", "disclosure_accounting",
            "court_file", "security_or_cusip", "sec_filing", "bond_or_surety",
            "tax_record", "consumer_file", "account_accounting",
            "debt_validation", "benefit_record", "licensing_or_bond")

# Each mechanism: who it reaches, what it compels, the statutory clock
# where one exists, and the authority. A mechanism with no authority on
# the shelf says so rather than implying one.
MECHANISMS = {
    "foia": {
        "name": "Freedom of Information Act request",
        "custodian": "a FEDERAL agency",
        "reaches": ("agency_record", "bond_or_surety", "tax_record", "security_or_cusip"),
        "compels": "records the agency holds, subject to nine exemptions",
        "clock_days": 20, "clock_basis": "business days to determine, 5 U.S.C. 552(a)(6)(A)(i)",
        "authority": ["5 U.S.C. 552"],
        "note": ("An agency must search and must state the outcome. A 'no responsive "
                 "records' answer is a FINDING with a name and a date on it, which is "
                 "the thing an assertion can never be."),
    },
    "privacy_act_access": {
        "name": "Privacy Act access request",
        "custodian": "a FEDERAL agency, for records about YOU",
        "reaches": ("record_about_me", "benefit_record", "tax_record"),
        "compels": "your own record in a system of records, and a chance to correct it",
        "clock_days": None, "clock_basis": "agency regulations set the period",
        "authority": ["5 U.S.C. 552a"],
        "note": "Subsection (d) is the access right; (d)(2) is amendment.",
    },
    "disclosure_accounting": {
        "name": "Privacy Act accounting of disclosures",
        "custodian": "a FEDERAL agency",
        "reaches": ("disclosure_accounting", "record_about_me"),
        "compels": ("an accounting of each disclosure of your record - date, nature, "
                    "purpose, and to whom"),
        "clock_days": None, "clock_basis": "agency regulations",
        "authority": ["5 U.S.C. 552a"],
        "note": ("THE LEDGER QUESTION, in the form an agency must answer. 'Who has my "
                 "record been given to, and when' is 552a(c)(3), and it is available on "
                 "request. If the concern is disclosures nobody accounted for, this is "
                 "the mechanism that puts the question to the custodian."),
    },
    "state_pia": {
        "name": "Texas Public Information Act request",
        "custodian": "a TEXAS state or local governmental body",
        "reaches": ("agency_record", "court_file", "licensing_or_bond"),
        "compels": "public information the body holds",
        "clock_days": 10, "clock_basis": "business days to produce or seek an AG ruling",
        "authority": ["Tex. Gov't Code 552.021"],
        "note": "A body that withholds must generally seek an Attorney General decision.",
    },
    "court_clerk": {
        "name": "Records request to the clerk of court",
        "custodian": "the clerk where the case was filed",
        "reaches": ("court_file", "bond_or_surety"),
        "compels": ("the register of actions and any instrument actually filed - a bond, "
                    "an undertaking, a surety, if one exists on that docket"),
        "clock_days": None, "clock_basis": "local rule",
        "authority": [],
        "note": ("The register of actions either shows a bond or it does not, and either "
                 "answer is evidence. This is the cheapest check in this table."),
    },
    "cusip_lookup": {
        "name": "CUSIP issue lookup / CUSIP Global Services enquiry",
        "custodian": "CUSIP Global Services",
        "reaches": ("security_or_cusip",),
        "compels": "nothing - it is a commercial enquiry, not a legal demand",
        "clock_days": None, "clock_basis": None,
        "authority": [],
        "note": ("Assignment runs from an ISSUER registering an issue, so the enquiry "
                 "that settles a claim is 'is there an issue identified as X'. A "
                 "negative answer from CGS is a finding with a custodian on it."),
    },
    "sec_edgar": {
        "name": "EDGAR search / SEC FOIA",
        "custodian": "the Securities and Exchange Commission",
        "reaches": ("sec_filing", "security_or_cusip"),
        "compels": "filings are public; anything else goes through FOIA",
        "clock_days": 20, "clock_basis": "business days under FOIA",
        "authority": ["5 U.S.C. 552"],
        "note": "If a security was issued and registered, a filing exists or it does not.",
    },
    "treasury_fiscal": {
        "name": "Bureau of the Fiscal Service / Treasury FOIA",
        "custodian": "the U.S. Department of the Treasury",
        "reaches": ("bond_or_surety", "agency_record", "tax_record"),
        "compels": "records Treasury holds, subject to FOIA exemptions",
        "clock_days": 20, "clock_basis": "business days, 5 U.S.C. 552(a)(6)(A)(i)",
        "authority": ["5 U.S.C. 552"],
        "note": ("If an instrument or account is said to sit at Treasury, Treasury is the "
                 "custodian who can say so."),
    },
    "irs_transcript": {
        "name": "IRS transcript request (Form 4506-T) or IRS FOIA",
        "custodian": "the Internal Revenue Service",
        "reaches": ("tax_record", "record_about_me"),
        "compels": "your own return and account transcripts",
        "clock_days": None, "clock_basis": "processing time, not a statutory clock",
        "authority": ["5 U.S.C. 552", "5 U.S.C. 552a"],
        "note": ("An account transcript shows what the IRS has actually posted to an "
                 "account - which is the record, whatever anyone says is on it."),
    },
    "cra_file_disclosure": {
        "name": "Full file disclosure from a consumer reporting agency",
        "custodian": "Equifax, Experian, TransUnion, or a specialty CRA",
        "reaches": ("consumer_file",),
        "compels": ("ALL information in your file, the sources, and who it was furnished "
                    "to"),
        "clock_days": None, "clock_basis": "on request; annual file disclosure is free",
        "authority": ["15 U.S.C. 1681g", "15 U.S.C. 1681j"],
        "note": ("1681g(a)(3) is the one people miss: the identity of each person who "
                 "procured the report. That is a disclosure ledger the CRA must keep."),
    },
    "ucc_accounting": {
        "name": "Request for an accounting from a secured party",
        "custodian": "the secured party of record",
        "reaches": ("account_accounting", "bond_or_surety"),
        "compels": ("an authenticated record of the unpaid obligation, or a list of "
                    "collateral"),
        "clock_days": 14, "clock_basis": "days to comply, Tex. Bus. & Com. Code 9.210(b)",
        "authority": ["Tex. Bus. & Com. Code 9.210"],
        "note": ("A real statutory right to an accounting, running between an actual "
                 "debtor and an actual secured party under an actual security "
                 "agreement. Where those exist, the duty to answer does too."),
    },
    "fdcpa_validation": {
        "name": "Debt validation demand",
        "custodian": "the debt collector",
        "reaches": ("debt_validation", "account_accounting"),
        "compels": ("the amount, the current creditor, and the name and address of the "
                    "original creditor"),
        "clock_days": 30, "clock_basis": "days for the consumer to dispute after notice",
        "authority": ["15 U.S.C. 1692g", "12 CFR 1006.34"],
        "note": "Collection must cease on a timely written dispute until validation.",
    },
    "va_records": {
        "name": "VA records request",
        "custodian": "the Department of Veterans Affairs",
        "reaches": ("benefit_record", "record_about_me", "disclosure_accounting"),
        "compels": "your claims file and benefit records",
        "clock_days": None, "clock_basis": "agency processing",
        "authority": ["5 U.S.C. 552a", "38 U.S.C. 5701"],
        "note": "38 U.S.C. 5701 governs confidentiality of VA claimant records.",
    },
}


class Refused(ValueError):
    pass


def _iso(d, what):
    if d is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d)):
        raise Refused(f"{what}: date must be YYYY-MM-DD, got {d!r}")
    return str(d)


def route(subject, federal=None, state=None):
    """Who holds that kind of record, and by what mechanism. -> dict.

    Makes no judgement about whether the record exists. That is the point:
    the question 'does it exist' is answered by the custodian, and this
    returns the way to ask them."""
    if subject not in SUBJECTS:
        raise Refused(f"subject {subject!r} is not one of {SUBJECTS}")
    hits = []
    for key, m in MECHANISMS.items():
        if subject not in m["reaches"]:
            continue
        if federal is False and m["custodian"].startswith("a FEDERAL"):
            continue
        if state and state.upper() != "TX" and key == "state_pia":
            continue
        hits.append({"mechanism": key, **{k: m[k] for k in
                                          ("name", "custodian", "compels", "clock_days",
                                           "clock_basis", "authority", "note")}})
    return {
        "subject": subject, "mechanisms": hits,
        "none_found": not hits,
        "rule": ("A claim that a record exists is settled by the custodian who would "
                 "hold it, not by anyone's view of the claim. Ask, and record the "
                 "answer with their name and the date on it."),
    }


def open_request(request_id, subject, mechanism, custodian, seeking,
                 sent_on=None, about=None, reference=None):
    """Record a request that has been made. -> the request."""
    if subject not in SUBJECTS:
        raise Refused(f"subject {subject!r} is not one of {SUBJECTS}")
    if mechanism not in MECHANISMS:
        raise Refused(f"mechanism {mechanism!r} is not one of {sorted(MECHANISMS)}")
    if subject not in MECHANISMS[mechanism]["reaches"]:
        raise Refused(f"{MECHANISMS[mechanism]['name']} does not reach {subject!r}; "
                      f"it reaches {MECHANISMS[mechanism]['reaches']}")
    if not str(seeking or "").strip():
        raise Refused("say what is being sought, specifically enough that a custodian "
                      "could search for it. 'Everything' is not a request.")
    m = MECHANISMS[mechanism]
    sent = _iso(sent_on, "sent_on")
    due = None
    if sent and m.get("clock_days"):
        # Business days where the statute says business days - a 20-day FOIA
        # clock is not 20 calendar days and diarising it as one misses it.
        d, n = date.fromisoformat(sent), 0
        while n < m["clock_days"]:
            d += timedelta(days=1)
            if d.weekday() < 5:
                n += 1
        due = d.isoformat()
    return {
        "request_id": str(request_id), "subject": subject, "mechanism": mechanism,
        "mechanism_name": m["name"], "custodian": str(custodian),
        "seeking": str(seeking), "about": about, "reference": reference,
        "authority": m["authority"], "sent_on": sent,
        "response_due": due, "clock_basis": m.get("clock_basis"),
        "state": "not_checked" if not sent else "unknown",
        "response": None,
        "sends": False,
        "note": ("Drafted and recorded. Nothing here transmits a request - see "
                 "core/dispute_letters on why the sending tier is refused."),
    }


def record_response(req, state, on=None, custodian_said=None, doc_id=None,
                    produced=None):
    """What came back. -> the request, with the answer on it.

    `not_found` needs a custodian and a date, because that is the whole
    difference between a finding and an opinion."""
    if state not in ABSENCE:
        raise Refused(f"{state!r} is not an absence state: {list(ABSENCE)}")
    on = _iso(on, "response date")
    if state in ("not_found", "not_applicable"):
        if not (req.get("custodian") and on):
            raise Refused(
                f"{state!r} requires the custodian and the date they said it. "
                f"'No such record exists' with nobody's name on it is an opinion, "
                f"not a finding.")
        if not str(custodian_said or "").strip():
            raise Refused(f"{state!r} requires what the custodian actually said, in "
                          f"their words.")
    req["response"] = {
        "state": state, "on": on, "custodian_said": custodian_said,
        "doc_id": doc_id, "produced": produced or [],
        "evidence_state": "cited" if doc_id else "stated_by_principal",
        "means": {
            "not_found": ("The custodian searched and says there is no responsive "
                          "record. That is evidence about the record, sourced to them."),
            "not_checked": "Nobody has asked yet.",
            "unknown": "Asked; no answer yet.",
            "not_read": "It arrived and nobody has read it.",
            "unreadable": "It arrived and cannot be read.",
            "conflicting": "Two custodians answer differently. Both answers are kept.",
            "not_applicable": "This custodian does not hold that kind of record.",
        }[state],
    }
    req["state"] = state
    return req


def summary(requests, today=None):
    """What is outstanding, what came back, and what is overdue."""
    now = today or date.today().isoformat()
    rows, overdue = [], []
    for r in requests:
        row = {"request_id": r["request_id"], "subject": r["subject"],
               "custodian": r["custodian"], "mechanism": r["mechanism_name"],
               "sent_on": r.get("sent_on"), "due": r.get("response_due"),
               "state": r.get("state"),
               "answered": bool(r.get("response"))}
        if r.get("response_due") and not r.get("response") and now > r["response_due"]:
            row["overdue_days"] = (date.fromisoformat(now)
                                   - date.fromisoformat(r["response_due"])).days
            overdue.append(r["request_id"])
        rows.append(row)
    found = [r["request_id"] for r in requests
             if (r.get("response") or {}).get("state") == "not_found"]
    return {"rows": rows, "overdue": overdue, "answered_not_found": found,
            "open": [r["request_id"] for r in requests if not r.get("response")],
            "rule": ("An unanswered request is `unknown`, never `not_found`. Only a "
                     "custodian can say a record is not there."),
            "disclaimer": "A request register, not legal advice and not a filing."}


def _path(request_id):
    rid = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(request_id)).strip("_")
    if not rid:
        raise Refused("request_id is empty after sanitising")
    return os.path.join(STORE, rid + ".json")


def save(req):
    from core.fs_boundary import ensure_dir
    from core.asset_registry import guard_fields
    try:
        guard_fields({"seeking": str(req.get("seeking") or ""),
                      "about": str(req.get("about") or ""),
                      "reference": str(req.get("reference") or "")})
    except ValueError as exc:
        raise Refused(str(exc))
    ensure_dir(STORE, 0o700)
    p = _path(req["request_id"])
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(req, fh, indent=2, sort_keys=True)
    return p


def load(request_id):
    p = _path(request_id)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def load_all():
    if not os.path.isdir(STORE):
        return []
    out = []
    for f in sorted(os.listdir(STORE)):
        if f.endswith(".json"):
            try:
                with open(os.path.join(STORE, f), encoding="utf-8") as fh:
                    out.append(json.load(fh))
            except Exception:                      # noqa: BLE001
                continue
    return out
