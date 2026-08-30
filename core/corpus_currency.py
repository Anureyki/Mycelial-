"""Is what this agent holds still the law?

A corpus is a snapshot. The CFR is amended continuously, the U.S. Code is
reissued annually, and a state legislature can recodify an entire title - Texas
moved its Securities Act from Art. 581 to Gov't Code ch. 4001 in 2019, and every
citation written before that is now wrong. An agent reasoning confidently from a
stale section is worse than one that says it does not know, because the answer
looks the same either way.

So each work is checked against the SOURCE THAT PUBLISHES IT, and the result is
one of:

  current      the publisher's latest amendment is not newer than our copy
  stale        the publisher has amended since we retrieved it - re-ingest
  unknown      no automated check exists for this source, and saying so is the
               honest answer rather than reporting "current" by default

That last one matters most. Most state statutes have no version API, so a
checker that silently treated "could not check" as "fine" would be a false
success wearing a timestamp.

This is not a domain. Legal, Trust and Accounting all hold law and all need it,
so it lives here and every agent inherits it.
"""
import json
import os
import re
import urllib.request
from datetime import datetime

ECFR_TITLES = "https://www.ecfr.gov/api/versioner/v1/titles.json"
_cache = {}


def _get(url, timeout=30):
    if url in _cache:
        return _cache[url]
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            _cache[url] = json.load(r)
    except Exception as e:
        _cache[url] = {"_error": str(e)}
    return _cache[url]


def _cfr_latest(title):
    d = _get(ECFR_TITLES)
    if d.get("_error"):
        return None, d["_error"]
    for t in d.get("titles", []):
        if str(t.get("number")) == str(title):
            return (t.get("latest_amended_on") or t.get("latest_issue_date")), None
    return None, f"title {title} not listed"


def _cfr_part_latest(title, part):
    """Newest amendment date for one PART, from the eCFR version feed."""
    if not part:
        return None, "no part number parsed"
    d = _get(f"https://www.ecfr.gov/api/versioner/v1/versions/title-{title}.json?part={part}")
    if d.get("_error"):
        return None, d["_error"]
    dates = [v.get("amendment_date") or v.get("date")
             for v in d.get("content_versions", []) if (v.get("amendment_date") or v.get("date"))]
    if not dates:
        return None, f"no versions listed for {title} CFR {part}"
    return max(dates), None


DATE_RE = re.compile(r"(20\d\d-\d\d-\d\d)")
CFR_RE = re.compile(r"\btitle\s+(\d+)\b.*?\bpart\s+(\w+)", re.I)
CFR_TITLE_RE = re.compile(r"\b(\d+)\s*CFR\b", re.I)
TITLE_PART_RE = re.compile(r"\b(\d+)\s*CFR\s+Part\s+(\w+)", re.I)
USC_RE = re.compile(r"\b(\d+)\s*U\.?S\.?C\.?\b", re.I)


def _identity(doc):
    """What this work IS, from its own title and source line."""
    title = (doc.get("title") or "")
    source = (doc.get("source") or "")
    both = f"{title} {source}"
    out = {"kind": None}
    # "12 CFR Part 220 - Regulation T" in the work's own title carries both
    # numbers; the source line of the earlier ingests carries neither. Read
    # both places before giving up on the part, because a title-only answer
    # marks every part stale and the check stops meaning anything.
    m = TITLE_PART_RE.search(both) or CFR_RE.search(source)
    if m:
        out["kind"] = "cfr"
        out["cfr_title"], out["cfr_part"] = m.group(1), m.group(2)
        return out
    m = CFR_TITLE_RE.search(both)
    if m:
        out["kind"] = "cfr"
        out["cfr_title"], out["cfr_part"] = m.group(1), None
        return out
    if USC_RE.search(both):
        out["kind"] = "usc"
        out["usc_title"] = USC_RE.search(both).group(1)
        return out
    if doc.get("jurisdiction") and doc.get("authority_class") == "state_statute":
        out["kind"] = "state_statute"
        out["state"] = doc["jurisdiction"]
        return out
    if doc.get("authority_class") in ("doctrine_summary", "treatise", "court_rules"):
        out["kind"] = doc["authority_class"]
    return out


