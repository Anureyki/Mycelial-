#!/usr/bin/env python3
"""
Service Manager – Pure HTTP service.
Owns process execution: start, stop, restart, monitor, crash recovery.
Now supports entry_point for module-based agent startup.
"""
import os
import json
import time
import re
import subprocess
import threading
import requests
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

VENV_PY = os.path.join(os.path.expanduser("~/mycelial"), "venv", "bin", "python3")
BASE = os.path.expanduser("~/mycelial")
PROCESS_FILE = os.path.join(BASE, "state", "processes.json")
CONFIG_DIR = os.path.join(BASE, "config", "agent_configs")
os.makedirs(os.path.dirname(PROCESS_FILE), exist_ok=True)

processes = {}
MONITOR_INTERVAL = 30  # seconds

# Core agent IDs (only these should be started)
CORE_AGENTS = {"boss_agent", "coding_agent", "hermes", "maintenance_agent", "anansi"}

def load_processes():
    global processes
    if os.path.exists(PROCESS_FILE):
        try:
            with open(PROCESS_FILE, "r") as f:
                processes = json.load(f)
        except:
            processes = {}

def save_processes():
    with open(PROCESS_FILE, "w") as f:
        json.dump(processes, f, indent=2)

load_processes()

def log_to_audit(agent_id, event_type, message):
    try:
        requests.post("http://localhost:8009/log", json={
            "agent_id": "service_manager",
            "event_type": event_type,
            "task": "process_management",
            "result": message,
            "level": "info",
            "metadata": {"agent_id": agent_id},
            "namespace": "service_manager"
        }, timeout=3)
    except:
        pass

def start_agent_process(agent_id, config_path):
    """Start the agent using entry_point (module) if available, else direct script."""
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        
        port = config.get("port", 9000)
        entry_point = config.get("entry_point")
        
        if entry_point:
            # Use module form: python3 -m agents.boss_agent.boss_agent
            module = entry_point.split(":")[0]
            cmd = f"cd {BASE} && exec {VENV_PY} -u -m {module} >> logs/{agent_id}.log 2>&1"
        else:
            # Fallback to direct script (old behavior)
            agent_file = os.path.join(BASE, "agents", agent_id, f"{agent_id}.py")
            if not os.path.exists(agent_file):
                agent_file = os.path.join(BASE, "agents", f"{agent_id}.py")
            if not os.path.exists(agent_file):
                return {"success": False, "error": f"Agent file {agent_file} not found"}
            cmd = f"cd {BASE} && exec {VENV_PY} -u {agent_file} >> logs/{agent_id}.log 2>&1"
        
        # shell=True runs /bin/sh, which has no `source` - so every start died
        # with "source: not found" while the endpoint returned success:true.
        # stop worked, start silently did not, and a restart therefore just
        # stopped the agent. That is why the stack has been restarted by hand
        # all session instead of through its own supervisor.
        #
        # Run the venv interpreter directly rather than activating it, and use
        # bash explicitly. setsid detaches the child so it survives this service
        # being restarted in turn.
        # `exec` replaces the shell with the agent instead of leaving a bash
        # wrapper alive - a leftover wrapper matches every pgrep for the agent
        # and makes process counts lie, which is how nine bash shells once got
        # reported as nine running services.
        subprocess.Popen(["/bin/bash", "-lc", cmd],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)

        processes[agent_id] = {
            "agent_id": agent_id,
            "status": "starting",
            "started_at": datetime.now().isoformat(),
            "config": config,
            "port": port,
            "last_health_check": None,
            "restart_count": processes.get(agent_id, {}).get("restart_count", 0) + 1
        }
        save_processes()
        log_to_audit(agent_id, "PROCESS_STARTED", f"Process start initiated for {agent_id}")
        return {"success": True, "status": "starting"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def stop_agent_process(agent_id):
    """Stop the agent process (uses pkill)."""
    try:
        # Agents are launched in module form ("python3 -m agents.grow_agent.grow_agent")
        # by both start_all.sh and start_agent_process below. The old pattern
        # "agents/{id}.py" only matched the direct-script form, which nothing
        # uses - so stop/restart silently matched nothing and reported success.
        # Match either form, and anchor on the agent id so a partial name can't
        # take down a different agent.
        pattern = rf"agents[./]{re.escape(agent_id)}[./]"
        result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
        if result.returncode == 0:
            pids = result.stdout.strip().split()
            for pid in pids:
                subprocess.run(["kill", "-9", pid], capture_output=True)
        processes[agent_id] = {
            "status": "stopped",
            "stopped_at": datetime.now().isoformat(),
            "port": processes.get(agent_id, {}).get("port"),
            "config": processes.get(agent_id, {}).get("config")
        }
        save_processes()
        log_to_audit(agent_id, "PROCESS_STOPPED", f"Process stopped for {agent_id}")
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

def check_agent_health(agent_id):
    info = processes.get(agent_id)
    if not info or info.get("status") != "running":
        return False
    port = info.get("port")
    if not port:
        return False
    try:
        resp = requests.get(f"http://localhost:{port}/health", timeout=3)
        return resp.status_code == 200
    except:
        return False

def monitor_agents():
    """Background thread: periodically check and restart dead agents."""
    while True:
        time.sleep(MONITOR_INTERVAL)
        for agent_id, info in list(processes.items()):
            if info.get("status") != "running":
                continue
            if not check_agent_health(agent_id):
                log_to_audit(agent_id, "PROCESS_DOWN", f"Agent {agent_id} not responding, restarting...")
                stop_result = stop_agent_process(agent_id)
                if not stop_result.get("success"):
                    continue
                config_path = os.path.join(CONFIG_DIR, f"{agent_id}.json")
                if os.path.exists(config_path):
                    start_agent_process(agent_id, config_path)
                else:
                    log_to_audit(agent_id, "RESTART_FAILED", f"Config not found for {agent_id}")

def reconcile_with_configs():
    """Read configs from CONFIG_DIR and start any core agents not running."""
    for filename in os.listdir(CONFIG_DIR):
        if not filename.endswith(".json"):
            continue
        agent_id = filename.replace(".json", "")
        # Only start core agents
        if agent_id not in CORE_AGENTS:
            continue
        if agent_id not in processes or processes[agent_id].get("status") != "running":
            config_path = os.path.join(CONFIG_DIR, filename)
            start_agent_process(agent_id, config_path)

# Start monitor thread
monitor_thread = threading.Thread(target=monitor_agents, daemon=True)
monitor_thread.start()

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "service_manager"})

