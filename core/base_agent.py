#!/usr/bin/env python3
"""
Mycelial Agent Base – with hook support + Registry Service integration + Logging helper
Now with JSON-RPC compatibility (handles both top-level and nested params).
Includes Tool Service integration for MCP tools and agent‑specific memory helpers.
"""
import os, json, uuid, time, requests, paho.mqtt.client as mqtt, subprocess
from datetime import datetime
from flask import Flask, request, jsonify

BASE = os.path.expanduser("~/mycelial")
CONFIG_DIR = os.path.join(BASE, "config", "agent_cards")
LOG_FILE = os.path.join(BASE, "logs", "audit.log")
REGISTRY_FILE = os.path.join(BASE, "state", "registry.json")
PENDING_DIR = os.path.join(BASE, "state", "pending_requests")

REGISTRY_SERVICE_URL = "http://localhost:8004/execute"
LOGGING_SERVICE_URL = "http://localhost:8009/log"

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
                "created": datetime.now().isoformat(),
                "pre_hook": None,
                "post_hook": None
            }
            with open(card_path, "w") as f:
                json.dump(card, f, indent=2)
            return card

    def log(self, message):
        timestamp = datetime.now().isoformat()
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

    # ---------- HOOKS ----------
    def run_pre_hook(self, task, args):
        hook_path = self.card.get("pre_hook")
        if hook_path and os.path.exists(os.path.expanduser(hook_path)):
            hook = os.path.expanduser(hook_path)
            self.log(f"Running pre‑hook: {hook}")
            try:
                subprocess.run([hook, task] + args, check=True)
            except subprocess.CalledProcessError as e:
                self.log(f"Pre‑hook failed: {e}")
                raise

    def run_post_hook(self, task, args, result):
        hook_path = self.card.get("post_hook")
        if hook_path and os.path.exists(os.path.expanduser(hook_path)):
            hook = os.path.expanduser(hook_path)
            self.log(f"Running post‑hook: {hook}")
            try:
                subprocess.run([hook, task] + args + [str(result)], check=True)
            except subprocess.CalledProcessError as e:
                self.log(f"Post‑hook failed: {e}")

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
                self.run_pre_hook(task, args)
                result = self.handle_task(task, args, sender)
                self.run_post_hook(task, args, result)
                return jsonify({"result": result})
            except Exception as e:
                self.log(f"Error: {e}")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/health", methods=["GET"])
        def health():
            return jsonify({"status": "alive", "agent": self.agent_id})

        import threading
        threading.Thread(target=lambda: self.app.run(host="0.0.0.0", port=self.port, debug=False, use_reloader=False)).start()
        self.log(f"HTTP server started on port {self.port}")

    def handle_task(self, task, args, sender):
        return f"Task '{task}' not implemented by {self.agent_id}"

    # ---------- A2A Client ----------
    def send_a2a(self, target, task, args=None):
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
            # Increased timeout to 120 seconds
            response = requests.post(url + "/execute", json=payload, timeout=120)
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
        else:
            self.log(f"MQTT connection failed with code {rc}")

    def on_mqtt_message(self, client, userdata, msg):
        payload = msg.payload.decode()
        self.log(f"MQTT received: {payload}")

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

    def forget_own_memory(self, key):
        """Delete memory from the agent's own namespace."""
        namespace = f"agent_{self.agent_id}"
        return self.send_a2a("hermes", "forget_memory", [namespace, key])
