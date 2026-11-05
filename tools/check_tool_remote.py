#!/usr/bin/env python3
"""Build gate for Tool Service's remote MCP transport (services/tool/service.py).

A local fake MCP server on loopback stands in for a remote one (no network, no real
key). It requires `Authorization: Bearer <secret>` and answers initialize, tools/list
(plain JSON) and tools/call (event stream). Proves:
  - a header referencing ${VAR} is filled from the environment and the call succeeds
  - an unset ${VAR} REFUSES the call - no request is sent with an empty credential
  - the session id from initialize is carried on the following requests
  - an event-stream reply is parsed
  - a server's allowed_tools list refuses any tool not on it
  - an HTTP error from the server comes back as an error with its text, not a silent empty
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "services", "tool"))
import service as ts  # noqa: E402

fails, seen = [], []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        seen.append({"auth": self.headers.get("Authorization"), "session": self.headers.get("Mcp-Session-Id"),
                     "method": body.get("method")})
        if self.headers.get("Authorization") != "Bearer test-secret":
            self.send_response(401); self.end_headers(); self.wfile.write(b'{"error":"unauthorized"}'); return
        m = body.get("method")
        if m == "notifications/initialized":
            self.send_response(202); self.end_headers(); return
        if m == "initialize":
            out, extra = {"jsonrpc": "2.0", "id": 0, "result": {"serverInfo": {"name": "fake"}}}, {"Mcp-Session-Id": "s-123"}
        elif m == "tools/list":
            out, extra = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "list_pathways"}, {"name": "send_call"}]}}, {}
        else:
            msg = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "pathways: []"}]}}
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            self.wfile.write(f"event: message\ndata: {json.dumps(msg)}\n\n".encode()); return
        self.send_response(200); self.send_header("Content-Type", "application/json")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers(); self.wfile.write(json.dumps(out).encode())


srv = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/mcp"
ts.servers = {"fake": {"url": url, "headers": {"Authorization": "Bearer ${FAKE_MCP_KEY}"},
                       "allowed_tools": ["list_pathways"]}}

os.environ.pop("FAKE_MCP_KEY", None)
r = ts.call_mcp_server("fake", "tools/list")
check("an unset ${VAR} in a header refuses the call, and nothing is sent",
      "not set" in (r.get("error") or "") and seen == [])
os.environ["FAKE_MCP_KEY"] = "test-secret"
r = ts.call_mcp_server("fake", "tools/list")
check("the header is filled from the environment and tools/list comes back",
      [t["name"] for t in r.get("result", {}).get("tools", [])] == ["list_pathways", "send_call"])
check("the session id from initialize rides on the following requests",
      [s["session"] for s in seen if s["method"] == "tools/list"] == ["s-123"])
r = ts.call_mcp_server("fake", "tools/call", {"name": "list_pathways", "arguments": {}})
check("an allowed tool is called and its event-stream reply parsed",
      r.get("result", {}).get("content", [{}])[0].get("text") == "pathways: []")
n = len(seen)
r = ts.call_mcp_server("fake", "tools/call", {"name": "send_call", "arguments": {"phone": "x"}})
check("a tool not on allowed_tools is refused before anything is sent",
      "not on this server's allowed_tools" in (r.get("error") or "") and len(seen) == n)
os.environ["FAKE_MCP_KEY"] = "wrong"
r = ts.call_mcp_server("fake", "tools/list")
check("an HTTP error from the server comes back with its status and text",
      "HTTP 401" in (r.get("error") or "") and "unauthorized" in r["error"])
os.environ["FAKE_MCP_KEY"] = "test-secret"
with ts.app.test_client() as c:
    rr = c.post("/execute", json={"task": "list_server_tools", "args": ["fake"], "sender": "anansi"}).get_json()
check("list_server_tools lists one server's tools over the remote transport",
      [t["name"] for t in rr["result"]["result"]["tools"]] == ["list_pathways", "send_call"])
# ---- guard: full access with guardrails (synthetic numbers only) ----
import tempfile
ts.HELD_LOG = os.path.join(tempfile.mkdtemp(), "held.jsonl")
ts.log_to_audit = lambda *a, **k: None
os.environ["FAKE_PRINCIPAL_PHONE"] = "+1 (555) 010-0100"
g = {"mode": "dry_run", "principal_phone_env": "FAKE_PRINCIPAL_PHONE", "read_tools": ["list_pathways"],
     "write_tools": ["update_agent"], "contact_targets": {"send_call": ["phone_number", "transfer_phone_number"]},
     "contact_untargeted": ["send_sms_conversation_message"], "held_tools": {"delete": ["delete_agent"]}}
ts.servers["fake"] = {"url": url, "headers": {"Authorization": "Bearer ${FAKE_MCP_KEY}"}, "guard": g}
n = len(seen)
mine = ts.call_mcp_server("fake", "tools/call", {"name": "send_call", "arguments": {"phone_number": "5550100100"}})
check("a call to the principal's own number is cleared (dry run: logged, not sent)",
      mine.get("dry_run") and mine.get("verdict") == "principal" and len(seen) == n)
other = ts.call_mcp_server("fake", "tools/call", {"name": "send_call", "arguments": {"phone_number": "5550199999"}})
check("a call to anyone else is HELD with an id", other.get("held") and other.get("id"))
mixed = ts.call_mcp_server("fake", "tools/call", {"name": "send_call",
                           "arguments": {"phone_number": "5550100100", "transfer_phone_number": "5550199999"}})
check("a call to him that transfers to someone else is held", mixed.get("held"))
nonum = ts.call_mcp_server("fake", "tools/call", {"name": "send_call", "arguments": {}})
check("a contact tool with no readable number is held, never assumed his", nonum.get("held"))
check("an untargeted contact tool is held",
      ts.call_mcp_server("fake", "tools/call", {"name": "send_sms_conversation_message", "arguments": {}}).get("held"))
check("a delete is held",
      ts.call_mcp_server("fake", "tools/call", {"name": "delete_agent", "arguments": {}}).get("held"))
check("an unclassified tool is held, not allowed",
      ts.call_mcp_server("fake", "tools/call", {"name": "brand_new_tool", "arguments": {}}).get("held"))
w = ts.call_mcp_server("fake", "tools/call", {"name": "update_agent", "arguments": {}})
check("an allowed write is cleared, and in dry run is not sent", w.get("dry_run") and w.get("verdict") == "write" and len(seen) == n)
r = ts.call_mcp_server("fake", "tools/call", {"name": "list_pathways", "arguments": {}})
check("a read reaches the server even in dry run", "result" in r and len(seen) > n)
os.environ.pop("FAKE_PRINCIPAL_PHONE")
check("with his number unset, a call 'to him' is held rather than guessed",
      ts.call_mcp_server("fake", "tools/call", {"name": "send_call", "arguments": {"phone_number": "5550100100"}}).get("held"))
with ts.app.test_client() as c:
    held = c.post("/execute", json={"task": "list_held", "args": [], "sender": "t"}).get_json()["result"]
    check("list_held shows the open holds", other["id"] in [h["id"] for h in held])
    ap = c.post("/execute", json={"task": "resolve_held", "args": [other["id"], "approve"], "sender": "principal"}).get_json()
    check("approving a hold in dry run still sends nothing", ap["result"]["outcome"].get("dry_run") and len(seen) == n + 3)
    again = c.post("/execute", json={"task": "resolve_held", "args": [other["id"], "approve"], "sender": "principal"}).get_json()
    check("a hold resolves only once", "already resolved" in (again.get("error") or ""))
    g["mode"] = "live"; os.environ["FAKE_PRINCIPAL_PHONE"] = "5550100100"
    k = len(seen)
    live = ts.call_mcp_server("fake", "tools/call", {"name": "send_call", "arguments": {"phone_number": "5550100100"}})
    check("in live mode a call to him is actually sent", len(seen) > k and "result" in live)
    k = len(seen)
    h2 = ts.call_mcp_server("fake", "tools/call", {"name": "send_call", "arguments": {"phone_number": "5550199999"}})
    check("in live mode a third-party call is still held, nothing sent", h2.get("held") and len(seen) == k)
srv.shutdown()
print("check_tool_remote: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
