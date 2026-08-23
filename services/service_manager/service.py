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

# The only agents this service will ever start, stop or restart.
#
# It used to supervise whatever it found in state/processes.json, which had
# accumulated on-demand agents from earlier runs - department heads that are
# meant to wake when called, not at boot. Nothing ever cleared them, so the
# monitor restarted them every 30 seconds forever: ag_agent reached 484
# restarts, quantum_agent 485, legal_agent 485. Each cycle spawned a process,
# failed its health check, and spawned another.
#
# On-demand agents are deliberately absent. An agent not in this set is not
# broken when it is down - it is off, which is its normal state.
CORE_AGENTS = {"anansi", "boss_agent", "coding_agent",
               "maintenance_agent", "security_agent"}

# A restart that has not worked three times in ten minutes is not going to work
# the fourth time. Past this the service stops trying and says so, because a
# supervisor that retries forever converts one broken agent into a machine that
# cannot be diagnosed - the logs fill with its own restarts.
MAX_RESTARTS = 3
RESTART_WINDOW = 600        # seconds
HEALTH_WAIT = 20            # seconds to wait for a started agent to serve

_restart_log = {}           # agent_id -> [unix timestamps]


def core_only(agent_id):
    """Reject anything outside the supervised set."""
    if agent_id in CORE_AGENTS:
        return None
    return {"success": False, "error": f"{agent_id} is not a core agent",
            "detail": "This service supervises only " + ", ".join(sorted(CORE_AGENTS))
                      + ". On-demand agents are started when they are needed."}


def restart_budget(agent_id):
    """Whether this agent has any restart attempts left in the window."""
    now = time.time()
    hits = [t for t in _restart_log.get(agent_id, []) if now - t < RESTART_WINDOW]
    _restart_log[agent_id] = hits
    return len(hits) < MAX_RESTARTS, len(hits)

def load_processes():
    """Load process bookkeeping, dropping anything outside the supervised set.

    state/processes.json had accumulated on-demand agents from earlier runs and
    nothing ever removed them, so every boot inherited a list of things to
    restart forever. Entries outside CORE_AGENTS are dropped on load - this
    service does not track what it does not supervise."""
    global processes
    if os.path.exists(PROCESS_FILE):
        try:
            with open(PROCESS_FILE, "r") as f:
                loaded = json.load(f)
        except Exception:
            loaded = {}
        dropped = [k for k in loaded if k not in CORE_AGENTS]
        processes = {k: v for k, v in loaded.items() if k in CORE_AGENTS}
        if dropped:
            save_processes()
            print(f"[service_manager] dropped {len(dropped)} non-core entries: "
                  f"{', '.join(sorted(dropped))}", flush=True)

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
    refused = core_only(agent_id)
    if refused:
        return refused
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

        # Wait for the port to actually answer. "success" used to mean the
        # Popen call did not raise, which stayed true while the process died
        # immediately - the caller believed the agent was up and moved on.
        # Exit status is the weakest evidence available; the new pid serving
        # the port is the claim worth making.
        serving = False
        deadline = time.time() + HEALTH_WAIT
        while time.time() < deadline:
            time.sleep(1)
            try:
                if requests.get(f"http://localhost:{port}/health", timeout=2).status_code == 200:
                    serving = True
                    break
            except Exception:
                continue
        processes[agent_id]["status"] = "running" if serving else "failed"
        processes[agent_id]["last_health_check"] = datetime.now().isoformat()
        save_processes()
        if serving:
            log_to_audit(agent_id, "PROCESS_STARTED", f"{agent_id} is serving port {port}")
            return {"success": True, "status": "running", "port": port}
        log_to_audit(agent_id, "START_FAILED",
                     f"{agent_id} did not serve port {port} within {HEALTH_WAIT}s")
        return {"success": False, "status": "failed", "port": port,
                "error": f"started but did not serve port {port} within {HEALTH_WAIT}s",
                "hint": f"tail logs/{agent_id}.log"}
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

