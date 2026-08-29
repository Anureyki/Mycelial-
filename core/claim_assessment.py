"""Test a claim instead of believing it.

A claim that sounds like law is not law. The internet manufactures legal
folklore faster than anyone can read it, and the failure mode is always the
same shape: a real statute is quoted, the words in it resemble the outcome
somebody wanted, and the resemblance is treated as the holding.

So this module refuses to let a claim become a conclusion by assertion. It
walks one path and only one:

    CLAIM -> SOURCE -> EVIDENCE -> OBSERVATION -> ANALYSIS -> CONCLUSION
                                                                  -> CONFIDENCE

Every stage can fail, and a failure at any stage is a real outcome that is
reported rather than routed around. The default conclusion is UNSUPPORTED. A
claim earns anything better by having each prerequisite actually filled with
something checkable; nothing here promotes a claim because it is popular,
because it is cited confidently, or because the person making it owns the
machine.

THE SYMMETRY RULE. `asserted_by` is recorded and never consulted. A claim from
the principal is tested exactly as a claim from a stranger on the internet, and
this module has no field that could privilege one over the other. That is the
whole point: a pipeline that validates its owner's beliefs faster than a
stranger's is not an analysis, it is a confirmation engine with extra steps.

WHAT THIS IS NOT. It is not legal advice, it does not predict how a court will
rule, and `supported` never means `true`. It means: an authority was located,
it governs this transaction, the factual prerequisites it names are evidenced
in the record, and the observed outcome is consistent with it. That is a
narrow, honest claim, and it is the most this machinery can say.
"""
from datetime import datetime
import hashlib
import json

# ---------------------------------------------------------------------------
# The rights ontology.
#
# Collapsing every question into "who owns it" is the most common analytical
# error in this area, because Article 9 is not an ownership machine. Its rules
# turn on which of several DISTINCT rights is in play, and a party can hold one
# while conspicuously lacking another - control without ownership, possession
# without authority, a perfected security interest without priority.
#
# Each entry names the uniform provision that defines it, so the agent reasons
# in uniform sections and resolves the citation to the operating jurisdiction
# (see reference/legal_agent/jurisdictions.json).
# ---------------------------------------------------------------------------
RIGHTS = {
    "ownership": {
        "question": "Who holds title?",
        "not_the_same_as": ["possession", "control"],
        "note": "Article 9 largely does not care. A sale of accounts is inside "
                "Article 9 (9-109(a)(3)) and the buyer owns them, while a "
                "secured party with no title can still enforce.",
    },
    "possession": {
        "question": "Who physically holds the collateral or the instrument?",
        "uniform": ["9-313"],
        "not_the_same_as": ["control", "ownership"],
    },
    "control": {
        "question": "Who can direct disposition without further consent of the owner?",
        "uniform": ["9-104 (deposit accounts)", "9-105 (electronic chattel paper)",
                    "9-106 (investment property)", "9-107 (letter-of-credit rights)"],
        "note": "Control is a SECURED PARTY concept. It is defined against a "
                "third party's collateral, not as a way to hold one's own "
                "assets, and it exists only for the collateral types listed.",
        "not_the_same_as": ["ownership", "possession"],
    },
    "custody": {
        "question": "Who holds it for someone else's benefit?",
        "note": "A custodian holds without beneficial interest. Distinct from "
                "possession, which implies no fiduciary character.",
    },
    "security_interest": {
        "question": "Is there an interest in collateral securing an obligation?",
        "uniform": ["9-203 (attachment)", "9-308 (perfection)"],
        "note": "Attachment and perfection are separate. An unperfected "
                "security interest is real and enforceable against the debtor.",
    },
    "priority": {
        "question": "Whose interest wins against a competing claimant?",
        "uniform": ["9-322 (first-to-file-or-perfect)", "9-327 (deposit accounts)",
                    "9-328 (investment property)", "9-330 (chattel paper)"],
        "note": "9-322 is the ORDINARY rule. Control beats filing only for the "
                "collateral types 9-327/9-328 name.",
    },
    "authority": {
        "question": "Who is empowered to act, and under what instrument?",
        "note": "Authority to sign, to pay, to instruct. Distinct from every "
                "right above - an agent may have authority over an asset in "
                "which they hold nothing.",
    },
    "enforcement_right": {
        "question": "Who may collect, foreclose, or sue?",
        "uniform": ["9-607", "9-609"],
    },
}

