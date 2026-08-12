#!/usr/bin/env python3
"""
Policy Service – Pure HTTP service.
Manages policies for retention, routing, training, evaluation, etc.
Now routing uses requirements (not explicit model names).
"""
import os
import json
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

BASE = os.path.expanduser("~/mycelial")
POLICY_FILE = os.path.join(BASE, "config", "policies.json")
os.makedirs(os.path.dirname(POLICY_FILE), exist_ok=True)

_policies = None

def load_policies():
    global _policies
    if _policies is not None:
        return _policies
    if os.path.exists(POLICY_FILE):
        try:
            with open(POLICY_FILE, "r") as f:
                _policies = json.load(f)
                return _policies
        except:
            pass
    # Default policies – routing uses requirements (not model names)
    _policies = {
        "retention": {
            "conversation": {"pin": False, "ttl_days": 30},
            "legal": {"pin": True, "ttl_days": 0},
            "contract": {"pin": True, "ttl_days": 0},
            "model_checkpoint": {"pin": True, "ttl_days": 0},
            "project_memory": {"pin": False, "ttl_days": 365}
        },
        "routing": {
            "general": {"speed": "fast", "quality": "medium"},
            "coding": {"speed": "medium", "quality": "high", "specialization": "code"},
            "legal": {"speed": "slow", "quality": "high", "domain": "legal"}
        },
        "training": {
            "epochs": 10,
            "batch_size": 32,
            "learning_rate": 0.001
        },
        "evaluation": {
            "metrics": ["accuracy", "f1", "precision", "recall"]
        }
    }
    save_policies()
    return _policies

def save_policies():
    global _policies
    with open(POLICY_FILE, "w") as f:
        json.dump(_policies, f, indent=2)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "policy"})

@app.route("/policies", methods=["GET"])
def list_policies():
    policies = load_policies()
    return jsonify({"success": True, "policies": policies})

@app.route("/policies/<category>", methods=["GET"])
def get_policy_category(category):
    policies = load_policies()
    if category in policies:
        return jsonify({"success": True, "category": category, "policy": policies[category]})
    return jsonify({"success": False, "error": "Category not found"}), 404

@app.route("/policies", methods=["POST"])
def create_policy():
    data = request.json or {}
    category = data.get("category")
    if not category:
        return jsonify({"success": False, "error": "Missing category"}), 400
    policy = data.get("policy")
    if policy is None:
        return jsonify({"success": False, "error": "Missing policy"}), 400
    policies = load_policies()
    policies[category] = policy
    save_policies()
    return jsonify({"success": True, "category": category, "policy": policy})

@app.route("/policies/<category>", methods=["PUT"])
def update_policy_category(category):
    data = request.json or {}
    policy = data.get("policy")
    if policy is None:
        return jsonify({"success": False, "error": "Missing policy"}), 400
    policies = load_policies()
    if category not in policies:
        return jsonify({"success": False, "error": "Category not found"}), 404
    policies[category] = policy
    save_policies()
    return jsonify({"success": True, "category": category, "policy": policy})

@app.route("/policies/<category>", methods=["DELETE"])
def delete_policy_category(category):
    policies = load_policies()
    if category not in policies:
        return jsonify({"success": False, "error": "Category not found"}), 404
    del policies[category]
    save_policies()
    return jsonify({"success": True, "message": f"Deleted {category}"})

@app.route("/evaluate", methods=["POST"])
def evaluate():
    """
    Evaluate a decision based on policies.
    For routing, returns requirements (not a model name).
    """
    data = request.json or {}
    decision_type = data.get("type")
    context = data.get("context", {})
    policies = load_policies()

    if decision_type == "pin":
        namespace = context.get("namespace")
        if not namespace:
            return jsonify({"success": False, "error": "Missing namespace"}), 400
        retention = policies.get("retention", {})
        pin_policy = retention.get(namespace, {"pin": False, "ttl_days": 0})
        return jsonify({
            "success": True,
            "decision": "pin" if pin_policy.get("pin") else "no_pin",
            "policy": pin_policy
        })

    elif decision_type == "routing":
        role = context.get("role", "general")
        routing = policies.get("routing", {})
        requirements = routing.get(role, routing.get("general", {}))
        return jsonify({
            "success": True,
            "role": role,
            "requirements": requirements
        })

    elif decision_type == "training":
        training = policies.get("training", {})
        return jsonify({
            "success": True,
            "params": training
        })

    elif decision_type == "evaluation":
        evaluation = policies.get("evaluation", {})
        return jsonify({
            "success": True,
            "metrics": evaluation.get("metrics", [])
        })

    else:
        return jsonify({"success": False, "error": f"Unknown decision type: {decision_type}"}), 400

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8008, debug=False)
