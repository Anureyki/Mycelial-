"""GSA Directives Library monitor - the governance spine, read from the source.

The principal's monitor (2026-10-16): ingest GSA's Directives Library on a
schedule, flag anything whose outdated date approaches 2028, and map each
active order to the agent behaviours it constrains.

The library publishes each number series as a table - Number, Title, Issue
Date, Outdated Date - at /directives-library/by-number?num_series=NNNN. That
table is the source of the dates; nothing is typed in by hand.

A MAPPING IS SET FROM THE CONTENT, NEVER FROM THE TITLE. Which agent
behaviour a directive constrains is a standing field, and filling it from a
title is the error CLAUDE.md names ("a standing field is set from the
content, never from the wrapper"). Every directive starts `unmapped`; a
mapping needs the basis - the directive's own words - recorded with it.
"""
import html
import re
import urllib.request
from datetime import date, datetime

BASE = "https://www.gsa.gov"
SERIES = ("1000", "2000", "3000", "4000", "5000", "6000", "7000", "8000", "9000", "10000", "other")
ROW = re.compile(r"(?is)<tr[^>]*>(.*?)</tr>")
CELL = re.compile(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>")
HREF = re.compile(r'href="(/directives-library/\d+)"')


def _clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def _date(s):
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y").date().isoformat()
    except Exception:
        return None


def parse_series(page_html, series):
    out = []
    for r in ROW.findall(page_html or ""):
        cells = [_clean(c) for c in CELL.findall(r)]
        if len(cells) < 4 or cells[0].lower() == "number":
            continue
        link = HREF.search(r)
        out.append({"number": cells[0], "title": cells[1], "issued": _date(cells[2]),
                    "outdated": _date(cells[3]), "outdated_raw": cells[3], "series": series,
                    "url": BASE + link.group(1) if link else None})
    return out


def fetch_series(series, timeout=60):
    url = f"{BASE}/directives-library/by-number?num_series={series}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (MycOS Legal directives monitor)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace"), url


def scan(fetch=fetch_series):
    """-> {directives, by_series, errors}. A series that cannot be read is an
    error, never an empty series - 'nothing listed' and 'could not look' are
    different findings."""
    rows, by, errors = [], {}, []
    for s in SERIES:
        try:
            page, _ = fetch(s)
            got = parse_series(page, s)
            by[s] = len(got)
            rows.extend(got)
        except Exception as exc:
            errors.append({"series": s, "error": f"{type(exc).__name__}: {exc}"})
    return {"directives": rows, "by_series": by, "errors": errors,
            "scanned_at": datetime.now().isoformat(timespec="seconds")}


def flags(directives, today=None, horizon="2028-12-31"):
    """Outdated already, or outdated on or before the horizon."""
    t = (today or date.today()).isoformat()
    past, soon, undated = [], [], []
    for d in directives:
        o = d.get("outdated")
        if not o:
            undated.append(d["number"])
        elif o < t:
            past.append({"number": d["number"], "title": d["title"], "outdated": o})
        elif o <= horizon:
            soon.append({"number": d["number"], "title": d["title"], "outdated": o})
    soon.sort(key=lambda x: x["outdated"])
    return {"past_outdated_date": past, "outdated_by_horizon": soon, "no_outdated_date": undated,
            "horizon": horizon, "as_of": t}
