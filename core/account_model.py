#!/usr/bin/env python3
"""Four layers plus the benefit. One schema for every account, no special cases.

    from core.account_model import trace, layers_of
    trace("trulink_card")

THE MODEL. Any account can be decomposed into six questions, and they are
different questions even when one entity answers several of them:

    identifier   what names the account and who issued that name
    holder       who currently holds the interest - the right to enforce it
    fiduciary    who holds legal title / owes duties of loyalty and care
    servicer     who administers it day to day, for a fee
    debt_holder  who carries the obligation if it goes wrong
    benefit      what the account exists to deliver, and to whom

`holder` AND `debt_holder` ARE OPPOSITE SIDES AND THE NAME IS A TRAP. In
ordinary usage "holds the debt" means owning it as an asset - the creditor.
HERE IT DOES NOT. `debt_holder` is who CARRIES the obligation, the obligor; on
a VA benefit that is the United States, and on a car note it is the person.
`holder` is the other side: who may enforce it and receive payment. The name
predates this layer and is left alone rather than renamed, because renaming a
field that five corpus entries and a build gate already use would move the
ambiguity rather than remove it - so it is written down here instead.

WHY THE HOLDER LAYER WAS MISSING FOR A WHILE, since the gap is instructive.
The first five layers all answer "who is doing something to this account".
Nobody asked who OWNS the right, because on a public benefit the answer is
obvious and never moves. On assigned consumer paper it is the only question
that matters, it moves repeatedly, and the party now demanding payment is
often not the party the person dealt with.

THE LABEL ON THE DOOR IS NOT ONE OF THE FIVE. A thing called a "trust" whose
fiduciary is a corporation, whose servicer is the same corporation, and whose
debt_holder is the person who funded it, is a custodial product. Calling it a
trust does not move a duty; only an instrument does. The label is evidence
about marketing, not about structure - which is why it is stored in its own
field and never consulted when the layers are read.

WHY A SCHEMA AND NOT RULES IN CODE. A statute changes, an instruction is
reissued, a servicer is replaced - and every one of those is a data change. If
the layers were branches in an agent, each change would be a code change, a
review and a deploy, and the agent would drift from the law between them. The
entries live in reference/_shared/account_layers.json, versioned and cited, and
this module only reads them.

DETERMINISM IS A REQUIREMENT, NOT A PROPERTY. Two agents tracing one account
must produce the same map, or the map is an opinion. Nothing here samples,
scores, ranks by similarity, or asks a model. It reads fields and returns them
in a fixed order.

WHAT IS NOT FILLED IN IS SAID. A layer nobody verified is `unverified` with the
reason, never a plausible guess. An account map that invents a debt-holder is
worse than one that admits it does not know: the whole point of the trace is to
find out who carries the obligation, and a guess there is the answer somebody
would act on.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "reference", "_shared", "account_layers.json")

LAYERS = ("identifier", "holder", "fiduciary", "servicer", "debt_holder",
          "benefit")

# How much a field is actually known. Same vocabulary the corpus uses, because
# a reader who knows one knows the other.
EVIDENCE = ("cited", "stated_by_principal", "unverified", "not_applicable")


class CorpusUnavailable(Exception):
    pass


# The principal's OWN entries, kept out of git. See private/README.md: this
# repository is public, the schema belongs in it and the inventory does not.
PRIVATE = os.path.join(ROOT, "private", "account_layers.private.json")


def load(path=None, private=None):
    """Public schema, with the private inventory overlaid if it is there.

    TWO FILES, ONE MAP. reference/_shared/account_layers.json is committed and
    holds the schema plus generic instruments that name nobody - a passport, a
    money order. Entries describing what this principal actually holds live in
    private/, which .gitignore excludes.

    THE OVERLAY IS OPTIONAL AND ITS ABSENCE IS NORMAL. A fresh clone, and CI,
    have no private/ at all, and everything that reads the schema must work
    there - so a missing overlay is not an error. What it is NOT is silent: an
    unreadable private file raises, because "the file is not there" and "the
    file is there and broken" are opposite situations and only one of them is
    fine."""
    p = path or CORPUS
    try:
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception as e:
        raise CorpusUnavailable(f"{p}: {e}") from e
    if "accounts" not in doc:
        raise CorpusUnavailable(f"{p} has no accounts map")
    pp = private or PRIVATE
    if os.path.exists(pp):
        try:
            with open(pp, encoding="utf-8") as fh:
                overlay = json.load(fh)
        except Exception as e:
            raise CorpusUnavailable(f"{pp} exists and is unreadable: {e}") from e
        doc["accounts"].update(overlay.get("accounts") or {})
        doc["_private_overlay"] = os.path.relpath(pp, ROOT)
    return doc


# A LAYER HAS A STATUS, AND IT IS NOT OPTIONAL.
#
# The principal's correction: "an appointment that's active versus one that has
# been revoked is the difference between a claim and a gift. Without that, the
# who-owns-it map shows the structure but points you dead at dead appointments."
#
# He is right, and the consequence is worse than a stale map. A fiduciary
# appointment and a power of attorney both sit ON an account, and BOTH have to
# be revoked before title actually moves. A map that shows an appointment
# without saying whether it still binds will send somebody to revoke a thing
# that was revoked years ago, while the live one stays in place.
#
# UNKNOWN IS THE DEFAULT AND IS NOT ACTIVE. A layer whose status nobody checked
# must not read as binding - "we did not look" and "it still binds" are
# opposite findings, and only one of them is safe to act on.
LAYER_STATUS = ("active", "revoked", "superseded", "expired", "unknown",
                "never_existed")

# Statuses that still bind. Everything else is history, and history on an
# ownership map is the thing that misdirects.
BINDING = ("active",)


def layers_of(entry):
    """-> [{layer, entity, status, evidence, citation, note}] in FIXED order."""
    out = []
    for name in LAYERS:
        v = (entry.get("layers") or {}).get(name) or {}
        st = v.get("status")
        if st not in LAYER_STATUS:
            # not_applicable evidence means the layer genuinely does not exist -
            # a passport has no fiduciary. That is never_existed, not unknown.
            st = ("never_existed" if v.get("evidence") == "not_applicable"
                  else "unknown")
        out.append({
            "layer": name,
            "entity": v.get("entity"),
            "status": st,
            "binding": st in BINDING,
            "status_evidence": v.get("status_evidence", "unverified"),
            "status_as_of": v.get("status_as_of"),
            # SECTOR TRAVELS. It was dropped here and detect_inversion reads
            # it, so structure_handed_down could never fire - the detector ran,
            # found sector None, and returned nothing. A field lost at a
            # boundary takes the check that needed it with it, and the check
            # still reports a clean result.
            "sector": v.get("sector"),
            "evidence": v.get("evidence", "unverified"),
            "citation": v.get("citation"),
            "note": v.get("note"),
        })
    return out


def distinct_entities(entry):
    """-> how many DIFFERENT parties are stacked on this one account.

    Counting entities rather than layers is the point: one company can be
    fiduciary and servicer and issuer at once, and that is a different
    arrangement from three independent parties even though both have five
    layers."""
    seen = []
    for l in layers_of(entry):
        e = l["entity"]
        if e and e not in seen and l["layer"] != "benefit" and l["binding"]:
            seen.append(e)
    return seen


def dead_layers(entry):
    """-> layers that name an entity which no longer binds.

    Reported SEPARATELY from the live map rather than dropped. A revoked
    appointment is not nothing: it is a thing somebody may still believe is in
    force, and the map that hides it cannot correct them."""
    return [l for l in layers_of(entry)
            if l["entity"] and not l["binding"] and l["status"] != "never_existed"]


def must_revoke(entry):
    """-> everything that has to come off before title moves.

    The Form 56 problem: an appointment and a power of attorney can both sit on
    one account, and BOTH have to be revoked. Listing one is how the other gets
    left in place."""
    return [{"layer": l["layer"], "entity": l["entity"], "status": l["status"],
             "status_evidence": l["status_evidence"]}
            for l in layers_of(entry)
            if l["binding"] and l["layer"] in ("fiduciary", "servicer")
            and l["entity"]]


# ---------------------------------------------------------------- assignment

# THE SAME VOCABULARY THE OWNERSHIP GRAPH USES, PLUS ONE.
#
# core/ownership_graph.py already defines these six for corporate structure, and
# a reader who knows one should know the other. `never_existed` is the addition
# this layer needs and that one does not: a private company's parent is
# undisclosed, which is a fact about disclosure. An assignment chain can be
# absent because the LAW FORBIDS THE ASSIGNMENT - 38 U.S.C. 5301(a)(1) on VA
# benefits - and "we looked and found none" is a weaker statement than "none can
# exist". Collapsing those two would lose the stronger one.
TRAIL_ABSENCE = ("nothing_found", "not_checked", "incomplete", "conflicting",
                 "verified_clear", "not_disclosed", "never_existed")

# What a link may claim about itself. Same states as a layer's evidence, because
# a link IS an assertion about who held what, and it earns no softer standard.
LINK_EVIDENCE = EVIDENCE

# Whether the contract carried the notice 16 CFR 433.2 requires. This is
# RECORDED, never inferred from the kind of account: the rule makes its absence
# the seller's violation, not the consumer's loss, and guessing either way
# decides the question the field exists to ask.
HOLDER_RULE_NOTICE = ("present", "absent", "unknown", "not_applicable")


def _links(entry):
    a = entry.get("assignment") or {}
    return [l for l in (a.get("links") or []) if isinstance(l, dict)]


def assignment_trail(entry):
    """-> the chain of interest, with every break in it named.

    WHAT THIS ANSWERS: the party demanding payment today is often not the party
    the person dealt with, and between them sits a chain of assignments that
    either holds together or does not. `holder` says who claims the interest
    now. This says how they say they got it.

    A LINK EXISTS BECAUSE A DOCUMENT SAYS SO. Same rule as the ownership graph:
    not because it is the obvious next step, not because an entity is the usual
    assignee for that kind of paper. An inferred link is a false allegation
    about who may enforce a debt, and it is indistinguishable from a real one
    once it is written down.

    FIVE BREAKS, all arithmetic on recorded fields rather than judgement:

      chain_break            link N's assignee is not link N+1's assignor
      origination_mismatch   the first assignor is not the recorded originator
      terminus_disagrees     the last assignee is not the `holder` layer
      dates_out_of_order     an assignment is dated before the one it follows
      declared_state_disagrees  the entry grades itself clean and is not

    THE ENTRY DOES NOT GET TO GRADE ITSELF. A corpus entry may DECLARE its state
    - that is how `never_existed` gets said at all, since no amount of reading
    an empty list proves a statute forbids the assignment. But a declaration is
    checked against the links, and one that disagrees is itself a finding. This
    file is full of reasons why: an agent reporting `productive / high
    confidence` on a photo nothing assessed, a desk assembling its own P&L. A
    record that certifies its own chain is the same shape."""
    a = entry.get("assignment") or {}
    links = _links(entry)
    breaks = []

    declared = a.get("state")
    if declared is not None and declared not in TRAIL_ABSENCE:
        breaks.append({"kind": "undeclared_state", "detail": {"state": declared},
                       "why": f"{declared!r} is not one of {list(TRAIL_ABSENCE)}"})
        declared = None

    for i in range(len(links) - 1):
        here, nxt = links[i], links[i + 1]
        if here.get("to") != nxt.get("from"):
            breaks.append({
                "kind": "chain_break", "at": i + 1,
                "detail": {"link_%d_to" % i: here.get("to"),
                           "link_%d_from" % (i + 1): nxt.get("from")},
                "why": ("The interest leaves one party and arrives from "
                        "another. Somewhere between them is a transfer nobody "
                        "recorded, and it is the transfer that would have to be "
                        "proved."),
            })
        d1, d2 = here.get("date"), nxt.get("date")
        if d1 and d2 and str(d2) < str(d1):
            breaks.append({
                "kind": "dates_out_of_order", "at": i + 1,
                "detail": {"earlier_link": d1, "later_link": d2},
                "why": ("An assignment dated before the one it follows. Either "
                        "a date is wrong or the order is, and both change who "
                        "held the paper when."),
            })

    originator = entry.get("originated_by") or a.get("originated_by")
    if links and originator and links[0].get("from") != originator:
        breaks.append({
            "kind": "origination_mismatch",
            "detail": {"originated_by": originator,
                       "first_assignor": links[0].get("from")},
            "why": ("The chain starts from someone other than the party the "
                    "person contracted with. The first link is the one that "
                    "carries the consumer's own transaction into it."),
        })

    holder = {l["layer"]: l for l in layers_of(entry)}["holder"]
    terminus = links[-1].get("to") if links else None
    if links and holder.get("entity") and terminus != holder["entity"]:
        breaks.append({
            "kind": "terminus_disagrees_with_holder",
            "detail": {"chain_ends_at": terminus, "holder_layer": holder["entity"]},
            "why": ("The map and the chain name different parties as holding "
                    "the interest. One of them is out of date and nothing here "
                    "can say which - that is a document question."),
        })

    unevidenced = [i for i, l in enumerate(links)
                   if l.get("evidence") not in ("cited", "stated_by_principal")]

    # Derived state. Computed BEFORE the declaration is consulted.
    if breaks:
        derived = "conflicting"
    elif unevidenced:
        derived = "incomplete"
    elif links:
        derived = "verified_clear"
    elif "assignment" not in entry:
        derived = "not_checked"
    else:
        derived = a.get("state") or "not_checked"

    state = derived
    if declared in ("never_existed", "not_disclosed", "nothing_found"):
        # Only a declaration with a citation may say what the links cannot
        # show. Without one it is an assertion, and an unsourced assertion in
        # this field is the thing the field exists to catch.
        if not a.get("citation"):
            breaks.append({
                "kind": "declared_state_disagrees",
                "detail": {"declared": declared, "citation": None},
                "why": ("An absence this strong is a claim about the law or "
                        "about disclosure, and it needs a source. Without one "
                        "the honest state is not_checked."),
            })
            state = "not_checked"
        elif links:
            breaks.append({
                "kind": "declared_state_disagrees",
                "detail": {"declared": declared, "links_recorded": len(links)},
                "why": ("The entry says no assignment exists and records "
                        "assignments."),
            })
            state = "conflicting"
        else:
            state = declared

    notice = a.get("holder_rule_notice", "unknown")
    if notice not in HOLDER_RULE_NOTICE:
        notice = "unknown"

    return {
        "state": state,
        "declared_state": a.get("state"),
        "derived_state": derived,
        "links": [{"from": l.get("from"), "to": l.get("to"),
                   "date": l.get("date"), "instrument": l.get("instrument"),
                   "evidence": l.get("evidence", "unverified"),
                   "citation": l.get("citation"), "note": l.get("note")}
                  for l in links],
        "link_count": len(links),
        "unevidenced_links": unevidenced,
        "originated_by": originator,
        "ends_at": terminus,
        "breaks": breaks,
        "holder_rule_notice": notice,
        "holder_rule_note": (
            "16 CFR 433.2 requires a consumer credit contract to carry the "
            "notice making ANY HOLDER subject to the claims and defenses the "
            "debtor could assert against the seller - and the same notice caps "
            "recovery at amounts the debtor actually paid. Whether this "
            "contract carries it is recorded here and is never inferred from "
            "the kind of account."),
        "not_a_conclusion": (
            "A chain read off recorded links. Whether any assignment was "
            "effective, and what rides along it, is Legal's question."),
        "citation": a.get("citation"),
        "note": a.get("note"),
    }


def holds_the_interest(entry):
    """-> who may enforce this account today, and whether the chain agrees.

    Two independent records answer the same question - the `holder` layer and
    the end of the assignment chain - and they are reported TOGETHER with a
    flag rather than reconciled into one. Reconciling them would pick a winner
    silently, and which of the two is stale is exactly the thing somebody needs
    to go and find out."""
    holder = {l["layer"]: l for l in layers_of(entry)}["holder"]
    trail = assignment_trail(entry)
    ends = trail["ends_at"]
    return {
        "entity": holder.get("entity"),
        "status": holder.get("status"),
        "binding": holder.get("binding"),
        "evidence": holder.get("evidence"),
        "citation": holder.get("citation"),
        "note": holder.get("note"),
        "chain_ends_at": ends,
        "chain_state": trail["state"],
        "agrees_with_chain": (None if not trail["links"] or not holder.get("entity")
                              else ends == holder["entity"]),
        "why_both": ("The layer is who the record says holds it. The chain is "
                     "how they say they got it. An account where those "
                     "disagree is the ordinary case in assigned consumer "
                     "paper, not an exotic one."),
    }


# -------------------------------------------------------------------- chains

# FOUR KINDS OF EDGE, AND THEY ARE NOT INTERCHANGEABLE.
#
# The principal described a military-benefits map out loud as one chain -
# "Treasury to DEERS, DEERS to Navy, Navy to the account... DEERS to VA, DEERS
# to SGLI". Read as a single chain it says money flows through a personnel
# database, which it does not. Split by KIND it is substantially right, and the
# split is the thing worth recording:
#
#   eligibility   establishes that a person QUALIFIES. DEERS is this and only
#                 this - an enrollment and eligibility record. No money moves
#                 along an eligibility edge, ever.
#   disbursement  money actually moving. Treasury funds a disbursing agency
#                 (DFAS for military pay, VA for compensation) and the agency
#                 pays the person.
#   deduction     money moving OUT of a payment before it lands. SGLI is this:
#                 38 U.S.C. 1969(a)(1) deducts the premium from the member's
#                 basic pay. The edge runs pay -> insurer, which is the
#                 OPPOSITE direction from the eligibility edge that qualified
#                 them for the policy.
#   assignment    the INTEREST itself changing hands. Recorded in the
#                 `assignment` block, because a chain of interest has breaks,
#                 a terminus and a holder to agree with, and these do not.
#
# WHY THE DISTINCTION IS LOAD-BEARING RATHER THAN TIDY. Somebody tracing "who
# owes me and how did they get here" along a flattened chain arrives at a
# database and stops. Somebody who has the kinds separated can say: eligibility
# is established HERE, payment is owed BY this agency, this much is deducted
# BEFORE it arrives, and the right itself has not moved at all. Those are four
# different questions and only one of them is about ownership.
CHAIN_KINDS = ("eligibility", "disbursement", "deduction", "assignment")


def chains(entry, kind=None):
    """-> typed edges, each with its own evidence. Never inferred.

    An edge exists because something says so - a statute, an instrument, or the
    principal. `stated_by_principal` is a first-class evidence state here and
    is NOT a lesser form of `cited`: he is the person these accounts belong to
    and often the only source for how they connect. It is a different state so
    a reader can tell which edges would survive someone else checking."""
    out = []
    for e in (entry.get("chains") or []):
        if not isinstance(e, dict):
            continue
        k = e.get("kind")
        if k not in CHAIN_KINDS:
            k = "unknown"
        if kind and k != kind:
            continue
        out.append({
            "kind": k,
            "from": e.get("from"),
            "to": e.get("to"),
            "what_moves": e.get("what_moves"),
            "evidence": e.get("evidence", "unverified"),
            "citation": e.get("citation"),
            "note": e.get("note"),
        })
    return out


def payment_path(entry):
    """-> how money reaches this account, and what leaves it on the way.

    Disbursement and deduction reported together because a person asking what
    they receive is asking about both, and a path that shows only the inflow
    overstates it by exactly the deductions."""
    disb = chains(entry, "disbursement")
    ded = chains(entry, "deduction")
    elig = chains(entry, "eligibility")
    unsourced = [e for e in disb + ded if e["evidence"] == "unverified"]
    # HIS OWN STATEMENT IS A SOURCE AND IS NOT A SECOND OPINION.
    # `asserted_by is recorded and never scored` cuts both ways: the principal's
    # account of his own affairs is not discounted, and it is not corroborated
    # either. Counting it as clear would report a map nobody has checked as
    # finished; counting it as unverified would grade him below a stranger with
    # a document. It is its own row.
    principal_only = [e for e in disb + ded
                      if e["evidence"] == "stated_by_principal"]
    broken = []
    for i in range(len(disb) - 1):
        if disb[i]["to"] != disb[i + 1]["from"]:
            broken.append({
                "kind": "disbursement_gap", "at": i + 1,
                "detail": {"pays_to": disb[i]["to"],
                           "next_paid_by": disb[i + 1]["from"]},
                "why": ("Money leaves one party and arrives from another. The "
                        "hop between them is unrecorded."),
            })
    if not disb:
        state = "not_checked"
    elif broken:
        state = "conflicting"
    elif unsourced or principal_only:
        state = "incomplete"
    else:
        state = "verified_clear"
    return {
        "state": state,
        "disbursement": disb,
        "deduction": ded,
        "eligibility": elig,
        "breaks": broken,
        "unsourced_edges": len(unsourced),
        "edges_on_principal_statement": [
            {"from": e["from"], "to": e["to"], "kind": e["kind"]}
            for e in principal_only],
        "eligibility_is_not_payment": (
            "Eligibility edges are listed and are deliberately NOT part of the "
            "path. A record system can decide whether someone qualifies and "
            "still never touch a dollar; putting it in the money path is how a "
            "database ends up looking like a payer."),
        "not_a_conclusion": (
            "Edges as recorded. Whether any payment was correctly computed is "
            "Accounting's question, and whether any of it was lawfully "
            "withheld is Legal's."),
    }


def detect_inversion(entry):
    """-> [findings]. Traced from the fields, never argued.

    Two inversions, and they are mirror images:

      privatised_benefit   something that exists to deliver a public benefit
                           has a private servicer taking a fee out of it and a
                           private party holding the paper.
      structure_handed_down a corporate form is presented to a person as
                           though it were theirs, while the duties and the
                           obligation stayed with the corporation - or worse,
                           the obligation moved to the person and the control
                           did not.

    Each finding names the fields it was read from. An inversion nobody can
    trace back to a field is an argument, and this module does not make
    arguments."""
    L = {l["layer"]: l for l in layers_of(entry)}
    found = []

    label = (entry.get("label") or "").lower()
    ben = L["benefit"]
    fid, srv, debt = L["fiduciary"], L["servicer"], L["debt_holder"]

    # 1. A PUBLIC BENEFIT WITH A PRIVATE SERVICER AND PRIVATE PAPER.
    if (entry.get("benefit_origin") == "public" and srv.get("entity")
            and srv.get("sector") == "private"):
        found.append({
            "inversion": "privatised_benefit",
            "read_from": ["benefit_origin", "servicer", "debt_holder"],
            "detail": {"benefit": ben.get("entity"),
                       "servicer": srv.get("entity"),
                       "debt_holder": debt.get("entity")},
            "why": ("A benefit that originates as a public entitlement is being "
                    "delivered through a private servicer. That is not "
                    "automatically improper - it is how most benefit delivery "
                    "works - but it means the fee, the float and the failure "
                    "mode sit with a private party while the entitlement is "
                    "public."),
            "not_a_conclusion": ("Traced from the fields. Whether it is lawful "
                                 "or contractual is Legal's question, not this "
                                 "module's."),
        })

    # 2. THE LABEL CLAIMS A FIDUCIARY THAT THE LAYERS DO NOT SHOW.
    if any(w in label for w in ("trust", "fiduciary", "estate")):
        holder = (entry.get("funding_obligation_holder")
                  or debt.get("entity"))
        principal_funded = entry.get("funded_by") == "the person"
        if principal_funded and holder and holder == entry.get("funded_by"):
            found.append({
                "inversion": "custodial_product_labelled_trust",
                "read_from": ["label", "funded_by", "funding_obligation_holder"],
                "detail": {"label": entry.get("label"),
                           "funded_by": entry.get("funded_by"),
                           "funding_obligation_holder": holder,
                           "fiduciary": fid.get("entity")},
                "why": ("The label says trust. The funding obligation never "
                        "transferred - the person who funded it still carries "
                        "it - so no settlor parted with anything and no "
                        "beneficiary gained a claim against a fund. That is a "
                        "custodial arrangement with a trust-shaped name."),
                "not_a_conclusion": ("A structural reading of the recorded "
                                     "fields. Whether any instrument in fact "
                                     "created a trust is decided by reading "
                                     "the instrument, which Trust owns."),
            })

    # 3. A CORPORATE FORM HANDED TO A PERSON WHO CARRIES ITS PAPER.
    if fid.get("entity") and fid.get("sector") == "corporate" \
            and debt.get("entity") == "the person" \
            and entry.get("control_holder") not in ("the person", None):
        found.append({
            "inversion": "structure_handed_down",
            "read_from": ["fiduciary", "debt_holder", "control_holder"],
            "detail": {"fiduciary": fid.get("entity"),
                       "control_holder": entry.get("control_holder"),
                       "debt_holder": debt.get("entity")},
            "why": ("The obligation sits with the person and the control does "
                    "not. Duty and control coming apart is the condition every "
                    "fiduciary doctrine exists to address, and it is worth "
                    "naming wherever it appears."),
            "not_a_conclusion": "Traced, not argued.",
        })
    return found


def trace(account_id, path=None):
    """-> the full four-layer map for one account. Deterministic."""
    doc = load(path)
    entry = (doc.get("accounts") or {}).get(account_id)
    if not entry:
        return {"account": account_id, "found": False,
                "absence_state": "nothing_found",
                "why": (f"{account_id!r} has no entry. The schema takes any "
                        f"account type and this one has not been recorded - "
                        f"which is a gap in the corpus, not a finding about "
                        f"the account."),
                "known_accounts": sorted((doc.get("accounts") or {}))}
    ls = layers_of(entry)
    stacked = distinct_entities(entry)
    unverified = [l["layer"] for l in ls if l["evidence"] == "unverified"]
    # Same distinction one level up. An entry built entirely from what the
    # principal said is a real entry with a real source, and it has not been
    # checked against a document - `incomplete` says both.
    from_principal = [l["layer"] for l in ls
                      if l["evidence"] == "stated_by_principal"]
    return {
        "account": account_id,
        "found": True,
        "label": entry.get("label"),
        "label_note": ("The label is recorded and is NOT one of the layers. It "
                       "is evidence about how the thing is presented, not about "
                       "who holds what."),
        "schema_version": doc.get("schema_version"),
        "entry_version": entry.get("version"),
        "layers": ls,
        "entities_stacked": stacked,
        "entities_stacked_count": len(stacked),
        "carries_the_debt": next((l["entity"] for l in ls
                                  if l["layer"] == "debt_holder"), None),
        # THE OTHER SIDE. `carries_the_debt` is the obligor; this is who may
        # enforce against them. A map with only one of the two answers half the
        # question somebody actually has, which is usually "who is this company
        # writing to me, and how did they get my contract".
        "holds_the_interest": holds_the_interest(entry),
        "assignment": assignment_trail(entry),
        "payment_path": payment_path(entry),
        "inversions": detect_inversion(entry),
        # SURFACED, NOT BURIED. A layer whose status is unknown is the one a
        # reader must chase - it names an entity and cannot say whether that
        # entity still binds. Counting it as binding would overstate the map;
        # hiding it would understate the work left.
        "unverified_status": [
            {"layer": l["layer"], "entity": l["entity"],
             "why": "named an entity; whether it still binds was not checked"}
            for l in ls if l["entity"] and l["status"] == "unknown"],
        "revoked_or_expired": dead_layers(entry),
        "must_revoke_before_title_moves": must_revoke(entry),
        "status_note": ("`entities_stacked` counts only layers that BIND. An "
                        "appointment that was revoked is not a lighter version "
                        "of one in force - it is the difference between a claim "
                        "and a gift, and a map that shows structure without "
                        "status points at dead appointments."),
        "unverified_layers": unverified,
        "layers_on_principal_statement": from_principal,
        "absence_state": ("incomplete" if unverified or from_principal
                          else "verified_clear"),
        "absence_note": ("`incomplete` covers two different gaps and names "
                         "which: a layer nobody filled, and a layer filled "
                         "from what the principal said and not yet checked "
                         "against a document. Neither is a failing entry - the "
                         "second is often the only record that exists."),
        "citations": sorted({l["citation"] for l in ls if l["citation"]}),
    }
