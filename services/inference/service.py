#!/usr/bin/env python3
"""
Inference Service – Pure HTTP service, NOT an agent.
Runs inference using a specified model or the default.
Supports local Ollama models and cloud models (Claude) via cloud_service.
"""
import os
import re
import subprocess
import time
from flask import Flask, request, jsonify

# Import the cloud reasoning provider
try:
    from cloud_service import reason as cloud_reason, ANTHROPIC_MODELS
except ImportError:
    # Fallback if cloud_service.py is missing
    ANTHROPIC_MODELS = {}
    def cloud_reason(prompt, model, max_tokens=1024):
        return {"error": "Cloud service not available"}

app = Flask(__name__)

DEFAULT_MODEL = os.getenv("INFERENCE_MODEL", "qwen2.5:1.5b")
TIMEOUT = int(os.getenv("INFERENCE_TIMEOUT", "180"))

def clean_output(text):
    """Remove ANSI escape codes and normalize whitespace."""
    if not text:
        return ""
    text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        if line not in cleaned[-3:]:
            cleaned.append(line)
    return '\n'.join(cleaned).strip()

def run_ollama_inference(model, prompt):
    """Run inference with Ollama."""
    start_time = time.time()
    try:
        result = subprocess.run(
            ["ollama", "run", model, prompt],
            capture_output=True, text=True, timeout=TIMEOUT
        )
        latency = int((time.time() - start_time) * 1000)
        if result.returncode != 0:
            return {
                "success": False,
                "error": "inference_failed",
                "message": result.stderr,
                "latency_ms": latency
            }
        output = clean_output(result.stdout.strip())
        tokens = len(output) // 4
        return {
            "success": True,
            "model": model,
            "result": output,
            "latency_ms": latency,
            "tokens": tokens,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "timeout",
            "message": f"Inference timed out after {TIMEOUT}s",
            "latency_ms": TIMEOUT * 1000
        }
    except Exception as e:
        return {
            "success": False,
            "error": "exception",
            "message": str(e),
            "latency_ms": int((time.time() - start_time) * 1000)
        }

def run_claude_inference(model, prompt, image_path=None):
    """Run inference with Claude (via cloud_service). image_path is forwarded for
    the vision-escalation tier - omitting it here silently broke every escalated
    vision call with a TypeError, which then fell back to the (wrong) local read."""
    start_time = time.time()
    result = cloud_reason(prompt=prompt, model=model, max_tokens=1024, image_path=image_path)
    latency = int((time.time() - start_time) * 1000)
    if "error" in result:
        return {
            "success": False,
            "error": "claude_error",
            "message": result["error"],
            "latency_ms": latency
        }
    tokens = result.get("tokens", {}).get("output", 0)
    return {
        "success": True,
        "model": result["model_used"],
        "result": result["result"],
        "latency_ms": latency,
        "tokens": tokens,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }

def run_inference(model, prompt, image_path=None):
    """Route to the appropriate inference backend."""
    if model in ANTHROPIC_MODELS or model.startswith("claude"):
        return run_claude_inference(model, prompt, image_path=image_path)
    if image_path:
        return {
            "success": False,
            "error": "vision_unsupported",
            "message": "Image input requires a Claude model - local Ollama models here are text-only.",
        }
    return run_ollama_inference(model, prompt)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "inference_service"})

@app.route("/reason", methods=["POST"])
def reason_endpoint():
    data = request.json or {}
    prompt = data.get("prompt", "")
    model = data.get("model") or DEFAULT_MODEL
    image_path = data.get("image_path")
    if not prompt:
        return jsonify({"success": False, "error": "missing_prompt", "message": "No prompt provided"}), 400

    result = run_inference(model, prompt, image_path=image_path)
    return jsonify(result)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8005, debug=False)
