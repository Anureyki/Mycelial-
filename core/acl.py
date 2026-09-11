#!/usr/bin/env python3
"""The interior check. Permission, not ownership - and it fails CLOSED.

    from core.acl import check
    check("legal_agent", "ledger:finance_shared", "read")   # -> (True, why)
    check("trust_agent", "credential:COURTLISTENER_API_TOKEN", "read")  # -> (False, why)

WHY THIS REPLACED AN OWNERSHIP TEST. The first cut asked "who owns this
secret" and gave it to nobody else, which is too blunt for how these
departments actually work. Legal, Trust and Accounting all operate in finance
and share resources constantly: Legal reads Accounting's ledger to test whether
an instrument operates as claimed; Trust reads it to test whether a beneficial
interest is borne out. Under an ownership test both are refused, and the only
way to allow them is to pretend they own the books.

So an agent does not need to OWN a resource to read it. It needs PERMISSION,
and permission is a grant somebody wrote down. Ownership survives as the
default grant - the owner always has read, write and execute - which keeps the
common case one line and makes sharing an edit rather than a redesign.

THE TWO DIRECTIONS ARE DELIBERATE AND OPPOSITE:

    perimeter (check_guard)   FAILS OPEN. A Security Agent that is restarting
                              must not halt a grow reading. An outage should
                              not stop work.
    interior  (this file)     FAILS CLOSED. An unreadable ACL, an unknown
                              resource, an unlisted action - all denied. An
                              outage must not GRANT access.

Both are correct and the difference is what the failure costs. An
unavailable perimeter that denies would take the system down every time
Security restarts; an unavailable interior that allows would hand out Legal's
token to whoever asked while nobody could check.

READ / WRITE / EXECUTE ARE SEPARATE ON PURPOSE. Legal reads Accounting's
ledger and may never write it - a department that could write another's ledger
could make its own reading true.
"""
import json
import os
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACL_FILE = os.path.join(ROOT, "config", "resource_acl.json")
ACTIONS = ("read", "write", "execute")

_cache = {"at": 0.0, "doc": None, "error": None}
_lock = threading.Lock()


class ACLUnavailable(Exception):
    """The ACL could not be read. Everything is denied until it can be."""


def load_acl(path=None, force=False):
    """-> resources dict. Raises rather than returning {} when unreadable.

    An empty dict and an unreadable file must not look the same: the first
    denies everything because nothing is granted, the second denies everything
    because nothing is KNOWN, and only the second is an incident."""
    import time
    p = path or ACL_FILE
    with _lock:
        if not force and _cache["doc"] is not None and time.time() - _cache["at"] < 30:
            return _cache["doc"]
        try:
            with open(p, encoding="utf-8") as fh:
                doc = json.load(fh)
            res = doc.get("resources")
            if not isinstance(res, dict):
                raise ACLUnavailable(f"{p} has no resources map")
            _cache.update({"at": time.time(), "doc": res, "error": None})
            return res
        except ACLUnavailable:
            raise
        except Exception as e:
            _cache.update({"at": time.time(), "doc": None, "error": str(e)})
            raise ACLUnavailable(f"{p}: {e}") from e


def _provenance_for_grant(agent, resource, action, reason):
    """Record the grant, return its event id. -> None if it could not be written.

    AN ALLOW NEEDS SOMETHING BEHIND IT. The harness refuses to treat an
    `allowed` event as a positive training example unless its provenance
    resolves, and rightly: "it was allowed" with nothing behind it is
    indistinguishable from "nobody checked". Without this, every allow emitted
    provenance_event: None, every one was quarantined, and the training set was
    127 negatives and 0 positives - a set whose best model denies everything.

    Only ALLOWS are recorded. A denial is already fully described by its
    reason, and writing a provenance row for every refused probe would let
    anyone fill the store by asking for things they cannot have."""
    try:
        from core.provenance_schemas import new_provenance_event
        from core.provenance_manager import ProvenanceManager
        ev = new_provenance_event(
            operation="approve", actor_type="agent", agent_id=agent,
            metadata={"resource": resource, "action": action,
                      "reason": reason, "layer": "interior_acl"})
        ProvenanceManager().record_event(ev)
        return ev["event_id"]
    except Exception:
        return None