RIGHT_STATES = (
    "established",      # the record supports it
    "not_established",  # examined; the record does not support it
    "contradicted",     # the record affirmatively cuts against it
    "not_applicable",   # this right is not in play on these facts
    "undetermined",     # not yet examined - NOT a synonym for absent
)

# The ten prerequisites. A claim cannot reach "supported" with any of these
# unanswered. They are deliberately concrete: each names a thing that either
# exists in the record or does not.
PREREQUISITES = (
    ("instrument",        "What instrument is the claim about?"),
    ("jurisdiction",      "Which jurisdiction's law governs?"),
    ("governing_law",     "Which body of law - and does it reach this transaction?"),
    ("provision",         "Which specific provision is relied on?"),
    ("factual_prereqs",   "What facts does that provision require?"),
    ("definition",        "What does the provision itself define the operative term to mean?"),
    ("documentation",     "What document establishes it?"),
    ("recognizing_party", "What institution or counterparty recognises it?"),
    ("subsequent_action", "What actually happened after?"),
    ("reproducible",      "Can an independent person repeat the procedure and get the same result?"),
)

CONCLUSIONS = (
    "supported",            # authority located, governs, prerequisites evidenced, observation consistent
    "partially_supported",  # true for some collateral/facts, not as stated generally
    "prerequisite_missing", # the authority may govern, but a required fact is not in the record
    "not_governed",         # the cited provision does not reach this transaction
    "unsupported",          # DEFAULT - no authority located, or nothing establishes it
    "contested",            # domains disagree and the conflict is unresolved
    "contradicted",         # the record cuts against the claim
)

REPRODUCIBILITY = (
    "reproduced",       # someone independent ran the procedure and got the result
    "not_reproduced",   # attempted, different result
    "untested",         # DEFAULT - nobody has tried
    "untestable",       # no procedure is even specified
)


def new_claim(statement, asserted_by="unknown", rights_asserted=None, source_of_claim=""):
    """Record a claim as a claim. Nothing here evaluates it yet."""
    cid = "claim_" + hashlib.sha1(
        f"{statement}{datetime.now().isoformat()}".encode()).hexdigest()[:12]
    return {
        "claim_id": cid,
        "statement": statement,
        # Recorded for provenance and NEVER consulted by the scoring below.
        "asserted_by": asserted_by,
        "source_of_claim": source_of_claim,
        "recorded_at": datetime.now().isoformat(),
        "rights_asserted": [r for r in (rights_asserted or []) if r in RIGHTS],
        "prerequisites": {k: {"state": "unanswered", "detail": ""}
                          for k, _ in PREREQUISITES},
        "authorities": [],      # {citation, located_in_corpus, governs}
        "evidence": [],         # {kind, reference, held}
        "observations": [],     # {what_happened, recorded_where}
        "rights": {},           # right -> state
        "reproducibility": {"state": "untested", "procedure": "", "attempts": []},
        "corroboration": [],    # cross-domain agreement AND conflict
        "conclusion": "unsupported",
        "confidence": 0.0,
        "why": ["Nothing has been established yet. Unsupported is the default, "
                "not a verdict."],
    }


