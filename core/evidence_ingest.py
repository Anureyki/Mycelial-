#!/usr/bin/env python3
"""Document -> extracted fact -> normalized fact. And there it STOPS.

    from core.evidence_ingest import register_document, extract, correct

PHASE 1B. The purpose is not to make the system know more. It is to make it
able to say WHY it believes what it believes, with a page number.

THE LAYERS ARE SEPARATE AND THIS FILE OWNS ONLY THE FIRST THREE

    SOURCE DOCUMENT          a file, hashed
    EXTRACTED FACT           what the document SAYS, and where
    NORMALIZED FACT          the same, mapped to a registry id
    ---------------------------------------------- this module ends here
    DOMAIN INTERPRETATION    what it MEANS. Legal's or Accounting's.
    ACTIONABLE DECISION      a person's.

A statement saying "Account holder: Example Bank" establishes that the
document contains that string in that position. Whether "holder" means owner,
custodian, creditor or debtor is a legal question, and an ingestion layer that
answers it has quietly become a lawyer with a regex. So `interpret()` does not
exist here; `open_question()` does, and it records the question for the domain
that practises it.

KEYWORDS ESTABLISH NOTHING. Finding the word "assignment" does not establish
that assignment is permitted. "Holder" does not establish owner. "Authorized
user" does not establish beneficial owner. "Beneficiary" does not establish
CURRENT beneficiary. The source establishes what it says.

SEVEN KINDS OF NOT-KNOWING, kept apart because they have different fixes:

    not_checked     nobody looked at the material
    not_found       the search ran and matched nothing
    not_read        the document exists and has not been read
    unreadable      looked, and could not resolve it
    unknown         explicitly unresolved
    conflicting     sources disagree
    not_applicable  the field cannot apply here

Collapsing these into `unknown` throws away which one it is, and they are the
difference between "go and look", "it is not there" and "two documents
disagree". None becomes permission.

NOTHING IS EVER OVERWRITTEN. A correction is a new event carrying the original,
the reason, the author and the time. A second source is a second fact, not a
replacement - and two facts disagreeing is a CONFLICT, surfaced, not resolved
by whichever was written last.

TIME IS PART OF THE FACT. effective / observed / source / superseded. "What
did the system believe at time T" must be answerable, so a later statement
supersedes an earlier one by REFERENCE, and the earlier one stays.

DETERMINISM. The same document, parser and version produce the same facts. The
extractor and its version are recorded on every fact, so a later version that
extracts differently is visible as a version difference rather than as history
quietly changing.
"""
import hashlib
import json
import os
from datetime import datetime, timezone

from core.asset_registry import EVIDENCE, ESTABLISHED, Refused, guard_fields

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "private", "evidence.json")

ABSENCE = ("not_checked", "not_found", "not_read", "unreadable", "unknown",
           "conflicting", "not_applicable")

SOURCE_TYPES = ("statement", "contract", "notice", "tax_document",
                "credit_report", "correspondence", "court_document",
                "certificate", "invoice", "receipt", "screenshot", "other")

# A fact says what the document SAYS. It does not say what that means.
FACT_STATUS = ("extracted", "normalized", "superseded", "corrected",
               "conflicting")


def _now():
    return datetime.now(timezone.utc).isoformat()


def load(path=None):
    p = path or STORE
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"schema_version": "1.0", "documents": {}, "facts": [],
                "questions": []}
    except Exception as exc:
        raise Refused(f"{p} exists and is unreadable: {exc}") from exc


def _save(doc, path=None):
    p = path or STORE
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
    os.chmod(p, 0o600)


def hash_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def register_document(doc_id, source_type, path=None, file_path=None,
                      source_date=None, received=None, pages=None, note=""):
    """Record that a document exists, with its hash. Not its contents."""
    if source_type not in SOURCE_TYPES:
        raise Refused(f"{source_type!r} is not a source type: {list(SOURCE_TYPES)}")
    store = load(path)
    rec = {
        "document_id": doc_id, "source_type": source_type,
        "sha256": hash_file(file_path) if file_path else None,
        "source_date": source_date, "received": received or _now(),
        "pages": pages, "note": note, "registered": _now(),
    }
    if not rec["sha256"]:
        # A document with no hash can still be recorded - a paper letter has
        # no file - but the absence is stated rather than left blank, because
        # "cannot verify this is the same document later" is a real property.
        rec["hash_absent_because"] = (
            "no file was supplied. This document cannot be checked later for "
            "having changed, which is a fact about the evidence, not a gap in "
            "the schema.")
    store["documents"][doc_id] = rec
    _save(store, path)
    return rec


