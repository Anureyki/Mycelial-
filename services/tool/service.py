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


# ---------- Guarded servers ----------
# Full access with guardrails (principal, 2026-11-05, for Bland). A server whose
# config carries `guard` is sorted per call:
#   read tools ............................ run
#   contact tools aimed ONLY at the principal  run (his number from the env var)
#   contact to anyone else, or untargeted ... HELD for his yes
#   deletes, spending, raw API ............... HELD for his yes
#   every other write ........................ run
# and in guard mode "dry_run" nothing but a read reaches the server: the call is
# logged as what it would do. A hold is recorded in state/ and released only by
# resolve_held. A contact tool whose number cannot be read is held, never assumed
# to be the principal.
HELD_LOG = os.path.join(BASE, "state", "tool", "held_calls.jsonl")


def _digits(v):
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else d


def _held_append(rec):
    os.makedirs(os.path.dirname(HELD_LOG), mode=0o700, exist_ok=True)
    fd = os.open(HELD_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def _held_records():
    try:
        with open(HELD_LOG) as fh:
            return [json.loads(x) for x in fh if x.strip()]
    except OSError:
        return []


def _check_release(rec, artifact):
    """-> (ok, why). Two independent checks; both must pass."""
    if not isinstance(artifact, dict):
        return False, "no release was presented - a release is signed by Security and carries the principal's signature"
    try:
        import sys as _sys
        if BASE not in _sys.path:
            _sys.path.insert(0, BASE)
        from core.sandbox.signing import verify
        from core.principal_release import verify_release
    except Exception as e:
        return False, f"release checking is unavailable ({e}) - refused, not assumed"
    ok, why = verify(artifact, expected_sender="security_agent")
    if not ok:
        return False, f"Security's envelope does not verify: {why}"
    env = artifact["envelope"]
    body = env.get("body") or {}
    if env.get("recipient") != "tool_service" or env.get("kind") != "hold_release":
        return False, "the envelope is not a hold release addressed to Tool Service"
    if body.get("hold_id") != rec.get("id"):
        return False, "the envelope is for a different hold"
    ok, why, _ = verify_release(body.get("principal_payload"), body.get("principal_signature"), rec)
    if not ok:
        return False, f"the principal's signature does not hold up here: {why}"
    return True, "ok"


def guard_decision(server_id, guard, name, args):
    """-> (verdict, why). verdict: read | principal | write | held."""
    args = args or {}
    if name in (guard.get("read_tools") or []):
        return "read", "read-only tool"
    held = guard.get("held_tools") or {}
    for kind, names in held.items():
        if name in names:
            return "held", f"{kind}: needs the principal's yes"
    if name in (guard.get("contact_untargeted") or []):
        return "held", "contacts someone with no single number to check - needs the principal's yes"
    targets = (guard.get("contact_targets") or {}).get(name)
    if targets is not None:
        mine = _digits(os.environ.get(guard.get("principal_phone_env") or "", ""))
        present = [_digits(args.get(f)) for f in targets if args.get(f)]
        if not mine:
            return "held", "the principal's number is not set in the environment - held rather than guessed"
        if present and all(n == mine for n in present):
            return "principal", "to the principal's own number"
        return "held", "third-party contact - needs the principal's yes"
    if name in (guard.get("write_tools") or []):
        return "write", "a write the principal allowed directly"
    return "held", "not classified in this server's guard - held until someone decides what it is"


def _emit_hold(event_type, agent, rec, decision, reason, attempted_by=None):
    """Emit, never record - the harness writes the record (core/security_events).
    Observation must never change the decision, and must never fail mute."""
    try:
        import sys as _sys
        if BASE not in _sys.path:
            _sys.path.insert(0, BASE)
        from core.security_events import emit
        emit(event_type, agent=agent, resource=f"hold:{rec.get('server')}.{rec.get('tool')}",
             action="release" if attempted_by else "hold", decision=decision, reason=reason,
             extra={"hold_id": rec.get("id"), "requested_by": rec.get("requested_by"),
                    "attempted_by": attempted_by})
    except Exception as _e:
        print(f"SECURITY EVENT NOT OBSERVED ({_e}) - the decision stood, the record did not", flush=True)


def guarded_call(server_id, config, name, args, approved=False, requested_by=None):
    guard = config["guard"]
    verdict, why = guard_decision(server_id, guard, name, args)
    if verdict == "held" and not approved:
        hid = uuid.uuid4().hex[:10]
        from core.principal_release import args_digest
        rec = {"id": hid, "at": datetime.now().isoformat(timespec="seconds"), "server": server_id,
               "tool": name, "args": args or {}, "args_sha256": args_digest(args or {}), "why": why,
               "requested_by": requested_by or "unknown", "status": "held"}
        _held_append(rec)
        _emit_hold("hold_created", rec["requested_by"], rec, "held", f"held, not run: {why}")
        log_to_audit("TOOL_HELD", f"{server_id}.{name} held: {why}", metadata={"id": hid})
        return {"held": True, "id": hid, "tool": name, "why": why,
                "release": f"resolve_held {hid} approve - only on the principal's yes"}
    if verdict != "read" and guard.get("mode", "dry_run") != "live":
        return {"dry_run": True, "would_run": name, "verdict": verdict, "why": why,
                "note": "guard mode is dry_run - nothing was sent; the principal switches it live"}
    return None          # proceed to the server


# ---------- MCP server caller ----------
def call_mcp_server(server_id, method, params=None, approved=False, requested_by=None):
    if server_id not in servers:
        return {"error": f"Unknown MCP server: {server_id}"}
    config = servers[server_id]
    # A per-server tool allowlist: when present, only the tools named in it may be
    # called. A server that can act on the world - place a call, send a message -
    # starts with only its read-only tools listed.
    if method == "tools/call" and config.get("guard"):
        stop = guarded_call(server_id, config, (params or {}).get("name"), (params or {}).get("arguments"),
                            approved, requested_by)
        if stop is not None:
            return stop
    elif method == "tools/call" and "allowed_tools" in config:
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

    elif task == "resolve_held":
        # The principal's yes (or no) on a held call. A hold resolves once.
        if len(args) < 2 or args[1] not in ("approve", "deny"):
            return jsonify({"error": "Usage: resolve_held <id> approve|deny"})
        recs = _held_records()
        rec = next((r for r in recs if r.get("id") == args[0] and r.get("status") == "held"), None)
        if rec is None:
            return jsonify({"error": f"no open hold {args[0]!r}"})
        done = {d.get("resolves") for d in recs if d.get("resolves")}
        if rec["id"] in done:
            return jsonify({"error": f"hold {args[0]} was already resolved"})
        outcome = None
        if args[1] == "approve":
            # A release must be (1) an envelope Security signed for Tool Service, for this
            # hold, and (2) carry the principal's own signature over this hold's id, tool
            # and exact arguments - checked HERE again, not taken from Security's word.
            # sender is self-reported and proves nothing; it is recorded, never trusted.
            ok, why = _check_release(rec, args[2] if len(args) > 2 else None)
            if not ok:
                _emit_hold("hold_release_refused", sender, rec, "denied",
                           f"release refused: {why}", attempted_by=sender)
                return jsonify({"error": f"approve refused: {why}. The hold stays open."})
            _emit_hold("hold_released", sender, rec, "allowed",
                       "released on the principal's signature, verified by Security and by Tool Service",
                       attempted_by=sender)
            outcome = call_mcp_server(rec["server"], "tools/call", {"name": rec["tool"], "arguments": rec["args"]},
                                      approved=True, requested_by=rec.get("requested_by"))
        else:
            _emit_hold("hold_denied", sender, rec, "denied", "denied - the held action will not run",
                       attempted_by=sender)
        _held_append({"resolves": rec["id"], "at": datetime.now().isoformat(timespec="seconds"),
                      "decision": args[1], "by": sender, "outcome": outcome})
        log_to_audit("TOOL_HELD_RESOLVED", f"{rec['server']}.{rec['tool']} {args[1]} by {sender}",
                     metadata={"id": rec["id"]})
        return jsonify({"result": {"id": rec["id"], "decision": args[1], "outcome": outcome}})

    elif task == "list_held":
        recs = _held_records()
        done = {d.get("resolves") for d in recs if d.get("resolves")}
        return jsonify({"result": [r for r in recs if r.get("status") == "held" and r["id"] not in done]})

    elif task == "list_server_tools":
        # One server's tools - list_tools starts every configured server to answer.
        if not args:
            return jsonify({"error": "Usage: list_server_tools <server_id>"})
        return jsonify({"result": call_mcp_server(args[0], "tools/list")})

    elif task == "call_tool":
        if len(args) < 2:
            return jsonify({"error": "Usage: call_tool <server_id> <tool_name> [args_json]"})
        server_id = args[0]
        tool_name = args[1]
        tool_args = json.loads(args[2]) if len(args) > 2 else {}
        response = call_mcp_server(server_id, "tools/call", {"name": tool_name, "arguments": tool_args},
                                   requested_by=sender)

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
