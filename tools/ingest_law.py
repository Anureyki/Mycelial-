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


def fetch_usc(title, year="2024"):
    url = (f"https://www.govinfo.gov/bulkdata/USCODE/{year}/title{title}/"
           f"USCODE-{year}-title{title}.xml")
    body = _strip(_get(url, timeout=600))
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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=["cfr", "usc", "irm"])
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

    if len(body) < 2000:
        print(f"REFUSING: retrieved only {len(body)} characters - that is not a "
              f"body of law, it is an error page.", file=sys.stderr)
        return 2

    txt = os.path.join(TMP, stem + ".txt")
    with open(txt, "w") as fh:
        fh.write(body)
    print(f"  fetched {len(body):,} characters -> {txt}")
    return subprocess.call([sys.executable,
                            os.path.join(ROOT, "tools", "ingest_pdf.py"),
                            txt, "--agent", a.agent, "--title", title,
                            "--source", source])


if __name__ == "__main__":
    sys.exit(main())
