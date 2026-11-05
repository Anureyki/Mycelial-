#!/usr/bin/env python3
"""
Tool Service – MCP integration layer.
Loads config from mcp.json and all .json files in mcp.d/
Includes common service wrapper: logging, memory, policy.
"""
import os
import json
import glob
import subprocess
import time
import uuid
import requests
from urllib.parse import urlparse
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

BASE = os.path.expanduser("~/mycelial")
CONFIG_DIR = os.path.join(BASE, "config")
MAIN_CONFIG = os.path.join(CONFIG_DIR, "mcp.json")
EXTRA_DIR = os.path.join(CONFIG_DIR, "mcp.d")
HTTP_ALLOWLIST_FILE = os.path.join(CONFIG_DIR, "tool_allowlist.json")
HTTP_FETCH_MAX_BYTES = 500_000
os.makedirs(EXTRA_DIR, mode=0o700, exist_ok=True)

# In-memory config
servers = {}

# ---------- Common service helpers ----------
def log_to_audit(event_type, message, level="info", metadata=None):
    """Send log to Logging Service."""
    if metadata is None:
        metadata = {}
    try:
        import requests
        requests.post("http://localhost:8009/log", json={
            "agent_id": "tool_service",
            "event_type": event_type,
            "task": "mcp_tool",
            "result": message,
            "level": level,
            "metadata": metadata,
            "namespace": "tool"
        }, timeout=3)
    except:
        pass

def store_memory(key, value, namespace="tool_usage"):
    """Store tool usage in Memory Service."""
    try:
        import requests
        requests.post("http://localhost:8007/store", json={
            "namespace": namespace,
            "key": key,
            "value": value,
            "pin": False
        }, timeout=3)
    except:
        pass

def check_policy(action, context):
    """Ask Policy Service if action is allowed."""
    try:
        import requests
        resp = requests.post("http://localhost:8008/evaluate", json={
            "type": action,
            "context": context
        }, timeout=3)
        if resp.status_code == 200:
            return resp.json().get("allowed", True)
    except:
        pass
    return True  # allow by default if policy service is unreachable

# ---------- MCP config loading ----------
def load_mcp_config():
    global servers
    config = {}
    if os.path.exists(MAIN_CONFIG):
        with open(MAIN_CONFIG, "r") as f:
            config.update(json.load(f).get("servers", {}))
    if os.path.exists(EXTRA_DIR):
        for filepath in glob.glob(os.path.join(EXTRA_DIR, "*.json")):
            with open(filepath, "r") as f:
                config.update(json.load(f))
    servers = config
    return config

def reload_config():
    load_mcp_config()
    return servers

load_mcp_config()

# ---------- Remote MCP (Streamable HTTP) ----------
# Tool Service holds MCP usage for every agent (principal, 2026-11-05). Local
# servers are launched over stdio; a server configured with a `url` is spoken to
# over HTTP. Secrets never sit in the config: a header value may reference an
# environment variable as ${NAME}, filled in at call time from .env - and an
# unset variable REFUSES the call rather than sending an empty credential.
import re as _re

_ENV_REF = _re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_headers(headers):
    out, missing = {}, []
    for k, v in (headers or {}).items():
        def sub(m):
            val = os.environ.get(m.group(1))
            if not val:
                missing.append(m.group(1))
                return ""
            return val
        out[k] = _ENV_REF.sub(sub, str(v))
    return out, missing


def _rpc_body(resp):
    """A JSON-RPC response from a Streamable HTTP reply - plain JSON or an event stream."""
    ctype = resp.headers.get("Content-Type", "")
    if "text/event-stream" in ctype:
        msg = None
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                try:
                    msg = json.loads(line[5:].strip())
                except ValueError:
                    continue
        return msg
    return resp.json() if resp.text.strip() else None


def call_remote_mcp(server_id, config, method, params=None):
    url = config.get("url")
    headers, missing = _expand_headers(config.get("headers"))
    if missing:
        return {"error": f"{server_id}: header references {', '.join(missing)}, which is not set in the "
                         "environment - add it to .env; the call was refused rather than sent without it"}
    base = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers}
    try:
        init = requests.post(url, headers=base, timeout=30, json={
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "mycelial-tool-service", "version": "1.1.0"}}})
        if init.status_code >= 400:
            return {"error": f"{server_id}: initialize returned HTTP {init.status_code}: {init.text[:400]}"}
        sess = init.headers.get("Mcp-Session-Id")
        h = dict(base, **({"Mcp-Session-Id": sess} if sess else {}))
        requests.post(url, headers=h, timeout=30, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = requests.post(url, headers=h, timeout=60, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                                            "params": params or {}})
        if r.status_code >= 400:
            return {"error": f"{server_id}: {method} returned HTTP {r.status_code}: {r.text[:400]}"}
        body = _rpc_body(r)
        return body if body is not None else {"error": f"{server_id}: {method} returned no JSON-RPC message"}
    except Exception as e:
        return {"error": f"{server_id}: {type(e).__name__}: {e}"}


