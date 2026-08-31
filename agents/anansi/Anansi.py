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

    # ------------------------------------------------------------------
    # Reaching the principal.
    #
    # The design is the principal's own, in two corrections. First: agents do
    # not each grow an outbound channel - Anansi is the interface layer, so it
    # owns every way of reaching him and nothing else does. Second, and the one
    # that shapes this: *"Anansi is not necessarily the one that's remembering.
    # The domains are remembering their task. But it can be the interface that
    # interacts with me on different levels."*
    #
    # So THIS HOLDS NO QUEUE. Grow remembers what is due; Legal remembers what
    # runs out. Anansi keeps no copy of either, because a copy is a second
    # source of truth and the copy is always the one that drifts. It is handed
    # something and it delivers it. What it owns is the CHANNEL and the voice.
    #
    # "Different levels" is the interesting part and it is already built:
    # the voice registers rank a situation from low_stakes 1.0 to
    # safety_critical 0.1, so how serious a thing is already has a number.
    # Channel keys off the same number rather than a second scale nobody
    # maintains.
    #
    # THREE TIERS OF AUTHORITY, AND ONLY THE FIRST IS AUTOMATIC:
    #
    #   tell the principal      a reminder, a deadline. Safe. Automatic.
    #   draft a document        Legal writes it, nothing leaves. Safe.
    #   send to a third party   a landlord, HUD, a regulator. NEVER automatic.
    #
    # The third is refused here outright. An agent that can post a statutory
    # notice on someone's behalf is an agent that can post the wrong one, and
    # a misdirected 92.056(b) notice or a premature filing is not correctable
    # afterwards. Hardware sits behind an authorization boundary in this system
    # for the same reason, and an outbound legal communication is the same kind
    # of act.

    # Voice strength -> how loudly to reach him. One scale, not two.
    CHANNELS = (
        (0.00, 0.34, ("email", "dashboard"), "serious enough to interrupt"),
        (0.34, 0.61, ("email", "dashboard"), "worth an email"),
        (0.61, 1.01, ("dashboard",),         "it can wait for him to look"),
    )

    def notify(self, args):
        """Deliver something a domain agent needs the principal to know.

        `verbatim` is the whole distinction between a courier and a narrator.
        A domain document - a notice Legal drafted, a figure Accounting
        derived - goes out UNCHANGED. Anansi narrating legal text would be
        Anansi practising law, which is the one thing this agent must never
        do. It tells the story of what happened; it does not write the
        instrument.
        """
        a = args if isinstance(args, dict) else {}
        body = str(a.get("body") or "").strip()
        subject = str(a.get("subject") or "").strip()
        sender = str(a.get("from_agent") or a.get("sender") or "").strip()
        if not body:
            return {"error": "notify needs a body - the thing to say."}
        if not sender:
            return {"error": ("notify needs from_agent. A message the principal cannot "
                              "trace to a domain is a message he cannot check.")}

        # Refused, structurally, not by convention.
        to = str(a.get("to") or "principal").strip().lower()
        if to != "principal":
            return {"error": (f"Refused: notify reaches the principal only, and this "
                              f"names '{to}'. Sending to a third party is an outward act "
                              f"with consequences that cannot be recalled - it needs his "
                              f"explicit sign-off, not an agent's decision."),
                    "sent": False, "requires_signoff": True}

        verbatim = bool(a.get("verbatim"))
        hint = a.get("register")
        if not hasattr(self, "_voice"):
            self._voice = Voice(log=self.log)
        try:
            name, reg = self._voice.register_for(body, hint)
            strength = float(reg.get("voice", 0.5))
        except Exception as exc:
            # Named, not swallowed. Falling back silently to a mid register
            # would decide the CHANNEL for a message whose seriousness was
            # never actually assessed - and the channel is the whole point.
            self.log(f"notify: register_for failed ({exc}); defaulting to technical")
            name, strength = "technical", 0.6

        channels, why = ("dashboard",), "default"
        for lo, hi, ch, reason in self.CHANNELS:
            if lo <= strength < hi:
                channels, why = ch, reason
                break
        if a.get("channel"):
            channels = (str(a["channel"]),)
            why = "caller named the channel"

        # Voice applies to a telling, never to a document. And even for a
        # telling the guarantee still runs inside `tell` - a number lost or
        # invented and the plain text ships instead.
        text = body if verbatim else self._voice.tell(body, hint=hint)

        return {
            "delivered_to": "principal",
            "channels": list(channels),
            "channel_reason": why,
            "register": name,
            "voice_strength": strength,
            "verbatim": verbatim,
            "from_agent": sender,
            "subject": subject or None,
            "text": text,
            "held_here": False,
            "note": ("Anansi keeps no copy of this. The domain that raised it is "
                     "still the only place it is remembered."),
            **self._deliver(channels, subject, text, sender),
        }

    def _deliver(self, channels, subject, text, sender):
        """Actually put it in front of him, and say honestly if it could not.

        `email` needs a credential this machine does not have yet. A delivery
        that silently does nothing while reporting success is the exact failure
        this project hunts, so an unconfigured channel returns `sent: False`
        with the reason and the one thing that would fix it - it does not
        pretend, and it does not fall back to the dashboard while claiming the
        email went.
        """
        import os
        out = {"sent": {}, "unsent": {}}
        for ch in channels:
            if ch == "dashboard":
                # The dashboard reads the DOMAIN's register directly, so there
                # is nothing to push - which is the point. Grow's reminders and
                # Legal's actions are already on their cards.
                out["sent"][ch] = ("visible on the domain's own card; nothing was copied "
                                   "here to make that true")
            elif ch == "email":
                if not (os.getenv("NOTIFY_SMTP_HOST") and os.getenv("NOTIFY_SMTP_USER")
                        and os.getenv("NOTIFY_SMTP_PASS") and os.getenv("NOTIFY_TO")):
                    out["unsent"][ch] = (
                        "No mail credential on this machine. Set NOTIFY_SMTP_HOST, "
                        "NOTIFY_SMTP_USER, NOTIFY_SMTP_PASS and NOTIFY_TO in .env. "
                        "Until then this system cannot reach the principal when he is "
                        "not looking at it - every reminder it holds is a note to "
                        "someone it cannot contact.")
                    continue
                try:
                    import smtplib
                    from email.message import EmailMessage
                    m = EmailMessage()
                    m["Subject"] = subject or f"MycOS: {sender}"
                    m["From"] = os.environ["NOTIFY_SMTP_USER"]
                    m["To"] = os.environ["NOTIFY_TO"]
                    m.set_content(text)
                    port = int(os.getenv("NOTIFY_SMTP_PORT", "587"))
                    with smtplib.SMTP(os.environ["NOTIFY_SMTP_HOST"], port, timeout=30) as s:
                        s.starttls()
                        s.login(os.environ["NOTIFY_SMTP_USER"], os.environ["NOTIFY_SMTP_PASS"])
                        s.send_message(m)
                    out["sent"][ch] = f"emailed to {os.environ['NOTIFY_TO']}"
                except Exception as exc:
                    # Named, never swallowed. A notification that failed
                    # quietly is worse than one never attempted, because the
                    # domain believes he was told.
                    out["unsent"][ch] = f"send failed: {type(exc).__name__}: {exc}"
                    self.log(f"notify: email delivery failed: {exc}")
            else:
                out["unsent"][ch] = f"unknown channel '{ch}'"
        out["sent_any"] = bool(out["sent"] and any(k != "dashboard" for k in out["sent"]))
        return out

    # ------------------------------------------------------------------
    # Mail arriving.
    #
    # Anansi owns every channel, inward as well as outward - the symmetric
    # half of `notify`. What makes the inbound direction different is that
    # an email is written by SOMEBODY ELSE, who may know it is being read by
    # an agent. Outbound text comes from a domain this system trusts; inbound
    # text is a stranger's, and the difference decides the whole design.
    #
    # SO NO DOMAIN AGENT EVER SEES THE BODY.
    #
    # The message is written to disk and the domain is handed a REFERRAL - who
    # it came from, when, what it is about, and a path. Exactly the rule that
    # already governs cross-domain findings: the sending side decides what
    # crosses the boundary and sends the minimum. Legal learns *a letter
    # arrived from the property manager on this date, it is here*; it does not
    # receive a paragraph of somebody else's prose into the agent that holds
    # the corpus and drafts documents.
    #
    # Two things follow, and both are refusals:
    #
    #   - Nothing arriving by mail is authority. It enters as a source with
    #     `evidence_kind: reported` and `source_class: unknown`, and it earns
    #     anything better by being read - by the principal, or through
    #     triage_source. A document does not become true by being emailed.
    #   - The referral carries no instruction. Only metadata and a path, so
    #     text a stranger wrote cannot arrive shaped like a task. An agent that
    #     will act on sentences from an untrusted mailbox is an agent anyone
    #     with the address can drive.

    MAIL_DIR = "state/inbox"

    def receive_mail(self, args):
        """Fetch what arrived, file it, and refer it without its contents."""
        import email as _email
        import hashlib
        import imaplib
        import os
        a = args if isinstance(args, dict) else {}
        limit = int(a.get("limit", 10))

        need = ("MAIL_IMAP_HOST", "MAIL_IMAP_USER", "MAIL_IMAP_PASS")
        missing = [k for k in need if not os.getenv(k)]
        if missing:
            return {"fetched": 0, "configured": False, "missing_env": missing,
                    "note": ("No mailbox credential on this machine, so nothing can "
                             "arrive. Set " + ", ".join(need) + " in .env, pointing at a "
                             "DEDICATED address rather than the principal's personal "
                             "inbox - a mailbox an agent reads should contain only what "
                             "was meant for it.")}

        os.makedirs(self.MAIL_DIR, exist_ok=True)
        out, errors = [], []
        try:
            box = imaplib.IMAP4_SSL(os.environ["MAIL_IMAP_HOST"],
                                    int(os.getenv("MAIL_IMAP_PORT", "993")))
            box.login(os.environ["MAIL_IMAP_USER"], os.environ["MAIL_IMAP_PASS"])
            box.select(os.getenv("MAIL_IMAP_FOLDER", "INBOX"))
            typ, data = box.search(None, "UNSEEN")
            ids = (data[0].split() if data and data[0] else [])[:limit]
            for mid in ids:
                typ, raw = box.fetch(mid, "(RFC822)")
                if not raw or not raw[0]:
                    continue
                blob = raw[0][1]
                msg = _email.message_from_bytes(blob)
                digest = hashlib.sha256(blob).hexdigest()[:16]
                path = os.path.join(self.MAIL_DIR, f"mail_{digest}.eml")
                with open(path, "wb") as fh:
                    fh.write(blob)
                # THE REFERRAL. Metadata and a path. No body, no snippet, no
                # subject-derived "action" - a subject line is written by the
                # sender too.
                out.append({
                    "id": f"mail_{digest}",
                    "from": str(msg.get("From") or "")[:200],
                    "date": str(msg.get("Date") or "")[:80],
                    "subject": str(msg.get("Subject") or "")[:200],
                    "has_attachments": any(p.get_filename() for p in msg.walk()),
                    "attachment_names": [p.get_filename() for p in msg.walk()
                                         if p.get_filename()][:10],
                    "bytes": len(blob),
                    "stored_at": path,
                    "evidence_kind": "reported",
                    "source_class": "unknown",
                    "read_by_system": False,
                    "note": ("Filed, not read. Nothing here has been assessed, and the "
                             "subject line was written by the sender like everything "
                             "else. It becomes evidence when a person or triage_source "
                             "opens it."),
                })
            box.close()
            box.logout()
        except Exception as exc:
            # Named. A mailbox that silently fetches nothing looks exactly like
            # a mailbox with nothing in it.
            self.log(f"receive_mail: {type(exc).__name__}: {exc}")
            errors.append(f"{type(exc).__name__}: {exc}")

        return {"fetched": len(out), "configured": True, "messages": out,
                "errors": errors or None,
                "domains_saw_body": False,
                "note": ("Each message is on disk and each domain gets the reference "
                         "only. Nothing arriving by mail is authority - it is a source "
                         "with unknown standing until something reads it.")}

    def ingest_upload(self, args):
        """Send an uploaded file to the domain that should read it.

        Anansi owns the channel and picks the destination; it does NOT read the
        file. Which domain a screenshot belongs to is a routing decision - the
        thing this agent does - and what the screenshot MEANS is the domain's,
        which is the thing it must never do.

        The caller names the domain when they know it. When they do not, the
        default is Legal, and the reason is stated rather than hidden: a legal
        card can be checked against a corpus in seconds, while a grow card
        cannot be checked until harvest, so a wrong guess costs far less in one
        direction than the other."""
        a = args if isinstance(args, dict) else {}
        path = a.get("path")
        if not path:
            return {"error": "ingest_upload needs the path returned by /upload"}
        domain = (a.get("domain") or "").strip().lower()
        if domain not in ("legal", "grow"):
            domain = "legal"
            why = ("No domain given, so this went to Legal. A legal card resolves against "
                   "a corpus in seconds; a grow card resolves at harvest. The cheap check "
                   "is the safer default.")
        else:
            why = "Domain named by the caller."
        agent = "legal_agent" if domain == "legal" else "grow_agent"
        payload = {"image_path": path}
        if a.get("topic"):
            payload["topic"] = a["topic"]
        if a.get("captured_from"):
            payload["captured_from"] = a["captured_from"]
        out = self.send_a2a(agent, "ingest_screenshot", payload, timeout=300)
        return {"routed_to": agent, "why": why, "path": path, "result": out}

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
        if task == "receive_mail":
            return self.receive_mail(args if isinstance(args, dict) else {})

        if task == "ingest_upload":
            return self.ingest_upload(args if isinstance(args, dict) else {})

        if task == "notify":
            return self.notify(args if isinstance(args, dict) else {})

        # The dashboard tracks EVERY plant, not the current one.
        #
        # The Grow card called grow_snapshot with no plant_id, which means
        # `current_plant`, so GSC2 - a second Girl Scout Cookies in an LWC at
        # day 10 - was tracked by the agent and invisible on the screen. A plant
        # the system holds readings for and the dashboard does not show is a
        # plant the grower has to remember on his own, which is the job the
        # dashboard exists to take off him.
        if task == "grow_roster":
            roster = self.send_a2a("grow_agent", "list_plants", {})
            r = roster
            for _ in range(6):
                if isinstance(r, dict) and "result" in r and len(r) == 1:
                    r = r["result"]
                else:
                    break
            active = (r or {}).get("active") or []
            out = []
            for p in active:
                pid = p.get("plant_id")
                if not pid:
                    continue
                snap = self.send_a2a("grow_agent", "grow_snapshot", {"plant_id": pid})
                for _ in range(6):
                    if isinstance(snap, dict) and "result" in snap and len(snap) == 1:
                        snap = snap["result"]
                    else:
                        break
                if isinstance(snap, dict) and not snap.get("error"):
                    out.append(snap)
            return {"plants": out, "count": len(out)}

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

        # The Legal card wants the register, not a telling of it. A matter is
        # lost by a step nobody took, and narration is exactly the wrong layer
        # for that - it is allowed to shorten, and the thing that gets shortened
        # out of a to-do list is the item nobody has started.
        if task == "actions":
            payload = args if isinstance(args, dict) else {}
            return self.send_a2a("legal_agent", "actions",
                                 {"case_id": payload.get("case_id"),
                                  "include_closed": bool(payload.get("include_closed"))})

        if task == "deadlines":
            payload = args if isinstance(args, dict) else {}
            return self.send_a2a("legal_agent", "deadlines",
                                 {"case_id": payload.get("case_id")})

        if task == "system_graph":
            payload = args if isinstance(args, dict) else {}
            return self.send_a2a("maintenance_agent", "system_graph",
                                 {"hours": int(payload.get("hours", 24)),
                                  "min_calls": int(payload.get("min_calls", 2))},
                                 timeout=60)

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
