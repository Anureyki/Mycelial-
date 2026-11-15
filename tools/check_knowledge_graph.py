#!/usr/bin/env python3
"""Build gate for the native per-domain knowledge-graph substrate (2026-11-15).

Proves the invariants the principal required, in an isolated temp store (no live-store write):
schema validation, required provenance, domain ownership (no cross-domain overwrite), conflict
-> preserve both + refer to the governing agent (not applied), cross-domain verified edges only
through a declared type, and resolution only by the governing agent with an audit record.
"""
import os
import sys
import tempfile

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core.knowledge_graph import KnowledgeGraph  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


TMP = tempfile.mkdtemp(prefix="kg_")
PROV = {"source": "USN-9065-1", "confidence": 0.9, "timestamp": "2026-11-15T00:00:00", "asserted_by": "legal_agent"}
legal = KnowledgeGraph("legal", store_dir=TMP)
fin = KnowledgeGraph("financial", store_dir=TMP)

# schema validation
check("a node type not in the domain schema is refused",
      legal.propose_node("legal_agent", "n1", "spaceship", {}, PROV)["status"] == "refused")
# provenance required
check("a node without full provenance is refused",
      legal.propose_node("legal_agent", "c1", "case", {"cite": "260 U.S. 393"},
                         {"source": "x"})["status"] == "refused")
# ownership: no cross-domain overwrite
check("a non-owner may not write the domain graph (no cross-domain overwrite)",
      legal.propose_node("accounting_agent", "c1", "case", {"cite": "260 U.S. 393"}, PROV)["status"] == "refused")
# owner write accepted
check("the owning agent's valid node is accepted",
      legal.propose_node("legal_agent", "c1", "case", {"cite": "260 U.S. 393", "holding": "taking"}, PROV)["status"] == "accepted")

# conflict: contradicting value -> preserve both, refer, NOT applied
P2 = dict(PROV, asserted_by="legal_agent", source="other")
r = legal.propose_node("legal_agent", "c1", "case", {"cite": "260 U.S. 393", "holding": "no taking"}, P2)
check("a contradicting change is a CONFLICT: both preserved, referred to the governing agent, not applied",
      r["status"] == "conflict" and r["applied"] is False and r["referral"]["to"] == "boss_agent"
      and legal.view()["nodes"][0]["props"]["holding"] == "taking")  # existing kept

# cross-domain verified relationship only through a declared type
check("a cross-domain edge is allowed only via a cross_domain_edge_type, marked verified",
      legal.propose_edge("legal_agent", "c1", "evidences", "txn_9", PROV, dst_domain="financial")["verified_relationship"] is True)
check("an intra-domain edge type is refused for a cross-domain edge",
      legal.propose_edge("legal_agent", "c1", "relied_upon", "txn_9", PROV, dst_domain="financial")["status"] == "refused")

# resolution only by the governing agent, with an audit record
check("only the governing agent (boss_agent) may resolve a conflict",
      legal.apply_resolution(r["conflict_id"], "accept", "legal_agent", "self-serving")["status"] == "refused")
res = legal.apply_resolution(r["conflict_id"], "accept", "boss_agent", "newer source verified")
check("the governing agent's 'accept' applies the proposed change and records the decision",
      res["status"] == "resolved"
      and legal.view()["nodes"][0]["props"]["holding"] == "no taking"
      and legal.view()["decisions"][-1]["authorised_by"] == "boss_agent")
esc = fin.propose_node("accounting_agent", "t1", "transaction", {"amt": 550}, dict(PROV, asserted_by="accounting_agent"))
fin.propose_node("accounting_agent", "t1", "transaction", {"amt": 999}, dict(PROV, asserted_by="accounting_agent", source="z"))
openc = fin.view()["open_conflicts"]
er = fin.apply_resolution(openc[0]["conflict_id"], "escalate", "boss_agent", "insufficient evidence")
check("the governing agent can ESCALATE a conflict to human review (never invents a resolution)",
      er["escalated_to_human"] is True)

print("check_knowledge_graph: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
