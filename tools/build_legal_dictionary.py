#!/usr/bin/env python3
"""Parse a public-domain law dictionary OCR dump into term -> definition JSON.

Source texts are scans, so the OCR is imperfect ("pnblic" for "public"). That is
tolerable for a reference the model reads for sense, and it is why entries are
kept SHORT and looked up by exact headword rather than fuzzy-matched: a bad
character inside a definition costs little, a bad headword would cost a lookup.

Headwords in these editions are ALL-CAPS at line start followed by a period.
Running heads and page numbers share that shape, so they are filtered by length,
by the digits around them, and by requiring a plausible definition body.
"""
import json, re, sys, unicodedata

HEAD = re.compile(r'^([A-Z][A-Z\'\-&, ]{2,44})\.\s+(.*)$')
PAGENUM = re.compile(r'^\s*\d{1,4}\s*$')
MIN_DEF, MAX_DEF = 60, 900

def clean(text):
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r'-\s*\n\s*', '', text)      # rejoin words split across lines
    text = re.sub(r'\s*\n\s*', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()

def parse(path, source_label):
    entries = {}
    cur_term, buf = None, []
    with open(path, errors="ignore") as fh:
        lines = fh.readlines()
    for raw in lines:
        line = raw.rstrip()
        if PAGENUM.match(line):
            continue
        m = HEAD.match(line)
        if m and not m.group(1).strip().isdigit():
            if cur_term and buf:
                body = clean(" ".join(buf))
                if MIN_DEF <= len(body):
                    entries.setdefault(cur_term, {"term": cur_term,
                                                  "definition": body[:MAX_DEF],
                                                  "source": source_label})
            term = re.sub(r'\s+', ' ', m.group(1).strip().title())
            cur_term, buf = term, [m.group(2)]
        elif cur_term:
            buf.append(line)
    if cur_term and buf:
        body = clean(" ".join(buf))
        if MIN_DEF <= len(body):
            entries.setdefault(cur_term, {"term": cur_term,
                                          "definition": body[:MAX_DEF],
                                          "source": source_label})
    return entries

if __name__ == "__main__":
    src, label, out = sys.argv[1], sys.argv[2], sys.argv[3]
    e = parse(src, label)
    json.dump(e, open(out, "w"), indent=0, sort_keys=True)
    print(f"  {len(e)} entries -> {out}")
    for probe in ("Custodian","Trustee","Beneficiary","Fiduciary","Consideration","Settlor"):
        d = e.get(probe)
        print(f"    {probe:14} {'-' if not d else d['definition'][:110]}")
