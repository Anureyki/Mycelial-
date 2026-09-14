#!/usr/bin/env python3
"""An agent acquires its own authority: fetch, READ, verify, class, shelve.

    from core.authority_acquisition import acquire
    acquire("legal_agent", "38 U.S.C. 1969", expect="SGLI premium deducted from pay")

WHY THIS IS A CAPABILITY AND NOT A TOOL RUN BY HAND. `tools/ingest_law.py` has
worked for weeks and every acquisition went through a person typing it. The
principal's correction, in his words: "that's exactly why the legal agent is
supposed to be fetching those things. Fetch it. Read it. and do all those
things." CLAUDE.md has said the same since it was written - ask the agent, and
if it cannot do the thing yet, BUILD THE CAPABILITY, because "substituting your
own arithmetic leaves the agent exactly as capable as it was."

It was measurably absent rather than merely undeclared. Legal declares 84
capabilities and not one of them acquires a provision; `add_deadline` refuses a
citation it cannot open and tells the USER to go and run the tool. An agent that
knows exactly what is missing and hands the work back is the clearest possible
statement of a missing verb.

IT LIVES IN core/ SO EVERY AGENT INHERITS IT. Accounting has the identical gap
for the IRM and the ASC. This file already records that a fix made in one agent
for a fault that lives in the base class is not a fix, it is a second place for
the bug to hide - `_load_reference_docs` was fixed in Legal while Accounting's
2,108 unreachable sections sat untouched.

THE READ IS THE POINT, AND IT IS NEW.
=====================================
The tool fetches and shelves. It does not check that what came back is what was
asked for, and nothing else did either. So:

    asked for:   the statute establishing DEERS
    fetched:     10 U.S.C. 1061
    received:    "Survivors of certain Reserve and Guard members" - commissary
                 privileges for dependents of Reserve members who died on duty
    would have:  been shelved as the DEERS authority, correctly classed as a
                 federal statute, correctly marked doctrinal, and cited from
                 then on for a proposition it says nothing about

Every downstream field would have been right. The corpus would have been wrong
in the one way nothing downstream can detect, because a citation that resolves
looks identical to a citation that is correct. This is the same shape as
5 U.S.C. 552a once sitting under two foreign citations in this corpus: a Debt
Collection Act lookup returning Privacy Act text.

So `expect` is a first-class argument and a MISMATCH REFUSES THE SHELVE. The
retrieved text comes back in the refusal so somebody can read it and decide -
a check that found something wrong must hand over what it found, not just say
no.

WHAT IT WILL NOT DO. It will not pass `expect=None` silently: with nothing to
check against, the result says `subject_verified: false` and says why, and the
entry is shelved but flagged. An unchecked acquisition is allowed, because
sometimes the citation IS the whole of what is known. An unchecked acquisition
that reports itself as checked is not.

AND IT VERIFIES THE EFFECT, NOT THE EXIT CODE. After shelving it looks the
citation up through the agent's own `lookup_reference` and reports whether it
actually resolves. A shelved file the agent's own lookup cannot reach is the
`inert knowledge` state from CLAUDE.md - information held that no verb can
reason with - and it is invisible unless something asks.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Words that carry no subject. A subject check that matches on "the" would pass
# on anything, which is the failure mode of every keyword gate in this system.
STOPWORDS = {
    "the", "a", "an", "of", "for", "to", "and", "or", "in", "on", "by", "with",
    "from", "that", "this", "it", "is", "are", "was", "be", "as", "at", "any",
    "all", "law", "laws", "act", "section", "statute", "code", "rule", "part",
    "title", "us", "u", "s", "c", "cfr", "usc", "provision", "regulation",
}

# How much of a retrieved provision is read when checking the subject. The
# heading carries the subject; the opening sentences carry it again in the
# operative words. Reading the whole section would match a passing mention in a
# cross-reference - which is the incidental-mention problem the retrieval
# ranking already had to solve once.
SUBJECT_WINDOW = 700


class Unparseable(ValueError):
    pass


def parse_citation(text):
    """-> (source_kind, kwargs, canonical). Raises Unparseable.

    Deliberately narrow. A parser that guesses which corpus a half-recognised
    string belongs to will eventually fetch the wrong body of law and be right
    often enough that nobody checks it."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t:
        raise Unparseable("no citation given")

    # A PASTED URL IS A CITATION IN A DIFFERENT COSTUME, and converting it is
    # parsing rather than inference - the title and part are IN the path, not
    # guessed from the page. The principal pastes eCFR links; requiring him to
    # translate one into "12 CFR 226" by hand is asking a person to do
    # deterministic string work for a machine.
    #
    # Only the official sources. A link to somebody's blog about Regulation Z
    # is not Regulation Z, and this refuses rather than fetching whatever is
    # on the end of an arbitrary URL.
    m = re.search(r"ecfr\.gov/(?:current/)?title-(\d+).*?/part-(\d+)", t, re.I)
    if m:
        return ("cfr", {"title": m.group(1), "part": m.group(2)},
                f"{m.group(1)} CFR Part {m.group(2)}")
    m = re.search(r"law\.cornell\.edu/uscode/text/(\d+)/([0-9]+[A-Za-z-]*)", t, re.I)
    if m:
        return ("usc-section", {"title": m.group(1), "section": m.group(2)},
                f"{m.group(1)} U.S.C. \u00a7 {m.group(2)}")
    m = re.search(r"govinfo\.gov/.*?CFR-\d+-title(\d+).*?-part(\d+)", t, re.I)
    if m:
        return ("cfr", {"title": m.group(1), "part": m.group(2)},
                f"{m.group(1)} CFR Part {m.group(2)}")
    m = re.search(r"govinfo\.gov/.*?PLAW-(\d+)publ(\d+)", t, re.I)
    if m:
        return ("plaw", {"congress": m.group(1), "number": m.group(2)},
                f"Pub. L. {m.group(1)}-{m.group(2)}")
    if t.lower().startswith(("http://", "https://")):
        raise Unparseable(
            f"{t[:80]!r} is a URL this cannot turn into a citation. Recognised "
            f"sources: ecfr.gov title/part, law.cornell.edu uscode, govinfo "
            f"CFR. A link to a page ABOUT a regulation is not the regulation, "
            f"and fetching whatever is on the end of an arbitrary URL is how "
            f"a blog post ends up shelved as authority.")

    m = re.match(r"^(\d+)\s*(?:U\.?\s*S\.?\s*C\.?)\s*(?:§+\s*)?"
                 r"([0-9]+[A-Za-z]*(?:[-–][0-9]+)?)\s*$", t, re.I)
    if m:
        return ("usc-section", {"title": m.group(1), "section": m.group(2)},
                f"{m.group(1)} U.S.C. § {m.group(2)}")

    m = re.match(r"^(\d+)\s*C\.?\s*F\.?\s*R\.?\s*(?:Part\s*)?(\d+)\s*$", t, re.I)
    if m:
        return ("cfr", {"title": m.group(1), "part": m.group(2)},
                f"{m.group(1)} CFR Part {m.group(2)}")

    m = re.match(r"^(?:Pub(?:lic)?\.?\s*L(?:aw)?\.?)\s*(\d+)[-\u2013](\d+)\s*$", t, re.I)
    if m:
        return ("plaw", {"congress": m.group(1), "number": m.group(2)},
                f"Pub. L. {m.group(1)}-{m.group(2)}")
    m = re.match(r"^IRM\s*(?:Part\s*)?(\d+)\s*$", t, re.I)
    if m:
        return ("irm", {"part": m.group(1)}, f"IRM Part {m.group(1)}")

    m = re.match(r"^(?:Ohio\s+Rev(?:ised)?\.?\s*Code|O\.?R\.?C\.?)\s*"
                 r"(?:§\s*)?([0-9]+\.[0-9]+)\s*$", t, re.I)
    if m:
        return ("orc", {"section": m.group(1)}, f"Ohio Rev. Code {m.group(1)}")

    raise Unparseable(
        f"{t!r} is not a citation this can fetch. Recognised: "
        f"'38 U.S.C. 1969', '16 CFR 433', 'IRM 5', 'Ohio Rev. Code 2329.02'. "
        f"State law other than Ohio has no scriptable source and must go "
        f"through tools/ingest_pdf.py - see the note in tools/ingest_law.py.")


