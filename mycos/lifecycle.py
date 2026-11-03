#!/usr/bin/env python3
"""The MYCOS-CORE-001 execution lifecycle, composed from what core/ already has.

    from mycos.lifecycle import Request, run
    run(Request(agent_id="grow_agent", verb="record_level", resource="corpus:grow_agent",
                action="write", payload={...}),
        execute=fn, verify=fn, commit=fn)

Request -> Identity -> Classification -> Orchestration -> Capability ->
Policy -> Authorization -> Preflight -> Execution -> Verification ->
State commit -> Audit

Nothing here is a second governance engine. Each step calls the one that
exists:
  identity        the agent's declared contract (mycos/contracts, read from config)
  classification  the effects map core/effects.py wrote onto the agent's card
  orchestration   NOT done here - Anansi is the single router; the request names
                  the agent it was routed to and this records who routed it
  policy          core/acl.check - fails closed, as it already does
  authorization   Boss's authorize_route (policy only; Boss carries no request)
  audit           the Provenance store (core/provenance_manager), append-only

What is new is only what MYCOS-CORE-001 found missing: a decision fingerprint
(same validated inputs, config and policy -> same hash), the rule that a failed
verification commits nothing, and containment - an executor that raises is
logged and returned as a failure, never propagated as a crash.

A model may propose; nothing in this path asks one anything. ATI is not
imported anywhere under mycos/ (tools/check_mycos_core.py enforces it).
"""
import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from mycos.contracts import contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUMAN_SIGNOFF = {"actuates", "external"}     # CLAUDE.md: no agent holds actuation or third-party authority
POLICY_FILES = ("config/resource_acl.json", "config/guards.json")


@dataclass
class Request:
    agent_id: str
    verb: str
    resource: str
    action: str                     # read | write | execute  (core/acl.ACTIONS)
    payload: dict = field(default_factory=dict)
    actor: str = "anansi"           # who routed it - Anansi is the router
    human_signoff: bool = False     # an explicit sign-off for actuates/external


def _canon(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str)


def policy_version():
    """A hash of the policy files in force. Part of every fingerprint, so a
    decision made under one ACL is never mistaken for one made under another."""
    h = hashlib.sha256()
    for p in POLICY_FILES:
        try:
            with open(os.path.join(ROOT, p), "rb") as fh:
                h.update(p.encode() + b"\0" + fh.read())
        except OSError:
            h.update(p.encode() + b"\0<absent>")
    return h.hexdigest()[:16]


def fingerprint(req, steps):
    """Same validated inputs + same policy version + same step decisions ->
    the same hash. Timestamps are deliberately excluded."""
    return hashlib.sha256(_canon({"request": asdict(req), "policy": policy_version(),
                                  "steps": steps}).encode()).hexdigest()


def _default_authorize(domains):
    """Boss's authorize_route, called as the policy function it is. It holds no
    transport and no request, so calling it needs no running Boss."""
    from agents.boss_agent.boss_agent import BossAgent
    b = object.__new__(BossAgent)
    b.log = lambda *a, **k: None
    return BossAgent.authorize_route(b, {"domains": domains})


