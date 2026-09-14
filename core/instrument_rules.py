#!/usr/bin/env python3
"""Instrument doctrine: what an instrument IS, when it matures, and what a
tender does to a claim. Declared as rules, each tested against the shelf.

    from core.instrument_rules import classify_instrument, doctrine_report

THE PRINCIPAL'S LIST, 2026-09-14, grouped as he grouped it - instrument
rules, credit classification, discharge mechanics, shelter and fiduciary
inversion, system flags, transmission, training pairs. Every rule here is
one of his sentences, held with its evidence state. That state is the
point. CLAUDE.md is explicit that the principal's own claims run the
identical gauntlet as a stranger's: a rule is `cited` when the shelved text
says it, `stated_by_principal` when the shelf is silent, and `contested`
when the shelved text says otherwise - and a contested rule is NOT applied
by any verb here. It is reported, with the words that contest it, so he
can read them. Encoding a rule the corpus refutes would make the agents
confidently wrong in his voice, which is the one failure worse than
ignorance.

Two of the rules are contested by the text on the shelf as of today, and
doctrine_report() shows exactly where:

  - "Under 3.311, the tender is the payment - the funds behind it don't have
    to be good."  Tex. Bus. & Com. Code § 3.311(a)(3) makes discharge turn
    on "the claimant obtained payment of the instrument", and § 3.310(b)(1)
    says an uncertified check only SUSPENDS the obligation "until dishonor
    of the check or until it is paid". A dishonoured tender discharges
    nothing. What IS true, and cited: once the instrument is paid, the
    claim is discharged unless the claimant repays within 90 days, and a
    paid instrument cannot afterwards have "bounced" - that contradiction
    is real and the bank statement settles it.
  - "Every document you can download is electronic transmission - the
    violation is live every time you pull a file."  Pub. L. 115-59 § 2
    restricts the SSN "on any document sent by mail". Mail. The shelf holds
    nothing extending it to a download, so the rule is contested as to
    downloads and cited as to mail.

MATURITY IS MANDATORY. An instrument declares a value; it must also declare
when that value is available. An award letter is present; SGLI is
contingent - it matures on death and never at a purchase counter; a check
tendered as full satisfaction is conditional - on payment, and on the
claimant not repaying within the statutory window. An instrument that
declares a value with no maturity is Unclassifiable and refuses to ship.
That is the principal's rule and it is also this codebase's: `unknown is
never complete`.

TRAINING PAIRS ARE COLLECTED, NOT TRAINED. Every inversion caught becomes a
denial pair - the false claim, why it is false, the correct classification,
and the authority - appended under datasets/ with its origin. Nothing here
trains anything: "Do not train Core" stands, and a pair carrying a personal
identifier is refused at the door by the same scanner the registries use.
"""
import hashlib
import json
import os
import re
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAIRS = os.path.join(ROOT, "datasets", "inversion_pairs.jsonl")


class Unclassifiable(Exception):
    """A value with no maturity, or a maturity nobody declared."""


# ----------------------------------------------------------------------
# Credit classification
# ----------------------------------------------------------------------

MATURITY = ("present", "contingent", "conditional")

# What each instrument kind IS, before any value is read off it. The role
# is the principal's distinction between a document that PROVES something
# and a token that VERIFIES something; the default maturity is his
# classification, and a caller may not override it with silence.
INSTRUMENTS = {
    "award_letter":     {"role": "present_credit",   "maturity": "present",
                         "note": "A present credit. The value is available now."},
    "dd214":            {"role": "source_document",  "maturity": "contingent",
                         "note": ("A source document, not a verification token. Service is "
                                  "verified through the VA or DoD without the original "
                                  "changing hands. An original once transmitted is treated "
                                  "as a sold document.")},
    "sgli":             {"role": "contingent_credit", "maturity": "contingent",
                         "note": ("A contingent credit. It matures on death and never at a "
                                  "purchase counter.")},
    "check_full_satisfaction": {"role": "conditional_payment", "maturity": "conditional",
                         "note": ("A conditional payment: conditional on the instrument "
                                  "being paid, and on the claimant not repaying within the "
                                  "statutory window.")},
    "check":            {"role": "conditional_payment", "maturity": "conditional",
                         "note": "Suspends the obligation until paid or dishonoured."},
    "cashiers_check":   {"role": "payment",           "maturity": "present",
                         "note": "Discharges to the extent of its amount when taken."},
    "wire":             {"role": "payment",           "maturity": "present",
                         "note": "Funds transfer; present on completion."},
    "promissory_note":  {"role": "conditional_payment", "maturity": "conditional",
                         "note": "Suspends the obligation until paid or dishonoured."},
    "escrow":           {"role": "conditional_payment", "maturity": "conditional",
                         "note": "Held by a third party against stated conditions."},
}


