#!/usr/bin/env python3
"""Build gate for Legal's asset-protection + cited-authority-web capabilities (2026-11-15).

Pure functions / in-process method calls - no network, no shelving, no live-store write.

review_deed: red-flags a SURFACE-ONLY or severed-mineral deed (the Mahon trap) and does NOT
false-flag a 'subsurface rights' conveyance as surface-only. Default posture is caution.

_cited_pairs: a cited case is NOT load-bearing merely because the winner cited it. The verdict
must be PROVEN by a passage that shows reliance; 'Compare'/'Cf.', a negative cite, or a string
cite are not load-bearing; an affirmative cite with no reliance language is 'unproven' (review).
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


# ---- review_deed ----
agent = object.__new__(la.LegalAgent)
agent.agent_id, agent.log = "legal_agent", (lambda *a, **k: None)

surface = agent.review_deed({"text": "Grantor conveys the surface estate only, reserving unto "
                                     "grantor all coal, oil, gas and other minerals beneath.",
                             "intended_owner": "trust"})
check("surface-only / mineral-reservation deed -> HOLD, severed, red flags raised",
      surface["posture"] == "HOLD" and surface["mineral_rights"] == "severed_or_reserved"
      and len(surface["red_flags"]) >= 2
      and "260 U.S. 393" in surface["authority"])

good = agent.review_deed({"text": "Grantor conveys in fee simple all right, title and interest "
                                  "including all mineral and subsurface rights to the Family Trust.",
                          "intended_owner": "trust"})
check("fee-simple-with-minerals deed -> OK, conveyed, and 'subsurface rights' is NOT mis-flagged as surface-only",
      good["posture"] == "OK_IF_TITLED_TO_PRINCIPAL" and good["mineral_rights"] == "conveyed"
      and good["red_flags"] == [] and good["intended_owner_ok"] is True)

silent = agent.review_deed({"text": "Grantor conveys the property at 1 Main St to the buyer."})
check("a deed silent on minerals -> INVESTIGATE (not treated as full ownership)",
      silent["posture"] == "INVESTIGATE" and silent["mineral_rights"] == "unknown")

# ---- _cited_pairs: proof-based load-bearing ----
TEXT = ("The question is governed by Mugler v. Kansas, 123 U.S. 623, which controls here. "
        "See Welch v. Swasey, 214 U.S. 91. Compare Lindsley v. Natural Carbonic Gas Co., "
        "220 U.S. 61. The statute was overruled in Adkins v. Children's Hospital, 261 U.S. 525. "
        "See Smith v. Jones, 1 U.S. 1, Doe v. Roe, 2 U.S. 2.")
pairs = {p["citation"]: p for p in la._cited_pairs(TEXT, limit=12)}
check("a cite with reliance language ('governed by ... controls') -> support / proven, with its passage",
      pairs.get("123 U.S. 623", {}).get("load_bearing") == "proven"
      and "mugler" in pairs["123 U.S. 623"]["passage"].lower())
check("an affirmative cite with NO reliance shown -> support / unproven (review), not asserted load-bearing",
      pairs.get("214 U.S. 91", {}).get("load_bearing") == "unproven")
check("a 'Compare' cite -> distinguished, never load-bearing",
      pairs.get("220 U.S. 61", {}).get("role") == "compare_or_cf"
      and pairs["220 U.S. 61"]["load_bearing"] == "distinguished")
check("an 'overruled' cite -> negative, never load-bearing",
      pairs.get("261 U.S. 525", {}).get("load_bearing") == "negative")
check("a string cite (another citation immediately after) -> string_cite, not load-bearing",
      pairs.get("1 U.S. 1", {}).get("load_bearing") == "string_cite")
check("no case is load-bearing merely by being cited: exactly the one with a reliance passage is 'proven'",
      sum(1 for p in pairs.values() if p["load_bearing"] == "proven") == 1)

print("check_legal_asset: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