def _heading_of(body):
    """The provision's own heading, which is where the subject lives."""
    m = re.match(r"\s*§+\s*[0-9A-Za-z.–-]+\.?\s*([^.;]{3,120})", body or "")
    return m.group(1).strip() if m else ""


def _terms(phrase):
    return [w for w in re.findall(r"[A-Za-z][A-Za-z'-]+", str(phrase or "").lower())
            if w not in STOPWORDS and len(w) > 2]


def check_subject(body, expect):
    """-> {verified, matched, missing, heading, why}. The READ step.

    Matching is prefix-based on the first five characters so that `deduction`
    answers `deducted` and `assignment` answers `assignable`, without pulling in
    a stemmer whose behaviour nobody here can predict. ONE significant term is
    enough: a heading is a few words and demanding several would refuse correct
    provisions, which trains a caller to pass force=True by reflex and turns the
    whole check off."""
    heading = _heading_of(body)
    window = (heading + " " + (body or "")[:SUBJECT_WINDOW]).lower()
    wanted = _terms(expect)
    if not wanted:
        return {"verified": False, "matched": [], "missing": [],
                "heading": heading, "checked": False,
                "why": ("Nothing to check against. `expect` was empty, so this "
                        "was shelved without anybody confirming the text is "
                        "about the subject it was fetched for. That is allowed "
                        "and it is recorded, because an unchecked acquisition "
                        "reporting itself as checked is the failure this "
                        "capability exists to prevent.")}
    matched = [w for w in wanted if w[:5] in window]
    missing = [w for w in wanted if w not in matched]
    ok = bool(matched)
    return {
        "verified": ok, "checked": True, "matched": matched, "missing": missing,
        "heading": heading,
        "why": (f"Heading {heading!r} carries {matched}."
                if ok else
                f"Nothing in {wanted} appears in the heading {heading!r} or the "
                f"opening {SUBJECT_WINDOW} characters. The provision that came "
                f"back is not about what was asked for, so nothing was shelved. "
                f"Read the text in `retrieved` and either fetch a different "
                f"citation or re-run with force=True if the subject is stated "
                f"in words this could not match."),
    }


