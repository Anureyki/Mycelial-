#!/usr/bin/env python3
"""
Registry Service – Pure HTTP service.
No AgentBase, no circular dependency.
Stores agent info in memory and JSON file.
"""
import os
import json
import time
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

BASE = os.path.expanduser("~/mycelial")
REGISTRY_FILE = os.path.join(BASE, "state", "registry.json")
os.makedirs(os.path.dirname(REGISTRY_FILE), exist_ok=True)

agents = {}  # agent_id -> info
capability_map = {}

def load_registry():
    global agents, capability_map
    if os.path.exists(REGISTRY_FILE):
        try:
            with open(REGISTRY_FILE, "r") as f:
                data = json.load(f)
                for aid, info in data.items():
                    agents[aid] = info
                    for cap in info.get("capabilities", []):
                        capability_map[cap] = aid
        except:
            pass

def save_registry():
    with open(REGISTRY_FILE, "w") as f:
        json.dump(agents, f, indent=2)

load_registry()

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "registry"})

@app.route("/execute", methods=["POST"])
def execute():
    data = request.json or {}
    task = data.get("params", {}).get("task") or data.get("task")
    args = data.get("params", {}).get("args") or data.get("args", [])
    sender = data.get("sender", "unknown")

    if task == "register":
        if len(args) < 2:
            return jsonify({"error": "Missing agent_id or info"}), 400
        agent_id = args[0]
        info = json.loads(args[1]) if isinstance(args[1], str) else args[1]
        agents[agent_id] = {
            "agent_id": agent_id,
            "role": info.get("role", "unknown"),
            "url": info.get("url", f"http://localhost:{info.get('port', 0)}"),
            "port": info.get("port", 0),
            "capabilities": info.get("capabilities", []),
            "last_seen": datetime.now().isoformat(),
            "registered_at": info.get("registered_at", datetime.now().isoformat()),
            "status": "active"
        }
        for cap in info.get("capabilities", []):
            capability_map[cap] = agent_id
        save_registry()
        return jsonify({"result": {"status": "registered", "agent_id": agent_id}})

    elif task == "lookup":
        if len(args) < 1:
            return jsonify({"error": "Missing agent_id"}), 400
        agent_id = args[0]
        agent = agents.get(agent_id)
        if agent:
            agent["last_seen"] = datetime.now().isoformat()
        return jsonify({"result": agent})

    elif task == "find_capability":
        if len(args) < 1:
            return jsonify({"error": "Missing capability"}), 400
        capability = args[0]
        agent_id = capability_map.get(capability)
        if agent_id:
            agent = agents.get(agent_id)
            if agent:
                agent["last_seen"] = datetime.now().isoformat()
                return jsonify({"result": agent})
        return jsonify({"result": None})

    elif task == "list_agents":
        # Return as a clean JSON array
        return jsonify({"result": list(agents.values())})

    elif task == "heartbeat":
        if len(args) < 1:
            return jsonify({"error": "Missing agent_id"}), 400
        agent_id = args[0]
        if agent_id in agents:
            agents[agent_id]["last_seen"] = datetime.now().isoformat()
            save_registry()
            return jsonify({"result": {"status": "heartbeat_received"}})
        return jsonify({"result": {"status": "agent_not_found"}})

    elif task == "deregister":
        if len(args) < 1:
            return jsonify({"error": "Missing agent_id"}), 400
        agent_id = args[0]
        if agent_id in agents:
            del agents[agent_id]
            for cap, aid in list(capability_map.items()):
                if aid == agent_id:
                    del capability_map[cap]
            save_registry()
            return jsonify({"result": {"status": "deregistered"}})
        return jsonify({"result": {"status": "agent_not_found"}})

    else:
        return jsonify({"error": f"Unknown task: {task}"}), 400

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8004, debug=False)
