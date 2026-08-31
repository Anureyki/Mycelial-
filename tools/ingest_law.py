#!/usr/bin/env python3
"""Acquire any publicly available statute, rule or regulation into an agent's corpus.

    python3 tools/ingest_law.py cfr --title 12 --part 220 --agent legal_agent
    python3 tools/ingest_law.py usc --title 15                --agent legal_agent
    python3 tools/ingest_law.py irm --part 5                  --agent accounting_agent

WHY FETCH-ON-DEMAND RATHER THAN HOARD EVERYTHING.

"Legal should be able to read all publicly available law" is right, and it does
NOT mean every title should sit on disk. The corpus is retrieved by exact
citation, so an unread title contributes nothing to an answer while costing
disk, boot time and index size - the loader indexes eagerly at startup, and the
full CFR plus the full U.S. Code is several million sections. A machine with
7 GB of RAM would spend minutes at boot indexing law nobody asked about.

What matters is that ANY citation can be obtained when it is actually needed.
So this is one command per source, and Legal calls it itself through
`acquire_authority` when asked for something it cannot open. The corpus grows
towards what this principal actually works on rather than towards completeness.

COPYRIGHT. Everything reachable here is public domain: federal statutes and
regulations are U.S. Government works, and a state's ENACTMENT of a uniform act
is state law (edicts of government are uncopyrightable - Georgia v.
Public.Resource.Org, 590 U.S. 255 (2020)). The *model* UCC as published by the
ALI and the Uniform Law Commission is NOT public domain, which is why a state
enactment is the right source and not merely a workaround - the enactment is
also the text that actually governs.
"""
import argparse, html, json, os, re, subprocess, sys, time, urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.environ.get("CLAUDE_JOB_DIR", "/tmp") + "/tmp"


def _get(url, timeout=180, tries=3):
    for a in range(tries):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": UA})
            return urllib.request.urlopen(r, timeout=timeout).read()
        except Exception as e:
            if a == tries - 1:
                raise
            time.sleep(2 + a * 4)


def _strip(raw):
    """XML/HTML to lines, keeping the text and dropping the markup."""
    s = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    s = re.sub(r'(?is)<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>', ' ', s)
    t = html.unescape(re.sub(r'<[^>]+>', '\n', s))
    return "\n".join(l.strip() for l in t.splitlines() if l.strip())


def fetch_cfr(title, part, date="2026-08-01"):
    url = (f"https://www.ecfr.gov/api/versioner/v1/full/{date}/"
           f"title-{title}.xml?part={part}")
    body = _strip(_get(url))
    head = re.search(r'PART \d+[—-]([^\n]+)', body)
    name = (head.group(1).strip().title() if head else f"Part {part}")
    return body, f"{title} CFR Part {part} - {name}", (
        f"Electronic CFR, title {title} part {part}, as of {date}, retrieved "
        f"from ecfr.gov. Federal regulation - public domain.")


# govinfo answers a bad bulkdata path with HTTP 200 and an HTML error page. A
# status check therefore passes, `_strip` reduces the page to its navigation
# text, no citations match, and a corpus file is written claiming to hold a
# title of the U.S. Code while containing zero sections. That file then looks
# exactly like a real one on the shelf.
#
# It happened twice in one session and again today for Title 31, which produced
# `31_u_s_c_2024_edition.json` with 0 sections and the words "Govinfo Bulkdata
# Service Error" inside it. Checking the BODY is the fix; checking the status
# was never going to find it.
_ERROR_PAGE_MARKERS = (
    "bulkdata service error", "service error", "page not found",
    "404 not found", "an error occurred", "browse by category",
)


def _reject_error_page(body, url):
    head = (body or "")[:4000].lower()
    hit = next((m for m in _ERROR_PAGE_MARKERS if m in head), None)
    if hit:
        raise SystemExit(
            f"REFUSED: {url}\n"
            f"  The server returned a page containing '{hit}' rather than the document.\n"
            f"  This arrives as HTTP 200, so only the body reveals it. Nothing was\n"
            f"  written - a corpus file with zero sections is worse than no file,\n"
            f"  because it sits on the shelf looking like law.")
    if len((body or "").strip()) < 20000:
        raise SystemExit(
            f"REFUSED: {url}\n"
            f"  Only {len((body or '').strip()):,} characters came back. A title of the\n"
            f"  U.S. Code is megabytes; this is an index or an error page.\n"
            f"  Nothing was written.")
    return body


