#!/usr/bin/env python3
"""
Evaluation Service – Pure HTTP service.
Evaluates models against test datasets and stores metrics.
"""
import os
import sys
import glob
import subprocess
import json
import uuid
import time
import threading
import subprocess
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

BASE = os.path.expanduser("~/mycelial")
EVAL_JOBS_FILE = os.path.join(BASE, "state", "eval_jobs.json")
os.makedirs(os.path.dirname(EVAL_JOBS_FILE), exist_ok=True)

# In-memory store
eval_jobs = {}

def load_jobs():
    global eval_jobs
    if os.path.exists(EVAL_JOBS_FILE):
        try:
            with open(EVAL_JOBS_FILE, "r") as f:
                eval_jobs = json.load(f)
        except:
            eval_jobs = {}

def save_jobs():
    with open(EVAL_JOBS_FILE, "w") as f:
        json.dump(eval_jobs, f, indent=2)

load_jobs()

def get_policy():
    """Fetch evaluation metrics from Policy Service."""
    try:
        import requests
        resp = requests.post("http://localhost:8008/evaluate",
                             json={"type": "evaluation", "context": {}},
                             timeout=3)
        if resp.status_code == 200:
            return resp.json().get("metrics", [])
    except:
        pass
    return ["accuracy", "f1", "precision", "recall"]

def log_to_audit(eval_id, event_type, message):
    """Send a log entry to Logging Service."""
    try:
        import requests
        requests.post("http://localhost:8009/log", json={
            "agent_id": "evaluation_service",
            "event_type": event_type,
            "task": "evaluation",
            "result": message,
            "level": "info",
            "metadata": {"eval_id": eval_id},
            "namespace": "evaluation"
        }, timeout=3)
    except:
        pass

def store_memory(namespace, key, value):
    """Store evaluation results in Memory Service."""
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

def _serving_checkpoint(core):
    """What is serving now, from core's own pointer. Not guessed."""
    try:
        return json.load(open(os.path.join(core, "models",
                                           "serving.json")))["checkpoint"]
    except Exception:
        return None


def run_evaluation(eval_id, config):
    """Background thread that simulates evaluation."""
    job = eval_jobs.get(eval_id)
    if not job:
        return

    model_id = config.get("model_id", "unknown")
    test_dataset = config.get("test_dataset", "agriculture_test")

    job["status"] = "running"
    job["started_at"] = datetime.now().isoformat()
    save_jobs()

    log_to_audit(eval_id, "EVAL_START", f"Evaluating model {model_id} on {test_dataset}")

    # REAL EVALUATION, DISPATCHED TO MYCELIAL-CORE.
    #
    # What was here counted to twenty with a sleep, interpolated four metrics
    # upward, and finished on a hardcoded accuracy of 0.92 for ANY model on ANY
    # dataset. A number that is the same whatever you evaluate is not a
    # measurement; it is a decoration that survives every regression.
    #
    # The real scorer runs the checkpoint against HELD-OUT harness events the
    # model has not seen, and its primary metric is BALANCED accuracy. Plain
    # accuracy is the trap on this data: it is 87% denials, so a model that
    # denies everything scores 0.875 and looks respectable. The first real
    # checkpoint did exactly that - 0.875 decision accuracy, 0.500 balanced,
    # allowed-recall ZERO.
    core = os.environ.get("MYCELIAL_CORE", os.path.expanduser("~/mycelial-core"))
    scorer = os.path.join(core, "training", "eval_checkpoint.py")
    checkpoint = config.get("checkpoint") or _serving_checkpoint(core)
    if not os.path.exists(scorer) or not checkpoint:
        job["status"] = "failed"
        job["error"] = (f"no scorer at {scorer} or no checkpoint given. "
                        f"Evaluation is not simulated here any more, so a "
                        f"missing model is a failed job rather than a 0.92.")
        job["completed_at"] = datetime.now().isoformat()
        save_jobs()
        log_to_audit(eval_id, "EVAL_FAILED", job["error"][:200])
        return

    records = os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "datasets", "security_eval")
    cmd = [sys.executable, scorer, "--checkpoint", checkpoint,
           "--records", records]
    if config.get("promote"):
        cmd.append("--promote")
    job["command"] = " ".join(cmd)
    job["progress"] = 10
    save_jobs()
    try:
        proc = subprocess.run(cmd, cwd=core, capture_output=True, text=True,
                              timeout=1800)
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)[:300]
        job["completed_at"] = datetime.now().isoformat()
        save_jobs()
        return

    job["stdout_tail"] = (proc.stdout or "")[-1500:]
    name = os.path.basename(checkpoint.rstrip("/"))
    try:
        result = json.load(open(os.path.join(core, "runs", f"eval-{name}.json")))
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = f"scorer wrote no result for {name}: {exc}"
        job["completed_at"] = datetime.now().isoformat()
        save_jobs()
        return

    new = result.get("new") or {}
    job["progress"] = 100
    job["verdict"] = result.get("verdict")
    job["verdict_why"] = result.get("why")
    job["metrics"] = {
        "decision_accuracy": new.get("decision_accuracy"),
        "register_accuracy": new.get("register_accuracy"),
        "balanced_accuracy": new.get("balanced_accuracy"),
        "per_class_recall": new.get("per_class_recall"),
        "heldout": new.get("heldout"),
    }
    save_jobs()

    # Final metrics (simulated)
    final_metrics = {
        "decision_accuracy": new.get("decision_accuracy"),
        "register_accuracy": new.get("register_accuracy"),
        # PRIMARY. See the note above - plain accuracy rewards the base rate.
        "balanced_accuracy": new.get("balanced_accuracy"),
        "per_class_recall": new.get("per_class_recall"),
        "verdict": result.get("verdict"),
        "promoted": result.get("verdict") in ("IMPROVED", "FIRST")
                    and bool(config.get("promote")),
        "test_dataset": "held-out harness events",
        "model_id": model_id,
        "timestamp": datetime.now().isoformat()
    }
    job["status"] = "completed"
    job["completed_at"] = datetime.now().isoformat()
    job["progress"] = 100
    job["final_metrics"] = final_metrics
    save_jobs()

    log_to_audit(eval_id, "EVAL_COMPLETE", f"Evaluation completed: accuracy {final_metrics['accuracy']:.2f}")
    store_memory("evaluation_results", eval_id, final_metrics)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "evaluation"})

