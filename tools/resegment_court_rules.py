#!/usr/bin/env python3
"""Re-key a shelved set of court rules by RULE and SUBDIVISION.

    python3 tools/resegment_court_rules.py reference/legal_agent/federal_rules_of_civil_procedure.json
    python3 tools/resegment_court_rules.py <shelf.json> --write

WHY THIS EXISTS. The Federal Rules of Civil Procedure were shelved by the
statute splitter, which keys a section on the last subsection marker it saw.
Court rules are not laid out like a statute - "Rule 8." is a heading and "(c)"
is a subdivision of whichever rule came before - so every citation on the
shelf was an accident of layout. Measured 2026-09-29: the text of Rule 8(c)
(Affirmative Defenses) sat under the citation "3C(1)", and "8(c)" opened the
Supplemental Rules for Social Security actions. Legal handed over a wrong
passage as authority whenever anyone asked for a Civil Rule by number.

The fix reads the rules' OWN table of contents for the list of rule numbers,
finds each heading in that order (so a cross-reference like "see Rule 8." in a
committee note cannot be mistaken for a heading), and keys:

    "8"          the whole rule as adopted, heading to the first note
    "8(c)"       each lettered subdivision, with the rule heading prefixed
    "8 notes"    the Advisory Committee notes, kept apart from the rule text

Text is rebuilt from the sections already stored, not re-extracted, and only
when every stored section records itself complete - re-segmenting a partial
text would put a whole-looking citation on half a rule. Material after the
Civil Rules (the Supplemental Rules) is kept, keyed so it cannot be mistaken
for a Civil Rule. Every stamp on the work is carried forward unchanged.
"""
import argparse, json, os, re, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.ingest_pdf import index_terms, load_seed_vocabulary, MAX_SECTION  # noqa: E402

PAGE_HEADER = re.compile(r"Page \d+ ?TITLE 28, APPENDIX\s?[—-]\s?RULES OF CIVIL PROCEDURE ?"
                         # The running label after a page header ("...PROCEDURERule 9")
                         # names the rule on that page; a label followed by ". " is a
                         # HEADING that happens to open the page, and stripping it
                         # merged Rule 52 into Rule 51.
                         r"(?:Rule \d+(?:\.\d+)?(?!\.\s|\d|\.\d) ?)?")
# "(c) AFFIRMATIVE DEFENSES." - small caps arrive split ("A FFIRMATIVE"), so a
# caps word may carry an inner space; at least one real caps word is required.
SUBDIV = re.compile(r"\(([a-z])\) ((?:[A-Z][A-Z’'\-,;:&]*\s?){1,14}?)\.")
NOTES = re.compile(r"NOTES OF ADVISORY COMMITTEE|COMMITTEE NOTES ON RULES|\(As amended ")


def _integrity(body, stored, where):
    cut = len(body) > len(stored)
    return {"state": "truncated" if cut else "complete",
            "basis": (f"{where}: {len(body):,} characters, "
                      + (f"cut to {len(stored):,} at the {MAX_SECTION:,} cap." if cut
                         else f"under the {MAX_SECTION:,} cap, so nothing was cut.")),
            "source_chars": len(body), "stored_chars": len(stored), "cap": MAX_SECTION,
            "stamped_at": datetime.now().isoformat(timespec="seconds")}


