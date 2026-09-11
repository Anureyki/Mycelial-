#!/usr/bin/env python3
"""What agent code may do: EMIT. It may not write a record.

    from core.security_events import emit
    emit("acl_denied", agent="trust_agent",
         resource="credential:COURTLISTENER_API_TOKEN",
         action="read", decision="denied", reason=why)

THE SEPARATION IS THE POINT. The component that made a decision does not get to
write the record of that decision. It reports what happened; the harness in
tools/eval_harness.py decides what the record says, verifies what it can, and
is the only thing that writes.

Self-grading is the failure this exists to prevent, and it is not hypothetical
in this repo: CLAUDE.md already records an agent reporting `productive / high
confidence` on a photo no model had assessed, and a trading desk assembling its
own end-of-day report from what its components said they did. A security layer
that writes its own report card is the same shape with worse consequences,
because the thing it would be grading is whether it let something through.

WHAT EMIT CAN AND CANNOT CONTROL:

  it supplies    the facts of what happened - who, what, the decision, the
                 reason given, and any provenance id it already has
  it cannot set  the record's classification, its position in the chain, its
                 integrity hash, or whether the provenance actually exists

An emitter that could label its own event as a positive training example could
train the student on its own mistakes. So the spool carries raw facts and
nothing that looks like a verdict on itself.

APPEND-ONLY AT THE SYSCALL. O_APPEND with one write per line, so two agents
emitting at once cannot interleave a partial record, and a process cannot seek
back over what it already wrote.
"""
import json
import os
import threading
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPOOL = os.path.join(ROOT, "state", "security_events")
_lock = threading.Lock()

# A closed set. An event type that is not here is a kind of decision nobody
# decided to observe, and inventing one at the call site means the harness has
# no rule for classifying it.
EVENT_TYPES = (
    "acl_denied", "acl_allowed",
    "guard_denied", "guard_allowed",
    "channel_denied", "channel_allowed",
    "veto_fired", "veto_passed",
    "artifact_signed", "artifact_rejected",
    "route_selected", "route_refused",
)


class UnknownEventType(ValueError):
    pass


def emit(event_type, agent=None, resource=None, action=None, decision=None,
         reason=None, provenance_event=None, extra=None, spool=None):
    """Report one security decision. -> the emitted dict, or raises.

    RAISES on an unknown type rather than recording it as unclassified. A
    security event the harness cannot classify is one that would land in the
    training set with no label, and an unlabelled denial teaches avoidance
    without teaching why."""
    if event_type not in EVENT_TYPES:
        raise UnknownEventType(
            f"{event_type!r} is not an observed event type. The set is closed: "
            f"{list(EVENT_TYPES)}. Add it there and give the harness a rule "
            f"for it before emitting it.")
    ev = {
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "agent": agent,
        "resource": resource,
        "action": action,
        "decision": decision,
        # THE REASON IS NOT OPTIONAL, and the harness rejects a record without
        # one. A denied request with no reason is a negative example that
        # teaches a model to avoid a shape rather than to understand a rule.
        "reason": reason,
        "provenance_event": provenance_event,
        "pid": os.getpid(),
    }
    if extra:
        ev["extra"] = extra
    d = spool or SPOOL
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"emitted-{datetime.now(timezone.utc):%Y-%m-%d}.jsonl")
    line = json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n"
    with _lock:
        # O_APPEND: the kernel places every write at the end, so a process
        # cannot seek back over what it already emitted.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    return ev
