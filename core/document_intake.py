"""Take in a document, cut it into clauses, and say who needs to see each one.

The point of this is not filing. It is that a lease, an email chain and a trust
deed all contain OBLIGATIONS - who must do what, by when, and what happens if
they do not - and until those are addressable one at a time, nobody can check
whether the other side is adhering to them. A document sitting in a folder
proves nothing; a document cut into numbered obligations, each with its own
deadline and its own consequence, is a thing you can hold someone to.

Three stages, all deterministic:

  EXTRACT   pypdf for a text layer, tesseract for a photograph or a scan, plain
            read for text. A page with no text layer is REPORTED, never
            silently treated as blank - a lease that OCRs to nothing must fail
            loudly, because a missing clause is worse than a missing document.
  SEGMENT   by the document's own numbering where it has one, and by paragraph
            otherwise. The numbering is the citation: "para 7.1" has to reach
            the same clause every time or nothing built on it can be relied on.
  ROUTE     each clause carries the domains with an interest in it. A late-fee
            clause is Accounting's and Legal's; a trustee-discretion clause is
            Trust's; a notice-and-cure clause is Legal's alone. One clause can
            belong to three departments, and that is the normal case rather
            than an edge one.

Nothing here decides whether a term was breached. It produces the obligations
against which the record can be checked, and the checking is the domain's.
"""
import os
import re
import subprocess

# What a clause DOES. Order matters: the specific tests run before the general.
CLAUSE_KINDS = (
    ("definition",  r"\bmeans\b|\bshall mean\b|\bis defined as\b|\brefers to\b"),
    ("prohibition", r"\b(shall not|may not|must not|is prohibited|no [a-z ,'-]{0,60}shall)\b"),
    ("condition",   r"\b(provided (that|however)|on condition that|subject to|unless|only if|"
                    r"in the event (that|of)|if .{0,40}then)\b"),
    ("deadline",    r"\b(within \d+ (calendar |business )?days?|no later than|by the \d+\w{0,2} day|"
                    r"at least \d+ days|prior to|on or before)\b"),
    ("money",       r"\$\s?\d|\b\d+(\.\d+)?\s?%|\b(fee|charge|deposit|rent|penalty|interest)\b"),
    ("power",       r"\b(may|is authori[sz]ed|shall have (the )?power|at (its|his|her|their) "
                    r"(sole |absolute )?(option|discretion)|reserves the right)\b"),
    ("obligation",  r"\b(shall|must|is required to|agrees to|is responsible for|"
                    r"covenants to|undertakes to|will be liable)\b"),
    ("precatory",   r"\b(wish(es)?|hope[sd]?|desire[sd]?|request(s|ed)?|recommend(s|ed)?|"
                    r"would like|suggest(s|ed)?)\b"),
    ("recital",     r"^\s*(whereas|recitals?\b|background\b)"),
)

# Which department has an interest, and why. A clause can match several.
DOMAIN_SIGNALS = {
    "legal_agent": (
        r"\b(notice|terminat\w+|default|cure|remedy|breach|waiver|jurisdiction|venue|"
        r"arbitrat\w+|indemnif\w+|liabilit\w+|governing law|attorney|enforce\w*|"
        r"eviction|possession|quiet enjoyment|accommodat\w+|discriminat\w+)\b"),
    "accounting_agent": (
        r"\b(rent|fee|deposit|payment|charge|late|interest|invoice|ledger|balance|"
        r"prorat\w+|escrow|tax|utilit\w+|reimburse\w*|refund)\b|\$\s?\d"),
    "trust_agent": (
        r"\b(trustee|beneficiar\w+|settlor|grantor|fiduciar\w+|corpus|res\b|"
        r"distribut\w+|remainder|spendthrift|in trust for|estate)\b"),
}

# A numbering scheme the document uses on its own. First match wins, so a
# document is segmented the way IT is organised rather than the way we guess.
NUMBERING = (
    (r"^\s*(ARTICLE\s+[IVXLC]+|Article\s+\d+)\b", "article"),
    (r"^\s*(§+\s*[\d.]+[A-Za-z]?)\b", "section_sign"),
    (r"^\s*(\d+\.\d+(?:\.\d+)*)\s", "decimal"),
    (r"^\s*(\d{1,3})\.\s+[A-Z]", "numbered"),
    (r"^\s*\(([a-z])\)\s", "lettered"),
    (r"^\s*([A-Z])\.\s+[A-Z]", "capital"),
)


def _tess(path, extra=()):
    try:
        out = subprocess.run(["tesseract", path, "stdout", *extra],
                             capture_output=True, text=True, timeout=240)
        return out.stdout or ""
    except Exception:
        return ""


def _legible(text):
    """Crude score: how much of this looks like English words rather than OCR
    noise. Used to pick an orientation, so it only has to rank, not judge."""
    words = re.findall(r"[A-Za-z]{3,}", text or "")
    if not words:
        return 0.0
    vowelly = sum(1 for w in words if re.search(r"[aeiouAEIOU]", w))
    return (vowelly / len(words)) * min(len(words) / 60.0, 1.0)


