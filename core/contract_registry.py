#!/usr/bin/env python3
"""The instrument that governs an account, and what it actually says.

    from core.contract_registry import record, clause, assignability
    record("nmac_2019", kind="retail_installment",
           parties=["dealer_x", "nmac"], evidence="cited",
           document_ref="contract 2019-03-14")

PHASE 1A.3. The asset registry knows an account exists and the counterparty
registry knows who is on the other side. This holds the document that decides
what either of them may do - and CLAUDE.md's own distinction applies: a signed
contract is not automatically a lawful one, and execution is not enforceability.

THE RULE THIS MODULE IS BUILT AROUND

    A clause nobody found is not a clause that does not exist.

"No assignment restriction was located" and "this contract permits assignment"
are opposite findings that look identical in a database storing only the
conclusion. The first is a gap; the second is a right. So every clause is
recorded with an EXPLICIT state -

    present      found, with where it is
    absent       READ THE WHOLE DOCUMENT and it is not there
    not_read     nobody looked at this document yet
    unreadable   looked, and could not resolve it

- and `not_read` blocks exactly as `unknown` blocks everywhere else. The
system does not get to infer freedom from its own failure to look.

A CLAUSE NEEDS A LOCATION. `present` without a page or section is an assertion
about a document nobody can check, which is the same standing as a citation
the agent cannot open - and CLAUDE.md already refuses those: a provision the
agent cannot locate cannot support anything.

THIS DOES NOT DECIDE TRANSFERABILITY. `assignability()` reports what the
clauses say and stops. Whether a particular transfer is lawful depends on the
statute over the contract - a VA benefit is non-assignable no matter what any
instrument says, and core/financial_authority.py owns that. Two layers, and
the statute is the outer one.
"""
import json
import os
from datetime import datetime, timezone

from core.asset_registry import (EVIDENCE, ESTABLISHED, Refused,  # noqa: F401
                                 guard_fields)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "private", "contracts.json")

KINDS = ("retail_installment", "loan", "lease", "deposit_agreement",
         "card_agreement", "insurance_policy", "service_agreement",
         "employment", "settlement", "trust_instrument", "assignment",
         "guaranty", "other", "unknown")

# The clauses that decide what may move and who must be told.
CLAUSES = ("assignment", "change_of_control", "termination", "renewal",
           "notice", "payment", "security_interest", "governing_law",
           "dispute_resolution", "beneficiary", "transfer_restriction",
           "consent_required")

# WHAT IS KNOWN ABOUT A CLAUSE. `absent` is a finding and requires that the
# document was actually read; `not_read` is the honest default and blocks.
CLAUSE_STATE = ("present", "absent", "not_read", "unreadable")
BLOCKING_CLAUSE_STATES = ("not_read", "unreadable")

ASSIGNABILITY = ("assignable", "assignable_with_consent", "requires_novation",
                 "not_assignable", "unknown")


def _now():
    return datetime.now(timezone.utc).isoformat()


def load(path=None):
    p = path or STORE
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"schema_version": "1.0", "contracts": {}}
    except Exception as exc:
        raise Refused(f"{p} exists and is unreadable: {exc}") from exc


def _save(doc, path=None):
    p = path or STORE
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
    os.chmod(p, 0o600)


def record(contract_id, kind, evidence="unknown", path=None, **fields):
    if kind not in KINDS:
        raise Refused(f"{kind!r} is not a contract kind: {list(KINDS)}")
    if evidence not in EVIDENCE:
        raise Refused(f"{evidence!r} is not an evidence state: {list(EVIDENCE)}")
    if evidence in ESTABLISHED and not fields.get("document_ref"):
        raise Refused(
            f"evidence={evidence!r} says a document establishes this contract "
            f"and none is referenced. A contract recorded without its "
            f"instrument is a memory of one.")
    guard_fields(fields)

    doc = load(path)
    rec = doc["contracts"].get(contract_id) or {
        "contract_id": contract_id, "created": _now(),
        # EVERY CLAUSE STARTS not_read. Not absent - nobody has looked, and
        # the difference between those two is the whole point of this module.
        "clauses": {c: {"state": "not_read"} for c in CLAUSES},
    }
    rec.update(fields)
    rec["kind"] = kind
    rec["evidence"] = evidence
    rec["updated"] = _now()
    doc["contracts"][contract_id] = rec
    _save(doc, path)
    return rec


