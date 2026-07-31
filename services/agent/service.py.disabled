#!/usr/bin/env python3
"""
Agent Service – Pure definition service.
Manages agent configurations, templates, and lifecycle orchestration.
Includes sync endpoint to reconcile agents folder with configs.
Supports agent scripts in subfolders: agents/<agent_id>/<agent_id>.py
"""
import os
import json
import uuid
import shutil
import time
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

BASE = os.path.expanduser("~/mycelial")
AGENTS_DIR = os.path.join(BASE, "agents")
CONFIG_DIR = os.path.join(BASE, "config", "agent_configs")
TEMPLATE_DIR = os.path.join(BASE, "templates", "agent_templates")
os.makedirs(AGENTS_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(TEMPLATE_DIR, exist_ok=True)

REGISTRY_URL = "http://localhost:8004/execute"
POLICY_URL = "http://localhost:8008/evaluate"
SERVICE_MANAGER_URL = "http://localhost:8014"

def call_registry(task, args):
    try:
        import requests
        resp = requests.post(REGISTRY_URL, json={"task": task, "args": args, "sender": "agent_service"}, timeout=5)
        if resp.status_code == 200:
            return resp.json().get("result")
        return None
    except:
        return None

def call_policy(action, context):
    try:
        import requests
        resp = requests.post(POLICY_URL, json={"type": action, "context": context}, timeout=3)
        if resp.status_code == 200:
            return resp.json()
        return {"allowed": False}
    except:
        return {"allowed": False}

def call_service_manager(endpoint, payload):
    try:
        import requests
        resp = requests.post(f"{SERVICE_MANAGER_URL}/{endpoint}", json=payload, timeout=5)
        if resp.status_code == 200:
            return resp.json()
        return None
    except:
        return None

def log_to_audit(agent_id, event_type, message):
    try:
        import requests
        requests.post("http://localhost:8009/log", json={
            "agent_id": "agent_service",
            "event_type": event_type,
            "task": "agent_management",
            "result": message,
            "level": "info",
            "metadata": {"agent_id": agent_id},
            "namespace": "agent"
        }, timeout=3)
    except:
        pass

# ---------- Agent generation ----------
def generate_agent_script(agent_id, role, capabilities, port, description=""):
    """Generate a Python agent file in agents/<agent_id>/<agent_id>.py"""
    agent_dir = os.path.join(AGENTS_DIR, agent_id)
    os.makedirs(agent_dir, exist_ok=True)
    caps_str = json.dumps(capabilities)
    template = f'''#!/usr/bin/env python3
import sys
import os
import time
from datetime import datetime

# Add project root
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

class {agent_id.title().replace("_","")}Agent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="{agent_id}",
            port={port},
            capabilities={caps_str},
            role="{role}"
        )
        self.log("{agent_id} initialized.")

    def handle_task(self, task, args, sender):
        self.log(f"Task {{task}} from {{sender}}")
        # Add your custom logic here
        return f"Task {{task}} executed by {agent_id}"

if __name__ == "__main__":
    agent = {agent_id.title().replace("_","")}Agent()
    while True:
        time.sleep(60)
        agent.heartbeat()
'''
    agent_file = os.path.join(agent_dir, f"{agent_id}.py")
    with open(agent_file, "w") as f:
        f.write(template)
    os.chmod(agent_file, 0o755)
    return agent_file

def find_agent_files():
    """Recursively find all agent.py files in agents/ folder."""
    agent_files = {}
    for root, dirs, files in os.walk(AGENTS_DIR):
        for file in files:
            if file.endswith(".py") and file != "__init__.py":
                # Assume the agent_id is the directory name or the file name without .py
                agent_id = os.path.basename(root)
                if agent_id == "agents":
                    agent_id = file.replace(".py", "")
                agent_files[agent_id] = os.path.join(root, file)
    return agent_files

# ---------- Config management ----------
def load_config(agent_id):
    config_path = os.path.join(CONFIG_DIR, f"{agent_id}.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return json.load(f)
    return None

def save_config(agent_id, config):
    config_path = os.path.join(CONFIG_DIR, f"{agent_id}.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

def list_configs():
    configs = []
    if os.path.exists(CONFIG_DIR):
        for f in os.listdir(CONFIG_DIR):
            if f.endswith(".json"):
                with open(os.path.join(CONFIG_DIR, f), "r") as fp:
                    configs.append(json.load(fp))
    return configs

# ---------- API endpoints ----------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "agent"})

@app.route("/agent", methods=["POST"])
def create_agent():
    data = request.json or {}
    agent_id = data.get("agent_id")
    if not agent_id:
        return jsonify({"success": False, "error": "Missing agent_id"}), 400

    policy = call_policy("agent_creation", {"agent_id": agent_id, "role": data.get("role")})
    if not policy.get("allowed", False):
        return jsonify({"success": False, "error": "Policy denied"}), 403

    if os.path.exists(os.path.join(CONFIG_DIR, f"{agent_id}.json")):
        return jsonify({"success": False, "error": "Agent already exists"}), 409

    role = data.get("role", "agent")
    capabilities = data.get("capabilities", [])
    port = data.get("port", 9000 + len(os.listdir(CONFIG_DIR)))
    description = data.get("description", "")

    config = {
        "agent_id": agent_id,
        "role": role,
        "port": port,
        "capabilities": capabilities,
        "description": description,
        "created_at": datetime.now().isoformat(),
        "status": "inactive"
    }
    save_config(agent_id, config)

    generate_agent_script(agent_id, role, capabilities, port, description)

    info = {"role": role, "port": port, "capabilities": capabilities, "url": f"http://localhost:{port}", "status": "inactive"}
    call_registry("register", [agent_id, json.dumps(info)])

    start_result = call_service_manager("start", {"agent_id": agent_id, "config_path": os.path.join(CONFIG_DIR, f"{agent_id}.json")})

    log_to_audit(agent_id, "AGENT_CREATED", f"Agent {agent_id} created")
    return jsonify({"success": True, "agent_id": agent_id, "config": config, "service_manager": start_result})

@app.route("/agent", methods=["GET"])
def list_agents():
    agents = call_registry("list_agents", [])
    if agents is None:
        return jsonify({"success": False, "error": "Unable to fetch agents"}), 500
    for agent in agents:
        aid = agent.get("agent_id")
        config = load_config(aid)
        if config:
            agent["config"] = config
    return jsonify({"success": True, "agents": agents})

@app.route("/agent/<agent_id>", methods=["GET"])
def get_agent(agent_id):
    agent = call_registry("lookup", [agent_id])
    if agent is None:
        return jsonify({"success": False, "error": "Agent not found"}), 404
    config = load_config(agent_id)
    if config:
        agent["config"] = config
    return jsonify({"success": True, "agent": agent})

@app.route("/agent/<agent_id>/update", methods=["PUT"])
def update_agent(agent_id):
    data = request.json or {}
    current = call_registry("lookup", [agent_id])
    if not current:
        return jsonify({"success": False, "error": "Agent not found"}), 404

    config = load_config(agent_id)
    if not config:
        return jsonify({"success": False, "error": "Config not found"}), 404

    new_role = data.get("role", config.get("role"))
    new_capabilities = data.get("capabilities", config.get("capabilities"))
    new_port = data.get("port", config.get("port"))

    config["role"] = new_role
    config["capabilities"] = new_capabilities
    config["port"] = new_port
    config["updated_at"] = datetime.now().isoformat()
    save_config(agent_id, config)

    generate_agent_script(agent_id, new_role, new_capabilities, new_port)

    call_registry("deregister", [agent_id])
    new_info = {"role": new_role, "port": new_port, "capabilities": new_capabilities, "url": f"http://localhost:{new_port}", "status": "active"}
    call_registry("register", [agent_id, json.dumps(new_info)])

    call_service_manager("restart", {"agent_id": agent_id})

    log_to_audit(agent_id, "AGENT_UPDATED", f"Agent {agent_id} updated")
    return jsonify({"success": True, "agent_id": agent_id, "config": config})

@app.route("/agent/<agent_id>/delete", methods=["DELETE"])
def delete_agent(agent_id):
    call_service_manager("stop", {"agent_id": agent_id})

    call_registry("deregister", [agent_id])

    config_path = os.path.join(CONFIG_DIR, f"{agent_id}.json")
    if os.path.exists(config_path):
        os.remove(config_path)

    agent_dir = os.path.join(AGENTS_DIR, agent_id)
    if os.path.exists(agent_dir):
        shutil.rmtree(agent_dir)

    log_to_audit(agent_id, "AGENT_DELETED", f"Agent {agent_id} deleted")
    return jsonify({"success": True, "message": f"Agent {agent_id} deleted"})

@app.route("/sync", methods=["POST"])
def sync_agents():
    """Reconcile agent configs with actual agents.
       Query param: create_config=true to auto-generate configs for orphans.
    """
    create_config = request.args.get("create_config", "false").lower() == "true"
    report = {"created": [], "updated": [], "orphaned": [], "errors": []}

    configs = list_configs()
    agent_files = find_agent_files()

    for config in configs:
        agent_id = config.get("agent_id")
        if not agent_id:
            report["errors"].append(f"Config missing agent_id: {config}")
            continue

        # Check if agent file exists in subfolder
        if agent_id not in agent_files:
            generate_agent_script(
                agent_id,
                config.get("role", "agent"),
                config.get("capabilities", []),
                config.get("port", 9000 + len(agent_files))
            )
            report["created"].append(agent_id)

        # Ensure registry registration
        registered = call_registry("lookup", [agent_id])
        if not registered:
            info = {
                "role": config.get("role", "agent"),
                "port": config.get("port", 9000),
                "capabilities": config.get("capabilities", []),
                "url": f"http://localhost:{config.get('port', 9000)}",
                "status": "active"
            }
            call_registry("register", [agent_id, json.dumps(info)])
            report["updated"].append(f"{agent_id} (registered)")

        # Ensure process is running
        start_result = call_service_manager("start", {
            "agent_id": agent_id,
            "config_path": os.path.join(CONFIG_DIR, f"{agent_id}.json")
        })
        if start_result and start_result.get("success"):
            report["updated"].append(f"{agent_id} (started)")
        else:
            report["errors"].append(f"{agent_id}: start failed")

    # Check for orphaned agent files (no config)
    for agent_id, filepath in agent_files.items():
        if not os.path.exists(os.path.join(CONFIG_DIR, f"{agent_id}.json")):
            report["orphaned"].append(agent_id)
            if create_config:
                # Create default config
                config = {
                    "agent_id": agent_id,
                    "role": "agent",
                    "port": 9000 + len(configs) + len(report["created"]),
                    "capabilities": [],
                    "description": "Auto-generated from orphan",
                    "created_at": datetime.now().isoformat()
                }
                save_config(agent_id, config)
                # Generate script
                generate_agent_script(agent_id, config["role"], config["capabilities"], config["port"])
                # Register
                info = {"role": config["role"], "port": config["port"], "capabilities": config["capabilities"], "url": f"http://localhost:{config['port']}", "status": "active"}
                call_registry("register", [agent_id, json.dumps(info)])
                # Start
                call_service_manager("start", {"agent_id": agent_id, "config_path": os.path.join(CONFIG_DIR, f"{agent_id}.json")})
                report["created"].append(f"{agent_id} (config auto-created)")
                # Update report: remove from orphaned list
                report["orphaned"].remove(agent_id)

    return jsonify({"success": True, "report": report})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8013, debug=False)
