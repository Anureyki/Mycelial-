#!/usr/bin/env python3
"""One lane, one object: the FCRA furnisher claim under 15 U.S.C. § 1681s-2(b).

    from core.fcra_furnisher import open_claim, add_evidence, assess

THE LANE IS LOCKED ON PURPOSE. Validation (§ 1692g) and the TCPA log stay
dark until this object can open, cite, and REFUSE on a real fact pattern.
A sheet that cannot refuse is a form, and a form fills itself.

WHAT MAKES THIS DIFFERENT FROM A CHECKLIST. Three things block rather than
annotate, and each traces to a holding on this shelf:

  § 1681s-2(a) HAS NO PRIVATE RIGHT OF ACTION. "They reported it wrong" is
  not this lane. Without a dispute that reached the furnisher THROUGH a
  consumer reporting agency - or a qualifying direct dispute under
  12 C.F.R. § 1022.43 - the claim does not belong here at all, and the
  honest answer is `use_1681e_b_or_1681i` or `no_claim`. Refusing the lane
  is a finding; filing it and losing is not.

  STANDING IS BLOCKING, AFTER RAMIREZ. TransUnion LLC v. Ramirez, 594 U.S.
  413 (2021): inaccurate data sitting in an internal file, never published,
  is usually not a concrete injury in federal court. So element 8 must name
  a real recipient - a lender, landlord, employer, insurer or collector who
  received the report - and "it sat at Experian" fails it. The failure is
  reported as `failed`, never smoothed into `unknown`.

  WILLFULNESS IS SAFECO, NOT AN ADJECTIVE. Safeco Ins. Co. v. Burr, 551
  U.S. 47 (2007): reckless disregard of a risk known or obvious. The usual
  theory is a rubber-stamped "verified" with no account-level records, and
  it is recorded as a THEORY with the evidence that would carry it, not as
  a conclusion.

BINDING IS NOT THE SAME AS PERSUASIVE. The operating jurisdiction is Texas,
so the Fifth Circuit binds and the Ninth does not. Gorman v. Wolpoff &
Abramson, 584 F.3d 1147 (9th Cir. 2009) is the standard case on what a
reasonable investigation requires and it is PERSUASIVE here; a sheet that
listed it beside a Fifth Circuit case without saying so would be teaching
the principal to cite it as though it governed in San Antonio.

NOTHING HERE IS ADVICE OR A FILING. It is an evidence sheet that says what
is proved, what is not, and what would close each gap.
"""
import hashlib
import json
import os
import re
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "private", "claims")

LANE = "fcra_furnisher_1681s2b"

# The elements, in the order they must happen. A later one cannot be proved
# while an earlier one is not: the duty does not exist until the CRA has
# notified the furnisher, which is the whole of subsection (b).
ELEMENTS = (
    ("1_cra_received_dispute", "The consumer disputed to a consumer reporting agency"),
    ("2_cra_notified_furnisher", "The CRA sent the dispute to the furnisher (§ 1681i(a)(2))"),
    ("3_furnisher_duty_triggered", "The furnisher's § 1681s-2(b) duties attached on that notice"),
    ("4_investigation_reasonable", "The furnisher's investigation was reasonable"),
    ("5_result_reported_back", "The furnisher reported its result to the CRA"),
    ("6_inaccuracy_or_incompleteness", "The information was inaccurate or incomplete"),
    ("7_willful_or_negligent", "Willful (§ 1681n, Safeco) or negligent (§ 1681o)"),
    ("8_publication_to_third_party", "A third party received the report (Ramirez)"),
    ("9_remedy", "Actual damages, statutory damages, or fees"),
)
ELEMENT_IDS = tuple(e[0] for e in ELEMENTS)

# Element 4 is the one a defendant wins on, so its default is NOT "proved".
STATES = ("unknown", "proved", "failed", "not_applicable")
BLOCKING = ("standing_publication", "bureau_notice_to_furnisher")

# Ramirez: who counts as publication. A recipient outside this set is not
# refused outright - it is reported as unrecognised, because the list is
# the usual cases and not the statute.
PUBLICATION_RECIPIENTS = ("lender", "landlord", "employer", "insurer", "collector",
                          "creditor", "bank", "mortgage_servicer", "tenant_screener",
                          "background_screener")
