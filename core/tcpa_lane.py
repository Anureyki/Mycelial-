#!/usr/bin/env python3
"""The TCPA lane: 47 U.S.C. § 227, one object per claim, and the log is the proof.

    from core.tcpa_lane import open_claim, add_call, assess

WHY THIS LANE IS DIFFERENT FROM THE FURNISHER LANE. There, the evidence is
a paper trail somebody else controls - the ACDV, the results letter. Here
the evidence is the call log on the principal's own phone, which is why
this is the statute people actually collect on: the record exists before
the lawyer does. So this object is built around CALLS, each one counted or
refused on its own facts, and the damages figure is a SUM over the calls
that survive - never a headline number.

THREE THEORIES, AND THEY HAVE DIFFERENT GATES. Collapsing them is the
usual error, because a call can violate one and not the others:

  (b) AUTOMATED CALL TO A CELL - 227(b)(1)(A)(iii). Needs an ATDS or an
      artificial/prerecorded voice. After Facebook, Inc. v. Duguid, 592
      U.S. 395 (2021), an ATDS must use a random or sequential number
      GENERATOR; a system that dials a stored list of targeted numbers is
      not one. So "they used a dialer" is not the element - the generator
      is. A prerecorded voice is an independent route and does not need a
      generator at all, which is the route most live claims actually run.
  (c) NATIONAL DO-NOT-CALL - 227(c)(5) and 47 C.F.R. 64.1200(c)(2). Needs
      the number on the registry 31+ days, a residential subscriber, more
      than one call in twelve months, and a telephone SOLICITATION.
  (d) INTERNAL DO-NOT-CALL - 64.1200(d). The caller must maintain its own
      list and honour a request; this one turns on the principal's own
      stop request and its date.

CONSENT IS THE DEFENDANT'S ISSUE BUT THE SHEET'S BLOCK. The caller bears
the burden of proving consent, and that is not a reason to record it as
absent: a claim built where consent was in fact given collapses on the
first motion. `consent` is blocking and its honest default is `unknown`.
Revocation is tracked separately, because a call before revocation and a
call after it are different violations.

THE MONEY IS NOT THE HEADLINE NUMBER. $500 per violation and $1,500 for a
willful or knowing violation are what a private plaintiff recovers under
227(b)(3) and 227(c)(5). The $43,792-per-violation figure on compliance
vendors' pages is a REGULATORY FORFEITURE the FCC imposes; no private
plaintiff collects it, and this module refuses to put it in a demand.
Treble is "up to" and discretionary - it is computed as a ceiling, and
labelled one.

FOUR YEARS, from 28 U.S.C. § 1658. The TCPA states no period of its own,
so the federal catch-all runs from the date of each call - which means the
clock is PER CALL, and an old call at the top of a log can be time-barred
while the rest are live.
"""
import hashlib
import json
import os
import re
from datetime import date, timedelta
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "private", "claims")

LANE = "tcpa_227"

THEORIES = {
    "b_automated_to_cell": {
        "statute": "47 U.S.C. § 227(b)(1)(A)(iii)",
        "needs": ("called_number_is_cell", "atds_or_prerecorded"),
        "damages_per_call": "500", "willful_per_call": "1500",
        "authority": ["47 U.S.C. 227", "Facebook, Inc. v. Duguid"]},
    "c_national_dnc": {
        "statute": "47 U.S.C. § 227(c)(5); 47 C.F.R. § 64.1200(c)(2)",
        "needs": ("on_national_registry_31_days", "residential_subscriber",
                  "is_solicitation", "more_than_one_call_in_12_months"),
        "damages_per_call": "500", "willful_per_call": "1500",
        "authority": ["47 U.S.C. 227", "47 CFR 64.1200"]},
    "d_internal_dnc": {
        "statute": "47 C.F.R. § 64.1200(d)",
        "needs": ("stop_request_made", "call_after_stop_request", "is_solicitation"),
        "damages_per_call": "500", "willful_per_call": "1500",
        "authority": ["47 U.S.C. 227", "47 CFR 64.1200"]},
}

# Duguid. A dialer is not an ATDS; a GENERATOR is. These are the words that
# describe a system that is not one, and they are read as a refusal of the
# ATDS route - never of the prerecorded route, which is independent.
NOT_ATDS = re.compile(
    r"\b(stored list|targeted|from a list|uploaded|crm|manually dialed|"
    r"click[- ]to[- ]dial|preview dial)\b", re.I)
IS_GENERATOR = re.compile(r"\b(random|sequential)\b.{0,30}\b(generat|number)", re.I)

CHANNELS = ("voice_call", "text_message", "prerecorded_voice", "artificial_voice")
SOL_YEARS = 4          # 28 U.S.C. 1658
REGISTRY_RIPENS_DAYS = 31

BLOCKING = ("consent", "standing_received")
CALL_STATES = ("counted", "refused", "unknown")

# The number a compliance vendor prints and a consumer cannot collect.
REGULATORY_FORFEITURE_NOT_PRIVATE = Decimal("43792")


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
    except ValueError:
        return (date(y + n, m, 28) + timedelta(days=1)).isoformat()


