#!/usr/bin/env python3
"""Consumer dispute letters, assembled from the principal's own prose and the
corpus - FCRA disputes, FDCPA validation demands, the combined notice, and
the short portal version.

    from core.dispute_letters import draft
    draft("fdcpa_validation", facts, resolver)

THE PROSE IS THE PRINCIPAL'S (2026-09-14). Self-advocacy language, in his
words: "not legal advice and not a filing for you." It is held here as
templates because a letter he will send under his own name should read as
he wrote it, and because the alternative - a model composing a legal demand
- is the one thing this platform refuses to let Anansi or Legal do
(CLAUDE.md: "Anansi narrating legal text would be Anansi practising law").

THREE RULES, the same three the contract engine runs on:

1. EVERY PARAGRAPH THAT ASSERTS A DUTY CITES THE SECTION IT RESTS ON, and
   the section must open in a corpus. A paragraph whose authority cannot
   be read is refused by name, not shipped on faith. The citations live in
   a schedule after the letter, so the letter reads as a letter.

2. NO IDENTIFIER GOES INTO A LETTER. Account references are last-four
   only; a full account number, a Social Security number, a VA file
   number or a card number anywhere in the facts refuses the whole draft.
   The enclosure list refuses an unredacted DD-214 unless the facts say
   that document is the exact thing being misused - the principal's own
   rule - and even then the letter says to redact SSN, SGLI face amount
   and duty stations.

3. LEGAL DRAFTS; NOTHING LEAVES. The "how to send" block is guidance to
   the principal. There is no verb here that mails, files, uploads or
   posts anything, and the sending tier in CLAUDE.md is refused
   structurally for a reason: a misdirected notice is not correctable.

Two agents drafting the same letter from the same facts produce the same
bytes: fixed paragraph order, no dates the facts did not supply, provenance
of the resolved sections reported outside the document.
"""
import hashlib
import re

# Each paragraph: (id, template, authorities). Authorities are the sections
# the paragraph's assertions rest on and must resolve. A paragraph with no
# assertion of law carries none and always ships.
FCRA_DISPUTE = [
    ("subject", "Subject: Formal dispute under the Fair Credit Reporting Act - inaccurate "
                "consumer-report information", []),
    ("intro", "I am writing to dispute information furnished and maintained on my consumer "
              "report.", []),
    ("duty", "Under the Fair Credit Reporting Act, 15 U.S.C. § 1681i and § 1681s-2, you must "
             "investigate and correct information that is inaccurate, incomplete, or cannot be "
             "verified.", ["15 U.S.C. 1681i", "15 U.S.C. 1681s-2"]),
    ("item", "The following item is inaccurate or incomplete:\n"
             "  - Creditor / furnisher: {furnisher}\n"
             "  - Account: ending {account_last4}\n"
             "  - Date first reported: {date_first_reported}\n"
             "  - What is wrong: {what_is_wrong}\n"
             "  - What is true: {what_is_true}", []),
    ("identifiers", "I did not authorize this reporting as a credit transaction against my "
                    "consumer file. If this item was furnished from documents that contain my "
                    "Social Security number, service history, or other sensitive identifiers, "
                    "those identifiers were not provided for credit-reporting purposes.", []),
    ("demands", "Please:\n"
                "  1. Conduct a reasonable investigation.\n"
                "  2. Verify the item with the furnisher using original account-level records, "
                "not a data dump.\n"
                "  3. Delete or correct the item if it cannot be verified.\n"
                "  4. Send me the results in writing within the statutory period.\n"
                "  5. Provide the name, address, and telephone number of the furnisher.",
     ["15 U.S.C. 1681i"]),
    ("enclosed", "Enclosed: {enclosures}.", []),
    ("dual", "I request that you treat this as a direct dispute to the furnisher and as a "
             "consumer dispute to the consumer reporting agency.",
     ["15 U.S.C. 1681s-2", "12 CFR 1022.43"]),
]

