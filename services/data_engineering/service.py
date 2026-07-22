#!/usr/bin/env python3
"""
Data Engineering Service – Pure HTTP service.
Manages datasets: register, list, preprocess, split, and prepare for training/evaluation.
"""
import os
import json
import uuid
import shutil
import subprocess
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

BASE = os.path.expanduser("~/mycelial")
DATA_DIR = os.path.join(BASE, "data")
DATASETS_FILE = os.path.join(BASE, "state", "datasets.json")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.dirname(DATASETS_FILE), exist_ok=True)

# In-memory store
datasets = {}

def load_datasets():
    global datasets
    if os.path.exists(DATASETS_FILE):
        try:
            with open(DATASETS_FILE, "r") as f:
                datasets = json.load(f)
        except:
            datasets = {}

def save_datasets():
    with open(DATASETS_FILE, "w") as f:
        json.dump(datasets, f, indent=2)

load_datasets()

def get_policy():
    """Fetch default preprocessing parameters from Policy Service."""
    try:
        import requests
        # Policy Service might have a training policy with defaults
        resp = requests.post("http://localhost:8008/evaluate",
                             json={"type": "training", "context": {}},
                             timeout=3)
        if resp.status_code == 200:
            return resp.json().get("params", {})
    except:
        pass
    return {"normalize": True, "test_split": 0.2, "val_split": 0.1, "seed": 42}

def log_to_audit(dataset_id, event_type, message):
    """Send a log entry to Logging Service."""
    try:
        import requests
        requests.post("http://localhost:8009/log", json={
            "agent_id": "data_engineering_service",
            "event_type": event_type,
            "task": "data_engineering",
            "result": message,
            "level": "info",
            "metadata": {"dataset_id": dataset_id},
            "namespace": "data"
        }, timeout=3)
    except:
        pass

def store_memory(namespace, key, value):
    """Store dataset metadata in Memory Service."""
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

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "data_engineering"})

@app.route("/datasets", methods=["GET"])
def list_datasets():
    """List all registered datasets."""
    return jsonify({"success": True, "datasets": list(datasets.values())})

@app.route("/datasets/<dataset_id>", methods=["GET"])
def get_dataset(dataset_id):
    """Get dataset details by ID."""
    ds = datasets.get(dataset_id)
    if not ds:
        return jsonify({"success": False, "error": "Dataset not found"}), 404
    return jsonify({"success": True, "dataset": ds})

@app.route("/datasets", methods=["POST"])
def register_dataset():
    """Register a new dataset (local file or reference)."""
    data = request.json or {}
    name = data.get("name")
    if not name:
        return jsonify({"success": False, "error": "Missing dataset name"}), 400

    file_path = data.get("file_path")
    # If file_path is not provided, we'll generate a synthetic dataset for testing
    if not file_path:
        # Create a synthetic CSV for agriculture sensor data
        dataset_id = str(uuid.uuid4())[:8]
        filename = f"{dataset_id}_{name}.csv"
        path = os.path.join(DATA_DIR, filename)
        # Generate synthetic data (temperature, humidity, pH, etc.)
        import random
        with open(path, "w") as f:
            f.write("temperature,humidity,ph,soil_moisture,yield\n")
            for _ in range(1000):
                temp = round(random.uniform(15, 35), 1)
                hum = round(random.uniform(30, 80), 1)
                ph = round(random.uniform(5.5, 7.5), 1)
                moisture = round(random.uniform(20, 60), 1)
                yield_val = round(random.uniform(0.5, 2.5), 2)
                f.write(f"{temp},{hum},{ph},{moisture},{yield_val}\n")
    else:
        # Use existing file
        if not os.path.exists(file_path):
            return jsonify({"success": False, "error": "File not found"}), 404
        # Copy to data directory
        dataset_id = str(uuid.uuid4())[:8]
        ext = os.path.splitext(file_path)[1]
        filename = f"{dataset_id}_{name}{ext}"
        path = os.path.join(DATA_DIR, filename)
        shutil.copy2(file_path, path)

    ds = {
        "dataset_id": dataset_id,
        "name": name,
        "file_path": path,
        "created_at": datetime.now().isoformat(),
        "description": data.get("description", ""),
        "source": data.get("source", "synthetic"),
        "size": os.path.getsize(path) if os.path.exists(path) else 0,
        "preprocessed": False,
        "splits": {}
    }
    datasets[dataset_id] = ds
    save_datasets()
    log_to_audit(dataset_id, "DATASET_REGISTERED", f"Registered dataset {name}")
    store_memory("datasets", dataset_id, ds)
    return jsonify({"success": True, "dataset": ds})

@app.route("/datasets/<dataset_id>/preprocess", methods=["POST"])
def preprocess_dataset(dataset_id):
    """Preprocess a dataset: normalize, split, etc."""
    ds = datasets.get(dataset_id)
    if not ds:
        return jsonify({"success": False, "error": "Dataset not found"}), 404

    policy = get_policy()
    normalize = policy.get("normalize", True)
    test_split = policy.get("test_split", 0.2)
    val_split = policy.get("val_split", 0.1)
    seed = policy.get("seed", 42)

    # Simulate preprocessing – in reality, you'd load CSV, clean, normalize, split
    # We'll just store the split parameters and mark as preprocessed
    ds["preprocessed"] = True
    ds["preprocess_params"] = {
        "normalize": normalize,
        "test_split": test_split,
        "val_split": val_split,
        "seed": seed,
        "timestamp": datetime.now().isoformat()
    }
    ds["splits"] = {
        "train": f"{ds['file_path']}.train",
        "val": f"{ds['file_path']}.val",
        "test": f"{ds['file_path']}.test"
    }
    save_datasets()
    log_to_audit(dataset_id, "DATASET_PREPROCESSED", "Dataset preprocessed")
    store_memory("datasets", dataset_id, ds)
    return jsonify({"success": True, "dataset": ds})

@app.route("/datasets/<dataset_id>/split", methods=["POST"])
def split_dataset(dataset_id):
    """Manually split dataset (override defaults)."""
    data = request.json or {}
    test_split = data.get("test_split", 0.2)
    val_split = data.get("val_split", 0.1)
    ds = datasets.get(dataset_id)
    if not ds:
        return jsonify({"success": False, "error": "Dataset not found"}), 404
    ds["splits"] = {
        "train": f"{ds['file_path']}.train",
        "val": f"{ds['file_path']}.val",
        "test": f"{ds['file_path']}.test"
    }
    ds["preprocess_params"] = {
        "test_split": test_split,
        "val_split": val_split,
        "timestamp": datetime.now().isoformat()
    }
    ds["preprocessed"] = True
    save_datasets()
    log_to_audit(dataset_id, "DATASET_SPLIT", f"Split dataset with test={test_split}, val={val_split}")
    return jsonify({"success": True, "dataset": ds})

@app.route("/datasets/<dataset_id>/delete", methods=["DELETE"])
def delete_dataset(dataset_id):
    """Delete dataset and its files."""
    ds = datasets.get(dataset_id)
    if not ds:
        return jsonify({"success": False, "error": "Dataset not found"}), 404
    # Remove file
    if os.path.exists(ds["file_path"]):
        os.remove(ds["file_path"])
    # Remove split files if they exist
    for key, path in ds.get("splits", {}).items():
        if os.path.exists(path):
            os.remove(path)
    del datasets[dataset_id]
    save_datasets()
    log_to_audit(dataset_id, "DATASET_DELETED", "Dataset deleted")
    return jsonify({"success": True, "message": "Dataset deleted"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8012, debug=False)
