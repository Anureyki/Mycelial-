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
    """Fetch, transparently decompressing.

    ACCEPT-ENCODING IS NOT OPTIONAL ANY MORE. eCFR's /full/ endpoint began
    answering every request without one with HTTP 406 and this body:

        This endpoint requires response compression. Send an Accept-Encoding
        header that permits compression.

    urllib sends no Accept-Encoding of its own, so every CFR acquisition and
    every re-ingest through this tool failed - loudly, which is the one good
    thing about it. Asking for gzip means handling gzip, because a server that
    honours the header returns bytes that are not text."""
    for a in range(tries):
        try:
            r = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "*/*",
            })
            resp = urllib.request.urlopen(r, timeout=timeout)
            raw = resp.read()
            enc = (resp.headers.get("Content-Encoding") or "").lower()
            if "gzip" in enc:
                import gzip as _gzip
                raw = _gzip.decompress(raw)
            elif "deflate" in enc:
                import zlib as _zlib
                try:
                    raw = _zlib.decompress(raw)
                except _zlib.error:
                    raw = _zlib.decompress(raw, -_zlib.MAX_WBITS)
            return raw
        except Exception:
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


def _reject_error_page(body, url, minimum=20000):
    """`minimum` is the size below which the body cannot be the document
    asked for. The default suits a U.S. Code title, which is megabytes. A
    single Public Law is a different shape: Pub. L. 115-59 is two sections
    and 4,057 characters in its entirety, and was refused as an index page by
    a floor sized for a title. The caller that knows what it asked for sets
    the floor; the error-page markers apply regardless."""
    head = (body or "")[:4000].lower()
    hit = next((m for m in _ERROR_PAGE_MARKERS if m in head), None)
    if hit:
        raise SystemExit(
            f"REFUSED: {url}\n"
            f"  The server returned a page containing '{hit}' rather than the document.\n"
            f"  This arrives as HTTP 200, so only the body reveals it. Nothing was\n"
            f"  written - a corpus file with zero sections is worse than no file,\n"
            f"  because it sits on the shelf looking like law.")
    if len((body or "").strip()) < minimum:
        raise SystemExit(
            f"REFUSED: {url}\n"
            f"  Only {len((body or '').strip()):,} characters came back; the document\n"
            f"  asked for cannot be under {minimum:,}. This is an index or an error\n"
            f"  page. Nothing was written.")
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


def fetch_plaw(congress, number):
    """One Public Law, from govinfo, in its enacted form.

    WHY A SEPARATE KIND. A statutory NOTE - Pub. L. 115-59 s.2, the mailing
    restriction on Social Security numbers - lives after the section text on
    Cornell, in the annotations that fetch_usc_section deliberately excludes.
    So 42 U.S.C. 405 was shelved at 60,000 characters and the one provision
    that mattered was not in it. The enacted law is the honest source for a
    note: it is the text Congress passed, not an editor's placement of it."""
    url = (f"https://www.govinfo.gov/content/pkg/PLAW-{congress}publ{number}/"
           f"html/PLAW-{congress}publ{number}.htm")
    raw = _get(url, timeout=60)
    body = _strip(raw)
    # An enacted law can be a page. The structural check below - it must
    # contain a SEC. 1 - is what separates a short law from an error shell.
    _reject_error_page(body, url, minimum=1000)
    if "SEC. 1" not in body and "SECTION 1" not in body.upper():
        raise SystemExit(f"REFUSED: {url} does not read as an enacted law. "
                         f"Nothing written.")
    m = re.search(r"An Act\s+(.{10,200}?)\.\s", body, re.S)
    heading = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    # Collapse runs of spaces WITHIN a line and keep the line breaks. The
    # first version flattened the whole law onto one line, and every section
    # pattern in ingest_pdf is anchored at line start - so `SEC. 2.` was in the
    # text and no segmenter could see it, and the law shelved as 0 sections.
    text = re.sub(r"[ \t]+", " ", body)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return (text, f"Pub. L. {congress}-{number}",
            f"Public Law {congress}-{number}, enacted text from govinfo.gov "
            f"(GPO). A United States government work, public domain."
            + (f" {heading}." if heading else ""))


