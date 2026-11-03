#!/usr/bin/env python3
"""Capability contracts, ASSEMBLED from what each agent already declares.

    from mycos.contracts import contract, all_contracts, coverage
    contract("grow_agent")

MYCOS-CORE-001 section 5 lists the fields every agent's contract carries.
Nothing here is policy of its own. Each field is read from a declaration that
already exists, and says which:

  config/agent_configs/<id>.json   identity, entry point, roles, capabilities
  config/agent_cards/<id>.json     version, transport, endpoint, MQTT topics,
                                   effects map (core/effects.py), effects_gated_by
  config/resource_acl.json         what the agent owns and is granted
  config/guards.json               deny rules that name the agent or match all
  config/departments.json          department membership
  services/service_manager         CORE_AGENTS, MAX_RESTARTS, RESTART_WINDOW
  agents/boss_agent                ROUTE_LOG (routes recorded as policy)

The two code sources are read as module CONSTANTS through the AST - the
declared scope of a supervisor and of Boss's policy - never by importing or
running them.

Where nothing declares a part of a field, that part is the string
REQUIRED_BUT_MISSING beside a `why`, and the field's status says so. A gap is
never filled with a plausible default: a contract that looks complete and is
not is the false-success shape this repo hunts, and the gate prints every gap
on every run.
"""
import ast
import fnmatch
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MISSING = "required_but_missing"
UNDECLARED = MISSING            # the name earlier callers used
SIGNOFF_CLASSES = ("actuates", "external")


def _j(*parts):
    try:
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _constants(relpath, names):
    """Module-level constants from a source file, by AST. Unreadable -> {}."""
    try:
        tree = ast.parse(open(os.path.join(ROOT, relpath), encoding="utf-8").read())
    except (OSError, SyntaxError):
        return {}
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in names:
                    try:
                        out[t.id] = ast.literal_eval(node.value)
                    except ValueError:
                        pass
    return out


def _sources():
    acl = _j("config", "resource_acl.json") or {}
    sm = _constants("services/service_manager/service.py", {"CORE_AGENTS", "MAX_RESTARTS", "RESTART_WINDOW"})
    boss = _constants("agents/boss_agent/boss_agent.py", {"ROUTE_LOG", "ROUTE_DENY"})
    return {"acl": acl.get("resources", acl) if isinstance(acl, dict) else {},
            "guards": (_j("config", "guards.json") or {}).get("deny") or [],
            "departments": _j("config", "departments.json") or {},
            "sm": sm, "boss": boss}


def agent_ids():
    d = os.path.join(ROOT, "config", "agent_configs")
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))


def _gap(why):
    return {"value": MISSING, "why": why}


def _status(parts):
    vals = [p for p in parts.values() if isinstance(p, dict) and "value" in p] + \
        [p for p in parts.values() if p == MISSING]
    n_missing = sum(1 for p in parts.values()
                    if p == MISSING or (isinstance(p, dict) and p.get("value") == MISSING))
    if n_missing == 0:
        return "declared"
    return "required_but_missing" if n_missing == len(parts) else "partial"


def _field(parts, sources):
    return {"status": _status(parts), "parts": parts, "sources": sources}


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