def _ocr_image(path):
    """OCR a photograph, correcting orientation first.

    A page photographed sideways OCRs to fluent-looking nonsense - the first
    lease page read as "4 e vee Me LUE Ne ON ee ee" and reported 37 clauses and
    zero obligations, which is worse than failing, because it looks like a
    result. Tesseract's own OSD pass reports the rotation; where OSD is not
    confident the four rotations are scored and the most word-like one wins.
    """
    meta = {"method": "tesseract", "pages": 1}
    osd = _tess(path, ("--psm", "0"))
    m = re.search(r"Rotate:\s*(\d+)", osd)
    conf = re.search(r"Orientation confidence:\s*([\d.]+)", osd)
    rotate = int(m.group(1)) if m else 0
    confidence = float(conf.group(1)) if conf else 0.0

    def rotated(deg):
        if not deg:
            return path
        try:
            from PIL import Image
            im = Image.open(path)
            out = os.path.join(os.path.dirname(path) or ".",
                               f".rot{deg}_" + os.path.basename(path))
            im.rotate(-deg, expand=True).save(out)
            return out
        except Exception:
            return path

    candidates = [rotate] if (rotate and confidence >= 1.0) else [0, 90, 180, 270]
    best, best_text, best_score = 0, "", -1.0
    tried = {}
    for deg in candidates:
        t = _tess(rotated(deg))
        sc = _legible(t)
        tried[deg] = round(sc, 3)
        if sc > best_score:
            best, best_text, best_score = deg, t, sc
    meta.update({"osd_rotate": rotate, "osd_confidence": confidence,
                 "applied_rotation": best, "legibility_by_rotation": tried,
                 "legibility": round(best_score, 3)})
    if best_score < 0.35:
        meta["warning"] = ("OCR output does not read as text at any rotation "
                           f"(best score {best_score:.2f}). Treat the clauses "
                           f"below as unreliable - a sharper photograph, or a "
                           f"PDF with a text layer, will fix this.")
    for f in os.listdir(os.path.dirname(path) or "."):
        if f.startswith(".rot"):
            try:
                os.remove(os.path.join(os.path.dirname(path) or ".", f))
            except OSError:
                pass
    return best_text, meta


def extract(path):
    """-> (text, meta). A page that yields nothing is counted and reported."""
    low = path.lower()
    if low.endswith((".txt", ".md")):
        return open(path, errors="replace").read(), {"method": "read", "pages": 1}
    if low.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp")):
        return _ocr_image(path)
    from pypdf import PdfReader
    reader = PdfReader(path)
    pages, empty = [], 0
    for page in reader.pages:
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        if not t.strip():
            empty += 1
        pages.append(t)
    meta = {"method": "pypdf", "pages": len(pages), "pages_without_text": empty}
    if empty:
        # Say it. A scanned lease silently read as empty is the worst possible
        # outcome: every later question answers "no such clause".
        meta["warning"] = (f"{empty} of {len(pages)} page(s) have no text layer. "
                           f"Those pages are NOT in the text below. Run OCR "
                           f"(tesseract) over the scan before relying on this.")
    return "\f".join(pages), meta


def segment(text):
    """-> [{ref, text}]. Keyed by the document's own numbering where it has one."""
    lines = [l.rstrip() for l in (text or "").splitlines()]
    scheme, hits = None, 0
    for rx, name in NUMBERING:
        n = sum(1 for l in lines if re.match(rx, l))
        if n > hits and n >= 3:
            scheme, hits = (rx, name), n
    clauses, cur = [], {"ref": None, "lines": []}
    if scheme:
        rx, name = scheme
        for l in lines:
            m = re.match(rx, l)
            if m:
                if cur["lines"]:
                    clauses.append(cur)
                cur = {"ref": m.group(1).strip(), "lines": [l]}
            else:
                cur["lines"].append(l)
        if cur["lines"]:
            clauses.append(cur)
    else:
        name = "paragraph"
        buf, n = [], 0
        for l in lines:
            if not l.strip():
                if buf:
                    n += 1
                    clauses.append({"ref": f"p{n}", "lines": buf})
                    buf = []
            else:
                buf.append(l)
        if buf:
            n += 1
            clauses.append({"ref": f"p{n}", "lines": buf})
    out = []
    for c in clauses:
        body = " ".join(x.strip() for x in c["lines"] if x.strip())
        body = re.sub(r"\s+", " ", body).strip()
        if len(body) < 25:          # headers and page furniture
            continue
        out.append({"ref": c["ref"] or f"c{len(out)+1}", "text": body,
                    "scheme": name})
    return out


def kinds_of(text):
    low = (text or "").lower()
    return [k for k, rx in CLAUSE_KINDS if re.search(rx, low, re.M)]


def domains_for(text):
    low = (text or "").lower()
    return sorted(d for d, rx in DOMAIN_SIGNALS.items() if re.search(rx, low))


DEADLINE_RE = re.compile(
    r"\bwithin\s+(\d+)\s+(calendar |business )?days?\b|"
    r"\bat least\s+(\d+)\s+days?\b|"
    r"\bno later than\s+([^.,;]{3,40})", re.I)
MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")


def analyse(path, max_clauses=400):
    """Everything the intake knows about one document."""
    text, meta = extract(path)
    if not (text or "").strip():
        return {"error": "no text could be extracted", "meta": meta,
                "advice": "If this is a photograph or a scan, OCR it first."}
    clauses = segment(text)[:max_clauses]
    interest = {}
    for c in clauses:
        c["kinds"] = kinds_of(c["text"])
        c["domains"] = domains_for(c["text"])
        c["deadlines"] = [m.group(0) for m in DEADLINE_RE.finditer(c["text"])][:4]
        c["amounts"] = MONEY_RE.findall(c["text"])[:6]
        for d in c["domains"]:
            interest.setdefault(d, []).append(c["ref"])
    obligations = [c for c in clauses if "obligation" in c["kinds"]
                   or "prohibition" in c["kinds"]]
    return {
        "source": os.path.basename(path),
        "meta": meta,
        "clauses": len(clauses),
        "obligations": len(obligations),
        "interested_domains": {d: len(v) for d, v in interest.items()},
        "clause_refs_by_domain": interest,
        "segmented_by": clauses[0]["scheme"] if clauses else None,
        "items": clauses,
    }
