#!/usr/bin/env python3
"""
Model Service – Pure HTTP service.
Manages model metadata and selects models based on requirements.
"""
import os
import json
from flask import Flask, request, jsonify

app = Flask(__name__)

# Model metadata with tags
MODELS = {
    "qwen2.5:1.5b": {
        "name": "qwen2.5:1.5b",
        "active": True,
        "speed": "fast",
        "quality": "medium",
        "size": "1.5B",
        "domain": "general",
        "specialization": "general"
    },
    "deepseek-coder:1.3b": {
        "name": "deepseek-coder:1.3b",
        "active": True,
        "speed": "fast",
        "quality": "medium",
        "size": "1.3B",
        "domain": "code",
        "specialization": "code"
    },
    "qwen2.5:7b": {
        "name": "qwen2.5:7b",
        "active": False,  # not active by default (large)
        "speed": "slow",
        "quality": "high",
        "size": "7B",
        "domain": "general",
        "specialization": "general"
    },
    "llama3.2:3b": {
        "name": "llama3.2:3b",
        "active": True,
        "speed": "medium",
        "quality": "high",
        "size": "3B",
        "domain": "general",
        "specialization": "general"
    }
}

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "model_service"})

@app.route("/models", methods=["GET"])
def list_models():
    return jsonify(MODELS)

@app.route("/models/active", methods=["GET"])
def get_active_models():
    active = {k: v for k, v in MODELS.items() if v.get("active", False)}
    return jsonify(active)

@app.route("/models/select", methods=["POST"])
def select_model():
    """
    Select a model based on requirements.
    Requirements: e.g., {"speed": "fast", "quality": "high", "domain": "code"}
    """
    data = request.json or {}
    requirements = data.get("requirements", {})
    # If no requirements, return first active model
    if not requirements:
        for name, meta in MODELS.items():
            if meta.get("active", False):
                return jsonify({"success": True, "model": name})
        return jsonify({"success": False, "error": "No active models"}), 404

    # Score each active model against requirements
    best_model = None
    best_score = -1
    for name, meta in MODELS.items():
        if not meta.get("active", False):
            continue
        score = 0
        for key, value in requirements.items():
            if key in meta:
                if meta[key] == value:
                    score += 1
                # For numeric/string matching we keep simple equality; could be extended
        # Weighted: speed and quality are important
        if score > best_score:
            best_score = score
            best_model = name

    if best_model:
        return jsonify({"success": True, "model": best_model, "score": best_score})
    else:
        # Fallback to any active model
        for name, meta in MODELS.items():
            if meta.get("active", False):
                return jsonify({"success": True, "model": name})
        return jsonify({"success": False, "error": "No active models found"}), 404

@app.route("/models/update", methods=["POST"])
def update_model():
    data = request.json or {}
    model_id = data.get("id")
    if not model_id or model_id not in MODELS:
        return jsonify({"success": False, "error": "Model not found"}), 404
    for key, value in data.items():
        if key != "id" and key in MODELS[model_id]:
            MODELS[model_id][key] = value
    return jsonify({"success": True, "model": MODELS[model_id]})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8006, debug=False)
