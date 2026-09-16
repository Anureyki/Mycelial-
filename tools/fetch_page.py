#!/usr/bin/env python3
"""Render a page that will not answer a plain GET, and print it as JSON.

    python3 tools/fetch_page.py <url> [--wait-for-text STR] [--timeout MS]

WHY THIS IS A SUBPROCESS AND NOT AN IMPORT. crawl4ai brings playwright, an
OpenAI client and litellm with it. None of that belongs inside an agent
process: CLAUDE.md puts every verb that reaches the network behind a
boundary, and `_run_perception` already established the pattern - heavy,
optional machinery runs out of process and returns JSON on stdout, so a
missing dependency is a reported failure rather than an import error at
boot. It also keeps an LLM client out of the address space of agents that
have no business holding one.

WHAT IT IS FOR. `tools/ingest_law.py` has one hand-written fetcher per
source, and the sources that have none are the ones that defeat a GET.
statutes.capitol.texas.gov - the state's OWN publisher of its own statutes
- is an Angular application that answers 200 with "Retrieving statute
document..." and no statute, which is why Texas law is currently acquired
from a third-party mirror. Rendered, the same URL yields 134,919
characters of Chapter 3 including 3.311.

WHAT IT IS NOT FOR. It does not decide that anything it fetched is
authority. It returns text, the URL it came from, and the rendering it
performed; classification stays where it is - read the work, or record
`unknown`. And it submits nothing: there is no form fill, no click, no
POST. A page is read.
"""
import argparse
import asyncio
import json
import re
import sys

KIT = "page_render"

# Official publishers only. A renderer that will fetch anything is a
# general-purpose scraper, and a general-purpose scraper wired to an
# authority pipeline is how somebody's blog about Regulation Z ends up
# shelved beside Regulation Z. The allowlist is the same judgement
# core/authority_acquisition.py already makes about URLs, in one place.
ALLOWED = (
    r"^https://statutes\.capitol\.texas\.gov/",
    r"^https://www\.ecfr\.gov/",
    r"^https://www\.law\.cornell\.edu/",
    r"^https://www\.govinfo\.gov/",
    r"^https://texas\.public\.law/",
    r"^https://codes\.ohio\.gov/",
    r"^https://www\.courtlistener\.com/",
    r"^https://www\.consumerfinance\.gov/",
    r"^https://www\.ftc\.gov/",
    r"^https://www\.fcc\.gov/",
    r"^https://www\.irs\.gov/",
    r"^https://www\.va\.gov/",
    r"^https://www\.esd\.whs\.mil/",
)


def allowed(url):
    return any(re.match(p, url or "", re.I) for p in ALLOWED)


async def _render(url, wait_for_text=None, timeout_ms=90000, delay=2.0):
    from crawl4ai import (AsyncWebCrawler, BrowserConfig, CrawlerRunConfig,
                          CacheMode)
    cfg = {"cache_mode": CacheMode.BYPASS, "page_timeout": timeout_ms,
           "delay_before_return_html": delay}
    if wait_for_text:
        safe = json.dumps(str(wait_for_text))
        cfg["wait_for"] = f"js:() => document.body.innerText.includes({safe})"
    async with AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False)) as c:
        r = await c.arun(url, config=CrawlerRunConfig(**cfg))
        md = ""
        if r.success:
            md = (r.markdown.raw_markdown if hasattr(r.markdown, "raw_markdown")
                  else str(r.markdown or ""))
        return {
            "ok": bool(r.success), "url": url, "final_url": getattr(r, "url", url),
            "status": getattr(r, "status_code", None),
            "chars": len(md), "text": md,
            "error": None if r.success else str(getattr(r, "error_message", "") or "failed"),
        }


def fetch(url, wait_for_text=None, timeout_ms=90000):
    """-> dict. Never raises for a fetch failure; the failure is the result."""
    if not allowed(url):
        return {"ok": False, "url": url, "text": "", "chars": 0,
                "error": ("REFUSED: not an allowlisted official publisher. This renderer "
                          "feeds an authority pipeline, so it fetches from the publishers "
                          "that publish the law and nothing else."),
                "allowlist": list(ALLOWED)}
    try:
        return asyncio.run(_render(url, wait_for_text=wait_for_text, timeout_ms=timeout_ms))
    except ImportError as exc:
        return {"ok": False, "url": url, "text": "", "chars": 0,
                "kit_not_installed": KIT,
                "error": f"the {KIT} kit is not installed ({exc}). This is absence, not "
                         f"failure - the capability exists and the dependency tree is "
                         f"not on disk.",
                "install": f"python3 tools/kit.py install {KIT}"}
    except Exception as exc:                        # noqa: BLE001
        return {"ok": False, "url": url, "text": "", "chars": 0,
                "error": f"{type(exc).__name__}: {exc}"}


def kit_available():
    """Is this kit's dependency tree here? -> (bool, reason).

    ABSENT AND UNREACHABLE ARE DIFFERENT FINDINGS. A caller that gets
    `kit_not_installed` knows the capability exists and is not downloaded;
    a caller that gets an ImportError traceback knows nothing except that
    something broke. The install command travels with the answer."""
    try:
        import crawl4ai                                    # noqa: F401
    except Exception as exc:                                # noqa: BLE001
        return False, {"kit_not_installed": KIT, "why": f"{type(exc).__name__}: {exc}",
                       "install": f"python3 tools/kit.py install {KIT}",
                       "what_it_costs": "measured at install; see config/kits.json"}
    return True, None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", nargs="?")
    ap.add_argument("--self-test", action="store_true",
                    help="report whether the kit is installed and the allowlist loads, "
                         "without touching the network")
    ap.add_argument("--wait-for-text", default=None,
                    help="render until this string appears in the body - the honest way "
                         "to know a single-page app has finished, rather than guessing a "
                         "sleep")
    ap.add_argument("--timeout", type=int, default=90000)
    ap.add_argument("--quiet-text", action="store_true",
                    help="report the size and drop the body, for a reachability check")
    a = ap.parse_args()
    if a.self_test:
        ok, why = kit_available()
        json.dump({"kit": KIT, "installed": ok, "allowlist": len(ALLOWED),
                   "detail": why}, sys.stdout)
        sys.stdout.write("\n")
        return 0 if ok else 1
    if not a.url:
        ap.error("a url is required unless --self-test")
    out = fetch(a.url, wait_for_text=a.wait_for_text, timeout_ms=a.timeout)
    if a.quiet_text:
        out.pop("text", None)
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
