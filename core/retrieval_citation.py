#!/usr/bin/env python3
"""An answer built from a source must name it, and the name must hold up.

    from core.retrieval_citation import cite, verify

    hit = cite(agent, "clean hands")        # -> entry + a citation token
    verify(answer_text, hit)                # -> (ok, reasons) BEFORE it ships

THE FAILURE THIS IS FOR. A model - or an agent - produces a fluent answer from
a stale or wrong entry and passes an accuracy check, because accuracy asks
whether the ANSWER looks right and never whether the SOURCE was. Both a correct
answer from the wrong entry and a wrong answer from the right one score the
same, and only one of them is recoverable.

WHERE THIS APPLIES, AND WHERE IT DOES NOT. Retrieval happens in
`lookup_reference` - 18 call sites across the agents. MYCOS CORE HAS NO
RETRIEVAL PATH: it is a sequence classifier that emits a label, holds no Hermes
integration, and has nothing to cite. Enforcement is wired where retrieval
actually occurs, and the 125M gets it when it gains one.

WHAT `verify` CAN ACTUALLY CHECK, and it is deliberately narrow:

  the citation resolves      an entry that cannot be opened supports nothing.
                             This is the claim pipeline's own rule.
  quoted spans are present   anything the answer puts in quotation marks must
                             appear in the cited entry. A quotation is the one
                             assertion that is checkable character by character.
  cited authorities are real a statute the answer cites must appear in the
                             entry it says it came from, or it came from
                             somewhere else.

WHAT IT DOES NOT CHECK, stated so nobody mistakes its silence for approval: it
cannot tell whether a paraphrase is faithful. That needs a reader. It answers
"is this answer grounded in the thing it named", not "is this answer right" -
and an answer that passes is not thereby correct.

NO BAG-OF-WORDS SIMILARITY. CLAUDE.md forbids it for retrieval and it is no
better here: two passages sharing vocabulary is not evidence one came from the
other, and a threshold on overlap would pass a fluent fabrication that reused
the right nouns.
"""
import hashlib
import re

# A citation token identifies ONE entry, not a work. Two sections of one
# treatise are different sources and an answer that cites the work has not
# said which part it relied on.
CITATION_RE = re.compile(r"\[src:([0-9a-f]{12})\]")
QUOTE_RE = re.compile(r'"([^"]{12,400})"')
# Statute / regulation shapes an answer might claim.
AUTHORITY_RE = re.compile(
    r"\b\d+\s+(?:U\.?S\.?C\.?|C\.?F\.?R\.?)\s*§?\s*[0-9][0-9a-zA-Z.\-]*", re.I)


def source_id(entry):
    """Stable id for one retrieved entry. Same entry -> same token, always."""
    basis = "|".join(str(entry.get(k) or "") for k in
                     ("title", "citation", "page"))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def token(entry):
    return f"[src:{source_id(entry)}]"


def cite(agent, term):
    """-> the first hit with a citation token attached, or None.

    Returns the ENTRY, not prose. An agent that wants to answer from it has to
    hold the entry, which is what makes verification possible afterwards - you
    cannot check an answer against a source nobody kept."""
    try:
        hits = agent.lookup_reference(term) or []
    except Exception:
        return None
    if not hits:
        return None
    e = dict(hits[0]) if isinstance(hits[0], dict) else {"text": str(hits[0])}
    e["source_id"] = source_id(e)
    e["citation_token"] = token(e)
    return e


def verify(answer, entry, require_citation=True):
    """-> (ok, reasons). Run BEFORE the answer ships, never after.

    After is not a check, it is a post-mortem: the answer already went out and
    the correction has to catch up with it."""
    reasons = []
    if not isinstance(answer, str) or not answer.strip():
        return False, ["empty answer"]
    if not entry:
        return False, ["no source entry was retained, so nothing can be "
                       "checked against. An answer whose source was not kept "
                       "is unverifiable by construction."]

    sid = entry.get("source_id") or source_id(entry)
    found = CITATION_RE.findall(answer)

    if require_citation and not found:
        reasons.append(
            "NO CITATION. The answer names no source. An uncited answer built "
            "from retrieval is indistinguishable from one built from nothing.")
    elif found and sid not in found:
        reasons.append(
            f"CITATION MISMATCH. The answer cites {found} and the entry it was "
            f"built from is {sid}. One of the two is wrong and neither can be "
            f"trusted until somebody says which.")

    body = str(entry.get("text") or "")
    norm = " ".join(body.lower().split())

    for q in QUOTE_RE.findall(answer):
        if " ".join(q.lower().split()) not in norm:
            reasons.append(
                f"QUOTED TEXT NOT IN THE SOURCE: {q[:70]!r}. A quotation is "
                f"checkable character by character, and this one does not "
                f"appear in the entry the answer named.")

    src_auth = {a.lower().replace(" ", "") for a in AUTHORITY_RE.findall(body)}
    for a in AUTHORITY_RE.findall(answer):
        if a.lower().replace(" ", "") not in src_auth:
            reasons.append(
                f"AUTHORITY NOT IN THE CITED ENTRY: {a}. The answer cites it "
                f"and the source it named does not, so it came from somewhere "
                f"else - possibly from the model.")

    return (not reasons), reasons


def enforce(answer, entry, agent=None):
    """-> the answer, or a refusal. The gate, at the point of shipping."""
    ok, reasons = verify(answer, entry)
    if ok:
        return {"shipped": True, "answer": answer,
                "source_id": entry.get("source_id"),
                "source_title": entry.get("title"),
                "source_citation": entry.get("citation")}
    return {
        "shipped": False,
        "refused": True,
        "reasons": reasons,
        "answer_withheld": answer[:400],
        "source_id": (entry or {}).get("source_id"),
        "why": ("Refused before shipping. A wrong answer that reaches the "
                "principal has to be chased down afterwards; a refused one "
                "costs a retry."),
    }
