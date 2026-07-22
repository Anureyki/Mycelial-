#!/usr/bin/env python3
"""
Environment Audit Skill
Answers: Git repo? Python version? Venv? Model/Inference services? Agents? Disk? Network?
"""
import os
import subprocess
import socket
import requests

def audit():
    result = {}

    # Git repository
    result["git_repo"] = os.path.exists(".git")

    # Python version
    try:
        py_ver = subprocess.run(["python3", "--version"], capture_output=True, text=True, timeout=5)
        result["python_version"] = py_ver.stdout.strip() or py_ver.stderr.strip()
    except:
        result["python_version"] = "unknown"

    # Virtual environment
    result["venv_active"] = bool(os.environ.get("VIRTUAL_ENV"))

    # Hostname
    result["hostname"] = socket.gethostname()

    # Disk usage (root)
    try:
        df = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        result["disk_usage"] = df.stdout.strip()
    except:
        result["disk_usage"] = "unknown"

    # Check if we can reach the Registry Service (port 8004)
    try:
        resp = requests.get("http://localhost:8004/health", timeout=2)
        result["registry_service"] = resp.status_code == 200
    except:
        result["registry_service"] = False

    # Check if we can reach the Model Service (port 8006)
    try:
        resp = requests.get("http://localhost:8006/health", timeout=2)
        result["model_service"] = resp.status_code == 200
    except:
        result["model_service"] = False

    # Check if we can reach the Inference Service (port 8005)
    try:
        resp = requests.get("http://localhost:8005/health", timeout=2)
        result["inference_service"] = resp.status_code == 200
    except:
        result["inference_service"] = False

    # Network connectivity (ping 8.8.8.8)
    try:
        subprocess.run(["ping", "-c", "1", "8.8.8.8"], capture_output=True, timeout=3, check=True)
        result["internet"] = True
    except:
        result["internet"] = False

    return result

if __name__ == "__main__":
    import json
    print(json.dumps(audit(), indent=2))