NOT_PUBLICATION = re.compile(
    r"\b(internal|never sent|not sent|sat at|only .*(?:file|bureau)|no one|nobody)\b", re.I)

KILLER_DEFENSES = ("no_1681s2a_private_right", "no_bureau_notice", "accuracy",
                   "no_standing", "arbitration", "sol")

# § 1681p: not later than the EARLIER of two years from discovery, or five
# years from the violation. Both are computed; the earlier governs.
SOL_DISCOVERY_YEARS = 2
SOL_OUTER_YEARS = 5

CIRCUITS = {"5th": "binding in Texas", "9th": "persuasive only in Texas",
            "11th": "persuasive only in Texas", "scotus": "binding everywhere"}


class Refused(ValueError):
    pass


def _iso(d, what):
    if d is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d)):
        raise Refused(f"{what}: date must be YYYY-MM-DD, got {d!r}")
    return str(d)


def _years(d, n):
    y, m, dd = (int(x) for x in d.split("-"))
    try:
        return date(y + n, m, dd).isoformat()
    except ValueError:                       # 29 Feb
        return (date(y + n, m, 28) + timedelta(days=1)).isoformat()


def limitations(violation_date=None, discovery_date=None, today=None):
    """§ 1681p, both clocks. -> dict. Unknown dates are said, never assumed."""
    out = {"authority": "15 U.S.C. § 1681p",
           "rule": ("the EARLIER of two years after the date of discovery of the "
                    "violation, or five years after the date on which the violation "
                    "occurred"),
           "violation_date": violation_date, "discovery_date": discovery_date}
    if not violation_date and not discovery_date:
        out.update({"deadline": None, "state": "unknown",
                    "why": "Neither the violation date nor the discovery date is on the "
                           "sheet, so no clock can be run. Diary it."})
        return out
    d2 = _years(discovery_date, SOL_DISCOVERY_YEARS) if discovery_date else None
    d5 = _years(violation_date, SOL_OUTER_YEARS) if violation_date else None
    cands = [d for d in (d2, d5) if d]
    deadline = min(cands)
    out["two_years_from_discovery"] = d2
    out["five_years_from_violation"] = d5
    out["deadline"] = deadline
    out["governed_by"] = ("discovery" if deadline == d2 else "outer")
    if not (d2 and d5):
        out["incomplete"] = ("Only one clock could be run; the other date is missing, "
                             "and the statute takes the earlier of the two.")
    now = today or date.today().isoformat()
    out["today"] = now
    out["state"] = "expired" if now > deadline else "live"
    out["days_left"] = (date.fromisoformat(deadline) - date.fromisoformat(now)).days
    return out


def open_claim(claim_id, caption, circuit="5th", **kw):
    """A blank sheet, every element unknown. -> the claim dict."""
    if not claim_id or not str(caption or "").strip():
        raise Refused("a claim needs a claim_id and a caption")
    if circuit not in CIRCUITS:
        raise Refused(f"circuit {circuit!r} not one of {sorted(CIRCUITS)}")
    return {
        "claim_id": str(claim_id), "lane": LANE, "caption": str(caption),
        "statute": "15 U.S.C. § 1681s-2(b)",
        "reg": kw.get("reg"),          # 12 C.F.R. § 1022.43, only if that path is used
        "circuit": circuit, "circuit_note": CIRCUITS[circuit],
        "parties": kw.get("parties") or {},
        "sol_start": _iso(kw.get("sol_start"), "sol_start"),
        "violation_date": _iso(kw.get("violation_date"), "violation_date"),
        "discovery_date": _iso(kw.get("discovery_date"), "discovery_date"),
        "blocking": {b: "unknown" for b in BLOCKING},
        "elements": {e: {"state": "unknown", "note": "", "what_would_close_it": None}
                     for e in ELEMENT_IDS},
        "evidence": [],
        "killer_defenses": [],
        "willfulness_theory": None,
        "authorities_cited": [],
        "status": "open",
    }


def add_evidence(claim, doc_id, date_, what_it_proves, element, proves=True):
    """Attach one document to one element. An element moves only on evidence."""
    if element not in ELEMENT_IDS:
        raise Refused(f"element {element!r} is not one of {ELEMENT_IDS}")
    if not doc_id or not str(what_it_proves or "").strip():
        raise Refused("evidence needs a doc_id and what it proves")
    claim["evidence"].append({"doc_id": str(doc_id), "date": _iso(date_, "evidence date"),
                              "what_it_proves": str(what_it_proves), "element": element,
                              "proves": bool(proves)})
    claim["elements"][element]["state"] = "proved" if proves else "failed"
    claim["elements"][element]["note"] = str(what_it_proves)
    return claim