@app.route("/start", methods=["POST"])
def start_service():
    data = request.json or {}
    agent_id = data.get("agent_id")
    config_path = data.get("config_path")
    if not agent_id:
        return jsonify({"success": False, "error": "Missing agent_id"}), 400
    if not config_path or not os.path.exists(config_path):
        return jsonify({"success": False, "error": "Invalid config_path"}), 400
    result = start_agent_process(agent_id, config_path)
    return jsonify(result)

@app.route("/stop", methods=["POST"])
def stop_service():
    data = request.json or {}
    agent_id = data.get("agent_id")
    if not agent_id:
        return jsonify({"success": False, "error": "Missing agent_id"}), 400
    result = stop_agent_process(agent_id)
    return jsonify(result)

@app.route("/restart", methods=["POST"])
def restart_service():
    data = request.json or {}
    agent_id = data.get("agent_id")
    if not agent_id:
        return jsonify({"success": False, "error": "Missing agent_id"}), 400
    stop_result = stop_agent_process(agent_id)
    if not stop_result.get("success"):
        return jsonify({"success": False, "error": "Stop failed", "detail": stop_result})
    config_path = os.path.join(CONFIG_DIR, f"{agent_id}.json")
    if not os.path.exists(config_path):
        return jsonify({"success": False, "error": "Config not found"}), 404
    start_result = start_agent_process(agent_id, config_path)
    return jsonify(start_result)

@app.route("/restart_all", methods=["POST"])
def restart_all():
    """Stop all agents and restart them from configs (only core agents)."""
    for agent_id in list(processes.keys()):
        if agent_id in CORE_AGENTS:
            stop_agent_process(agent_id)
    time.sleep(2)
    reconcile_with_configs()
    return jsonify({"success": True, "message": "All core agents restarted"})

@app.route("/reconcile", methods=["POST"])
def reconcile():
    """Start any core agents that have configs but are not running."""
    reconcile_with_configs()
    return jsonify({"success": True, "message": "Reconciliation complete"})

@app.route("/status", methods=["GET"])
def status():
    return jsonify({"success": True, "processes": processes})

@app.route("/monitor/interval", methods=["POST"])
def set_interval():
    global MONITOR_INTERVAL
    data = request.json or {}
    interval = data.get("interval", 30)
    if interval < 5:
        return jsonify({"success": False, "error": "Interval must be at least 5 seconds"}), 400
    MONITOR_INTERVAL = interval
    return jsonify({"success": True, "interval": MONITOR_INTERVAL})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8014, debug=False)