def fetch_usc(title, year="2024"):
    url = (f"https://www.govinfo.gov/bulkdata/USCODE/{year}/title{title}/"
           f"USCODE-{year}-title{title}.xml")
    body = _reject_error_page(_strip(_get(url, timeout=600)), url)
    return body, f"{title} U.S.C. ({year} edition)", (
        f"United States Code title {title}, {year} edition, retrieved from "
        f"govinfo.gov bulk data. Federal statute - public domain.")


def fetch_irm(part):
    """The Internal Revenue Manual - the IRS's own operating instructions.

    Not law, and it says so: the IRM binds IRS personnel, confers no rights on
    taxpayers, and courts have repeatedly held it does not have the force of a
    regulation. It is nonetheless the single best statement of what the agency
    will actually DO, which is exactly the "lived data" side of the corpus rule:
    the Code and the regulations are the floor, and this is how the floor is
    administered in practice.
    """
    idx = _strip(_get(f"https://www.irs.gov/irm/part{part}"))
    # Chapter URLs are zero-padded and some carry a revision suffix:
    # /irm/part5/irm_05-001-002r. Matching on the un-padded part number found
    # nothing and produced a 12 KB index page with no law in it.
    raw_idx = _get(f"https://www.irs.gov/irm/part{part}").decode("utf-8", "replace")
    chapters = sorted(set(re.findall(r'/irm/part\d+/irm_\d+-\d+-\d+r?', raw_idx)))
    if not chapters:
        raise RuntimeError(f"no IRM chapters found under part {part} - the "
                           f"index layout has changed; do not ingest the index "
                           f"alone, it contains no law")
    print(f"    {len(chapters)} chapters", file=sys.stderr)
    texts = [idx]
    for c in chapters:
        try:
            texts.append(_strip(_get("https://www.irs.gov" + c)))
            time.sleep(0.4)
        except Exception as e:
            print(f"    skip {c}: {e}", file=sys.stderr)
    body = "\n".join(texts)
    return body, f"Internal Revenue Manual Part {part}", (
        f"IRS Internal Revenue Manual part {part}, retrieved from irs.gov. "
        f"U.S. Government work - public domain. AGENCY GUIDANCE, NOT LAW: the "
        f"IRM directs IRS personnel and confers no rights on taxpayers.")


def fetch_usc_section(title, section):
    """One section of the U.S. Code, from Cornell LII.

    govinfo's bulk endpoint serves whole titles and is currently answering with
    an error page, and uscode.house.gov renders its text in JavaScript, so
    neither is usable from a script today. Cornell mirrors the Code as HTML that
    is actually in the response body.

    Fetching ONE section is also the better unit for this corpus. The whole of
    Title 31 is megabytes of law nobody here has asked about; 31 U.S.C. 5103 is
    the sentence that answers the question. Fetch-on-demand was already the
    design - this makes the demand as small as the question.

    On copyright: the statutory text is a United States government work and is
    public domain wherever it is mirrored. Cornell's own annotations and notes
    are theirs, so only the operative text between the section heading and the
    enacting credits is taken, and the source records that it came via Cornell
    rather than pretending it was fetched from the government directly."""
    url = f"https://www.law.cornell.edu/uscode/text/{title}/{section}"
    raw = _get(url, timeout=60)
    body = _strip(raw)
    low = body[:3000].lower()
    if "page not found" in low or "we couldn't find" in low:
        raise SystemExit(f"REFUSED: {url} returned a not-found page. Nothing written.")

    # Operative text only. Anchoring on the section number alone matched the
    # HTML <title> first and dragged the page chrome in with it - "Please help
    # us improve our site! x No thank you Quick search by citation" landed in
    # the corpus as though it were statute. The content is bracketed by
    # "prev | next" before and the enacting credits after, so anchor on those.
    heading = ""
    hm = re.search(r"U\.?S\.? Code\s*\u00a7\s*" + re.escape(str(section))
                   + r"\s*[-\u2013\u2014]\s*([^|]{2,80}?)\s+U\.?S\.? Code",
                   body, re.I)
    if hm:
        heading = hm.group(1).strip()
    m = re.search(r"prev\s*\|\s*next\s*(.+?)(?:\(\s*Pub\.?\s*L\.?|Historical and Revision|"
                  r"Editorial Notes|U\.S\. Code Toolbox|Statutory Notes)",
                  body, re.S | re.I)
    if not m or len(m.group(1).strip()) < 30:
        raise SystemExit(
            f"REFUSED: could not isolate the operative text of {title} U.S.C. {section} "
            f"from {url}.\n  Storing the whole page would put Cornell's navigation and "
            f"annotations into the corpus as though they were statute. Nothing written.")
    operative = re.sub(r"\s+", " ", m.group(1)).strip()
    text = f"\u00a7 {section}. {heading}. {operative}" if heading else f"\u00a7 {section}. {operative}"
    return (text,
            f"{title} U.S.C. \u00a7 {section}",
            f"United States Code title {title} section {section}, retrieved from Cornell LII "
            f"(law.cornell.edu). The statutory text is a U.S. government work and is public "
            f"domain; Cornell's annotations are excluded.")


