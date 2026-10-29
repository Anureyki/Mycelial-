#!/usr/bin/env python3
"""Rows of the first worksheet of an .xlsx, with the standard library only.

    from core.xlsx_rows import rows, to_csv

2026-10-29: a Mars Hydro app update changed the THP export from CSV to an Excel
workbook. Grow's tent import reads CSV, and one file format did not justify a
new dependency, so this reads the workbook the way Excel stores it - a zip of
XML parts - and nothing more: shared strings, inline strings, numbers, and the
first sheet's cell grid. Formulas are read as their cached values. A workbook
that cannot be read raises; it is never returned as an empty table.
"""
import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
MAX_UNZIPPED = 200 * 1024 * 1024  # a zip that inflates past this is refused, not read


def _col(ref):
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def rows(blob):
    """-> list of rows (lists of strings), first worksheet, in sheet order."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
            raise ValueError("workbook inflates past the size limit - refused")
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        sheets = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        if not sheets:
            raise ValueError("no worksheet in the workbook")
        root = ET.fromstring(z.read(sheets[0]))
    out = []
    for r in root.iter(f"{{{NS['m']}}}row"):
        cells = {}
        for c in r.findall("m:c", NS):
            t, ref = c.get("t"), c.get("r") or ""
            v = c.find("m:v", NS)
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
            else:
                val = v.text if v is not None else ""
            cells[_col(ref) if ref else len(cells)] = val or ""
        if cells:
            out.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return out


def to_csv(blob):
    """-> the first worksheet as CSV text."""
    buf = io.StringIO()
    csv.writer(buf).writerows(rows(blob))
    return buf.getvalue()
