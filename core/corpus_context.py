"""Self-describing @context at the corpus boundary (2026-11-17).

Publishes MEANING, not content. Attaches the MycOS corpus trust vocabulary
(config/context/mycos-corpus-v1.jsonld) to a record leaving the system so an
external tool can read it WITHOUT the internal schema. It is the semantic
sibling of the data boundary - differential privacy governs what NUMBERS leave,
federated learning governs what MODEL UPDATES leave, and this governs what
MEANING leaves.

Read-only. It does not touch the store. The guarantee that the vocabulary is
COMPLETE for the corpus schema and ACCURATE for its enum value-spaces is a
build-time gate (tools/check_corpus_context.py), so nothing trust-bearing can
cross the boundary undescribed.
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTEXT_PATH = os.path.join(BASE, "config", "context", "mycos-corpus-v1.jsonld")
CONTEXT_URI = "urn:mycos:vocab:corpus:1"
_RESERVED = {"@version", "mycos", "xsd"}


def load_context(path=None):
    with open(path or CONTEXT_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def declared_fields(ctx=None):
    """The metadata field names the vocabulary declares (JSON-LD term keys)."""
    ctx = ctx or load_context()
    return {k for k in (ctx.get("@context") or {}) if not k.startswith("@") and k not in _RESERVED}


def field_meaning(field, ctx=None):
    """The full definition of one field (definition, value-space, ranking...), or None."""
    ctx = ctx or load_context()
    return (ctx.get("definitions") or {}).get(field)


def enum_values(field, ctx=None):
    """Declared value-space of an enum field, or None if the field is not enumerated."""
    d = field_meaning(field, ctx)
    return list(d["values"]) if isinstance(d, dict) and "values" in d else None


def is_open_vocabulary(field, ctx=None):
    """True if the field's value-space is deliberately open (new values allowed
    without a vocabulary change) - e.g. structural `kind`. Closed enums
    (authority_class, claim_layer) must enumerate every value the corpus uses."""
    d = field_meaning(field, ctx)
    return bool(isinstance(d, dict) and "open" in str(d.get("note", "")).lower())


def undeclared(fields, ctx=None):
    """Which of `fields` the vocabulary does NOT declare - the leak set."""
    decl = declared_fields(ctx)
    return [f for f in fields if f not in decl]


def attach_context(record, by_reference=True, ctx=None):
    """A self-describing view of `record`: the record with the corpus @context
    attached (by URI reference, keeping exports small; inline on request). Does
    NOT mutate `record`. Every declared field in it is now interpretable by an
    outside tool; content fields pass through as content."""
    out = dict(record)
    out["@context"] = CONTEXT_URI if by_reference else (ctx or load_context())["@context"]
    return out


def describe(record, ctx=None):
    """Attach the context and split the record's keys into declared-meaning vs
    content, so a caller can SEE what crosses the boundary with a published
    meaning and what travels as opaque content."""
    ctx = ctx or load_context()
    decl = declared_fields(ctx)
    keys = [k for k in record if not str(k).startswith("@")]
    out = attach_context(record, ctx=ctx)
    out["_boundary"] = {
        "vocabulary": CONTEXT_URI,
        "declared": sorted(k for k in keys if k in decl),
        "content": sorted(k for k in keys if k not in decl),
    }
    return out