def _chunks(text):
    """Split at sentence ends under the cap rather than truncating."""
    out, cur = [], 0
    while cur < len(text):
        end = min(cur + MAX_SECTION, len(text))
        if end < len(text):
            dot = text.rfind(". ", cur + MAX_SECTION // 2, end)
            end = dot + 1 if dot > 0 else end
        out.append(text[cur:end].strip())
        cur = end
    return [c for c in out if c]


def resegment(doc, toc_start, body_start, civil_end_marker):
    secs = doc["sections"]
    states = {(s.get("integrity") or {}).get("state") for s in secs}
    if states != {"complete"}:
        sys.exit(f"refusing: stored sections are not all complete ({states}); re-ingest first")
    full = PAGE_HEADER.sub(" ", " ".join(s["text"] for s in secs))
    full = re.sub(r"\s{2,}", " ", full)
    if toc_start is None:
        toc_start = full.find("Rule 1. ")

    toc_end = full.find("Rule 1. ", full.find("Rule 1. ", toc_start) + 1)
    toc = full[toc_start:toc_end]
    # Number AND title from the table of contents. A heading is "Rule 52. Findings",
    # never merely "Rule 52." plus a capital: committee notes say "Rule 52. Although
    # the language..." and "Rule 4.1. The Caption of the Rule", and matching on the
    # number alone started Rule 52 inside Rule 51's notes.
    nums, titles = [], {}
    for m in re.finditer(r"(?<![\d.\w(])(\d{1,2}(?:\.\d)?)\. ([A-Z][A-Za-z’',;:\- ]{2,40})", toc):
        if m.group(1) not in nums:
            nums.append(m.group(1))
            words = re.sub(r"\s*-\s*", "", m.group(2)).split()[:2]
            titles[m.group(1)] = words
    end = full.find(civil_end_marker, toc_end)
    if end < 0:
        sys.exit(f"end marker not found: {civil_end_marker!r}")

    heads, pos = [], toc_end
    for n in nums:
        w = titles.get(n) or []
        # Letters only, with an optional space or hyphen between any two: the
        # contents breaks words across lines ("Pa - pers") and the body keeps
        # real hyphens ("Third-Party"), so neither form may be required.
        # First title word only: punctuation between title words (";", ",")
        # varies between the contents and the heading.
        letters = [ch for ch in (w[0] if w else "") if ch.isalnum()][:14]
        pat = r"Rule " + re.escape(n) + r"\. " + r"[\s\-’']?".join(re.escape(ch) for ch in letters)
        m = re.compile(pat).search(full, pos, end)
        if not m:
            sys.exit(f"heading for Rule {n} not found in order - refusing a partial shelf")
        heads.append((n, m.start()))
        pos = m.start() + 5

    out = []
    for i, (n, start) in enumerate(heads):
        stop = heads[i + 1][1] if i + 1 < len(heads) else end
        block = full[start:stop].strip()
        nm = NOTES.search(block)
        rule, notes = (block[:nm.start()].strip(), block[nm.start():].strip()) if nm else (block, "")
        title = re.match(r"Rule [\d.]+\. (.{0,160}?)(?= \(a\) |$)", rule)
        heading = f"Rule {n}. {title.group(1).strip()}" if title else f"Rule {n}."

        stored = rule[:MAX_SECTION]
        out.append({"citation": n, "kind": "rule", "heading": heading, "text": stored,
                    "integrity": _integrity(rule, stored, f"Rule {n} as adopted")})
        subs = list(SUBDIV.finditer(rule))
        expect = "a"
        keep = []
        for m in subs:  # lettered in order only, so an inline "(c)" cannot start one
            if m.group(1) == expect:
                keep.append(m)
                expect = chr(ord(expect) + 1)
        for j, m in enumerate(keep):
            body = rule[m.start():(keep[j + 1].start() if j + 1 < len(keep) else len(rule))].strip()
            txt = f"{heading} {body}"[:MAX_SECTION]
            out.append({"citation": f"{n}({m.group(1)})", "kind": "subdivision",
                        "heading": heading, "text": txt,
                        "integrity": _integrity(f"{heading} {body}", txt, f"Rule {n}({m.group(1)})")})
        for k, part in enumerate(_chunks(notes), 1):
            out.append({"citation": f"{n} notes" + (f" part {k}" if k > 1 else ""),
                        "kind": "committee_notes", "heading": heading, "text": part,
                        "integrity": _integrity(part, part, f"Rule {n} notes part {k}")})

    for k, part in enumerate(_chunks(full[end:]), 1):
        out.append({"citation": f"Supplemental Rules and appendix part {k}", "kind": "supplemental",
                    "text": part, "integrity": _integrity(part, part, f"post-Civil-Rules part {k}")})
    front = full[:toc_start].strip()
    if front:
        out.insert(0, {"citation": "front matter", "kind": "front", "text": front[:MAX_SECTION],
                       "integrity": _integrity(front, front[:MAX_SECTION], "front matter")})
    return out, nums


# The layer is fixed by WHAT the passage is, which the segmentation knows for
# certain: rule text is the rule, and the Advisory Committee notes explain it.
LAYER = {"rule": "doctrinal", "subdivision": "doctrinal", "supplemental": "doctrinal",
         "committee_notes": "explanatory", "front": "unknown"}
LAYER_MEANING = {"doctrinal": "States the rule. Cite for what the rule IS.",
                 "explanatory": "Advisory Committee notes: explain the rule, do not state it.",
                 "unknown": "Front matter, not classified."}


def stamp_layers(sections):
    for s in sections:
        s["claim_layer"] = LAYER.get(s.get("kind"), "unknown")
        s["claim_layer_meaning"] = LAYER_MEANING[s["claim_layer"]]
    return sections


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("shelf")
    ap.add_argument("--agent", default="legal_agent")
    ap.add_argument("--toc-start", type=int, default=None,
                    help="character offset of the table of contents (default: first 'Rule 1. ')")
    ap.add_argument("--end-marker", default="SUPPLEMENTAL RULES FOR ADMIRALTY")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    doc = json.load(open(a.shelf))
    sections, nums = resegment(doc, a.toc_start, None, a.end_marker)

    kinds = {}
    for s in sections:
        kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    print(f"  {len(nums)} rules from the table of contents; {len(sections)} sections: {kinds}")
    bad = [s["citation"] for s in sections if s["integrity"]["state"] != "complete"]
    print(f"  truncated: {bad or 'none'}")
    for c in ("8", "8(c)", "12(b)", "56(a)"):
        hit = next((s for s in sections if s["citation"] == c), None)
        print(f"  {c:6s} -> {hit['text'][:110] if hit else 'MISSING'}")
    if not a.write:
        print("  dry run - pass --write to replace the shelf")
        return

    doc["sections"] = stamp_layers(sections)
    doc["term_index"] = index_terms(sections, seed=load_seed_vocabulary(a.agent))
    if isinstance(doc["term_index"], tuple):
        doc["term_index"] = doc["term_index"][0]
    doc["source"] = (doc.get("source", "") + f" Re-segmented {datetime.now():%Y-%m-%d} by "
                     "tools/resegment_court_rules.py: keyed by rule and subdivision from the "
                     "rules' own table of contents, rebuilt from the complete stored text. "
                     "Earlier citations were keyed by layout and were wrong (Rule 8(c) sat "
                     "under \"3C(1)\").")
    doc["segmentation"] = "court_rules: rule / subdivision / committee notes"
    backup = a.shelf + ".pre-resegment"
    os.replace(a.shelf, backup)
    json.dump(doc, open(a.shelf, "w"), indent=1, ensure_ascii=False)
    print(f"  written {a.shelf} (previous kept at {backup} - move it off the shelf)")


if __name__ == "__main__":
    main()