def classify_instrument(kind, value=None, maturity=None, declared_by=None):
    """-> {kind, role, maturity, value, matures_on, ...}. Raises Unclassifiable.

    `maturity` must be declared or known for the kind. A value with no
    maturity is refused: that is the rule, and the whole reason SGLI's four
    hundred thousand cannot be spent at a counter."""
    k = str(kind or "").strip().lower().replace(" ", "_").replace("-", "")
    if k == "dd_214":
        k = "dd214"
    base = INSTRUMENTS.get(k)
    mat = (maturity or (base or {}).get("maturity") or "").strip().lower()
    if value is not None and not mat:
        raise Unclassifiable(
            f"{kind!r} declares a value ({value}) and no maturity. An instrument "
            f"that declares a value without declaring when that value is available "
            f"is unclassifiable and does not ship. Declare maturity as one of "
            f"{MATURITY}.")
    if mat and mat not in MATURITY:
        raise Unclassifiable(f"maturity {maturity!r} is not one of {MATURITY}")
    if base is None and not mat:
        raise Unclassifiable(
            f"{kind!r} is not a known instrument kind and no maturity was declared. "
            f"Known: {sorted(INSTRUMENTS)}.")
    return {
        "kind": k, "role": (base or {}).get("role", "unknown"), "maturity": mat,
        "value": value, "note": (base or {}).get("note", ""),
        "declared_by": declared_by,
        "spendable_now": mat == "present",
        "classification_basis": ("declared" if maturity else "instrument_kind"),
    }


# ----------------------------------------------------------------------
# Discharge mechanics, read from the text
# ----------------------------------------------------------------------

def discharge_by_instrument(facts, lookup=None):
    """Apply Tex. Bus. & Com. Code § 3.311 to a tender. -> verdict dict.

    Elements are (a)(1)-(3) and (b); exceptions are (c)(1) and (c)(2). Each
    element is answered from `facts` or reported `not_established` - never
    assumed. A tender the claimant did not obtain payment of is NOT a
    discharge, whatever the tender said: that is (a)(3), and the verdict
    quotes it. `facts` keys, each True/False/None:
        good_faith_tender, full_satisfaction_statement_conspicuous,
        claim_unliquidated_or_disputed, claimant_obtained_payment,
        claimant_repaid_within_90_days, organization_designated_address_missed
    """
    f = {k: facts.get(k) for k in (
        "good_faith_tender", "full_satisfaction_statement_conspicuous",
        "claim_unliquidated_or_disputed", "claimant_obtained_payment",
        "claimant_repaid_within_90_days", "organization_designated_address_missed")}
    elements = {
        "a1_good_faith_tender_as_full_satisfaction": f["good_faith_tender"],
        "a2_claim_unliquidated_or_bona_fide_dispute": f["claim_unliquidated_or_disputed"],
        "a3_claimant_obtained_payment": f["claimant_obtained_payment"],
        "b_conspicuous_full_satisfaction_statement": f["full_satisfaction_statement_conspicuous"],
    }
    missing = [k for k, v in elements.items() if v is None]
    failed = [k for k, v in elements.items() if v is False]
    exceptions = []
    if f["organization_designated_address_missed"]:
        exceptions.append("c1_designated_address_not_used")
    if f["claimant_repaid_within_90_days"]:
        exceptions.append("c2_repaid_within_90_days")
    if failed:
        verdict = "not_discharged"
    elif missing:
        verdict = "not_established"
    elif exceptions:
        verdict = "not_discharged"
    else:
        verdict = "discharged"
    out = {"verdict": verdict, "elements": elements, "missing": missing,
           "failed": failed, "exceptions": exceptions,
           "authority": "Tex. Bus. & Com. Code § 3.311"}
    if f["claimant_obtained_payment"] is False:
        out["why"] = ("§ 3.311(a)(3): discharge requires that 'the claimant obtained "
                      "payment of the instrument'. A tender that was not paid "
                      "discharges nothing; under § 3.310(b)(1) an uncertified check "
                      "only suspends the obligation until it is paid or dishonoured.")
    if lookup is not None:
        try:
            hits = lookup("3.311") or []
            out["text_in_corpus"] = bool(hits)
        except Exception:
            out["text_in_corpus"] = None
    return out