def heal_core_agents(agent_ids=None):
    """Check the core agents and restart only the ones that are actually down.

    This is called ON DEMAND. There is no background loop.

    The previous design ran this every 30 seconds from a daemon thread started
    at import, so simply booting the service committed the machine to restarting
    things forever - including agents that were never meant to run at boot. It
    also decided what to check from its own `processes` bookkeeping, so an agent
    it had lost track of was never checked, while one it wrongly believed was
    running got restarted on a loop.

    Health is read from the port, which is the ground truth, not from what this
    service remembers."""
    report = {}
    for agent_id in sorted(agent_ids or CORE_AGENTS):
        if agent_id not in CORE_AGENTS:
            report[agent_id] = {"action": "skipped", "reason": "not a core agent"}
            continue
        config_path = os.path.join(CONFIG_DIR, f"{agent_id}.json")
        if not os.path.exists(config_path):
            report[agent_id] = {"action": "skipped", "reason": "no config"}
            continue
        try:
            with open(config_path) as f:
                port = json.load(f).get("port")
        except Exception as e:
            report[agent_id] = {"action": "skipped", "reason": f"unreadable config: {e}"}
            continue

        alive = False
        try:
            alive = requests.get(f"http://localhost:{port}/health",
                                 timeout=3).status_code == 200
        except Exception:
            alive = False
        if alive:
            report[agent_id] = {"action": "none", "status": "healthy", "port": port}
            continue

        allowed, used = restart_budget(agent_id)
        if not allowed:
            report[agent_id] = {"action": "gave_up", "status": "down", "port": port,
                                "reason": f"{used} restarts already in the last "
                                          f"{RESTART_WINDOW // 60} minutes",
                                "hint": f"tail logs/{agent_id}.log"}
            log_to_audit(agent_id, "RESTART_ABANDONED",
                         f"{agent_id} down; restart budget exhausted ({used})")
            continue

        _restart_log.setdefault(agent_id, []).append(time.time())
        log_to_audit(agent_id, "PROCESS_DOWN", f"{agent_id} not responding, restarting")
        stop_agent_process(agent_id)
        result = start_agent_process(agent_id, config_path)
        report[agent_id] = {"action": "restarted", "port": port,
                            "status": "running" if result.get("success") else "failed",
                            "detail": result}
    return report


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

# No monitor thread. Supervision happens when someone asks for it, via /heal.
# Starting a background restart loop at import is what turned one misconfigured
# agent into 485 restarts.

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
    refused = core_only(agent_id)
    if refused:
        return jsonify(refused), 400
    result = stop_agent_process(agent_id)
    return jsonify(result)

@app.route("/restart", methods=["POST"])
def restart_service():
    data = request.json or {}
    agent_id = data.get("agent_id")
    if not agent_id:
        return jsonify({"success": False, "error": "Missing agent_id"}), 400
    refused = core_only(agent_id)
    if refused:
        return jsonify(refused), 400
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

@app.route("/heal", methods=["POST"])
def heal():
    """Check the core agents now and restart only the ones that are down.

    Call this when something is actually broken. Nothing runs on a timer.
    Optional body: {"agents": ["boss_agent", ...]} to narrow the check."""
    data = request.json or {}
    wanted = data.get("agents")
    if wanted and not isinstance(wanted, list):
        return jsonify({"success": False, "error": "agents must be a list"}), 400
    report = heal_core_agents(wanted)
    restarted = [a for a, r in report.items() if r.get("action") == "restarted"]
    gave_up = [a for a, r in report.items() if r.get("action") == "gave_up"]
    return jsonify({"success": not gave_up, "supervised": sorted(CORE_AGENTS),
                    "restarted": restarted, "gave_up": gave_up, "report": report})


@app.route("/scope", methods=["GET"])
def scope():
    """What this service will and will not touch."""
    return jsonify({"supervised": sorted(CORE_AGENTS),
                    "background_monitor": False,
                    "policy": "on demand only - POST /heal restarts core agents "
                              "that are down; nothing runs on a timer",
                    "max_restarts": MAX_RESTARTS,
                    "restart_window_seconds": RESTART_WINDOW})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8014, debug=False)