def extract(doc_id, subject, field, value, location, extractor,
            extractor_version, evidence="cited", path=None,
            original_text=None, transformations=None, effective_date=None,
            observed_date=None, confidence=None):
    """One fact the document SAYS, with where it says it.

    `location` is required and is the whole point. A fact with no page,
    section or line is an assertion about a document nobody can check, which
    is the standing CLAUDE.md already refuses for a citation the agent cannot
    open. "Because the model said so" is not provenance."""
    if not location:
        raise Refused(
            "an extracted fact needs its LOCATION in the document - a page, a "
            "section, a line. Without one the registry cannot answer 'why do "
            "you believe this' with anything a person can check.")
    if evidence not in EVIDENCE:
        raise Refused(f"{evidence!r} is not an evidence state")
    # TEMPORAL INTEGRITY IS MANDATORY, NOT A FIELD SOMEBODY MIGHT FILL.
    #
    # Accounts, contracts, benefits, debts and authority all change. A system
    # that stores only current truth is a filing cabinet with good handwriting:
    # it can say what the account looks like today and cannot say what it knew
    # last March, on what evidence, under what authority - which is the
    # question that matters when somebody asks why a decision was made.
    #
    # `effective_date` may be recorded as not_applicable, WITH A REASON. A
    # document's own date is not always the date a fact became true, and
    # pretending otherwise is how a stale statement becomes a current balance.
    if effective_date in (None, ""):
        raise Refused(
            "effective_date is required: the date this fact became true, "
            "which is NOT always the document's date. If it genuinely does "
            "not apply, pass 'not_applicable:<reason>' - a blank leaves the "
            "system unable to say what it believed at any time but now.")
    if str(effective_date).startswith("not_applicable") and \
            str(effective_date).strip() == "not_applicable":
        raise Refused(
            "effective_date='not_applicable' needs its reason: "
            "'not_applicable:<why>'")
    if not extractor or not extractor_version:
        raise Refused(
            "an extracted fact records WHICH extractor produced it and at "
            "what version. A later version that reads the same document "
            "differently must be visible as a version difference, not as "
            "history quietly changing.")
    guard_fields({"value": str(value), "original_text": original_text or ""})

    store = load(path)
    if doc_id not in store["documents"]:
        raise Refused(
            f"{doc_id!r} is not a registered document. A fact whose source was "
            f"never recorded has no provenance to trace.")
    fact = {
        "fact_id": f"F{len(store['facts']) + 1:06d}",
        "document_id": doc_id, "subject": subject, "field": field,
        "value": value, "location": location,
        "extractor": extractor, "extractor_version": extractor_version,
        "evidence": evidence, "status": "extracted",
        "original_text": original_text,
        "transformations": transformations or [],
        "effective_date": effective_date,
        # When the SYSTEM observed it, which is never the same as when it
        # became true and is what "as of time T" reads against.
        "observed_date": observed_date or _now(),
        "source_date": store["documents"][doc_id].get("source_date"),
        "extracted_at": _now(), "confidence": confidence,
        "superseded_by": None, "corrections": [],
        "means_nothing_yet": (
            "This records what the document SAYS. What it MEANS is a domain "
            "question - 'holder' is not 'owner' until Legal says which."),
    }
    store["facts"].append(fact)
    _save(store, path)
    return fact


def facts_for(subject, field=None, path=None, include_superseded=False):
    out = [f for f in load(path)["facts"]
           if f["subject"] == subject
           and (field is None or f["field"] == field)
           and (include_superseded or not f["superseded_by"])]
    return out