def contradiction(claim, observation):
    """A claim against a record. 'It bounced' against a statement showing it
    paid is a contradiction; both cannot be true, and the record is the
    evidence. Refuses when either side is missing - one side is a guess."""
    if not claim or not observation:
        return {"contradiction": None,
                "why": "One side is missing. A contradiction with one side absent is a guess."}
    c, o = str(claim).lower(), str(observation).lower()
    bounced = bool(re.search(r"\b(bounced|dishono[u]?red|returned|nsf|insufficient)\b", c))
    paid = bool(re.search(r"\b(paid|cleared|posted|deposited|cashed|settled)\b", o))
    if bounced and paid:
        return {"contradiction": True, "claim": claim, "observation": observation,
                "why": ("The claim says the instrument was dishonoured; the record says it "
                        "was paid. Under Tex. Bus. & Com. Code § 3.310(b)(1) payment of a "
                        "check discharges the obligation to its amount, and a paid "
                        "instrument was not dishonoured. Between a claim and a record, the "
                        "record wins."),
                "authority": ["Tex. Bus. & Com. Code § 3.310", "Tex. Bus. & Com. Code § 3.311"]}
    return {"contradiction": False, "claim": claim, "observation": observation,
            "why": "The claim and the record do not oppose each other on their face."}


# ----------------------------------------------------------------------
# System flags and inversions
# ----------------------------------------------------------------------

def system_flag(flag, by=None):
    """A fraud filter's flag is a risk-scoring decision, not a legal fact.
    Logged as what it is; the demand is for the authority behind it."""
    return {
        "flag": flag, "flagged_by": by,
        "classification": "risk_scoring_decision",
        "legal_fact": False,
        "demand": ("The authority check behind the flag: which rule, applied to which "
                   "fact, by whom. A score is not a finding until the rule that "
                   "produced it is named."),
        "logged_at": time.strftime("%Y-%m-%d"),
    }


def shelter_inversion(instrument, received_by, owed_to, booked_as):
    """A benefit owed to the person, booked as the receiver's revenue, is an
    inversion. The demand is binary: return the instrument or return the
    credit. Nothing here decides which - it names the inversion."""
    inverted = (str(owed_to or "").lower() != str(received_by or "").lower()
                and "revenue" in str(booked_as or "").lower())
    return {
        "instrument": instrument, "received_by": received_by, "owed_to": owed_to,
        "booked_as": booked_as, "inversion": inverted,
        "demand": (["return the instrument", "return the credit"] if inverted else []),
        "why": (f"A {instrument} owed to {owed_to} was booked by {received_by} as "
                f"{booked_as}. The credit belongs to the person it is owed to."
                if inverted else "No inversion on the facts given."),
        "evidence": "stated_by_principal",
    }


