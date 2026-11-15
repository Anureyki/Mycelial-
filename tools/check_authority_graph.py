#!/usr/bin/env python3
"""Build gate for Legal's authority_graph (2026-11-15) - the directed authority graph.

Runs on a SYNTHETIC opinion (the root text is stubbed; no network, no live-store write).
Proves the distinctions the principal required are preserved:
  - constitutional / statutory / property foundations are typed separately;
  - the DISSENT is removed from the majority and kept as counter-authority, even when it
    sits at the top of the assembled text (sections are not reliably majority-first);
  - cited vs relied-upon is decided by the proof passage, not by being cited;
  - a dissent's authorities are NOT mixed into the majority's;
  - extraction confidence and acquisition (`acquired`) are separate fields.
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


# Dissent placed FIRST on purpose - the assembly is not reliably majority-first.
OPINION = (
    "Mr. Justice Brandeis, dissenting. The regulation is a valid exercise of the police power. "
    "Compare Alpha Co. v. Beta Co., 3 U.S. 3. "
    "Mr. Justice Holmes delivered the opinion of the court. The question is one under the Fifth "
    "Amendment: property may not be taken without just compensation, and the Fourteenth Amendment "
    "due process is also implicated. The Kohler Act forbids the mining. The deed expressly reserving "
    "all coal and mining rights beneath the surface is valid. The matter is governed by Smith v. "
    "Jones, 1 U.S. 1, which controls. See Doe v. Roe, 2 U.S. 2.")

agent = object.__new__(la.LegalAgent)
agent.agent_id, agent.log = "legal_agent", (lambda *a, **k: None)
agent._shelved_opinion_text = lambda case, cite: (OPINION, None)

g = agent.authority_graph({"case": "Test Co. v. Example", "citation": "9 U.S. 9"})

check("constitutional foundations typed separately (Fifth + Fourteenth)",
      {c["provision"] for c in g["constitutional_foundation"]}
      >= {"Fifth Amendment Takings Clause", "Fourteenth Amendment Due Process"})
check("statutory source detected (Kohler Act)",
      any("Kohler Act" == s["statute"] for s in g["statutory_source"]))
check("property foundation detected (deed + reserved mining rights)",
      bool(g["property_foundation"]) and "deed" in g["property_foundation"][0]["basis"].lower())

sp = g["supporting_precedents"]
maj_cites = {p["citation"] for grp in sp.values() for p in grp}
check("the DISSENT is removed from the majority: the dissent's cite is NOT a majority precedent",
      "3 U.S. 3" not in maj_cites)
check("majority precedents are present and classified (Smith relied/cited, Doe cited)",
      "1 U.S. 1" in maj_cites and "2 U.S. 2" in maj_cites)
check("cited vs relied-upon: 'governed by ... controls' makes Smith relied_upon, not merely cited",
      any(p["citation"] == "1 U.S. 1" and p["relationship"] == "proven" for p in sp["relied_upon"]))
check("a bare 'See' cite is merely_cited, not asserted load-bearing",
      any(p["citation"] == "2 U.S. 2" for p in sp["merely_cited"]))

ca = g["counter_authorities"]
check("the dissent is kept as counter-authority, by its justice, with its OWN cites",
      ca.get("dissent_by") == "Brandeis"
      and any(c["citation"] == "3 U.S. 3" for c in ca.get("cites", [])))

check("every precedent carries BOTH extraction_confidence AND a separate `acquired` flag",
      all("extraction_confidence" in p and "acquired" in p
          for grp in sp.values() for p in grp))
check("the graph records the preserved distinctions and does not auto-acquire",
      "cited_vs_relied" in g["distinctions_preserved"]
      and "extraction_vs_acquisition" in g["distinctions_preserved"]
      and isinstance(g["acquisition_priority"], list))

print("check_authority_graph: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