def conflicts(subject, path=None):
    """-> fields where sources disagree. A finding, never auto-resolved.

    Both sources are kept. Which controls is a domain question with an
    authority hierarchy behind it - and that hierarchy is not universal, so
    this module refuses to guess it."""
    out = []
    by_field = {}
    for f in facts_for(subject, path=path):
        by_field.setdefault(f["field"], []).append(f)
    for field, fs in sorted(by_field.items()):
        values = {str(f["value"]) for f in fs}
        if len(values) > 1:
            out.append({
                "subject": subject, "field": field,
                "absence_state": "conflicting",
                "sources": [{"fact_id": f["fact_id"],
                             "document_id": f["document_id"],
                             "value": f["value"], "location": f["location"],
                             "source_date": f["source_date"]} for f in fs],
                "resolved": False,
                "why_not_resolved": (
                    "Both sources are preserved. Which controls depends on an "
                    "authority hierarchy that is not universal - a statute "
                    "over a contract over a statement is common and is not a "
                    "rule this module may assume. The domain decides."),
            })
    return out


def supersede(old_fact_id, new_fact_id, reason, path=None, at=None):
    """A later fact replaces an earlier one BY REFERENCE. Nothing is deleted."""
    store = load(path)
    ids = {f["fact_id"]: f for f in store["facts"]}
    for fid in (old_fact_id, new_fact_id):
        if fid not in ids:
            raise Refused(f"{fid!r} is not a recorded fact")
    if not reason:
        raise Refused("supersession needs a reason - otherwise the history "
                      "shows a fact vanishing with no account of why")
    ids[old_fact_id]["superseded_by"] = new_fact_id
    # WHEN the supersession happened, not when it was typed. Stamping every
    # supersession "now" means a fact learned to be stale in July still reads
    # as live for every point-in-time query before today - the past gets
    # reconstructed with today's knowledge in it, which is precisely what
    # as_of() exists to prevent.
    ids[old_fact_id]["superseded_at"] = at or _now()
    ids[old_fact_id]["superseded_recorded_at"] = _now()
    ids[old_fact_id]["superseded_reason"] = reason
    ids[old_fact_id]["status"] = "superseded"
    _save(store, path)
    return ids[old_fact_id]


def correct(fact_id, new_value, reason, author, path=None):
    """A human correction. The ORIGINAL SURVIVES.

    An extraction that was wrong is evidence about the extractor, and
    overwriting it destroys the only record that it ever misread anything."""
    if not reason or not author:
        raise Refused("a correction records its reason and its author. A "
                      "value that changed with neither is indistinguishable "
                      "from a bug.")
    store = load(path)
    ids = {f["fact_id"]: f for f in store["facts"]}
    if fact_id not in ids:
        raise Refused(f"{fact_id!r} is not a recorded fact")
    f = ids[fact_id]
    f.setdefault("corrections", []).append({
        "from": f["value"], "to": new_value, "reason": reason,
        "author": author, "at": _now(),
        "original_preserved": True,
    })
    f["value"] = new_value
    f["status"] = "corrected"
    f["evidence"] = "stated_by_principal" if author != "system" else f["evidence"]
    _save(store, path)
    return f


def open_question(subject, question, for_domain, raised_by, path=None,
                  fact_ids=None):
    """Hand the domain question to the domain. This module does not answer it.

    The ingestion layer's most important refusal: a statement saying "holder"
    has established a string in a position, not an ownership interest."""
    store = load(path)
    q = {"subject": subject, "question": question, "for_domain": for_domain,
         "raised_by": raised_by, "fact_ids": fact_ids or [],
         "raised_at": _now(), "answered": False,
         "why_not_answered_here": (
             "Ingestion records what a document says. What it means is "
             "practised by a domain - and an ingestion layer that answered "
             "would be a lawyer with a regex.")}
    store["questions"].append(q)
    _save(store, path)
    return q


def absence(subject, field, state, why, path=None):
    """Record WHICH kind of not-knowing this is. None of them is permission."""
    if state not in ABSENCE:
        raise Refused(
            f"{state!r} is not an absence state: {list(ABSENCE)}. They have "
            f"different fixes - 'go and look', 'it is not there' and 'two "
            f"documents disagree' are not one finding.")
    if not why:
        raise Refused("an absence needs its reason, or it is just a blank")
    store = load(path)
    store.setdefault("absences", []).append(
        {"subject": subject, "field": field, "absence_state": state,
         "why": why, "at": _now(), "is_permission": False})
    _save(store, path)
    return store["absences"][-1]


