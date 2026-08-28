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
    # Dashes: these documents use EN DASH (U+2013) in citations like
    # "210.1-01", not a hyphen. Omitting it truncated the citation at "210.1",
    # collapsing 1-01, 1-02 and 1-03 onto one ambiguous key.
    (re.compile(r'^\s*(§+\s*\d+[A-Za-z0-9.\-\u2010-\u2015]*)'), "section_sign"),
    (re.compile(r'^\s*(SEC(?:TION)?\.\s+\d+[A-Za-z0-9.\-]*)', re.I), "section"),
    # State codes head a section with its bare number and a period - Delaware's
    # "3806. Management of statutory trust." - while the § form appears only in
    # the table of contents at the top. Matching the § form alone captured the
    # contents listing and left every section body unattached: 4 sections out
    # of ~50, which looked like a successful ingest.
    (re.compile(r'^\s*(\d{3,4}[A-Za-z]?)\.\s+[A-Z]'), "code_section"),
]
MIN_SECTION, MAX_SECTION = 80, 4000


def spacing_looks_broken(pages):
    """True when the extraction produced text with no word breaks.

    Some scans carry a text layer whose font encoding defeats pypdf's spacing,
    and it returns "Everykindofvaluablepropertybothrealandpersonal". That is
    not a partial result, it is unusable - a citation index built on it cannot
    be searched and a model reading it sees one enormous word. It also looks
    like a success: pages are non-empty and the character count is healthy.
    Mean token length is the tell; real prose sits near 5."""
    text = " ".join(pages)
    tokens = text.split()
    if len(tokens) < 50:
        return False
    return sum(len(t) for t in tokens) / len(tokens) > 14


def extract_text(path):
    """Pages of text. A .txt input is treated as OCR output already extracted -
    Archive.org's _djvu.txt, for instance, which is often spaced correctly
    where the PDF's own text layer is not."""
    if path.lower().endswith(".txt"):
        raw = open(path, errors="replace").read()
        pages = raw.split("\f") if "\f" in raw else [raw]
        return pages, sum(1 for p in pages if not p.strip())
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


DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015"), "-")


def dehyphenate(text):
    """Rejoin words broken across a line by hyphenation.

    PDF text layers render an end-of-line hyphen as a SPACED hyphen
    ("at - torney", "vol - untary") once the line break is collapsed to a space.
    Left alone a search for "attorney" misses the word entirely - 3,625 such
    breaks in the Federal Rules alone - which quietly degrades every document in
    the corpus. Both sides must be lowercase word fragments so that genuine
    ranges and compounds ("2010 - 2012", "arm's - length") survive."""
    return re.sub(r'([a-z]{2,})\s+[-\u2010-\u2015]\s+([a-z]{2,})', r'\1\2', text)


def normalise_citation(c):
    """Fold the several dash characters these documents use onto one, so
    "210.1-01" is a single key however the typesetter wrote it."""
    return re.sub(r'\s+', ' ', c.translate(DASHES).strip())


def split_sections(pages):
    sections, cur_id, cur_kind, buf, cur_page = [], None, None, [], 1
    def flush():
        if cur_id and buf:
            body = dehyphenate(re.sub(r'\s+', ' ', " ".join(buf)).strip())
            if len(body) >= MIN_SECTION:
                sections.append({"citation": cur_id, "kind": cur_kind,
                                 "page": cur_page, "text": body[:MAX_SECTION]})
    for pno, text in enumerate(pages, 1):
        for line in text.splitlines():
            matched = None
            for rx, kind in SECTION_PATTERNS:
                m = rx.match(line)
                if m:
                    matched = (normalise_citation(m.group(1)), kind)
                    break
            if matched:
                flush()
                cur_id, cur_kind = matched
                cur_page, buf = pno, [line]
            elif cur_id:
                buf.append(line)
    flush()
    return sections


CASE_RX = re.compile(r'\b([A-Z][A-Za-z&.\' ]{2,34}?)\s+v\.\s+([A-Z][A-Za-z&.\' ]{2,34}?)[,.\s]')


STOP_EDGE = {"the","a","an","of","in","to","and","or","is","are","was","were","be","been",
             "that","this","these","those","it","its","as","by","for","with","on","at",
             "from","which","not","but","have","has","had","he","she","they","we","you",
             "his","her","their","our","such","said","would","may","must","shall","can",
             "all","any","no","one","two","upon","under","into","when","where","if","so",
             "there","then","than","also","other","same","own","case","cases","court"}


