#!/usr/bin/env python3
"""
Training Service – Pure HTTP service.
Manages training jobs: start, stop, status, logs, and metrics.
Integrates with Policy, Memory, Logging, and Inference services.
"""
import os
import json
import uuid
import time
import subprocess
import threading
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

BASE = os.path.expanduser("~/mycelial")
TRAINING_JOBS_FILE = os.path.join(BASE, "state", "training_jobs.json")
os.makedirs(os.path.dirname(TRAINING_JOBS_FILE), exist_ok=True)

# In-memory job store
jobs = {}

def load_jobs():
    global jobs
    if os.path.exists(TRAINING_JOBS_FILE):
        try:
            with open(TRAINING_JOBS_FILE, "r") as f:
                jobs = json.load(f)
        except:
            jobs = {}

def save_jobs():
    with open(TRAINING_JOBS_FILE, "w") as f:
        json.dump(jobs, f, indent=2)

load_jobs()

def get_policy(service="training"):
    """Fetch training hyperparameters from Policy Service."""
    try:
        import requests
        resp = requests.post("http://localhost:8008/evaluate",
                             json={"type": "training", "context": {}},
                             timeout=3)
        if resp.status_code == 200:
            return resp.json().get("params", {})
    except:
        pass
    return {"epochs": 10, "batch_size": 32, "learning_rate": 0.001}

def log_to_audit(job_id, event_type, message):
    """Send a log entry to Logging Service."""
    try:
        import requests
        requests.post("http://localhost:8009/log", json={
            "agent_id": "training_service",
            "event_type": event_type,
            "task": "training",
            "result": message,
            "level": "info",
            "metadata": {"job_id": job_id},
            "namespace": "training"
        }, timeout=3)
    except:
        pass

def store_memory(namespace, key, value):
    """Store training metrics in Memory Service."""
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

def run_training_job(job_id, config):
    """Background thread that executes the training script."""
    # Simulate training (replace with actual PyTorch training later)
    epochs = config.get("epochs", 10)
    batch_size = config.get("batch_size", 32)
    learning_rate = config.get("learning_rate", 0.001)
    dataset = config.get("dataset", "agriculture")
    model_type = config.get("model", "lstm")

    job = jobs.get(job_id)
    if not job:
        return

    job["status"] = "running"
    job["started_at"] = datetime.now().isoformat()
    save_jobs()

    log_to_audit(job_id, "TRAINING_START", f"Started training {model_type} on {dataset}")

    # Simulate training steps
    steps = epochs * 5  # 5 batches per epoch
    for step in range(steps):
        if job.get("stop_requested", False):
            job["status"] = "stopped"
            job["stopped_at"] = datetime.now().isoformat()
            save_jobs()
            log_to_audit(job_id, "TRAINING_STOP", "Training stopped by user")
            return

        # Simulate loss decreasing
        loss = 1.0 / (step + 1)
        accuracy = min(0.95, 0.5 + step / steps * 0.45)
        metrics = {"loss": loss, "accuracy": accuracy, "step": step, "epoch": step // 5 + 1}
        job["metrics"] = metrics
        job["progress"] = int((step + 1) / steps * 100)
        save_jobs()

        # Store metrics in Memory (every 10 steps)
        if step % 10 == 0:
            store_memory("training_metrics", f"{job_id}_step_{step}", metrics)

        time.sleep(0.5)  # Simulate compute time

    # Training complete
    job["status"] = "completed"
    job["completed_at"] = datetime.now().isoformat()
    job["progress"] = 100
    job["final_metrics"] = {
        "loss": 0.05,
        "accuracy": 0.92,
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate
    }
    save_jobs()

    log_to_audit(job_id, "TRAINING_COMPLETE", f"Training completed with accuracy {job['final_metrics']['accuracy']:.2f}")
    store_memory("training_results", job_id, job["final_metrics"])

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "training"})

@app.route("/train", methods=["POST"])
def start_training():
    """Start a new training job."""
    data = request.json or {}
    dataset = data.get("dataset", "agriculture")
    model_type = data.get("model", "lstm")
    config = data.get("config", {})

    # Merge with policy defaults
    policy_params = get_policy()
    full_config = {**policy_params, **config}
    full_config["dataset"] = dataset
    full_config["model"] = model_type

    job_id = str(uuid.uuid4())[:8]
    job = {
        "job_id": job_id,
        "dataset": dataset,
        "model": model_type,
        "config": full_config,
        "status": "queued",
        "created_at": datetime.now().isoformat(),
        "progress": 0,
        "metrics": {},
        "final_metrics": {},
        "stop_requested": False
    }
    jobs[job_id] = job
    save_jobs()

    log_to_audit(job_id, "TRAINING_QUEUED", f"Training job queued for {model_type} on {dataset}")

    # Start training in background thread
    thread = threading.Thread(target=run_training_job, args=(job_id, full_config))
    thread.daemon = True
    thread.start()

    return jsonify({"success": True, "job_id": job_id, "status": "queued"})

@app.route("/jobs", methods=["GET"])
def list_jobs():
    """List all training jobs."""
    return jsonify({"success": True, "jobs": list(jobs.values())})

@app.route("/jobs/<job_id>", methods=["GET"])
def get_job_status(job_id):
    """Get status of a specific job."""
    job = jobs.get(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found"}), 404
    return jsonify({"success": True, "job": job})

@app.route("/jobs/<job_id>/stop", methods=["POST"])
def stop_job(job_id):
    """Request to stop a running job."""
    job = jobs.get(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found"}), 404
    if job["status"] not in ["queued", "running"]:
        return jsonify({"success": False, "error": "Job cannot be stopped"}), 400
    job["stop_requested"] = True
    save_jobs()
    log_to_audit(job_id, "TRAINING_STOP_REQUESTED", "Stop requested by user")
    return jsonify({"success": True, "message": "Stop requested"})

@app.route("/jobs/<job_id>/logs", methods=["GET"])
def get_job_logs(job_id):
    """Get training logs (simulated)."""
    job = jobs.get(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found"}), 404
    # Simulate log retrieval from Memory Service or local file
    return jsonify({
        "success": True,
        "logs": [
            f"Job {job_id} started at {job.get('started_at', 'unknown')}",
            f"Current status: {job.get('status', 'unknown')}",
            f"Progress: {job.get('progress', 0)}%",
            f"Latest metrics: {job.get('metrics', {})}"
        ]
    })

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8010, debug=False)