def inversion_pair(false_claim, why_false, correct_classification, authority,
                   caught_by=None, path=None):
    """Append one denial pair. Collection only - nothing trains on it here.

    A pair carrying a personal identifier is refused: the dataset must never
    become the place a Social Security number lands."""
    from core.identifier_scan import scan
    blob = " ".join(str(x) for x in (false_claim, why_false, correct_classification))
    found = scan(blob).get("findings") or []
    if found:
        raise ValueError(f"REFUSED: the pair carries {len(found)} identifier(s); "
                         f"a training pair never carries one.")
    rec = {
        "false_claim": false_claim, "why_false": why_false,
        "correct_classification": correct_classification,
        "authority": authority if isinstance(authority, list) else [authority],
        "caught_by": caught_by,
        "origin": os.environ.get("MYCELIAL_EVENT_ORIGIN") or "runtime",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "trained": False,
    }
    rec["sha256"] = hashlib.sha256(json.dumps(
        {k: rec[k] for k in ("false_claim", "why_false", "correct_classification")},
        sort_keys=True).encode()).hexdigest()
    p = path or PAIRS
    os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


# ----------------------------------------------------------------------
# The rules, each with its test against the shelf
# ----------------------------------------------------------------------

def _quote(lookup, cite, needle, span=220):
    """The sentence in the shelved section that carries `needle`, or None."""
    try:
        hits = lookup(cite) or []
    except Exception:
        return None
    for h in hits:
        t = re.sub(r"\s+", " ", str(h.get("text") or ""))
        i = t.lower().find(needle.lower())
        if i >= 0:
            return t[max(0, i - 80): i + span]
    return None


def _rule_tender_is_payment(lookup):
    a3 = _quote(lookup, "3.311", "obtained payment of the instrument")
    b1 = _quote(lookup, "3.310", "until dishonor of the check")
    if a3 or b1:
        return "contested", [q for q in (a3, b1) if q], (
            "§ 3.311(a)(3) conditions discharge on the claimant having obtained "
            "payment; § 3.310(b)(1) suspends the obligation only until the check is "
            "paid or dishonoured. The funds DO have to be good.")
    return "not_checked", [], "Neither section is on the shelf."


def _rule_discharge_stands(lookup):
    b = _quote(lookup, "3.311", "the claim is discharged if")
    c2 = _quote(lookup, "3.311", "within 90 days after payment")
    if b:
        return "cited", [q for q in (b, c2) if q], (
            "Once the instrument is paid and the tender was conspicuous, the claim is "
            "discharged - unless the claimant tenders repayment within 90 days "
            "(§ 3.311(c)(2)) or had designated an address the tender missed (c)(1).")
    return "not_checked", [], "§ 3.311 is not on the shelf."


def _rule_cashing_is_conversion(lookup):
    b = _quote(lookup, "3.311", "the claim is discharged if")
    if b:
        return "unsupported_by_corpus", [b], (
            "§ 3.311 makes obtaining payment the DISCHARGE of the claim, and says "
            "nothing about conversion. The shelf holds no authority that cashing a "
            "conditional tender is conversion; the rule stays the principal's.")
    return "not_checked", [], "§ 3.311 is not on the shelf."


def _rule_bounced_after_cashing(lookup):
    b1 = _quote(lookup, "3.310", "Payment or certification of the check results in discharge")
    if b1:
        return "cited", [b1], (
            "A paid check discharged the obligation to its amount and was not "
            "dishonoured. 'It bounced' after the statement shows it paid is a "
            "contradiction, and the statement is the record.")
    return "not_checked", [], "§ 3.310 is not on the shelf."


def _rule_transmission(lookup):
    q = _quote(lookup, "Pub. L. 115-59 § 2", "sent by mail")
    if q:
        return "contested", [q], (
            "Pub. L. 115-59 § 2 restricts the SSN on a document 'sent by mail'. It "
            "is cited as to mailed documents. Nothing on the shelf extends it to a "
            "download, so 'every download is a live violation' is contested as to "
            "downloads until an authority for it is shelved.")
    return "not_checked", [], "Pub. L. 115-59 is not on the shelf."


