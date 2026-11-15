#!/usr/bin/env python3
"""Native per-domain knowledge graph (the lightweight substrate chosen 2026-11-15, over
OpenSPG's heavy stack). Each domain owns ONE graph with its OWN schema. The invariants the
principal required are enforced HERE, in code, not by convention:

  - SCHEMA: a node/edge type not in the domain's schema is refused.
  - OWNERSHIP: only the owning agent may change a domain's graph. No domain may overwrite
    another domain's knowledge - a cross-domain write is refused outright.
  - PROVENANCE: every node and edge carries {source, confidence, timestamp, asserted_by};
    a change without it is refused (a graph fact with no provenance is not admissible).
  - CONFLICT: a proposed change that contradicts an existing fact does NOT overwrite it.
    Both are preserved and a minimal referral payload is returned for the GOVERNING AGENT
    (Boss) to resolve - accept / reject / reconcile / escalate - via A2A. Nothing is applied
    on a conflict until a resolution authorised by the governing agent comes back.
  - CROSS-DOMAIN: Legal and Finance stay separate but may hold VERIFIED relationships between
    their graphs - only through a declared cross_domain_edge_type, recorded on the owner's
    side, never by writing into the other domain's graph.

This module stores and validates; it does not decide conflicts (that is the governing
agent's job) and it never calls a model. The human operator retains final authority.
"""
import json
import os
import time
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_DIR = os.path.join(BASE, "config", "knowledge_graphs")
STORE_DIR = os.path.join(BASE, "state", "knowledge_graphs")
REQUIRED_PROVENANCE = ("source", "confidence", "timestamp", "asserted_by")


def _refuse(reason, **extra):
    return {"status": "refused", "reason": reason, **extra}