def acquire(agent, citation, expect=None, force=False, lookup=None,
            reload=None):
    """Fetch, read, verify, class and shelve one provision. -> result dict."""
    from tools import ingest_law

    try:
        kind, kw, canonical = parse_citation(citation)
    except Unparseable as exc:
        return {"acquired": False, "stage": "parse", "error": str(exc),
                "citation": citation}

    fetcher = {"usc-section": ingest_law.fetch_usc_section,
               "cfr": ingest_law.fetch_cfr,
               "irm": ingest_law.fetch_irm,
               "plaw": ingest_law.fetch_plaw,
               "orc": ingest_law.fetch_orc}[kind]
    try:
        body, title, source = fetcher(**kw)
    except SystemExit as exc:
        # The fetchers refuse loudly rather than store an error page. That
        # refusal is a RESULT here, not a process exit - an agent must not die
        # because one citation was unavailable.
        return {"acquired": False, "stage": "fetch", "citation": canonical,
                "error": str(exc)}
    except Exception as exc:
        return {"acquired": False, "stage": "fetch", "citation": canonical,
                "error": f"{type(exc).__name__}: {exc}"}

    subject = check_subject(body, expect)
    if subject["checked"] and not subject["verified"] and not force:
        return {
            "acquired": False, "stage": "read", "citation": canonical,
            "title": title, "subject": subject,
            "retrieved": body[:1200],
            "retrieved_chars": len(body),
            "error": ("REFUSED: the text retrieved is not about the subject it "
                      "was fetched for. Nothing was written to the corpus."),
            "why_this_matters": (
                "A citation that resolves looks exactly like a citation that is "
                "correct. Shelving this would have produced a correctly classed, "
                "correctly dated, completely wrong authority that every later "
                "lookup would have trusted."),
        }

    res = ingest_law.shelve(body, title, source, agent, kind)
    if not res["ok"]:
        return {"acquired": False, "stage": "shelve", "citation": canonical,
                "title": title, "subject": subject, "error": res["error"]}

    # RELOAD BEFORE CHECKING, which is not a detail. The first cut checked
    # reachability against the caller's cache and then reloaded it afterwards,
    # so a provision that shelved perfectly reported `reachable_by_lookup:
    # false` every single time. A check that runs before the thing it checks
    # measures the previous state and calls it the result - which is the same
    # ordering fault as reading a file before the write is flushed, and it
    # fails in the safe direction only by luck.
    reload_error = None
    if reload is not None:
        try:
            reload()
        except Exception as exc:
            reload_error = f"{type(exc).__name__}: {exc}"

    # VERIFY THE EFFECT, NOT THE EXIT CODE. A shelved file the agent's own
    # lookup cannot reach is `inert knowledge` - held, and unreachable by any
    # verb. It is invisible unless something asks, so this asks.
    reachable, found = None, 0
    if lookup is not None:
        try:
            hits = lookup(canonical) or []
            found = len(hits)
            reachable = found > 0
        except Exception as exc:
            reachable, found = False, f"lookup raised {type(exc).__name__}: {exc}"

    return {
        "acquired": True, "citation": canonical, "title": title,
        "source": source, "path": os.path.relpath(res["path"], ROOT),
        "sections": res.get("sections"),
        "authority_class": res["authority_class"],
        "authority_class_basis": res.get("authority_class_basis"),
        "claim_layer": res["claim_layer"],
        "subject": subject,
        "subject_verified": subject["verified"],
        "reachable_by_lookup": reachable,
        "lookup_hits": found,
        "reload_error": reload_error,
        "reachability_note": (
            "Shelved and confirmed reachable through this agent's own "
            "lookup_reference." if reachable else
            "SHELVED BUT NOT REACHABLE by this agent's lookup - the corpus holds "
            "it and no verb can open it, which is the `inert knowledge` state."
            if reachable is False else
            "Reachability not checked: no lookup was supplied."),
    }
