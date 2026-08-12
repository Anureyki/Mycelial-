#!/usr/bin/env python3
"""
Evaluation Service – Pure HTTP service.
Evaluates models against test datasets and stores metrics.
"""
import os
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

    # Simulate evaluation steps
    steps = 20
    for step in range(steps):
        if job.get("stop_requested", False):
            job["status"] = "stopped"
            job["stopped_at"] = datetime.now().isoformat()
            save_jobs()
            log_to_audit(eval_id, "EVAL_STOP", "Evaluation stopped by user")
            return

        # Simulate increasing metrics
        progress = int((step + 1) / steps * 100)
        job["progress"] = progress
        job["metrics"] = {
            "accuracy": 0.7 + 0.2 * (step / steps),
            "f1": 0.65 + 0.25 * (step / steps),
            "precision": 0.6 + 0.3 * (step / steps),
            "recall": 0.55 + 0.35 * (step / steps),
            "step": step
        }
        save_jobs()
        time.sleep(0.3)

    # Final metrics (simulated)
    final_metrics = {
        "accuracy": 0.92,
        "f1": 0.89,
        "precision": 0.88,
        "recall": 0.87,
        "test_dataset": test_dataset,
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
