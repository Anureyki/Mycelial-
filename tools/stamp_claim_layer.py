#!/usr/bin/env python3
"""Give every shelved work a claim_layer, because None is not a value.

    python3 tools/stamp_claim_layer.py            # report only
    python3 tools/stamp_claim_layer.py --write

WHAT WAS WRONG. `claim_layer` was added after most of the corpus was built, so
60 of 91 works carried the field as `None` - not `unknown`, absent. The two are
different findings and the difference is the whole point of the field: `unknown`
says nobody classified this and a citation from it may be rationale rather than
rule, which is a warning a reader can act on. `None` says nothing at all, and
reads at lookup as a missing key rather than as a caution.

WHAT THIS MAY AND MAY NOT SET. Only the class where the layer is not a
judgement: a statute or a regulation does not describe the rule or argue for
it - it IS the rule. The citation in its title fixes that exactly as
definitionally as it fixes `authority_class`, which is the one case CLAUDE.md
already allows a field to be set without reading the body.

Everything else - a treatise, a doctrine summary, agency guidance, a work whose
class was never determined - must be READ, and gets `unknown` with the reason.
Backfilling those as `doctrinal` to make a report look clean would put a guess
in the one field whose entire purpose is not to be one.

BOTH LEVELS. `authority_class` is read off the document at lookup and
`claim_layer` off the section, so a work is only stamped when both agree.
"""
import argparse, json, glob, os, re, sys

DEFINITIONAL = {"federal_statute", "state_statute", "regulation", "court_rules"}

# The layer cannot be decided without the class, so a work missing BOTH has to
# have its class settled first. Three did - two CFR parts and a U.S.C. section
# whose files were hand-built before `authority_class` existed - and a statute
# carrying no class sorts BELOW a 1910 dictionary at lookup, because unknown
# sorts last by design. Same definitional rule, same restriction: the title has
# to be a citation, or the class stays unset and the work is reported instead.
USC_TITLE = re.compile(r'\b\d+\s+U\.?\s?S\.?\s?C\.?\b', re.I)
CFR_TITLE = re.compile(r'\b\d+\s+C\.?\s?F\.?\s?R\.?\b', re.I)


def class_from_title(title):
    """-> (authority_class, basis) or (None, None) when the title does not say."""
    t = title or ""
    if USC_TITLE.search(t):
        return ("federal_statute",
                "Title of the work is a U.S. Code citation, which fixes the class")
    if CFR_TITLE.search(t):
        return ("regulation",
                "Title of the work is a CFR citation, which fixes the class")
    return (None, None)

DOCTRINAL_BASIS = (
    "Set from the authority class, not from the text: a statute, regulation or "
    "set of court rules states the rule rather than describing or arguing it, "
    "which the citation in the title establishes definitionally.")
UNKNOWN_BASIS = (
    "Not declared at ingest and not derivable from the class. This work has to "
    "be read before it can be classed; UNKNOWN is the honest value, and a "
    "citation from here is unclassified until somebody reads it.")

MEANING = {
    "doctrinal": "States or describes the rule. Cite for what the law IS.",
    "unknown": ("Not declared at ingest. Treat any citation from here as "
                "unclassified: it may be rationale rather than rule."),
}


def plan(path):
    """-> (action, layer, why) or None when the work already carries a layer."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception as exc:
        return ("unreadable", None, str(exc))
    if not isinstance(doc, dict) or "sections" not in doc:
        return None
    have = doc.get("claim_layer")
    sections = doc.get("sections") or []
    section_layers = {s.get("claim_layer") for s in sections}
    # A layer that was set deliberately is never touched, at either level.
    if have not in (None, "unknown") :
        if section_layers - {have}:
            return ("disagrees", have,
                    f"document says {have}, sections say "
                    f"{sorted(x for x in section_layers if x != have)}")
        return None
    if have is None and section_layers and section_layers != {None}:
        return ("disagrees", have,
                f"document has no layer, sections say {sorted(section_layers)}")

    ac = doc.get("authority_class")
    if ac is None:
        ac, _ = class_from_title(doc.get("title"))
        if ac:
            return ("class+doctrinal", "doctrinal",
                    f"no authority_class; title fixes it as {ac}")
    if ac in DEFINITIONAL:
        return ("doctrinal", "doctrinal", f"authority_class={ac}")
    if have == "unknown" and section_layers == {"unknown"}:
        return None                       # already honest, nothing to do
    return ("unknown", "unknown", f"authority_class={ac!r} - must be read")


def apply(path, layer, basis, set_class=False):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if set_class:
        ac, ac_basis = class_from_title(doc.get("title"))
        if ac:
            doc["authority_class"] = ac
            doc["authority_class_basis"] = ac_basis
            for s in doc.get("sections", []):
                s["authority_class"] = ac
    doc["claim_layer"] = layer
    doc["claim_layer_meaning"] = MEANING[layer]
    doc["claim_layer_basis"] = basis
    for s in doc.get("sections", []):
        s["claim_layer"] = layer
        s["claim_layer_meaning"] = MEANING[layer]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--root", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reference"))
    a = ap.parse_args()

    counts = {}
    for path in sorted(glob.glob(os.path.join(a.root, "*", "*.json"))):
        p = plan(path)
        if not p:
            continue
        action, layer, why = p
        counts[action] = counts.get(action, 0) + 1
        rel = os.path.relpath(path, a.root)
        print(f"  {action:10} {rel[:64]:66} {why[:52]}")
        if a.write and action in ("doctrinal", "unknown", "class+doctrinal"):
            apply(path, layer,
                  DOCTRINAL_BASIS if layer == "doctrinal" else UNKNOWN_BASIS,
                  set_class=(action == "class+doctrinal"))
    print()
    for k, v in sorted(counts.items()):
        print(f"  {v:4} {k}")
    if not a.write and counts:
        print("\n  report only - re-run with --write to stamp")
    return 0


if __name__ == "__main__":
    sys.exit(main())