FDCPA_VALIDATION = [
    ("subject", "Subject: Notice of dispute and request to cease unlawful collection - FDCPA / "
                "12 C.F.R. Part 1006", []),
    ("intro", "I am the consumer you have contacted about an alleged debt.", []),
    ("dispute", "This letter is a dispute under the Fair Debt Collection Practices Act, "
                "15 U.S.C. § 1692g, and Regulation F. I dispute the alleged debt. Do not assume "
                "validity.", ["15 U.S.C. 1692g", "12 CFR 1006.34"]),
    ("validation", "You must, within five days of the initial communication if you have not "
                   "already, provide:\n"
                   "  - the amount of the debt\n"
                   "  - the name of the current creditor\n"
                   "  - my right to dispute within 30 days\n"
                   "  - my right to obtain the name and address of the original creditor",
     ["15 U.S.C. 1692g", "12 CFR 1006.34"]),
    ("cease", "Until you validate, cease collection of the disputed amount, including calls, "
              "texts, letters that demand payment, and reporting the item as undisputed.",
     ["15 U.S.C. 1692g", "15 U.S.C. 1692e"]),
    ("do_not", "Do not:\n"
               "  - contact third parties about this debt except as the statute allows\n"
               "  - threaten legal action you cannot or will not take\n"
               "  - misstate the amount, status, or character of the debt\n"
               "  - use my military or VA file as a collection lever\n"
               "  - treat VA compensation, fiduciary-held funds, or housing-subsidy rent as if "
               "they were garnishable consumer wages without lawful process",
     ["15 U.S.C. 1692c", "15 U.S.C. 1692e", "15 U.S.C. 1692f", "38 U.S.C. 5301"]),
    ("writing", "All future communication must be in writing to: {mailing_address}.",
     ["15 U.S.C. 1692c"]),
    ("furnished", "If you have already furnished this account to a consumer reporting agency, "
                  "notify them that the debt is disputed.", ["15 U.S.C. 1692e"]),
]

COMBINED = [
    ("subject", "Subject: Dual notice - FCRA dispute and FDCPA validation demand", []),
    ("roles", "You are acting as both a debt collector and a furnisher of consumer-report "
              "information. I dispute the debt and the reporting.", []),
    ("duties", "Under the FDCPA you must validate. Under the FCRA you must investigate what you "
               "furnished.", ["15 U.S.C. 1692g", "15 U.S.C. 1681s-2"]),
    ("until", "Until both are done:\n"
              "  - stop collection of the disputed amount\n"
              "  - stop reporting the item as accurate and undisputed\n"
              "  - do not sell, assign, or \"trade\" this file onward as if it were verified",
     ["15 U.S.C. 1692g", "15 U.S.C. 1692e"]),
    ("produce", "If you cannot produce the original contract, the complete payment history, and "
                "the legal right to collect and report, delete the tradeline and close the "
                "collection.", ["15 U.S.C. 1692g", "15 U.S.C. 1681s-2"]),
    ("item", "The account at issue: {furnisher}, ending {account_last4}.", []),
    ("writing", "All future communication must be in writing to: {mailing_address}.",
     ["15 U.S.C. 1692c"]),
]

PORTAL_SHORT = [
    ("body", "I dispute this account under the FCRA and FDCPA. The item is inaccurate or "
             "unverified. I demand a reasonable investigation, validation of any alleged debt, "
             "correction or deletion if it cannot be verified, and written results. Do not "
             "continue collection or furnishing while the dispute is open. Do not use my "
             "military service record or full DD-214 as a substitute for account-level proof.",
     ["15 U.S.C. 1681i", "15 U.S.C. 1692g"]),
]

KINDS = {
    "fcra_dispute": ("FCRA dispute - credit bureau / furnisher", FCRA_DISPUTE,
                     ("furnisher", "account_last4", "date_first_reported", "what_is_wrong",
                      "what_is_true")),
    "fdcpa_validation": ("FDCPA / Regulation F validation demand - collector", FDCPA_VALIDATION,
                         ("mailing_address",)),
    "combined": ("Combined FCRA + FDCPA notice", COMBINED,
                 ("furnisher", "account_last4", "mailing_address")),
    "portal_short": ("Short version for a portal / CFPB box", PORTAL_SHORT, ()),
}

HOW_TO_SEND = (
    "How to send it (guidance - nothing here sends anything):\n"
    "  - Credit bureaus: their online dispute, and certified mail if you want a paper trail.\n"
    "  - Furnisher / collector: certified mail, return receipt.\n"
    "  - Pattern / ignored: CFPB complaint at consumerfinance.gov, then the state AG consumer "
    "section.\n"
    "Attach only what proves the point. Do not attach an unredacted DD-214 unless that document "
    "is the exact thing being misused - and even then redact the SSN, the SGLI face amount, and "
    "duty stations."
)

DD214_RX = re.compile(r"\bdd[\s-]?214\b", re.I)
REDACTED_RX = re.compile(r"\bredact", re.I)


class Refused(ValueError):
    pass


def _guard(facts):
    """No identifier in a letter. Raises Refused."""
    from core.identifier_scan import scan
    from core.asset_registry import guard_fields
    blob = " ".join(str(v) for v in facts.values())
    found = scan(blob).get("findings") or []
    if found:
        raise Refused(f"the facts carry {len(found)} identifier(s); a letter carries a last-four "
                      f"and a document reference, never the identifier. Nothing drafted.")
    if "account_last4" in facts:
        try:
            guard_fields({"last_four": str(facts["account_last4"])})
        except ValueError as exc:          # asset_registry.Refused
            raise Refused(str(exc))
    for k, v in facts.items():
        # Spaces and dashes are typography: "123 45 6789" is nine digits.
        if re.search(r"\d{9,}", re.sub(r"[\s-]", "", str(v))):
            raise Refused(f"field {k!r} holds a 9+ digit run. Nothing drafted.")