def assess(claim):
    """Derive conclusion and confidence from what the record actually holds.

    Deterministic and inspectable on purpose. No model is asked to judge a
    claim - a model asked whether a legal theory is sound will produce fluent
    agreement, which is the exact failure this exists to prevent.
    """
    why = []
    prereq = claim.get("prerequisites", {})
    answered = {k for k, v in prereq.items() if v.get("state") == "answered"}
    missing = [k for k, _ in PREREQUISITES if k not in answered]

    auths = claim.get("authorities", [])
    located = [a for a in auths if a.get("located_in_corpus")]
    governing = [a for a in located if a.get("governs") is True]
    not_governing = [a for a in located if a.get("governs") is False]

    rights = claim.get("rights", {})
    contradicted = [r for r, s in rights.items() if s == "contradicted"]
    established = [r for r, s in rights.items() if s == "established"]
    asserted = claim.get("rights_asserted", [])
    unmet = [r for r in asserted if rights.get(r) not in ("established", "not_applicable")]

    repro = claim.get("reproducibility", {}).get("state", "untested")

    # Order matters: the harshest finding that actually applies wins.
    if contradicted:
        conclusion = "contradicted"
        why.append(f"The record cuts against: {', '.join(sorted(contradicted))}.")
    elif not auths:
        conclusion = "unsupported"
        why.append("No authority has been cited for this claim at all.")
    elif not located:
        conclusion = "unsupported"
        why.append("Every authority cited is absent from the corpus, so none "
                   "of them has been read. A citation is not an authority "
                   "until the text behind it is in hand.")
    elif not_governing and not governing:
        conclusion = "not_governed"
        why.append("The cited provisions were located and do not reach this "
                   "transaction: " + ", ".join(a["citation"] for a in not_governing) + ".")
    elif not governing:
        conclusion = "prerequisite_missing"
        why.append("Authority located, but whether it governs these facts has "
                   "not been determined.")
    elif missing:
        conclusion = "prerequisite_missing"
        why.append("Unanswered prerequisites: " + ", ".join(missing) + ".")
    elif unmet:
        conclusion = "partially_supported"
        why.append("Governing authority and full prerequisites, but these "
                   "asserted rights are not established: " + ", ".join(unmet) + ".")
    else:
        conclusion = "supported"
        why.append("Authority located and governing, all ten prerequisites "
                   "answered, and every asserted right established on the record.")

    # Reproducibility is reported separately and can only ever REDUCE
    # confidence. A claim nobody has managed to repeat is not thereby false -
    # it is untested, and saying so is the honest position.
    if repro == "not_reproduced":
        why.append("An independent attempt did not reproduce the result. This "
                   "is the strongest available signal against the claim short "
                   "of direct contradiction.")
        if conclusion == "supported":
            conclusion = "partially_supported"
    elif repro == "untested":
        why.append("Nobody has independently reproduced the procedure. "
                   "Untested is not the same as false, and not the same as true.")
    elif repro == "untestable":
        why.append("No repeatable procedure is even specified, so the claim "
                   "cannot be checked by anyone else. Treat with suspicion: an "
                   "assertion that cannot be tested cannot be relied on.")

    conflicts = [c for c in claim.get("corroboration", []) if c.get("agrees") is False]
    if conflicts:
        why.append(f"{len(conflicts)} cross-domain conflict(s) unresolved. "
                   "Recorded, not reconciled - forcing agreement would destroy "
                   "the finding.")
        # A claim another domain actively disputes is CONTESTED, whatever the
        # legal analysis alone concluded. Leaving it at "supported" with a
        # quieter number would be forced consensus by another route: the
        # headline would say established while the disagreement sat in a
        # subordinate field nobody reads. Contested is the finding.
        if conclusion in ("supported", "partially_supported"):
            conclusion = "contested"
            why.append("Downgraded to contested: the legal analysis is made "
                       "out on its own terms, but another domain's records do "
                       "not bear it out. Both readings are kept.")

    # Confidence is DERIVED, never asserted. It is a fraction of what was
    # actually checked, so an empty record scores zero rather than defaulting
    # to something reassuring.
    n_prereq = len(PREREQUISITES)
    score = len(answered) / n_prereq * 0.5
    if governing:
        score += 0.2
    if asserted:
        score += 0.2 * (len([r for r in asserted if rights.get(r) == "established"]) / len(asserted))
    else:
        score += 0.0
    if repro == "reproduced":
        score += 0.1
    if conclusion in ("unsupported", "contradicted", "not_governed"):
        score = min(score, 0.15)
    if conclusion == "contested":
        score = min(score, 0.5)
    if conflicts:
        score *= 0.6
    claim["conclusion"] = conclusion
    claim["confidence"] = round(min(score, 1.0), 2)
    claim["why"] = why
    claim["assessed_at"] = datetime.now().isoformat()
    claim["open_questions"] = [q for k, q in PREREQUISITES if k in missing]
    return claim
