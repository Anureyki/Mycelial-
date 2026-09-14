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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import source_integrity, pdf_text  # noqa: E402

# Citation shapes worth splitting on, most specific first.
SECTION_PATTERNS = [
    (re.compile(r'^\s*(ASC\s+\d{3}-\d{2}-\d{2}-\d+)', re.I), "asc"),
    (re.compile(r'^\s*(IFRS\s+\d+|IAS\s+\d+)\b', re.I), "ifrs"),
    (re.compile(r'^\s*(?:SEC(?:TION)?\.?\s*)?(\d+[A-Za-z]?\([a-z0-9]+\))', re.I), "subsection"),
    (re.compile(r'^\s*(Rule\s+\d+[A-Za-z]?-\d+)', re.I), "rule"),
    # IRM sections head themselves as bare dotted numbers - "5.1.1", "5.1.1.4.2"
    # - on their own line. Requiring THREE or more components keeps this off
    # CFR citations like "210.1-01" and statute subsections like "9.203".
    (re.compile(r'^\s*(\d{1,2}\.\d{1,2}\.\d{1,3}(?:\.\d{1,3})*)\s*$'), "irm"),
    # Dashes: these documents use EN DASH (U+2013) in citations like
    # "210.1-01", not a hyphen. Omitting it truncated the citation at "210.1",
    # collapsing 1-01, 1-02 and 1-03 onto one ambiguous key.
    (re.compile(r'^\s*(§+\s*\d+[A-Za-z0-9.\-\u2010-\u2015]*)'), "section_sign"),
    (re.compile(r'^\s*(SEC(?:TION)?\.\s+\d+[A-Za-z0-9.\-]*)', re.I), "section"),
    # An enacted Public Law opens "SECTION 1. SHORT TITLE." - the word spelt
    # out, no period after it - and every later section is "SEC. 2." The
    # pattern above requires the period, so section 1 of every Public Law
    # was absorbed into the preamble, and section 1 is the one that names
    # the Act. The period after the number is required so a sentence that
    # happens to begin "Section 5 of the Act" is not read as a heading.
    (re.compile(r'^\s*(SECTION\s+\d+[A-Za-z0-9\-]*\.)\s+[A-Z]'), "section"),
    # State codes head a section with its bare number and a period - Delaware's
    # "3806. Management of statutory trust." - while the § form appears only in
    # the table of contents at the top. Matching the § form alone captured the
    # contents listing and left every section body unattached: 4 sections out
    # of ~50, which looked like a successful ingest.
    (re.compile(r'^\s*(\d{3,4}[A-Za-z]?)\.\s+[A-Z]'), "code_section"),
]
# MAX_SECTION was 4000 and silently TRUNCATED. 12 U.S.C. 1813 is a long
# definitions section; it stored the first 4,000 characters, reported "1
# citation-addressable section", and cut off before subsection (l) - the
# definition of "deposit", which was the entire reason for fetching it. A reader
# would get the first third with nothing saying the rest existed.
#
# That is the false-success shape this project hunts, in the corpus itself: a
# section that looks whole and is a fragment, presented to the reasoning layer
# as authority. Raised, and anything still over the limit is marked so the
# truncation is visible rather than silent.
MIN_SECTION, MAX_SECTION = 80, 60000


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
    """-> (pages, extraction record). A .txt input is treated as OCR output
    already extracted - Archive.org's _djvu.txt, for instance, which is often
    spaced correctly where the PDF's own text layer is not.

    The record is built by core/pdf_text.py and distinguishes a page that holds
    no text from a page the extractor could not read. This function used to
    collapse both into an empty string, which is how the tool that fills the
    shelf would have dropped two pages of the EPIC white paper and reported a
    successful ingest."""
    if path.lower().endswith(".txt"):
        raw = open(path, errors="replace").read()
        pages = raw.split("\f") if "\f" in raw else [raw]
        return pages, {"extractor": "text file (already extracted)",
                       "pages": len(pages),
                       "no_text_layer": [i + 1 for i, p in enumerate(pages)
                                         if not p.strip()],
                       "raised": [], "recovered": [], "lost": [],
                       "complete": True}
    return pdf_text.extract_pages(path)


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
                # Integrity is stamped HERE, by the only code that knows.
                # This is the instant at which "was anything cut?" is a fact
                # rather than an inference. It used to be discarded and left
                # for a script to reconstruct from string length later - a
                # guess about form standing in for a record of history.
                sec = {"citation": cur_id, "kind": cur_kind, "page": cur_page,
                       "text": body[:MAX_SECTION]}
                if len(body) > MAX_SECTION:
                    source_integrity.stamp(
                        sec, "truncated",
                        f"Body exceeded MAX_SECTION ({MAX_SECTION:,}) at ingest and was "
                        f"cut. Recorded by the ingester at the moment of cutting.",
                        source_chars=len(body), stored_chars=MAX_SECTION, cap=MAX_SECTION)
                    sec["truncated"] = True          # readers predating the block
                    sec["full_length"] = len(body)
                else:
                    source_integrity.stamp(
                        sec, "complete",
                        f"Full retrieved body of {len(body):,} characters stored; under "
                        f"the {MAX_SECTION:,} cap, so nothing was cut.",
                        source_chars=len(body), stored_chars=len(body), cap=MAX_SECTION)
                sections.append(sec)
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


