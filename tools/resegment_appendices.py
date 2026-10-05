#!/usr/bin/env python3
"""Lift CFR appendices out of the section they were glued onto.

    python3 tools/resegment_appendices.py --agent legal_agent           # preview
    python3 tools/resegment_appendices.py --agent legal_agent --write   # apply

WHY. The splitter had no pattern for "Appendix C to Part 1002—...", so every
appendix of a CFR part was stored as the tail of the part's last section.
Measured 2026-10-05 on seven shelves: Reg B (model adverse-action reasons, in
"§ 1002.114"), Reg F (model validation notice, "§ 1006.108"), Reg V, Reg Z
(two editions), 24 CFR 200 and 47 CFR 64. The text was held and no citation
reached it - the "unreachable" state - and Legal answered a question about
the model reasons from the section about something else.

ingest_pdf now heads appendices on their own. This fixes what was already
shelved, from the text already stored - nothing is re-fetched - and only where
the parent records itself complete: splitting a truncated body would put a
whole-looking citation on part of an appendix. Every stamp on the work is
carried forward; each new section is stamped complete with its own length,
and the parent's stamp is rewritten to its new length with the reason.
Run reindex_terms after --write so the subject index sees the new sections.
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime

HEAD = re.compile(r"Appendix ([A-Z]{1,2}(?:-\d+)?) to Part (\d+)\s*[‐-―-]")


def split(sec):
    t = sec.get("text") or ""
    hits = list(HEAD.finditer(t))
    if not hits:
        return None
    seen, keep = set(), []
    for m in hits:
        key = m.group(0)[:-1].strip()
        if key in seen:
            continue        # a heading repeated inside its own appendix is not a new one
        seen.add(key)
        keep.append(m)
    parts = []
    for i, m in enumerate(keep):
        end = keep[i + 1].start() if i + 1 < len(keep) else len(t)
        parts.append((f"Appendix {m.group(1)} to Part {m.group(2)}", t[m.start():end].strip()))
    return t[:keep[0].start()].strip(), parts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    now = datetime.now().isoformat(timespec="seconds")
    total = 0
    for f in sorted(glob.glob(os.path.join(root, "reference", a.agent, "*.json"))):
        try:
            work = json.load(open(f))
        except Exception:
            continue
        secs = work.get("sections") if isinstance(work, dict) else None
        if not isinstance(secs, list):
            continue
        out, changed = [], []
        for sec in secs:
            r = split(sec) if isinstance(sec, dict) else None
            if not r:
                out.append(sec)
                continue
            state = (sec.get("integrity") or {}).get("state")
            if state != "complete":
                print(f"  SKIP {os.path.basename(f)} {sec.get('citation')}: integrity "
                      f"{state!r}, not complete - splitting it would mislabel a partial text")
                out.append(sec)
                continue
            head, parts = r
            parent = dict(sec, text=head)
            parent["integrity"] = dict(sec["integrity"], stored_chars=len(head),
                                       source_chars=len(head),
                                       basis=(f"{len(head):,} characters after {len(parts)} "
                                              f"appendix(es) glued to this section were lifted "
                                              f"into their own sections ({now}); nothing cut."),
                                       stamped_at=now)
            out.append(parent)
            for cit, body in parts:
                out.append({"citation": cit, "kind": "appendix", "page": sec.get("page"),
                            "text": body, "resegmented_from": sec.get("citation"),
                            "integrity": {"state": "complete",
                                          "basis": (f"Lifted from {sec.get('citation')}, whose "
                                                    f"stored body was complete; {len(body):,} "
                                                    f"characters, nothing cut."),
                                          "stamped_at": now, "stored_chars": len(body),
                                          "source_chars": len(body),
                                          "cap_applied": sec["integrity"].get("cap_applied")}})
            changed.append((sec.get("citation"), [c for c, _ in parts]))
        if changed:
            total += sum(len(p) for _, p in changed)
            for c, p in changed:
                print(f"  {os.path.basename(f)[:60]}: {c} -> {p}")
            if a.write:
                work["sections"] = out
                work.setdefault("resegmentations", []).append(
                    {"at": now, "tool": "resegment_appendices", "lifted": dict(changed)})
                tmp = f + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump(work, fh, indent=1)
                os.chmod(tmp, os.stat(f).st_mode & 0o777)
                os.replace(tmp, f)
    print(f"{'WROTE' if a.write else 'WOULD LIFT'} {total} appendix section(s)"
          + ("" if a.write else " - preview only; --write to apply, then reindex_terms"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