def set_publication(claim, recipient=None, recipient_kind=None, doc_id=None, date_=None):
    """Element 8 and the standing block, together - they are one question."""
    said = f"{recipient or ''} {recipient_kind or ''}".strip()
    if not said or NOT_PUBLICATION.search(said):
        claim["blocking"]["standing_publication"] = "failed"
        claim["elements"]["8_publication_to_third_party"].update({
            "state": "failed",
            "note": (f"No third party named{': ' + said if said else ''}."),
            "what_would_close_it": ("Name who received the report - a lender, landlord, "
                                    "employer, insurer or collector - and the document "
                                    "that shows it. After Ramirez, data that never left "
                                    "the file is usually not a concrete injury in "
                                    "federal court.")})
        return claim
    kind = str(recipient_kind or "").strip().lower().replace(" ", "_")
    recognised = kind in PUBLICATION_RECIPIENTS
    claim["blocking"]["standing_publication"] = "proved" if doc_id and recognised else "unknown"
    claim["elements"]["8_publication_to_third_party"].update({
        "state": "proved" if doc_id and recognised else "unknown",
        "note": f"{recipient or 'a third party'} ({kind or 'kind not stated'})"
                + (f", evidenced by {doc_id}" if doc_id else ", no document attached"),
        "what_would_close_it": None if (doc_id and recognised) else (
            "Attach the document that shows the report was received."
            if recognised else
            f"{kind!r} is not one of the usual recipients {PUBLICATION_RECIPIENTS}. "
            f"Not refused - the list is the usual cases, not the statute - but it "
            f"needs reading against Ramirez before it is called proved.")})
    if doc_id:
        claim["evidence"].append({"doc_id": str(doc_id), "date": _iso(date_, "publication date"),
                                  "what_it_proves": f"report received by {recipient}",
                                  "element": "8_publication_to_third_party", "proves": True})
    return claim


def set_bureau_notice(claim, proved, doc_id=None, date_=None, note=""):
    """The other block: did the CRA actually send the dispute to the furnisher?

    This is the whole of subsection (b). Without it there is no duty to
    breach, and what is left is § 1681s-2(a), which no consumer may sue on."""
    claim["blocking"]["bureau_notice_to_furnisher"] = "proved" if proved else (
        "failed" if proved is False else "unknown")
    st = "proved" if proved else ("failed" if proved is False else "unknown")
    for e in ("2_cra_notified_furnisher", "3_furnisher_duty_triggered"):
        claim["elements"][e]["state"] = st
        claim["elements"][e]["note"] = note or ("ACDV / CRA results letter" if proved else "")
        claim["elements"][e]["what_would_close_it"] = None if proved else (
            "The CRA's results letter or the ACDV trail showing the dispute reached "
            "the furnisher. Until that exists the duty never attached.")
    if doc_id and proved:
        claim["evidence"].append({"doc_id": str(doc_id), "date": _iso(date_, "notice date"),
                                  "what_it_proves": "CRA transmitted the dispute to the furnisher",
                                  "element": "2_cra_notified_furnisher", "proves": True})
    return claim


def set_willfulness(claim, theory, evidence_needed=None):
    """Safeco, stated as a theory with what would carry it."""
    claim["willfulness_theory"] = {
        "standard": ("reckless disregard of a risk known or obvious - Safeco Ins. Co. v. "
                     "Burr, 551 U.S. 47 (2007)"),
        "theory": str(theory),
        "evidence_needed": evidence_needed or [
            "the furnisher's account-level records, or their absence",
            "the ACDV response showing what was actually checked",
            "the furnisher's own dispute-handling procedure, and the deviation from it"],
        "state": "asserted_not_proved"}
    return claim