def check_work(path):
    """One work: what it is, when we took it, and whether it has moved since."""
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except Exception as e:
        return {"file": os.path.basename(path), "status": "unreadable", "why": str(e)}
    if not isinstance(doc, dict) or "sections" not in doc:
        return None
    name = os.path.basename(path)
    src = doc.get("source") or ""
    retrieved = doc.get("retrieved") or (DATE_RE.search(src).group(1)
                                         if DATE_RE.search(src) else None)
    ident = _identity(doc)
    base = {"file": name, "title": doc.get("title"), "retrieved": retrieved,
            "kind": ident.get("kind"), "sections": len(doc["sections"]),
            "authority_class": doc.get("authority_class")}

    if ident["kind"] == "cfr":
        # Ask about THE PART, not the title. A title is amended almost weekly,
        # so a title-level date marks every part stale and the check stops
        # meaning anything - nine works flagged at once on the first run, most
        # of which had not moved. The part-level feed is exact.
        latest, err = _cfr_part_latest(ident["cfr_title"], ident.get("cfr_part"))
        if err and ident.get("cfr_part"):
            latest, err = _cfr_latest(ident["cfr_title"])
            if latest:
                base["granularity"] = ("title-level - the part feed did not answer, so this "
                                       "says the TITLE moved, not necessarily this part")
        if err or not latest:
            return {**base, "status": "unknown",
                    "why": f"eCFR did not answer for title {ident['cfr_title']}: {err}"}
        base["publisher_latest"] = latest
        if not retrieved:
            return {**base, "status": "unknown",
                    "why": "no retrieval date recorded in this work, so nothing to compare"}
        return {**base, "status": "stale" if latest > retrieved else "current",
                "why": (f"eCFR title {ident['cfr_title']} amended {latest}; this copy is "
                        f"as of {retrieved}")}

    if ident["kind"] == "usc":
        return {**base, "status": "unknown",
                "why": ("the U.S. Code is reissued annually and amended by public law "
                        "continuously; no per-section version feed is wired up. Check the "
                        "section on uscode.house.gov before relying on it in a filing.")}

    if ident["kind"] == "state_statute":
        return {**base, "status": "unknown", "state": ident.get("state"),
                "why": (f"no automated version check exists for {ident.get('state')} "
                        f"statutes. State legislatures also RECODIFY - Texas moved its "
                        f"Securities Act from Art. 581 to Gov't Code ch. 4001 in 2019 - so "
                        f"a citation can be wrong without the text changing.")}

    if ident["kind"] in ("doctrine_summary", "treatise", "court_rules"):
        return {**base, "status": "not_applicable",
                "why": ("authored or historical: it does not go stale the way a statute "
                        "does, but the law it describes can move under it.")}

    return {**base, "status": "unknown", "why": "could not identify the publishing source"}


def survey(ref_dirs):
    """Every work an agent can see, with a plain summary."""
    works, counts = [], {}
    for d in ref_dirs:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            r = check_work(os.path.join(d, fn))
            if r:
                r["corpus"] = os.path.basename(d)
                works.append(r)
                counts[r["status"]] = counts.get(r["status"], 0) + 1
    stale = [w for w in works if w["status"] == "stale"]
    return {"checked_at": datetime.now().strftime("%Y-%m-%d"),
            "works": len(works), "by_status": counts,
            "stale": stale,
            "needs_attention": bool(stale),
            "note": ("'unknown' means no automated check exists for that source - it is "
                     "not a clean bill of health. State statutes and the U.S. Code are "
                     "checked by hand."),
            "detail": works}