def index_terms(sections, min_freq=None, max_terms=2500):
    """Doctrine terms a work actually discusses, keyed for exact lookup.

    A treatise is addressable by page and by the cases it cites, which is
    useless to anyone who does not already know the page - the whole point of
    having a retrieval layer is to ask by subject. Running heads would be the
    natural index and this OCR dropped them.

    So the vocabulary comes from the book's own repetition: a phrase Pomeroy
    uses 78 times is one of his subjects, not an incidental word. That keeps
    lookup on EXACT KEYS - CLAUDE.md forbids retrieving reference material by
    bag-of-words similarity, because the cache scores overlap with no stopword
    filter and a long passage of boilerplate outranks a short one that is
    exactly on point. Nothing here is scored or ranked; a term either addresses
    a section or it does not."""
    from collections import Counter
    # Scale with the work. A fixed floor of three sections indexes a
    # 187-section treatise well and a 25-section lecture course barely at all -
    # Maitland came back with two terms for a whole book on equity.
    if min_freq is None:
        min_freq = 3 if len(sections) >= 60 else 2
    freq, per_section = Counter(), []
    for s_ in sections:
        words = re.findall(r"[a-z]+", (s_.get("text") or "").lower())
        grams = set()
        for n in (2, 3):
            for i in range(len(words) - n + 1):
                g = words[i:i + n]
                if g[0] in STOP_EDGE or g[-1] in STOP_EDGE:
                    continue
                if any(len(w) < 4 for w in g):
                    continue
                grams.add(" ".join(g))
        per_section.append(grams)
        freq.update(grams)
    keep = {t for t, c in freq.most_common(max_terms) if c >= min_freq}
    index = {}
    for i, grams in enumerate(per_section):
        for t in grams & keep:
            index.setdefault(t, []).append(i)
    return index


def split_treatise(pages):
    """Segment a work that has no numbered sections.

    A statute or a rule set carries its own addresses; a treatise does not, so
    the citation splitter returns nothing and the document indexes as zero
    sections - honest, and useless. What a treatise IS addressable by is the
    authorities it discusses: you look up Paul v. Virginia and want the passage
    where this author reasons about it.

    So each chunk is keyed by the book's own printed page where one is visible,
    and carries the cases it cites as lookup terms. That keeps retrieval on
    exact headwords rather than bag-of-words similarity."""
    text = "\n".join(pages)
    lines = text.splitlines()
    # The OCR puts a bare page number on its own line between pages.
    breaks = [i for i, l in enumerate(lines) if re.fullmatch(r'\s*\d{1,3}\s*', l)]
    chunks, start, page_no = [], 0, None
    for b in breaks + [len(lines)]:
        body = "\n".join(lines[start:b]).strip()
        if body:
            chunks.append((page_no, body))
        page_no = lines[b].strip() if b < len(lines) else None
        start = b + 1
    sections = []
    for page_no, body in chunks:
        flat = dehyphenate(re.sub(r'\s+', ' ', body).strip())
        if len(flat) < MIN_SECTION:
            continue
        cases = sorted({f"{a.strip()} v. {b.strip()}" for a, b in CASE_RX.findall(body)})
        sections.append({
            "citation": f"p. {page_no}" if page_no else f"part {len(sections) + 1}",
            "kind": "treatise",
            "page": int(page_no) if (page_no or "").isdigit() else None,
            "authorities": cases,
            "text": flat[:MAX_SECTION],
        })
    return sections


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="a PDF, or a .txt of already-extracted OCR text")
    ap.add_argument("--agent", required=True, help="e.g. accounting_agent, legal_agent")
    ap.add_argument("--title", required=True)
    ap.add_argument("--source", required=True,
                    help="Provenance and rights, recorded with every section and shown to the model")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--treatise", action="store_true",
                    help="Work has no numbered sections: key by printed page and "
                         "index the authorities each passage cites")
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

    if spacing_looks_broken(pages):
        sys.exit(
            "  ABORTING: the extracted text has no word breaks - this scan's text layer\n"
            "  defeats pypdf's spacing. It would index as one unsearchable word.\n"
            "  Use the OCR text instead, which is usually spaced correctly:\n"
            "    curl -sL -o book.txt https://archive.org/download/<id>/<id>_djvu.txt\n"
            "    ingest_pdf.py book.txt --agent ... --title ... --source ...")

    sections = split_treatise(pages) if args.treatise else split_sections(pages)
    if not args.treatise and not sections:
        print("  no citation structure found - retry with --treatise to key by page "
              "and cited authority instead")
    root = args.out_dir or os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "reference", args.agent)
    os.makedirs(root, exist_ok=True)
    slug = re.sub(r'[^a-z0-9]+', '_', args.title.lower()).strip('_')[:60]
    out = os.path.join(root, f"{slug}.json")

    authorities = sorted({a for s_ in sections for a in s_.get("authorities", [])})
    terms = index_terms(sections) if args.treatise else {}
    doc = {"title": args.title, "source": args.source,
           "authorities_cited": authorities,
           "term_index": terms,
           "origin_pdf": os.path.basename(args.pdf),
           "pages": total, "sections": sections}
    with open(out, "w") as fh:
        json.dump(doc, fh, indent=0)

    print(f"  {len(sections)} citation-addressable sections -> {out}")
    if authorities:
        print(f"  {len(authorities)} distinct authorities indexed as lookup terms")
    if terms:
        print(f"  {len(terms)} doctrine terms indexed by subject")
    if not sections:
        print("  No citations matched. The document may not use a recognised citation")
        print("  format; it is stored with zero sections rather than as one unusable blob.")
    else:
        kinds = {}
        for s in sections:
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
        print("  by kind:", ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
        for s in sections[:3]:
            print(f"    {s['citation'][:28]:30} p{str(s['page'] or '-'):<4} {s['text'][:60]}...")


if __name__ == "__main__":
    main()
