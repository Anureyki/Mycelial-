#!/usr/bin/env python3
"""Build gate for Legal's citation provenance on acquire_opinion (2026-11-15).

A named case can exist at several court levels - the state decision and the federal one
that REVERSED it share parties and facts - so name + subject verification cannot tell them
apart. The citation is the discriminator. This proves Legal:
  - parses a citation out of a request and infers the court from its reporter;
  - REFUSES to shelve an opinion whose citation does not match the one requested
    (verdict citation_mismatch), instead of silently shelving a reversed lower-court case;
  - lets a MATCHING citation through.
No network, no shelving, no live-store write.
"""
import os
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import agents.legal_agent.legal_agent as la  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


# ---- parsers ----
check("extracts a citation from a request string",
      la._extract_citation("Pennsylvania Coal Co. v. Mahon, 260 U.S. 393 (1922)") == "260 U.S. 393")
check("infers the U.S. Supreme Court from a U.S. reporter",
      la._court_from_citation("260 U.S. 393") == "scotus"
      and la._court_from_citation("438 U.S. 104") == "scotus"
      and la._court_from_citation("480 U.S. 470") == "scotus")
check("infers a state court from a state reporter (Pa. -> pa)",
      la._court_from_citation("274 Pa. 489") == "pa")
check("citations compare after normalising punctuation/case",
      la._norm_cite("260 U.S. 393") == "260us393"
      and "260us393" in la._citation_norms(["260 U.S. 393", "43 S. Ct. 158"]))

# ---- citation_mismatch refusal, through the real method (no network, no shelve) ----
agent = object.__new__(la.LegalAgent)
agent.agent_id = "legal_agent"
agent.log = lambda *a, **k: None

STATE_ONLY = {"results": [{"cluster_id": 999, "caseName": "Mahon v. Pennsylvania Coal Co.",
                           "citation": ["274 Pa. 489"], "court": "Supreme Court of Pennsylvania"}]}


def stub(found, opinion_readable=False):
    def _call(caller, tool, args=None, timeout=120):
        if tool == "search":
            return found
        if tool == "opinion_text":
            return {"readable": opinion_readable, "text": "irrelevant body" if opinion_readable else ""}
        return {"error": "unexpected"}
    return _call


la._cl.call_unwrapped = stub(STATE_ONLY)
r = agent._acquire_published_opinion("Pennsylvania Coal Co. v. Mahon", "", {"citation": "260 U.S. 393"})
check("asking for 260 U.S. 393 when only the Pa. opinion is found -> citation_mismatch, nothing shelved",
      isinstance(r, dict) and r.get("verdict") == "citation_mismatch"
      and r.get("acquired") is False and r.get("requested_citation") == "260 U.S. 393")

# a MATCHING citation passes the filter (then stops on unreadable text - still no shelve)
MATCH = {"results": [{"cluster_id": 1000, "caseName": "Pennsylvania Coal Co. v. Mahon",
                      "citation": ["260 U.S. 393"], "court": "Supreme Court of the United States"}]}
la._cl.call_unwrapped = stub(MATCH, opinion_readable=False)
r2 = agent._acquire_published_opinion("Pennsylvania Coal Co. v. Mahon", "", {"citation": "260 U.S. 393"})
check("a matching citation passes the citation filter (not refused as a mismatch)",
      not (isinstance(r2, dict) and r2.get("verdict") == "citation_mismatch"))

print("check_legal_citation: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
