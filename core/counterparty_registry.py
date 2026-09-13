#!/usr/bin/env python3
"""Who the other side is. Established from evidence, never from resemblance.

    from core.counterparty_registry import record, link_identity, same_as
    record("bank_a", kind="bank", legal_name="Example Bank, N.A.",
           evidence="cited", document_ref="statement 2026-07 p.1")

PHASE 1A.2. The asset registry knows an account exists; this knows who holds
it, services it, is owed by it or regulates it - and those are different
parties even when one company does several of them, which is the same lesson
core/account_model.py learned about layers.

THE ONE RULE THIS MODULE IS BUILT AROUND

    "Looks like the same company" is not evidence.

Identity resolution by resemblance is how a registry becomes elaborate
fiction wearing a tie. "Example Bank", "Example Bank NA", "Example Bancorp"
and "EXAMPLE BK" may be one entity, four entities, or a parent and three
subsidiaries with genuinely different obligations - and a servicer is
routinely a different company from the one whose name is on the card. Merging
them because the strings are close produces a map that reads beautifully and
misroutes every notice.

So there is NO fuzzy matcher here, and there is no code path that merges two
records without a document. `link_identity` requires evidence and a citation
and refuses without them. `same_as` REPORTS a similarity as a QUESTION for a
person, and cannot act on it - it returns candidates and says explicitly that
it has established nothing.

KINDS ARE ROLES, AND ONE PARTY CAN HOLD SEVERAL. A bank that is also the
servicer of its own paper is one counterparty with two roles, not two
counterparties - and a servicer that is a different company is two parties
even though the statement carries one logo. The role belongs on the EDGE to
the asset, not on the party. This module records what a party IS; the edges
record what it DOES for which asset.

IDENTIFIERS HERE ARE PUBLIC BUSINESS ONES. A CIK, an NMLS number, a state
registration - these identify an organisation and are published to identify
it. The principal's identifiers are refused by the shared guard exactly as
everywhere else: this is a registry of counterparties, not a place to record
his account number under a bank's name.
"""
import json
import os
import re
from datetime import datetime, timezone

from core.asset_registry import (EVIDENCE, ESTABLISHED, Refused,  # noqa: F401
                                 guard_fields)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "private", "counterparties.json")

# What a party IS. Its ROLE for a given asset lives on the edge, because one
# company is routinely the holder of one account and the servicer of another.
KINDS = ("bank", "credit_union", "servicer", "creditor", "dealer", "lessor",
         "landlord", "insurer", "government_agency", "employer", "trustee",
         "fiduciary", "law_firm", "utility", "individual", "other", "unknown")

# Identifiers an ORGANISATION publishes about itself. Never the principal's.
PUBLIC_IDENTIFIERS = ("cik", "nmls", "state_registration", "lei", "duns",
                      "charter_number", "routing_public")

STATUS = ("active", "dissolved", "merged", "renamed", "unknown")


def _now():
    return datetime.now(timezone.utc).isoformat()


def load(path=None):
    p = path or STORE
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"schema_version": "1.0", "counterparties": {}}
    except Exception as exc:
        raise Refused(f"{p} exists and is unreadable: {exc}") from exc


def _save(doc, path=None):
    p = path or STORE
    # BORN 0700, not fixed later. os.makedirs applies the process umask, so
    # the same line produced 0775 under this service manager - and 1,507
    # files sat group- and world-readable until somebody audited. A private
    # store that depends on the umask it happened to inherit is not private.
    from core.fs_boundary import ensure_dir
    ensure_dir(os.path.dirname(p), 0o700)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
    os.chmod(p, 0o600)


def record(party_id, kind, evidence="unknown", path=None, **fields):
    """Add or amend one party. Refuses before it writes."""
    if kind not in KINDS:
        raise Refused(
            f"{kind!r} is not a counterparty kind: {list(KINDS)}. A servicer "
            f"is not a creditor and a landlord is not a lessor - choosing "
            f"deliberately is the point of the field.")
    if evidence not in EVIDENCE:
        raise Refused(f"{evidence!r} is not an evidence state: {list(EVIDENCE)}")
    if evidence in ESTABLISHED and not (fields.get("document_ref")
                                        or fields.get("citation")):
        raise Refused(
            f"evidence={evidence!r} claims a document established this party "
            f"and none is referenced. `cited` without a citation is the "
            f"strongest word in this vocabulary attached to nothing.")
    guard_fields(fields)

    doc = load(path)
    rec = doc["counterparties"].get(party_id) or {
        "party_id": party_id, "created": _now(), "aliases": [],
        "identity_links": [], "status": "unknown",
    }
    rec.update(fields)
    rec["kind"] = kind
    rec["evidence"] = evidence
    rec["updated"] = _now()
    if rec.get("status") not in STATUS:
        rec["status"] = "unknown"
    doc["counterparties"][party_id] = rec
    _save(doc, path)
    return rec


