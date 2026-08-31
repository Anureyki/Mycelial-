"""Source integrity as a property of an authority, not an inference about one.

A section of law in this corpus is read by agents that then reason from it. The
question "is this the whole provision?" therefore belongs to the section itself,
recorded by the code that KNEW the answer at the moment it wrote the file - not
reconstructed afterwards by a script measuring string lengths.

It was reconstructed afterwards. `check_inherited.py` found truncated statutes by
looking for a stored length of exactly 4000, the retired `MAX_SECTION`. That
worked once and is wrong three ways:

  - It is a guess from FORM, not a record of FACT. A provision that happens to be
    4000 characters long would be condemned; one truncated at any other cap would
    pass. This is the same error as setting `authority_class` from a filename.
  - It runs at a different time than the read. Between CI runs a truncated
    statute is indistinguishable from a whole one to every agent that opens it.
  - The truth was known and thrown away. `body[:MAX_SECTION]` knows, at that
    instant, whether it cut anything. That fact was discarded and then guessed at.

So integrity is stamped at ingest and travels with the section forever after.

THE DEFAULT IS `unknown`, AND `unknown` IS NOT `complete`. A section that never
recorded its integrity has not been vouched for by anything; saying so is the
honest position and is the same rule the claim pipeline runs, where the default
conclusion is `unsupported` rather than a hopeful one. 15,683 sections were in
this corpus with no integrity record of any kind when this was written, and every
one of them reads as `unknown` until something that actually knows says otherwise.
"""

from datetime import datetime

# What a section can say about its own completeness.
STATES = (
    "complete",    # the full retrieved body was stored; nothing was cut
    "truncated",   # the body was longer than what was stored, by a known amount
    "unknown",     # DEFAULT - nothing recorded it, so nothing vouches for it
)

FIELD = "integrity"


def stamp(section, state, basis, *, source_chars=None, stored_chars=None,
          cap=None, retrieved_at=None, source_url=None):
    """Record integrity on a section. `basis` is not optional.

    Every field in this system whose job is to carry how much weight something
    deserves must be filled by something that READ the thing - a blank is a
    known gap, a guess is an assumption laundered into metadata. So a state
    arrives with the reason it was assigned, exactly as `authority_class`
    carries `authority_class_basis`.
    """
    if state not in STATES:
        raise ValueError(f"integrity state must be one of {STATES}, got {state!r}")
    if not str(basis or "").strip():
        raise ValueError(
            "stamping integrity requires a basis - how completeness was "
            "determined. A state with no basis is indistinguishable from a guess.")
    rec = {
        "state": state,
        "basis": str(basis).strip(),
        "stamped_at": datetime.now().isoformat(timespec="seconds"),
    }
    if stored_chars is not None:
        rec["stored_chars"] = int(stored_chars)
    if source_chars is not None:
        rec["source_chars"] = int(source_chars)
    if cap is not None:
        rec["cap_applied"] = int(cap)
    if retrieved_at:
        rec["retrieved_at"] = retrieved_at
    if source_url:
        rec["source_url"] = source_url
    if state == "truncated" and source_chars and stored_chars:
        rec["missing_chars"] = max(0, int(source_chars) - int(stored_chars))
        rec["warning"] = (
            f"INCOMPLETE: {stored_chars:,} of {source_chars:,} characters stored. "
            f"Do not rely on the absence of a subsection - the rest of this "
            f"provision is not here.")
    section[FIELD] = rec
    return section


def read(section):
    """The integrity of a section, never guessed.

    An unstamped section returns `unknown` with a basis saying so. This function
    deliberately does NOT look at the text length: inferring completeness from
    size is the thing this module exists to replace.
    """
    rec = (section or {}).get(FIELD)
    if isinstance(rec, dict) and rec.get("state") in STATES:
        return rec
    return {
        "state": "unknown",
        "basis": ("No integrity was recorded when this section was stored, so "
                  "nothing vouches for it being the whole provision. Unknown is "
                  "not complete."),
        "stamped_at": None,
    }


def is_whole(section):
    """True only for a section that recorded itself complete."""
    return read(section).get("state") == "complete"


def caution(section):
    """One line to put in front of a reader, or None when there is nothing to say."""
    r = read(section)
    if r["state"] == "complete":
        return None
    if r["state"] == "truncated":
        return r.get("warning") or (
            "INCOMPLETE: this section was truncated when stored. Do not rely on "
            "the absence of a subsection.")
    return ("UNVERIFIED: nothing recorded whether this section was stored whole. "
            "Treat the absence of a subsection as unproven, not as absence.")