def clause(contract_id, name, state, location=None, text_ref=None,
           note="", path=None):
    """Record what was found - or that nothing was, having looked."""
    if name not in CLAUSES:
        raise Refused(f"{name!r} is not a tracked clause: {list(CLAUSES)}")
    if state not in CLAUSE_STATE:
        raise Refused(f"{state!r} is not a clause state: {list(CLAUSE_STATE)}")
    if state == "present" and not location:
        raise Refused(
            "a clause recorded as present needs its LOCATION - a page, a "
            "section, a paragraph. Without one it is an assertion about a "
            "document nobody can check, which is the same standing as a "
            "citation that cannot be opened.")
    if state == "absent" and not note:
        raise Refused(
            "recording a clause ABSENT is a finding that the whole document "
            "was read and it is not there. Say what was read. 'Not found' "
            "without that is `not_read` wearing a stronger word.")
    doc = load(path)
    rec = doc["contracts"].get(contract_id)
    if not rec:
        raise Refused(f"{contract_id!r} is not in the registry")
    rec["clauses"][name] = {"state": state, "location": location,
                            "text_ref": text_ref, "note": note, "at": _now()}
    rec["updated"] = _now()
    _save(doc, path)
    return rec


def assignability(contract_id, path=None):
    """-> what the CONTRACT says about moving. Not whether a transfer is lawful.

    The statute sits above this. A VA benefit is non-assignable whatever any
    instrument provides, and core/financial_authority.py owns that question -
    two layers, and the outer one wins."""
    doc = load(path)
    rec = doc["contracts"].get(contract_id)
    if not rec:
        return {"state": "unknown", "blocking": True,
                "why": f"{contract_id!r} is not in the registry"}
    a = rec["clauses"].get("assignment") or {"state": "not_read"}
    consent = rec["clauses"].get("consent_required") or {"state": "not_read"}
    restrict = rec["clauses"].get("transfer_restriction") or {"state": "not_read"}

    unread = [n for n, c in (("assignment", a), ("consent_required", consent),
                             ("transfer_restriction", restrict))
              if c["state"] in BLOCKING_CLAUSE_STATES]
    if unread:
        return {
            "state": "unknown", "blocking": True, "clauses_unread": unread,
            "why": (f"{unread} {'has' if len(unread) == 1 else 'have'} not "
                    f"been read. A clause nobody found is not a clause that "
                    f"does not exist - the system does not get to infer "
                    f"freedom from its own failure to look."),
        }
    if restrict["state"] == "present" or a["state"] == "present":
        state = ("assignable_with_consent" if consent["state"] == "present"
                 else "not_assignable")
        return {"state": state, "blocking": state == "not_assignable",
                "why": "a restriction or an assignment clause is present",
                "locations": [c.get("location") for c in (a, restrict, consent)
                              if c.get("location")]}
    return {
        "state": "assignable", "blocking": False,
        "why": ("The document was read and carries no assignment clause, no "
                "transfer restriction and no consent requirement. That is a "
                "finding about THIS contract only."),
        "not_a_conclusion": (
            "Says nothing about whether a transfer is lawful. A statute can "
            "forbid what a contract permits - see "
            "core/financial_authority.py, which reads 38 U.S.C. 5301 over any "
            "instrument."),
    }


def get(contract_id, path=None):
    return load(path)["contracts"].get(contract_id)


def unread_clauses(path=None):
    """-> the work outstanding, which is the registry's real output."""
    out = []
    for cid, rec in sorted(load(path)["contracts"].items()):
        pending = [n for n, c in (rec.get("clauses") or {}).items()
                   if c.get("state") in BLOCKING_CLAUSE_STATES]
        if pending:
            out.append({"contract_id": cid, "kind": rec.get("kind"),
                        "unread": pending})
    return out