def open_claim(claim_id, caption, **kw):
    if not claim_id or not str(caption or "").strip():
        raise Refused("a claim needs a claim_id and a caption")
    return {
        "claim_id": str(claim_id), "lane": LANE, "caption": str(caption),
        "statute": "47 U.S.C. § 227",
        "caller": kw.get("caller"),
        "my_number_is_cell": kw.get("my_number_is_cell"),
        "registry_listed_on": _iso(kw.get("registry_listed_on"), "registry_listed_on"),
        "residential_subscriber": kw.get("residential_subscriber"),
        "stop_request_on": _iso(kw.get("stop_request_on"), "stop_request_on"),
        "consent": {"state": "unknown", "given_on": None, "revoked_on": None, "note": ""},
        "blocking": {b: "unknown" for b in BLOCKING},
        "calls": [],
        "willfulness_theory": None,
        "status": "open",
    }


def add_call(claim, date_, channel, note="", system_description="", is_solicitation=None,
             prerecorded=None, doc_id=None):
    """One entry from the log. Nothing is counted here - assess() does that,
    because whether a call counts depends on the claim's dates and consent,
    not on the call alone."""
    if channel not in CHANNELS:
        raise Refused(f"channel {channel!r} is not one of {CHANNELS}")
    claim["calls"].append({
        "n": len(claim["calls"]) + 1,
        "date": _iso(date_, "call date"), "channel": channel, "note": str(note),
        "system_description": str(system_description or ""),
        "is_solicitation": is_solicitation,
        "prerecorded": (True if channel in ("prerecorded_voice", "artificial_voice")
                        else prerecorded),
        "doc_id": doc_id,
    })
    return claim


def set_consent(claim, state, given_on=None, revoked_on=None, note=""):
    """`state`: none | given | revoked | unknown. Unknown is the honest
    default and does NOT block counting - it blocks calling the sheet
    ready, which is a different thing."""
    if state not in ("none", "given", "revoked", "unknown"):
        raise Refused("consent state is one of none | given | revoked | unknown")
    claim["consent"] = {"state": state, "given_on": _iso(given_on, "consent given_on"),
                        "revoked_on": _iso(revoked_on, "consent revoked_on"),
                        "note": str(note)}
    claim["blocking"]["consent"] = {"none": "proved", "revoked": "proved",
                                    "given": "failed", "unknown": "unknown"}[state]
    return claim


def set_received(claim, proved, note=""):
    """Standing: the calls reached THIS principal on HIS line. After Ramirez
    the injury is the intrusion, so who received them is the fact that
    carries it."""
    claim["blocking"]["standing_received"] = (
        "proved" if proved else ("failed" if proved is False else "unknown"))
    claim["standing_note"] = str(note)
    return claim


def atds_read(system_description):
    """Duguid, applied to what the caller's system is described as doing."""
    s = str(system_description or "")
    if not s.strip():
        return {"state": "unknown", "why": ("No description of the dialing system. The "
                                            "ATDS route needs one; the prerecorded route "
                                            "does not.")}
    if IS_GENERATOR.search(s):
        return {"state": "atds_asserted",
                "why": ("Described as using a random or sequential number generator, which "
                        "is what Duguid requires. Asserted, not proved - the system is the "
                        "caller's and this is discovery.")}
    if NOT_ATDS.search(s):
        return {"state": "not_atds",
                "why": ("Described as dialing a stored or targeted list. Under Facebook, "
                        "Inc. v. Duguid, 592 U.S. 395 (2021), that is NOT an ATDS. The "
                        "(b) claim must run on a prerecorded or artificial voice instead, "
                        "or not at all.")}
    return {"state": "unknown",
            "why": "The description does not say whether a number generator was used."}


def limitations(call_date, today=None):
    """28 U.S.C. § 1658, per call."""
    if not call_date:
        return {"state": "unknown", "deadline": None}
    deadline = _years(call_date, SOL_YEARS)
    now = today or date.today().isoformat()
    return {"authority": "28 U.S.C. § 1658 (4 years; the TCPA states no period)",
            "call_date": call_date, "deadline": deadline,
            "state": "expired" if now > deadline else "live",
            "days_left": (date.fromisoformat(deadline) - date.fromisoformat(now)).days}


