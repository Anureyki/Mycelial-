"""Shared evidence taxonomy for the finance and credit domains.

Accounting, Legal and Trust all reason about the same documents from different
sides, and until now each carried its own words for them. A label is not a
finding, and the point of naming these is that each one says what is MISSING -
a class here is a gap with a name, not a category a document falls into
because nobody looked harder.

Two rules run through all five:

- **A label is a claim; the ledger event is the evidence.** Paper that reads as
  payment, purchase or discharge asserts that value MOVED. Whether it did is a
  separate fact, and the whole class `posted_label_unsupported` exists because
  those two look identical if all you keep is the amount.
- **Never promote an unverified outcome to authority.** A report of what
  happened to somebody else is a pointer to check, not a result. It is shelved
  as what it is and weighed accordingly.
"""

EVIDENCE_CLASSES = {
    "credit_decision_event": {
        "what": "An application went in and an adverse action came out. NO VALUE MOVED.",
        "why_it_is_its_own_class": (
            "A denial is a decision, not a transaction. Booking one as though "
            "something was bought or lent puts a number in the ledger for an event "
            "that moved nothing, and the entry then has to be explained away later."),
        "moves_value": False,
        "requires": ["application_date", "decision", "decision_date"],
        "adjacent_authority": "ECOA / Reg B adverse action; FCRA 1681m",
    },
    "attempted_origination": {
        "what": "The Accounting booking for a DENIED application.",
        "why_it_is_its_own_class": (
            "The attempt is real and worth recording - it evidences that credit was "
            "sought, when, and from whom - but it is not a purchase and not an "
            "asset. It has no contra account because nothing was received."),
        "moves_value": False,
        "never": ["purchase", "asset", "payment"],
        "pairs_with": "credit_decision_event",
    },
    "community_report": {
        "what": "An unverified account of an outcome somebody else obtained.",
        "why_it_is_its_own_class": (
            "It is a pointer to check, never a result and NEVER an authority class. "
            "The failure this prevents is a forum post read as precedent because it "
            "described a win."),
        "moves_value": False,
        "authority_class": None,
        "never": ["authority", "success", "precedent"],
    },
    "posted_label_unsupported": {
        "what": ("Paper that reads as payment, purchase, credit or discharge with no "
                 "matching ledger event behind it."),
        "why_it_is_its_own_class": (
            "The label asserts that value moved. The entry is what shows it did. A "
            "document and a transaction look the same in a file folder and are not "
            "the same in a set of books."),
        "moves_value": None,
        "resolve_with": ("Identify the funded account, the cash movement, or the "
                         "recorded release. If none exists, the label is the claim."),
    },
    "unbooked_instrument": {
        "what": ("Chattel paper or a credit form that exists, is unpaid, and appears on "
                 "nobody's books with a contra account."),
        "why_it_is_its_own_class": (
            "An instrument with no contra account is on somebody's balance sheet and "
            "the question of WHOSE is the finding, not a detail. Originator, SPE and "
            "investor are three different answers with three different consequences."),
        "moves_value": None,
        "must_ask": "Whose books should show it - originator, SPE, or investor?",
        "adjacent": "true_sale_vs_secured_borrowing",
    },
}

# Which of these may ever be treated as authority. None of them may.
NEVER_AUTHORITY = ("community_report",)

INGESTION_RULE = (
    "Live data is an instrument you can OPEN: a recorded satisfaction, a wire "
    "confirmation, a court order, a denial letter, a CRA disclosure. Community "
    "testimony and redacted sealed files are community_report or unknown - never "
    "success, never authority. This is `lived data outranks documentation` with "
    "its precondition made explicit: lived data has to be data, and a description "
    "of an outcome is not the outcome."
)


def classify_evidence(doc):
    """Name the class from what the document HAS, never from what it is called.

    Returns the class plus what is missing, because the missing part is the
    finding. A `posted_label_unsupported` with the ledger event supplied is
    simply a payment; the class exists to hold the state in between."""
    d = doc if isinstance(doc, dict) else {}
    label = str(d.get("label") or d.get("posted_as") or d.get("title") or "").lower()
    moved = d.get("value_moved")
    ledger = d.get("ledger_event") or d.get("evidence_ref")

    if d.get("source") in ("forum", "testimony", "anecdote", "community") \
            or d.get("verified") is False:
        return {"evidence_class": "community_report", "authority_class": None,
                "reason": "Unverified account of someone else's outcome.",
                "never": EVIDENCE_CLASSES["community_report"]["never"]}

    if d.get("decision") in ("denied", "declined", "adverse") or "adverse action" in label:
        return {"evidence_class": "credit_decision_event",
                "books_as": "attempted_origination",
                "reason": "An application and an adverse action. No value moved.",
                "missing": [k for k in EVIDENCE_CLASSES["credit_decision_event"]["requires"]
                            if not d.get(k)]}

    CLAIMS = ("purchase", "payment", "paid", "discharge", "discharged",
              "satisfied", "settled", "credit applied")
    hit = next((c for c in CLAIMS if c in label), None)
    if hit and not (moved is True and ledger):
        return {"evidence_class": "posted_label_unsupported", "claimed": hit,
                "reason": (f"Labelled '{hit}' with "
                           f"{'no ledger event' if not ledger else 'value_moved=' + repr(moved)}. "
                           "The label asserts value moved; nothing here shows it did."),
                "resolve_with": EVIDENCE_CLASSES["posted_label_unsupported"]["resolve_with"]}

    if d.get("instrument_type") and d.get("unpaid") and not d.get("contra_account"):
        return {"evidence_class": "unbooked_instrument",
                "reason": "An unpaid instrument with no contra account on any book.",
                "must_ask": EVIDENCE_CLASSES["unbooked_instrument"]["must_ask"]}

    return {"evidence_class": "unknown",
            "reason": ("Nothing in this document identifies a class. `unknown` is the "
                       "honest value - it is not a residual bucket, it is a statement "
                       "that the document has not been read closely enough to place."),
            "ingestion_rule": INGESTION_RULE}
