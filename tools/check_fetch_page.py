#!/usr/bin/env python3
"""The renderer fetches official publishers only, and decides no authority.

    python3 tools/check_fetch_page.py

No network. The allowlist and the refusal are checked directly; whether a
given site renders is a fact about that site and is not asserted here.
"""
import os
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    from tools import fetch_page as F

    print("official publishers only")
    for url in ("https://statutes.capitol.texas.gov/Docs/BC/htm/BC.3.htm",
                "https://www.ecfr.gov/current/title-12/part-1006",
                "https://www.law.cornell.edu/uscode/text/15/1681s-2",
                "https://www.govinfo.gov/content/pkg/PLAW-115publ59/html/PLAW-115publ59.htm"):
        ck(f"allowed: {url.split('/')[2]}", F.allowed(url))
    for url in ("https://someblog.example.com/reg-z-explained",
                "https://medium.com/@someone/sovereign-citizen-secrets",
                "http://statutes.capitol.texas.gov/Docs/BC/htm/BC.3.htm",
                "https://statutes.capitol.texas.gov.evil.example.com/x"):
        ck(f"refused: {url[:52]}", not F.allowed(url))

    print("a refusal is a result, not an exception")
    out = F.fetch("https://someblog.example.com/x")
    ck("refused fetch returns ok=False with the reason and the allowlist",
       out["ok"] is False and "REFUSED" in out["error"] and out.get("allowlist"))
    ck("and no text", out["text"] == "" and out["chars"] == 0)

    print("it renders, it does not classify or submit")
    src = open(os.path.join(ROOT, "tools", "fetch_page.py"), encoding="utf-8").read()
    for banned in ("authority_class", "claim_layer", "shelve("):
        ck(f"does not decide {banned!r}", banned not in src)
    for banned in ("net.post", "form_fill", ".click(", "submit("):
        ck(f"does not {banned!r}", banned not in src)
    ck("returns the url it actually landed on, so a redirect travels",
       "final_url" in src)

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails[:6]))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
