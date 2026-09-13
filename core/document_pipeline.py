#!/usr/bin/env python3
"""One document in: hashed, cut into cited clauses, refused where it must be.

    from core.document_pipeline import intake
    intake("/path/statement.pdf", "stmt_2026_07", "statement",
           declared_by="principal")

WHAT THIS JOINS. core/document_intake.py already extracts, segments by the
document's own numbering and says which domains have an INTEREST in a clause.
core/evidence_ingest.py already records facts with provenance and refuses what
must not be stored. Neither was reachable from an agent. This is the join, and
it adds exactly two things of its own: routing from the DECLARED source type,
and the handling of a clause that must not be kept.

ROUTING COMES FROM WHAT YOU DECLARED, NOT FROM READING. The interface layer
must not decide what a document means, and deciding where it goes by reading
it is that decision wearing a different hat. So the destination follows the
source type the person chose at upload - a statement is Accounting's because
they said "statement", not because the word "balance" appeared. The clause
level `domains` from document_intake stay as INTEREST, which is a different
claim: "this department will want to see it", not "this is theirs".

A CLAUSE THAT CANNOT BE STORED IS RECORDED AS REFUSED, NEVER DROPPED.
This is the case that matters for the principal's own papers: a VA letter
carries his file number, which this project established IS his Social Security
number. evidence_ingest refuses to store it, correctly - and if the pipeline
simply skipped those clauses, the document would be PARTIALLY ingested with
nothing saying so. A record that is quietly missing its third paragraph is
worse than one that failed, because everything present is accurate.

So a refused clause produces an absence record naming WHAT was refused, WHERE
it was, and its last four digits - enough to find it in the paper original,
never enough to be the identifier.

NOTHING HERE INTERPRETS. It files. What a clause MEANS is the domain's, and
the questions go to them as questions.
"""
import os
from datetime import datetime, timezone

from core.asset_registry import Refused
from core import document_intake, evidence_ingest

# The DECLARED source type decides the destination. One owner each, and the
# reasons are the departments' own subject matter rather than a keyword.
ROUTE = {
    "statement": ("accounting_agent", "a statement is a record of account"),
    "invoice": ("accounting_agent", "an obligation to pay"),
    "receipt": ("accounting_agent", "evidence a payment happened"),
    "tax_document": ("accounting_agent", "Accounting owns the IRM and 26 CFR"),
    "credit_report": ("accounting_agent", "an account record; Legal is "
                                          "notified because the FCRA is theirs"),
    "contract": ("legal_agent", "an instrument, and enforceability is Legal's"),
    "notice": ("legal_agent", "a notice starts or ends a period"),
    "court_document": ("legal_agent", "the docket is Legal's"),
    "correspondence": ("legal_agent", "default for an unclassified letter"),
    "certificate": ("trust_agent", "an appointment or a fiduciary capacity"),
    "screenshot": ("legal_agent", "triaged for what it cites"),
    "other": ("legal_agent", "the cheap check is the safer default - a legal "
                             "card resolves against a corpus in seconds"),
}

# A second department that must SEE it, without owning it.
ALSO_NOTIFY = {
    "credit_report": ["legal_agent"],
    "certificate": ["legal_agent", "accounting_agent"],
    "tax_document": ["legal_agent"],
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def intake(file_path, doc_id, source_type, declared_by="principal",
           effective_date=None, path=None, max_clauses=200):
    """-> a summary a person can read. Files nothing it cannot cite."""
    if source_type not in evidence_ingest.SOURCE_TYPES:
        raise Refused(
            f"{source_type!r} is not a source type: "
            f"{list(evidence_ingest.SOURCE_TYPES)}. The type is DECLARED - it "
            f"decides where this goes, and guessing it from the contents "
            f"would be the interface deciding what the document means.")
    if not os.path.isfile(file_path):
        raise Refused(f"{file_path!r} is not a file")

    doc = evidence_ingest.register_document(
        doc_id, source_type, path=path, file_path=file_path,
        source_date=effective_date, note=f"declared by {declared_by}")

    found = document_intake.analyse(file_path, max_clauses=max_clauses)
    if found.get("error"):
        # A document that produced no text is a FINDING, not an empty ingest.
        evidence_ingest.absence(
            doc_id, "text", "unreadable",
            f"{found['error']}. {found.get('advice', '')}".strip(), path=path)
        return {"document_id": doc_id, "sha256": doc["sha256"],
                "clauses": 0, "stored": 0, "refused": 0,
                "absence_state": "unreadable", "why": found["error"],
                "routed_to": None}

    owner, why = ROUTE[source_type]
    stored, refused, interest = [], [], {}
    for c in found["items"]:
        ref = c.get("ref") or "unnumbered"
        try:
            f = evidence_ingest.extract(
                doc_id, subject=doc_id, field=f"clause:{ref}",
                value=c["text"][:2000], location=ref,
                extractor="document_intake", extractor_version="1.0",
                evidence="cited", path=path,
                effective_date=effective_date or f"not_applicable:the document "
                                                 f"carries no date this "
                                                 f"clause became true",
                original_text=c["text"][:400])
            stored.append({"ref": ref, "fact_id": f["fact_id"],
                           "interest": c.get("domains") or []})
        except Refused as exc:
            # THE CASE THAT MATTERS. The clause carries something that must
            # not be stored. Record that it existed and was refused, with
            # enough to find it on paper and not enough to be the identifier.
            evidence_ingest.absence(
                doc_id, f"clause:{ref}", "not_applicable",
                f"present in the source and NOT STORED: {str(exc)[:200]}",
                path=path)
            refused.append({"ref": ref, "why": str(exc)[:160]})
        for d in c.get("domains") or []:
            interest.setdefault(d, []).append(ref)

    evidence_ingest.open_question(
        doc_id,
        f"what do these {len(stored)} clauses MEAN for this domain?",
        owner, "document_pipeline", path=path,
        fact_ids=[s["fact_id"] for s in stored])

    return {
        "document_id": doc_id, "sha256": doc["sha256"],
        "source_type": source_type, "declared_by": declared_by,
        "clauses": found["clauses"], "stored": len(stored),
        "refused": len(refused), "refused_detail": refused,
        "segmented_by": found.get("segmented_by"),
        "routed_to": owner, "why_routed": why,
        "also_notified": ALSO_NOTIFY.get(source_type, []),
        "domains_with_an_interest": {d: len(v) for d, v in interest.items()},
        "pages": (found.get("meta") or {}).get("pages"),
        "absence_state": "incomplete" if refused else "verified_clear",
        "not_interpreted": (
            "Filed, not read for meaning. What these clauses mean is "
            f"{owner}'s, and the question has been recorded for them."),
        "at": _now(),
    }