def assess(claim, today=None):
    """-> the claim with status, defenses, gaps and the reason for each.

    `cite_ready` is the only status that says a citation may assert the
    element is proved, and it is withheld unless BOTH blocks are proved,
    every element is proved or not_applicable, and the clock is live."""
    c = json.loads(json.dumps(claim))          # never mutate the caller's sheet
    defenses, gaps = [], []

    notice = c["blocking"]["bureau_notice_to_furnisher"]
    pub = c["blocking"]["standing_publication"]

    # The lane test comes first: is this a § 1681s-2(b) claim at all?
    direct = str(c.get("reg") or "")
    if notice == "failed" and "1022.43" not in direct:
        defenses.append("no_1681s2a_private_right")
        defenses.append("no_bureau_notice")
        c["status"] = "contested"
        c["lane_refused"] = {
            "verdict": "use_1681e_b_or_1681i_or_no_claim",
            "why": ("No dispute reached the furnisher through a consumer reporting "
                    "agency, and no qualifying direct dispute under 12 C.F.R. § 1022.43 "
                    "is recorded. § 1681s-2(b) duties never attached. What is left is "
                    "§ 1681s-2(a), which carries NO private right of action - so this "
                    "fact pattern is not this lane."),
            "where_it_might_live": ["15 U.S.C. § 1681e(b) against the CRA "
                                    "(reasonable procedures for maximum possible accuracy)",
                                    "15 U.S.C. § 1681i against the CRA (reinvestigation)"]}
    elif notice != "proved":
        gaps.append("bureau_notice_to_furnisher is not proved - the duty may never have attached")

    if pub == "failed":
        defenses.append("no_standing")
        gaps.append("publication failed: after Ramirez this usually ends a federal case "
                    "before damages are discussed")
    elif pub != "proved":
        gaps.append("standing_publication is not proved - name who received the report")

    sol = limitations(c.get("violation_date"), c.get("discovery_date"), today=today)
    c["limitations"] = sol
    if sol.get("state") == "expired":
        defenses.append("sol")
        gaps.append(f"the § 1681p clock ran out on {sol['deadline']}")
    elif sol.get("state") == "unknown":
        gaps.append("no § 1681p clock can be run - neither date is on the sheet")

    if c["elements"]["6_inaccuracy_or_incompleteness"]["state"] == "failed":
        defenses.append("accuracy")

    unproved = [e for e in ELEMENT_IDS
                if c["elements"][e]["state"] not in ("proved", "not_applicable")]
    for e in unproved:
        if not c["elements"][e].get("what_would_close_it"):
            c["elements"][e]["what_would_close_it"] = _CLOSERS.get(e)
    c["elements_unproved"] = unproved
    c["killer_defenses"] = sorted(set(c.get("killer_defenses", [])) | set(defenses))
    c["gaps"] = gaps

    if c.get("lane_refused"):
        pass                                   # status already contested
    elif unproved or defenses or notice != "proved" or pub != "proved":
        c["status"] = "contested"
    else:
        c["status"] = "cite_ready"
    c["cite_ready"] = c["status"] == "cite_ready"
    c["what_status_means"] = {
        "open": "a blank sheet",
        "contested": ("something material is unproved or a defense is live. No citation "
                      "from this sheet may assert an element is established."),
        "cite_ready": ("both blocks proved, every element proved or not applicable, and "
                       "the § 1681p clock live."),
    }[c["status"]]
    c["disclaimer"] = ("An evidence sheet, not legal advice and not a filing. It records "
                       "what is proved and what is not.")
    c["sha256"] = hashlib.sha256(json.dumps(
        {k: c[k] for k in ("claim_id", "blocking", "elements", "evidence", "status")},
        sort_keys=True).encode()).hexdigest()
    return c


_CLOSERS = {
    "1_cra_received_dispute": "The dated dispute you sent, and the bureau it went to.",
    "2_cra_notified_furnisher": "The CRA results letter or ACDV trail.",
    "3_furnisher_duty_triggered": "Follows from element 2; it cannot be proved separately.",
    "4_investigation_reasonable": ("What the furnisher actually checked - account-level "
                                   "records, or their absence. This is the element "
                                   "defendants win on."),
    "5_result_reported_back": "The furnisher's response to the CRA.",
    "6_inaccuracy_or_incompleteness": "The report after the response, showing the item unchanged.",
    "7_willful_or_negligent": "See the willfulness theory; Safeco sets the standard.",
    "8_publication_to_third_party": ("Who pulled or received the report after the "
                                     "response - a denial letter, a tenant screen, a "
                                     "collector's record."),
    "9_remedy": "Actual loss with documents, or the election of statutory damages.",
}


