#!/usr/bin/env python3
"""Build gate: Legal's MCP wiring lives in tools/legal/ and is callable only by permission.

Extracted 2026-11-09 (principal, option A). Proves: Legal's code no longer calls the
Tool Service for CourtListener itself; the owner is permitted; any other agent is
refused BEFORE anything is sent; an unreadable ACL refuses (fails closed); and the MCP
envelope unwraps as before. Security events go to a temp spool, never the live one.
"""
import os
import sys
import tempfile

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import core.security_events as se  # noqa: E402
se.SPOOL = os.path.join(tempfile.mkdtemp(), "spool")
from tools.legal import courtlistener as cl  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


src = open(os.path.join(ROOT, "agents", "legal_agent", "legal_agent.py"), encoding="utf-8").read()
check("Legal no longer wires CourtListener itself", 'call_tool("courtlistener"' not in src
      and "from tools.legal import courtlistener" in src)
check("one MCP unwrap, in the tool module", src.count("def _unwrap_mcp") == 0 and "_unwrap_mcp = staticmethod(_cl.unwrap)" in src)
check("the owner is permitted", cl.permitted("legal_agent")[0])
sent = []
import requests  # noqa: E402
_orig = requests.post
requests.post = lambda *a, **k: sent.append((a, k)) or type("R", (), {"status_code": 200, "json": lambda s: {"result": {"ok": 1}}})()
r = cl.call("trust_agent", "search", {"q": "x"})
check("another agent is refused, and nothing is sent", r.get("refused") and sent == [])
r = cl.call("legal_agent", "search", {"q": "x"})
check("the owner's call goes to the Tool Service as legal_agent",
      r == {"ok": 1} and len(sent) == 1 and sent[0][1]["json"]["sender"] == "legal_agent"
      and sent[0][1]["json"]["args"][0] == "courtlistener")
import core.acl as acl  # noqa: E402
_load = acl.load_acl
acl.load_acl = lambda *a, **k: (_ for _ in ()).throw(OSError("unreadable"))
n = len(sent)
r = cl.call("legal_agent", "search", {"q": "x"})
check("an unreadable ACL refuses - fails closed - and sends nothing", r.get("refused") and len(sent) == n)
acl.load_acl = _load
requests.post = _orig
env = {"result": {"content": [{"type": "text", "text": '{"count": 2}'}]}}
check("the MCP envelope unwraps to the payload", cl.unwrap(env) == {"count": 2}
      and cl.unwrap({"result": {"content": [{"type": "text", "text": "nope"}]}}).get("error"))
print("check_legal_tools: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