@app.route("/evaluate", methods=["POST"])
def start_evaluation():
    """Start a new evaluation job."""
    data = request.json or {}
    model_id = data.get("model_id")
    if not model_id:
        return jsonify({"success": False, "error": "Missing model_id"}), 400

    test_dataset = data.get("test_dataset", "agriculture_test")
    config = {"model_id": model_id, "test_dataset": test_dataset}

    eval_id = str(uuid.uuid4())[:8]
    job = {
        "eval_id": eval_id,
        "model_id": model_id,
        "test_dataset": test_dataset,
        "status": "queued",
        "created_at": datetime.now().isoformat(),
        "progress": 0,
        "metrics": {},
        "final_metrics": {},
        "stop_requested": False
    }
    eval_jobs[eval_id] = job
    save_jobs()

    log_to_audit(eval_id, "EVAL_QUEUED", f"Evaluation queued for model {model_id}")

    thread = threading.Thread(target=run_evaluation, args=(eval_id, config))
    thread.daemon = True
    thread.start()

    return jsonify({"success": True, "eval_id": eval_id, "status": "queued"})

@app.route("/jobs", methods=["GET"])
def list_jobs():
    """List all evaluation jobs."""
    return jsonify({"success": True, "jobs": list(eval_jobs.values())})

@app.route("/jobs/<eval_id>", methods=["GET"])
def get_job_status(eval_id):
    """Get status of a specific evaluation job."""
    job = eval_jobs.get(eval_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found"}), 404
    return jsonify({"success": True, "job": job})

@app.route("/jobs/<eval_id>/stop", methods=["POST"])
def stop_job(eval_id):
    """Request to stop a running evaluation."""
    job = eval_jobs.get(eval_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found"}), 404
    if job["status"] not in ["queued", "running"]:
        return jsonify({"success": False, "error": "Job cannot be stopped"}), 400
    job["stop_requested"] = True
    save_jobs()
    log_to_audit(eval_id, "EVAL_STOP_REQUESTED", "Stop requested by user")
    return jsonify({"success": True, "message": "Stop requested"})

@app.route("/metrics", methods=["GET"])
def get_metrics():
    """List all evaluation metrics from Memory Service."""
    try:
        import requests
        # Query Memory Service for evaluation results
        resp = requests.get("http://localhost:8007/retrieve?namespace=evaluation_results", timeout=3)
        if resp.status_code == 200:
            return jsonify({"success": True, "metrics": resp.json().get("entry", {})})
    except:
        pass
    return jsonify({"success": False, "error": "Unable to fetch metrics"}), 500

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8011, debug=False)