def _enclosures(facts):
    """The enclosure line, with the DD-214 rule enforced."""
    enc = facts.get("enclosures")
    if isinstance(enc, str):
        enc = [e.strip() for e in enc.split(",") if e.strip()]
    enc = list(enc or [])
    for e in enc:
        if DD214_RX.search(e) and not REDACTED_RX.search(e):
            if not facts.get("dd214_is_the_misused_document"):
                raise Refused(
                    "the enclosure list names a DD-214 that is not marked redacted. An "
                    "unredacted DD-214 goes only where that document is the exact thing being "
                    "misused (set dd214_is_the_misused_document: true), and then with the SSN, "
                    "SGLI face amount and duty stations redacted. Nothing drafted.")
    return ", ".join(enc) if enc else "[ID, proof of address, award letter or payment proof]"


def draft(kind, facts, resolver):
    """-> {kind, letter, schedule, refused, sha256, complete, resolved_from}."""
    if kind not in KINDS:
        raise Refused(f"unknown letter kind {kind!r}; known: {sorted(KINDS)}")
    facts = dict(facts or {})
    _guard(facts)
    title, paragraphs, needs = KINDS[kind]
    missing = [k for k in needs if not str(facts.get(k, "")).strip()]
    if missing:
        raise Refused(f"{kind} needs {missing}. Nothing drafted.")
    fields = dict(facts)
    fields["enclosures"] = _enclosures(facts)

    shipped, refused, schedule = [], [], []
    n = 0
    for pid, template, authorities in paragraphs:
        resolved = {}
        for cite in authorities:
            try:
                e = resolver(cite)
            except Exception:
                e = None
            resolved[cite] = e if isinstance(e, dict) and (e.get("text") or "").strip() else None
        unresolved = [c for c, e in resolved.items() if e is None]
        if unresolved:
            refused.append({"paragraph": pid, "reason": "authority_not_in_corpus",
                            "detail": f"cannot open {unresolved}"})
            continue
        n += 1
        try:
            text = template.format(**fields)
        except KeyError as exc:
            refused.append({"paragraph": pid, "reason": "facts_missing", "detail": str(exc)})
            n -= 1
            continue
        shipped.append({"n": n, "id": pid, "text": text, "authority": list(authorities)})
        for cite in authorities:
            e = resolved[cite]
            body = re.sub(r"\s+", " ", str(e.get("text") or "")).strip()
            schedule.append({"paragraph": n, "citation": cite,
                             "title": str(e.get("title") or ""),
                             "section": str(e.get("citation") or cite),
                             "text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                             "held_by": e.get("held_by") or "own"})

    letter = render(title, shipped, schedule)
    return {
        "kind": kind, "title": title, "letter": letter,
        "sha256": hashlib.sha256(letter.encode("utf-8")).hexdigest(),
        "paragraphs_shipped": [s["id"] for s in shipped],
        "refused": refused, "schedule": schedule, "complete": not refused,
        "resolved_from": sorted({s["held_by"] for s in schedule}),
        "sends": False,
        "standing": ("Self-advocacy language authored by the principal, assembled from the "
                     "corpus. Not legal advice; not a filing. Legal drafts; nothing leaves."),
    }


def render(title, shipped, schedule):
    lines = []
    for s in shipped:
        lines.append(s["text"])
        lines.append("")
    lines.append("---")
    lines.append("AUTHORITIES (for the sender's reference; not part of the letter)")
    seen = set()
    for e in schedule:
        key = (e["paragraph"], e["citation"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"Paragraph {e['paragraph']}: {e['citation']} - {e['title']} "
                     f"[sha256 {e['text_sha256'][:16]}]")
    lines.append("")
    lines.append(HOW_TO_SEND)
    return "\n".join(lines).rstrip() + "\n"


# ----------------------------------------------------------------------
# What the principal SAYS, turned into the facts a letter needs
# ----------------------------------------------------------------------

KIND_CUES = (
    # Most specific first. "both" and "they collect and report" name the
    # combined notice; a portal or CFPB box names the short one.
    ("combined", r"\b(both|combined|dual notice|collect(?:s|ing)? and report|"
                 r"report(?:s|ing)? and collect)\b"),
    ("portal_short", r"\b(portal|cfpb|online form|short version|box)\b"),
    ("fdcpa_validation", r"\b(collector|collection agency|validate|validation|"
                         r"debt collector|stop calling|cease)\b"),
    ("fcra_dispute", r"\b(bureau|credit report|consumer report|tradeline|furnish|"
                     r"experian|equifax|transunion|reporting)\b"),
)

_LABELLED = {
    "furnisher": r"(?:creditor|furnisher|collector|company|outfit)\s*(?:name)?\s*[:\-]\s*(.+)",
    "account_last4": r"(?:account|acct)\s*(?:number)?\s*[:\-]?\s*(?:ending\s*(?:in\s*)?)?(\d{4})\b",
    "what_is_wrong": r"(?:what(?:'s| is)? wrong|wrong|they say|tradeline(?: says| language)?|"
                     r"reported as)\s*[:\-]\s*(.+)",
    # Bare "truth:" as well as "the truth:" - dictation drops the article,
    # and without it the value ran on into what_is_wrong.
    "what_is_true": r"(?:what(?:'s| is)? true|(?:the\s+)?truth|actually|in fact)\s*[:\-]\s*(.+)",
    "date_first_reported": r"(?:first reported|date first reported|reported on)\s*[:\-]?\s*"
                           r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|[A-Z][a-z]+ \d{4})",
    "mailing_address": r"(?:mail(?:ing)? address|write to|send to)\s*[:\-]\s*(.+)",
}


def parse_request(text):
    """-> {kind, facts, missing, asked}. Deterministic; invents nothing.

    WHAT IT WILL NOT DO. It never guesses the creditor's name from
    capitalisation, never paraphrases the tradeline language, and never
    fills `what_is_true` from anything but the principal's own words. Those
    three are the substance of the dispute: a guessed furnisher sends the
    letter to the wrong company, and an invented "what is true" is a false
    statement over his signature. Anything not said plainly comes back in
    `missing` as a question, and nothing drafts until he answers.

    What it does read: a labelled line ("creditor: X", "wrong: Y"), a
    four-digit ending ("account ending 1234"), a quoted tradeline, and the
    cue words that choose which of the four letters he means."""
    t = (text or "").strip()
    low = t.lower()
    kind = next((k for k, pat in KIND_CUES if re.search(pat, low)), None)
    facts, asked = {}, []

    # A LABELLED VALUE ENDS WHERE THE NEXT LABEL BEGINS, not at the end of
    # the message. Dictation runs the fields together on one line -
    # "creditor: Acme LLC. account ending 1234. wrong: charged off" - and
    # taking each value to end-of-line put three facts into the furnisher's
    # name, which would address the letter to a sentence.
    hits = []
    for field, pat in _LABELLED.items():
        for m in re.finditer(pat, t, re.I):
            hits.append((m.start(), m.start(1), m.end(), field))
    hits.sort()
    for i, (lab_start, val_start, m_end, field) in enumerate(hits):
        if field in facts:
            continue
        stop = len(t)
        for later_start, _, _, _ in hits[i + 1:]:
            if later_start > lab_start:
                stop = later_start
                break
        # A fixed-width field (a four-digit ending, a date) ends where its
        # own match ended; only a free-text field runs to the next label.
        if field in ("account_last4", "date_first_reported"):
            val = t[val_start:m_end]
        else:
            val = t[val_start:stop]
        val = val.splitlines()[0].strip().strip(",;").rstrip(".").strip()
        if val:
            facts[field] = val

    if "account_last4" not in facts:
        m = re.search(r"\bending(?:\s+in)?\s+(\d{4})\b", t, re.I) or \
            re.search(r"\blast\s*four\s*(?:is\s*)?(\d{4})\b", t, re.I)
        if m:
            facts["account_last4"] = m.group(1)
    if "what_is_wrong" not in facts:
        # A quoted passage is the tradeline as it reads - his words about
        # their words, which is exactly what belongs in "what is wrong".
        m = re.search(r"[\"\u201c]([^\"\u201d]{4,200})[\"\u201d]", t)
        if m:
            facts["what_is_wrong"] = m.group(1).strip()

    needed = {"fcra_dispute": ("furnisher", "account_last4", "date_first_reported",
                              "what_is_wrong", "what_is_true"),
              "fdcpa_validation": ("mailing_address",),
              "combined": ("furnisher", "account_last4", "mailing_address"),
              "portal_short": ()}
    if kind is None:
        return {"kind": None, "facts": facts, "missing": ["kind"],
                "asked": ["Which letter: the FCRA dispute to a bureau or furnisher, the "
                          "FDCPA validation demand to a collector, the combined notice when "
                          "one outfit does both, or the short portal version?"]}
    missing = [f for f in needed[kind] if not str(facts.get(f, "")).strip()]
    prompts = {
        "furnisher": "the creditor or collector's name, exactly as it appears",
        "account_last4": "the account's last four digits",
        "date_first_reported": "the date it was first reported",
        "what_is_wrong": "the tradeline language - what it says that is wrong",
        "what_is_true": "what is actually true, in your words",
        "mailing_address": "the mailing address all contact must go to",
    }
    asked = [prompts[f] for f in missing]
    return {"kind": kind, "facts": facts, "missing": missing, "asked": asked}
