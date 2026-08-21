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
LOGGING_SERVICE_URL = "http://localhost:8009"

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
                "search_docs",          # NEW: librarian skill
                "log_session_summary", "get_progress_summary",
                "consolidate_audit", "get_audit_digests"
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

    def _unwrap_memory_entry(self, retrieval_result):
        # _call_memory_service hits the Memory Service's REST API directly (no
        # A2A/HTTP round-trip through this agent itself), so the response is
        # flat - {"entry": {...}, "success": ...} - unlike store_own_memory/
        # retrieve_own_memory elsewhere in this codebase, which go through
        # send_a2a and get an extra "result" wrapper from that HTTP hop.
        if not isinstance(retrieval_result, dict):
            return None
        entry = retrieval_result.get("entry")
        if not isinstance(entry, dict):
            return None
        return entry.get("value")


    def _last_consolidation_marker(self):
        """Timestamp of the newest audit row already folded into memory, so a
        consolidation run reads forward instead of re-digesting the whole log."""
        res = self._call_memory_service("retrieve", "GET",
                                        {"namespace": "audit_digest", "key": "last_consolidation"})
        entry = self._unwrap_memory_entry(res)
        return entry or ""

    def _load_session_log_index(self):
        raw = self._call_memory_service("retrieve", "GET", {"namespace": "session_log", "key": "session_log_index"})
        value = self._unwrap_memory_entry(raw)
        if not value:
            return []
        try:
            index = json.loads(value)
            return index if isinstance(index, list) else []
        except Exception:
            return []

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

        # ----- NEW: session progress log -----
        # Anansi narrates progress in plain language on request; this is the log
        # of record it reads from - what got done, what's pending, what's next,
        # and what a pending item is waiting on, per work session.
        elif task == "consolidate_audit":
            # The bridge between the two record-keepers. Operational activity is
            # written to the Logging Service (TASK_COMPLETED rows) while memory
            # holds only domain facts, and nothing ever connected them - so the
            # system recorded everything it did but could not recall any of it,
            # because recall reads memory. This distils the audit trail into a
            # memory entry, which is what makes progress self-updating instead
            # of depending on someone remembering to write a summary by hand.
            since = args.get("since") or self._last_consolidation_marker()
            limit = int(args.get("limit", 500))

            try:
                resp = requests.get(f"{LOGGING_SERVICE_URL}/logs", params={"limit": limit}, timeout=15)
                entries = resp.json().get("entries", []) if resp.status_code == 200 else []
            except Exception as e:
                return {"error": f"Could not read audit log: {e}"}

            fresh = [e for e in entries if (e.get("timestamp") or "") > since] if since else entries
            if not fresh:
                return {"result": {"consolidated": 0, "since": since,
                                   "note": "No audit activity since the last consolidation."}}

            by_agent, denials, errors = {}, [], []
            for e in fresh:
                agent = e.get("agent_id", "unknown")
                meta = e.get("metadata") or "{}"
                try:
                    task_name = json.loads(meta).get("task", e.get("task", "?"))
                except Exception:
                    task_name = e.get("task", "?")
                by_agent.setdefault(agent, {}).setdefault(task_name, 0)
                by_agent[agent][task_name] += 1
                if e.get("event_type") == "GUARD_DENY":
                    denials.append({"agent": agent, "task": task_name, "at": e.get("timestamp")})
                if (e.get("level") or "").lower() in ("error", "critical"):
                    errors.append({"agent": agent, "task": task_name, "at": e.get("timestamp"),
                                   "result": (e.get("result") or "")[:200]})

            newest = max((e.get("timestamp") or "") for e in fresh)
            record = {
                "consolidated_at": datetime.now().isoformat(),
                "window_start": since,
                "window_end": newest,
                "entries_seen": len(fresh),
                "activity_by_agent": by_agent,
                "denials": denials[:20],
                "errors": errors[:20],
            }
            key = f"audit_digest_{record['consolidated_at']}"
            self._call_memory_service("store", "POST", {
                "namespace": "audit_digest", "key": key, "value": json.dumps(record), "pin": False
            })
            # Marker so the next run only reads forward, not the whole log again.
            self._call_memory_service("store", "POST", {
                "namespace": "audit_digest", "key": "last_consolidation", "value": newest, "pin": True
            })
            self.log(f"Consolidated {len(fresh)} audit entries into memory ({key})")
            return {"result": record}

        elif task == "get_audit_digests":
            limit = int(args.get("limit", 5)) if isinstance(args, dict) else 5
            try:
                resp = requests.get(f"{MEMORY_SERVICE_URL}/search",
                                    params={"q": "audit_digest_", "namespace": "audit_digest"}, timeout=10)
                rows = resp.json().get("results", []) if resp.status_code == 200 else []
            except Exception as e:
                return {"error": str(e)}
            digests = []
            for r in sorted(rows, key=lambda x: x.get("key", ""), reverse=True):
                if not r.get("key", "").startswith("audit_digest_"):
                    continue
                try:
                    digests.append(json.loads(r["value"]))
                except Exception:
                    pass
                if len(digests) >= limit:
                    break
            return {"result": digests}

        elif task == "log_session_summary":
            if not isinstance(args, dict):
                return {"error": "Usage: {session_id, accomplished, updated, started, pending, next_steps, [depends_on]}"}
            session_id = args.get("session_id") or f"session_{int(time.time())}"
            summary = {
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
                "accomplished": args.get("accomplished", []),
                "updated": args.get("updated", []),
                "started": args.get("started", []),
                "pending": args.get("pending", []),
                "next_steps": args.get("next_steps", []),
                "depends_on": args.get("depends_on", ""),
            }
            entry_key = f"summary_{summary['timestamp']}"
            self._call_memory_service("store", "POST", {
                "namespace": "session_log", "key": entry_key, "value": json.dumps(summary), "pin": True
            })
            index = self._load_session_log_index()
            index.append(entry_key)
            self._call_memory_service("store", "POST", {
                "namespace": "session_log", "key": "session_log_index", "value": json.dumps(index), "pin": True
            })
            self.log(f"Logged session summary for {session_id}")
            return {"result": "Session summary logged", "summary": summary}

        elif task == "get_progress_summary":
            limit = args.get("limit", 5) if isinstance(args, dict) else 5
            index = self._load_session_log_index()
            summaries = []
            for key in index[-limit:]:
                raw = self._call_memory_service("retrieve", "GET", {"namespace": "session_log", "key": key})
                value = self._unwrap_memory_entry(raw)
                if value:
                    try:
                        summaries.append(json.loads(value))
                    except Exception:
                        pass
            return {"result": summaries}

        else:
            return {"error": f"Unknown task: {task}"}

if __name__ == "__main__":
    hermes = HermesInterface()
    while True:
        time.sleep(60)
        hermes.heartbeat()
