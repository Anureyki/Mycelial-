#!/usr/bin/env python3
import sys
import os
import json
import time
import requests
from datetime import datetime

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.base_agent import AgentBase

BASE = os.path.expanduser("~/mycelial")
MEMORY_SERVICE_URL = "http://localhost:8007"
POLICY_SERVICE_URL = "http://localhost:8008"

class HermesInterface(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="hermes",
            role="memory",
            port=8002,
            capabilities=[
                "store_memory",
                "retrieve_memory",
                "knowledge_search",
                "update_memory",
                "forget_memory",
                "pin_memory",
                "search_docs"          # NEW: librarian skill
            ]
        )
        self.log("🧠 Hermes (Memory Intelligence + Librarian) initialized.")

    def _call_memory_service(self, endpoint, method="GET", data=None):
        url = f"{MEMORY_SERVICE_URL}/{endpoint}"
        try:
            if method == "GET":
                resp = requests.get(url, params=data, timeout=10)
            elif method == "POST":
                resp = requests.post(url, json=data, timeout=10)
            elif method == "DELETE":
                resp = requests.delete(url, json=data, timeout=10)
            else:
                return {"error": f"Unsupported method: {method}"}
            if resp.status_code == 200:
                return resp.json()
            else:
                return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        except Exception as e:
            return {"error": str(e)}

    def _ask_policy(self, namespace):
        """Ask Policy Service if this namespace should be pinned."""
        try:
            resp = requests.post(
                f"{POLICY_SERVICE_URL}/evaluate",
                json={"type": "pin", "context": {"namespace": namespace}},
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("decision") == "pin"
            return False
        except:
            return False

    def handle_task(self, task, args, sender):
        self.log(f"A2A task: {task} from {sender}")

        if task == "store_memory":
            if len(args) < 3:
                return {"error": "Usage: store_memory <namespace> <key> <value>"}
            namespace, key, value = args[0], args[1], args[2]
            should_pin = self._ask_policy(namespace)
            self.log(f"Policy says pin={should_pin} for namespace {namespace}")
            result = self._call_memory_service("store", "POST", {
                "namespace": namespace,
                "key": key,
                "value": value,
                "pin": should_pin
            })
            return result

        elif task == "retrieve_memory":
            if len(args) < 2:
                return {"error": "Usage: retrieve_memory <namespace> <key>"}
            result = self._call_memory_service("retrieve", "GET", {
                "namespace": args[0],
                "key": args[1]
            })
            return result

        elif task == "knowledge_search":
            if len(args) < 1:
                return {"error": "Usage: knowledge_search <query>"}
            result = self._call_memory_service("search", "GET", {"q": args[0]})
            return result

        elif task == "update_memory":
            if len(args) < 3:
                return {"error": "Usage: update_memory <namespace> <key> <value>"}
            result = self._call_memory_service("update", "POST", {
                "namespace": args[0],
                "key": args[1],
                "value": args[2]
            })
            return result

        elif task == "forget_memory":
            if len(args) < 2:
                return {"error": "Usage: forget_memory <namespace> <key>"}
            result = self._call_memory_service("forget", "DELETE", {
                "namespace": args[0],
                "key": args[1]
            })
            return result

        elif task == "pin_memory":
            if len(args) < 2:
                return {"error": "Usage: pin_memory <namespace> <key>"}
            namespace, key = args[0], args[1]
            result = self._call_memory_service("pin", "POST", {
                "namespace": namespace,
                "key": key
            })
            return result

        # ----- NEW: Librarian skill (search documentation) -----
        elif task == "search_docs":
            if isinstance(args, dict):
                library = args.get("library")
                query = args.get("query", "")
            else:
                # support both list and dict
                if len(args) >= 1:
                    library = args[0]
                    query = args[1] if len(args) > 1 else ""
                else:
                    library = None
                    query = ""
            if not library:
                return {"error": "Missing 'library' (e.g., python, javascript, go)"}
            self.log(f"Searching docs for library='{library}', query='{query}'")
            # Call the grounded-docs MCP server via Tool Service
            tool_args = {"library": library}
            if query:
                tool_args["query"] = query
            result = self.call_tool("grounded-docs", "search_docs", tool_args)
            # Store the result in memory (under namespace 'docs')
            self._call_memory_service("store", "POST", {
                "namespace": "docs",
                "key": f"{library}_{query}",
                "value": json.dumps(result),
                "pin": False
            })
            return {"result": result, "library": library, "query": query}

        else:
            return {"error": f"Unknown task: {task}"}

if __name__ == "__main__":
    hermes = HermesInterface()
    while True:
        time.sleep(60)
        hermes.heartbeat()
