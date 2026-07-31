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
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

BASE = os.path.expanduser("~/mycelial")
CONFIG_DIR = os.path.join(BASE, "config")
MAIN_CONFIG = os.path.join(CONFIG_DIR, "mcp.json")
EXTRA_DIR = os.path.join(CONFIG_DIR, "mcp.d")
os.makedirs(EXTRA_DIR, exist_ok=True)

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

# ---------- MCP server caller ----------
def call_mcp_server(server_id, method, params=None):
    if server_id not in servers:
        return {"error": f"Unknown MCP server: {server_id}"}
    config = servers[server_id]
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
        proc = subprocess.Popen(
            [cmd] + config.get("args", []),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ.copy()
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
    app.run(host="0.0.0.0", port=8015, debug=False)
