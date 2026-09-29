#!/usr/bin/env python3
"""Rebuild the subject index of already-shelved works against the current seed.

    python3 tools/reindex_terms.py --agent trust_agent
    python3 tools/reindex_terms.py --agent trust_agent --write

WHY THIS EXISTS. `config/doctrine_seed.json` is meant to be edited - the file
says so - but editing it changed nothing for anything already on the shelf,
because the index is built once at ingest. So a term added today reaches only
works ingested after today, and the shelf splits into two vocabularies with
nothing announcing the split.

That was measured: "conflict of laws" and "esg" returned [no hit] immediately
after shelving the two works those phrases are the SUBJECT of, because the seed
gained the terms after the ingest.

Re-ingesting would answer it and costs more than it should - it re-extracts the
PDF, re-splits it, and needs every ingest-time flag supplied again by hand,
which is a chance to get `authority_class` or `claim_layer` wrong on a work
that already has them right. The section text is already stored. Only the index
is stale, so only the index is rebuilt; every stamp on the work is untouched.
"""
import argparse, json, glob, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.ingest_pdf import index_terms, load_seed_vocabulary  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--root", default=None,
                    help="reference/ directory to reindex (default: this checkout's). "
                         "The agents read the checkout they run from, which need not "
                         "be the one this script lives in.")
    a = ap.parse_args()

    base = a.root or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "reference")
    root = os.path.join(base, a.agent)
    if not os.path.isdir(root):
        sys.exit(f"no such shelf: {root}")
    seed = load_seed_vocabulary(a.agent)
    print(f"  seed vocabulary: {len(seed)} terms\n")

    total_new = 0
    for path in sorted(glob.glob(os.path.join(root, "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception as exc:
            print(f"  unreadable {os.path.basename(path)}: {exc}")
            continue
        if not isinstance(doc, dict) or not doc.get("sections"):
            continue
        before = doc.get("term_index") or {}
        after = index_terms(doc["sections"], seed=seed)
        gained = sorted(set(after) - set(before))
        lost = sorted(set(before) - set(after))
        # PLACEMENTS COUNT, NOT JUST NAMES. This compared only which terms a work
        # had, so a term that pointed at the wrong sections and still existed
        # after a matcher fix was left exactly as wrong: IRM Part 5 kept
        # "form 1099-c" on 465 sections when the phrase is in one, because the
        # key survived and the file was never rewritten.
        moved = sorted(k for k in set(after) & set(before)
                       if set(after[k]) != set(before.get(k) or []))
        if not gained and not lost and not moved:
            continue
        total_new += len(gained)
        print(f"  {os.path.basename(path)[:58]:60} +{len(gained):3} -{len(lost):3} "
              f"~{len(moved):3}")
        if moved:
            print(f"      MOVED:  {', '.join(moved[:12])}" + (" ..." if len(moved) > 12 else ""))
        if gained:
            print(f"      gained: {', '.join(gained[:12])}"
                  + (" ..." if len(gained) > 12 else ""))
        if lost:
            # A term the current seed no longer finds. Worth seeing rather than
            # silently dropping: it usually means the seed changed, not the text.
            print(f"      LOST:   {', '.join(lost[:12])}"
                  + (" ..." if len(lost) > 12 else ""))
        if a.write:
            doc["term_index"] = after
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=0)

    print(f"\n  {total_new} new subject keys"
          + ("" if a.write else "   (report only - re-run with --write)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