def _rule_sgli_contingent(lookup):
    q = _quote(lookup, "38 U.S.C. 1970", "death")
    if q:
        return "cited", [q], "SGLI is payable on the death of the insured."
    return "stated_by_principal", [], "38 U.S.C. 1970 is not on the shelf; the rule stands as stated."


def _stated(_lookup):
    return "stated_by_principal", [], "No authority sought; the principal's rule."


RULES = (
    {"id": "dd214_source_document", "group": "instrument_rules",
     "statement": ("The DD-214 is a source document, not a verification token - a bank or "
                   "shelter can verify service through the VA or DoD without receiving the "
                   "original."), "test": _stated},
    {"id": "transmitted_original_is_sold", "group": "instrument_rules",
     "statement": "Once you transmit the original, treat it as a sold document.",
     "test": _stated},
    {"id": "sgli_contingent", "group": "instrument_rules",
     "statement": ("The SGLI four hundred thousand is a contingent credit, not present "
                   "value - it matures on death, never at a purchase counter."),
     "test": _rule_sgli_contingent},
    {"id": "maturity_mandatory", "group": "credit_classification",
     "statement": ("Every instrument declares a value but must also declare its maturity. "
                   "An instrument that declares a value without declaring its maturity is "
                   "unclassifiable and refuses to ship."), "test": _stated,
     "enforced_by": "classify_instrument"},
    {"id": "tender_is_payment", "group": "discharge_mechanics",
     "statement": ("Under 3.311, the tender is the payment - the funds behind it don't "
                   "have to be good."), "test": _rule_tender_is_payment},
    {"id": "discharge_stands", "group": "discharge_mechanics",
     "statement": "If they accept it and can't return it, the discharge stands.",
     "test": _rule_discharge_stands},
    {"id": "cashing_is_conversion", "group": "discharge_mechanics",
     "statement": ("Cashing it as ordinary revenue while ignoring the conditions is "
                   "conversion."), "test": _rule_cashing_is_conversion},
    {"id": "bounced_after_cashing", "group": "discharge_mechanics",
     "statement": ("Claiming it 'bounced' after cashing it is a contradiction your agents "
                   "log against the bank statement."), "test": _rule_bounced_after_cashing,
     "enforced_by": "contradiction"},
    {"id": "shelter_inversion", "group": "shelter_and_fiduciary_inversion",
     "statement": ("A VA check received for your room and board is a benefit owed to you, "
                   "booked as their revenue. The demand is binary: return the instrument "
                   "or return the credit."), "test": _stated,
     "enforced_by": "shelter_inversion"},
    {"id": "flag_is_not_fact", "group": "system_flags",
     "statement": ("A bank's fraud filter flagging instruments in your name is a "
                   "risk-scoring decision, not a legal fact. The agent logs the flag, then "
                   "demands the actual authority check behind it."), "test": _stated,
     "enforced_by": "system_flag"},
    {"id": "ssn_transmission", "group": "transmission",
     "statement": ("Public Law 115-59 bars the SSN on transmitted documents. Every "
                   "document you can download is electronic transmission - the violation "
                   "is live every time you pull a file."), "test": _rule_transmission},
    {"id": "inversions_become_pairs", "group": "training_pairs",
     "statement": ("Every inversion you catch becomes a denial pair: the false claim, the "
                   "reason it's false, the correct classification."), "test": _stated,
     "enforced_by": "inversion_pair"},
)

APPLIED_STATES = ("cited", "stated_by_principal")


def doctrine_report(lookup):
    """Every rule, tested against the shelf right now. -> list of dicts.

    `applied` is True only for cited or stated rules; a contested or
    unsupported rule is reported and NOT applied by any verb here."""
    out = []
    for r in RULES:
        state, quotes, why = r["test"](lookup)
        out.append({
            "id": r["id"], "group": r["group"], "statement": r["statement"],
            "evidence": state, "applied": state in APPLIED_STATES,
            "corpus_says": quotes, "why": why,
            "enforced_by": r.get("enforced_by"),
        })
    return out