def load_seed_vocabulary(agent):
    """Hand-curated doctrine terms for this domain, indexed regardless of frequency.

    Frequency mining answers "what does this document repeat", and in a scanned
    law review the answer is the publisher's footer - it indexed "electronic
    copy available" and missed "spendthrift". Worse, it is structurally blind to
    a term of art that appears ONCE, which is exactly how a term appears when it
    is the precise thing somebody needs.

    A seed list turns the indexer from a frequency miner into a domain
    dictionary. It only ever WIDENS what can be found: a seed term absent from
    the text is absent from the index, so nothing is claimed that is not there."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config", "doctrine_seed.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return [t.lower() for t in (json.load(fh).get(agent) or [])]
    except Exception as exc:
        print(f"  seed vocabulary unavailable ({exc}) - frequency terms only")
        return []


def index_terms(sections, min_freq=None, max_terms=2500, seed=()):
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
    # PUBLISHING FURNITURE IS NOT VOCABULARY.
    #
    # Repetition is the signal here, and the most repeated strings in a scanned
    # law review are the things printed on every page: "electronic copy
    # available", "dukeminier supra note", the publisher's name. They indexed
    # beautifully and address nothing. A term that appears on every page
    # discriminates between no two pages.
    FURNITURE = {
        "supra", "infra", "ibid", "idem", "note", "notes", "hereinafter",
        "electronic", "copy", "available", "download", "downloaded", "abstract",
        "journal", "review", "volume", "issue", "article", "press", "university",
        "oxford", "harvard", "cornell", "chicago", "yale", "stanford", "columbia",
        "ssrn", "http", "https", "www", "com", "edu", "org", "reprinted",
        "copyright", "rights", "reserved", "published", "publishing", "edition",
        "manuscript", "draft", "forthcoming", "footnote", "page", "pages",
    }

    freq, per_section = Counter(), []
    for s_ in sections:
        words = re.findall(r"[a-z]+", (s_.get("text") or "").lower())
        grams = set()
        # SINGLE WORDS COME FROM THE SEED, NOT FROM COUNTING.
        #
        # They were mined by frequency here for a good reason: every one-word
        # doctrine term was unreachable by construction - contractarian,
        # spendthrift, disgorgement, prudence, bifurcation, impartiality - and a
        # term printed as a section HEADING could not be looked up in the work
        # that headed a section with it.
        #
        # The seed vocabulary now answers that properly, and all eight of those
        # terms are in it. What the frequency pass adds on top is ordinary
        # legal English, because counting cannot tell "spendthrift" from
        # "accordance". Measured on the Texas Trust Code: 652 single-word keys,
        # of which the first sixty are absence, accept, accepting, accordance,
        # according, achieve, acknowledged, acting, action, activities, actual,
        # actually, addition, address, adjust, advance, affairs, affect,
        # against, another, applicable, applied, applies, applying. A key that
        # returns a third of the shelf is not a subject index.
        #
        # Multi-word grams stay: a phrase disambiguates itself, which is why
        # "ascertainable standard" and "actual knowledge" survive the same pass
        # that produced "actually". So frequency keeps what the seed cannot know
        # in advance, and the seed keeps what frequency cannot recognise.
        for n in (2, 3):
            for i in range(len(words) - n + 1):
                g = words[i:i + n]
                if g[0] in STOP_EDGE or g[-1] in STOP_EDGE:
                    continue
                if any(len(w) < 4 for w in g):
                    continue
                if any(w in FURNITURE for w in g):
                    continue
                grams.add(" ".join(g))
        per_section.append(grams)
        freq.update(grams)
    # NO DOCUMENT-FREQUENCY CEILING, and the reason is worth keeping because
    # the argument for one is good and wrong.
    #
    # "A term that addresses every section addresses none" is the rule the
    # FURNITURE list runs on, and dropping any gram present in more than 40% of
    # a work's sections looked like the same rule generalised. Measured, it cut
    # `agency cost`, `agency costs theory`, `dead hand`, `default rules`,
    # `private trust`, `residual claims`, `settlor standing`, `spendthrift
    # trusts`, `trust property`, `trust protectors` and `trustee removal` out of
    # Sitkoff's agency-costs paper - 71 multi-word keys, and the list reads as a
    # table of contents.
    #
    # The flaw is the unit. Within one work, a term on every page does not
    # discriminate between pages. But this index answers WHICH WORK covers a
    # subject, across a shelf, and a paper that says "agency costs" in every
    # section is exactly the paper to return for agency costs. Saturation is the
    # signal there, not the noise.
    keep = {t for t, c in freq.most_common(max_terms) if c >= min_freq}

    # Seed terms bypass the frequency floor entirely. Present once is enough -
    # that is the whole point, and it is why this is a dictionary rather than a
    # counter.
    # MATCH THE SEED TERM THE WAY THE TEXT IS MATCHED.
    #
    # The haystack is rebuilt as `[a-z]+` runs joined by spaces, so every hyphen
    # and apostrophe in the document is already gone: "entity-level exemption"
    # is "entity level exemption" by the time anything is compared to it. The
    # needle was not put through the same mill, so a seed term written with a
    # hyphen could not match ANY document - unreachable by construction, the
    # same shape as one-word terms of art before the seed existed.
    # "entity-level exemption", "data-level exemption" and "gramm-leach-bliley"
    # were all in the EPIC paper and all returned [no hit].
    #
    # A naive plural of the last word is tried too. Terms of art are written
    # singular in a dictionary and used plural in prose - the paper's own
    # glossary heads the entry "Specialty Consumer Reporting Agencies". This
    # widens what can be FOUND and never what is claimed, which is the seed
    # file's own rule: a term absent from the text stays absent from the index.
    def _variants(term):
        flat = " ".join(re.findall(r"[a-z]+", term))
        if not flat:
            return ()
        head, _, last = flat.rpartition(" ")
        if last.endswith("y") and len(last) > 3:
            plural = last[:-1] + "ies"
        elif last.endswith(("s", "x", "z", "ch", "sh")):
            plural = last + "es"
        else:
            plural = last + "s"
        return (flat, (head + " " + plural).strip())

    seed_variants = [(t, _variants(t)) for t in seed]

    seed_hits = 0
    for i, s_ in enumerate(sections):
        flat = " ".join(re.findall(r"[a-z]+", (s_.get("text") or "").lower()))
        for term, forms in seed_variants:
            if not any(f and f in flat for f in forms):
                continue
            # ALWAYS add to `keep`, even when the term is already in this
            # section's grams. The guard `and term not in per_section[i]` meant a
            # seed term that the unigram pass had already collected was skipped
            # here - and since `keep` is built from the frequency floor, a word
            # appearing twice in a 13-page paper then fell out anyway.
            # "opportunism", "moral hazard" and "prudence" were all in the text,
            # all in per_section, and none of them indexed. Bypassing the floor
            # is the entire purpose of a seed list, so it cannot be conditional
            # on the floor having already passed.
            if term not in per_section[i]:
                per_section[i].add(term)
            if term not in keep:
                keep.add(term)
            seed_hits += 1
    if seed_hits:
        print(f"  {seed_hits} seed-term placements from the domain dictionary")

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
    # FALL BACK TO THE PDF'S OWN PAGE BOUNDARIES.
    #
    # Splitting on a bare page number alone assumes a SCANNED book, where OCR
    # leaves the printed folio on its own line. A born-digital paper has no such
    # marker, so no break is found, the whole work becomes one chunk, and
    # MAX_SECTION then truncates it - 37,613 characters in, 36,661 stored, one
    # section called "part 1". That is not an index, it is the blob this tool
    # exists to avoid, and it looks like coverage on the shelf.
    #
    # `pages` is already one string per page. Where the folio scan finds no
    # structure, use it.
    if len([c for c in chunks if c[1].strip()]) < 2 and len(pages) > 1:
        # str, not int: page_no flows into `(page_no or "").isdigit()` below.
        chunks = [(str(i + 1), pg) for i, pg in enumerate(pages) if pg.strip()]

    # SHORT CHUNKS ARE MERGED, NEVER DISCARDED.
    #
    # A `continue` here threw the text away. On a 65-page law review article
    # with 380 footnote-number lines, almost every chunk is a fragment between
    # two footnote markers - and 118,609 of 216,498 characters, more than half
    # the article, were dropped while the shelf reported "5 sections" as though
    # that were the document. Silent loss that looks like coverage is the exact
    # shape this project hunts.
    #
    # A fragment belongs to the passage it was cut out of, so it is appended to
    # the previous chunk rather than deleted.
    merged, carry = [], None
    for page_no, body in chunks:
        flat_probe = re.sub(r'\s+', ' ', body).strip()
        if len(flat_probe) < MIN_SECTION:
            if merged:
                merged[-1] = (merged[-1][0], merged[-1][1] + "\n" + body)
            else:
                carry = (carry[0] if carry else page_no,
                         ((carry[1] + "\n") if carry else "") + body)
            continue
        if carry:
            body = carry[1] + "\n" + body
            carry = None
        merged.append((page_no, body))
    if carry and merged:
        merged[0] = (merged[0][0], carry[1] + "\n" + merged[0][1])
    elif carry:
        merged.append(carry)
    chunks = merged

    # AN OVERSIZED CHUNK IS SPLIT, NOT CUT.
    #
    # Merging fragments forward made the loss visible - 178,585 characters in
    # one chunk, 60,000 stored, 118,585 honestly reported missing. Honest is
    # better than silent and is still not good: the text exists, the reader
    # cannot reach it, and "truncated" on a 65-page article means most of the
    # argument is absent.
    #
    # A cap is a storage limit, not a reason to lose a document. Where a chunk
    # exceeds it, break it at a sentence boundary into numbered parts so every
    # character is stored and each part stays addressable.
    sized = []
    for page_no, body in chunks:
        probe = dehyphenate(re.sub(r'\s+', ' ', body).strip())
        if len(probe) <= MAX_SECTION:
            sized.append((page_no, body, None))
            continue
        part, cursor, n = [], 0, 0
        while cursor < len(probe):
            end = min(cursor + MAX_SECTION, len(probe))
            if end < len(probe):
                dot = probe.rfind(". ", cursor + int(MAX_SECTION * 0.5), end)
                if dot > cursor:
                    end = dot + 1
            n += 1
            sized.append((page_no, probe[cursor:end], n))
            cursor = end
    chunks = sized

    sections = []
    for entry in chunks:
        page_no, body = entry[0], entry[1]
        part_no = entry[2] if len(entry) > 2 else None
        flat = dehyphenate(re.sub(r'\s+', ' ', body).strip())
        if len(flat) < MIN_SECTION:
            continue
        cases = sorted({f"{a.strip()} v. {b.strip()}" for a, b in CASE_RX.findall(body)})
        sections.append({
            "citation": ((f"p. {page_no}" if page_no else f"part {len(sections) + 1}")
                         + (f" (part {part_no})" if part_no else "")),
            "kind": "treatise",
            "page": int(page_no) if (page_no or "").isdigit() else None,
            "authorities": cases,
            "text": flat[:MAX_SECTION],
            "_full_len": len(flat),
        })
    # Integrity stamped HERE, by the only code holding the full body at the
    # moment it cuts. split_sections already did this; split_treatise did not,
    # so a 60,000-character cap silently produced half a passage with no record
    # that anything was removed - a half passage presented as whole, which is
    # the failure that survives review because everything shown is accurate.
    for sec in sections:
        full_len = sec.pop("_full_len", len(sec.get("text") or ""))
        if full_len > MAX_SECTION:
            source_integrity.stamp(
                sec, "truncated",
                f"Body exceeded MAX_SECTION ({MAX_SECTION:,}) at ingest and was cut. "
                f"Recorded by the ingester at the moment of cutting.",
                source_chars=full_len, stored_chars=MAX_SECTION, cap=MAX_SECTION)
            sec["truncated"] = True
            sec["full_length"] = full_len
        else:
            source_integrity.stamp(
                sec, "complete",
                f"Full retrieved body of {full_len:,} characters stored; under the "
                f"{MAX_SECTION:,} cap, so nothing was cut.",
                source_chars=full_len, stored_chars=full_len, cap=MAX_SECTION)
    return sections


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="a PDF, or a .txt of already-extracted OCR text")
    ap.add_argument("--agent", required=True, help="e.g. accounting_agent, legal_agent")
    ap.add_argument("--title", required=True)
    ap.add_argument("--source", required=True,
                    help="Provenance and rights, recorded with every section and shown to the model")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--claim-layer", default=None,
                    choices=["doctrinal", "explanatory", "normative", "mixed", "unknown"],
                    help="WHAT KIND OF CLAIM the work makes, which is a different axis "
                         "from authority_class. doctrinal = states or describes the rule. "
                         "explanatory = theory about WHY the rule has its shape. "
                         "normative = argues what the rule SHOULD be. mixed = carries more "
                         "than one and the passage must be read to tell. Two works can both "
                         "be authority_class treatise and be these three different things.")
    ap.add_argument("--authority-class", default=None,
                    choices=["federal_statute", "state_statute", "regulation",
                             "court_rules", "case_law", "agency_guidance",
                             "agency_instruction", "treatise", "doctrine_summary",
                             "dictionary", "advocacy", "unknown"],
                    help="How this work should be WEIGHED. Required unless --treatise "
                         "implies it. See CLAUDE.md: the claim pipeline weighs whatever "
                         "it can open as potentially governing, so a work with no class "
                         "is a commentary that can be read as law. "
                         "advocacy = an interested party arguing a position. It is not a "
                         "treatise: a treatise expounds the law and an advocacy paper "
                         "asks for a different one, and the distinction is about standing "
                         "rather than quality. Set it only after READING - CLAUDE.md "
                         "records a talk tagged advocacy from its title that argued the "
                         "opposite of the tag, which is the same error in the other "
                         "direction. "
                         "The set here must match AUTHORITY_RANK in "
                         "core/base_agent.py: that map already ranked "
                         "agency_instruction, doctrine_summary and dictionary "
                         "while this list refused to write them, so a work the "
                         "system knows how to WEIGH could not be re-ingested "
                         "with the class it already had. DoDI 1000.30 hit it.")
    ap.add_argument("--allow-lost-pages", action="store_true",
                    help="shelve the work even though a page could not be read "
                         "by any extractor. The loss is recorded on the document")
    ap.add_argument("--treatise", action="store_true",
                    help="Work has no numbered sections: key by printed page and "
                         "index the authorities each passage cites")
    args = ap.parse_args()

    if not os.path.exists(args.pdf):
        sys.exit(f"not found: {args.pdf}")

    pages, extraction = extract_text(args.pdf)
    total = len(pages)
    empty = len(extraction["no_text_layer"])
    chars = sum(len(p) for p in pages)
    print(f"  {total} pages, {chars:,} characters, {empty} pages with no text layer")
    for line in pdf_text.describe(extraction):
        print(f"  {line}")
    # A page no extractor could read is text that is GONE, and nothing
    # downstream can tell what was on it. The tool already refuses to write an
    # index it knows is bad; this is the same refusal.
    if extraction["lost"] and not args.allow_lost_pages:
        sys.exit("  ABORTING: pages above were lost. Re-run with "
                 "--allow-lost-pages to shelve it anyway; the loss is then "
                 "recorded on the document and travels with every citation.")
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
    # Doctrine terms index every work, not just treatises. Restricting it to
    # --treatise left three CFR parts with term_index: 0, so "reasonable
    # accommodation" could not reach the regulation that defines it while it
    # could reach an 1886 equity treatise. A regulation is MORE worth indexing
    # by subject, not less - nobody looks a duty up by section number they do
    # not already know.
    terms = index_terms(sections, seed=load_seed_vocabulary(args.agent))
    # AUTHORITY CLASS IS NOT OPTIONAL.
    #
    # This tool wrote none at all. A licensed treatise went onto the shelf with
    # authority_class: None, and CLAUDE.md is explicit that the claim pipeline
    # weighs whatever it can OPEN as potentially governing - so scholarly
    # commentary describing the Restatements would have been scored as though
    # it stated them. That is the exact failure the field exists to prevent,
    # produced by the tool that fills the shelf.
    #
    # For a statute or regulation the title IS the citation and fixes the class
    # definitionally. Anything else must be read, or stay unknown.
    ac = args.authority_class or ("treatise" if args.treatise else None)
    if ac is None:
        t = args.title.lower()
        if re.search(r'\bu\.?s\.?c\.?\b|\bact of \d{4}\b', t):
            ac, basis = "federal_statute", "title states a U.S. Code citation - definitional"
        elif re.search(r'\bcfr\b|regulation [a-z]\b', t):
            ac, basis = "regulation", "title states a CFR part - definitional"
        else:
            ac, basis = "unknown", ("Not derivable from the title, and --authority-class "
                                    "was not given. UNKNOWN is the honest value; a guess "
                                    "here launders an assumption into the field the "
                                    "reasoning layer trusts.")
    else:
        basis = (f"--authority-class {ac}" if args.authority_class
                 else "--treatise: scholarly commentary, describes the law without stating it")

    # CLAIM LAYER: a second axis, because authority_class cannot carry this.
    #
    # Three works by the same author were shelved together, all
    # authority_class treatise, and a passage from each came back looking
    # identical at the point of citation. One DESCRIBES the rule as the
    # Restatements state it; one is an economic model of WHY the rule has its
    # shape; one ARGUES what the rule should be. Citing the second as though it
    # were the first is how a confident citation becomes a wrong one, and
    # nothing in the returned object distinguished them.
    cl = args.claim_layer or ("unknown" if not args.treatise else "unknown")
    CL_MEANING = {
        "doctrinal":   "States or describes the rule. Cite for what the law IS.",
        "explanatory": ("A theory about WHY the rule has its shape. NOT evidence of the "
                        "rule. Cite for rationale, never for content."),
        "normative":   ("Argues what the rule SHOULD be. An argument, not a statement of "
                        "law, and the fact that it is well made is not authority."),
        "mixed":       ("Carries more than one layer. The PASSAGE has to be read to tell "
                        "which - the document-level label cannot."),
        "unknown":     ("Not declared at ingest. Treat any citation from here as "
                        "unclassified: it may be rationale rather than rule."),
    }

    doc = {"title": args.title, "source": args.source,
           "authority_class": ac,
           "authority_class_basis": basis,
           "claim_layer": cl,
           "claim_layer_meaning": CL_MEANING[cl],
           "authorities_cited": authorities,
           "term_index": terms,
           "origin_pdf": os.path.basename(args.pdf),
           "extraction": extraction,
           "pages": total, "sections": sections}
    for _s in sections:
        _s.setdefault("authority_class", ac)
        # Stamped on the SECTION, because a passage is what gets cited and a
        # header nobody reads is not a warning.
        _s.setdefault("claim_layer", cl)
        _s.setdefault("claim_layer_meaning", CL_MEANING[cl])
    with open(out, "w") as fh:
        json.dump(doc, fh, indent=0)

    print(f"  {len(sections)} citation-addressable sections -> {out}")
    print(f"  authority_class: {ac}  ({basis[:70]})")
    print(f"  claim_layer: {cl}  ({CL_MEANING[cl][:66]})")
    if cl == "unknown":
        print("  ^ pass --claim-layer, or every citation from this work is unclassified.")
    if ac == "unknown":
        print("  ^ NOTHING may cite this as governing until the class is set.")
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
