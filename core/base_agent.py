#!/usr/bin/env python3
"""
Mycelial Agent Base – Security Agent guards + Registry Service integration + Logging helper
Now with JSON-RPC compatibility (handles both top-level and nested params).
Includes Tool Service integration for MCP tools and agent‑specific memory helpers.
"""
import hashlib
import os, re, json, uuid, time, threading, requests, paho.mqtt.client as mqtt
from datetime import datetime
from flask import Flask, request, jsonify

BASE = os.path.expanduser("~/mycelial")
CONFIG_DIR = os.path.join(BASE, "config", "agent_cards")
LOG_FILE = os.path.join(BASE, "logs", "audit.log")
REGISTRY_FILE = os.path.join(BASE, "state", "registry.json")
PENDING_DIR = os.path.join(BASE, "state", "pending_requests")
KNOWLEDGE_BASE_ROOT = os.path.join(BASE, "knowledge_base")
CAG_STATE_DIR = os.path.join(BASE, "state", "cag")

# File types read as text into the cache. Anything else is skipped (logged, not crashed on).
CAG_TEXT_EXTENSIONS = {".txt", ".md", ".json", ".csv"}
CAG_MAX_DOC_CHARS = 200_000  # guard against one huge file blowing up memory
CAG_TOKEN_RE = re.compile(r"[a-zA-Z0-9§][a-zA-Z0-9§.\-]*")

REGISTRY_SERVICE_URL = "http://localhost:8004/execute"
LOGGING_SERVICE_URL = "http://localhost:8009/log"
LOCK_FILE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "state", "LOCKED")
SECURITY_AGENT_URL = "http://localhost:9010/execute"

# Guard checks sit in front of every request, so they must be fast and must
# never be the reason a request hangs.
GUARD_TIMEOUT = 5

# Registration retry settings
REGISTRY_RETRY_ATTEMPTS = 10
REGISTRY_RETRY_DELAY = 2

CORE_CASE_TASKS = {
    "case_open", "case_list", "case_get", "case_summary", "case_timeline",
    "case_add_document", "case_add_evidence", "case_add_participant",
    "case_add_complaint", "case_set_element", "case_set_state",
    "case_complete_task", "case_void",
}