# Texas codes, as enacted, from texas.public.law. The state's own site
# (statutes.capitol.texas.gov) is an Angular shell that answers 200 with no
# statute in the body; public.law serves each section as plain HTML with the
# operative text in <section class="... non-meta outline"> and the enactment
# history in <section class="meta ...">. Chapter 9 was shelved from here by
# hand on 2026-08-29; this makes it a fetch kind Legal can run itself.
TEXAS_CODES = {
    "bc":     ("tex._bus._&_com._code", "Tex. Bus. & Com. Code",
               "Texas Business and Commerce Code"),
    "transp": ("tex._transp._code", "Tex. Transp. Code", "Texas Transportation Code"),
    "prop":   ("tex._prop._code", "Tex. Prop. Code", "Texas Property Code"),
    "fin":    ("tex._fin._code", "Tex. Fin. Code", "Texas Finance Code"),
    "penal":  ("tex._penal_code", "Tex. Penal Code", "Texas Penal Code"),
}

# The State's own publisher, per code. Used when the mirror is short - see
# fetch_texas_official.
TEXAS_OFFICIAL = {"bc": "BC", "transp": "TN", "prop": "PR", "fin": "FI", "penal": "PE"}


def fetch_texas_official(code, chapter):
    """One chapter from statutes.capitol.texas.gov, rendered.

    WHY THIS EXISTS, and it is not a preference for the official source on
    principle. texas.public.law is a mirror, and on 2026-09-18 its index of
    Finance Code chapter 392 held 16 sections and went straight from 392.307
    to 392.401 - Section 392.308, CONSUMER VICTIM OF IDENTITY THEFT, was
    simply absent. The whole Texas coerced-debt mechanism turns on that
    section. A mirror that silently omits a section is worse than one that
    fails, because the corpus looks complete.

    The State's own site is an Angular application that answers a plain GET
    with 'Retrieving statute document...', which is why the mirror was used
    in the first place. The page_render kit renders it; if the kit is not
    installed this raises with the install command rather than falling back
    to the short source."""
    import subprocess as _sp
    url = (f"https://statutes.capitol.texas.gov/Docs/{TEXAS_OFFICIAL[code]}/htm/"
           f"{TEXAS_OFFICIAL[code]}.{chapter}.htm")
    proc = _sp.run([sys.executable, os.path.join(ROOT, "tools", "fetch_page.py"),
                    url, "--wait-for-text", f"{chapter}."],
                   capture_output=True, text=True, timeout=300)
    try:
        got = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        raise SystemExit(f"REFUSED: the renderer returned nothing usable for {url}. "
                         f"{proc.stderr[-300:]}")
    if not got.get("ok"):
        raise SystemExit(f"REFUSED: {url} - {got.get('error')}"
                         + (f"  Install it: {got['install']}" if got.get("install") else ""))
    body = got["text"]
    # The rendered markdown carries a self-link after every section heading;
    # strip those so the heading sits at the start of its own line, which is
    # what every segmenter pattern anchors on.
    body = re.sub(r"\[\]\(https?://[^)]*\)", "", body)
    body = re.sub(r"\[([^\]]*)\]\(https?://[^)]*\)", r"\1", body)
    body = re.sub(r"(?m)^\s*(Sec\.\s*\d)", r"\1", body)
    body = re.sub(r"\s*(Sec\.\s*\d+[A-Za-z]?\.\d+[A-Za-z]?\.)", r"\n\1", body)
    # ONE CITATION FORM ACROSS BOTH SOURCES. The State writes "Sec. 392.308."
    # and the mirror writes "§ 392.308"; shelved as the former, every section
    # of these three chapters was unreachable by the number a person actually
    # types. The mirror path already normalises its folded headings for the
    # same reason - this is that rule applied to the official source.
    body = re.sub(r"(?m)^Sec\.\s*(\d+[A-Za-z]?\.\d+[A-Za-z]?)\.\s*",
                  "\u00a7 \\1 ", body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    _reject_error_page(body, url, minimum=2000)
    n = len(re.findall(r"(?m)^\u00a7 \d", body))
    if n == 0:
        raise SystemExit(f"REFUSED: {url} rendered but no section headings were found. "
                         f"Nothing written.")
    name = TEXAS_CODES[code][2]
    abbrev = TEXAS_CODES[code][1]
    m = re.search(r"CHAPTER\s+" + str(chapter) + r"\.\s*([A-Z][A-Z \-,'&]{3,80})", body)
    chapter_name = (m.group(1).strip().title() if m else "")
    title = f"{abbrev} Chapter {chapter}" + (f" - {chapter_name}" if chapter_name else "")
    source = (f"{name}, Chapter {chapter}, {n} sections, retrieved from "
              f"statutes.capitol.texas.gov - the State's OWN publisher - and rendered, "
              f"{time.strftime('%Y-%m-%d')}. Used in preference to the texas.public.law "
              f"mirror because that mirror's index of Fin. Code ch. 392 omitted "
              f"Sec. 392.308 entirely. STATE STATUTE - public domain: a legislature's "
              f"enactment is an edict of government (Georgia v. Public.Resource.Org, "
              f"590 U.S. 255 (2020)).")
    return body, title, source
_TEXAS_PAUSE = 0.6      # courtesy spacing between section fetches


def fetch_texas(code, chapter):
    """One chapter of a Texas code: every section, from the chapter index.

    A state's enactment is public domain - Georgia v. Public.Resource.Org,
    590 U.S. 255 (2020) - and for the UCC it is also the text that actually
    governs, where the ALI/ULC model act is copyrighted and governs nowhere.
    """
    if code not in TEXAS_CODES:
        raise SystemExit(f"REFUSED: unknown Texas code {code!r}; "
                         f"known: {sorted(TEXAS_CODES)}")
    slug, abbrev, longname = TEXAS_CODES[code]
    base = "https://texas.public.law/statutes/"
    index_url = f"{base}{slug}_chapter_{chapter}"
    index = _get(index_url, timeout=60)
    index = index.decode("utf-8", "replace") if isinstance(index, bytes) else index
    # The slug carries "&", which the index page writes as "&amp;". Escaped
    # by hand: re.escape turns "&" into "\&" and a replace on that produced
    # an unbalanced pattern.
    slug_rx = slug.replace(".", r"\.").replace("&", "(?:&|&amp;)")
    hrefs = sorted(set(re.findall(
        rf'href="[^"]*?{slug_rx}_section_({re.escape(str(chapter))}\.[0-9A-Za-z]+)"',
        index)), key=lambda x: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", x)])
    if not hrefs:
        raise SystemExit(f"REFUSED: {index_url} lists no sections of chapter {chapter}. "
                         f"Nothing written.")
    chapter_name = ""
    m = re.search(r"<title>[^<]*?Chapter\s+\S+\s*[\u2013-]\s*([^<|]+)", index, re.I)
    if m:
        chapter_name = re.sub(r"\s+", " ", m.group(1)).strip()
    out, missing, folded = [], [], 0
    for sec in hrefs:
        url = f"{base}{slug}_section_{sec}"
        try:
            page = _get(url, timeout=60)
        except Exception as exc:
            missing.append(f"{sec}: {exc}")
            continue
        page = page.decode("utf-8", "replace") if isinstance(page, bytes) else page
        nm = re.search(r'<span id="name">\s*(.*?)\s*</span>', page, re.S)
        heading = html.unescape(re.sub(r"\s+", " ", nm.group(1))).strip() if nm else ""
        bodies = re.findall(r'<section class="[^"]*non-meta[^"]*">(.*?)</section>', page, re.S)
        text = "\n".join(html.unescape(re.sub(r"<[^>]+>", " ", b)).strip() for b in bodies)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        if not text:
            missing.append(f"{sec}: no operative text found on the page")
            continue
        # THE INDEX IS SHORT. Chapter 2 lists 88 sections and has 103:
        # public.law folds 2.104-2.106, 2.319-2.325 and others into the
        # neighbouring page's body under the state's own "Sec. 2.104.
        # HEADING." lines. Those are real sections and the segmenter
        # recovers them - but keyed "Sec. 2.104.", a form the lookup does
        # not normalise, while everything else is "§ 2.104". Rewritten to
        # the one form here, so a folded section is reachable exactly like
        # a listed one. A prefix naming THIS section is dropped as redundant
        # with the heading line written below.
        text = re.sub(rf"^\s*Sec\.\s*{re.escape(sec)}\.\s*", "", text)
        text, n_folded = re.subn(r"(?m)^\s*Sec\.\s*(\d+[A-Za-z]?\.\d+[A-Za-z]?)\.\s*",
                                 "\u00a7 \\1 ", text)
        folded += n_folded
        hist = re.findall(r'<section class="meta[^"]*">(.*?)</section>', page, re.S)
        history = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", h)).strip() for h in hist)
        history = re.sub(r"\s+", " ", history).strip()
        out.append(f"\u00a7 {sec} {heading}\n{text}"
                   + (f"\n[History: {history}]" if history else ""))
        time.sleep(_TEXAS_PAUSE)
    if not out:
        raise SystemExit(f"REFUSED: none of {len(hrefs)} sections of chapter {chapter} "
                         f"yielded text. Nothing written.")
    body = "\n\n".join(out)
    title = f"{abbrev} Chapter {chapter}" + (f" - {chapter_name}" if chapter_name else "")
    source = (f"{longname}, Chapter {chapter}, {len(out)} of {len(hrefs)} indexed sections"
              + (f" plus {folded} sections the site folds into neighbouring pages, "
                 f"recovered by their own headings" if folded else "") + ", "
              f"retrieved from texas.public.law {time.strftime('%Y-%m-%d')}. STATE "
              f"STATUTE - public domain: a legislature's enactment is an edict of "
              f"government (Georgia v. Public.Resource.Org, 590 U.S. 255 (2020)). "
              f"This is Texas's ENACTMENT, which is the text that governs in Texas."
              + (f" NOT RETRIEVED: {'; '.join(missing)}." if missing else ""))
    return body, title, source


def fetch_orc(section):
    """One section of the Ohio Revised Code, from the state's own site.

    codes.ohio.gov serves the statutory text in the response body - unlike
    Texas, whose statutes and rules are both Angular applications that answer
    200 with an empty shell. So Ohio is scriptable and Texas is not, which is a
    fact about the two states' publishing choices and worth recording where the
    next person will look for it.

    The page carries navigation, amendment history and cross-reference chrome
    around the operative text. Only the text between the section heading and
    the effective-date footer is taken.
    """
    url = f"https://codes.ohio.gov/ohio-revised-code/section-{section}"
    raw = _get(url, timeout=45)
    body = _strip(raw)
    if "Any judgment" not in body and "Section " + str(section) not in body:
        _reject_error_page(body, url)
    m = re.search(r"Latest Legislation:[^\n]{0,120}?(?:General Assembly)\s*(.+?)"
                  r"(?:Available Versions of this Section|Last updated|Cite as)",
                  body, re.S)
    if not m or len(m.group(1).strip()) < 120:
        m = re.search(r"(Any judgment.+?)(?:Available Versions|Last updated|Cite as)",
                      body, re.S)
    if not m or len(m.group(1).strip()) < 120:
        raise SystemExit(
            f"REFUSED: could not isolate the operative text of ORC {section} from {url}.\n"
            f"  Storing the whole page would put site navigation into the corpus as though "
            f"it were statute. Nothing written.")
    operative = re.sub(r"\s+", " ", m.group(1)).strip()
    heading = ""
    hm = re.search(r"Section\s+" + re.escape(str(section)) + r"\s*\|\s*([^\n|]{3,120})", body)
    if hm:
        heading = hm.group(1).strip()
    text = (f"\u00a7 {section}. {heading}. {operative}" if heading
            else f"\u00a7 {section}. {operative}")
    return (text,
            f"Ohio Rev. Code \u00a7 {section}",
            f"Ohio Revised Code section {section}, retrieved from the State of Ohio's own "
            f"publication at codes.ohio.gov. State statutes are government works and are "
            f"public domain.")

# LIFTED OUT OF main() SO THE LEGAL AGENT CAN USE IT.
#
# The agent needs to acquire its own authority - CLAUDE.md is explicit that a
# capability belongs in the domain agent and that substituting a hand-run tool
# "leaves the agent exactly as capable as it was". What it must NOT do is grow
# its own copy of this table and this stamping. Two classifiers is two answers
# to "is this a regulation", and the copy is always the one that drifts.
#
# So main() and the agent are two CALLERS of one implementation.
CLASSIFICATION = {
    "cfr": ("regulation",
            "Title of the work is a CFR part citation, which fixes the class",
            "doctrinal"),
    "usc": ("federal_statute",
            "Title of the work is a U.S. Code title citation, which fixes the class",
            "doctrinal"),
    "usc-section": ("federal_statute",
                    "Title of the work is a U.S. Code section citation, which fixes "
                    "the class",
                    "doctrinal"),
    "plaw": ("federal_statute",
             "Title of the work is a Public Law citation, which fixes the class",
             "doctrinal"),
    "tex-official": ("state_statute",
                     "Title of the work is a Texas code chapter citation, which fixes the class",
                     "doctrinal"),
    "tex": ("state_statute",
            "Title of the work is a Texas code chapter citation, which fixes the class",
            "doctrinal"),
    "orc": ("state_statute",
            "Title of the work is an Ohio Revised Code section citation, which fixes "
            "the class",
            "doctrinal"),
    "irm": ("agency_guidance",
            "Internal Revenue Manual - directs IRS personnel, confers no rights on "
            "taxpayers, and courts have held it lacks the force of a regulation",
            "unknown"),
    # A COURT'S OWN TEXT, as filed. The class is fixed by what the document
    # is; the claim layer is NOT - a holding states the rule and the dicta
    # around it describe, explain and sometimes argue, and which is which is
    # a reading, not a stamp. Legal's live column (CLAUDE.md): how courts
    # actually rule, shelved beside the statutes they apply.
    "opinion": ("case_law",
                "The work is a court's opinion or order, read from the docket as "
                "filed; a judicial decision is case law by what it is",
                "unknown"),
}


def shelve(body, title, source, agent, source_kind, stem=None, treatise=False):
    """Write a fetched body into an agent's reference corpus, classed.

    Returns {"ok": bool, "path": str|None, "authority_class", "claim_layer",
    "error": str|None}. Raises nothing the caller has to catch.

    CLAIM LAYER, alongside authority class, and set at the same moment.

    Every statute this tool acquired arrived with `claim_layer: unknown`, whose
    stored meaning is "treat any citation from here as unclassified: it may be
    rationale rather than rule". That is exactly backwards for the one class of
    work where the layer is not a judgement call. A statute does not describe
    the rule or argue for it - it IS the rule, and the citation in the title
    fixes that as definitionally as it fixes the authority class.

    42 federal statutes, 11 regulations and 6 state statutes sat on the shelves
    telling every reader they might be somebody's rationale.

    The IRM is the one source here that is NOT doctrinal by construction: it
    directs personnel and states agency practice rather than law, so it stays
    unknown until somebody reads it, which is the honest value."""
    klass, basis, layer = CLASSIFICATION[source_kind]
    os.makedirs(TMP, exist_ok=True)
    stem = stem or re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:40]
    txt = os.path.join(TMP, stem + ".txt")
    with open(txt, "w") as fh:
        fh.write(body)

    # PASSED IN, not written over the finished file afterwards.
    #
    # The old shape re-opened the JSON and set `authority_class` on the
    # DOCUMENT only, while ingest_pdf had already stamped every SECTION from
    # its own guess at the title. For the IRM those disagreed: the document
    # read `agency_guidance` and all 10,233 sections read None. Both fields are
    # read at lookup - authority class off the document, claim layer off the
    # section - so a disagreement between the two levels is a disagreement the
    # reader cannot see. One ingest, one classification, both levels.
    # An opinion has no numbered sections; it is addressable by page and by
    # the authorities each passage cites, which is what --treatise keys on.
    # Without it the section splitter finds no headings and shelves 0
    # sections - the empty-shell shape that sits on the shelf looking like law.
    cmd = [sys.executable, os.path.join(ROOT, "tools", "ingest_pdf.py"),
           txt, "--agent", agent, "--title", title, "--source", source,
           "--authority-class", klass, "--claim-layer", layer]
    if treatise:
        cmd.append("--treatise")
    rc = subprocess.call(cmd)
    if rc != 0:
        return {"ok": False, "path": None, "error": f"ingest_pdf exited {rc}",
                "authority_class": klass, "claim_layer": layer}
    out = _written_path(agent, title)
    if not (out and os.path.exists(out)):
        return {"ok": False, "path": None,
                "error": "could not locate the written file to stamp authority_class",
                "authority_class": klass, "claim_layer": layer}
    try:
        with open(out, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["authority_class_basis"] = basis
        if layer != "unknown":
            doc["claim_layer_basis"] = (
                "Set from the class, not from the text: a statute or regulation "
                "states the rule rather than describing or arguing it, which the "
                "citation in the title establishes definitionally.")
        for sec in doc.get("sections", []):
            sec["authority_class"] = klass
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
    except Exception as exc:
        return {"ok": False, "path": out, "error": f"could not stamp: {exc}",
                "authority_class": klass, "claim_layer": layer}
    return {"ok": True, "path": out, "error": None,
            "authority_class": klass, "claim_layer": layer,
            "authority_class_basis": basis, "sections": len(doc.get("sections", []))}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=["cfr", "usc", "usc-section", "irm", "orc", "plaw", "tex"])
    ap.add_argument("--code", help="tex: bc | transp | prop | fin")
    ap.add_argument("--chapter", help="tex: chapter number, e.g. 2")
    ap.add_argument("--congress")
    ap.add_argument("--number")
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
    elif a.source == "plaw":
        if not (a.congress and a.number):
            ap.error("plaw needs --congress and --number, e.g. 115 59")
        body, title, source = fetch_plaw(a.congress, a.number)
        stem = f"plaw{a.congress}_{a.number}"
    elif a.source == "tex":
        if not (a.code and a.chapter):
            ap.error("tex needs --code (bc|transp|prop|fin) and --chapter")
        body, title, source = fetch_texas(a.code, a.chapter)
        stem = f"tex_{a.code}_ch{a.chapter}"
    elif a.source == "orc":
        if not a.section:
            ap.error("orc needs --section, e.g. --section 2329.02")
        body, title, source = fetch_orc(a.section)
        stem = f"orc{a.section}"
    else:
        if not a.part:
            ap.error("irm needs --part")
        body, title, source = fetch_irm(a.part)
        stem = f"irm{a.part}"

    if a.source not in ("usc-section", "orc") and len(body) < 2000:
        print(f"REFUSING: retrieved only {len(body)} characters - that is not a "
              f"body of law, it is an error page.", file=sys.stderr)
        return 2

    print(f"  fetched {len(body):,} characters")

    # CLAIM LAYER, alongside authority class, and set at the same moment.
    #
    # Every statute this tool acquired arrived with `claim_layer: unknown`,
    # whose stored meaning is "treat any citation from here as unclassified: it
    # may be rationale rather than rule". That is exactly backwards for the one
    # class of work where the layer is not a judgement call. A statute does not
    # describe the rule or argue for it - it IS the rule, and the citation in
    # the title fixes that as definitionally as it fixes the authority class.
    #
    # 42 federal statutes, 11 regulations and 6 state statutes sat on the
    # shelves telling every reader they might be somebody's rationale.
    #
    # The IRM is the one source here that is NOT doctrinal by construction: it
    # directs personnel and states agency practice rather than law, so it stays
    # unknown until somebody reads it, which is the honest value.
    res = shelve(body, title, source, a.agent, a.source, stem=stem)
    if not res["ok"]:
        print(f"  WARNING: {res['error']}", file=sys.stderr)
        return 1
    print(f"  authority_class: {res['authority_class']}   "
          f"claim_layer: {res['claim_layer']}")
    return 0


def _written_path(agent, title):
    """Where ingest_pdf puts a work, derived the same way it derives it."""
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:60]
    return os.path.join(ROOT, "reference", agent, slug + ".json")


if __name__ == "__main__":
    sys.exit(main())