def assess(claim, today=None, theory=None):
    """Count the calls that survive, per theory, and say why each was refused."""
    c = json.loads(json.dumps(claim))
    now = today or date.today().isoformat()
    wanted = [theory] if theory else list(THEORIES)
    consent = c["consent"]
    results = {}

    for th in wanted:
        spec = THEORIES[th]
        counted, refused = [], []
        for call in c["calls"]:
            why = []
            sol = limitations(call["date"], today=now)
            if sol["state"] == "expired":
                why.append(f"time-barred: the 4-year clock ran out {sol['deadline']}")
            # Consent: a call while consent stood is not a violation; a call
            # after revocation is. The dates decide, not the label.
            if consent["state"] == "given" and not consent.get("revoked_on"):
                why.append("consent was given and not revoked")
            elif consent["state"] == "revoked" and consent.get("revoked_on") \
                    and call["date"] < consent["revoked_on"]:
                why.append(f"call predates the revocation on {consent['revoked_on']}")

            if th == "b_automated_to_cell":
                if c.get("my_number_is_cell") is False:
                    why.append("the number called is not a cellular line")
                elif c.get("my_number_is_cell") is None:
                    why.append("not established that the number called is a cellular line")
                pre = call.get("prerecorded")
                a = atds_read(call.get("system_description"))
                if not pre:
                    if a["state"] == "not_atds":
                        why.append("not a prerecorded voice, and the system described is "
                                   "not an ATDS under Duguid")
                    elif a["state"] == "unknown":
                        why.append("neither a prerecorded voice nor a described number "
                                   "generator - the (b) route has no basis yet")
                call["atds"] = a
            elif th == "c_national_dnc":
                listed = c.get("registry_listed_on")
                if not listed:
                    why.append("no national registry listing date on the sheet")
                else:
                    ripe = (date.fromisoformat(listed) + timedelta(days=REGISTRY_RIPENS_DAYS)
                            ).isoformat()
                    if call["date"] < ripe:
                        why.append(f"call is within 31 days of the registry listing "
                                   f"(ripens {ripe})")
                if c.get("residential_subscriber") is not True:
                    why.append("not established as a residential subscriber")
                if call.get("is_solicitation") is not True:
                    why.append("not established as a telephone solicitation")
            elif th == "d_internal_dnc":
                stop = c.get("stop_request_on")
                if not stop:
                    why.append("no stop request recorded")
                elif call["date"] <= stop:
                    why.append(f"call is not after the stop request of {stop}")
                if call.get("is_solicitation") is not True:
                    why.append("not established as a telephone solicitation")

            (refused if why else counted).append(
                {"n": call["n"], "date": call["date"], "channel": call["channel"],
                 "why_refused": why} if why else
                {"n": call["n"], "date": call["date"], "channel": call["channel"]})

        n = len(counted)
        base = Decimal(spec["damages_per_call"]) * n
        ceil = Decimal(spec["willful_per_call"]) * n
        results[th] = {
            "statute": spec["statute"], "authority": spec["authority"],
            "calls_counted": n, "counted": counted, "refused": refused,
            "statutory_damages": f"${base:,.2f}",
            "treble_ceiling_if_willful": f"${ceil:,.2f}",
            "damages_note": ("$500 per violation under the private right; up to $1,500 for a "
                             "willful or knowing violation, and 'up to' is the court's "
                             "discretion, not an entitlement."),
            "not_recoverable": (f"The ${REGULATORY_FORFEITURE_NOT_PRIVATE:,.0f}-per-violation "
                                f"figure on compliance pages is an FCC regulatory forfeiture. "
                                f"A private plaintiff does not collect it and it does not "
                                f"belong in a demand."),
        }

    live = {t: r for t, r in results.items() if r["calls_counted"] > 0}
    blocks = c["blocking"]
    c["theories"] = results
    c["theories_with_calls"] = sorted(live)
    gaps = []
    if blocks["consent"] == "unknown":
        gaps.append("consent is unknown - the caller bears the burden, but a claim built "
                    "where consent was in fact given collapses on the first motion")
    if blocks["consent"] == "failed":
        gaps.append("consent was given and not revoked - every theory here needs that "
                    "undone before it goes anywhere")
    if blocks["standing_received"] != "proved":
        gaps.append("not established that these calls reached this principal on his line")
    if not live:
        gaps.append("no call survives on any theory")
    c["gaps"] = gaps
    c["status"] = ("cite_ready" if (live and not gaps) else "contested")
    c["cite_ready"] = c["status"] == "cite_ready"
    c["what_status_means"] = (
        "cite_ready: at least one call counts on a theory, consent is not a live "
        "defence, and receipt is established."
        if c["cite_ready"] else
        "contested: something material is unproved, or no call survives. No demand "
        "from this sheet may assert a number.")
    c["disclaimer"] = ("An evidence sheet, not legal advice and not a filing. The log is "
                       "the proof; each call is counted or refused on its own facts.")
    c["sha256"] = hashlib.sha256(json.dumps(
        {k: c[k] for k in ("claim_id", "calls", "consent", "blocking", "status")},
        sort_keys=True).encode()).hexdigest()
    return c


def _path(claim_id):
    cid = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(claim_id)).strip("_")
    if not cid:
        raise Refused("claim_id is empty after sanitising")
    return os.path.join(STORE, cid + ".json")


def save(claim):
    from core.fs_boundary import ensure_dir
    from core.asset_registry import guard_fields
    flat = {f"call.{i}": json.dumps(x, sort_keys=True)
            for i, x in enumerate(claim.get("calls") or [])}
    flat["caption"] = str(claim.get("caption") or "")
    flat["caller"] = str(claim.get("caller") or "")
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
