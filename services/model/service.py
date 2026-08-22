#!/usr/bin/env python3
"""
Model Service – Pure HTTP service.
Manages model metadata and selects models based on requirements.
"""
import os
import json
import requests
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

# ---------------------------------------------------------------------------
# Capability routing. This service owns model selection, so the capability ->
# backend chains live here rather than in the Inference Service, which only
# executes. Inference asks; Model decides.
# ---------------------------------------------------------------------------
ROUTING_FILE = os.path.expanduser("~/mycelial/config/model_routing.json")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")


def load_routing():
    try:
        with open(ROUTING_FILE) as f:
            return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    except Exception:
        return {}


def ollama_installed_models():
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if resp.status_code == 200:
            return {m.get("name", "") for m in resp.json().get("models", [])}
    except Exception:
        pass
    return set()


def resolve_capability(capability):
    """First backend in the capability's chain that is usable right now: an
    Ollama model that is actually pulled, or a provider whose required env var
    is set. Returns (entry, skipped) so a caller can report WHY nothing was
    available rather than failing opaquely."""
    chain = (load_routing().get(capability) or {}).get("chain", [])
    installed = None
    skipped = []
    for entry in chain:
        provider, model = entry.get("provider"), entry.get("model")
        if provider == "ollama":
            if installed is None:
                installed = ollama_installed_models()
            if model in installed or any(m.split(":")[0] == model.split(":")[0] for m in installed):
                return entry, skipped
            skipped.append(f"{provider}:{model} (not pulled - `ollama pull {model}`)")
        else:
            required = entry.get("requires")
            if required and not os.getenv(required):
                skipped.append(f"{provider}:{model} ({required} not set)")
                continue
            return entry, skipped
    return None, skipped


@app.route("/resolve", methods=["GET", "POST"])
def resolve_endpoint():
    data = request.json if request.method == "POST" else {}
    capability = (data or {}).get("capability") or request.args.get("capability")
    if not capability:
        return jsonify({"success": False, "error": "missing capability"}), 400
    entry, skipped = resolve_capability(capability)
    if not entry:
        return jsonify({
            "success": False, "capability": capability, "skipped": skipped,
            "error": f"No usable backend for '{capability}'. Tried: "
                     + ("; ".join(skipped) if skipped else "nothing configured")
                     + f". Configure it in {ROUTING_FILE}.",
        }), 404
    return jsonify({"success": True, "capability": capability,
                    "provider": entry.get("provider"), "model": entry.get("model"),
                    "skipped": skipped})


@app.route("/capabilities", methods=["GET"])
def capabilities_endpoint():
    out = {}
    for capability in load_routing():
        entry, skipped = resolve_capability(capability)
        out[capability] = {
            "resolves_to": f"{entry['provider']}:{entry['model']}" if entry else None,
            "available": bool(entry),
            "skipped": skipped,
        }
    return jsonify({"success": True, "capabilities": out, "routing_file": ROUTING_FILE})


@app.route("/models/installed", methods=["GET"])
def installed_endpoint():
    """What is actually pulled, as opposed to what the static MODELS table
    claims. The table drifts; this does not."""
    return jsonify({"success": True, "installed": sorted(ollama_installed_models())})

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
