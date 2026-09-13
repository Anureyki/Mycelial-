#!/usr/bin/env python3
"""The edges. An asset is HELD AT a party, GOVERNED BY an instrument.

    from core.registry_graph import edge, edges_for, orphans
    edge("chase_checking", "held_at", "bank_a",
         evidence="cited", citation="statement 2026-07 p.1")

PHASE 1A.4. Three registries with no relationships are three lists. The edges
are where the ontology actually lives: this account, at this bank, serviced by
a different company, under this contract, owed to this creditor.

AN EDGE EXISTS BECAUSE A DOCUMENT SAYS SO. The same rule
core/ownership_graph.py already runs on for corporate structure, and for the
same reason: a hallucinated relationship in a financial graph is not a bad
answer, it is a false statement about who may demand money. There is no
inference here. An edge between two records that obviously belong together is
refused exactly like any other until something establishes it.

BOTH ENDS MUST EXIST FIRST. An edge to an id nobody recorded would create the
node by implication - which is how a registry acquires parties nobody ever
verified, named after whatever string was typed into a relationship.

THE ROLE IS ON THE EDGE, NOT ON THE PARTY. One bank is routinely the holder of
one account and the servicer of another, and a servicer is routinely a
different company from the one whose name is on the card. Putting the role on
the party forces a choice that the facts do not support; putting it on the
edge lets one party hold different roles for different assets, which is what
actually happens.

DIRECTION IS PART OF THE FACT. `owes` and `owed_by` are not the same edge read
from two sides - getting it backwards inverts who may sue whom. Every edge
kind declares which way it points and what the two ends mean.
"""
import json
import os
from datetime import datetime, timezone

from core.asset_registry import EVIDENCE, ESTABLISHED, Refused  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "private", "registry_edges.json")

# (from_registry, to_registry, what it means). Direction is declared, never
# inferred from the name.
EDGE_KINDS = {
    "held_at":      ("asset", "counterparty",
                     "the asset sits with this party"),
    "serviced_by":  ("asset", "counterparty",
                     "this party administers it day to day"),
    "owed_to":      ("asset", "counterparty",
                     "this party may demand payment. NOT the same as held_at"),
    "owed_by":      ("asset", "counterparty",
                     "this party carries the obligation"),
    "governed_by":  ("asset", "contract",
                     "this instrument decides what may be done with it"),
    "beneficiary_of": ("counterparty", "asset",
                       "this party receives what the asset delivers"),
    "party_to":     ("counterparty", "contract",
                     "this party is bound by the instrument"),
    "assigned_to":  ("contract", "counterparty",
                     "the interest under this contract moved to this party"),
    "secured_by":   ("asset", "asset",
                     "collateral. The second asset secures the first"),
    "regulated_by": ("counterparty", "counterparty",
                     "the second party supervises the first"),
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def load(path=None):
    p = path or STORE
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"schema_version": "1.0", "edges": []}
    except Exception as exc:
        raise Refused(f"{p} exists and is unreadable: {exc}") from exc


def _save(doc, path=None):
    p = path or STORE
    # BORN 0700, not fixed later. os.makedirs applies the process umask, so
    # the same line produced 0775 under this service manager - and 1,507
    # files sat group- and world-readable until somebody audited. A private
    # store that depends on the umask it happened to inherit is not private.
    from core.fs_boundary import ensure_dir
    ensure_dir(os.path.dirname(p), 0o700)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
    os.chmod(p, 0o600)


def _exists(registry, node_id, stores):
    """-> whether a node is actually recorded. No node is created here."""
    if registry == "asset":
        from core.asset_registry import get as g
        return g(node_id, stores.get("assets")) is not None
    if registry == "counterparty":
        from core.counterparty_registry import get as g
        return g(node_id, stores.get("counterparties")) is not None
    if registry == "contract":
        from core.contract_registry import get as g
        return g(node_id, stores.get("contracts")) is not None
    return False