class AgentBase:
    def __init__(self, agent_id, port, capabilities, role="agent", mqtt_broker="localhost"):
        self.agent_id = agent_id
        self.port = port
        self.capabilities = capabilities
        self.role = role
        self.mqtt_broker = mqtt_broker
        self.mqtt_client = None
        self._extra_subscriptions = []

        os.makedirs(CONFIG_DIR, exist_ok=True)
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        os.makedirs(os.path.dirname(REGISTRY_FILE), exist_ok=True)
        os.makedirs(PENDING_DIR, exist_ok=True)

        self.card = self.load_or_create_card()
        self.register_agent_with_retry()
        self.setup_mqtt()
        self.start_http_server()

    def load_or_create_card(self):
        card_path = os.path.join(CONFIG_DIR, f"{self.agent_id}.json")
        if os.path.exists(card_path):
            with open(card_path, "r") as f:
                return json.load(f)
        else:
            card = {
                "agent_id": self.agent_id,
                "role": self.role,
                "version": "1.0.0",
                "capabilities": self.capabilities,
                "endpoint": "/execute",
                "port": self.port,
                "transport": ["http", "mqtt"],
                "mqtt_topics": {
                    "publish": f"mycelial/agent/{self.agent_id}/out",
                    "subscribe": f"mycelial/agent/{self.agent_id}/in"
                },
                "public_key": "",
                "owner": os.getenv("USER", "unknown"),
                "created": datetime.now().isoformat()
            }
            with open(card_path, "w") as f:
                json.dump(card, f, indent=2)
            return card

    # A single base64 image pasted into a log line is megabytes. Every agent
    # writes to the same audit.log, and 24 lines carrying photo payloads grew
    # it to 201MB - 196MB of which was base64 that also exists on disk as the
    # actual .jpg. It made the log unusable for diagnosis long before it made
    # the disk a problem: grepping it returned megabytes of encoded pixels.
    _B64_RUN = re.compile(r'[A-Za-z0-9+/]{200,}={0,2}')
    LOG_LINE_MAX = 4000

    @classmethod
    def _scrub(cls, message):
        """Strip payloads that carry no diagnostic information."""
        text = message if isinstance(message, str) else str(message)
        text = cls._B64_RUN.sub(lambda m: f"<{len(m.group(0))} bytes elided>", text)
        if len(text) > cls.LOG_LINE_MAX:
            text = text[:cls.LOG_LINE_MAX] + f"... [truncated, {len(text)} chars]"
        return text

    def log(self, message):
        timestamp = datetime.now().isoformat()
        message = self._scrub(message)
        with open(LOG_FILE, "a") as f:
            f.write(f"{timestamp} | {self.agent_id} | {message}\n")
        print(f"[{self.agent_id}] {message}")

    # ---------- Registry Service Integration ----------
    def _call_registry_service(self, task, args):
        try:
            payload = {
                "jsonrpc": "2.0",
                "method": "execute",
                "params": {"task": task, "args": args, "sender": self.agent_id},
                "id": str(uuid.uuid4())
            }
            response = requests.post(REGISTRY_SERVICE_URL, json=payload, timeout=5)
            if response.status_code == 200:
                data = response.json()
                return data.get("result")
            else:
                return None
        except Exception as e:
            self.log(f"Registry Service call failed: {e}")
            return None

    def register_agent_with_retry(self):
        info = {
            "role": self.role,
            "port": self.port,
            "capabilities": self.capabilities,
            "url": f"http://localhost:{self.port}"
        }
        payload = [self.agent_id, json.dumps(info)]
        attempt = 0
        delay = REGISTRY_RETRY_DELAY
        registered = False

        while attempt < REGISTRY_RETRY_ATTEMPTS:
            result = self._call_registry_service("register", payload)
            if result:
                self.log(f"Registered {self.agent_id} with Registry Service (attempt {attempt+1})")
                registered = True
                break
            self.log(f"Registry Service unavailable (attempt {attempt+1}/{REGISTRY_RETRY_ATTEMPTS}), retrying in {delay}s...")
            time.sleep(delay)
            attempt += 1
            delay *= 2

        if not registered:
            self.log("Registry Service still unavailable, using fallback JSON registry")

        registry = {}
        if os.path.exists(REGISTRY_FILE):
            try:
                with open(REGISTRY_FILE, "r") as f:
                    content = f.read().strip()
                    if content:
                        registry = json.loads(content)
            except (json.JSONDecodeError, ValueError):
                registry = {}
        registry[self.agent_id] = {
            "agent_id": self.agent_id,
            "role": self.role,
            "url": f"http://localhost:{self.port}",
            "capabilities": self.capabilities,
            "last_seen": datetime.now().isoformat(),
            "card_path": os.path.join(CONFIG_DIR, f"{self.agent_id}.json")
        }
        with open(REGISTRY_FILE, "w") as f:
            json.dump(registry, f, indent=2)
        self.log(f"Registered {self.agent_id} in JSON registry")
        self.announce_reference_corpus()

    def announce_reference_corpus(self):
        """Say at boot what this agent's corpus holds, and warn if it is
        unreachable.

        The load is lazy, so an agent that owned a corpus said nothing about it
        until someone happened to look a term up. accounting_agent held 2,108
        sections of the Securities Acts and Reg S-X/S-K and never logged a word
        about them, while its lookup went straight past to a web search - and
        nothing anywhere reported the mismatch. Silence is why that survived.

        So: report the corpus at startup, and if the agent holds one but never
        calls lookup_reference, say so loudly. A corpus nothing loads is
        decoration; a corpus nothing CALLS is worse, because it looks present."""
        try:
            idx = self._load_reference_docs()
        except Exception as e:
            self.log(f"reference: corpus failed to load: {e}")
            return
        n_sections = len(idx.get("by_citation", {}))
        if not n_sections:
            return
        try:
            import inspect
            src = inspect.getsource(type(self))
            wired = "lookup_reference" in src
        except (OSError, TypeError):
            wired = True          # cannot tell - do not cry wolf
        if not wired:
            self.log(f"reference: WARNING - {n_sections} sections in "
                     f"reference/{self.agent_id}/ are loaded but this agent "
                     f"never calls lookup_reference, so nothing can reach them")

    def _lookup_agent(self, agent_id):
        result = self._call_registry_service("lookup", [agent_id])
        if result:
            return result
        if os.path.exists(REGISTRY_FILE):
            try:
                with open(REGISTRY_FILE, "r") as f:
                    content = f.read().strip()
                    if content:
                        registry = json.loads(content)
                        return registry.get(agent_id)
            except:
                pass
        return None

    # ---------- Logging & Auditing Service ----------
    def _call_logging_service(self, log_data):
        try:
            response = requests.post(LOGGING_SERVICE_URL, json=log_data, timeout=3)
            return response.status_code == 200
        except Exception as e:
            self.log(f"Failed to send log to audit service: {e}")
            return False

    def generate_correlation_id(self):
        return str(uuid.uuid4())

    def log_to_audit(self, task, result, level="info", event_type="SYSTEM", metadata=None, correlation_id=None, pin=False):
        if metadata is None:
            metadata = {}
        if correlation_id is None:
            correlation_id = self.generate_correlation_id()
        result_trunc = result[:500] if result else ""
        log_entry = {
            "agent_id": self.agent_id,
            "event_type": event_type,
            "task": task,
            "result": result_trunc,
            "level": level,
            "metadata": metadata,
            "correlation_id": correlation_id,
            "namespace": metadata.get("namespace", "system")
        }
        self._call_logging_service(log_entry)
        return correlation_id

    # ---------- GUARDS ----------
    def _extract_target(self, args):
        """Best-effort resource path out of a task's args, for path-scoped
        guard rules. Args shapes vary across agents, so check the usual keys
        and give up quietly rather than guessing."""
        if isinstance(args, dict):
            for key in ("path", "file", "file_path", "target", "filename"):
                value = args.get(key)
                if isinstance(value, str):
                    return value
        return ""

    def check_guard(self, task, args, sender):
        """Ask the Security Agent whether this request may proceed.

        Replaces the old card["pre_hook"] shell-out. Returns (allowed, reason).

        Fails OPEN on transport error, matching how _lookup_agent degrades: a
        Security Agent that is down or restarting must not halt the whole swarm.
        Only an explicit allowed=False denies."""
        # The Security Agent cannot ask itself for permission to answer the
        # question - that recurses until something gives out.
        if self.agent_id == "security_agent":
            return True, "security_agent is exempt"

        # PHASE 3, third fix. Every inbound /execute pays a network round trip
        # to the Security Agent, so Hermes serving 50 memory reads inside one
        # answer made 50 identical guard calls with identical arguments and
        # identical results. That is 13,407 of them in two days, and it is the
        # single loudest edge on the interaction graph.
        #
        # ALLOW decisions are cached for a short window keyed by exactly what
        # the decision depends on. Three deliberate limits:
        #
        #  - Only ALLOW is cached. A denial is re-evaluated every time. Denials
        #    are rare, so this costs nothing, and a cached denial would keep
        #    refusing after the rule that caused it was removed.
        #  - The kill switch is checked on EVERY call, from local disk, before
        #    the cache is consulted. `touch state/LOCKED` must stop the swarm
        #    now, not within a TTL - that is the whole point of a kill switch.
        #  - 30 seconds, so an edited denylist takes effect within 30s of the
        #    reload rather than instantly. That staleness is bounded and stated;
        #    an unbounded cache here would be a security regression.
        if os.path.exists(LOCK_FILE):
            return False, "state/LOCKED is present - all requests denied"

        ck = (self.agent_id, task, sender, str(self._extract_target(args))[:120])
        now = time.time()
        cached = self._guard_cache.get(ck)
        if cached and cached[0] > now:
            return True, cached[1]

        try:
            response = requests.post(
                SECURITY_AGENT_URL,
                json={"jsonrpc": "2.0", "method": "execute", "params": {
                    "task": "check_guard",
                    "args": {"agent": self.agent_id, "task": task,
                             "target": self._extract_target(args),
                             "sender": sender},
                    "sender": self.agent_id,
                }, "id": str(uuid.uuid4())},
                timeout=GUARD_TIMEOUT,
            )
            if response.status_code != 200:
                self.log(f"Guard check returned HTTP {response.status_code}; allowing by default")
                return True, "guard unavailable"
            result = response.json().get("result", {})
            if not isinstance(result, dict) or "allowed" not in result:
                self.log("Guard check returned an unexpected shape; allowing by default")
                return True, "guard unavailable"
            allowed = bool(result["allowed"])
            reason = result.get("reason", "")
            # Cache the ALLOW only. See the note above - a cached denial would
            # keep refusing after its rule was removed, and denials are rare
            # enough that re-checking them costs nothing.
            if allowed:
                self._guard_cache[ck] = (now + self.GUARD_CACHE_SECONDS, reason)
                if len(self._guard_cache) > 2000:
                    for k, v in list(self._guard_cache.items()):
                        if v[0] <= now:
                            self._guard_cache.pop(k, None)
            return allowed, reason
        except Exception as e:
            self.log(f"Guard check failed ({e}); allowing by default")
            return True, "guard unavailable"

    # ---------- HTTP SERVER ----------
    def start_http_server(self):
        self.app = Flask(__name__)

        @self.app.after_request
        def add_cors_headers(response):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        @self.app.route("/execute", methods=["OPTIONS"])
        def execute_options():
            return "", 204

        @self.app.route("/execute", methods=["POST"])
        def execute():
            data = request.get_json(silent=True)
            if not data:
                self.log("Rejected /execute call: missing or invalid JSON body")
                return jsonify({"error": "Missing or invalid JSON body"}), 400
            # Handle both top-level and nested (JSON-RPC) payloads
            task = data.get("task")
            args = data.get("args", [])
            sender = data.get("sender", "unknown")
            # If top-level task is missing, look inside params
            if task is None and "params" in data:
                params = data["params"]
                task = params.get("task")
                args = params.get("args", [])
                sender = params.get("sender", "unknown")
            self.log(f"Received A2A: {task} from {sender}")

            self._cache_begin()
            try:
                allowed, reason = self.check_guard(task, args, sender)
                if not allowed:
                    self.log(f"Denied {task} from {sender}: {reason}")
                    self.log_to_audit(task, f"Denied: {reason}", level="warning",
                                      event_type="GUARD_DENY",
                                      metadata={"task": task, "sender": sender,
                                                "reason": reason})
                    return jsonify({"error": f"Denied: {reason}"}), 403

                # Answered by every agent, before its own dispatcher sees it,
                # so declaring a vocabulary costs an agent nothing.
                if task == "routing_terms":
                    result = self.routing_terms()
                elif task == "answer":
                    prompt = (args.get("prompt") or args.get("question") or ""
                              if isinstance(args, dict)
                              else (args[0] if isinstance(args, list) and args else ""))
                    result = self.answer(prompt) if prompt else None
                elif task == "case_event_notice":
                    result = self.case_event_notice(args if isinstance(args, dict) else {})
                elif task in CORE_CASE_TASKS:
                    # Only the SHARED vocabulary is intercepted here. A domain's
                    # own case task - accounting's case_add_obligation, legal's
                    # case_assess_elements - must reach that agent's handler.
                    # Catching every "case_*" swallowed them into "unknown case
                    # task" and the domain frames were unreachable.
                    result = self.handle_case_task(task, args if isinstance(args, dict) else {})
                elif task in ("open_differential", "add_hypothesis", "weigh_evidence",
                              "assess_differential", "decide_differential",
                              "record_differential_outcome", "list_differentials",
                              "retract_stance", "set_discriminator"):
                    result = self.handle_differential_task(
                        task, args if isinstance(args, dict) else {})
                elif task == "base_version":
                    result = self.base_version()
                elif task == "corpus_currency":
                    result = self.corpus_currency(args if isinstance(args, dict) else {})
                elif task == "refer_finding":
                    # The outbound half. receive_finding was dispatched here
                    # and this was not, so an agent could accept a referral but
                    # nothing could ask one to make one - half a pipeline that
                    # CLAUDE.md documents as inherited in both directions.
                    a = args if isinstance(args, dict) else {}
                    result = self.refer_finding(a.get("to_agent"), a.get("kind"),
                                                a.get("payload") or {},
                                                a.get("why", ""))
                elif task == "ask_peer_corpus":
                    # The third cross-domain direction, and the only one that
                    # was never wired. CLAUDE.md documents all three as
                    # inherited; `refer_finding` was found undispatched earlier
                    # today and this one had the same fault - a method with a
                    # docstring, reachable by nothing. It had run zero times in
                    # 48 hours and that read as "nobody needed it" rather than
                    # "nobody could".
                    a = args if isinstance(args, dict) else {}
                    result = {"results": self.ask_peer_corpus(
                        a.get("agent_id") or a.get("agent"),
                        a.get("term") or a.get("citation") or a.get("query"),
                        timeout=int(a.get("timeout", 20)))}
                elif task == "receive_finding":
                    result = self.receive_finding(
                        (args or {}).get("kind"), (args or {}).get("payload") or {},
                        sender) if isinstance(args, dict) else None
                elif task == "ingest":
                    result = self.ingest(args.get("prompt") or ""
                                         if isinstance(args, dict) else "")
                elif task == "describe":
                    result = {"text": self.describe(args.get("task") or "",
                                                    args.get("payload"))
                              if isinstance(args, dict) else None}
                else:
                    result = self.handle_task(task, args, sender)

                hits, misses = self._cache_end()
                self.log_to_audit(task, str(result), event_type="TASK_COMPLETED",
                                  metadata={"task": task, "sender": sender,
                                            "reads": misses, "read_cache_hits": hits})
                self.publish_event("task.completed", {"task": task, "sender": sender})
                return jsonify({"result": result})
            except Exception as e:
                self._cache_end()
                self.log(f"Error: {e}")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/health", methods=["GET"])
        def health():
            return jsonify({"status": "alive", "agent": self.agent_id})

        import threading
        from waitress import serve
        threading.Thread(target=lambda: serve(self.app, host="127.0.0.1", port=self.port, _quiet=True)).start()
        self.log(f"HTTP server started on port {self.port}")

    def handle_task(self, task, args, sender):
        return f"Task '{task}' not implemented by {self.agent_id}"

    # Words that mean a request belongs to this agent. Every agent declares its
    # own; the orchestrator holds none of them.
    #
    # Routing used to work off a keyword list kept inside Boss, which made Boss
    # carry the vocabulary of every domain it routed to - horticulture, law,
    # accounting - and made adding an agent an edit to the orchestrator. Worse,
    # the list could only contain what someone thought to type into it: "DWC"
    # was read as "Direct Water Cooker" and a strain name was explained as
    # African folklore, because the router did not know they were plant words
    # and the agent that did know was never asked.
    #
    # An agent that knows something the list cannot - the names of the plants it
    # is currently tracking - overrides this to add them at request time.
    ROUTING_TERMS = ()

    def base_version(self):
        """Fingerprint of the shared base class this PROCESS is running.

        Every agent inherits its reasoning layer from this file, so a change
        here reaches all of them - but only after each one restarts. Until then
        the swarm is running two different shared classes at once, and nothing
        says so: an agent on yesterday's base answers "Unknown task" to a verb
        added today, which is indistinguishable from a verb that was never meant
        to reach it.

        That is not hypothetical. On 2026-08-30 the differential verbs reached
        three agents of ten, and the other seven had simply been running since
        before the file changed. Comparing this hash across agents turns that
        from an invisible condition into a one-line check."""
        try:
            with open(os.path.abspath(__file__), "rb") as fh:
                data = fh.read()
        except Exception as exc:
            return {"error": f"cannot read base class: {exc}"}
        return {
            "agent_id": self.agent_id,
            "base_sha": hashlib.sha256(data).hexdigest()[:12],
            "base_mtime": datetime.fromtimestamp(
                os.path.getmtime(os.path.abspath(__file__))).isoformat(timespec="seconds"),
            "loaded_at": getattr(self, "_started_at", None),
        }

    # Terms an agent OWNS outright. A match here is not a vote to be counted
    # against other agents' votes - it is the department saying the request is
    # definitively its own, and it ends the routing decision.
    #
    # The distinction exists because a flat term list has no way to express
    # certainty, and that cost real answers: Trust declared a bare `\bwill\b`
    # and claimed "when WILL the aloe flower", while Grow - which had declared
    # `flower` and `aloe` - was outvoted by a model's guess. Hand-tuning Trust's
    # regex fixed that sentence. It did not fix the class, because nothing in
    # the mechanism let Grow say "a registered plant name is MINE, stop asking".
    #
    # What belongs here: names only this agent can own. A plant this grow is
    # actually tracking. A statute citation. An account number. Not a subject
    # word - "legal" or "trust" or "plant" are subjects, and two departments can
    # reasonably both want a subject.
    OWNS_TERMS = ()

    def routing_terms(self):
        """Regex fragments that claim a request for this agent, in two tiers.

        `terms` are ordinary claims, counted against other agents'. `owns` are
        definitive: a match means the request belongs here and the router should
        stop. `terms` is still returned unchanged so an older Boss keeps working
        - the tier is additive, not a break."""
        return {"agent": self.agent_id,
                "terms": list(self.ROUTING_TERMS),
                "owns": list(self.owns_terms())}

    def owns_terms(self):
        """Overridable so an agent can own things it only learns at runtime -
        the names of the plants it is actually tracking, the cases it holds.
        A static list cannot know those."""
        return list(self.OWNS_TERMS)

    # ---------- Shared case management ----------
    # A case is ONE object in a shared namespace, referenced by id. Every agent
    # gets these; none keeps its own copy. store_own_memory namespaces per
    # agent (agent_<id>), which is exactly the drift this avoids - four partial
    # views of a matter and no way to ask what its state is.
    def case(self, case_id=None):
        from core.case_manager import CaseManager
        return CaseManager(self, case_id)

    def case_event_notice(self, envelope):  # noqa: D401
        """Told that something happened on a case. Override to act on it.

        The default records the notice and says it only recorded it - the same
        rule as receive_finding, for the same reason: an agent that silently
        dropped a notice would look identical to one that acted on it.

        The notice carries a REFERENCE, not content. An agent that needs the
        evidence fetches the case; it is not pushed to anyone."""
        if not isinstance(envelope, dict) or not envelope.get("case_id"):
            return {"noted": False, "why": "not a case envelope"}
        return {"noted": True, "acted": False,
                "case_id": envelope.get("case_id"), "type": envelope.get("type"),
                "note": (f"{self.agent_id} recorded this {envelope.get('type')} notice but "
                         "has no handler for it yet - it is not acted on. Read the case "
                         "by id if the content matters.")}

    def handle_case_task(self, task, args):
        cm = self.case(args.get("case_id"))
        if task == "case_open":
            if not args.get("title"):
                return {"error": "case_open needs a title"}
            return cm.open_case(args["title"], args.get("kind", ""),
                                args.get("participants"), args.get("elements"))
        if task == "case_list":
            return {"cases": cm.list_cases()}
        if not args.get("case_id") and task != "case_open":
            return {"error": f"{task} needs a case_id"}
        if task == "case_get":
            return cm.get()
        if task == "case_summary":
            return cm.summary()
        if task == "case_timeline":
            return cm.timeline(limit=int(args.get("limit", 100)))
        if task == "case_add_document":
            return cm.add_document(args.get("kind", ""), args.get("title", ""),
                                   args.get("ref", ""), args.get("note", ""))
        if task == "case_add_evidence":
            for f in ("supports", "kind", "summary"):
                if not args.get(f):
                    return {"error": f"case_add_evidence needs {f}"}
            return cm.add_evidence(args["supports"], args["kind"], args["summary"],
                                   args.get("doc_id"), args.get("weight", "unweighted"))
        if task == "case_add_participant":
            return cm.add_participant(args.get("role", ""), args.get("name", ""),
                                      args.get("note", ""))
        if task == "case_add_complaint":
            return cm.add_complaint_number(args.get("number", ""), args.get("forum", ""))
        if task == "case_set_element":
            return cm.set_element(args.get("element", ""), args.get("state", ""),
                                  args.get("note", ""))
        if task == "case_set_state":
            return cm.set_state(args.get("state", ""), args.get("note", ""))
        if task == "case_void":
            return cm.void_item(args.get("kind", ""), args.get("item_id", ""),
                                args.get("reason", ""))
        if task == "case_complete_task":
            return cm.complete_task(args.get("what", ""), args.get("outcome", ""))
        return {"error": f"unknown case task {task!r}"}

    def answer(self, prompt):
        """Answer a question in this agent's domain, or None if it cannot.

        The agent chooses which of its own capabilities the question needs.
        That choice used to be made by Boss from a table of intent patterns,
        which meant a router that practises no domain was picking between
        capabilities it did not understand - and every new capability was
        reachable from exactly one branch of it."""
        return None

    # A domain agent will see things that belong to another domain: Grow
    # reading an equipment invoice that Accounting owns, Legal spotting a
    # payment obligation inside a contract. The finding travels agent to agent
    # as a MINIMAL STRUCTURED PAYLOAD - never a raw document dump, because the
    # receiving agent should not have to parse someone else's evidence, and the
    # sending agent should not be shipping a shipping address into a ledger.
    #
    # Boss is not in the middle of the consultation. It gates on thresholds,
    # which is orchestration; the consultation itself is between the two
    # domains that understand it.
    REFERRAL_THRESHOLD = 500.0        # value above which a human signs off

    # ---- Reference corpus -------------------------------------------------
    #
    # Every domain agent carries a body of codified rules in
    # reference/<agent_id>/, retrieved by EXACT headword or citation. This
    # lived only in the Legal Agent, so accounting_agent's 2,108 sections of
    # the Securities Acts and Reg S-X/S-K sat on disk reachable by nothing -
    # its lookup went cache -> web -> model and never opened the books it
    # owns. A corpus nothing loads is decoration, so the loader belongs here
    # where every agent inherits it.
    #
    # Agents whose corpus includes term->definition dictionaries name them in
    # DICTIONARY_FILES; those are read separately and skipped here.
    DICTIONARY_FILES = ()
    _refdocs = None

    def _load_reference_docs(self):
        """Index every section-bearing reference file by citation and by the
        authorities it cites. Exact keys only - CLAUDE.md is explicit that
        reference material is retrieved by headword or citation, never by
        bag-of-words similarity, because a long passage of boilerplate
        outscores a short passage that is exactly on point."""
        if self._refdocs is not None:
            return self._refdocs
        root = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "reference")
        # An agent reads its OWN corpus plus any it shares.
        #
        # Equity maxims, equitable doctrine and trust doctrine backed by case
        # law do not belong to one department: Legal argues them in court,
        # Trust construes instruments under them, and Accounting values
        # positions by them. Giving each a copy would be three sources of truth
        # and two of them would drift. So they live in reference/_shared/ and
        # nobody owns them - the same reason a case is one object.
        ref_dirs = [os.path.join(root, self.agent_id)] + [
            os.path.join(root, d) for d in getattr(self, "SHARED_CORPORA", ())]
        by_citation, by_authority, by_term, titles = {}, {}, {}, []
        names = []
        for d in ref_dirs:
            try:
                names += [(d, n) for n in sorted(os.listdir(d))]
            except FileNotFoundError:
                continue
        for ref_dir, fname in names:
            if not fname.endswith(".json") or fname in self.DICTIONARY_FILES:
                continue
            try:
                with open(os.path.join(ref_dir, fname)) as fh:
                    doc = json.load(fh)
            except Exception as e:
                self.log(f"reference: could not load {fname}: {e}")
                continue
            sections = doc.get("sections") if isinstance(doc, dict) else None
            if not sections:
                continue
            title, source = doc.get("title", fname), doc.get("source", "")
            titles.append(f"{title} ({len(sections)} sections)")
            for s in sections:
                entry = {"title": title, "source": source,
                         "citation": s.get("citation"), "page": s.get("page"),
                         # Carried so results can be ranked primary-before-
                         # secondary. It was recorded on every work and read by
                         # nothing at lookup time, so a treatise and a statute
                         # came back in whatever order the files were walked.
                         "authority_class": doc.get("authority_class"),
                         # SOURCE INTEGRITY TRAVELS WITH THE SECTION.
                         #
                         # The index used to build a fresh dict from a chosen
                         # handful of keys, so a section that recorded itself
                         # truncated arrived at the reader with that fact
                         # dropped. The corpus knew; the agent could not. This
                         # is the same failure as the ingester discarding what
                         # it knew at the moment it cut - the fact existed and
                         # the pipe leaked it - and it is why integrity had to
                         # become a property carried end to end rather than a
                         # thing a script measures afterwards.
                         "integrity": s.get("integrity"),
                         "truncated": s.get("truncated"),
                         "full_length": s.get("full_length"),
                         "text": s.get("text", "")}
                # Index a section under every form a caller might cite it by.
                # The stored citation is whatever the source wrote - CFR gives
                # "§ 1022.3", the U.S. Code gives "§ 1681i." WITH a trailing
                # period - and a single exact key meant the punctuation of the
                # source decided whether the law was reachable. Every FCRA
                # section ingested today was invisible for exactly that reason.
                #
                # These are all the SAME citation written differently, not a
                # loose match: the section sign and the trailing period are
                # typography, not identity.
                cit = str(s.get("citation") or "").strip().lower()
                if cit:
                    bare = cit.lstrip("\u00a7 ").strip().rstrip(".")
                    for form in {cit, cit.rstrip("."), bare,
                                 f"\u00a7 {bare}", f"\u00a7{bare}"}:
                        if form:
                            by_citation.setdefault(form, entry)
                for a in s.get("authorities", []) or []:
                    by_authority.setdefault(a.strip().lower(), []).append(entry)
            # Subject terms the work itself repeats, so a doctrine can be asked
            # for by name instead of by page. Exact keys only - nothing scored.
            for term, idxs in (doc.get("term_index") or {}).items():
                for i in idxs:
                    if i < len(sections):
                        s = sections[i]
                        by_term.setdefault(term, []).append(
                            {"title": title, "source": source,
                             "citation": s.get("citation"), "page": s.get("page"),
                             "text": s.get("text", "")})
        self._refdocs = {"by_citation": by_citation, "by_authority": by_authority,
                         "by_term": by_term}
        self.log(f"reference: {len(by_citation)} sections, {len(by_term)} subject terms, "
                 f"{len(by_authority)} authorities from {len(titles)} work(s): "
                 + "; ".join(titles))
        return self._refdocs

    # PRIMARY GOVERNS; SECONDARY EXPLAINS.
    #
    # The standard legal distinction, and the principal stated it twice - first
    # for Black's, then generalising: *"the dictionary is a support tool, just
    # like the Corpus Juris Secundum would also be a supporting tool."* Right.
    # A dictionary, an encyclopedia and a treatise are all SECONDARY: they say
    # what a term or doctrine is understood to mean. A statute, a regulation, a
    # court rule and a case say what the law IS.
    #
    # This was implicit in the order things happened to be checked, which is not
    # the same as being enforced. Ranking by the class the work already declares
    # makes it a property of the corpus rather than of the call order - and
    # `authority_class` was already being recorded on every work for exactly
    # this kind of decision.
    AUTHORITY_RANK = {
        "federal_statute": 0,
        "state_statute": 0,
        "regulation": 1,
        "court_rules": 1,
        "case_law": 1,
        "agency_guidance": 2,      # directs personnel, confers no rights
        "agency_instruction": 2,
        "doctrine_summary": 3,     # secondary
        "treatise": 3,             # secondary - Pomeroy, Maitland
        "dictionary": 4,           # secondary, and the oldest thing here
    }

    def _authority_rank(self, entry):
        """Lower is more authoritative. Unknown sorts last rather than first -
        a work whose class was never determined must not outrank one that
        declares itself a statute."""
        cls = str((entry or {}).get("authority_class") or "").lower()
        return self.AUTHORITY_RANK.get(cls, 5)

    def lookup_reference(self, term):
        """A citation or a case name, with its integrity attached.

        EVERY read goes through here, so this is where integrity has to be
        surfaced or it is not surfaced at all. The corpus already recorded
        whether a section was stored whole; nothing ever showed a reader, so a
        statute cut at 4,000 characters and a statute stored in full were
        indistinguishable to the agent reasoning from them - and 15 U.S.C.
        1681b was read as though its employment provisions did not exist,
        because the half containing them had been dropped at ingest.

        A wrong passage presented as authority is worse than none. A HALF
        passage presented as whole is the same failure with a quieter surface,
        and it is the one that survives review, because everything shown is
        accurate.

        So each returned entry carries `integrity`, and anything not recorded
        as complete carries a `caution` a caller cannot help but see. Unknown
        is not complete: 15,683 sections had no integrity record at all when
        this was written, and each reads as unverified rather than as fine."""
        entries = self._lookup_reference_raw(term)
        if not entries:
            return entries
        from core import source_integrity
        out = []
        for e in entries:
            if not isinstance(e, dict):
                out.append(e)
                continue
            e = dict(e)
            e["integrity"] = source_integrity.read(e)
            note = source_integrity.caution(e)
            if note:
                e["caution"] = note
                # In the text itself, because a caller that reads `text` and
                # nothing else is the normal case and must not be able to miss
                # it. A flag in a sibling field is a flag nobody sees.
                e["text"] = f"[{note}]\n\n" + str(e.get("text") or "")
            out.append(e)
        return out

    def _lookup_reference_raw(self, term):
        """Match a citation or case name. Integrity is added by the caller."""
        idx = self._load_reference_docs()
        key = (term or "").strip().lower().rstrip(".")
        if not key:
            return []
        # The FRCP index is keyed as the rules cite themselves - "9(h)",
        # "16(e)" - so a person asking for "Rule 12" matched nothing.
        key = re.sub(r'^(fed\.? ?r\.? ?civ\.? ?p\.?|frcp|rule)\s+', '', key).strip()
        # Regulations are keyed as they cite themselves - "§ 8.4", "§ 100.204" -
        # so a person typing "8.4" or "24 CFR 8.4" matched nothing and fell
        # through to public web search for a rule sitting in the corpus.
        key = re.sub(r'^\d+\s*c\.?f\.?r\.?\s*(part\s*)?', '', key).strip()
        # STATUTES HAD NO SUCH RULE, so law acquired by citation could not be
        # reached by citation. Sections are keyed as they cite themselves -
        # "§ 1681i.", "§ 1681s-2." - and "15 U.S.C. 1681i" matched nothing, fell
        # through to the cache, and then to a public web search for a statute
        # sitting on the shelf. Verified: 12 CFR 1022.3 resolved and every FCRA
        # section ingested minutes earlier did not.
        key = re.sub(r'^\d+\s*u\.?\s*s\.?\s*c\.?(\s*a\.?)?\s*', '', key).strip()
        key = re.sub(r'^(section|sec\.?|\u00a7+)\s*', '', key).strip()
        # The index keys carry the section sign; a bare number is how a person
        # types it. Try both rather than making the caller guess.
        for candidate in (key, f"\u00a7 {key}", f"\u00a7{key}"):
            if candidate in idx["by_citation"]:
                key = candidate
                break
        if key not in idx["by_citation"]:
            for variant in (f"§ {key}", f"§{key}", key.lstrip("§ ").strip()):
                if variant in idx["by_citation"]:
                    return [idx["by_citation"][variant]]
        if key in idx["by_citation"]:
            return [idx["by_citation"][key]]
        if key in idx["by_authority"]:
            return idx["by_authority"][key]
        if key in idx.get("by_term", {}):
            # Primary before secondary. A subject term can appear in a statute
            # AND in a treatise discussing it, and returning them in file-walk
            # order meant Pomeroy on equity could arrive ahead of the statute
            # that governs. Stable sort, so within a class the existing order
            # is untouched.
            return sorted(idx["by_term"][key], key=self._authority_rank)[:4]
        # A case is often cited with extra words around it ("as the tax
        # considered in Gleason v. McKay"), so match on containment in either
        # direction - but only for names that look like a case.
        # "Rule 12" normalises to "12", but the rules are indexed as they cite
        # themselves - 12(b), 12(d) - so a bare rule number has to gather its
        # subsections rather than miss.
        if re.fullmatch(r'\d{1,4}', key):
            subs = [e for c, e in idx["by_citation"].items()
                    if c == key or c.startswith(key + "(")]
            if subs:
                return subs[:6]
        if " v. " in key or " v " in key:
            out = []
            for name, entries in idx["by_authority"].items():
                if key in name or name in key:
                    out.extend(entries)
            return out[:4]
        return []

    def _unwrap_value(self, retrieval_result):
        """Canonical name for unwrapping a memory read.

        The base class defined `_unwrap_memory_value`, but shared code in
        `core/` calls `_unwrap_value` - quest_manager did, and the differential
        verbs below do. That worked only because Grow and Maintenance each
        happened to define their own copy; the other twelve agents would have
        raised AttributeError the first time any of that shared code ran on
        them. A helper that shared code depends on belongs on the base, not in
        whichever agent needed it first."""
        return self._unwrap_memory_value(retrieval_result)

    def _uid(self):
        """A unique suffix for a memory key.

        Lived in Grow only, so every base-class helper that used it worked for
        exactly one agent and raised AttributeError everywhere else - the
        differential verbs are inherited by all of them, so the first non-Grow
        caller found it. Microsecond precision because a second-granularity id
        let two writes in the same second collide."""
        return f"{int(time.time() * 1_000_000)}"

    # ---- Differential diagnosis -------------------------------------------
    # Inherited by every agent, deliberately. Collapsing an observation
    # straight into a treatment is not a horticulture mistake - it is the same
    # move as reading a statute's title as its holding, or a registry row as a
    # running process. The domain differs; the failure does not.

    def _differential_index(self):
        raw = self._unwrap_value(self.retrieve_own_memory("differential_index"))
        try:
            idx = json.loads(raw) if raw else []
            return idx if isinstance(idx, list) else []
        except Exception:
            return []

    def _load_differential(self, diff_id):
        raw = self._unwrap_value(self.retrieve_own_memory(diff_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def _save_differential(self, diff_id, diff):
        self.store_own_memory(diff_id, json.dumps(diff))
        idx = self._differential_index()
        if diff_id not in idx:
            idx.append(diff_id)
            self.store_own_memory("differential_index", json.dumps(idx))

    def handle_differential_task(self, task, args):
        try:
            from core import differential as dx
        except Exception as exc:
            return {"error": f"differential module unavailable: {exc}"}

        if task == "list_differentials":
            out = []
            for did in self._differential_index():
                d = self._load_differential(did)
                if not d:
                    continue
                if args.get("open_only") and d.get("status") != "open":
                    continue
                out.append({"id": did, "subject": d.get("subject"),
                            "observation": d.get("observation"),
                            "status": d.get("status"),
                            "opened_at": d.get("opened_at"),
                            "hypotheses": [h["name"] for h in d.get("hypotheses", [])]})
            return {"differentials": out, "count": len(out)}

        if task == "open_differential":
            if not args.get("observation"):
                return {"error": "A differential starts from an observation. "
                                 "Pass what was actually seen."}
            d = dx.new_differential(args.get("subject", "unspecified"),
                                    args["observation"],
                                    observed_by=args.get("observed_by", "principal"),
                                    domain=self.agent_id)
            for e in args.get("evidence", []) or []:
                if isinstance(e, dict):
                    dx.add_evidence(d, e.get("fact", ""), kind=e.get("kind", "observed"),
                                    value=e.get("value"), note=e.get("note", ""))
            did = f"differential_{self._uid()}"
            self._save_differential(did, d)
            return {"id": did, "state": dx.assess(d)}

        did = args.get("id")
        if not did:
            return {"error": "Pass the differential id."}
        d = self._load_differential(did)
        if d is None:
            return {"error": f"No differential {did}."}

        if task == "add_hypothesis":
            r = dx.add_hypothesis(d, args.get("name", ""), args.get("mechanism", ""),
                                  discriminator=args.get("discriminator"),
                                  discriminator_ready_in_hours=args.get(
                                      "discriminator_ready_in_hours"))
            if isinstance(r, dict) and r.get("error"):
                return r
            self._save_differential(did, d)
            return {"id": did, "state": dx.assess(d)}

        if task == "weigh_evidence":
            r = dx.weigh(d, args.get("hypothesis", ""), args.get("fact", ""),
                         args.get("stance", "neutral"))
            if isinstance(r, dict) and r.get("error"):
                return r
            self._save_differential(did, d)
            return {"id": did, "state": dx.assess(d)}

        if task == "set_discriminator":
            r = dx.set_discriminator(d, args.get("hypothesis", ""),
                                     args.get("discriminator", ""),
                                     ready_in_hours=args.get("ready_in_hours"),
                                     supersedes_note=args.get("supersedes_note", ""))
            if isinstance(r, dict) and r.get("error"):
                return r
            self._save_differential(did, d)
            return {"id": did, "state": dx.assess(d)}

        if task == "retract_stance":
            r = dx.retract_stance(d, args.get("hypothesis", ""), args.get("fact", ""),
                                  args.get("reason", ""),
                                  retracted_by=args.get("retracted_by", "principal"))
            if isinstance(r, dict) and r.get("error"):
                return r
            self._save_differential(did, d)
            return {"id": did, "state": dx.assess(d)}

        if task == "assess_differential":
            return {"id": did, "state": dx.assess(d), "decision": d.get("decision")}

        if task == "decide_differential":
            rec = dx.propose_decision(d, args.get("decision", "hold"),
                                      basis=args.get("basis", ""),
                                      changes=args.get("changes"),
                                      reassess_in_hours=args.get("reassess_in_hours"))
            self._save_differential(did, d)
            return {"id": did, "decision": rec}

        if task == "record_differential_outcome":
            if not args.get("observation"):
                return {"error": "An outcome is an observation. Pass what happened."}
            dx.record_outcome(d, args["observation"],
                              supports=args.get("supports"),
                              contradicts=args.get("contradicts"),
                              closes=bool(args.get("closes")))
            self._save_differential(did, d)
            return {"id": did, "state": dx.assess(d)}
        return {"error": f"Unhandled differential task {task}"}

    def ask_peer_corpus(self, agent_id, term, timeout=20):
        """Ask another domain for an authority this one does not hold.

        The third cross-domain direction. `refer_finding` HANDS a finding to
        another domain; `receive_finding` takes one. This ASKS - a question
        out, an answer back, no state changed on either side.

        The rule it enforces is that a domain does not keep a copy of another
        domain's authority. Accounting owns ASC, IFRS and the reporting
        regulations; Legal owns the statutes, the CFR, the state codes. When
        Accounting needs a statute it borrows it rather than shelving its own
        copy, because two copies of an authority is two sources of truth, and
        the one that drifts is always the copy.

        Returns [] when the peer holds nothing or is unreachable - a question
        that could not be asked is not an answer, and must never read as one."""
        if not agent_id or not term or agent_id == self.agent_id:
            return []
        try:
            reply = self.send_a2a(agent_id, "lookup", [term], timeout=timeout)
        except Exception as e:
            self.log(f"ask_peer_corpus: {agent_id} unreachable: {e}")
            return []
        inner, seen = reply, 0
        while isinstance(inner, dict) and "results" not in inner and "result" in inner and seen < 6:
            inner, seen = inner["result"], seen + 1
        if not isinstance(inner, dict):
            return []
        # Only a corpus answer is borrowable. The peer's own cache, its web
        # fallback and its model output are NOT authority, and passing one of
        # those back as though it were would launder an unverified answer
        # across a domain boundary - worse than returning nothing, because the
        # borrowing agent has no way to tell.
        src = str(inner.get("source") or "")
        if "corpus" not in src:
            return []
        results = inner.get("results") or []
        if not isinstance(results, list):
            return []

        # SECONDARY KNOWLEDGE IS MARKED AS SECONDARY.
        #
        # A borrowed authority came back looking exactly like a firsthand one -
        # same shape, same fields - so the moment it left this method nothing
        # could tell that Accounting was reading Legal's books rather than its
        # own. Three classes now, and they are not interchangeable:
        #
        #   primary    - this agent's own corpus. Its domain, its responsibility.
        #   secondary  - another agent's corpus, borrowed. Trusted, because that
        #                agent is the domain expert and maintains it. NOT owned.
        #   unverified - a public search. Discovery, never authority.
        #
        # The point of the middle class is that Legal acts as counsel to the
        # other departments: they keep working in accordance with current
        # regulation without each shelving a copy of it. And because Legal is
        # the one running `corpus_currency`, borrowed law is CURRENT law - which
        # is the whole reason not to cache it here. A cached copy would be
        # firsthand-looking, stale, and unowned, which is the worst of the three.
        stamped = []
        for r in results:
            if not isinstance(r, dict):
                continue
            r = dict(r)
            r["knowledge_class"] = "secondary"
            r["held_by"] = agent_id
            r["borrowed_by"] = self.agent_id
            r["borrowed_at"] = datetime.now().isoformat(timespec="seconds")
            r["provenance"] = (f"{agent_id}'s corpus, borrowed at request time and not "
                               f"cached here - {agent_id} maintains it and is the domain "
                               f"expert. Re-borrow rather than storing a copy.")
            stamped.append(r)
        return stamped

    def corpus_currency(self, args=None):
        """Is what I hold still the law? Inherited, because Legal, Trust and
        Accounting all carry statute and all go stale the same way."""
        try:
            from core.corpus_currency import survey
        except Exception as e:
            return {"error": f"currency check unavailable: {e}"}
        root = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "reference")
        dirs = [os.path.join(root, self.agent_id)] + [
            os.path.join(root, d) for d in getattr(self, "SHARED_CORPORA", ())]
        out = survey(dirs)
        out["agent"] = self.agent_id
        return out

    def refer_finding(self, to_agent, kind, payload, why=""):
        """Hand another domain something this one noticed but does not own."""
        if not to_agent or not kind:
            return {"error": "refer_finding needs a target agent and a kind"}
        envelope = {"kind": kind, "payload": payload or {},
                    "from": self.agent_id, "why": why,
                    "referred_at": datetime.now().isoformat()}
        amount = payload.get("amount") if isinstance(payload, dict) else None
        try:
            amount = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount = None
        envelope["requires_signoff"] = (
            amount is not None and amount > self.REFERRAL_THRESHOLD)
        reply = self.send_a2a(to_agent, "receive_finding", envelope, timeout=60)
        # Report what the other side actually said, not that the call returned.
        inner = reply
        seen = 0
        while isinstance(inner, dict) and "recorded" not in inner and "result" in inner and seen < 6:
            inner, seen = inner["result"], seen + 1
        accepted = isinstance(inner, dict) and inner.get("recorded")
        self.log(f"referred {kind} to {to_agent}: "
                 + ("accepted" if accepted else f"NOT recorded ({inner})"))
        return {"referred_to": to_agent, "kind": kind,
                "accepted": bool(accepted), "response": inner,
                "requires_signoff": envelope["requires_signoff"]}

    def receive_finding(self, kind, payload, sender):
        """Take a finding from another domain. Override to act on it.

        The default records it and says plainly that it only recorded it. An
        agent that silently dropped a referral would look identical to one that
        filed it, which is the false-success shape this codebase keeps hunting."""
        if not kind:
            return {"recorded": False, "why": "no kind given"}
        fid = f"finding_{int(time.time() * 1000)}"
        self.store_own_memory(fid, json.dumps(
            {"id": fid, "kind": kind, "payload": payload, "from": sender,
             "received_at": datetime.now().isoformat(), "acted": False}))
        return {"recorded": fid, "acted": False,
                "note": f"{self.agent_id} recorded this {kind} but has no handler "
                        "for it yet - it is stored, not acted on."}

    def ingest(self, prompt):
        """Absorb anything recordable in raw user input, or return None.

        Only the domain agent knows what counts as data. Grow recognises a
        reservoir reading stated in passing and records it before answering;
        Boss cannot, because it does not know what a reading looks like. It
        used to hold that parser anyway, in two copies that had drifted apart.

        Return a dict with a truthy "logged" when something was recorded."""
        return None

    def describe(self, task, payload):
        """Put this agent's own result into words, or None if it has none.

        An agent owns the vocabulary of its domain, so it owns the sentences
        too. Orchestrators that formatted domain results themselves ended up
        holding a second, drifting copy of the domain's language."""
        return None

    # ---------- A2A Client ----------
    def send_a2a(self, target, task, args=None, timeout=120):
        agent_info = self._lookup_agent(target)
        if not agent_info:
            self.log(f"Unknown agent {target}")
            return False

        url = agent_info.get("url")
        if not url:
            self.log(f"No URL for agent {target}")
            return False

        payload = {
            "jsonrpc": "2.0",
            "method": "execute",
            "params": {"task": task, "args": args or [], "sender": self.agent_id},
            "id": str(uuid.uuid4())
        }
        try:
            response = requests.post(url + "/execute", json=payload, timeout=timeout)
            self.log(f"A2A sent to {target}: {task}")
            try:
                return response.json()
            except ValueError:
                self.log(
                    f"A2A error: {target} returned non-JSON response "
                    f"(HTTP {response.status_code}): {response.text[:200]!r}"
                )
                return False
        except Exception as e:
            self.log(f"A2A error: {e}")
            return False

    # ---------- MQTT ----------
    def setup_mqtt(self):
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        try:
            self.mqtt_client.connect(self.mqtt_broker, 1883, 60)
            self.mqtt_client.loop_start()
            self.log(f"MQTT connected to {self.mqtt_broker}")
        except Exception as e:
            self.log(f"MQTT connection failed: {e}")

    def on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            topic = self.card["mqtt_topics"]["subscribe"]
            self.mqtt_client.subscribe(topic)
            self.log(f"MQTT subscribed to {topic}")
            for extra_topic in self._extra_subscriptions:
                self.mqtt_client.subscribe(extra_topic)
                self.log(f"MQTT subscribed to {extra_topic}")
        else:
            self.log(f"MQTT connection failed with code {rc}")

    def subscribe_project_events(self):
        """Opt in to Boss's project-wide event topics (mycelial/project/<id>/{stage,
        action,graph_update}, published by Boss's publish_event task). Incoming
        messages are routed to self.on_project_event() - override that in a
        subclass to react autonomously; the default just logs."""
        topics = [
            "mycelial/project/+/stage",
            "mycelial/project/+/action",
            "mycelial/project/+/graph_update",
        ]
        self._extra_subscriptions = list(set(self._extra_subscriptions) | set(topics))
        if self.mqtt_client and self.mqtt_client.is_connected():
            for t in topics:
                self.mqtt_client.subscribe(t)
        self.log("Subscribed to project events (stage, action, graph_update)")

    def on_project_event(self, project_id, event_type, data, sender):
        """Override in a subclass to react to a project event. Called for every
        message on mycelial/project/<project_id>/<event_type> once
        subscribe_project_events() has been called. Default: log only."""
        self.log(f"Project event {project_id}/{event_type} from {sender}: {json.dumps(data)[:200]}")

    def on_mqtt_message(self, client, userdata, msg):
        payload = msg.payload.decode()
        self.log(f"MQTT received on {msg.topic}: {payload}")
        if msg.topic.startswith("mycelial/project/"):
            try:
                data = json.loads(payload)
            except Exception:
                data = {"raw": payload}
            parts = msg.topic.split("/")
            project_id = parts[2] if len(parts) > 2 else None
            event_type = parts[3] if len(parts) > 3 else "unknown"
            try:
                self.on_project_event(project_id, event_type, data, data.get("sender") if isinstance(data, dict) else None)
            except Exception as e:
                self.log(f"on_project_event handler failed: {e}")

    def publish_event(self, event_type, data):
        topic = self.card["mqtt_topics"]["publish"]
        message = {
            "sender": self.agent_id,
            "event": event_type,
            "data": data,
            "timestamp": datetime.now().isoformat()
        }
        self.mqtt_client.publish(topic, json.dumps(message))
        self.log(f"Published event {event_type}")

    # ---------- Permission ----------
    def request_permission(self, target, task, args=None):
        request_id = str(uuid.uuid4())
        request_file = os.path.join(PENDING_DIR, f"{request_id}.json")
        data = {
            "request_id": request_id,
            "requester": self.agent_id,
            "target": target,
            "task": task,
            "args": args,
            "timestamp": datetime.now().isoformat(),
            "status": "pending"
        }
        with open(request_file, "w") as f:
            json.dump(data, f, indent=2)
        self.log(f"Permission requested {request_id}")
        return request_id

    def heartbeat(self):
        self._call_registry_service("heartbeat", [self.agent_id])
        if os.path.exists(REGISTRY_FILE):
            try:
                with open(REGISTRY_FILE, "r") as f:
                    registry = json.load(f)
                if self.agent_id in registry:
                    registry[self.agent_id]["last_seen"] = datetime.now().isoformat()
                    with open(REGISTRY_FILE, "w") as f:
                        json.dump(registry, f, indent=2)
            except:
                pass

    def find_agent_by_capability(self, capability):
        result = self._call_registry_service("find_capability", [capability])
        if result:
            return result.get("agent_id")
        return None

    # ---------- Tool Service Integration ----------
    def call_tool(self, server_id, tool_name, tool_args=None):
        """Call an MCP tool via the Tool Service."""
        if tool_args is None:
            tool_args = {}
        payload = {
            "task": "call_tool",
            "args": [server_id, tool_name, json.dumps(tool_args)],
            "sender": self.agent_id
        }
        try:
            # Increased timeout to 120 seconds
            response = requests.post("http://localhost:8015/execute", json=payload, timeout=120)
            if response.status_code == 200:
                return response.json().get("result", {"error": "No result"})
            else:
                return {"error": f"Tool Service error: {response.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    # ---------- Provenance ----------
    def record_provenance_event(self, operation, actor_type="agent", artifact_id=None,
                                 parent_artifact_id=None, execution_id=None, actor_id=None,
                                 model_id=None, tools_used=None, input_artifacts=None,
                                 output_artifacts=None, metadata=None, artifact_content=None):
        """Record a provenance event via the Provenance Service. actor_type
        defaults to "agent" with agent_id=self.agent_id - pass
        actor_type="human"/actor_id=<who> when recording on a human's
        behalf (e.g. an approval relayed through this agent). See
        core/provenance_schemas.py for the operation vocabulary."""
        payload = {
            "task": "record_event",
            "args": {
                "operation": operation,
                "actor_type": actor_type,
                "actor_id": actor_id,
                "agent_id": self.agent_id if actor_type == "agent" else None,
                "artifact_id": artifact_id,
                "parent_artifact_id": parent_artifact_id,
                "execution_id": execution_id,
                "model_id": model_id,
                "tools_used": tools_used,
                "input_artifacts": input_artifacts,
                "output_artifacts": output_artifacts,
                "metadata": metadata,
                "artifact_content": artifact_content,
            },
        }
        try:
            response = requests.post("http://localhost:8016/execute", json=payload, timeout=30)
            if response.status_code == 200:
                return response.json().get("result", {"error": "No result"})
            return {"error": f"Provenance Service error: {response.status_code}: {response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}

    # ---------- Public search (delegates to PQA Agent) ----------
    def search_public(self, query):
        """Ask PQA Agent to run a public web search. Falls back to calling the
        searxng tool directly (same path PQA itself uses) if PQA is unreachable,
        so callers get a result even if that one agent is down."""
        response = self.send_a2a("pqa_agent", "search", {"query": query})
        if response and isinstance(response, dict) and not response.get("error"):
            result = response.get("result", response)
            return {"source": "pqa_agent", "query": query, "result": result}

        self.log(f"PQA Agent unavailable for search ({response}); falling back to direct tool call")
        tool_result = self.call_tool("searxng", "search", {"query": query})
        return {"source": "searxng_tool", "query": query, "result": tool_result}

    # ---------- Convenience method for Vestige ----------
    def vestige_memory(self, action, content=None, memory_id=None, reason=None, confirm=False):
        """Call Vestige's memory tool with a clean interface."""
        args = {"action": action}
        if content:
            args["content"] = content
        if memory_id:
            args["id"] = memory_id
        if reason:
            args["reason"] = reason
        if action in ("purge", "delete"):
            args["confirm"] = confirm
        return self.call_tool("vestige", "memory", args)

    # ---------- Agent‑specific Memory Helpers ----------
    # ---------- Request-scoped read cache ----------
    # PHASE 3. Measured on "how is my plant": one question produced 282 audited
    # events - 137 memory reads, each paying a guard check, for 5 calls of
    # actual work. 87 of the 137 returned data that had already been read
    # inside the SAME request.
    #
    # Those 87 are not the system doubting what it recorded. Nothing re-reads
    # because it distrusts the first answer - it re-reads because several code
    # paths inside one `answer()` each fetch what they need independently and
    # none of them can see that another already has. There is no shared
    # scratchpad for "what do we know right now", so every path starts from
    # nothing. That is a coordination gap, not a confidence one.
    #
    # The distinction matters because it decides the fix. Doubt would be
    # answered by verification - re-reading and comparing, which is what it
    # already looks like it is doing and would make it slower still. A
    # coordination gap is answered by remembering, which is this.
    #
    # Scope is one inbound request and nothing longer. A cache that outlives
    # the request would serve a stale reading to the next question, and a grow
    # that doses off a stale volume is exactly the failure this project keeps
    # finding. It dies when the request does.
    _req = threading.local()
    # (agent, task, sender, target) -> (expires_at, reason). ALLOW only.
    _guard_cache = {}
    GUARD_CACHE_SECONDS = 30

    def _cache_begin(self):
        self._req.reads = {}
        self._req.hits = 0
        self._req.misses = 0

    def _cache_end(self):
        stats = (getattr(self._req, "hits", 0), getattr(self._req, "misses", 0))
        self._req.reads = None
        return stats

    def _cache_invalidate(self, key):
        """A write makes the cached read wrong. Anything that stores must drop
        the key, or a read-after-write inside one request returns the value
        from before the write - which is worse than any number of extra reads,
        because it is silently wrong rather than merely slow."""
        reads = getattr(self._req, "reads", None)
        if isinstance(reads, dict):
            reads.pop(key, None)

    def store_own_memory(self, key, value, pin=False):
        """Store memory in the agent's own namespace (agent_<agent_id>)."""
        namespace = f"agent_{self.agent_id}"
        # Hermes accepts pin as 4th argument (boolean string)
        pin_str = str(pin).lower()
        self._cache_invalidate(key)
        return self.send_a2a("hermes", "store_memory", [namespace, key, value, pin_str])

    def retrieve_own_memory(self, key):
        """Retrieve memory from the agent's own namespace.

        Served from the request-scoped cache when this request has already read
        the key. See the note above `_req` for why the scope is exactly one
        request and no longer."""
        reads = getattr(self._req, "reads", None)
        if isinstance(reads, dict) and key in reads:
            self._req.hits = getattr(self._req, "hits", 0) + 1
            return reads[key]
        namespace = f"agent_{self.agent_id}"
        value = self.send_a2a("hermes", "retrieve_memory", [namespace, key])
        if isinstance(reads, dict):
            reads[key] = value
            self._req.misses = getattr(self._req, "misses", 0) + 1
        return value

    def retrieve_own_memories(self, keys):
        """Many keys, one round trip. Returns {key: value_or_None}.

        The request cache removes RE-reads; this removes the cost of the first
        read of each key, which the cache cannot help with. Reading a grow's 25
        readings went from 25 A2A calls to one.

        Anything already in the request cache is served from there and not
        asked for again, so the two fixes compose instead of duplicating."""
        keys = [k for k in (keys or []) if k]
        if not keys:
            return {}
        reads = getattr(self._req, "reads", None)
        out, need = {}, []
        for k in keys:
            if isinstance(reads, dict) and k in reads:
                out[k] = reads[k]
                self._req.hits = getattr(self._req, "hits", 0) + 1
            else:
                need.append(k)
        if not need:
            return out
        namespace = f"agent_{self.agent_id}"
        resp = self.send_a2a("hermes", "retrieve_many", [namespace, need])
        payload = resp
        for _ in range(4):
            if isinstance(payload, dict) and "entries" not in payload and "result" in payload:
                payload = payload["result"]
            else:
                break
        entries = (payload or {}).get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, dict):
            # Batch unavailable - fall back to one at a time rather than
            # returning nothing. A performance fix that can lose data is not a
            # performance fix.
            self.log("retrieve_many unavailable; falling back to individual reads")
            for k in need:
                out[k] = self.retrieve_own_memory(k)
            return out
        for k in need:
            v = entries.get(k)
            # SHAPE MUST MATCH THE SINGLE READ EXACTLY.
            #
            # retrieve_own_memory returns what /execute sends: {"result":
            # {"entry": ...}}. The batch hands back {"entry": ...} - one level
            # shallower - so every caller's _unwrap_value looked one level too
            # deep and got None. Every reading read as absent, and the grow
            # reported having no readings at all. A faster path that returns a
            # different shape is not a faster path, it is a second API nobody
            # was told about.
            out[k] = {"result": v} if v is not None else None
            if isinstance(reads, dict):
                reads[k] = out[k]
                self._req.misses = getattr(self._req, "misses", 0) + 1
        return out

    def search_own_memory(self, query):
        """Search memory in the agent's own namespace."""
        namespace = f"agent_{self.agent_id}"
        return self.send_a2a("hermes", "knowledge_search", [namespace, query])

    # ---------- Checkpoint / resume ----------
    # Long-running work (multi-step reasoning, multi-agent chains, model loads)
    # shouldn't assume an uninterrupted session - a restart or timeout mid-task
    # loses nothing if the agent periodically checkpoints its own progress here.
    # Built on the same store_own_memory/retrieve_own_memory mechanism every
    # agent already uses, so it works identically for all of them for free.
    def _unwrap_memory_value(self, retrieval_result):
        if not isinstance(retrieval_result, dict):
            return None
        result = retrieval_result.get("result")
        if not isinstance(result, dict):
            return None
        entry = result.get("entry")
        if not isinstance(entry, dict):
            return None
        return entry.get("value")

    def save_checkpoint(self, checkpoint_id, state, status="in_progress"):
        """status: 'in_progress' | 'completed' | 'failed'. Call periodically during
        long-running work with whatever's needed to resume (e.g. which step
        finished, partial results so far)."""
        record = {
            "checkpoint_id": checkpoint_id,
            "status": status,
            "state": state,
            "updated_at": datetime.now().isoformat(),
        }
        self.store_own_memory(f"checkpoint_{checkpoint_id}", json.dumps(record))
        return record

    def load_checkpoint(self, checkpoint_id):
        """Returns the last saved checkpoint dict, or None if none exists yet."""
        raw = self._unwrap_memory_value(self.retrieve_own_memory(f"checkpoint_{checkpoint_id}"))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def clear_checkpoint(self, checkpoint_id):
        """Call once work completes successfully - a lingering 'in_progress'
        checkpoint after real completion would make a future resume attempt
        redo already-finished work."""
        self.forget_own_memory(f"checkpoint_{checkpoint_id}")

    def forget_own_memory(self, key):
        """Delete memory from the agent's own namespace."""
        namespace = f"agent_{self.agent_id}"
        return self.send_a2a("hermes", "forget_memory", [namespace, key])

    # ---------- CAG (Cache-Augmented Generation) ----------
    # Opt-in: a subclass calls self.init_cag(...) once, after super().__init__(),
    # to get a per-agent file-backed knowledge cache. Agents that never call
    # init_cag are completely unaffected - self.cache simply stays unset.
    def init_cag(self, knowledge_dir=None, cache_ttl=3600, watch_interval=None):
        """Enable the CAG layer for this agent.

        knowledge_dir: directory of source documents (default: knowledge_base/<agent_id>/).
                       Subdirectories become the doc "category" (e.g. statutes/, dictionary/).
        cache_ttl:     seconds after which cache_age() callers should consider the cache stale.
        watch_interval: if set, a background thread calls refresh_cache() every N seconds
                        (a simple mtime-poll "file watcher" - swap for inotify/watchdog later
                        without changing the public API).
        """
        self.knowledge_dir = knowledge_dir or os.path.join(KNOWLEDGE_BASE_ROOT, self.agent_id)
        self.cache_ttl = cache_ttl
        self.cache = {}          # doc_id -> {category, path, content, tokens, mtime, size}
        self.cache_loaded_at = None
        os.makedirs(self.knowledge_dir, exist_ok=True)
        os.makedirs(CAG_STATE_DIR, exist_ok=True)
        self._cag_manifest_path = os.path.join(CAG_STATE_DIR, f"{self.agent_id}_manifest.json")
        self.load_cache()
        if watch_interval:
            self._start_cache_watcher(watch_interval)

    def _cag_manifest(self):
        if os.path.exists(self._cag_manifest_path):
            try:
                with open(self._cag_manifest_path, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _write_cag_manifest(self, manifest):
        with open(self._cag_manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

    def _read_source_file(self, path):
        try:
            with open(path, "r", errors="ignore") as f:
                return f.read(CAG_MAX_DOC_CHARS)
        except Exception as e:
            self.log(f"CAG: failed to read {path}: {e}")
            return None

    def _tokenize(self, text):
        return set(m.group(0).lower() for m in CAG_TOKEN_RE.finditer(text))

    def load_cache(self):
        """Full (re)build of the in-memory cache from knowledge_dir. Safe to call repeatedly."""
        manifest = {}
        new_cache = {}
        added, skipped = 0, 0
        for root, _dirs, files in os.walk(self.knowledge_dir):
            category = os.path.relpath(root, self.knowledge_dir)
            category = "" if category == "." else category
            for fname in files:
                if fname.upper() == "README.MD":
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext not in CAG_TEXT_EXTENSIONS:
                    skipped += 1
                    continue
                path = os.path.join(root, fname)
                relpath = os.path.relpath(path, self.knowledge_dir)
                content = self._read_source_file(path)
                if content is None:
                    continue
                stat = os.stat(path)
                doc_id = relpath.replace(os.sep, "/")
                new_cache[doc_id] = {
                    "id": doc_id,
                    "category": category,
                    "path": path,
                    "content": content,
                    "tokens": self._tokenize(content) | self._tokenize(doc_id),
                    "mtime": stat.st_mtime,
                    "size": stat.st_size,
                }
                manifest[doc_id] = {
                    "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "size": stat.st_size,
                    "indexed_at": datetime.now().isoformat(),
                }
                added += 1
        self.cache = new_cache
        self.cache_loaded_at = time.time()
        self._write_cag_manifest(manifest)
        self.log(f"CAG: loaded {added} document(s) from {self.knowledge_dir} ({skipped} skipped file type)")
        return {"loaded": added, "skipped": skipped}

    def refresh_cache(self):
        """Incremental refresh: re-reads only new/changed files, drops removed ones."""
        if not hasattr(self, "cache"):
            return self.load_cache()
        seen = set()
        added, updated, removed = 0, 0, 0
        for root, _dirs, files in os.walk(self.knowledge_dir):
            category = os.path.relpath(root, self.knowledge_dir)
            category = "" if category == "." else category
            for fname in files:
                if fname.upper() == "README.MD":
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext not in CAG_TEXT_EXTENSIONS:
                    continue
                path = os.path.join(root, fname)
                relpath = os.path.relpath(path, self.knowledge_dir)
                doc_id = relpath.replace(os.sep, "/")
                seen.add(doc_id)
                stat = os.stat(path)
                cached = self.cache.get(doc_id)
                is_new_or_changed = cached is None or cached["mtime"] != stat.st_mtime
                if is_new_or_changed:
                    content = self._read_source_file(path)
                    if content is None:
                        continue
                    self.cache[doc_id] = {
                        "id": doc_id,
                        "category": category,
                        "path": path,
                        "content": content,
                        "tokens": self._tokenize(content) | self._tokenize(doc_id),
                        "mtime": stat.st_mtime,
                        "size": stat.st_size,
                    }
                    if cached is None:
                        added += 1
                    else:
                        updated += 1
        for doc_id in list(self.cache.keys()):
            if doc_id not in seen:
                del self.cache[doc_id]
                removed += 1
        manifest = {
            doc_id: {
                "last_modified": datetime.fromtimestamp(doc["mtime"]).isoformat(),
                "size": doc["size"],
                "indexed_at": datetime.now().isoformat(),
            }
            for doc_id, doc in self.cache.items()
        }
        self._write_cag_manifest(manifest)
        self.cache_loaded_at = time.time()
        if added or updated or removed:
            self.log(f"CAG: refresh - {added} added, {updated} updated, {removed} removed")
        return {"added": added, "updated": updated, "removed": removed, "total": len(self.cache)}

    PLACEHOLDER_MARKERS = ("[placeholder", "placeholder - not", "this placeholder",
                           "replace with a properly licensed", "sample_placeholder",
                           "exists only to verify")

    def _is_placeholder_doc(self, doc):
        blob = f"{doc.get('id','')} {doc.get('content','')[:400]}".lower()
        return any(m in blob for m in self.PLACEHOLDER_MARKERS)

    def query_cache(self, query, top_k=5, category=None):
        """Keyword-overlap search over the cache. Returns [] if nothing scores above zero -
        callers should treat that as 'cache lacks sufficient context' and fall back to inference."""
        if not query or not hasattr(self, "cache") or not self.cache:
            return []
        q_tokens = self._tokenize(query)
        if not q_tokens:
            return []
        scored = []
        for doc in self.cache.values():
            if category and doc["category"] != category:
                continue
            overlap = q_tokens & doc["tokens"]
            if not overlap:
                continue
            score = len(overlap) / len(q_tokens)
            scored.append((score, doc, overlap))
        # Documents that announce themselves as placeholders are test fixtures,
        # not reference material. Several knowledge_base folders are seeded with
        # files that say "[PLACEHOLDER - not from Black's Law Dictionary...]" and
        # then define terms with invented meanings; retrieval was faithfully
        # surfacing them to models under the heading "cached reference material",
        # i.e. as authority. Filtered here, in the shared retrieval path, so that
        # every agent is covered - fixing it in one agent left the identical bug
        # live in the others, which is how accounting_agent was still citing
        # irs_forms/sample_placeholder.txt hours after legal_agent stopped.
        scored = [t for t in scored if not self._is_placeholder_doc(t[1])]
        scored.sort(key=lambda t: t[0], reverse=True)
        results = []
        for score, doc, overlap in scored[:top_k]:
            snippet = self._snippet(doc["content"], overlap)
            results.append({
                "id": doc["id"],
                "category": doc["category"],
                "score": round(score, 3),
                "snippet": snippet,
                "path": doc["path"],
            })
        return results

    def _snippet(self, content, overlap_tokens, window=300):
        lower = content.lower()
        for tok in overlap_tokens:
            idx = lower.find(tok)
            if idx != -1:
                start = max(0, idx - window // 2)
                end = min(len(content), idx + window // 2)
                return ("..." if start > 0 else "") + content[start:end].strip() + ("..." if end < len(content) else "")
        return content[:window]

    def cache_manifest(self):
        """Per-document versioning info: last_modified (source file mtime) and indexed_at
        (when this agent last picked that version up)."""
        return self._cag_manifest()

    def cache_age(self):
        if not getattr(self, "cache_loaded_at", None):
            return None
        return time.time() - self.cache_loaded_at

    def cache_stats(self):
        if not hasattr(self, "cache"):
            return {"enabled": False}
        by_category = {}
        for doc in self.cache.values():
            by_category[doc["category"]] = by_category.get(doc["category"], 0) + 1
        return {
            "enabled": True,
            "knowledge_dir": self.knowledge_dir,
            "documents": len(self.cache),
            "by_category": by_category,
            "cache_age_seconds": self.cache_age(),
            "cache_ttl_seconds": self.cache_ttl,
            "stale": (self.cache_age() or 0) > self.cache_ttl,
        }

    def _start_cache_watcher(self, interval):
        def _loop():
            while True:
                time.sleep(interval)
                try:
                    self.refresh_cache()
                except Exception as e:
                    self.log(f"CAG: background refresh failed: {e}")
        threading.Thread(target=_loop, daemon=True).start()
        self.log(f"CAG: file-watch polling every {interval}s on {self.knowledge_dir}")

    def try_handle_cag_task(self, task, args):
        """Generic cache tasks every CAG-enabled agent gets for free.
        Call from the top of a subclass's handle_task(); if this returns
        not-None, return that result directly. Returns None for anything
        it doesn't recognize so the caller's own dispatch continues."""
        if not hasattr(self, "cache"):
            return None
        if task == "refresh_cache":
            return self.refresh_cache()
        if task == "cache_stats":
            return self.cache_stats()
        if task == "cache_manifest":
            return self.cache_manifest()
        if task == "query_cache":
            if not args or not args[0]:
                return {"error": "Usage: query_cache <query> [top_k]"}
            top_k = int(args[1]) if len(args) > 1 else 5
            return {"query": args[0], "results": self.query_cache(args[0], top_k=top_k)}
        return None
