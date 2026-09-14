#!/usr/bin/env python3
"""Flag sensitive identifiers in a document at intake, before anything files it.

    from core.identifier_scan import scan
    scan(text, context="VA Form 21P-555")

WHY THIS EXISTS. A VA fiduciary certificate was ingested carrying the veteran's
full Social Security number as the file number. The SSN was noticed by a human
reading the output and by nothing in the system - no flag, no field, no record
that the document carried one. The principal's point: it should have been
flagged.

TWO QUESTIONS, AND THEY HAVE DIFFERENT ANSWERS. Conflating them is why this
subject produces bad arguments:

  MAY THE AGENCY COLLECT IT   For VA compensation and pension: yes, expressly.
                              38 U.S.C. 5101(c)(1) requires the claimant to
                              furnish it, and (c)(2) directs the Secretary to
                              DENY or TERMINATE payment for failure to do so.
                              That also disposes of the Privacy Act s 7(a)(1)
                              argument, because s 7 exempts disclosures required
                              by federal statute - and this one is required by
                              one.

  MUST IT APPEAR ON THE PAPER The separate question, and the live one. Whether
                              a full SSN belongs printed on a document that
                              goes in the mail is governed by different
                              authority from whether the agency may hold the
                              number at all.

SO THIS FLAGS, IT DOES NOT ALLEGE. The finding is "this document displays a
full SSN", which is a fact about the document. Whether that is unlawful depends
on authority this system does not yet hold, and saying otherwise would be the
overreach the corpus's own SSN summary warns against: a compliance gap is good
leverage in a complaint to an agency or an IG and is not automatically a
damages claim.

NOTHING HERE STORES WHAT IT FINDS. A scanner that wrote the number into its own
finding would be a second copy of the thing it is warning about. Matches are
reported by TYPE, POSITION and LAST FOUR only.
"""
import re

# Deliberately narrow. A scanner that flags every 9-digit run produces noise
# nobody reads, and the noise is what gets it turned off.
PATTERNS = {
    "ssn_formatted": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "ssn_spaced": re.compile(r"\b\d{3}\s\d{2}\s\d{4}\b"),
    # A bare 9-digit run is only an SSN when the document says so nearby.
    "ssn_labelled": re.compile(
        r"(?is)(social\s+security\s+(?:no|number|#)|\bSSN\b|VA\s+FILE\s+NO)"
        r"[^0-9]{0,40}(\d{3}[-\s]?\d{2}[-\s]?\d{4})"),
    "edipi": re.compile(r"(?is)(EDIPI|DoD\s*ID)[^0-9]{0,20}(\d{10})"),
    # A file number printed with ANY internal spacing - "000000 000" stands in
    # here for nine digits broken 6/3, which no SSN pattern matches and which
    # is exactly how the VA form prints it. Missing that shape missed the
    # instance that mattered.
    #
    # THE EXAMPLE USED TO BE THE REAL NUMBER. This file detects a Social
    # Security number on a document, and it carried the principal's own - the
    # VA file number, which this system established IS his SSN - in a comment,
    # in a PUBLIC repository. Redacted 2026-09-12. A detector that leaks the
    # thing it detects is the sharpest version of a rule this repo already
    # has: a redacting log that prints what it redacted has not redacted it.
    #
    # Never paste a real identifier into a test, a fixture or a comment. The
    # shape is what the pattern needs; the value adds nothing and cannot be
    # taken back.
    "file_number": re.compile(
        r"(?is)(VA\s+FILE\s+NO|FILE\s+NUMBER|CLAIM\s+NUMBER)"
        r"[^0-9]{0,40}((?:\d[\s-]?){8}\d)"),
}

# Identifiers a document could carry INSTEAD. Named so a finding can say what
# the alternative was rather than only that there is a problem.
ALTERNATIVES = (
    "VA file number issued separately from the SSN",
    "DoD ID number / EDIPI (10 digits, on the CAC and the VHIC)",
    "VA health care identification number on the VHIC",
    "ICN - the VA Integration Control Number",
)