def run(req, execute, verify, commit, preflight=None, authorize=None, acl=None, audit=None):
    """Run one request through the lifecycle. -> record dict.

    execute(payload) -> result;  verify(result) -> (ok, why);  commit(result) -> ref
    preflight(req) -> (ok, why), optional.  authorize(domains) -> Boss's shape.
    audit(record) -> audit ref; defaults to the Provenance store."""
    from core import acl as core_acl
    steps, out = [], {"agent_id": req.agent_id, "verb": req.verb, "committed": False}

    def stop(step, why):
        steps.append({"step": step, "ok": False, "why": why})
        out.update(outcome="refused" if step not in ("execution", "verification") else "failed",
                   stopped_at=step, why=why)
        return finish()

    def finish():
        out["steps"] = steps
        out["policy_version"] = policy_version()
        out["fingerprint"] = fingerprint(req, [{k: s[k] for k in ("step", "ok")} for s in steps])
        try:
            out["audit"] = (audit or _audit_provenance)(out)
        except Exception as exc:    # an audit that cannot be written is said, never skipped silently
            out["audit"] = {"recorded": False, "why": str(exc)[:200]}
        return out

    # identity
    c = contract(req.agent_id)
    if c is None:
        return stop("identity", f"{req.agent_id!r} has no declared contract - unknown agent")
    steps.append({"step": "identity", "ok": True})
    # classification - the effects class the code was measured to have
    effects = c["permitted_operations"] if isinstance(c["permitted_operations"], dict) else {}
    cls = effects.get(req.verb, "unclassified")
    steps.append({"step": "classification", "ok": True, "class": cls})
    # orchestration - recorded, not performed
    steps.append({"step": "orchestration", "ok": True, "routed_by": req.actor})
    # capability selection
    caps = c["capabilities"] if isinstance(c["capabilities"], list) else []
    if req.verb not in caps:
        return stop("capability", f"{req.verb!r} is not a declared capability of {req.agent_id}")
    steps.append({"step": "capability", "ok": True})
    # policy validation - the interior ACL, fail closed
    ok, why = core_acl.check(req.agent_id, req.resource, req.action, acl=acl, observe=False)
    if not ok:
        return stop("policy", why)
    steps.append({"step": "policy", "ok": True})
    # authorization - Boss's policy, plus the standing human-signoff rule
    try:
        auth = (authorize or _default_authorize)([req.agent_id])
    except Exception as exc:
        return stop("authorization", f"authorization unavailable ({exc}) - fails closed")
    if not auth.get("allowed"):
        return stop("authorization", auth.get("reason") or "not permitted")
    if cls in HUMAN_SIGNOFF and not req.human_signoff:
        return stop("authorization", f"{req.verb} is classed {cls!r}; it needs the principal's sign-off")
    steps.append({"step": "authorization", "ok": True, "conditions": auth.get("conditions") or []})
    # preflight
    if preflight is not None:
        pok, pwhy = preflight(req)
        if not pok:
            return stop("preflight", pwhy)
    steps.append({"step": "preflight", "ok": True})
    # controlled execution - contained
    try:
        result = execute(req.payload)
    except Exception as exc:
        return stop("execution", f"{type(exc).__name__}: {str(exc)[:200]} (contained)")
    steps.append({"step": "execution", "ok": True})
    # verification - a failed one commits NOTHING
    try:
        vok, vwhy = verify(result)
    except Exception as exc:
        vok, vwhy = False, f"verifier raised {type(exc).__name__}: {exc}"
    if not vok:
        return stop("verification", f"{vwhy} - nothing committed")
    steps.append({"step": "verification", "ok": True})
    # state commit
    try:
        out["commit_ref"] = commit(result)
    except Exception as exc:
        return stop("execution", f"commit failed: {exc} (contained)")
    out["committed"] = True
    steps.append({"step": "state_commit", "ok": True})
    out["outcome"] = "committed"
    return finish()


def _audit_provenance(record, db_path=None):
    """The audit step: one append-only provenance event per lifecycle run, the
    record's canonical JSON hashed as its artifact, so the original can be
    re-verified later with ProvenanceManager.verify_artifact."""
    from core.provenance_manager import ProvenanceManager
    body = _canon({k: v for k, v in record.items() if k != "audit"})
    aid = "mycos:" + hashlib.sha256(body.encode()).hexdigest()[:24]
    ProvenanceManager(db_path).record_event({
        "event_id": aid + ":" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"),
        "operation": "execute", "actor_type": "system", "agent_id": record.get("agent_id"),
        "timestamp": datetime.now(timezone.utc).isoformat(), "artifact_id": aid,
        "metadata": {"verb": record.get("verb"), "outcome": record.get("outcome"),
                     "fingerprint": record.get("fingerprint")}}, artifact_content=body)
    return {"recorded": True, "artifact_id": aid, "content": body}
