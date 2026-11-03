#!/usr/bin/env python3
"""Capability contracts, ASSEMBLED from what each agent already declares.

    from mycos.contracts import contract, all_contracts
    contract("grow_agent")

MYCOS-CORE-001 section 5 lists thirteen fields per agent. Most already exist,
spread across three declarations: config/agent_configs/<id>.json (identity,
port, roles, capabilities), config/agent_cards/<id>.json (the declared verbs
and the effects map read from the code by core/effects.py), and
config/resource_acl.json (what the agent owns and is granted). This reads them;
it keeps no copy. A field nothing declares comes back as "undeclared" - never
filled with a plausible default, because a contract that looks complete and is
not is the false-success shape this repo hunts.

No agent can grant itself authority through this: it is read-only, and the
grants come from the ACL, which no agent writes.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNDECLARED = "undeclared"


def _j(*parts):
    p = os.path.join(ROOT, *parts)
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def agent_ids():
    d = os.path.join(ROOT, "config", "agent_configs")
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))


def _grants(agent_id, acl):
    owns, granted = [], {"read": [], "write": [], "execute": []}
    for res, e in (acl or {}).items():
        if not isinstance(e, dict):
            continue
        if e.get("owner") == agent_id:
            owns.append(res)
            continue
        for act in granted:
            g = e.get(act)
            if isinstance(g, list) and (agent_id in g or "*" in g):
                granted[act].append(res)
    return owns, granted


def contract(agent_id, acl=None):
    cfg = _j("config", "agent_configs", f"{agent_id}.json")
    if cfg is None:
        return None
    card = _j("config", "agent_cards", f"{agent_id}.json") or {}
    if acl is None:
        acl = _j("config", "resource_acl.json") or {}
        acl = acl.get("resources", acl) if isinstance(acl, dict) else {}
    owns, granted = _grants(agent_id, acl)
    verbs = card.get("capabilities") or cfg.get("capabilities") or []
    effects = card.get("effects") or {}
    return {
        "identity": {"agent_id": agent_id, "port": cfg.get("port"),
                     "entry_point": cfg.get("entry_point") or UNDECLARED},
        "capabilities": verbs or UNDECLARED,
        "roles": cfg.get("roles") or UNDECLARED,
        "inputs_outputs": card.get("io") or UNDECLARED,
        "permitted_operations": effects or UNDECLARED,
        "data_access_boundaries": {"owns": owns, "granted": granted},
        "required_permissions": card.get("effects_gated_by") or UNDECLARED,
        "dependencies": cfg.get("dependencies") or UNDECLARED,
        "execution_environment": cfg.get("sandbox") or UNDECLARED,
        "validation_requirements": card.get("validation") or UNDECLARED,
        "failure_handling": card.get("failure_handling") or UNDECLARED,
        "escalation_conditions": card.get("escalation") or UNDECLARED,
        "audit_requirements": card.get("audit") or UNDECLARED,
        "version_provenance": {"version": cfg.get("version") or card.get("version") or UNDECLARED,
                               "effects_basis": card.get("effects_basis") or UNDECLARED},
        "assembled_from": ["config/agent_configs", "config/agent_cards", "config/resource_acl.json"],
    }


def all_contracts():
    return {a: contract(a) for a in agent_ids()}


def coverage():
    """Per field, how many agents declare it - the honest gap list."""
    out = {}
    for a, c in all_contracts().items():
        for k, v in (c or {}).items():
            if k in ("assembled_from",):
                continue
            declared = v != UNDECLARED and not (isinstance(v, dict) and any(
                x == UNDECLARED for x in v.values()))
            out.setdefault(k, [0, 0])
            out[k][0] += int(declared)
            out[k][1] += 1
    return {k: f"{d}/{n}" for k, (d, n) in out.items()}