def contract(agent_id, acl=None, src=None):
    cfg = _j("config", "agent_configs", f"{agent_id}.json")
    if cfg is None:
        return None
    src = src or _sources()
    if acl is not None:
        src = dict(src, acl=acl)
    card = _j("config", "agent_cards", f"{agent_id}.json") or {}
    owns, granted = _grants(agent_id, src["acl"])
    verbs = card.get("capabilities") or cfg.get("capabilities") or []
    effects = card.get("effects") if isinstance(card.get("effects"), dict) else None
    gated = card.get("effects_gated_by") if "effects_gated_by" in card else None
    core = set(src["sm"].get("CORE_AGENTS") or [])
    route_log = set(src["boss"].get("ROUTE_LOG") or [])
    guard_rules = [g for g in src["guards"] if fnmatch.fnmatch(agent_id, str(g.get("agent", "")))]
    depts = [name for name, d in src["departments"].items() if isinstance(d, dict) and (
        d.get("head") == agent_id or any(m.get("agent_id") == agent_id for m in d.get("members") or []))]
    no_fx = "no effects map on this agent's card - run tools/check_effects.py --write once the agent is covered"

    io = _field({
        "transport": card.get("transport") or _gap("card declares no transport"),
        "endpoint": card.get("endpoint") or _gap("card declares no endpoint"),
        "mqtt_topics": card.get("mqtt_topics") or _gap("card declares no MQTT topics"),
        "per_verb_schemas": _gap("no agent declares input/output schemas per verb"),
    }, ["config/agent_cards"])

    deps = _field({
        "granted_resources": {k: v for k, v in granted.items() if v} or "none granted",
        "departments": depts or "none",
        "services": _gap("no declaration names the services this agent calls (Hermes, Memory, Inference ...)"),
    }, ["config/resource_acl.json", "config/departments.json"])

    env = _field({
        "entry_point": cfg.get("entry_point") or _gap("agent config declares no entry_point"),
        "port": card.get("port") or cfg.get("port") or _gap("no port declared"),
        "supervision": ("service_manager (on demand: POST /heal)" if agent_id in core
                        else "unsupervised - on demand; off is its normal state") if core else
        _gap("service_manager scope unreadable"),
        "sandbox": _gap("no per-agent sandbox assignment is declared"),
    }, ["config/agent_configs", "config/agent_cards", "services/service_manager CORE_AGENTS"])

    fail = _field({
        "supervisor_restart": ({"max_restarts": src["sm"].get("MAX_RESTARTS"),
                                "window_seconds": src["sm"].get("RESTART_WINDOW"),
                                "then": "abandoned, with a reason and a log hint"}
                               if agent_id in core else
                               _gap("not in service_manager's supervised set - nothing restarts it")),
        "agent_policy": _gap("the agent declares no failure policy of its own"),
    }, ["services/service_manager MAX_RESTARTS, RESTART_WINDOW"])

    if effects is not None:
        signoff = sorted(v for v, c in effects.items() if c in SIGNOFF_CLASSES)
        esc_verbs = signoff or f"none of its {len(effects)} classified verbs actuate or go external"
    else:
        esc_verbs = _gap(no_fx)
    esc = _field({
        "principal_signoff_verbs": esc_verbs,
        "gated_verbs": gated if gated is not None else _gap("card declares no effects_gated_by"),
    }, ["config/agent_cards effects / effects_gated_by", "CLAUDE.md: no agent holds actuation or third-party authority"])

    aud = _field({
        "routes_recorded_by_boss": (agent_id in route_log) if src["boss"].get("ROUTE_LOG") is not None
        else _gap("Boss ROUTE_LOG unreadable"),
        "record_verbs": (sorted(v for v, c in effects.items() if c == "records") if effects is not None
                         else _gap(no_fx)),
        "lifecycle_audit": "every mycos lifecycle run is recorded in the Provenance store",
    }, ["agents/boss_agent ROUTE_LOG", "config/agent_cards effects", "mycos/lifecycle"])

    val = _field({
        "guard_deny_rules": [{"task": g.get("task"), "target_glob": g.get("target_glob"),
                              "reason": g.get("reason")} for g in guard_rules] or "none name this agent",
        "gated_verbs": gated if gated is not None else _gap("card declares no effects_gated_by"),
        "per_verb_validation": _gap("no agent declares what a verb's output must satisfy"),
    }, ["config/guards.json", "config/agent_cards effects_gated_by"])

    return {
        "identity": {"agent_id": agent_id, "port": cfg.get("port"),
                     "entry_point": cfg.get("entry_point") or MISSING},
        "capabilities": verbs or MISSING,
        "roles": cfg.get("roles") or MISSING,
        "inputs_outputs": io,
        "permitted_operations": effects if effects is not None else MISSING,
        "data_access_boundaries": {"owns": owns, "granted": granted},
        "required_permissions": {k: v for k, v in granted.items() if v} or "ownership only",
        "dependencies": deps,
        "execution_environment": env,
        "validation_requirements": val,
        "failure_handling": fail,
        "escalation_conditions": esc,
        "audit_requirements": aud,
        "version_provenance": {"version": card.get("version") or MISSING,
                               "owner": card.get("owner") or MISSING,
                               "effects_basis": card.get("effects_basis") or MISSING},
        "assembled_from": ["config/agent_configs", "config/agent_cards", "config/resource_acl.json",
                           "config/guards.json", "config/departments.json",
                           "services/service_manager (constants)", "agents/boss_agent (constants)"],
    }


def all_contracts():
    src = _sources()
    return {a: contract(a, src=src) for a in agent_ids()}


def gaps(c):
    """Every required_but_missing part in one contract: [(field, part, why)]."""
    out = []
    for k, v in (c or {}).items():
        if v == MISSING:
            out.append((k, None, "not declared"))
        elif isinstance(v, dict) and "parts" in v:
            for pk, pv in v["parts"].items():
                if isinstance(pv, dict) and pv.get("value") == MISSING:
                    out.append((k, pk, pv["why"]))
        elif isinstance(v, dict):
            for pk, pv in v.items():
                if pv == MISSING:
                    out.append((k, pk, "not declared"))
    return out


def coverage():
    """Per field: declared / partial / required_but_missing across agents."""
    out = {}
    for a, c in all_contracts().items():
        for k, v in (c or {}).items():
            if k == "assembled_from":
                continue
            if isinstance(v, dict) and "status" in v:
                st = v["status"]
            elif v == MISSING or (isinstance(v, dict) and MISSING in v.values()):
                st = "required_but_missing" if v == MISSING else "partial"
            else:
                st = "declared"
            out.setdefault(k, {"declared": 0, "partial": 0, "required_but_missing": 0})[st] += 1
    return out
