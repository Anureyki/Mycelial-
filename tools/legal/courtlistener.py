#!/usr/bin/env python3
"""CourtListener through the Tool Service - standalone, and callable only by permission.

    from tools.legal import courtlistener
    courtlistener.call("legal_agent", "search", {"q": "...", "type": "o"})       # raw MCP reply
    courtlistener.call_unwrapped("legal_agent", "opinion_text", {"id": 123})       # the payload

Extracted from agents/legal_agent/legal_agent.py (principal, 2026-11-09, option A):
the wiring to an outside service is not legal reasoning, so it lives here as plain
functions, and Legal calls them like any other tool. What Legal DOES with a docket or
an opinion stays in Legal.

BY PERMISSION. Every call is checked against config/resource_acl.json for
`tool:courtlistener`, action `execute`, before anything is sent - the interior check,
which fails CLOSED: an unreadable ACL, an unlisted caller or an unknown resource is a
refusal with the reason, never a call. The resource is Legal's, like the credential
behind it (credential:COURTLISTENER_API_TOKEN); granting another department is an
edit to the ACL, not to this file.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SERVER = "courtlistener"
RESOURCE = "tool:courtlistener"
TOOL_SERVICE = "http://localhost:8015/execute"


def permitted(caller):
    """-> (ok, why). Fails closed."""
    try:
        from core.acl import check
        return check(caller, RESOURCE, "execute")
    except Exception as e:
        return False, f"the ACL check could not run ({e}) - refused, not assumed"


def call(caller, tool, args=None, timeout=120):
    """The Tool Service's reply for one CourtListener tool, or {"error": ...}."""
    ok, why = permitted(caller)
    if not ok:
        return {"error": f"{caller} may not call {SERVER}.{tool}: {why}", "refused": True}
    import requests
    try:
        r = requests.post(TOOL_SERVICE, timeout=timeout, json={
            "task": "call_tool", "args": [SERVER, tool, json.dumps(args or {})], "sender": caller})
        if r.status_code != 200:
            return {"error": f"Tool Service error: {r.status_code}"}
        return r.json().get("result", {"error": "No result"})
    except Exception as e:
        return {"error": str(e)}


def unwrap(raw):
    """Get the payload out of the JSON-RPC envelope MCP returns.

    A tool call comes back as {"result": {"content": [{"type": "text",
    "text": "<json>"}]}}, and reading the wrapper for the payload's keys
    finds none of them - so the first version reported the court's own order
    as unreadable while the text sat two levels down. The same nesting class
    as describe() looking for `reading` one level too deep and telling the
    grower his entry had not been logged.
    """
    d = raw
    for _ in range(4):
        if not isinstance(d, dict):
            break
        if "content" in d and isinstance(d["content"], list) and d["content"]:
            t = (d["content"][0] or {}).get("text")
            if isinstance(t, str):
                try:
                    return json.loads(t)
                except Exception:
                    return {"error": "MCP returned text that is not JSON", "raw": t[:400]}
        if "result" in d:
            d = d["result"]
            continue
        break
    return d


def call_unwrapped(caller, tool, args=None, timeout=120):
    return unwrap(call(caller, tool, args, timeout))
