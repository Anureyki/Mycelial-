#!/usr/bin/env python3
"""Broader validation suite for Legal's authority_graph (2026-11-15) across opinion shapes:
multiple dissents, a concurrence, an express overrule, several distinguished precedents,
extensive statutory interpretation, and a complicated procedural history.

Synthetic opinions, root text stubbed - no network, no live-store write.
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


def graph_of(text):
    a = object.__new__(la.LegalAgent)
    a.agent_id, a.log = "legal_agent", (lambda *x, **k: None)
    a._shelved_opinion_text = lambda case, cite: (text, None)
    return a.authority_graph({"case": "Test Co. v. Example", "citation": "9 U.S. 9"})


# 1. MULTIPLE DISSENTS
g = graph_of(
    "Mr. Justice Holmes delivered the opinion of the court. The statute is void. "
    "Governed by Alpha v. Beta, 1 U.S. 1, which controls. "
    "Mr. Justice Brandeis, dissenting. I would uphold it. See Gamma v. Delta, 2 U.S. 2. "
    "Mr. Justice Day, dissenting. I also dissent. See Epsilon v. Zeta, 3 U.S. 3.")
ds = g["counter_authorities"].get("dissents", [])
check("multiple dissents: each is kept separately with its own justice",
      {d["justice"] for d in ds} >= {"Brandeis", "Day"} and len(ds) >= 2)
maj_cites = {p["citation"] for grp in g["supporting_precedents"].values() for p in grp}
check("multiple dissents: neither dissent's cites leak into the majority precedents",
      "2 U.S. 2" not in maj_cites and "3 U.S. 3" not in maj_cites and "1 U.S. 1" in maj_cites)

# 2. CONCURRENCE
g = graph_of(
    "Mr. Justice Holmes delivered the opinion of the court. The judgment is affirmed. "
    "Governed by Alpha v. Beta, 1 U.S. 1. "
    "Mr. Justice Stone, concurring. I agree in the result only. See Theta v. Iota, 4 U.S. 4.")
conc = g["separate_concurrences"]
maj_cites = {p["citation"] for grp in g["supporting_precedents"].values() for p in grp}
check("a concurrence is held separately (not the majority), with its own cites",
      len(conc) >= 1 and conc[0]["justice"] == "Stone"
      and "4 U.S. 4" not in maj_cites)

# 3. EXPRESS OVERRULE
g = graph_of(
    "Mr. Justice Holmes delivered the opinion of the court. We overrule Lochner v. New York, "
    "1 U.S. 1, and hold the statute valid.")
adverse = {p["citation"]: p for p in g["supporting_precedents"]["distinguished_or_adverse"]}
check("an expressly overruled precedent is classified adverse/negative, never load-bearing support",
      adverse.get("1 U.S. 1", {}).get("relationship") == "negative")

# 4. DISTINGUISHES SEVERAL PRECEDENTS
g = graph_of(
    "Mr. Justice Holmes delivered the opinion of the court. We distinguish Alpha v. Beta, "
    "1 U.S. 1. Compare Gamma v. Delta, 2 U.S. 2. But see Epsilon v. Zeta, 3 U.S. 3.")
check("several distinguished/adverse precedents are all captured as adverse",
      len(g["supporting_precedents"]["distinguished_or_adverse"]) >= 2)

# 5. EXTENSIVE STATUTORY INTERPRETATION
g = graph_of(
    "Mr. Justice Holmes delivered the opinion of the court. The Securities Act governs, and the "
    "Securities Exchange Act supplements it. We interpret the Sherman Act as well.")
stat = {s["statute"] for s in g["statutory_source"]}
check("extensive statutory interpretation: multiple named statutes captured",
      len(stat) >= 2)

# 6. COMPLICATED PROCEDURAL HISTORY
g = graph_of(
    "Mr. Justice Holmes delivered the opinion of the court. This case comes on writ of certiorari. "
    "The trial court sustained the demurrer; the court below reversed and remanded; we affirm in part.")
check("a complicated procedural history is captured (arrival and proceedings below)",
      len(g["procedural_history"]) >= 1)

print("check_authority_graph_suite: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
