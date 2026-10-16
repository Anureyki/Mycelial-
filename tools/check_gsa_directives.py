#!/usr/bin/env python3
"""The GSA directives monitor reads dates from the source, says when it
could not look, and never maps a directive from its title.

    python3 tools/check_gsa_directives.py      (offline - synthetic page)
"""
import json
import os
import sys
from datetime import date

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
fails = []

PAGE = """<table><tr><th>Number</th><th>Title</th><th>Issue Date</th><th>Outdated Date</th></tr>
<tr><td><a href="/directives-library/91945">2160.1H CIO</a></td><td>IT Standards Policy</td>
<td>08/27/2025</td><td>08/31/2028</td></tr>
<tr><td><a href="/directives-library/91923">2030.2D ADM P</a></td><td>Internal Audit Follow-up HB</td>
<td>03/02/2010</td><td>05/31/2026</td></tr>
<tr><td><a href="/directives-library/91958">2320.1A ADM</a></td><td>Nondiscrimination Policy</td>
<td>08/21/2023</td><td>08/31/2030</td></tr></table>"""


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    import core.gsa_directives as G
    rows = G.parse_series(PAGE, "2000")
    ck("the table parses: number, title, issued, outdated, link",
       len(rows) == 3 and rows[0]["number"] == "2160.1H CIO" and rows[0]["outdated"] == "2028-08-31"
       and rows[0]["url"].endswith("/91945"), str(rows[0]))
    f = G.flags(rows, today=date(2026, 10, 16))
    ck("past its outdated date is flagged", [x["number"] for x in f["past_outdated_date"]] == ["2030.2D ADM P"])
    ck("outdated by the end of 2028 is flagged", [x["number"] for x in f["outdated_by_horizon"]] == ["2160.1H CIO"])
    ck("2030 is not", all(x["number"] != "2320.1A ADM" for x in f["outdated_by_horizon"]))

    def broken(s):
        if s == "3000":
            raise OSError("timed out")
        return PAGE if s == "2000" else "<table></table>", ""
    r = G.scan(fetch=broken)
    ck("a series that could not be read is an error, not an empty series",
       any(e["series"] == "3000" for e in r["errors"]) and "3000" not in r["by_series"])

    from agents.legal_agent.legal_agent import LegalAgent
    lg = LegalAgent.__new__(LegalAgent)
    lg.agent_id = "legal_agent"
    lg.log = lambda *a, **k: None
    store = {"gsa_directives": json.dumps({d["number"]: dict(d, listed=True) for d in rows})}
    lg.retrieve_own_memory = lambda k: store.get(k)
    lg.store_own_memory = lambda k, v, pin=False: store.__setitem__(k, v) or {"success": True}
    lg._unwrap_value = lambda v: v
    lg._gsa_cfg = lambda: {"mode": "dry_run", "horizon": "2028-12-31"}
    r = lg._gsa_directives_task("map_gsa_directive", {"number": "2160.1H CIO",
                                                        "constrains": [{"agent": "coding_agent", "behavior": "x"}]})
    ck("a mapping without the directive's own words is refused", "error" in r)
    r = lg._gsa_directives_task("map_gsa_directive", {
        "number": "2160.1H CIO", "constrains": [{"agent": "coding_agent", "behavior": "software standards"}],
        "basis": "quoted text from the directive"})
    ck("with a basis it maps", r.get("mapped") == "2160.1H CIO")
    v = lg._gsa_directives_task("gsa_directives", {})
    ck("unmapped directives are counted, never guessed", v["unmapped_count"] == 2 and "2160.1H CIO" in v["mapped"])
    if fails:
        print(f"\nFAIL: {len(fails)} check(s): {fails}")
        return 1
    print("\nOK - directive dates come from the source; mappings come from the text.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
