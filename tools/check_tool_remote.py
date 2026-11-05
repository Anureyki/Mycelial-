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
srv.shutdown()
print("check_tool_remote: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