# PAYMENT CARD / ACCOUNT NUMBERS, and why they live HERE.
#
# tools/check_no_secrets.py had this and core/asset_registry.py did not, so a
# card number in a prose `note` field passed the registry's write guard: the
# field-shape check exempts prose from the long-digit rule (correctly - a note
# may quote a statute number), and the prose scan had no card pattern at all.
# Two scanners, one gap between them, which is the defect class
# tools/check_contracts.py exists to catch.
#
# Three independent conditions must coincide, so that a float's mantissa, a
# list of health-check ports and an Internet Archive identifier do not fire:
# card-shaped and standalone, issuer-prefixed (3-6), and Luhn-valid.
CARD_RE = re.compile(
    r"(?<![\d.])(?:[3-6]\d{12,18}|[3-6]\d{3}[ -]\d{4}[ -]\d{4}[ -]\d{4})"
    r"(?![\d.])(?![\s-]*\d)")


def luhn(value):
    """-> True if the digits satisfy the Luhn checksum, as a card number must."""
    d = [int(c) for c in re.sub(r"\D", "", value or "")]
    if not 13 <= len(d) <= 19:
        return False
    total, parity = 0, len(d) % 2
    for i, n in enumerate(d):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def find_cards(text):
    """-> [{last4}] for every Luhn-valid card-shaped number. Never the value."""
    return [{"kind": "card_number", "last4": _last4(m.group(0)),
             "offset": m.start()}
            for m in CARD_RE.finditer(text or "") if luhn(m.group(0))]


def redact(text):
    """-> (redacted_text, findings). The identifier is REPLACED, never kept.

    WHY THIS EXISTS. The first real document through the pipeline was a VA
    certificate: one page, one paragraph, one file number - which this project
    established IS the veteran's Social Security number. The write guard
    refused the clause, correctly, and because the whole document was that one
    clause, the whole document was refused. The fiduciary's name, the date,
    the form number and the signer were thrown away with the identifier.

    Dropping a clause because one field in it must not be stored destroys the
    evidence around the field. Redacting the field keeps the evidence and
    honours the rule: the number never persists, and a marker says what was
    removed and its last four - enough to find it on the paper original, never
    enough to be the identifier.

    Uses the SAME patterns scan() uses. Two identifier detectors that disagree
    about what is an identifier would let a value pass one and be redacted by
    the other, which is the two-readers fault this repository has met enough
    times to have a gate for."""
    text = text or ""
    spans = []
    for name, rx in PATTERNS.items():
        for m in rx.finditer(text):
            if m.lastindex and m.lastindex >= 2:
                spans.append((m.start(2), m.end(2), name, _last4(m.group(2))))
            else:
                spans.append((m.start(), m.end(), name, _last4(m.group(0))))
    for m in CARD_RE.finditer(text):
        if luhn(m.group(0)):
            spans.append((m.start(), m.end(), "card_number", _last4(m.group(0))))
    # THE RUN THE GUARD WOULD REFUSE, WHATEVER IT IS. The VA form prints the
    # file number as six digits, a space, three digits, with seventy characters
    # of layout between the label and the number - outside every labelled
    # pattern's window. The write guard refused the clause on the bare run and
    # this function found nothing to redact, so the clause was dropped whole.
    #
    # A nine-or-more digit run that no pattern can name is STILL not storable,
    # so it is redacted and labelled for what it is: a number this could not
    # classify. That mirrors core/asset_registry._field_shape exactly - one
    # rule about what may persist, applied by the guard and by the redactor,
    # rather than two rules that disagree about the same digits.
    for m in re.finditer(r"\d(?:[ -]?\d){8,}", text):
        if len(re.sub(r"\D", "", m.group(0))) >= 9:
            spans.append((m.start(), m.end(), "unclassified_digit_run",
                          _last4(m.group(0))))
    # Longest first, then by position, so an SSN inside a labelled file-number
    # match is redacted once rather than twice with an overlap.
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    out, last, findings = [], 0, []
    for start, end, name, l4 in spans:
        if start < last:
            continue
        out.append(text[last:start])
        out.append(f"[REDACTED {name} ending {l4}]")
        findings.append({"kind": name, "last4": l4, "offset": start})
        last = end
    out.append(text[last:])
    return "".join(out), findings


def _last4(s):
    digits = re.sub(r"\D", "", s or "")
    return digits[-4:] if len(digits) >= 4 else "????"