def edge(src, kind, dst, evidence="unknown", citation=None, note="",
         path=None, stores=None):
    """Record one relationship. Refuses before it writes."""
    stores = stores or {}
    if kind not in EDGE_KINDS:
        raise Refused(f"{kind!r} is not an edge kind: {sorted(EDGE_KINDS)}")
    if evidence not in EVIDENCE:
        raise Refused(f"{evidence!r} is not an evidence state")
    if evidence not in ESTABLISHED and evidence != "stated_by_principal":
        raise Refused(
            f"an edge on {evidence!r} is refused. A relationship is a claim "
            f"about who may demand money from whom; `derived` and `unknown` "
            f"are how a graph fills with plausible connections nobody can "
            f"trace back to a document.")
    if not citation:
        raise Refused(
            "an edge needs a citation - the document, filing or statement "
            "that establishes it. An edge that exists because it was obvious "
            "is a false statement about who may demand money.")

    src_reg, dst_reg, _ = EDGE_KINDS[kind]
    for reg, node in ((src_reg, src), (dst_reg, dst)):
        if not _exists(reg, node, stores):
            raise Refused(
                f"{node!r} is not recorded in the {reg} registry. An edge "
                f"will not create it: a node brought into existence by a "
                f"relationship is a party nobody verified, named after "
                f"whatever string was typed.")

    doc = load(path)
    rec = {"from": src, "kind": kind, "to": dst, "from_registry": src_reg,
           "to_registry": dst_reg, "evidence": evidence,
           "citation": citation, "note": note, "at": _now()}
    if any(e["from"] == src and e["kind"] == kind and e["to"] == dst
           for e in doc["edges"]):
        return {"already_recorded": True, "edge": rec}
    doc["edges"].append(rec)
    _save(doc, path)
    return {"already_recorded": False, "edge": rec}


def edges_for(node_id, path=None):
    """-> everything touching this node, both directions, direction kept."""
    doc = load(path)
    out = {"outbound": [], "inbound": []}
    for e in doc["edges"]:
        if e["from"] == node_id:
            out["outbound"].append(e)
        if e["to"] == node_id:
            out["inbound"].append(e)
    return out


def governing_contract(asset_id, path=None):
    """-> the instrument for an asset, or an explicit absence.

    `None` would be indistinguishable from "no contract governs this", and
    those are different: one is unrecorded, the other is a finding."""
    g = [e for e in edges_for(asset_id, path)["outbound"]
         if e["kind"] == "governed_by"]
    if not g:
        return {"contract_id": None, "absence_state": "not_checked",
                "why": ("No governing instrument is recorded. That is not a "
                        "finding that none exists - nobody has established "
                        "one either way.")}
    if len(g) > 1:
        return {"contract_id": None, "absence_state": "conflicting",
                "candidates": [e["to"] for e in g],
                "why": ("More than one instrument is recorded as governing "
                        "this asset. Both are kept; which controls is a "
                        "document question, not one this module decides.")}
    return {"contract_id": g[0]["to"], "absence_state": "verified_clear",
            "citation": g[0]["citation"], "evidence": g[0]["evidence"]}


def orphans(path=None, stores=None):
    """-> recorded things nothing connects to. The registry's real worklist."""
    stores = stores or {}
    from core.asset_registry import load as la
    from core.counterparty_registry import load as lc
    doc = load(path)
    touched = {e["from"] for e in doc["edges"]} | {e["to"] for e in doc["edges"]}
    out = []
    for aid in (la(stores.get("assets")).get("assets") or {}):
        if aid not in touched:
            out.append({"node": aid, "registry": "asset",
                        "why": "no counterparty and no governing instrument"})
    for pid in (lc(stores.get("counterparties")).get("counterparties") or {}):
        if pid not in touched:
            out.append({"node": pid, "registry": "counterparty",
                        "why": "recorded, and connected to nothing"})
    return out