# Texas statutes are NOT scriptable from statutes.capitol.texas.gov today.
# The site is an Angular application: /Docs/PR/htm/PR.92.htm answers 200 with a
# 250 KB shell containing none of the statutory text, and /GetStatute?code=PR&
# level=SE&value=92.104 answers 200 with the same shell. Probed 2026-08-31.
# The same class of failure as uscode.house.gov, noted in fetch_usc_section.
# Tex. Prop. Code sections in this corpus were ingested from PDF via
# tools/ingest_pdf.py, which remains the path for state law until a source that
# serves text to a script is found. Adding a `tx` mode that fetches the shell
# would put an empty page into the corpus reporting success, which is the exact
# failure this file already guards against in _reject_error_page.

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=["cfr", "usc", "usc-section", "irm"])
    ap.add_argument("--section", help="single U.S.C. section, e.g. 5103")
    ap.add_argument("--title")
    ap.add_argument("--part")
    ap.add_argument("--year", default="2024")
    ap.add_argument("--date", default="2026-08-01")
    ap.add_argument("--agent", required=True)
    a = ap.parse_args()

    os.makedirs(TMP, exist_ok=True)
    if a.source == "cfr":
        if not (a.title and a.part):
            ap.error("cfr needs --title and --part")
        body, title, source = fetch_cfr(a.title, a.part, a.date)
        stem = f"{a.title}cfr{a.part}"
    elif a.source == "usc-section":
        if not (a.title and a.section):
            ap.error("usc-section needs --title and --section")
        body, title, source = fetch_usc_section(a.title, a.section)
        stem = f"usc{a.title}_{a.section}"
    elif a.source == "usc":
        if not a.title:
            ap.error("usc needs --title")
        body, title, source = fetch_usc(a.title, a.year)
        stem = f"usc{a.title}"
    else:
        if not a.part:
            ap.error("irm needs --part")
        body, title, source = fetch_irm(a.part)
        stem = f"irm{a.part}"

    if a.source != "usc-section" and len(body) < 2000:
        print(f"REFUSING: retrieved only {len(body)} characters - that is not a "
              f"body of law, it is an error page.", file=sys.stderr)
        return 2

    txt = os.path.join(TMP, stem + ".txt")
    with open(txt, "w") as fh:
        fh.write(body)
    print(f"  fetched {len(body):,} characters -> {txt}")
    rc = subprocess.call([sys.executable,
                          os.path.join(ROOT, "tools", "ingest_pdf.py"),
                          txt, "--agent", a.agent, "--title", title,
                          "--source", source])
    if rc != 0:
        return rc

    # CLAUDE.md requires every work to carry `authority_class` alongside the
    # basis for it, and nothing was writing either - the existing files were
    # classified by hand, so anything acquired by this tool arrived unclassified
    # and the claim pipeline could not weigh it. For a statute or a regulation
    # the title IS the citation and fixes the class definitionally, which is the
    # one case where it can be set without reading the text.
    klass, basis = {
        "cfr": ("regulation",
                "Title of the work is a CFR part citation, which fixes the class"),
        "usc": ("federal_statute",
                "Title of the work is a U.S. Code title citation, which fixes the class"),
        "usc-section": ("federal_statute",
                        "Title of the work is a U.S. Code section citation, which fixes "
                        "the class"),
        "irm": ("agency_guidance",
                "Internal Revenue Manual - directs IRS personnel, confers no rights on "
                "taxpayers, and courts have held it lacks the force of a regulation"),
    }[a.source]
    out = _written_path(a.agent, title)
    if out and os.path.exists(out):
        try:
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            doc["authority_class"] = klass
            doc["authority_class_basis"] = basis
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2)
            print(f"  authority_class: {klass}")
        except Exception as exc:
            print(f"  WARNING: could not stamp authority_class on {out}: {exc}",
                  file=sys.stderr)
            return 1
    else:
        print(f"  WARNING: could not locate the written file to stamp authority_class",
              file=sys.stderr)
        return 1
    return 0


def _written_path(agent, title):
    """Where ingest_pdf puts a work, derived the same way it derives it."""
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:60]
    return os.path.join(ROOT, "reference", agent, slug + ".json")


if __name__ == "__main__":
    sys.exit(main())
