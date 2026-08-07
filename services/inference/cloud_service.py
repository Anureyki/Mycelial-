#!/usr/bin/env python3
"""
Cloud reasoning provider – routes prompts to the Anthropic API.
Used by the Inference Service to serve Claude models alongside local Ollama models.
"""
import os
import base64
import mimetypes

from anthropic import Anthropic

ANTHROPIC_MODELS = {
    "claude-sonnet-5": "claude-sonnet-5",
    "claude-opus-5": "claude-opus-5",
    "claude-fable-5": "claude-fable-5",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
}

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    _client = Anthropic(api_key=api_key)
    return _client


def reason(prompt, model=None, max_tokens=1024, image_path=None):
    """Send a prompt to Claude and return a normalized result dict.

    image_path is optional and used only by low-confidence-escalation callers
    (e.g. Grow Agent's perception pipeline) - the primary reasoning path stays
    text-only local inference; this is the rarely-hit verification tier."""
    client = _get_client()
    if client is None:
        return {"error": "ANTHROPIC_API_KEY not set"}

    model_id = ANTHROPIC_MODELS.get(model, model or "claude-sonnet-5")

    content = prompt
    if image_path:
        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            media_type = mimetypes.guess_type(image_path)[0] or "image/jpeg"
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.b64encode(image_bytes).decode("utf-8"),
                    },
                },
                {"type": "text", "text": prompt},
            ]
        except Exception as e:
            return {"error": f"Could not read image for vision call: {e}"}

    try:
        response = client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as e:
        return {"error": str(e)}

    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )

    return {
        "result": text,
        "model_used": model_id,
        "tokens": {
            "input": response.usage.input_tokens,
            "output": response.usage.output_tokens,
        },
    }