def add_alias(party_id, alias, evidence, document_ref, path=None):
    """A name this party is ALSO known by - from a document, not a guess.

    An alias is a claim that two strings denote one party, which is the same
    claim link_identity makes, so it earns the same standard."""
    if evidence not in ESTABLISHED:
        raise Refused(
            f"an alias needs {list(ESTABLISHED)} evidence; {evidence!r} is a "
            f"belief that two names are the same party, which is precisely "
            f"what must not be recorded on a hunch.")
    if not document_ref:
        raise Refused("an alias needs the document that shows both names")
    doc = load(path)
    rec = doc["counterparties"].get(party_id)
    if not rec:
        raise Refused(f"{party_id!r} is not in the registry")
    rec.setdefault("aliases", []).append(
        {"name": alias, "evidence": evidence, "document_ref": document_ref,
         "at": _now()})
    rec["updated"] = _now()
    _save(doc, path)
    return rec


def link_identity(a, b, evidence, citation, note="", path=None):
    """Assert that two records are ONE party. Requires a document.

    THE MERGE THAT MUST NEVER HAPPEN ON RESEMBLANCE. Two counterparty records
    becoming one changes who is owed, who must be noticed, and who a claim
    runs against. A wrong merge does not look wrong afterwards - it looks like
    a tidier database.

    There is deliberately no `force` and no similarity threshold. The evidence
    is a filing, a merger notice, a statement showing both names, or the
    principal saying so - and which of those it was is recorded, because
    `stated_by_principal` and `cited` support different weight."""
    if evidence not in EVIDENCE:
        raise Refused(f"{evidence!r} is not an evidence state")
    if evidence not in ESTABLISHED and evidence != "stated_by_principal":
        raise Refused(
            f"linking two identities on {evidence!r} is not permitted. It "
            f"needs a document, or the principal saying so - `derived` and "
            f"`unknown` are exactly the states that produce a plausible "
            f"merge nobody can trace.")
    if not citation:
        raise Refused(
            "an identity link needs a citation. 'Looks like the same company' "
            "is not evidence; it is how a registry becomes elaborate fiction "
            "wearing a tie.")
    doc = load(path)
    for pid in (a, b):
        if pid not in doc["counterparties"]:
            raise Refused(f"{pid!r} is not in the registry")
    link = {"with": b, "evidence": evidence, "citation": citation,
            "note": note, "at": _now()}
    doc["counterparties"][a].setdefault("identity_links", []).append(link)
    doc["counterparties"][b].setdefault("identity_links", []).append(
        dict(link, with_=a, **{"with": a}))
    for pid in (a, b):
        doc["counterparties"][pid]["updated"] = _now()
    # NOT MERGED. Both records survive with a link between them, because a
    # merge destroys the fact that two documents named two things - and if the
    # link turns out to be wrong, an un-merge cannot restore what was lost.
    _save(doc, path)
    return {"linked": [a, b], "merged": False,
            "why_not_merged": (
                "Both records are kept. A merge destroys the fact that two "
                "sources named two parties, and an incorrect merge cannot be "
                "undone by splitting - the provenance is already gone.")}


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def same_as(party_id, path=None):
    """-> candidates that RESEMBLE this one. A QUESTION, never a finding.

    This exists because a person reviewing the registry should be able to see
    what might need linking. It returns nothing actionable: no score is used
    anywhere, nothing calls link_identity with its output, and every result
    carries the reminder that resemblance establishes nothing.

    The separation is the point. Finding candidates is useful; acting on them
    is the failure mode."""
    doc = load(path)
    me = doc["counterparties"].get(party_id)
    if not me:
        raise Refused(f"{party_id!r} is not in the registry")
    mine = _norm(me.get("legal_name") or party_id)
    out = []
    for pid, rec in doc["counterparties"].items():
        if pid == party_id:
            continue
        theirs = _norm(rec.get("legal_name") or pid)
        shared = set(mine.split()) & set(theirs.split())
        if shared:
            out.append({"party_id": pid,
                        "legal_name": rec.get("legal_name"),
                        "shared_tokens": sorted(shared),
                        "already_linked": any(
                            l.get("with") == pid
                            for l in me.get("identity_links") or [])})
    return {
        "candidates": out,
        "established": False,
        "what_this_is": (
            "Names that share a word. That is ALL this is. A servicer is "
            "routinely a different company from the one whose name is on the "
            "card, and a parent and its subsidiary share almost every word "
            "while owing different duties."),
        "what_to_do": (
            "Find a document naming both, then call link_identity with it. "
            "Nothing here may be used to merge records automatically, and no "
            "code path does."),
    }


def get(party_id, path=None):
    return load(path)["counterparties"].get(party_id)


def unresolved(path=None):
    """-> parties still resting on something weaker than a document."""
    return [{"party_id": p, "kind": r.get("kind"), "evidence": r.get("evidence"),
             "status": r.get("status")}
            for p, r in sorted(load(path)["counterparties"].items())
            if r.get("evidence") not in ESTABLISHED
            or r.get("status") == "unknown"]