class KnowledgeGraph:
    def __init__(self, domain, store_dir=None, schema_dir=None):
        self.domain = domain
        sd = schema_dir or SCHEMA_DIR
        with open(os.path.join(sd, f"{domain}.schema.json"), encoding="utf-8") as fh:
            self.schema = json.load(fh)
        self.owner = self.schema["owner_agent"]
        self._store_dir = store_dir or STORE_DIR
        self.path = os.path.join(self._store_dir, f"{domain}.json")

    # --- storage (owner-only on disk) ---
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {"domain": self.domain, "nodes": {}, "edges": [], "conflicts": [], "decisions": []}

    def _save(self, g):
        os.makedirs(self._store_dir, mode=0o700, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(g, fh, indent=2)
        os.chmod(self.path, 0o600)
        try:
            os.chmod(self._store_dir, 0o700)
        except OSError:
            pass

    # --- validation helpers ---
    def _bad_provenance(self, prov):
        if not isinstance(prov, dict):
            return "provenance must be an object with source, confidence, timestamp, asserted_by"
        missing = [k for k in REQUIRED_PROVENANCE if prov.get(k) in (None, "")]
        return f"provenance missing {missing}" if missing else None

    def _owns(self, by_agent):
        return by_agent == self.owner

    # --- node proposal ---
    def propose_node(self, by_agent, node_id, node_type, props, provenance):
        if not self._owns(by_agent):
            return _refuse(f"{by_agent} may not write the {self.domain} graph - domain ownership. "
                           f"No domain overwrites another's knowledge.", owner=self.owner)
        if node_type not in self.schema["node_types"]:
            return _refuse(f"node type {node_type!r} is not in the {self.domain} schema",
                           allowed=self.schema["node_types"])
        bad = self._bad_provenance(provenance)
        if bad:
            return _refuse(bad)
        g = self._load()
        existing = g["nodes"].get(node_id)
        new = {"id": node_id, "type": node_type, "props": props or {},
               "provenance": provenance, "recorded_at": datetime.now().isoformat()}
        if existing and (existing.get("props") != new["props"] or existing.get("type") != node_type):
            return self._conflict(g, "node", existing, new)
        g["nodes"][node_id] = new
        self._save(g)
        return {"status": "accepted", "kind": "node", "id": node_id}

    # --- edge proposal (incl. cross-domain verified relationships) ---
    def propose_edge(self, by_agent, src, rel, dst, provenance, dst_domain=None):
        if not self._owns(by_agent):
            return _refuse(f"{by_agent} may not write the {self.domain} graph - domain ownership.",
                           owner=self.owner)
        cross = bool(dst_domain) and dst_domain != self.domain
        allowed = self.schema["cross_domain_edge_types"] if cross else self.schema["edge_types"]
        if rel not in allowed:
            return _refuse(f"edge type {rel!r} not allowed "
                           f"({'cross-domain' if cross else 'intra-domain'}) in the {self.domain} schema",
                           allowed=allowed)
        bad = self._bad_provenance(provenance)
        if bad:
            return _refuse(bad)
        g = self._load()
        edge = {"src": src, "rel": rel, "dst": dst, "dst_domain": dst_domain or self.domain,
                "cross_domain": cross, "verified_relationship": cross,
                "provenance": provenance, "recorded_at": datetime.now().isoformat()}
        # a contradicting edge (same src+rel+dst, different provenance value) is a conflict
        for e in g["edges"]:
            if e["src"] == src and e["rel"] == rel and e["dst"] == dst and e["dst_domain"] == edge["dst_domain"]:
                if e["provenance"] != provenance:
                    return self._conflict(g, "edge", e, edge)
                return {"status": "accepted", "kind": "edge", "already": True}
        g["edges"].append(edge)
        self._save(g)
        return {"status": "accepted", "kind": "edge",
                "cross_domain": cross, "verified_relationship": cross}

    # --- conflict: preserve both, return a referral for the governing agent ---
    def _conflict(self, g, kind, existing, proposed):
        conflict_id = f"conflict_{int(time.time()*1000)}"
        record = {"conflict_id": conflict_id, "kind": kind, "domain": self.domain,
                  "existing": existing, "proposed": proposed, "status": "open",
                  "opened_at": datetime.now().isoformat()}
        g["conflicts"].append(record)
        self._save(g)   # the EXISTING fact is kept; the proposed change is NOT applied
        return {"status": "conflict", "conflict_id": conflict_id,
                "referral": {"to": "boss_agent", "domain": self.domain, "kind": kind,
                             "existing": {"value": existing.get("props") or {k: existing.get(k) for k in ("src", "rel", "dst")},
                                          "provenance": existing.get("provenance")},
                             "proposed": {"value": proposed.get("props") or {k: proposed.get(k) for k in ("src", "rel", "dst")},
                                          "provenance": proposed.get("provenance")},
                             "note": "Both records preserved. Governing agent to resolve; no overwrite applied."},
                "applied": False}

    # --- apply a resolution AUTHORISED by the governing agent ---
    def apply_resolution(self, conflict_id, decision, authorised_by, rationale, evidence=None):
        if authorised_by != "boss_agent":
            return _refuse("only the governing agent (boss_agent) may resolve a conflict")
        if decision not in ("accept", "reject", "reconcile", "escalate"):
            return _refuse("decision must be accept|reject|reconcile|escalate")
        g = self._load()
        conflict = next((c for c in g["conflicts"] if c["conflict_id"] == conflict_id), None)
        if not conflict:
            return _refuse(f"no open conflict {conflict_id}")
        if decision == "accept":                      # the proposed change wins
            p = conflict["proposed"]
            if conflict["kind"] == "node":
                g["nodes"][p["id"]] = p
            else:
                g["edges"] = [e for e in g["edges"]
                              if not (e["src"] == p["src"] and e["rel"] == p["rel"] and e["dst"] == p["dst"])]
                g["edges"].append(p)
        # reject keeps existing; reconcile/escalate leave both pending the recorded decision
        conflict["status"] = "resolved" if decision in ("accept", "reject", "reconcile") else "escalated_to_human"
        g["decisions"].append({"conflict_id": conflict_id, "decision": decision,
                               "authorised_by": authorised_by, "rationale": rationale,
                               "evidence": evidence, "at": datetime.now().isoformat()})
        self._save(g)
        return {"status": "resolved", "decision": decision, "conflict_id": conflict_id,
                "escalated_to_human": decision == "escalate"}

    # --- read-only views (for Anansi visualisation) ---
    def view(self):
        g = self._load()
        return {"domain": self.domain, "owner": self.owner,
                "nodes": list(g["nodes"].values()), "edges": g["edges"],
                "open_conflicts": [c for c in g["conflicts"] if c["status"] == "open"],
                "decisions": g["decisions"]}