# ----------------------------------------------------------------------
# Store: one file per claim under private/claims, 0600
# ----------------------------------------------------------------------

def _path(claim_id):
    cid = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(claim_id)).strip("_")
    if not cid:
        raise Refused("claim_id is empty after sanitising")
    return os.path.join(STORE, cid + ".json")


def save(claim):
    from core.fs_boundary import ensure_dir
    from core.asset_registry import guard_fields
    flat = {f"evidence.{i}": json.dumps(e, sort_keys=True)
            for i, e in enumerate(claim.get("evidence") or [])}
    flat["caption"] = str(claim.get("caption") or "")
    flat["parties"] = json.dumps(claim.get("parties") or {}, sort_keys=True)
    try:
        guard_fields(flat)
    except ValueError as exc:
        raise Refused(str(exc))
    ensure_dir(STORE, 0o700)
    p = _path(claim["claim_id"])
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(claim, fh, indent=2, sort_keys=True)
    return p


def load(claim_id):
    p = _path(claim_id)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)
# ----------------------------------------------------------------------
# The lane's authorities, and what each one BINDS
# ----------------------------------------------------------------------

# (citation, what it settles, court, weight in Texas). Weight is the field
# that stops a persuasive case being cited as though it governed - the
# principal's instruction, and the reason the shelf records a court at all.
AUTHORITIES = (
    ("15 U.S.C. 1681s-2", "the furnisher duties, and that (a) carries no private right",
     "statute", "binding"),
    ("15 U.S.C. 1681i", "the CRA reinvestigation that triggers the furnisher's duty",
     "statute", "binding"),
    ("15 U.S.C. 1681n", "willful: actual or statutory damages, punitive, fees",
     "statute", "binding"),
    ("15 U.S.C. 1681o", "negligent: actual damages and fees", "statute", "binding"),
    ("15 U.S.C. 1681p", "the two clocks", "statute", "binding"),
    ("12 CFR 1022.43", "the direct-dispute path, when that path is used",
     "regulation", "binding"),
    ("TransUnion LLC v. Ramirez", "concrete harm; publication to a third party",
     "Supreme Court", "binding"),
    ("Spokeo, Inc. v. Robins", "a statutory violation is not automatic standing",
     "Supreme Court", "binding"),
    ("Safeco Insurance Co. of America v. Burr", "willful = reckless disregard of a known "
     "or obvious risk", "Supreme Court", "binding"),
    ("Gorman v. Wolpoff & Abramson", "what a reasonable investigation requires after notice",
     "Ninth Circuit", "persuasive"),
)

# DECLARED GAPS ARE WORK, NOT COVERAGE. Searched 2026-09-15 and not found in
# the index: a Fifth Circuit § 1681s-2(b) investigation case. The Seventh
# and Tenth Circuits have them and neither binds Texas, so none was shelved
# - shelving one and citing it in San Antonio is the exact error the weight
# column exists to prevent.
GAPS_DECLARED = (
    {"wanted": "a Fifth Circuit § 1681s-2(b) reasonable-investigation case",
     "why_it_matters": "the operating jurisdiction is Texas; Gorman is persuasive only",
     "searched": "2026-09-15", "found": "none in the CourtListener opinions index",
     "nearest": ["Frazier v. Dovenmuehle Mortgage (7th Cir. 2023)",
                 "Chaitoff v. Experian (7th Cir. 2023)"],
     "state": "not_found"},
)


def authorities(resolver=None, circuit="5th"):
    """Every authority this lane rests on, with its weight and whether the
    shelf can open it. `resolver(cite) -> entry or None`."""
    out = []
    for cite, settles, court, weight in AUTHORITIES:
        entry = None
        if resolver is not None:
            try:
                entry = resolver(cite)
            except Exception:
                entry = None
        w = weight
        if weight == "persuasive" and circuit != "5th":
            w = "persuasive (weight depends on the forum)"
        out.append({"citation": cite, "settles": settles, "court": court,
                    "weight_in_texas": w,
                    "in_corpus": bool(entry),
                    "title": (entry or {}).get("title") if entry else None})
    return {"circuit": circuit, "authorities": out,
            "gaps": [dict(g) for g in GAPS_DECLARED],
            "rule": ("A persuasive case is cited as persuasive. Gorman is Ninth Circuit "
                     "and does not bind a Texas district court.")}
