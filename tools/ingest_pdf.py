#!/usr/bin/env python3
"""Ingest a PDF into an agent's reference library as a citation-addressable index.

    python3 tools/ingest_pdf.py <file.pdf> --agent accounting_agent \
        --title "Securities Exchange Act of 1934" --source "15 U.S.C. 78a et seq. (public domain)"

Why not just drop the PDF in knowledge_base/ and let the CAG cache handle it:

  1. The CAG loader reads text files only and truncates any document at 200,000
     characters. A statute or standards volume is far larger, so most of it would
     silently never be indexed.
  2. CAG scores len(overlap)/len(query_tokens) with no stopword filter, so a long
     passage of boilerplate outscores a short passage that is exactly on point.
     Measured on a real case: irrelevant boilerplate 0.040, correct definition
     0.030. More text makes that worse, not better.

So a PDF is split into SECTIONS and indexed by citation - "ASC 606-10-25-1",
"Section 10(b)", "Rule 10b-5" - and looked up by the citation the document
actually uses. That matches how these sources are cited in practice and keeps
what reaches the model small and on point.

Scanned PDFs have no text layer; this reports that rather than emitting empty
sections. Run OCR first (tesseract is installed) if so.
"""
import argparse, json, os, re, sys

# Citation shapes worth splitting on, most specific first.
SECTION_PATTERNS = [
    (re.compile(r'^\s*(ASC\s+\d{3}-\d{2}-\d{2}-\d+)', re.I), "asc"),
    (re.compile(r'^\s*(IFRS\s+\d+|IAS\s+\d+)\b', re.I), "ifrs"),
    (re.compile(r'^\s*(?:SEC(?:TION)?\.?\s*)?(\d+[A-Za-z]?\([a-z0-9]+\))', re.I), "subsection"),
    (re.compile(r'^\s*(Rule\s+\d+[A-Za-z]?-\d+)', re.I), "rule"),
    (re.compile(r'^\s*(§+\s*\d+[A-Za-z0-9.\-]*)'), "section_sign"),
    (re.compile(r'^\s*(SEC(?:TION)?\.\s+\d+[A-Za-z0-9.\-]*)', re.I), "section"),
]
MIN_SECTION, MAX_SECTION = 80, 4000


def extract_text(path):
    from pypdf import PdfReader
    reader = PdfReader(path)
    pages, empty = [], 0
    for i, page in enumerate(reader.pages):
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        if not t.strip():
            empty += 1
        pages.append(t)
    return pages, empty


def split_sections(pages):
    sections, cur_id, cur_kind, buf, cur_page = [], None, None, [], 1
    def flush():
        if cur_id and buf:
            body = re.sub(r'\s+', ' ', " ".join(buf)).strip()
            if len(body) >= MIN_SECTION:
                sections.append({"citation": cur_id, "kind": cur_kind,
                                 "page": cur_page, "text": body[:MAX_SECTION]})
    for pno, text in enumerate(pages, 1):
        for line in text.splitlines():
            matched = None
            for rx, kind in SECTION_PATTERNS:
                m = rx.match(line)
                if m:
                    matched = (re.sub(r'\s+', ' ', m.group(1).strip()), kind)
                    break
            if matched:
                flush()
                cur_id, cur_kind = matched
                cur_page, buf = pno, [line]
            elif cur_id:
                buf.append(line)
    flush()
    return sections


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--agent", required=True, help="e.g. accounting_agent, legal_agent")
    ap.add_argument("--title", required=True)
    ap.add_argument("--source", required=True,
                    help="Provenance and rights, recorded with every section and shown to the model")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    if not os.path.exists(args.pdf):
        sys.exit(f"not found: {args.pdf}")

    pages, empty = extract_text(args.pdf)
    total = len(pages)
    chars = sum(len(p) for p in pages)
    print(f"  {total} pages, {chars:,} characters, {empty} pages with no text layer")
    if total and empty / total > 0.5:
        print("  WARNING: mostly image pages. This is a scan with no text layer.")
        print("  OCR it first, e.g.:  ocrmypdf in.pdf out.pdf   (tesseract is installed)")
        if chars < 500:
            sys.exit("  nothing to index - aborting rather than writing an empty index")

    sections = split_sections(pages)
    root = args.out_dir or os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "reference", args.agent)
    os.makedirs(root, exist_ok=True)
    slug = re.sub(r'[^a-z0-9]+', '_', args.title.lower()).strip('_')[:60]
    out = os.path.join(root, f"{slug}.json")

    doc = {"title": args.title, "source": args.source,
           "origin_pdf": os.path.basename(args.pdf),
           "pages": total, "sections": sections}
    with open(out, "w") as fh:
        json.dump(doc, fh, indent=0)

    print(f"  {len(sections)} citation-addressable sections -> {out}")
    if not sections:
        print("  No citations matched. The document may not use a recognised citation")
        print("  format; it is stored with zero sections rather than as one unusable blob.")
    else:
        kinds = {}
        for s in sections:
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
        print("  by kind:", ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
        for s in sections[:3]:
            print(f"    {s['citation'][:28]:30} p{s['page']:<4} {s['text'][:60]}...")


if __name__ == "__main__":
    main()
