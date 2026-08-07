#!/usr/bin/env python3
import sys
import os
import time
import json
import uuid
from datetime import datetime

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

BASE = os.path.expanduser("~/mycelial")
REGISTRY_FILE = os.path.join(BASE, "state", "registry.json")

EVIDENCE_KEYWORDS = ("why", "how do you know", "show your work", "evidence", "proof", "based on what", "show evidence")

class Anansi(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="anansi",
            port=8081,
            capabilities=["process_request"],
            role="interface"
        )
        self.sessions = {}
        self.log("🕸️ Anansi interface ready (uses Registry Service + fallback)")

    def find_orchestrator(self):
        """Query the Registry Service, fallback to local registry.json."""
        # Try Registry Service first
        try:
            import requests
            resp = requests.post(
                "http://localhost:8004/execute",
                json={"task": "list_agents", "args": [], "sender": "anansi"},
                timeout=3
            )
            if resp.status_code == 200:
                agents = resp.json().get("result", [])
                for agent in agents:
                    if agent.get("role") == "orchestrator":
                        self.log(f"Found orchestrator (Registry Service): {agent.get('agent_id')}")
                        return agent.get("agent_id")
        except Exception as e:
            self.log(f"Registry Service query failed: {e}")

        # Fallback: read local registry.json
        if os.path.exists(REGISTRY_FILE):
            try:
                with open(REGISTRY_FILE, "r") as f:
                    registry = json.load(f)
                for agent_id, info in registry.items():
                    if info.get("role") == "orchestrator":
                        self.log(f"Found orchestrator (local registry): {agent_id}")
                        return agent_id
            except Exception as e:
                self.log(f"Failed to read local registry: {e}")

        # Hardcoded fallback
        self.log("Using default orchestrator: boss_agent")
        return "boss_agent"

    def handle_task(self, task, args, sender):
        self.log(f"Received task: {task}, args: {args}, sender: {sender}")

        if task == "process_request" or task == "process":
            if len(args) == 0:
                return {"error": "Missing prompt"}

            # Parse prompt and metadata
            if len(args) == 1 and args[0].startswith('{'):
                try:
                    payload = json.loads(args[0])
                    prompt = payload.get("prompt", "")
                    metadata = payload.get("metadata", {})
                except:
                    prompt = args[0]
                    metadata = {}
            else:
                prompt = args[0]
                metadata = {
                    "session_id": args[1] if len(args) > 1 else str(uuid.uuid4()),
                    "user_id": "default_user",
                    "modality": "text",
                    "timestamp": datetime.now().isoformat(),
                    "source": "anansi"
                }

            # "default" so evidence-mode works out of the box for callers that
            # don't manage their own session id (curl, the webapp client as-is).
            session_id = metadata.get("session_id") or "default"

            if any(kw in prompt.lower() for kw in EVIDENCE_KEYWORDS):
                session = self.get_or_create_session(session_id)
                last = session["conversation"][-1] if session["conversation"] else None
                if last and last.get("evidence") is not None:
                    self.log(f"Evidence request for session {session_id} - returning cached detail, not re-routing")
                    return {"result": last["evidence"]}

            response = self.route_to_orchestrator(prompt, metadata)

            # Agents attach raw "evidence" alongside the narrated "result" so the
            # architecture can stay behind the curtain by default - cache it here,
            # strip it from what's actually shown, and only surface it if asked.
            evidence = None
            narrated = response
            if isinstance(response, dict) and "result" in response:
                inner = response.get("result")
                if isinstance(inner, dict) and "evidence" in inner:
                    evidence = inner.get("evidence")
                    narrated = {"result": inner.get("result")}
            self.append_to_session(session_id, {
                "timestamp": datetime.now().isoformat(),
                "prompt": prompt,
                "response": narrated,
                "evidence": evidence,
            })
            return narrated

        elif task == "voice":
            transcript = " ".join(args) if args else ""
            self.log("🎤 Voice input converted to text")
            return self.handle_task("process_request", [transcript], sender)

        else:
            return {"error": f"Unknown task: {task}"}

    def route_to_orchestrator(self, prompt, metadata):
        orchestrator = self.find_orchestrator()
        if not orchestrator:
            return {"error": "No orchestrator available"}

        self.log(f"Routing to {orchestrator}: {prompt[:50]}...")
        try:
            payload = json.dumps({"prompt": prompt, "metadata": metadata})
            # Boss may fan requests out to multiple agents sequentially (e.g.
            # analyze_relationship_document); give this hop more room than the
            # 120s default so a multi-agent chain doesn't get reported as a
            # failure when it actually completed on the Boss side.
            response = self.send_a2a(orchestrator, "process_request", [payload], timeout=280)
            return response
        except Exception as e:
            self.log(f"Error routing to orchestrator: {e}")
            return {"error": str(e)}

    def get_or_create_session(self, session_id):
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "created": datetime.now().isoformat(),
                "conversation": [],
                "user_id": "default_user"
            }
        return self.sessions[session_id]

    def append_to_session(self, session_id, entry):
        session = self.get_or_create_session(session_id)
        session["conversation"].append(entry)

if __name__ == "__main__":
    agent = Anansi()
    while True:
        time.sleep(60)
        agent.heartbeat()