def _observe(event_type, agent, resource, action, decision, reason):
    """Report the decision. This is an EMIT, not a write - core/security_events
    hands it to the harness, which decides what the record says. A component
    that wrote its own security record would be grading its own homework on the
    one question that matters: whether it let something through."""
    try:
        from core.security_events import emit
        prov = (_provenance_for_grant(agent, resource, action, reason)
                if event_type == "acl_allowed" else None)
        emit(event_type, agent=agent, resource=resource, action=action,
             decision=decision, reason=reason, provenance_event=prov)
    except Exception as _e:
        # Observation must never change the DECISION - a harness that is down
        # cannot be allowed to deny a request, nor to allow one. But a silent
        # observer is the failure this project hunts: a removed import made
        # this path dead while the static gate still passed, because the call
        # was still written. So it is swallowed and SAID.
        try:
            import sys as _sys
            print(f"SECURITY EVENT NOT OBSERVED ({_e}) - the decision stood, "
                  f"the record did not", file=_sys.stderr)
        except Exception:
            pass


def check(agent, resource, action, acl=None, observe=True):
    """-> (allowed, reason). Never raises; a caller must be able to branch."""
    if action not in ACTIONS:
        return False, (f"{action!r} is not an action. The set is "
                       f"{list(ACTIONS)} and an unrecognised one is not a "
                       f"lesser permission, it is an unknown request.")
    if not agent or not resource:
        return False, "agent and resource are both required"
    try:
        res = acl if acl is not None else load_acl()
    except ACLUnavailable as e:
        # FAIL CLOSED. This is the interior.
        return False, (f"the ACL could not be read ({e}). The interior denies "
                       f"what it cannot verify - an outage must not grant "
                       f"access.")
    entry = res.get(resource)
    if not entry:
        _why = (f"{resource!r} has no ACL entry. An unlisted resource is "
                f"denied to everyone, including its owner - a resource nobody "
                f"wrote a rule for is a resource nobody decided about.")
        if observe:
            _observe("acl_denied", agent, resource, action, "denied", _why)
        return False, _why
    if False:
        return False, (f"{resource!r} has no ACL entry. An unlisted resource is "
                       f"denied to everyone, including its owner - a resource "
                       f"nobody wrote a rule for is a resource nobody decided "
                       f"about.")
    owner = entry.get("owner")
    if owner and agent == owner:
        _why = f"{agent} owns {resource} (ownership is the default grant)"
        if observe:
            _observe("acl_allowed", agent, resource, action, "allowed", _why)
        return True, _why
    granted = entry.get(action)
    if not isinstance(granted, list):
        return False, (f"{resource!r} declares no {action} list. A missing list "
                       f"is not an empty one - it means nobody decided.")
    if "*" in granted:
        _why = f"{resource} grants {action} to every agent"
        if observe:
            _observe("acl_allowed", agent, resource, action, "allowed", _why)
        return True, _why
    if agent in granted:
        _why = f"{resource} grants {action} to {agent} explicitly"
        if observe:
            _observe("acl_allowed", agent, resource, action, "allowed", _why)
        return True, _why
    _why = (f"{agent} is not granted {action} on {resource} "
            f"(owner={owner}, granted={granted or 'nobody'})")
    if observe:
        _observe("acl_denied", agent, resource, action, "denied", _why)
    return False, _why


def resources_for(agent, action="read", acl=None):
    """Everything this agent may touch. For building a sandbox environment."""
    try:
        res = acl if acl is not None else load_acl()
    except ACLUnavailable:
        return []          # fail closed: no ACL, no resources
    return sorted(r for r in res if check(agent, r, action, res)[0])


def audit(acl=None):
    """Structural problems in the ACL itself, for CI to fail on."""
    try:
        res = acl if acl is not None else load_acl()
    except ACLUnavailable as e:
        return {"readable": False, "why": str(e), "problems": ["ACL unreadable"]}
    problems = []
    for name, e in sorted(res.items()):
        if not e.get("owner"):
            problems.append(f"{name}: no owner")
        for a in ACTIONS:
            if not isinstance(e.get(a), list):
                problems.append(f"{name}: no {a} list (missing is not empty)")
        if not e.get("why"):
            problems.append(f"{name}: no reason recorded for its grants")
        if e.get("owner") and e.get("owner") in (e.get("read") or []):
            problems.append(f"{name}: owner listed in its own read grant - "
                            f"ownership is already the default grant, and a "
                            f"redundant entry hides whether it was deliberate")
    return {"readable": True, "resources": len(res), "problems": problems}
