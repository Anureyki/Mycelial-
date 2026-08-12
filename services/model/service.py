#!/usr/bin/env python3
"""
Model Service – Pure HTTP service.
Manages model metadata and selects models based on requirements.
"""
import os
import json
from flask import Flask, request, jsonify

app = Flask(__name__)

# Detect if Claude API key is present
CLAUDE_AVAILABLE = bool(os.getenv("ANTHROPIC_API_KEY"))

# Model metadata with tags – only models installed on this system
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
    "deepseek-coder:6.7b": {
        "name": "deepseek-coder:6.7b",
        "active": False,  # too large, not active by default
        "speed": "slow",
        "quality": "high",
        "size": "6.7B",
        "domain": "code",
        "specialization": "code"
    },
    "qwen2.5:7b": {
        "name": "qwen2.5:7b",
        "active": False,  # too large, not active by default
        "speed": "slow",
        "quality": "high",
        "size": "7B",
        "domain": "general",
        "specialization": "general"
    },
    # ---- Claude models (cloud) ----
    "claude-sonnet-5": {
        "name": "claude-sonnet-5",
        "active": CLAUDE_AVAILABLE,
        "speed": "medium",
        "quality": "very_high",
        "size": "cloud",
        "domain": "general",
        "specialization": "reasoning"
    },
    "claude-opus-5": {
        "name": "claude-opus-5",
        "active": CLAUDE_AVAILABLE,
        "speed": "slow",
        "quality": "maximum",
        "size": "cloud",
        "domain": "general",
        "specialization": "reasoning"
    },
    "claude-fable-5": {
        "name": "claude-fable-5",
        "active": CLAUDE_AVAILABLE,
        "speed": "fast",
        "quality": "high",
        "size": "cloud",
        "domain": "general",
        "specialization": "reasoning"
    },
    "claude-haiku-4-5": {
        "name": "claude-haiku-4-5",
        "active": CLAUDE_AVAILABLE,
        "speed": "fast",
        "quality": "medium",
        "size": "cloud",
        "domain": "general",
        "specialization": "reasoning"
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
    data = request.json or {}
    requirements = data.get("requirements", {})
    if not requirements:
        for name, meta in MODELS.items():
            if meta.get("active", False):
                return jsonify({"success": True, "model": name})
        return jsonify({"success": False, "error": "No active models"}), 404

    best_model = None
    best_score = -1
    for name, meta in MODELS.items():
        if not meta.get("active", False):
            continue
        score = 0
        for key, value in requirements.items():
            if key in meta and meta[key] == value:
                score += 1
        if score > best_score:
            best_score = score
            best_model = name

    if best_model:
        return jsonify({"success": True, "model": best_model, "score": best_score})
    else:
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
    app.run(host="127.0.0.1", port=8006, debug=False)
