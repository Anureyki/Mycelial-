"""Page text out of a PDF, with a record of what the extraction could not do.

Two call sites used to hold the identical four lines:

    try:
        t = page.extract_text() or ""
    except Exception:
        t = ""

which makes a page that RAISED indistinguishable from a page that is a scan
with no text layer. They are opposite problems. A scan needs OCR. A page whose
font resource defeats pypdf's width parser has a perfectly good text layer and
OCR would be the wrong advice - another extractor reads it fine.

Measured on EPIC's July 2025 white paper: pypdf raises on pages 13 and 16 of
26 (`invalid literal for int() with base 10: '/Kids'`). Under the old code both
became empty strings, were counted as "pages with no text layer", and the
ingest would have reported success having silently dropped the page carrying
the paper's central finding. That is the false-success shape this project
hunts, in the tool that fills the shelf.

So: every page that raises is retried with a second extractor, and the outcome
of every page is recorded where it is known - recovered, or lost, with the
error that caused it. The record is part of the payload, not a log line.
"""
import os


def _pymupdf_pages(path):
    """-> {page_index: text} or {} if pymupdf is unavailable or cannot open it."""
    try:
        import pymupdf  # noqa: F401
    except Exception:
        return {}
    try:
        doc = pymupdf.open(path)
    except Exception:
        return {}
    out = {}
    try:
        for i in range(doc.page_count):
            try:
                out[i] = doc[i].get_text() or ""
            except Exception:
                pass
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return out


def extract_pages(path):
    """-> (pages, record).

    `pages` is one string per page, in order, so page N is always index N-1
    even when its extraction failed - a dropped page would shift every page
    number after it and silently corrupt every citation keyed to one.

    `record` carries what happened, and is meant to be stored, not printed:

        extractor        the primary extractor
        pages            page count
        no_text_layer    pages that extracted cleanly and hold no text - a scan
        raised           [{page, error}] - pages the primary extractor failed on
        recovered        [{page, by, chars}] - of those, the ones a fallback read
        lost             pages no extractor could read. Text that is simply gone
        complete         True only when nothing was lost
    """
    from pypdf import PdfReader
    reader = PdfReader(path)
    pages, raised = [], []
    for i, page in enumerate(reader.pages):
        try:
            t = page.extract_text() or ""
        except Exception as exc:
            raised.append({"page": i + 1,
                           "error": f"{type(exc).__name__}: {exc}"[:200]})
            t = None            # None means "not read", "" means "read, empty"
        pages.append(t)

    recovered = []
    if raised:
        fallback = _pymupdf_pages(path)
        for r in raised:
            t = fallback.get(r["page"] - 1)
            if t and t.strip():
                pages[r["page"] - 1] = t
                recovered.append({"page": r["page"], "by": "pymupdf",
                                  "chars": len(t)})

    recovered_pages = {r["page"] for r in recovered}
    lost = [r["page"] for r in raised if r["page"] not in recovered_pages]
    pages = ["" if p is None else p for p in pages]
    no_text_layer = [i + 1 for i, p in enumerate(pages)
                     if not p.strip() and (i + 1) not in lost]

    return pages, {
        "extractor": "pypdf",
        "pages": len(pages),
        "no_text_layer": no_text_layer,
        "raised": raised,
        "recovered": recovered,
        "lost": lost,
        "complete": not lost,
    }


def describe(record):
    """One or more human lines for a record. Empty list when nothing to say."""
    out = []
    n = record["pages"]
    if record["no_text_layer"]:
        k = len(record["no_text_layer"])
        out.append(f"{k} of {n} page(s) have no text layer (a scan). "
                   f"Those pages hold no text here - OCR the scan first.")
    if record["recovered"]:
        pp = ", ".join(str(r["page"]) for r in record["recovered"])
        out.append(f"{len(record['recovered'])} page(s) failed in pypdf and were "
                   f"recovered with pymupdf: {pp}. Their text IS present.")
    if record["lost"]:
        pp = ", ".join(str(p) for p in record["lost"])
        out.append(f"LOST: {len(record['lost'])} page(s) no extractor could read: "
                   f"{pp}. Their text is NOT below and nothing downstream can "
                   f"tell what was on them.")
    return out
