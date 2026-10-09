#!/usr/bin/env python3
import sys
import os
import time
import hashlib
import re
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
            capabilities=["process_request", "narrate_contradiction", "voice_policy",
                          "remember_user_fact", "user_context", "check_cross_domain", "refresh_routing", "drift_alerts",
                          "routing_map",
                          # Declared because they dispatch. An undeclared verb
                          # works when called and is invisible to the registry,
                          # the dashboard and the router - which is the 82-capability
                          # fault, and four of these were added today without
                          # declaring them, reproducing it inside the same week
                          # it was documented.
                          "notify", "receive_mail", "ingest_upload", "grow_roster",
                          "grow_snapshot", "recent_changes", "phase_status",
                          "system_graph", "actions", "deadlines", "reminders",
                          "inbound_mail", "route_held_mail", "held_mail_digest",
                          "training_candidates", "training_quest_status",
                          "advance_campaign", "review_candidate", "voice"],
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
        if a.get("channels"):
            channels = tuple(str(c) for c in a["channels"])
            why = "caller named the channels"
        elif a.get("channel"):
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
            elif ch == "email" and os.getenv("AGENTMAIL_API_KEY") and os.getenv("AGENTMAIL_INBOX") \
                    and os.getenv("NOTIFY_TO"):
                # AgentMail (the principal's choice, 2026-10-04): MycOS sends from
                # its own inbox, so no personal mail credential lives here.
                try:
                    import urllib.request, urllib.parse
                    inbox = os.environ["AGENTMAIL_INBOX"]
                    req = urllib.request.Request(
                        "https://api.agentmail.to/v0/inboxes/"
                        + urllib.parse.quote(inbox, safe="") + "/messages/send",
                        data=json.dumps({"to": os.environ["NOTIFY_TO"],
                                         "subject": subject or f"MycOS: {sender}",
                                         "text": text}).encode(),
                        headers={"Authorization": "Bearer " + os.environ["AGENTMAIL_API_KEY"],
                                 "Content-Type": "application/json"}, method="POST")
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        code = resp.status
                    if 200 <= code < 300:
                        out["sent"][ch] = f"emailed to {os.environ['NOTIFY_TO']} from {inbox}"
                    else:
                        out["unsent"][ch] = f"AgentMail returned HTTP {code}"
                except Exception as exc:
                    out["unsent"][ch] = f"AgentMail send failed: {type(exc).__name__}: {exc}"
                    self.log(f"notify: AgentMail delivery failed: {exc}")
            elif ch == "email":
                if not (os.getenv("NOTIFY_SMTP_HOST") and os.getenv("NOTIFY_SMTP_USER")
                        and os.getenv("NOTIFY_SMTP_PASS") and os.getenv("NOTIFY_TO")):
                    out["unsent"][ch] = (
                        "No mail credential on this machine. Set AGENTMAIL_API_KEY, "
                        "AGENTMAIL_INBOX and NOTIFY_TO (or the NOTIFY_SMTP_* set) in .env. "
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

        if os.getenv("AGENTMAIL_API_KEY") and os.getenv("AGENTMAIL_INBOX"):
            return self._receive_agentmail(a)

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

    # ------------------------------------------------------------------
    # The AgentMail listener (principal's request, 2026-10-05).
    #
    # Polled every five minutes from cron. Three rules, his:
    #   1. LOG FIRST. Sender, subject and timestamp go to the inbound log
    #      before the body is fetched, filed or routed - so there is a record
    #      of what came in even when everything after it fails.
    #   2. NOTHING EXECUTES FROM AN EMAIL. A domain receives a referral as a
    #      finding of kind `inbound_mail`, which records and does not act;
    #      the domain proposes, the principal confirms.
    #   3. DRY RUN FIRST. config/mail_listener.json `mode` starts at dry_run,
    #      which logs what WOULD be routed and touches no agent. Only the
    #      principal turns it to live.
    #
    # Routing uses the same live router as a spoken request - the subject and
    # attachment names are run through DomainRouter, which asks each agent what
    # words it claims. Anansi keeps no mail keywords of its own. One clear
    # owner among the routable domains gets the referral; none, several, or a
    # domain outside the list means HELD for the principal. A subject is
    # written by the sender, so it may pick a department and nothing more.
    INBOUND_LOG = "state/inbox/inbound_log.jsonl"
    LISTENER_CONFIG = "config/mail_listener.json"

    def _listener_config(self):
        try:
            with open(self.LISTENER_CONFIG) as fh:
                cfg = json.load(fh)
        except Exception as exc:
            # An unreadable config is dry_run, never live.
            return {"mode": "dry_run", "routable": [], "config_error": str(exc)}
        if cfg.get("mode") != "live":
            cfg["mode"] = "dry_run"
        return cfg

    def _private_write(self, path, data, append=False):
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC),
                     0o600)
        with os.fdopen(fd, "a" if append else "w") as fh:
            fh.write(data)

    def _inbound_log(self, entry):
        entry = {"logged_at": datetime.now().isoformat(timespec="seconds"), **entry}
        self._private_write(self.INBOUND_LOG, json.dumps(entry) + "\n", append=True)
        return entry

    def _inbound_entries(self):
        try:
            with open(self.INBOUND_LOG) as fh:
                return [json.loads(l) for l in fh if l.strip()]
        except FileNotFoundError:
            return []

    def _agentmail(self, path, method="GET", body=None):
        import urllib.request
        req = urllib.request.Request(
            "https://api.agentmail.to/v0" + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + os.environ["AGENTMAIL_API_KEY"],
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")

    def _route_mail(self, ref, routable):
        text = " ".join([ref.get("subject") or ""] + list(ref.get("attachment_names") or []))
        if not text.strip():
            return None, "no subject and no attachment names - nothing to route on"
        try:
            claims = self.router.domains_for(text, fallback=False)
        except Exception as exc:
            return None, f"router unavailable: {type(exc).__name__}: {exc}"
        if not claims:
            return None, "no department claims it"
        if len(claims) > 1:
            return None, f"claimed by more than one department: {claims}"
        if claims[0] not in routable:
            return None, f"{claims[0]} claims it, and mail routes only to {routable}"
        return claims[0], f"{claims[0]} claims it on the live routing vocabulary"

    def _refer_mail(self, agent, ref):
        r = self.send_a2a(agent, "receive_finding",
                          {"kind": "inbound_mail", "payload": ref}, timeout=60)
        for _ in range(4):
            if isinstance(r, dict) and "recorded" not in r and "result" in r:
                r = r["result"]
        return r

    def _receive_agentmail(self, a):
        import urllib.parse
        cfg = self._listener_config()
        mode, routable = cfg["mode"], list(cfg.get("routable") or [])
        inbox = os.environ["AGENTMAIL_INBOX"]
        qi = urllib.parse.quote(inbox, safe="")
        done_key = "live_done" if mode == "live" else "dry_done"
        seen = {e.get("message_id") for e in self._inbound_entries()
                if e.get("event") == done_key}
        try:
            listing = self._agentmail(f"/inboxes/{qi}/messages?limit="
                                      f"{int(a.get('limit', 25))}&labels=received")
        except Exception as exc:
            # Named. An inbox that cannot be read is not an empty inbox.
            self.log(f"receive_mail: AgentMail list failed: {exc}")
            return {"mode": mode, "fetched": 0, "error": f"{type(exc).__name__}: {exc}"}
        out = []
        for m in reversed(listing.get("messages") or []):
            mid = m.get("message_id")
            if not mid or mid in seen:
                continue
            # 1. LOG FIRST - before the body is fetched or anything is decided.
            self._inbound_log({"event": "arrived", "mode": mode, "message_id": mid,
                               "from": str(m.get("from") or "")[:200],
                               "subject": str(m.get("subject") or "")[:200],
                               "timestamp": m.get("timestamp")})
            ref = {"message_id": mid, "from": str(m.get("from") or "")[:200],
                   "subject": str(m.get("subject") or "")[:200],
                   "timestamp": m.get("timestamp"), "evidence_kind": "reported",
                   "source_class": "unknown", "read_by_system": False}
            full = {}
            try:
                full = self._agentmail(f"/inboxes/{qi}/messages/"
                                       + urllib.parse.quote(mid, safe=""))
                digest = hashlib.sha256(mid.encode()).hexdigest()[:16]
                path = os.path.join(self.MAIL_DIR, f"mail_{digest}.json")
                self._private_write(path, json.dumps(full))
                ref.update({"stored_at": path,
                            "attachment_names": [x.get("filename") for x in
                                                 (full.get("attachments") or [])
                                                 if x.get("filename")][:10]})
            except Exception as exc:
                ref["store_error"] = f"{type(exc).__name__}: {exc}"
            # THE ONE LINK ANANSI FOLLOWS. Mail is a stranger's text and its links
            # are not followed - except the principal's own sensor export, under
            # every condition he would check himself (see _marshydro_export).
            if str((cfg.get("auto_fetch") or {}).get("marshydro_thp_export")) == "live":
                fx = self._marshydro_export(full, mid)
                if fx is not None:
                    self._inbound_log({"event": "auto_fetch", "message_id": mid, **fx})
                    ref["auto_fetch"] = fx
            agent, why = self._route_mail(ref, routable)
            rec = {"message_id": mid, "from": ref["from"], "subject": ref["subject"],
                   "route": agent, "why": why, "stored_at": ref.get("stored_at")}
            if mode == "dry_run":
                rec["decision"] = (f"WOULD refer to {agent}" if agent
                                   else "WOULD hold for the principal")
            elif agent:
                res = self._refer_mail(agent, ref)
                rec["decision"] = f"referred to {agent} (recorded, not acted on)"
                rec["domain_reply"] = res
            else:
                rec["decision"] = "held for the principal"
            self._inbound_log({"event": done_key, "mode": mode, **rec})
            out.append(rec)
        told = None
        if mode == "live" and out:
            lines = [f"- {r['decision']}: \"{r['subject']}\" from {r['from']}" for r in out]
            told = self.notify({"subject": f"{len(out)} new message(s) in the MycOS inbox",
                                "body": "\n".join(lines) + "\n\nNothing was acted on. "
                                        "Held mail waits for you; a referred message is "
                                        "recorded by that department, which will propose "
                                        "and wait for your yes.",
                                "from_agent": "anansi", "register": "low_stakes",
                                "channels": ["email", "dashboard"]})
        return {"mode": mode, "fetched": len(out), "messages": out,
                "domains_saw_body": False, "principal_told": told,
                "note": ("DRY RUN - nothing was referred and nobody was told. Set "
                         "mode to live in config/mail_listener.json when this looks right."
                         if mode == "dry_run" else
                         "Referrals carry metadata and a path, never the body.")}

    MARSHYDRO_SENDER = "marshydro-noreply@notice00.marshydro.eu"
    MARSHYDRO_URL = re.compile(r"https://mars-pro\.api\.lgledsolutions\.com/api/common/download/"
                               r"file/v1\?uuid=[0-9a-f]{16,64}")
    FETCH_DIR = "state/inbox/fetched"
    FETCH_MAX_BYTES = 25 * 1024 * 1024

    def _marshydro_export(self, full, mid):
        """-> None when the message is not a Mars Hydro export at all; otherwise a
        record of what was checked and what happened. Every refusal says why."""
        sender = str((full or {}).get("from") or "")
        if self.MARSHYDRO_SENDER not in sender.lower():
            return None
        auth = (full or {}).get("authentication_results") or {}
        if not all(str(auth.get(k)).lower() == "pass" for k in ("spf", "dkim", "dmarc")):
            return {"fetched": False, "why": f"authentication not all pass: {auth}"}
        links = sorted(set(self.MARSHYDRO_URL.findall(str(full.get("html") or "") + " "
                                                       + str(full.get("text") or ""))))
        if len(links) != 1:
            return {"fetched": False, "why": f"expected exactly one export link, found {len(links)}"}
        try:
            import urllib.request
            req = urllib.request.Request(links[0], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                ctype = str(r.headers.get("Content-Type") or "")
                blob = r.read(self.FETCH_MAX_BYTES + 1)
        except Exception as exc:
            return {"fetched": False, "why": f"download failed: {type(exc).__name__}: {exc}"}
        if len(blob) > self.FETCH_MAX_BYTES:
            return {"fetched": False, "why": "larger than the 25 MB cap"}
        head = blob[:400].decode("utf-8-sig", errors="replace").splitlines()[0].lower() if blob else ""
        if "text/csv" not in ctype.lower() or not all(c in head for c in
                                                       ("deviceserialnum", "temperature", "humidity", "timestamp")):
            return {"fetched": False, "why": f"not a THP CSV (type {ctype!r}, header {head[:80]!r})"}
        digest = hashlib.sha256(blob).hexdigest()[:16]
        path = os.path.join(self.FETCH_DIR, f"marshydro_thp_{digest}.csv")
        self._private_write(path, blob.decode("utf-8-sig", errors="replace"))
        res = self.send_a2a("grow_agent", "ingest_tent_export",
                            {"csv_path": os.path.abspath(path),
                             "note": f"Mars Hydro THP export, auto-fetched by Anansi from message {mid[:40]}"},
                            timeout=300)
        inner = res
        for _ in range(4):
            if isinstance(inner, dict) and "plants" not in inner and "result" in inner:
                inner = inner["result"]
        return {"fetched": True, "bytes": len(blob), "stored_at": path,
                "handed_to": "grow_agent.ingest_tent_export",
                "grow_result": (inner.get("plants") if isinstance(inner, dict) else str(inner)[:200])}

    def inbound_mail(self, args):
        """The inbound log, newest last: what arrived and what was done with it."""
        a = args if isinstance(args, dict) else {}
        ents = self._inbound_entries()
        if a.get("held"):
            routed = {e["message_id"] for e in ents if e.get("event") == "routed_by_principal"}
            ents = [e for e in ents if e.get("event") == "live_done" and not e.get("route")
                    and e["message_id"] not in routed]
        return {"mode": self._listener_config()["mode"], "entries": ents[-int(a.get("limit", 50)):]}

    def held_mail_digest(self, args):
        """Weekly: what sat in held since the last digest, sent to the principal.

        The principal's point: new words will arrive that no department claims,
        the held pile grows, and *"a pile nobody checks is just a slower version
        of the inbox."* So the review comes to him. Each row carries the reason
        nothing claimed it - that reason is the vocabulary gap to close before
        the listener goes live, or the message to route by hand once it is."""
        a = args if isinstance(args, dict) else {}
        ents = self._inbound_entries()
        last = max((e["logged_at"] for e in ents if e.get("event") == "held_digest_sent"),
                   default="")
        routed = {e["message_id"] for e in ents if e.get("event") == "routed_by_principal"}
        held = [e for e in ents if e.get("event") in ("dry_done", "live_done")
                and not e.get("route") and e["logged_at"] > last
                and e["message_id"] not in routed]
        if not held:
            return {"held": 0, "sent": False, "since": last or "the start"}
        lines = [f"- \"{e.get('subject')}\" from {e.get('from')} ({e['logged_at'][:10]}): "
                 f"{e.get('why')}" for e in held]
        mode = self._listener_config()["mode"]
        body = (f"{len(held)} message(s) held since {last[:10] or 'the listener started'} "
                f"(listener mode: {mode}).\n\n" + "\n".join(lines) + "\n\n"
                + ("Dry run: these WOULD have been held. A message here that belongs to "
                   "a department is a word that department should declare."
                   if mode == "dry_run" else
                   "Route one with route_held_mail, or tell Anansi which department "
                   "it belongs to."))
        told = None if a.get("dry") else self.notify({
            "subject": f"MycOS inbox: {len(held)} held message(s) to review",
            "body": body, "from_agent": "anansi", "register": "low_stakes",
            "channels": ["email", "dashboard"]})
        delivered = isinstance(told, dict) and told.get("sent_any")
        if delivered:
            self._inbound_log({"event": "held_digest_sent", "count": len(held)})
        return {"held": len(held), "sent": bool(delivered), "body": body,
                "delivery": told}

    def route_held_mail(self, args):
        """The principal decided where a held message goes. Still a referral -
        recorded by the domain, not acted on."""
        a = args if isinstance(args, dict) else {}
        mid, agent = a.get("message_id"), a.get("agent")
        hit = next((e for e in self._inbound_entries() if e.get("message_id") == mid
                    and e.get("event") == "live_done" and not e.get("route")), None)
        if not hit:
            # Only mail HELD by the live listener. A dry run refers nothing,
            # and this must not be a way to refer from one.
            return {"error": f"{mid!r} is not a message the live listener held"}
        if agent not in ("legal_agent", "accounting_agent", "grow_agent", "trust_agent"):
            return {"error": "agent must be legal_agent, accounting_agent, grow_agent or trust_agent"}
        ref = {k: hit.get(k) for k in ("message_id", "from", "subject", "stored_at")}
        ref.update({"evidence_kind": "reported", "source_class": "unknown"})
        res = self._refer_mail(agent, ref)
        self._inbound_log({"event": "routed_by_principal", "message_id": mid,
                           "route": agent, "domain_reply": res})
        return {"referred_to": agent, "domain_reply": res}

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
        # ROUTED FROM THE DECLARED SOURCE TYPE, to any department.
        #
        # This offered two destinations - legal and grow - and called
        # ingest_screenshot, so a bank statement had nowhere to go and a
        # statement sent anyway was handled as a picture of a post. Accounting
        # and Trust could not receive a document at all.
        #
        # The destination now follows the TYPE the person declared at upload:
        # a statement is Accounting's because they said "statement", not
        # because a word appeared in it. That is the same rule Anansi already
        # routes sentences by - a declared fact, never a reading - and it is
        # why this agent can route a document it must never open.
        src = (a.get("source_type") or "").strip().lower()
        domain = (a.get("domain") or "").strip().lower()
        if src:
            from core.document_pipeline import ROUTE
            if src not in ROUTE:
                return {"error": f"{src!r} is not a declared source type",
                        "known": sorted(ROUTE)}
            agent, why = ROUTE[src]
            out = self.send_a2a(agent, "ingest_document",
                                {"path": path, "source_type": src,
                                 "document_id": a.get("document_id"),
                                 "effective_date": a.get("effective_date")},
                                timeout=600)
            also = []
            from core.document_pipeline import ALSO_NOTIFY
            for peer in ALSO_NOTIFY.get(src, []):
                also.append(peer)
            return {"routed_to": agent, "why": why, "source_type": src,
                    "also_notified": also, "path": path, "result": out}

        # No type declared: the old screenshot path, unchanged.
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
        if task == "inbound_mail":
            return self.inbound_mail(args)
        if task == "route_held_mail":
            return self.route_held_mail(args)
        if task == "held_mail_digest":
            return self.held_mail_digest(args)

        if task == "ingest_upload":
            return self.ingest_upload(args if isinstance(args, dict) else {})

        if task == "notify":
            return self.notify(args if isinstance(args, dict) else {})

        if task == "remember_user_fact":
            return self.remember_user_fact(args if isinstance(args, dict) else {})

        if task == "user_context":
            return self.user_context(
                refresh=bool((args or {}).get("refresh")) if isinstance(args, dict) else False)

        if task == "check_cross_domain":
            a = args if isinstance(args, dict) else {}
            return self.check_cross_domain(a.get("prompt", ""), a.get("answers") or {})

        if task == "refresh_routing":
            # Moved here with the router. An agent that restarts with changed
            # routing terms is invisible to the cache until its TTL expires,
            # and the remedy must not be "restart the interface as well".
            self.router._domain_cache["map"] = None
            self.router._domain_cache["at"] = 0
            v = self.router.vocabulary()
            return {"refreshed": True, "agents": len(v),
                    "terms": sum(len(t) for t in v.values())}

        if task == "drift_alerts":
            # RELAYED, NOT COMPUTED. Anansi owns the channel and Security owns
            # the monitor - this agent has no view on whether a threshold
            # breach means anything, and forming one would be the interface
            # layer practising a domain.
            #
            # A DOWN MONITOR IS NOT AN ALL-CLEAR, and the card must be able to
            # tell the difference. An unreachable Security produces `unknown`,
            # never `clear`.
            r = self.send_a2a("security_agent", "drift_scan", {}, timeout=40)
            for _ in range(6):
                if isinstance(r, dict) and "status" not in r and "result" in r:
                    r = r["result"]
                else:
                    break
            if not isinstance(r, dict) or "status" not in r:
                return {"status": "unknown", "alerts": [], "count": 0,
                        "headline": "drift monitor unreachable",
                        "why": ("Security did not answer. Nothing is being "
                                "observed, which is not the same as nothing "
                                "being wrong.")}
            alerts = r.get("alerts") or []
            return {
                "status": r.get("status"),
                "headline": r.get("headline"),
                "count": len(alerts),
                "alerts": [{
                    "kind": a.get("kind"),
                    "severity": a.get("severity"),
                    "agent": a.get("agent"),
                    "detail": a.get("detail"),
                    "why": a.get("why"),
                    "not_a_verdict": a.get("not_a_verdict"),
                } for a in alerts],
                "records": r.get("records"),
                "checked_at": r.get("scanned_at"),
                "note": r.get("note"),
            }

        if task == "routing_map":
            return {"vocabulary": {k: len(v) for k, v in self.router.vocabulary().items()},
                    "note": "Anansi routes directly; Boss governs and is not a hop."}

        # The dashboard tracks EVERY plant, not the current one.
        #
        # The Grow card called grow_snapshot with no plant_id, which means
        # `current_plant`, so GSC2 - a second Girl Scout Cookies in an LWC at
        # day 10 - was tracked by the agent and invisible on the screen. A plant
        # the system holds readings for and the dashboard does not show is a
        # plant the grower has to remember on his own, which is the job the
        # dashboard exists to take off him.
        if task == "grow_roster":
            # RELAYED, NOT ASSEMBLED. This used to call list_plants and then
            # grow_snapshot once per plant - four sequential A2A round trips
            # for three plants, 3.42 SECONDS, while the agent answers any one
            # of them in 26ms. The cost was transport, paid once per plant, and
            # the loop was the interface layer doing Grow's job: every new
            # field on a plant meant editing Anansi.
            #
            # Grow now has `roster` and this forwards to it. It also stops
            # silently dropping a plant whose snapshot failed - that comes back
            # in `unreadable` and the card can say so.
            r = self.send_a2a("grow_agent", "roster", {})
            for _ in range(6):
                if isinstance(r, dict) and "result" in r and len(r) == 1:
                    r = r["result"]
                else:
                    break
            return r if isinstance(r, dict) else {"plants": [], "count": 0,
                                                  "error": str(r)[:200]}

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

        # What a domain says is coming due, for its card. Read live from the
        # domain every time - Anansi keeps no copy of a reminder. This is what
        # makes notify's "visible on the domain's own card" true: before it,
        # the Grow card asked for maintenance_due, this agent answered
        # "Unknown task", and the card rendered without the banner, silently.
        if task == "reminders":
            agent = (args or {}).get("agent") if isinstance(args, dict) else None
            if agent not in ("grow_agent", "legal_agent", "accounting_agent"):
                return {"error": "reminders needs agent: grow_agent, legal_agent "
                                 "or accounting_agent"}
            return self.send_a2a(agent, "reminders_due", {})

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

    # ==================================================================
    # DIRECT ROUTING. Anansi routes; Boss governs.
    #
    # Boss used to sit between the person and the department as a transport
    # hop, and it also authorised. Those are different edges - this file's own
    # architecture notes say so about safety loops - and running them through
    # one component meant every request paid a governance round trip whether it
    # needed one or not, while the governance itself was invisible inside the
    # forwarding.
    #
    # Now: Anansi asks the router which department owns the sentence, asks Boss
    # whether it MAY route there, and goes straight to the department. Boss
    # answers a policy question and never carries the payload.
    #
    # THE ROUTER IS NOT ANANSI'S EITHER. It lives in core/routing.py and holds
    # no domain vocabulary - it asks each agent what words it claims. Anansi
    # gained a route, not a domain. An interface layer that started keeping its
    # own domain words would become the thing Boss was stopped from becoming.
    # ==================================================================

    @property
    def router(self):
        if getattr(self, "_router", None) is None:
            from core.routing import DomainRouter
            self._router = DomainRouter(self.agent_id, log=self.log)
        return self._router

    def _policy_check(self, domains, prompt):
        """Ask Boss whether this route is permitted. Boss governs, not forwards.

        FAILS OPEN, DELIBERATELY, AND ONLY HERE. The swarm guard already works
        this way and for the same reason: a Boss that is restarting must not
        stop a grower asking about his reservoir. This is a routing question,
        not a capital one - CLAUDE.md inverts the default for anything touching
        money, and that inversion is not undone by this path, because nothing
        here can move money. An explicit refusal is honoured; an unreachable
        governor is logged and the request proceeds."""
        try:
            r = self.send_a2a("boss_agent", "authorize_route",
                              {"domains": list(domains), "prompt": prompt[:400]},
                              timeout=10)
            for _ in range(6):
                if isinstance(r, dict) and "allowed" not in r and "result" in r:
                    r = r["result"]
                else:
                    break
            if isinstance(r, dict) and r.get("allowed") is False:
                return {"allowed": False, "reason": r.get("reason") or "policy refused",
                        "veto_by": "boss_agent"}
            if isinstance(r, dict):
                return {"allowed": True, "conditions": r.get("conditions") or [],
                        "logged": bool(r.get("logged"))}
        except Exception as e:
            self.log(f"policy: boss unreachable ({e}) - routing anyway, "
                     f"an unreachable governor must not halt an interface")
        return {"allowed": True, "conditions": [], "governor_unreachable": True}

    def _provenance(self, prompt, domains, chosen, metadata, outcome):
        """Every route writes provenance, through the shared schema.

        `select` is the operation - Anansi chose a department. It is not
        `execute`: Anansi did not do the domain work and must not appear in the
        lineage as having authored the answer."""
        try:
            from core.provenance_schemas import new_provenance_event
            from core.provenance_manager import ProvenanceManager
            ev = new_provenance_event(
                operation="select",
                actor_type="agent",
                agent_id=self.agent_id,
                actor_id=(metadata or {}).get("user_id") or "default_user",
                execution_id=(metadata or {}).get("session_id"),
                metadata={
                    "prompt_sha256": hashlib.sha256(
                        (prompt or "").encode("utf-8")).hexdigest(),
                    "prompt_chars": len(prompt or ""),
                    "candidate_domains": list(domains),
                    "routed_to": chosen,
                    "cross_domain": len(domains) > 1,
                    "outcome": outcome,
                    "router": "core.routing.DomainRouter",
                },
            )
            ProvenanceManager().record_event(ev)
            return ev["event_id"]
        except Exception as e:
            # A provenance write that fails must SAY so. A route with no record
            # and a route whose record was lost look identical afterwards, and
            # they are different problems.
            self.log(f"PROVENANCE NOT WRITTEN for route to {chosen}: {e}")
            return None

    def _ask_domain(self, agent_id, prompt, metadata):
        """One department, its own verb, its own words.

        CONSTRAINTS RIDE ALONG. A standing constraint is true regardless of
        which department is asked, and the department is the only thing that
        can say what it means for its own domain - so it is sent, not applied
        here. Anansi holds the fact and practises no domain."""
        payload = {"prompt": prompt}
        cons = self._constraints_for_request()
        if cons.get("_unavailable"):
            payload["constraints_unavailable"] = cons.get("_why") or True
        elif cons:
            payload["constraints"] = cons
        r = self.send_a2a(agent_id, "answer", payload, timeout=240)
        for _ in range(6):
            if isinstance(r, dict) and "result" in r and len(r) == 1:
                r = r["result"]
            else:
                break
        return r

    def route_direct(self, prompt, metadata):
        """-> the department's answer, or a flagged contradiction. Never a merge."""
        domains = self.router.domains_for(prompt) or []
        if not domains:
            self._provenance(prompt, [], None, metadata, "no_domain_claimed")
            return {"result": ("No department claims this. That is a gap in what "
                               "the system can answer, not a refusal - nothing here "
                               "declared the words you used."),
                    "routed_to": None, "domains_considered": []}

        policy = self._policy_check(domains, prompt)
        if not policy.get("allowed"):
            self._provenance(prompt, domains, None, metadata, "policy_refused")
            return {"result": f"That request was not permitted: {policy.get('reason')}",
                    "vetoed_by": policy.get("veto_by"), "domains_considered": domains}

        if len(domains) == 1:
            chosen = domains[0]
            self.log(f"routing DIRECT to {chosen} (no Boss hop)")
            ans = self._ask_domain(chosen, prompt, metadata)
            self._provenance(prompt, domains, chosen, metadata, "answered")
            return {"result": ans, "routed_to": chosen, "domains_considered": domains}

        # TWO DEPARTMENTS CLAIM THIS. Ask both; do NOT merge.
        self.log(f"CROSS-DOMAIN: {domains} both claim this - asking each, then checking")
        answers = {}
        for d in domains[:3]:
            answers[d] = self._ask_domain(d, prompt, metadata)
        finding = self.check_cross_domain(prompt, answers)
        self._provenance(prompt, domains, list(answers), metadata,
                         "contradiction" if finding["contradicted"] else "cross_domain_agreed")
        return {"result": finding, "routed_to": list(answers),
                "domains_considered": domains, "cross_domain": True}

    # ------------------------------------------------------------------
    # PERSISTENT USER CONTEXT
    #
    # Anansi is the interface, so it is where a person's standing facts belong -
    # preferences, rituals, and CONSTRAINTS. Constraints are the reason this is
    # not a convenience feature: a dietary restriction that has to be restated
    # every session is a restriction the system will eventually miss, and the
    # cost of missing a celiac constraint is not a worse answer, it is harm.
    #
    # SHARED NAMESPACE, NOT `agent_anansi`. store_own_memory namespaces per
    # agent, which is exactly the drift the case layer avoids: a constraint
    # filed under one agent is invisible to every other, so the department that
    # needed it cannot see it. This talks to Hermes directly, the same way
    # core/case_manager.py does and for the same reason.
    #
    # ANANSI HOLDS THE CHANNEL, NOT THE DOMAIN. It stores that the principal
    # has coeliac disease; it does not reason about gluten. The constraint
    # travels with the request to whichever department owns the question, and
    # that department decides what it means. The principal's own correction -
    # "Anansi is not necessarily the one that's remembering. The domains are
    # remembering their task" - is about DOMAIN facts. A standing fact about
    # the person belongs to the interface, because it is true regardless of
    # which department is being asked.
    # ------------------------------------------------------------------

    USER_NS = "user_context"
    CONTEXT_KINDS = ("preference", "ritual", "constraint")

    def remember_user_fact(self, args):
        """Store a standing fact about the person. Survives restarts."""
        a = args if isinstance(args, dict) else {}
        kind = str(a.get("kind") or "").lower()
        key, value = a.get("key"), a.get("value")
        if kind not in self.CONTEXT_KINDS:
            return {"stored": False,
                    "error": f"kind must be one of {list(self.CONTEXT_KINDS)}; "
                             f"got {kind!r}. An unclassified standing fact cannot "
                             f"be weighed - a preference may be overridden and a "
                             f"constraint may not."}
        if not key or value is None:
            return {"stored": False, "error": "key and value are both required"}
        rec = {"kind": kind, "key": key, "value": value,
               "why": a.get("why"), "source": a.get("source") or "stated by the principal",
               "recorded_at": datetime.now().isoformat()}
        mem_key = f"{kind}:{key}"
        try:
            self.send_a2a("hermes", "store_memory",
                          [self.USER_NS, mem_key, json.dumps(rec)])
            # AN INDEX, BECAUSE retrieve_many TAKES KEYS AND NOT A NAMESPACE.
            # Hermes is a broker and deliberately does not enumerate - so the
            # writer keeps the list of what it wrote, the same way Grow keeps
            # reading_index. Without it the first read after a restart returns
            # nothing and looks exactly like a person who has stated no
            # constraints.
            idx = self._context_index()
            if mem_key not in idx:
                idx.append(mem_key)
                self.send_a2a("hermes", "store_memory",
                              [self.USER_NS, "_index", json.dumps(sorted(idx))])
        except Exception as e:
            return {"stored": False, "error": f"hermes unreachable: {e}"}
        self.log(f"user context: remembered {kind} {key!r}")
        return {"stored": True, "record": rec}

    def _context_index(self):
        """-> [keys] written under user_context, or None if it cannot be read.

        None and [] are different answers and are kept apart: an unreadable
        index means the constraints are unknown, an empty one means there are
        none."""
        try:
            r = self.send_a2a("hermes", "retrieve_memory", [self.USER_NS, "_index"])
            raw = self._unwrap_value(r)
            if raw in (None, ""):
                return []
            v = json.loads(raw) if isinstance(raw, str) else raw
            return v if isinstance(v, list) else []
        except Exception as e:
            self.log(f"user context: index unreadable ({e})")
            return None

    def user_context(self, refresh=False):
        """-> {"preference": {...}, "ritual": {...}, "constraint": {...}}

        Cached briefly per process. A constraint that is one Hermes round trip
        away from every sentence is a constraint that gets skipped under load."""
        c = getattr(self, "_ctx_cache", None)
        if c and not refresh and time.time() - c["at"] < 60:
            return c["data"]
        out = {k: {} for k in self.CONTEXT_KINDS}
        try:
            keys = self._context_index()
            if keys is None:
                raise RuntimeError("context index unreadable")
            if not keys:
                self._ctx_cache = {"at": time.time(), "data": out}
                return out
            r = self.send_a2a("hermes", "retrieve_many", [self.USER_NS, keys])
            for _ in range(6):
                if isinstance(r, dict) and "entries" not in r and "result" in r:
                    r = r["result"]
                else:
                    break
            # AN ERROR IS NOT AN EMPTY RESULT. The first version of this read
            # an {"error": ...} dict as "no entries" and returned a clean, empty
            # context - which is the single most dangerous shape this method
            # has, because a missing constraint and a person with no
            # constraints are indistinguishable to every caller downstream.
            if not isinstance(r, dict) or "entries" not in r:
                raise RuntimeError(f"hermes returned {str(r)[:120]}")
            for k, wrapper in (r.get("entries") or {}).items():
                entry = wrapper.get("entry") if isinstance(wrapper, dict) else None
                raw = entry.get("value") if isinstance(entry, dict) else entry
                try:
                    rec = json.loads(raw) if isinstance(raw, str) else raw
                except Exception:
                    continue
                if isinstance(rec, dict) and rec.get("kind") in out:
                    out[rec["kind"]][rec.get("key")] = rec
        except Exception as e:
            # SAY SO. An empty context and an unreachable store look identical
            # to a caller, and one of them means a constraint is missing.
            self.log(f"user context: could not load from Hermes ({e}) - "
                     f"treating as UNKNOWN, not as empty")
            return {**out, "_unavailable": True, "_why": str(e)}
        self._ctx_cache = {"at": time.time(), "data": out}
        return out

    def _constraints_for_request(self):
        """Constraints only. They travel with every route; preferences do not,
        because a preference that overrides a department's judgement is not a
        preference any more."""
        ctx = self.user_context()
        if ctx.get("_unavailable"):
            return {"_unavailable": True, "_why": ctx.get("_why")}
        return {k: v.get("value") for k, v in (ctx.get("constraint") or {}).items()}

    # ------------------------------------------------------------------
    # CROSS-DOMAIN CONTRADICTION CHECKING
    #
    # Two departments answering the same question is not a tie to be broken.
    # CLAUDE.md already settled the principle for claims - "Domains are allowed
    # to disagree, and disagreement is not resolved by outranking" - and the
    # outcome there is `contested`, with both readings kept and the conflict
    # surfaced. This is that rule applied one layer up, at the point where the
    # answers would otherwise be read out as one.
    #
    # The failure it prevents: Legal says the instrument establishes an
    # interest, Accounting's books do not show it, and the person is told a
    # single confident sentence assembled from both. The disagreement was the
    # most important thing either department said, and merging deleted it.
    #
    # WHAT COUNTS AS A CONTRADICTION is deliberately narrow. A checker that
    # flags everything is a checker nobody reads, and "these two answers feel
    # different" is not a finding. Only three shapes count, and each is
    # checkable without understanding either domain:
    #
    #   opposed decisions   one refuses, the other permits, on the same request
    #   same field, different value   both name a quantity and disagree past
    #                       any tolerance either of them declared
    #   self-declared       a department says contested / conflicting itself
    #
    # Anansi does NOT adjudicate. It has no domain and cannot know which
    # department is right - naming the conflict is the whole of its job here.
    # ------------------------------------------------------------------

    REFUSE_WORDS = ("refuse", "denied", "not permitted", "prohibited",
                    "impermissible", "unsupported", "refuted")
    PERMIT_WORDS = ("permitted", "allowed", "approved", "supported",
                    "established", "pass")

    @staticmethod
    def _flatten(obj, prefix="", out=None, depth=0):
        """field path -> scalar, for comparing two answers field by field."""
        out = {} if out is None else out
        if depth > 6:
            return out
        if isinstance(obj, dict):
            for k, v in obj.items():
                Anansi._flatten(v, f"{prefix}.{k}" if prefix else str(k), out, depth + 1)
        elif isinstance(obj, (int, float, str, bool)) or obj is None:
            out[prefix] = obj
        return out

    @staticmethod
    def _decision_of(flat):
        """-> 'refuse' | 'permit' | None, from whatever the department called it."""
        for k, v in flat.items():
            if not isinstance(v, str):
                continue
            kl, vl = k.lower(), v.lower()
            if not any(w in kl for w in ("decision", "status", "conclusion", "verdict")):
                continue
            if any(w in vl for w in Anansi.REFUSE_WORDS):
                return "refuse"
            if any(w in vl for w in Anansi.PERMIT_WORDS):
                return "permit"
        return None

    def check_cross_domain(self, prompt, answers):
        """-> a finding. Both answers kept, the conflict named, nothing merged."""
        flats = {d: self._flatten(a) for d, a in answers.items()}
        conflicts = []

        # 1. Opposed decisions.
        decisions = {d: self._decision_of(f) for d, f in flats.items()}
        stated = {d: v for d, v in decisions.items() if v}
        if len(set(stated.values())) > 1:
            conflicts.append({
                "kind": "opposed_decisions",
                "detail": {d: v for d, v in stated.items()},
                "why": ("One department permits what another refuses, on the same "
                        "request. Neither is overruled here - Anansi practises no "
                        "domain and cannot say which is right."),
            })

        # 2. A department saying so itself.
        for d, f in flats.items():
            for k, v in f.items():
                if isinstance(v, str) and v.lower() in ("contested", "conflicting"):
                    conflicts.append({"kind": "self_declared", "domain": d,
                                      "field": k, "value": v,
                                      "why": f"{d} reported this as {v} on its own."})

        # 3. Same field, different value.
        doms = list(flats)
        for i in range(len(doms)):
            for j in range(i + 1, len(doms)):
                a, b = doms[i], doms[j]
                shared = set(flats[a]) & set(flats[b])
                for k in sorted(shared):
                    va, vb = flats[a][k], flats[b][k]
                    if va is None or vb is None or isinstance(va, bool) != isinstance(vb, bool):
                        continue
                    if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                        hi = max(abs(va), abs(vb)) or 1
                        if abs(va - vb) / hi > 0.01:
                            conflicts.append({
                                "kind": "value_mismatch", "field": k,
                                "detail": {a: va, b: vb},
                                "why": (f"Both departments named {k} and gave "
                                        f"different numbers."),
                            })
                    elif va != vb and isinstance(va, str) and len(str(va)) < 60:
                        conflicts.append({
                            "kind": "value_mismatch", "field": k,
                            "detail": {a: va, b: vb},
                            "why": f"Both departments named {k} and disagree.",
                        })

        return {
            "cross_domain": True,
            "contradicted": bool(conflicts),
            "domains": list(answers),
            "conflicts": conflicts,
            "answers": answers,
            "resolution": None if conflicts else "domains agree",
            "note": ("Both answers are kept. A contradiction is surfaced, never "
                     "resolved by outranking, and never merged into one sentence."
                     if conflicts else
                     "Both departments were asked and did not disagree."),
        }

    def route_to_orchestrator(self, prompt, metadata):
        """Kept as the name the webapp and older callers use. It no longer
        reaches an orchestrator - there is no hop to reach."""
        return self.route_direct(prompt, metadata)

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