def record_authority(subject, state, who, why, path=None, at=None):
    """Append one authority change. What was PERMITTED, and when.

    The third axis of the question. Knowing what the system believed and on
    what evidence still does not say what it was allowed to do at that moment
    - and "why was this decision made" is unanswerable without all three.

    Append-only. An authority state that was later revoked must still be
    visible as having been in force, or the record shows a permission that
    never existed rather than one that ended."""
    if not (state and who and why):
        raise Refused("an authority record needs the state, who set it, and "
                      "why. Two of the three is a permission with no author.")
    store = load(path)
    store.setdefault("authority", []).append(
        {"subject": subject, "state": state, "who": who, "why": why,
         "at": at or _now()})
    _save(store, path)
    return store["authority"][-1]


def as_of(subject, when, field=None, path=None):
    """-> what the system BELIEVED about this subject at time `when`.

    Observed on or before `when`, and not superseded on or before `when`. A
    fact superseded later was still the live belief then, and reconstructing
    the past from today's live set would show the system having always known
    what it only learned afterwards."""
    store = load(path)
    facts = {f["fact_id"]: f for f in store["facts"]}
    out = []
    for f in store["facts"]:
        if f["subject"] != subject or (field and f["field"] != field):
            continue
        if (f.get("observed_date") or "") > when:
            continue                      # not yet known at that time
        sup = f.get("superseded_by")
        if sup:
            s_at = f.get("superseded_at") or ""
            if s_at and s_at <= when:
                continue                  # already replaced by then
        out.append(f)
    return out


def knowledge_at(subject, when, path=None):
    """-> what was believed, on what evidence, and what was authorized. At T.

    The whole point of temporal integrity, in one call. Anything that cannot
    be reconstructed says so rather than being omitted - an absent section
    reads as "nothing was happening", which is a different claim from "this
    was not recorded"."""
    store = load(path)
    believed = as_of(subject, when, path=path)
    docs = store.get("documents", {})
    evidence = []
    for f in believed:
        d = docs.get(f["document_id"], {})
        evidence.append({
            "fact_id": f["fact_id"], "field": f["field"], "value": f["value"],
            "document": f["document_id"], "source_type": d.get("source_type"),
            "sha256": d.get("sha256"), "location": f["location"],
            "evidence": f["evidence"],
            "effective_date": f.get("effective_date"),
            "observed_date": f.get("observed_date"),
        })
    auth = [a for a in store.get("authority", [])
            if a["subject"] == subject and a["at"] <= when]
    conflicting = [c for c in conflicts(subject, path=path)]
    return {
        "subject": subject, "as_of": when,
        "believed": [{"field": f["field"], "value": f["value"]}
                     for f in believed],
        "on_this_evidence": evidence,
        "authority_in_force": auth[-1] if auth else None,
        "authority_history": auth,
        "unresolved_conflicts": conflicting,
        "reconstructable": bool(believed) or bool(auth),
        "what_is_not_captured": (
            "Authority is reconstructed only from record_authority() entries. "
            "A permission that was never recorded cannot be reconstructed, and "
            "this says so rather than reporting none in force - an empty "
            "section reads as 'nothing was happening', which is a different "
            "claim from 'this was not recorded'."),
    }


def provenance(fact_id, path=None):
    """-> why the system believes this, end to end."""
    store = load(path)
    f = next((x for x in store["facts"] if x["fact_id"] == fact_id), None)
    if not f:
        return {"found": False, "absence_state": "not_found"}
    d = store["documents"].get(f["document_id"], {})
    return {
        "found": True, "fact_id": fact_id, "value": f["value"],
        "because": {
            "document": f["document_id"], "source_type": d.get("source_type"),
            "sha256": d.get("sha256"), "location": f["location"],
            "original_text": f.get("original_text"),
            "extractor": f"{f['extractor']} v{f['extractor_version']}",
            "extracted_at": f["extracted_at"],
            "evidence": f["evidence"],
        },
        "transformations": f.get("transformations"),
        "corrections": f.get("corrections"),
        "superseded_by": f.get("superseded_by"),
        "interpretation": None,
        "interpretation_note": (
            "No interpretation is recorded here. What this fact MEANS belongs "
            "to the domain that practises it."),
    }