# ---------- MCP server caller ----------
def call_mcp_server(server_id, method, params=None):
    if server_id not in servers:
        return {"error": f"Unknown MCP server: {server_id}"}
    config = servers[server_id]
    # A per-server tool allowlist: when present, only the tools named in it may be
    # called. A server that can act on the world - place a call, send a message -
    # starts with only its read-only tools listed.
    if method == "tools/call" and "allowed_tools" in config:
        name = (params or {}).get("name")
        if name not in (config.get("allowed_tools") or []):
            return {"error": f"{server_id}: tool {name!r} is not on this server's allowed_tools list - "
                             "refused; add it to the server's config deliberately to allow it"}
    if config.get("url"):
        return call_remote_mcp(server_id, config, method, params)
    cmd = config.get("command")
    if not cmd:
        return {"error": f"No command for server: {server_id}"}

    init_request = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mycelial-tool-service", "version": "1.0.0"}
        },
        "id": 0
    }
    method_request = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": 1
    }
    input_data = json.dumps(init_request) + "\n" + json.dumps(method_request) + "\n"

    try:
        proc_env = os.environ.copy()
        proc_env.update({k: v for k, v in config.get("env", {}).items() if v})
        proc = subprocess.Popen(
            [cmd] + config.get("args", []),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=proc_env
        )
        stdout, stderr = proc.communicate(input=input_data)
        if proc.returncode != 0:
            return {"error": f"MCP server error: {stderr}"}
        lines = stdout.strip().split('\n')
        if len(lines) >= 2:
            response = json.loads(lines[-1])
            return response
        else:
            return {"error": "No response from MCP server"}
    except Exception as e:
        return {"error": str(e)}

# ---------- API Endpoints ----------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "tool"})

@app.route("/reload", methods=["POST"])
def reload():
    config = reload_config()
    log_to_audit("CONFIG_RELOAD", f"Reloaded config: {list(config.keys())}")
    return jsonify({"success": True, "servers": list(config.keys())})

@app.route("/execute", methods=["POST"])
def execute():
    data = request.json or {}
    task = data.get("task")
    args = data.get("args", [])
    sender = data.get("sender", "unknown")
    if task is None and "params" in data:
        params = data["params"]
        task = params.get("task")
        args = params.get("args", [])
        sender = params.get("sender", "unknown")

    # Policy check for tool access
    if not check_policy("tool_access", {"sender": sender, "task": task}):
        log_to_audit("TOOL_DENIED", f"Tool access denied for {sender} on task {task}", level="warning")
        return jsonify({"error": "Access denied by policy"}), 403

    if task == "list_tools":
        tools = []
        for server_id, config in servers.items():
            response = call_mcp_server(server_id, "tools/list")
            if response and "result" in response and "tools" in response["result"]:
                for tool in response["result"]["tools"]:
                    tool["server"] = server_id
                    tools.append(tool)
        return jsonify({"result": tools})

    elif task == "call_tool":
        if len(args) < 2:
            return jsonify({"error": "Usage: call_tool <server_id> <tool_name> [args_json]"})
        server_id = args[0]
        tool_name = args[1]
        tool_args = json.loads(args[2]) if len(args) > 2 else {}
        response = call_mcp_server(server_id, "tools/call", {"name": tool_name, "arguments": tool_args})

        # Log the call
        log_to_audit("TOOL_CALL", f"Tool {tool_name} called on {server_id} by {sender}",
                     metadata={"server": server_id, "tool": tool_name, "args": tool_args, "sender": sender})
        # Store usage in memory
        store_memory(f"{server_id}_{tool_name}_{datetime.now().isoformat()}", json.dumps({"sender": sender, "args": tool_args, "response": response}), namespace="tool_logs")

        return jsonify({"result": response})

    else:
        return jsonify({"error": f"Unknown task: {task}"}), 400

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8015, debug=False)
