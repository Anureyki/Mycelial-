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
# Anansi's voice.
#
# CLAUDE.md has said from the beginning that Anansi is a storyteller, not a
# dispatcher: "Your reservoir pH has been drifting - I'd adjust it today," never
# "Grow Agent reported a warning." What was actually shipping was a bulletin -
# correct facts in clipped, period-separated fragments. The variable was even
# called `narrated` and nothing narrated.
#
# Deliberately DETERMINISTIC. A model rewriting these sentences would be free to
# invent, and every fabrication caught today came from exactly that - a small
# model given room to fill a gap. So this restructures and connects what it was
# given and never adds a fact. If a sentence is not in the payload it does not
# appear in the telling.
#
# What makes it a story rather than a report:
#   - it opens by saying what kind of news this is
#   - it joins fragments with connectives instead of full stops
#   - it speaks to the grower, not about the system
#   - it lands on what to do, or on the fact that there is nothing to do
import random
import re as _re

OPENERS = {
    "blocked": ["Not yet, and here is what is standing in the way.",
                "Hold off a moment - a few things are in front of you.",
                "I would wait, and I can tell you exactly why."],
    "clear":   ["Nothing is in the way.", "You are clear to go ahead.",
                "No reason to wait on this one."],
    "steady":  ["All quiet.", "Nothing needs you right now.",
                "Everything is sitting where it should."],
    "action":  ["Here is what that takes.", "Right, here is the arithmetic.",
                "That one has a number attached."],
    "estimate":["Here is the arithmetic, with a caveat.",
                "I can give you a number, but read the second half.",
                "Roughly, and then why roughly."],
    "history": ["Here is how it got this way.", "That goes back a bit.",
                "The record has the answer to that one."],
    # No counts in the openers - the number of conditions varies and an opener
    # that says "two" over three items reads as not having looked.
    "timing":  ["Here is what has to be true first.", "It is not a date, it is a set of conditions.",
                "It depends on a couple of things landing."],
}
CONNECTIVES = ["And ", "On top of that, ", "There is also this: ", "Then ",
               "The other thing: "]

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

    def narrate(self, text, prompt=""):
        """Tell it, rather than report it. Facts in, same facts out, arranged
        like someone talking."""
        if not text or not isinstance(text, str) or len(text) < 40:
            return text
        # Anything already conversational is left alone.
        if text.lstrip().startswith(("Got it", "I couldn't", "I could not", "Hold", "Not yet,")):
            return text

        # Classify on the LEAD sentence. The situation carries every facet, so
        # judging by the whole payload gave a dose answer a "three things are in
        # the way" opener - the blockers were in there, just not first.
        lead_sentence = _re.split(r'(?<=[.!?])\s+', text.strip())[0]
        low = lead_sentence.lower()
        if "in the way" in low or "clears when" in low or low.startswith("not yet"):
            kind = "blocked"
        elif low.startswith("nothing is blocking") or "go ahead" in low:
            kind = "clear"
        elif _re.search(r"\bon 20\d\d-\d\d-\d\d\b", lead_sentence):
            kind = "history"
        elif _re.search(r"\badd\b.*\d+(\.\d+)?ml", low):
            kind = "action"
        elif low.startswith("when:") or low.startswith("no condition"):
            kind = "timing"
        # A calculation opened with "Nothing needs you right now", because a
        # projection matched none of the shapes above and fell through to the
        # all-quiet default. An answer containing a number the grower asked for
        # is never "nothing needs you".
        elif _re.search(r"\bworks out at\b|\bday\(s\)\b|\bcomes to\b|\d+\s*ppm/day", low):
            kind = "estimate"
        else:
            kind = "steady"

        parts = [p.strip() for p in _re.split(r'(?<=[.!?])\s+', text) if p.strip()]
        if len(parts) < 2:
            return text

        # Deterministic per input, so the same question does not reword itself
        # every time it is asked - that reads as instability, not personality.
        rnd = random.Random(hash(text) & 0xffff)
        opener = rnd.choice(OPENERS[kind])

        # Drop a leading sentence the opener already covers.
        if kind == "blocked" and _re.match(r'^not yet\b', parts[0], _re.I):
            parts = parts[1:]

        body = []
        for i, p in enumerate(parts):
            if (i and i % 3 == 0 and len(p) > 30
                    and not p.startswith(("Clears", "And", "The", "That", "At", "A "))
                    and not _re.match(r'^(A|An|The|This|That|It|They)\b', p)):
                body.append(rnd.choice(CONNECTIVES) + p[0].lower() + p[1:])
            else:
                body.append(p)
        told = opener + " " + " ".join(body)

        # "Clears when:" is machine phrasing. A storyteller says what unlocks it.
        # "Clears when:" is machine phrasing; a storyteller says what unlocks it.
        # The replacement runs mid-sentence, so the word that followed the colon
        # was capitalised and has to come back down - "once A reading has been
        # taken" is the sort of seam that gives away a template.
        def _lower_next(m):
            return m.group(1) + m.group(2).lower()
        told = _re.sub(r'(Clears when:\s+)([A-Z])', lambda m: "That lifts once " + m.group(2).lower(), told)
        told = _re.sub(r'(clears when:\s+)([A-Z])', lambda m: "which lifts once " + m.group(2).lower(), told)
        told = _re.sub(r'\bWhen:\s+', '', told)
        told = _re.sub(r'\bAnd:\s+([A-Z])', lambda m: "and " + m.group(1).lower(), told)
        told = _re.sub(r'\.\s+and ', ', and ', told)
        told = told.replace("Clears when:", "That lifts once")
        told = told.replace("clears when:", "which lifts once")
        told = _re.sub(r'\bNot yet - (\d+) thing\(s\) in the way\.',
                       lambda m: f"There are {m.group(1)} of them.", told)
        told = told.replace(" thing(s)", " things")
        return told

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
