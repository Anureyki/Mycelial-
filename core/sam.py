"""SAM.gov compliance - registration, identifiers, the renewal clock, and the
one hard refusal. The principal's SAM module (2026-10-16).

What the held text actually says, and what it does not:
  - FAR 4.1102(a) / 52.204-7(b)(1): registered when an offer or quotation is
    submitted AND at the time of award. Two points, not "continuous" - the
    "August 2026 continuous-registration rule" in the brief is not in the
    FAR as of 2026-08-01; the August 2025 change it echoes went the other way.
  - 52.204-13: review and update in SAM "on an annual basis, from the date of
    initial registration or subsequent updates". SAM.gov: "renew your
    registration every 365 days". So the clock runs 365 days from the LAST
    update, not from first registration.
  - The UEI is "a number or other identifier" (52.204-7) - FAR fixes no
    format. The 12-character alphanumeric UEI and the 5-character CAGE code
    are formats as published by GSA and DLA: checked here as REFERENCE,
    labelled as such, never cited as regulation.

THE REFUSAL. Paying anyone to register, renew or obtain a UEI is refused, by
construction, whatever the price or the vendor's story. GSA (bulletin
GSA-SAM-2023-05-04): "Registration in SAM.gov is always free" and "any email
or website that asks for money ... is not a government message or site".
Paid SAM "setup" is a known scam vector against small firms; a refusal state
that a persuasive email could talk round is not a refusal state.
"""
import re
from datetime import datetime, timedelta

UEI_RX = re.compile(r"^[A-Z0-9]{12}$")
CAGE_RX = re.compile(r"^[A-Z0-9]{5}$")
FORMAT_BASIS = ("format as published by GSA (UEI: 12 characters, letters and digits) and DLA "
                "(CAGE: 5 characters) - reference, not regulation; FAR 52.204-7 defines the UEI "
                "only as 'a number or other identifier'")

SAM_SERVICE = re.compile(r"\b(sam(\.gov)?|system for award management|unique entity id(entifier)?|"
                         r"\buei\b|cage code|entity registration)\b", re.I)
REGISTRATION_ACT = re.compile(r"\b(regist|renew|activat|reactivat|set ?up|obtain|update|"
                              r"maintain|annual confirmation)", re.I)
MONEY = re.compile(r"(\$\s?\d|\bfee\b|\bcharge|\bpay(ment)?\b|\binvoice|\bcost|\bprice|"
                   r"\bpurchase|\bsubscription)", re.I)


def validate_ids(uei=None, cage=None):
    out = {"basis": FORMAT_BASIS}
    if uei is not None:
        u = str(uei).strip().upper()
        out["uei"] = {"value": u, "format_ok": bool(UEI_RX.match(u)),
                      "note": None if UEI_RX.match(u) else "not 12 letters/digits"}
    if cage is not None:
        c = str(cage).strip().upper()
        out["cage"] = {"value": c, "format_ok": bool(CAGE_RX.match(c)),
                       "note": None if CAGE_RX.match(c) else "not 5 letters/digits"}
    out["not_checked"] = ("a well-formed identifier is not a verified one - the contracting "
                          "officer verifies registration in SAM itself (FAR 4.1103)")
    return out


def renewal_due(last_updated):
    d = datetime.fromisoformat(str(last_updated)[:10])
    return (d + timedelta(days=365)).date().isoformat()


def offer_check(registration, offer_date, award_date=None):
    """Active at submission and at award (52.204-7(b)(1))?"""
    if not registration:
        return {"ok": False, "why": "no SAM registration on record"}
    exp = registration.get("expires")
    status = registration.get("status")
    pts = [("offer", offer_date)] + ([("award", award_date)] if award_date else [])
    res = []
    for name, d in pts:
        active = (status == "active" and exp is not None and str(d)[:10] <= exp)
        res.append({"point": name, "date": str(d)[:10], "registered": active})
    ok = all(r["registered"] for r in res)
    return {"ok": ok, "points": res, "expires": exp,
            "rule": "registered when the offer is submitted and at time of award (48 CFR 52.204-7(b)(1); 4.1102(a))",
            "why": None if ok else "registration lapses before a required point - renew before submitting"}


def screen_service_offer(text="", amount=None, vendor=None):
    """REFUSED if anything asks for money to register, renew or get a UEI."""
    t = f"{text or ''} {vendor or ''}"
    is_sam = bool(SAM_SERVICE.search(t)) and bool(REGISTRATION_ACT.search(t))
    asks_money = bool(MONEY.search(t)) or (amount not in (None, "", 0, "0"))
    if is_sam and asks_money:
        return {"decision": "REFUSED", "pay": False,
                "why": ("Paying for SAM registration, renewal or a UEI is refused, always. GSA: "
                        "'Registration in SAM.gov is always free'; any email or website that asks "
                        "for money to register is not a government message (GSA-SAM-2023-05-04)."),
                "do_instead": ("Register or renew yourself at sam.gov; free help is at fsd.gov. "
                               "Do not click links in the message; report it to GSA if it imitates "
                               "the government."),
                "citation": "GSA-SAM-2023-05-04", "standing": "agency guidance"}
    if is_sam:
        return {"decision": "no_payment_requested", "pay": False,
                "why": "about SAM registration, but no request for money found"}
    return {"decision": "not_a_sam_registration_offer", "pay": None}
