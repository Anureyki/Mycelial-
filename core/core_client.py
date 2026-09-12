#!/usr/bin/env python3
"""Ask Mycos Core what it thinks. The OS's only door into the model.

    from core.core_client import ask
    ask("trust_agent", "read", "credential:COURTLISTENER_API_TOKEN")

THE BOUNDARY RUNS BOTH WAYS. mycelial-core must not import this repo, and this
repo must not import mycelial-core - its tools/check_boundary.py fails that
build over either. So the only coupling between them is a socket and an agreed
request shape, which means Core can be restarted, retrained, replaced with a
different generation, or simply absent, and nothing here changes.

WHAT COMES BACK IS AN OPINION, AND THE SHAPE SAYS SO.

    {"available": True, "opinion": {"decision": "denied", ...}}
    {"available": False, "why": "..."}

There is deliberately NO BOOLEAN in that structure. A function returning
True/False here would be one refactor from `if core_client.ask(...):` guarding
a resource, and CLAUDE.md is explicit that a monitor acting on its own
findings is an authority nobody granted. The caller has to reach into
`opinion` and decide what to do with a model's guess, which is the point.

UNAVAILABLE IS NEITHER ALLOW NOR DENY. Core being down must not open a gate,
and it must not close one either - an inference service that halted work by
being offline would be a single point of failure bolted onto a system whose
interior already fails closed on its own authority. `available: False` is a
third state and callers must handle it as one.

SHORT TIMEOUT, ON PURPOSE. The ACL decides in microseconds and fails closed;
a network call in front of it would make every permission check wait on a
model. Nothing in the OS may BLOCK on Core, so the timeout is small and a
timeout is just unavailability with a reason attached.
"""
import json
import os
import urllib.error
import urllib.request

# Loopback only. The model has no business being reachable from the LAN, and
# the nginx proxy deliberately does not carry it.
CORE_URL = os.environ.get("MYCOS_CORE_URL", "http://127.0.0.1:8018")
TIMEOUT = float(os.environ.get("MYCOS_CORE_TIMEOUT", "3.0"))


def _get(path, timeout=None):
    req = urllib.request.Request(CORE_URL.rstrip("/") + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def model():
    """-> which checkpoint is answering, and what it scored. Or unavailable."""
    try:
        return {"available": True, "model": _get("/model")}
    except Exception as exc:                        # noqa: BLE001
        return {"available": False, "why": f"{type(exc).__name__}: {exc}"}


def ask(agent, action, resource, timeout=None):
    """-> {"available": bool, "opinion": {...}} — never a decision.

    The three fields are the only question this model was trained to answer.
    They are sent as given and not defaulted: Core refuses a blank field with
    a 400 rather than rendering "agent=None" into a string the model would
    answer confidently, and that refusal is worth keeping visible here."""
    if not (agent and action and resource):
        return {"available": False,
                "why": ("agent, action and resource are all required; a blank "
                        "one would be answered as though it were a real "
                        "request")}
    body = json.dumps({"agent": agent, "action": action,
                       "resource": resource}).encode("utf-8")
    req = urllib.request.Request(
        CORE_URL.rstrip("/") + "/predict", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
            out = json.loads(r.read().decode("utf-8"))
        return {"available": True, "opinion": out,
                "authority": "none - the ACL decides; this is an opinion"}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", "")
        except Exception:                           # noqa: BLE001
            pass
        # A 503 means Core is up and has nothing PROMOTED to serve, which is a
        # different fact from Core being down, and a reader should be able to
        # tell them apart.
        return {"available": False,
                "why": f"HTTP {exc.code}: {detail or exc.reason}",
                "nothing_promoted": exc.code == 503}
    except Exception as exc:                        # noqa: BLE001
        return {"available": False, "why": f"{type(exc).__name__}: {exc}"}
