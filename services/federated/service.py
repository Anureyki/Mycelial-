#!/usr/bin/env python3
"""
Federated Learning Service - Platform Service (port 8017).

Owns the lifecycle of the Flower server: start/stop, round progress, connected
clients, and aggregated per-round metrics.

Why this is its own service rather than a job type in the Training Service
(8010): that service is a batch job runner (queued -> running -> completed) that
shells out to a training script and waits for it to finish. Federated learning
is a long-lived gRPC listener whose state is *rounds and client availability*,
and it needs its own gRPC port regardless. Bolting round and aggregation config
onto a schema built for local training would fit badly in both directions.

Replaces the retired hooks/fl_orchestrator.sh (start/stop/status) and
hooks/fl_train.sh, both of which cd'd into ~/AgTechAI - a directory that stopped
existing when the project was renamed to mycelial.
"""
import json
import os
import signal
import subprocess
import sys
from datetime import datetime

from flask import Flask, request, jsonify

BASE = os.path.expanduser("~/mycelial")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE, "state", "federated")
ROUNDS_FILE = os.path.join(STATE_DIR, "rounds.json")
HISTORY_FILE = os.path.join(STATE_DIR, "history.json")
PID_FILE = os.path.join(STATE_DIR, "server.pid")
LOG_FILE = os.path.join(BASE, "logs", "federated_server.log")

GRPC_ADDRESS = os.getenv("FL_GRPC_ADDRESS", "0.0.0.0:9092")

app = Flask(__name__)

# The running Flower server, if any. A subprocess (not a thread) so that /stop
# can actually terminate it - Flower's start_server blocks until all rounds end.
_server = {"process": None, "started_at": None, "rounds": None, "min_clients": None}


def _is_running():
    proc = _server["process"]
    return proc is not None and proc.poll() is None


def _read_rounds():
    try:
        with open(ROUNDS_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"total_rounds": 0, "completed": 0, "rounds": []}


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "federated", "running": _is_running()})


@app.route("/start", methods=["POST"])
def start():
    if _is_running():
        return jsonify({"success": False, "error": "FL server already running",
                        "pid": _server["process"].pid}), 409

    data = request.get_json(silent=True) or {}
    rounds = int(data.get("rounds", 5))
    min_clients = int(data.get("min_clients", 2))
    if rounds < 1 or min_clients < 1:
        return jsonify({"success": False, "error": "rounds and min_clients must be >= 1"}), 400

    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    # Stale results from a previous run would otherwise look like this run's.
    for path in (ROUNDS_FILE, HISTORY_FILE):
        if os.path.exists(path):
            os.remove(path)

    log = open(LOG_FILE, "a")
    log.write(f"\n=== FL server start {datetime.now().isoformat()} "
              f"({rounds} rounds, min {min_clients} clients) ===\n")
    log.flush()

    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "fl_server.py"),
         "--rounds", str(rounds),
         "--min-clients", str(min_clients),
         "--address", GRPC_ADDRESS],
        stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,  # so /stop kills the whole group, not just the parent
    )

    _server.update({"process": proc, "started_at": datetime.now().isoformat(),
                    "rounds": rounds, "min_clients": min_clients})
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))

    return jsonify({"success": True, "pid": proc.pid, "grpc_address": GRPC_ADDRESS,
                    "rounds": rounds, "min_clients": min_clients,
                    "message": f"Waiting for {min_clients} client(s) to connect"})


@app.route("/stop", methods=["POST"])
def stop():
    if not _is_running():
        return jsonify({"success": False, "error": "FL server is not running"}), 404

    proc = _server["process"]
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=5)
    except ProcessLookupError:
        pass  # already gone

    _server["process"] = None
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    return jsonify({"success": True, "message": "FL server stopped"})


@app.route("/status", methods=["GET"])
def status():
    state = _read_rounds()
    running = _is_running()
    proc = _server["process"]

    payload = {
        "running": running,
        "grpc_address": GRPC_ADDRESS if running else None,
        "pid": proc.pid if running else None,
        "started_at": _server["started_at"],
        "min_clients": _server["min_clients"],
        "round": state.get("completed", 0),
        "of": state.get("total_rounds", _server["rounds"] or 0),
    }
    if not running and proc is not None:
        # Distinguish "finished its rounds" from "died". A crashed server that
        # reports 'idle' is exactly how the old shell orchestrator hid failures.
        payload["last_exit_code"] = proc.returncode
        payload["outcome"] = "completed" if proc.returncode == 0 else "failed"
        payload["log"] = LOG_FILE
    return jsonify(payload)


@app.route("/clients", methods=["GET"])
def clients():
    """Clients that took part in the most recent round.

    Flower does not expose a live roster through start_server, so this reports
    participation per round rather than a real-time connection list.
    """
    state = _read_rounds()
    rounds = state.get("rounds", [])
    latest = rounds[-1] if rounds else None
    return jsonify({
        "latest_round": latest.get("round") if latest else None,
        "fit_clients": (latest.get("fit") or {}).get("clients") if latest else 0,
        "evaluate_clients": (latest.get("evaluate") or {}).get("clients") if latest else 0,
        "min_clients": _server["min_clients"],
    })


@app.route("/rounds", methods=["GET"])
def all_rounds():
    return jsonify(_read_rounds())


@app.route("/rounds/<int:n>/metrics", methods=["GET"])
def round_metrics(n):
    state = _read_rounds()
    entry = next((r for r in state.get("rounds", []) if r["round"] == n), None)
    if entry is None:
        return jsonify({"error": f"No metrics for round {n}"}), 404
    return jsonify(entry)


@app.route("/history", methods=["GET"])
def history():
    """Final aggregated history, written once all rounds complete."""
    try:
        with open(HISTORY_FILE) as f:
            return jsonify(json.load(f))
    except (OSError, json.JSONDecodeError):
        return jsonify({"error": "No completed run yet"}), 404


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8017, debug=False)
