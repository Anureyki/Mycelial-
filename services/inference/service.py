#!/usr/bin/env python3
"""
Inference Service – Pure HTTP service, NOT an agent.
Runs inference using a specified model or the default.
Supports local Ollama models and cloud models (Claude) via cloud_service.
"""
import base64
import json
import os
import re
import subprocess
import time

import requests
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
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
ROUTING_FILE = os.path.expanduser("~/mycelial/config/model_routing.json")


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
    """Pick the first backend in a capability's chain that is actually usable
    right now - an ollama model that's pulled, or a cloud provider whose key is
    present. Returns (entry, skipped) so a caller can report *why* nothing was
    available instead of failing opaquely."""
    chain = (load_routing().get(capability) or {}).get("chain", [])
    installed = None
    skipped = []
    for entry in chain:
        provider = entry.get("provider")
        model = entry.get("model")
        if provider == "ollama":
            if installed is None:
                installed = ollama_installed_models()
            # Ollama tags carry an explicit :tag; match with or without it.
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


def simplify_prompt(prompt):
    """Flatten punctuation small vision models choke on.

    Verified against moondream: a prompt containing an apostrophe and a
    dash-joined clause ("Describe this leaf's health - color, spots, ...")
    returns an EMPTY completion with done_reason=stop, while the same question
    in plain sentences answers normally. Reproducible, not intermittent."""
    simplified = prompt.replace("’", "").replace("'", "")
    simplified = simplified.replace("—", " ").replace("–", " ")
    simplified = re.sub(r'\s+-\s+', '. ', simplified)
    return re.sub(r'\s{2,}', ' ', simplified).strip()


def _ollama_generate(model, prompt, b64, timeout):
    resp = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": model, "prompt": prompt, "images": [b64], "stream": False},
        timeout=timeout,
    )
    return resp


def run_ollama_vision(model, prompt, image_path):
    """Vision via Ollama's HTTP API - the CLI path used for text can't carry
    images, so this is a separate call rather than a flag on the other one.

    An empty completion is treated as a failure to retry, not as a valid answer:
    callers reasonably treat "" as "no result", and silently returning success
    with nothing in it is how a broken read gets mistaken for a clean one."""
    start_time = time.time()
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return {"success": False, "error": "image_unreadable", "message": str(e)}

    attempts = [prompt]
    simplified = simplify_prompt(prompt)
    if simplified and simplified != prompt:
        attempts.append(simplified)

    last_error = None
    for attempt_prompt in attempts:
        try:
            resp = _ollama_generate(model, attempt_prompt, b64, TIMEOUT)
        except Exception as e:
            last_error = str(e)
            continue
        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            continue
        text = clean_output((resp.json().get("response") or "").strip())
        # Small VLMs sometimes emit a degenerate completion ("!!!") instead of
        # nothing. That's not an answer either, and it passes a bare truthiness
        # check, so require some actual words before accepting it.
        if text and not re.search(r'[A-Za-z]{3}', text):
            last_error = f"model returned a degenerate completion ({text[:20]!r})"
            continue
        if text:
            return {
                "success": True,
                "model": model,
                "result": text,
                "latency_ms": int((time.time() - start_time) * 1000),
                "retried_simplified": attempt_prompt is not prompt,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        last_error = "model returned an empty completion"

    return {
        "success": False,
        "error": "ollama_vision_empty" if last_error and "empty" in last_error else "ollama_vision_failed",
        "message": f"{model}: {last_error}",
        "latency_ms": int((time.time() - start_time) * 1000),
    }

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

def run_inference(model, prompt, image_path=None, capability=None):
    """Route to a backend. Prefer capability-based routing ('vision',
    'reasoning') so callers never name a vendor - an explicit `model` still
    works and wins, for callers that genuinely need one specific brain."""
    if capability and not model:
        entry, skipped = resolve_capability(capability)
        if not entry:
            return {
                "success": False,
                "error": "no_backend_for_capability",
                "message": (
                    f"No usable backend for capability '{capability}'. Tried: "
                    + ("; ".join(skipped) if skipped else "nothing configured")
                    + f". Configure it in {ROUTING_FILE}."
                ),
                "skipped": skipped,
            }
        provider, model = entry.get("provider"), entry.get("model")
        if provider == "ollama":
            return run_ollama_vision(model, prompt, image_path) if image_path \
                else run_ollama_inference(model, prompt)
        return run_claude_inference(model, prompt, image_path=image_path)

    model = model or DEFAULT_MODEL
    if model in ANTHROPIC_MODELS or model.startswith("claude"):
        return run_claude_inference(model, prompt, image_path=image_path)
    if image_path:
        return run_ollama_vision(model, prompt, image_path)
    return run_ollama_inference(model, prompt)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "inference_service"})

@app.route("/reason", methods=["POST"])
def reason_endpoint():
    data = request.json or {}
    prompt = data.get("prompt", "")
    capability = data.get("capability")
    # Only default the model when no capability was requested - otherwise the
    # default would silently override routing and defeat the whole point.
    model = data.get("model") or (None if capability else DEFAULT_MODEL)
    image_path = data.get("image_path")
    if not prompt:
        return jsonify({"success": False, "error": "missing_prompt", "message": "No prompt provided"}), 400

    result = run_inference(model, prompt, image_path=image_path, capability=capability)
    return jsonify(result)


@app.route("/capabilities", methods=["GET"])
def capabilities_endpoint():
    """What each capability would resolve to right now, and what got skipped -
    so a missing backend is diagnosable without reading code."""
    out = {}
    for capability in load_routing():
        entry, skipped = resolve_capability(capability)
        out[capability] = {
            "resolves_to": f"{entry['provider']}:{entry['model']}" if entry else None,
            "available": bool(entry),
            "skipped": skipped,
        }
    return jsonify({"success": True, "capabilities": out, "routing_file": ROUTING_FILE})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8005, debug=False)