def scan(text, context=None):
    """-> a finding. Never contains the identifier it found."""
    text = text or ""
    hits = []
    for name, rx in PATTERNS.items():
        for m in rx.finditer(text):
            raw = m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(0)
            hits.append({
                "kind": name,
                "offset": m.start(),
                "last4": _last4(raw),
                # The surrounding words, with every digit removed, so a reader
                # can see WHERE it sits without the value travelling.
                "context_redacted": re.sub(r"\d", "#",
                                           text[max(0, m.start() - 60):
                                                m.start() + 40]).strip(),
            })
    # Cards are detected by checksum rather than by a labelled pattern, so
    # they are found separately and folded in here - one findings list, so no
    # caller has to remember to ask twice.
    hits += find_cards(text)
    # One number can match several patterns. Collapse by last4 so a single SSN
    # printed once is one finding, not three.
    seen, unique = set(), []
    for h in sorted(hits, key=lambda x: x["offset"]):
        k = (h["last4"], h["offset"] // 40)
        if k in seen:
            continue
        seen.add(k)
        unique.append(h)

    ssn = [h for h in unique if h["kind"].startswith("ssn")]
    fileno = [h for h in unique if h["kind"] == "file_number"]

    # THE FINDING THAT MATTERS IS NOT "AN SSN APPEARS".
    #
    # It is that the FILE NUMBER IS the SSN - the same nine digits doing two
    # jobs, so every use of the file number is a use of the SSN and every
    # document bearing the file number bears the SSN. That is a sharper and
    # more checkable fact than "this page shows a number", and it is what the
    # principal was pointing at.
    # WIDENING THE LABEL WINDOW WAS THE WRONG FIX. On this form the text
    # extracts with about seventy characters of unrelated layout between
    # "VA FILE NO." and the digits, and a window big enough to span that is a
    # window big enough to pair a label with any number on the page.
    #
    # So: take the digits the document ITSELF labels as the SSN, and look for
    # them anywhere else. The same nine digits appearing twice is the finding,
    # and it needs no guess about which label they sit under.
    same = sorted({h["last4"] for h in ssn} & {h["last4"] for h in fileno})
    if ssn and not same:
        import re as _re
        flat = _re.sub(r"[\s-]", "", text)
        for h in ssn:
            # Recover the full nine digits from the original match position.
            window = _re.sub(r"[\s-]", "", text[h["offset"]:h["offset"] + 120])
            m = _re.search(r"(\d{9})", window)
            if not m:
                continue
            if flat.count(m.group(1)) > 1:
                same = [h["last4"]]
                out_reuse = flat.count(m.group(1))
                break
    out = {
        "context": context,
        "identifiers_found": len(unique),
        "ssn_displayed": bool(ssn),
        "findings": unique,
        "absence_state": "verified_clear" if not unique else "nothing_found",
    }
    if same:
        out["flag"] = "FILE_NUMBER_IS_THE_SSN"
        out["file_number_equals_ssn"] = True
        out["times_the_number_appears"] = locals().get("out_reuse")
        out["what_this_is"] = (
            f"The Social Security number ending {same[0]} appears more than "
            f"once on this document - it is doing duty as the file number as "
            f"well. Every use of the file number is therefore a use of the "
            f"SSN, and every document bearing the file number bears the SSN. "
            f"That is a fact about the document, not a legal conclusion.")
    elif ssn:
        out["flag"] = "SSN_ON_DOCUMENT"
        out["what_this_is"] = (
            "This document displays a full Social Security number. That is a "
            "fact about the document, not a legal conclusion.")
    if same or ssn:
        out["absence_state"] = "verified_clear"
        out["collection_authority"] = (
            "For VA compensation and pension the SSN is required by statute - "
            "38 U.S.C. 5101(c)(1) - and (c)(2) directs denial or termination "
            "for failure to furnish it. So the question is NOT whether VA may "
            "hold the number.")
        out["the_live_question"] = (
            "Whether a full SSN must appear PRINTED on a document that goes in "
            "the mail, when a non-SSN identifier exists.")
        out["alternatives_that_exist"] = list(ALTERNATIVES)
        out["not_in_corpus"] = (
            "The authority governing SSNs on mailed federal documents is not "
            "shelved. Until it is, this system can say the document displays "
            "one and cannot say what follows.")
        out["caution"] = (
            "The corpus's own SSN summary warns against overreach here: a "
            "compliance gap is real leverage in a complaint to the agency or "
            "an IG, and is not automatically a damages claim.")
    return out
