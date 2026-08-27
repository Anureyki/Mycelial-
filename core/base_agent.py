#!/usr/bin/env python3
"""
Mycelial Agent Base – Security Agent guards + Registry Service integration + Logging helper
Now with JSON-RPC compatibility (handles both top-level and nested params).
Includes Tool Service integration for MCP tools and agent‑specific memory helpers.
"""
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
SECURITY_AGENT_URL = "http://localhost:9010/execute"

# Guard checks sit in front of every request, so they must be fast and must
# never be the reason a request hangs.
GUARD_TIMEOUT = 5

# Registration retry settings
REGISTRY_RETRY_ATTEMPTS = 10
REGISTRY_RETRY_DELAY = 2

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
            return bool(result["allowed"]), result.get("reason", "")
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

                self.log_to_audit(task, str(result), event_type="TASK_COMPLETED",
                                  metadata={"task": task, "sender": sender})
                self.publish_event("task.completed", {"task": task, "sender": sender})
                return jsonify({"result": result})
            except Exception as e:
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

    def routing_terms(self):
        """Regex fragments that claim a request for this agent."""
        return {"agent": self.agent_id, "terms": list(self.ROUTING_TERMS)}

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
    def store_own_memory(self, key, value, pin=False):
        """Store memory in the agent's own namespace (agent_<agent_id>)."""
        namespace = f"agent_{self.agent_id}"
        # Hermes accepts pin as 4th argument (boolean string)
        pin_str = str(pin).lower()
        return self.send_a2a("hermes", "store_memory", [namespace, key, value, pin_str])

    def retrieve_own_memory(self, key):
        """Retrieve memory from the agent's own namespace."""
        namespace = f"agent_{self.agent_id}"
        return self.send_a2a("hermes", "retrieve_memory", [namespace, key])

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
