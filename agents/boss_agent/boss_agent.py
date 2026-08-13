#!/usr/bin/env python3
import sys
import os
import time
import json
import uuid
import threading
import requests
import re
from datetime import datetime

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase
from core.graph_manager import GraphManager
from core.schemas import RELATIONSHIP_DOMAINS

# Agents that model relationships and are expected to keep the graph in sync.
RELATIONSHIP_AGENTS = ["legal_agent", "accounting_agent", "trust_agent"]

# Soft ACL for update_graph: the `sender` field is self-reported by the calling
# agent (AgentBase doesn't cryptographically authenticate A2A callers), so this
# is a basic guardrail, not a security boundary. Callers that want a real
# guarantee should get a token from Security Agent's authorize() and pass it -
# see _authorize_graph_write below.
GRAPH_WRITE_ALLOWLIST = set(RELATIONSHIP_AGENTS) | {"boss_agent"}


class BossAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="boss_agent",
            port=8000,
            capabilities=[
                "think", "store_memory", "retrieve_memory", "delegate",
                "process_request", "call_tool", "alert", "check_errors",
                "process_recommendations",
                "update_graph", "query_graph", "get_entity_relationships",
                "get_project_relationships", "aggregate_relationship_view",
                "answer_question", "publish_event",
                "refresh_cache", "query_cache", "cache_stats", "cache_manifest"
            ],
            role="orchestrator"
        )
        self.graph = GraphManager()
        # CAG: Boss's own project-state cache - static docs / contract templates
        # useful across projects, independent of any one relationship agent's cache.
        self.init_cag(cache_ttl=3600, watch_interval=300)
        self.subscribe_project_events()
        self.log("👑 Boss orchestrator started with Sentry integration + KAG (graph + cache) layer.")
        self.default_org = os.getenv("SENTRY_ORG", "your-org")
        self.default_project = os.getenv("SENTRY_PROJECT", "your-project")

    def on_project_event(self, project_id, event_type, data, sender):
        """Boss is the central orchestrator, so it records every project event to
        the audit trail (useful even for events it published itself, and for
        graph_update pings from relationship agents)."""
        self.log_to_audit(
            f"project_event:{event_type}", f"project={project_id} sender={sender} data={json.dumps(data)[:300]}",
            level="info", metadata={"namespace": f"project_{project_id}"}
        )

    def _trigger_reconcile(self):
        try:
            resp = requests.post("http://localhost:8014/reconcile", timeout=5)
            if resp.status_code == 200:
                self.log("Reconciliation triggered successfully.")
                return True
            else:
                self.log(f"Reconciliation failed: {resp.status_code}")
                return False
        except Exception as e:
            self.log(f"Reconciliation error: {e}")
            return False

    def _save_uploaded_image(self, image_base64, image_name):
        """Decode a base64 (optionally data-URL-prefixed) image and save it to
        Grow Agent's photo directory, returning a real path evaluate_leaf can
        pass to the vision pipeline. Returns None on any decode/size failure."""
        import base64
        try:
            data = image_base64
            if isinstance(data, str) and "," in data and data.strip().lower().startswith("data:"):
                data = data.split(",", 1)[1]
            raw = base64.b64decode(data, validate=False)
            if not raw:
                return None
            if len(raw) > 15 * 1024 * 1024:
                self.log("Rejected uploaded image: exceeds 15MB limit")
                return None
            safe_name = re.sub(r'[^A-Za-z0-9_.-]', '_', image_name or "upload.jpg")
            ext = os.path.splitext(safe_name)[1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".heic", ".webp"):
                ext = ".jpg"
            photos_dir = os.path.expanduser("~/mycelial/knowledge_base/grow_agent/photos")
            os.makedirs(photos_dir, exist_ok=True)
            path = os.path.join(photos_dir, f"upload_{int(time.time())}{ext}")
            with open(path, "wb") as f:
                f.write(raw)
            self.log(f"Saved uploaded image to {path} ({len(raw)} bytes)")
            return path
        except Exception as e:
            self.log(f"Failed to save uploaded image: {e}")
            return None

    def _format_response(self, task, result, sender):
        if result is None:
            return "The request did not return a result."
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            if "error" in result:
                return f"Error: {result['error']}"
            if task == "evaluate_leaf":
                inner = result.get("result", {}) if isinstance(result, dict) else {}
                inner = inner.get("result", inner) if isinstance(inner, dict) else {}
                if not isinstance(inner, dict) or not inner:
                    return "I couldn't get a read on that photo right now."
                classification = inner.get("classification", "unknown")
                lines = [inner.get("observation", "")]
                if inner.get("reason"):
                    lines.append(inner["reason"])
                if inner.get("action"):
                    lines.append(inner["action"])
                text = " ".join(l for l in lines if l)
                if classification == "problem":
                    text = f"Heads up - {text}"
                elif classification == "productive":
                    text = f"Looks healthy. {text}"
                vision_note = inner.get("vision_note")
                if vision_note and "escalated" in vision_note.lower():
                    text += " (This one was uncertain enough locally that I double-checked it more carefully.)"
                return text
            if task == "log_reading":
                inner = result.get("result", {}) if isinstance(result, dict) else {}
                reading = inner.get("reading") if isinstance(inner, dict) else None
                if not isinstance(reading, dict):
                    return "I wasn't able to log that reading."
                parts = []
                if reading.get("ppm") is not None:
                    parts.append(f"{reading['ppm']} ppm")
                if reading.get("temp") is not None:
                    parts.append(f"{reading['temp']}°C")
                if reading.get("ph") is not None:
                    parts.append(f"pH {reading['ph']}")
                detail = ", ".join(parts) if parts else "that reading"
                return f"Got it - logged {detail} for the {reading.get('stage', 'current')} stage. I'll factor it into the reservoir trend."
            if task == "evaluate":
                issues = result.get("issues_found", 0)
                files = result.get("python_files", 0)
                if issues == 0:
                    return f"Codebase evaluation complete: {files} Python files, no issues found."
                else:
                    return f"Codebase evaluation complete: found {issues} issues in {files} Python files. First few: {', '.join(result.get('details', [])[:3])}"
            elif task == "reason" or task == "think":
                return result.get("result", "No specific result provided.")
            elif task == "check_errors":
                errors = result.get("result", {})
                if isinstance(errors, dict) and "error" in errors:
                    return f"Sentry check failed: {errors['error']}"
                return f"Sentry check completed. Details: {json.dumps(errors)[:200]}"
            elif task == "call_tool":
                return f"Tool call result: {json.dumps(result.get('result', result))}"
            elif task == "search" or task == "search_web":
                return result.get("result", "Search completed.")
            elif task == "fix_code":
                if result.get("success"):
                    return f"Code fixed successfully: {result.get('fixed_code', '')[:200]}"
                else:
                    return f"Code fix failed. Verification: {result.get('verification', 'unknown error')}"
            elif task == "generate_recommendations":
                recs = result.get("recommendations", [])
                if not recs:
                    return "No issues found. The system is healthy."
                msg = f"Analysis complete: found {len(recs)} recommendations.\n"
                for rec in recs[:3]:
                    msg += f"- {rec.get('issue')} (criticality: {rec.get('criticality')})\n"
                if len(recs) > 3:
                    msg += f"... and {len(recs)-3} more."
                return msg
            elif task == "fetch_repo":
                if "result" in result:
                    return result["result"]
                else:
                    return "Repo summary available."
            elif task == "progress_recap":
                inner = result.get("result", {}) if isinstance(result, dict) else {}
                summaries = inner.get("result", []) if isinstance(inner, dict) else []
                if not summaries:
                    return "I don't have any recorded progress to recap yet."
                latest = summaries[-1]
                lines = []
                accomplished = latest.get("accomplished") or []
                if accomplished:
                    lines.append("Since we last checked in, I've " + "; ".join(accomplished).lower() + ".")
                pending = latest.get("pending") or []
                if pending:
                    lines.append("Still pending: " + ", ".join(pending) + ".")
                next_steps = latest.get("next_steps") or []
                if next_steps:
                    lines.append("Next up: " + ", ".join(next_steps) + ".")
                depends_on = latest.get("depends_on")
                if depends_on:
                    lines.append(f"That's waiting on: {depends_on}.")
                if len(summaries) > 1:
                    lines.append(f"({len(summaries)} recent sessions on record - ask for more detail if you want the full history.)")
                return " ".join(lines) if lines else "Nothing notable to report from the last session."
            elif task == "cleanup_routine":
                inner = result.get("result", {}) if isinstance(result, dict) else {}
                data = inner.get("result", {}) if isinstance(inner, dict) else {}
                if not isinstance(data, dict) or not data:
                    return "I couldn't run the cleanup routine right now."
                cleared = data.get("cleared", [])
                needs_confirmation = data.get("needs_confirmation", [])
                lines = []
                if cleared:
                    lines.append(f"Cleared {len(cleared)} unused test/build item(s) that weren't tied to anything active.")
                else:
                    lines.append("Nothing unused to clear right now.")
                if needs_confirmation:
                    names = ", ".join(img.get("tag", "?") for img in needs_confirmation[:5])
                    lines.append(f"I also found {len(needs_confirmation)} unused item(s) that reference known project infrastructure ({names}) - let me know if you want those removed too, since they're rebuildable but not currently disposable-looking.")
                return " ".join(lines)
            elif task == "purchase_recommendation":
                inner = result.get("result", {}) if isinstance(result, dict) else {}
                rec = inner.get("result", {}) if isinstance(inner, dict) else {}
                if not isinstance(rec, dict) or not rec:
                    return "I couldn't put together a recommendation for that."
                constraint = rec.get("budget_constraint", {}) or {}
                item = rec.get("item", "that")
                cost = rec.get("estimated_cost", 0)
                if rec.get("requires_escalation"):
                    if constraint.get("within_budget") is False:
                        return (
                            f"I'd recommend picking up {item} (about ${cost:.2f}), but there isn't enough "
                            f"discretionary budget available right now (${constraint.get('available_discretionary', 0):.2f} free). "
                            f"Let me know if you want to move funds or go ahead anyway."
                        )
                    return f"I'd recommend {item} (about ${cost:.2f}) - that's above the amount I'll auto-approve, so I'm holding it for your go-ahead."
                if constraint.get("within_budget") is True:
                    return f"I'd recommend picking up {item} - about ${cost:.2f}, and that's within your discretionary budget."
                return f"I'd recommend {item} (about ${cost:.2f}). {constraint.get('note', '')}".strip()
            elif task == "grow_status":
                status_resp = result.get("status", {}) if isinstance(result, dict) else {}
                if isinstance(status_resp, dict) and "error" in status_resp:
                    return "I couldn't reach the grow system right now."
                status_inner = status_resp.get("result", status_resp) if isinstance(status_resp, dict) else {}
                r = status_inner.get("result", status_inner) if isinstance(status_inner, dict) else {}
                if not isinstance(r, dict) or not r:
                    return "I don't have any grow data yet."

                history_resp = result.get("history", {}) if isinstance(result, dict) else {}
                history_inner = history_resp.get("result", {}) if isinstance(history_resp, dict) else {}
                history = history_inner.get("result", {}) if isinstance(history_inner, dict) else {}
                timeline = history.get("timeline", []) if isinstance(history, dict) else []

                # Lead with an alert only if the MOST RECENT check of that type is
                # still unresolved - a fresh stable reservoir_eval must supersede an
                # older warning, not get skipped past while hunting further back in
                # history for the last time something looked bad. The underlying
                # observation/reason/action/confidence shape already carries everything
                # needed to narrate this in plain language, with no agent/task names.
                alert_line = None
                latest_by_type = {}
                for entry in reversed(timeline):
                    etype = entry.get("type")
                    if etype in ("reservoir_eval", "leaf_eval", "stage_eval") and etype not in latest_by_type:
                        latest_by_type[etype] = entry

                reservoir_entry = latest_by_type.get("reservoir_eval")
                if reservoir_entry:
                    rec = reservoir_entry.get("data", {}).get("recommendation", {})
                    if rec.get("stability_band") in ("warning", "critical"):
                        urgency = "I'd address this now" if rec.get("stability_band") == "critical" else "I recommend addressing it today"
                        alert_line = f"{rec.get('observation', 'Something in the reservoir needs attention.')} {urgency}."

                if not alert_line:
                    leaf_entry = latest_by_type.get("leaf_eval")
                    if leaf_entry:
                        rec = leaf_entry.get("data", {}).get("recommendation", {})
                        if rec.get("classification") == "problem":
                            alert_line = f"{rec.get('observation', 'A leaf issue was spotted.')} {rec.get('action', '')}".strip()

                if not alert_line:
                    stage_entry = latest_by_type.get("stage_eval")
                    if stage_entry:
                        rec = stage_entry.get("data", {}).get("recommendation", {})
                        if rec.get("classification") in ("decline", "regression"):
                            alert_line = f"{rec.get('observation', '')} {rec.get('action', '')}".strip()

                if alert_line:
                    lines = [alert_line]
                else:
                    stage = r.get("current_stage", "unknown")
                    strain = r.get("current_strain")
                    plant_label = f"Your {strain}" if strain else "Your plant"
                    lines = [f"{plant_label} is in the {stage} stage and everything looks stable."]

                nutrients = r.get("current_nutrients")
                if isinstance(nutrients, dict) and nutrients.get("nutrients"):
                    n_str = ", ".join(f"{k} {v}" for k, v in nutrients["nutrients"].items())
                    lines.append(f"Current feed: {n_str}.")

                for p in r.get("other_plants") or []:
                    lines.append(f"Also coming along: {p.get('strain', 'another plant')}, {p.get('stage', 'unknown')} stage.")

                reminders = r.get("pending_reminders") or []
                if reminders:
                    reminder_str = "; ".join(f"{rem.get('title')} (due {rem.get('target_date')})" for rem in reminders)
                    lines.append(f"Also on your list: {reminder_str}.")

                return "\n".join(lines)
            elif task == "system_status":
                alive = result.get("alive", [])
                dead = result.get("dead", [])
                total = result.get("total_registered", 0)
                projects = result.get("projects", [])
                lines = [f"{len(alive)} of {total} registered agents are up: {', '.join(alive) if alive else 'none'}."]
                if dead:
                    lines.append(f"Not responding: {', '.join(dead)}.")
                if projects:
                    lines.append(f"Active projects tracked: {', '.join(projects)}.")
                else:
                    lines.append("No projects currently tracked in the relationship graph.")
                return "\n".join(lines)
            elif task == "analyze_relationship_document":
                legal_resp = result.get("legal_result", {}) if isinstance(result, dict) else {}
                legal_doc = legal_resp.get("result", {}) if isinstance(legal_resp, dict) else {}
                accounting_resp = result.get("accounting_result", {}) if isinstance(result, dict) else {}
                accounting_doc = accounting_resp.get("result", {}) if isinstance(accounting_resp, dict) else {}

                has_legal = isinstance(legal_doc, dict) and legal_doc.get("entity_a")
                has_financial = isinstance(accounting_doc, dict) and (accounting_doc.get("creditor") or accounting_doc.get("debtor"))

                if not has_legal and not has_financial:
                    return "I couldn't find a clear relationship or financial terms in that text - can you share more detail?"

                lines = []
                if has_legal:
                    obligations = ", ".join(legal_doc.get("obligations", [])) or "none stated"
                    lines.append(
                        f"This looks like a {legal_doc.get('relationship_type', 'relationship')} between "
                        f"{legal_doc.get('entity_a', '?')} and {legal_doc.get('entity_b', '?')}, with obligations: {obligations}."
                    )
                if has_financial:
                    lines.append(
                        f"Financially, it's a {accounting_doc.get('instrument_type', 'instrument')} - "
                        f"{accounting_doc.get('creditor', '?')} is owed by {accounting_doc.get('debtor', '?')}, "
                        f"amount {accounting_doc.get('principal_amount', 'unspecified')}."
                    )
                lines.append("I've recorded this so I can reference it if it comes up again.")
                return " ".join(lines)
            else:
                return json.dumps(result, indent=2)
        if isinstance(result, list):
            if len(result) == 0:
                return "No results returned."
            return "\n".join([str(item) for item in result[:5]]) + (f"\n... and {len(result)-5} more" if len(result) > 5 else "")
        return str(result)

    # ---------- KAG: graph write authorization ----------
    def _authorize_graph_write(self, sender, args):
        """If a token is supplied, verify it with Security Agent's real authorize()
        flow. Otherwise fall back to the soft sender allowlist (self-reported,
        not cryptographically verified - see GRAPH_WRITE_ALLOWLIST comment)."""
        token = args.get("token") if isinstance(args, dict) else None
        if token:
            resp = self.send_a2a("security_agent", "authorize", {"token": token, "action": "update_graph"})
            result = resp.get("result") if isinstance(resp, dict) else None
            if isinstance(result, dict) and result.get("authorized"):
                return True, None
            return False, "Token did not authorize update_graph"
        if sender in GRAPH_WRITE_ALLOWLIST:
            return True, None
        return False, (
            f"'{sender}' is not authorized to call update_graph. Get a token from "
            f"security_agent.issue_token and pass it as args.token, or call from an "
            f"allowlisted agent ({', '.join(sorted(GRAPH_WRITE_ALLOWLIST))})."
        )

    # ---------- KAG: relationship agent fan-out ----------
    def _fanout(self, task, payload):
        """Call `task` with `payload` on every known relationship agent, in parallel-ish
        (sequential A2A calls, timeouts already bounded by send_a2a). Returns
        {agent_id: response_or_error}."""
        results = {}
        for agent_id in RELATIONSHIP_AGENTS:
            resp = self.send_a2a(agent_id, task, payload)
            results[agent_id] = resp if resp else {"error": f"{agent_id} unreachable or errored"}
        return results

    def _publish_project_event(self, project_id, event_type, data):
        topic = f"mycelial/project/{project_id}/{event_type}"
        message = {
            "sender": self.agent_id,
            "project_id": project_id,
            "event_type": event_type,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }
        self.mqtt_client.publish(topic, json.dumps(message))
        self.log(f"Published project event on {topic}")
        return topic

    def _extract_mentioned_entities(self, prompt):
        """Cheap entity extraction: does any known graph node's id appear (case-insensitive,
        whole-word-ish) in the prompt text? No NER model - deliberately simple for Phase 1."""
        prompt_lower = prompt.lower()
        try:
            rows = self.graph.query_graph(
                "SELECT id, type FROM nodes WHERE type IN ('entity', 'project') LIMIT 500"
            )
        except Exception as e:
            self.log(f"answer_question: graph lookup failed: {e}")
            return []
        return [r["id"] for r in rows if r["id"] and r["id"].lower() in prompt_lower]

    def _get_system_status(self):
        """Aggregate live health across every registered agent, plus any
        active projects tracked in the relationship graph."""
        try:
            resp = requests.post(
                "http://localhost:8004/execute",
                json={"task": "list_agents", "args": [], "sender": self.agent_id},
                timeout=5
            )
            agents = resp.json().get("result", []) if resp.status_code == 200 else []
        except Exception as e:
            self.log(f"system_status: registry lookup failed: {e}")
            agents = []

        alive, dead = [], []
        for agent in agents:
            agent_id = agent.get("agent_id")
            url = agent.get("url")
            if not agent_id or not url:
                continue
            try:
                h = requests.get(f"{url}/health", timeout=2)
                (alive if h.status_code == 200 else dead).append(agent_id)
            except Exception:
                dead.append(agent_id)

        projects = []
        try:
            rows = self.graph.query_graph("SELECT id FROM nodes WHERE type = 'project' LIMIT 100")
            projects = [r["id"] for r in rows if r.get("id")]
        except Exception as e:
            self.log(f"system_status: project graph lookup failed: {e}")

        return {
            "alive": sorted(alive),
            "dead": sorted(dead),
            "total_registered": len(agents),
            "projects": projects
        }

    def handle_task(self, task, args, sender):
        self.log(f"Task: {task} from {sender} with args: {args}")

        if task == "update_graph":
            authorized, reason = self._authorize_graph_write(sender, args if isinstance(args, dict) else {})
            if not authorized:
                self.log_to_audit("update_graph", f"REJECTED from {sender}: {reason}", level="warning")
                return {"error": reason}
            action = args.get("action")
            try:
                if action == "add_node":
                    node = self.graph.add_node(args["id"], args["type"], args.get("properties", {}))
                    result = {"result": "node added", "node": node}
                elif action == "add_edge":
                    edge = self.graph.add_edge(
                        args["from_id"], args["to_id"], args["rel_type"],
                        args.get("properties", {}), dedupe=args.get("dedupe", True)
                    )
                    result = {"result": "edge added", "edge": edge}
                elif action == "update_node":
                    node = self.graph.update_node(args["id"], args.get("properties", {}))
                    result = {"result": "node updated", "node": node}
                elif action == "ingest_relationship":
                    rel_id = self.graph.ingest_relationship(args["relationship"], source_agent=sender)
                    result = {"result": "relationship ingested", "relationship_id": rel_id}
                else:
                    return {"error": f"Unknown update_graph action: {action}"}
            except KeyError as e:
                return {"error": f"Missing required field: {e}"}
            self.log_to_audit("update_graph", f"{action} by {sender}", level="info")
            if isinstance(args, dict) and args.get("project_id"):
                self._publish_project_event(args["project_id"], "graph_update", {"action": action, "by": sender})
            return result

        elif task == "query_graph":
            sql = args.get("sql") or args.get("query")
            if not sql:
                return {"error": "Usage: query_graph {sql: '<SELECT ...>', params: [...]}"}
            try:
                rows = self.graph.query_graph(sql, args.get("params"))
                return {"rows": rows, "count": len(rows)}
            except ValueError as e:
                return {"error": str(e)}

        elif task == "get_entity_relationships":
            entity_id = args.get("entity_id")
            if not entity_id:
                return {"error": "Missing entity_id"}
            graph_view = self.graph.get_entity_relationships(entity_id)
            agent_views = self._fanout("find_relationships", [entity_id])
            return {"entity_id": entity_id, "graph": graph_view, "agent_relationships": agent_views}

        elif task == "get_project_relationships":
            project_id = args.get("project_id")
            if not project_id:
                return {"error": "Missing project_id"}
            graph_view = self.graph.get_project_relationships(project_id)
            agent_views = self._fanout("find_relationships_by_project", [project_id])
            return {"project_id": project_id, "graph": graph_view, "agent_relationships": agent_views}

        elif task == "aggregate_relationship_view":
            entity_id = args.get("entity_id")
            if not entity_id:
                return {"error": "Missing entity_id"}
            agent_views = self._fanout("find_relationships", [entity_id])
            view = {"entity_id": entity_id}
            for domain, agent_id in (("legal_roles", "legal_agent"),
                                      ("financial_roles", "accounting_agent"),
                                      ("trust_roles", "trust_agent")):
                resp = agent_views.get(agent_id, {})
                result = resp.get("result") if isinstance(resp, dict) else None
                view[domain] = result.get("relationships", result) if isinstance(result, dict) else (result or [])
            graph_view = self.graph.get_entity_relationships(entity_id)
            view["graph_connections"] = len(graph_view.get("edges", []))
            view["connected_node_ids"] = [n["id"] for n in graph_view.get("connected_nodes", [])]
            view["disclaimer"] = (
                "Aggregated automatically from Legal, Accounting, and Trust Agent records plus "
                "the local relationship graph, for informational purposes only - not legal, "
                "tax, or financial advice."
            )
            return view

        elif task == "analyze_relationship_document":
            # Drives the cross-agent workflow end-to-end from one request: Legal Agent
            # identifies contractual obligations, Accounting Agent identifies financial
            # consequences, both already push to the shared relationship graph
            # (domain="legal" / domain="financial" respectively), then this pulls the
            # combined view back via the same get_project_relationships used above.
            text = args.get("text") if isinstance(args, dict) else (args[0] if args else None)
            if not text:
                return {"error": "Missing text"}
            project_id = args.get("project_id") if isinstance(args, dict) else ""
            if not project_id:
                project_id = f"doc_{uuid.uuid4().hex[:8]}"

            # Run both extractions in parallel - each is a single local-model call, so
            # running them sequentially roughly doubled latency for no reason. This was
            # the direct cause of Anansi's A2A hop timing out under load even though
            # the underlying work completed correctly when called directly.
            results = {}

            def _call(key, agent_id, agent_task):
                # Local-model extraction can run well past the 120s default under
                # load (observed directly this session) - give it real headroom
                # since these two calls now run in parallel, not stacked.
                results[key] = self.send_a2a(agent_id, agent_task, [text, project_id], timeout=240)

            legal_thread = threading.Thread(target=_call, args=("legal", "legal_agent", "model_relationship"))
            accounting_thread = threading.Thread(target=_call, args=("accounting", "accounting_agent", "parse_financial_instrument"))
            legal_thread.start()
            accounting_thread.start()
            legal_thread.join()
            accounting_thread.join()
            legal_response = results.get("legal")
            accounting_response = results.get("accounting")
            combined = self.graph.get_project_relationships(project_id)

            return {
                "project_id": project_id,
                "legal_result": legal_response,
                "accounting_result": accounting_response,
                "combined_graph_view": combined,
            }

        elif task == "answer_question":
            prompt = args.get("prompt", "")
            if not prompt:
                return {"error": "Missing prompt"}
            entities = self._extract_mentioned_entities(prompt)
            graph_facts = [self.graph.get_entity_relationships(e) for e in entities]
            cache_hits = self.query_cache(prompt, top_k=3) if hasattr(self, "cache") else []
            context_parts = []
            if graph_facts:
                context_parts.append("Known graph relationships:\n" + json.dumps(graph_facts, indent=2)[:3000])
            if cache_hits:
                context_parts.append("Cached reference material:\n" + "\n".join(
                    f"- [{h['id']}] {h['snippet']}" for h in cache_hits
                ))
            context = "\n\n".join(context_parts)
            reasoning_prompt = (
                (context + "\n\n" if context else "") +
                f"Question: {prompt}\n\n"
                "Answer using ONLY the graph relationships and cached material above where "
                "relevant. If they don't contain enough information, say so plainly rather than "
                "guessing. This is informational only, not legal, tax, or financial advice."
            )
            response = self.send_a2a("coding_agent", "reason", {"prompt": reasoning_prompt})
            answer = self._format_response("reason", response, "coding_agent")
            return {
                "question": prompt,
                "entities_recognized": entities,
                "cache_sources": [h["id"] for h in cache_hits],
                "answer": answer,
                "disclaimer": "Informational only, not legal, tax, or financial advice.",
            }

        elif task == "publish_event":
            project_id = args.get("project_id")
            event_type = args.get("event_type", "action")
            data = args.get("data", {})
            if not project_id:
                return {"error": "Missing project_id"}
            topic = self._publish_project_event(project_id, event_type, data)
            return {"result": "published", "topic": topic}

        elif task == "refresh_cache":
            return self.refresh_cache()

        elif task == "cache_stats":
            return self.cache_stats()

        elif task == "cache_manifest":
            return self.cache_manifest()

        elif task == "query_cache":
            query = args.get("query")
            if not query:
                return {"error": "Usage: query_cache {query: '...', top_k: 5}"}
            return {"query": query, "results": self.query_cache(query, top_k=args.get("top_k", 5))}

        elif task == "think":
            thought = args.get("thought", "")
            self.store_own_memory("last_thought", thought)
            self.log_to_audit("THOUGHT", f"Thought: {thought}", level="info")
            return {"result": f"Thought stored: {thought}"}

        elif task == "store_memory":
            key = args.get("key")
            value = args.get("value")
            pin = args.get("pin", False)
            if not key or value is None:
                return {"error": "Missing key or value"}
            self.store_own_memory(key, value, pin=pin)
            return {"result": f"Stored {key}"}

        elif task == "retrieve_memory":
            key = args.get("key")
            if not key:
                return {"error": "Missing key"}
            value = self.retrieve_own_memory(key)
            return {"result": value}

        elif task == "delegate":
            target = args.get("target")
            subtask = args.get("task")
            subargs = args.get("args", {})
            if not target or not subtask:
                return {"error": "Missing target or task"}
            self.log(f"Delegating {subtask} to {target}")
            response = self.send_a2a(target, subtask, subargs)
            return {"delegated": True, "response": response}

        elif task == "process_request":
            if isinstance(args, dict):
                prompt = args.get("prompt", "")
                metadata = args.get("metadata", {})
            elif isinstance(args, list) and len(args) > 0:
                first = args[0]
                if isinstance(first, str) and first.startswith('{'):
                    try:
                        payload = json.loads(first)
                        prompt = payload.get("prompt", "")
                        metadata = payload.get("metadata", {})
                    except:
                        prompt = first
                        metadata = {}
                else:
                    prompt = str(first)
                    metadata = {}
            else:
                return {"error": "Invalid args format"}

            image_base64 = metadata.get("image_base64") if isinstance(metadata, dict) else None

            if not prompt and not image_base64:
                return {"error": "Missing prompt"}

            self.log(f"Received user prompt: {prompt[:80]}...")

            # --- Image upload (plant photo) ---
            # Only Grow Agent has a vision pipeline today, so any uploaded image
            # routes there. Kept as its own branch (not folded into the generic
            # image_base64 handling below) so a future second vision-capable
            # agent can be added by branching on prompt content/metadata here
            # without touching the upload/save plumbing.
            if image_base64:
                self.log("User uploaded an image - routing to grow_agent's vision pipeline")
                photo_path = self._save_uploaded_image(image_base64, metadata.get("image_name", "upload.jpg"))
                if not photo_path:
                    return {"result": "I couldn't process that image - it may be corrupted, empty, or too large (15MB max)."}
                response = self.send_a2a(
                    "grow_agent", "evaluate_leaf",
                    {"plant_id": metadata.get("plant_id", "current_plant"), "photo_path": photo_path},
                    timeout=180
                )
                text = self._format_response("evaluate_leaf", response, "grow_agent")
                return {"result": text, "evidence": response}

            # --- README / documentation ---
            if "readme" in prompt.lower() or "documentation" in prompt.lower():
                self.log("User asking about README – reading and summarizing")
                content = self.send_a2a("coding_agent", "read_file", {"path": "~/mycelial/README.md"})
                if isinstance(content, dict) and "result" in content:
                    summary_prompt = f"Summarize the following README content in plain text, without the ASCII architecture diagram. Focus on the purpose, core agents, and services:\n\n{content['result']}"
                    summary = self.send_a2a("coding_agent", "reason", {"prompt": summary_prompt})
                    text = self._format_response("reason", summary, "coding_agent")
                    return {"result": text}
                else:
                    return {"result": "Could not read README."}

            # --- GitHub repo ---
            if "github" in prompt.lower() or "repo" in prompt.lower() or "repository" in prompt.lower():
                self.log("User asking about a GitHub repo – delegating to coding_agent.fetch_repo")
                url_match = re.search(r'https?://github\.com/[^\s]+', prompt)
                if not url_match:
                    return {"result": "Please provide a GitHub URL."}
                url = url_match.group(0)
                response = self.send_a2a("coding_agent", "fetch_repo", {"url": url})
                text = self._format_response("fetch_repo", response, "coding_agent")
                return {"result": text}

            # --- Progress / session recap (from Hermes's session log) ---
            # Checked before "System status" below since phrasing like "status
            # update" could otherwise match the wrong branch - progress recap
            # needs more specific phrasing to win.
            if any(keyword in prompt.lower() for keyword in
                   ("progress", "what have you accomplished", "what's been done", "what have you done",
                    "what's pending", "what's next", "recap", "summary of work", "catch me up")):
                self.log("User asking for a progress recap – reading Hermes's session log")
                summary_resp = self.send_a2a("hermes", "get_progress_summary", {"limit": 3})
                text = self._format_response("progress_recap", summary_resp, "hermes")
                return {"result": text, "evidence": summary_resp}

            # --- System status (all agents + active projects) ---
            if any(keyword in prompt.lower() for keyword in
                   ("system status", "all agents", "agent status", "everything running", "how is everything", "status update")) \
                    or prompt.lower().strip() in ("status", "status?"):
                self.log("User asking for system-wide status – aggregating agent health + graph projects")
                status = self._get_system_status()
                text = self._format_response("system_status", status, "boss_agent")
                return {"result": text, "evidence": status}

            # --- Cross-agent relationship document analysis (Legal + Accounting) ---
            # Must come before the generic "analyze"/"evaluate" code-review branch below -
            # its single-word "analyze" keyword was silently swallowing every prompt that
            # started with "Analyze this agreement...", so this branch was unreachable via
            # natural language despite working correctly when called directly. Specific
            # multi-word phrases need to be checked before broad single-word ones.
            if any(keyword in prompt.lower() for keyword in ("analyze this agreement", "analyze this contract", "obligations and financial consequences", "legal and financial consequences")):
                self.log("User asking for combined legal+financial analysis – delegating to legal_agent + accounting_agent")
                doc_text = re.sub(
                    r"^(analyze this (agreement|contract)|what are the (obligations and )?"
                    r"(legal and )?financial consequences( of)?)[:\s]*",
                    "", prompt, flags=re.IGNORECASE
                ).strip() or prompt
                result = self.handle_task("analyze_relationship_document", {"text": doc_text}, sender)
                text = self._format_response("analyze_relationship_document", result, "boss_agent")
                return {"result": text, "evidence": result}

            # --- Evaluation / Lint / Analyze code ---
            if any(keyword in prompt.lower() for keyword in ("evaluate", "lint", "analyze", "check code", "quality")):
                self.log("User asking for code evaluation – delegating to coding_agent")
                response = self.send_a2a("coding_agent", "evaluate", {"path": "~/mycelial"})
                text = self._format_response("evaluate", response, "coding_agent")
                return {"result": text}

            # --- Analyze outcomes / recommendations ---
            if any(keyword in prompt.lower() for keyword in ("analyze outcomes", "analyze", "recommendations", "report")):
                self.log("User asking for analysis – delegating to analyzer_agent")
                response = self.send_a2a("analyzer_agent", "generate_recommendations", {})
                text = self._format_response("generate_recommendations", response, "analyzer_agent")
                return {"result": text}

            # --- FIX / DEBUG (moved before error check) ---
            if any(keyword in prompt.lower() for keyword in ("fix", "debug", "troubleshoot", "what is the cause")):
                self.log("User asking for debugging help – delegating to coding_agent")
                response = self.send_a2a("coding_agent", "reason", {"prompt": prompt})
                text = self._format_response("reason", response, "coding_agent")
                return {"result": text}

            # --- Error / Sentry checks ---
            if "error" in prompt.lower() or "sentry" in prompt.lower():
                self.log("User asking about errors – delegating to maintenance_agent")
                org = metadata.get("org", self.default_org)
                project = metadata.get("project", self.default_project)
                match = re.search(r'for\s+([\w-]+)', prompt, re.IGNORECASE)
                if match:
                    project = match.group(1)
                match = re.search(r'org\s+([\w-]+)', prompt, re.IGNORECASE)
                if match:
                    org = match.group(1)
                response = self.send_a2a("maintenance_agent", "check_errors", {"org": org, "project": project})
                text = self._format_response("check_errors", response, "maintenance_agent")
                return {"result": text}

            # --- Disk/cleanup routine ---
            if any(keyword in prompt.lower() for keyword in ("clean up", "cleanup", "free up disk", "free up space", "clear unused", "disk space")):
                self.log("User asking about disk cleanup – delegating to maintenance_agent")
                response = self.send_a2a("maintenance_agent", "run_cleanup_routine", {}, timeout=90)
                text = self._format_response("cleanup_routine", response, "maintenance_agent")
                return {"result": text, "evidence": response}

            # --- Purchase recommendation (Grow consults Accounting directly;
            # Boss's only role here is the threshold-escalation gate) ---
            # Checked before the generic Grow branch below since phrasing like
            # "should I buy more nutrients" contains "nutrient" and would
            # otherwise be swallowed by the broader plant/status branch.
            if any(keyword in prompt.lower() for keyword in ("should i buy", "can i afford", "worth buying", "recommend buying", "recommend a purchase")):
                self.log("User asking about a purchase – grow_agent consulting accounting_agent directly")
                cost_match = re.search(r'\$?(\d+(?:\.\d+)?)', prompt)
                estimated_cost = float(cost_match.group(1)) if cost_match else 0.0
                item = re.sub(
                    r'^(should i buy|can i afford|worth buying|recommend buying|recommend a purchase of)?\s*',
                    '', prompt, flags=re.IGNORECASE
                )
                item = re.sub(r'\$?\d+(\.\d+)?', '', item).strip(" ?.!")
                item = re.sub(r'\b(for|at|costs?|around|about)\s*$', '', item, flags=re.IGNORECASE).strip(" ?.!")
                item = item or "this item"
                response = self.send_a2a("grow_agent", "recommend_purchase", {"item": item, "estimated_cost": estimated_cost})
                text = self._format_response("purchase_recommendation", response, "grow_agent")
                return {"result": text, "evidence": response}

            # --- Log a reservoir/plant reading given in plain language ---
            # e.g. "388 ppm, 21.0c, 6.42 ph are today's average results" - checked
            # before the generic grow-status branch below since a reading like this
            # often doesn't contain any of that branch's keywords at all and would
            # otherwise fall through all the way to the generic reasoning delegate,
            # silently discarding the reading instead of logging it.
            ppm_match = re.search(r'(\d+(?:\.\d+)?)\s*ppm', prompt, re.IGNORECASE)
            ph_match = re.search(r'(\d+(?:\.\d+)?)\s*ph\b', prompt, re.IGNORECASE) or \
                re.search(r'\bph\s*(?:of|is|:)?\s*(\d+(?:\.\d+)?)', prompt, re.IGNORECASE)
            temp_c_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:°|deg(?:rees)?)?\s*c\b', prompt, re.IGNORECASE)
            temp_f_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:°|deg(?:rees)?)?\s*f\b', prompt, re.IGNORECASE)
            reading_signals = sum(1 for m in (ppm_match, ph_match, temp_c_match, temp_f_match) if m)
            if reading_signals >= 2:
                self.log("User reported a reading in plain language - logging to grow_agent")
                temp_c = None
                if temp_c_match:
                    temp_c = float(temp_c_match.group(1))
                elif temp_f_match:
                    temp_c = (float(temp_f_match.group(1)) - 32) * 5 / 9
                status = self.send_a2a("grow_agent", "get_status", {})
                status_result = status.get("result", {}) if isinstance(status, dict) else {}
                status_result = status_result.get("result", status_result) if isinstance(status_result, dict) else {}
                stage = status_result.get("current_stage") or "seedling"
                if stage == "unknown":
                    stage = "seedling"
                reading_args = {"stage": stage}
                if ppm_match:
                    reading_args["ppm"] = float(ppm_match.group(1))
                if ph_match:
                    reading_args["ph"] = float(ph_match.group(1))
                if temp_c is not None:
                    reading_args["temp"] = round(temp_c, 1)
                response = self.send_a2a("grow_agent", "log_reading", reading_args)
                text = self._format_response("log_reading", response, "grow_agent")
                return {"result": text, "evidence": response}

            # --- Grow Agent (plant/garden monitoring) ---
            if any(keyword in prompt.lower() for keyword in ("plant", "grow ", "garden", "reservoir", "seedling", "nutrient")):
                self.log("User asking about the grow/plant – delegating to grow_agent")
                response = self.send_a2a("grow_agent", "get_status", {})
                history = self.send_a2a("grow_agent", "get_grow_history", {"plant_id": "current_plant"})
                text = self._format_response("grow_status", {"status": response, "history": history}, "grow_agent")
                return {"result": text, "evidence": {"status": response, "history": history}}

            # --- Web search ---
            if any(keyword in prompt.lower() for keyword in ("search", "find", "look up", "google")):
                self.log("User asking for search – delegating to PQA (or tool)")
                response = self.send_a2a("pqa_agent", "search", {"query": prompt})
                if response and not isinstance(response, dict) or response.get("error"):
                    tool_result = self.call_tool("searxng", "search", {"query": prompt})
                    text = self._format_response("call_tool", tool_result, "tool")
                else:
                    text = self._format_response("search", response, "pqa_agent")
                return {"result": text}

            # --- Default: delegate to Coding Agent for reasoning ---
            self.log("Delegating to coding_agent for reasoning...")
            response = self.send_a2a("coding_agent", "reason", {"prompt": prompt})
            text = self._format_response("reason", response, "coding_agent")
            self.store_own_memory(f"request_{int(time.time())}", prompt)
            self.store_own_memory(f"response_{int(time.time())}", text)
            return {"result": text}

        elif task == "call_tool":
            server = args.get("server")
            tool_name = args.get("tool_name")
            tool_args = args.get("tool_args", {})
            if not server or not tool_name:
                return {"error": "Missing server or tool_name"}
            result = self.call_tool(server, tool_name, tool_args)
            text = self._format_response("call_tool", result, "tool")
            return {"result": text}

        elif task == "alert":
            message = args.get("message", "")
            recommendations = args.get("recommendations", [])
            report_path = args.get("report_path", "")
            self.log_to_audit("ALERT", message, level="warning")
            self.log(f"Alert received: {message}")
            if recommendations:
                self.store_own_memory("last_recommendations", json.dumps(recommendations))
                self.log(f"Stored {len(recommendations)} recommendations.")
                high_critical = any(r.get("criticality") == "high" for r in recommendations)
                if high_critical:
                    self.log("High criticality recommendations detected. Triggering reconciliation...")
                    if self._trigger_reconcile():
                        return {"result": "Alert logged, reconciliation triggered", "recommendations": len(recommendations)}
                    else:
                        return {"result": "Alert logged, but reconciliation failed", "recommendations": len(recommendations)}
            return {"result": "Alert logged", "recommendations": len(recommendations)}

        elif task == "process_recommendations":
            recs = self.retrieve_own_memory("last_recommendations")
            if not recs:
                return {"error": "No recommendations found."}
            try:
                recs = json.loads(recs)
            except:
                return {"error": "Invalid recommendations format."}
            if not isinstance(recs, list):
                return {"error": "Recommendations not a list."}
            result = {"processed": 0, "actions": []}
            for rec in recs:
                agent = rec.get("agent")
                issue = rec.get("issue")
                suggestion = rec.get("suggestion")
                if agent == "coding_agent" and "hook" in suggestion.lower():
                    self.log(f"Delegating to coding_agent to apply suggestion: {suggestion[:50]}...")
                    resp = self.send_a2a("coding_agent", "edit_file", {
                        "path": f"~/mycelial/agents/{agent}/{agent}.py",
                        "content": "# Placeholder for adding hook logic"
                    })
                    result["actions"].append({"agent": agent, "action": "edit_file", "response": resp})
                    result["processed"] += 1
            return {"result": result}

        elif task == "check_errors":
            org = args.get("org", self.default_org)
            project = args.get("project", self.default_project)
            self.log(f"Checking Sentry errors for {org}/{project}")
            response = self.send_a2a("maintenance_agent", "check_errors", {"org": org, "project": project})
            text = self._format_response("check_errors", response, "maintenance_agent")
            return {"result": text}

        else:
            return {"error": f"Unknown task: {task}"}

if __name__ == "__main__":
    agent = BossAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
