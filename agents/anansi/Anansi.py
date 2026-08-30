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


# ---------------------------------------------------------------------------
# Anansi's voice lives in config/anansi_voice.json and is applied by
# agents/anansi/voice.py. It used to be a block of constants and a method right
# here, which meant the personality could not change without editing the agent
# - and the spec is explicit that personality must evolve independently of
# domain logic, because a voice change must never be able to become an
# authority change.
#
# The engine is deterministic and verifies that every number, date, unit and
# citation survives the telling. If one does not, the telling is discarded and
# the plain text ships. Anansi narrates a determination; he never makes one.
from agents.anansi.voice import Voice

class Anansi(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="anansi",
            port=8081,
            capabilities=["process_request", "narrate_contradiction", "voice_policy"],
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

    def narrate(self, text, prompt=""):
        """Tell it, rather than report it. Facts in, the same facts out."""
        if not hasattr(self, "_voice"):
            self._voice = Voice(log=self.log)
        return self._voice.tell(text)

    def narrate_contradiction(self, claim, observed, resolution=None):
        """The trickster's actual job: name a discrepancy plainly.

        Both sides must arrive as facts. Anansi exposes a contradiction he was
        handed; he does not go looking for irony that is not in the payload."""
        if not hasattr(self, "_voice"):
            self._voice = Voice(log=self.log)
        return self._voice.contradiction(claim, observed, resolution)

    def handle_task(self, task, args, sender):
        self.log(f"Received task: {task}, args: {args}, sender: {sender}")

        # A narrow passthrough for the training review panel. The webapp
        # reaches this agent and nothing else - Phase 6 put one TLS front door
        # in place deliberately - but a review UI cannot work through
        # natural-language round trips: it needs a list of images and two
        # verbs. So exactly three grow tasks are forwarded, by name. Anything
        # else still has to come in as a request and be routed.
        # Two dashboard cards that want STATE, not narration. Routing them
        # through process_request handed a question to the reasoning path,
        # which answered it - the Grow card returned an argument about feed
        # strength while the grower was asking what the numbers are, and the
        # Progress card returned a paragraph assembled from three session-log
        # entries. Neither carried a timestamp, so stale output looked current.
        if task == "grow_snapshot":
            return self.send_a2a("grow_agent", "grow_snapshot",
                                 args if isinstance(args, dict) else {})

        if task == "recent_changes":
            payload = args if isinstance(args, dict) else {}
            fwd = {"limit": int(payload.get("limit", 10))}
            if payload.get("scope"):
                fwd["scope"] = payload["scope"]
            if payload.get("include_domain"):
                fwd["include_domain"] = True
            return self.send_a2a("maintenance_agent", "recent_changes", fwd)

        if task == "phase_status":
            return self.send_a2a("maintenance_agent", "phase_status", {})

        if task == "training_candidates":
            return self.send_a2a("grow_agent", "list_training_candidates", {})

        if task == "training_quest_status":
            return self.send_a2a("grow_agent", "training_quest_status", {})

        if task == "advance_campaign":
            payload = args if isinstance(args, dict) else {}
            return self.send_a2a("grow_agent", "advance_training_campaign",
                                 {"per_label": int(payload.get("per_label", 3)),
                                  "max_labels": int(payload.get("max_labels", 2))},
                                 timeout=180)

        if task == "review_candidate":
            payload = args if isinstance(args, dict) else (
                json.loads(args[0]) if args and isinstance(args[0], str)
                and args[0].startswith('{') else {})
            cid = payload.get("candidate_id")
            decision = (payload.get("decision") or "").lower()
            if not cid or decision not in ("accept", "reject"):
                return {"error": "Usage: {candidate_id, decision: accept|reject}"}
            return self.send_a2a("grow_agent", "review_training_candidate",
                                 {"candidate_id": cid, "decision": decision})

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
                    narrated = {"result": self.narrate(inner.get("result"), prompt)}
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

        elif task == "narrate_contradiction":
            a = args if isinstance(args, dict) else {}
            told = self.narrate_contradiction(a.get("claim"), a.get("observed"),
                                              a.get("resolution"))
            if not told:
                return {"error": "narrate_contradiction needs both a claim and "
                                 "an observation - a contradiction with one side "
                                 "missing is not a contradiction, it is a guess"}
            return {"told": told}

        elif task == "voice_policy":
            # Inspect the personality layer without touching an agent. Editing
            # config/anansi_voice.json takes effect on the next telling; there
            # is nothing to restart, and nothing here can alter a conclusion.
            if not hasattr(self, "_voice"):
                self._voice = Voice(log=self.log)
            cfg = self._voice.cfg
            sample = (args or {}).get("sample") if isinstance(args, dict) else None
            out = {"config": "config/anansi_voice.json",
                   "registers": cfg.get("registers", {}),
                   "identity": cfg.get("identity", {}),
                   "authority": "narration, translation, presentation - nothing else"}
            if sample:
                reg, meta = self._voice.register_for(sample)
                out["sample_register"] = {"register": reg, **meta}
                out["sample_told"] = self._voice.tell(sample)
            return out

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
