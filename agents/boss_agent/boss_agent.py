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
            # Microsecond precision: a batch upload saves several images inside
            # the same second, and second-granularity names would silently
            # overwrite each other down to a single file.
            path = os.path.join(photos_dir, f"upload_{int(time.time() * 1_000_000)}{ext}")
            with open(path, "wb") as f:
                f.write(raw)
            self.log(f"Saved uploaded image to {path} ({len(raw)} bytes)")
            return path
        except Exception as e:
            self.log(f"Failed to save uploaded image: {e}")
            return None




    @staticmethod
    def _unwrap(resp, key="text"):
        """Dig an A2A payload out of however many "result" envelopes it arrived in.

        Responses come back wrapped a variable number of times depending on the
        path taken, and a single .get("result") silently yields the next
        envelope rather than the payload - which reads as "the agent had
        nothing to say" and falls through to a status card. Unwrap until the
        object actually carries what was asked for."""
        seen = 0
        while isinstance(resp, dict) and key not in resp and "result" in resp and seen < 6:
            resp, seen = resp["result"], seen + 1
        return resp if isinstance(resp, dict) and key in resp else None

    # Which domains care about which case events. Types only - Boss matches on
    # the envelope's `type` field and never opens `ref`.
    CASE_EVENT_ROUTES = {
        "evidence_added":      ("legal_agent",),
        "document_added":      ("legal_agent", "hermes"),
        "element_updated":     ("legal_agent",),
        "obligation_recorded": ("accounting_agent",),
        "payment_recorded":    ("accounting_agent",),
        "participant_added":   ("trust_agent",),
        "case_state_changed":  ("legal_agent", "accounting_agent", "trust_agent", "hermes"),
        "task_completed":      ("hermes",),
        "case_opened":         ("legal_agent", "accounting_agent", "trust_agent", "hermes"),
        "note_added":          ("hermes",),
    }

    def route_case_event(self, envelope):
        """Forward a case event to the domains that care about its type.

        Deliberately checks the envelope for leakage before forwarding: if a
        caller has stuffed evidence or document content into it, that is a bug
        in the caller and the event is refused rather than quietly relayed."""
        if not isinstance(envelope, dict):
            return {"error": "case_event must be an envelope object"}
        leaked = [k for k in ("evidence", "content", "document", "text", "body")
                  if k in envelope]
        if leaked:
            return {"error": "envelope carries domain content and was refused",
                    "offending_fields": leaked,
                    "why": ("Boss routes case events on type and must not be handed "
                            "evidence. Send the reference; the domain agent reads the "
                            "case itself.")}
        etype, cid = envelope.get("type"), envelope.get("case_id")
        if not etype or not cid:
            return {"error": "envelope needs at least type and case_id"}
        targets = self.CASE_EVENT_ROUTES.get(etype)
        if not targets:
            return {"error": f"no route for event type {etype!r}",
                    "known": sorted(self.CASE_EVENT_ROUTES)}
        delivered, failed = [], []
        for t in targets:
            try:
                r = self.send_a2a(t, "case_event_notice", envelope, timeout=30)
                (delivered if r else failed).append(t)
            except Exception as e:
                failed.append(f"{t}: {e}")
        self.log(f"case event {etype} on {cid} -> {', '.join(delivered) or 'nobody'}")
        return {"routed": etype, "case_id": cid, "delivered_to": delivered,
                "not_delivered": failed, "inspected_evidence": False}

    _domain_cache = {"map": None, "at": 0, "ttl": 300}

    def _domain_vocabulary(self):
        """Ask every registered agent what vocabulary claims a request for it.

        Boss holds no domain words of its own. It asks. An agent that declares
        nothing simply never matches, and a new domain agent becomes routable
        by starting up - not by editing this file, which is what made the
        orchestrator accumulate the vocabulary of every domain beneath it."""
        c = self._domain_cache
        if c["map"] is not None and time.time() - c["at"] < c.get("ttl", 300):
            return c["map"]
        vocab = {}
        try:
            resp = requests.post("http://localhost:8004/execute",
                                 json={"task": "list_agents", "args": [],
                                       "sender": self.agent_id}, timeout=5)
            agents = resp.json().get("result", []) if resp.status_code == 200 else []
        except Exception as e:
            self.log(f"routing: registry lookup failed: {e}")
            agents = []
        silent, declined = [], []
        for a in agents:
            aid, url = a.get("agent_id"), a.get("url")
            if not aid or not url or aid == self.agent_id:
                continue
            try:
                r = requests.post(f"{url}/execute",
                                  json={"task": "routing_terms", "args": {},
                                        "sender": self.agent_id}, timeout=4)
                body = r.json() if r.status_code == 200 else {}
                while isinstance(body, dict) and "terms" not in body and "result" in body:
                    body = body["result"]
                terms = (body or {}).get("terms") or []
                if terms:
                    vocab[aid] = [t for t in terms if isinstance(t, str) and t]
                elif isinstance(body, dict) and "terms" in body:
                    declined.append(aid)     # answered, and claims nothing
                else:
                    silent.append(aid)       # no usable answer
            except Exception:
                silent.append(aid)           # down, or still starting

        # An agent that was registered but did not answer is usually still
        # booting - start_all.sh brings Boss up before most of them. Caching
        # that gap for the full five minutes left the router blind to every
        # domain except the one that happened to be ready, so retry soon
        # instead of freezing an incomplete map.
        #
        # Declaring nothing is not the same as failing to answer. Anansi
        # narrates and Hermes brokers; neither owns a domain, so both answer
        # with an empty list on purpose and must not keep the router retrying.
        c["map"], c["at"] = vocab, time.time()
        c["ttl"] = 30 if silent else 300
        self.log("routing vocabulary: " +
                 ", ".join(f"{k}={len(v)}" for k, v in sorted(vocab.items())) +
                 (f" | claims nothing: {', '.join(sorted(declined))}" if declined else "") +
                 (f" | silent: {', '.join(sorted(silent))} (retry in 30s)" if silent else ""))
        return vocab

    def ingest_document(self, args):
        """Take in a document, cut it into clauses, and tell each department
        which clauses are theirs.

        Boss does the ROUTING and nothing else - it does not read a clause for
        meaning, and the referral carries clause REFERENCES rather than clause
        text. An orchestrator that ships document content to a department is one
        refactor away from reasoning about it, and this one practises no domain.
        """
        path = args.get("path")
        if not path or not os.path.exists(path):
            return {"error": f"no such file: {path}"}
        try:
            from core.document_intake import analyse
        except Exception as e:
            return {"error": f"intake unavailable: {e}"}
        doc = analyse(path)
        if doc.get("error"):
            return doc

        case_id = args.get("case_id")
        kind = args.get("kind") or "document"
        title = args.get("title") or doc["source"]
        stored = None
        if case_id:
            # The document itself goes to the shared case, once, by reference.
            # Report what actually happened, not what _unwrap happened to
            # surface. This returned stored_to_case false while the document
            # was sitting in the case - a false FAILURE, which sends someone
            # re-uploading a file that is already filed.
            _resp = self.send_a2a("accounting_agent", "case_add_document", {
                "case_id": case_id, "kind": kind, "title": title,
                "ref": args.get("ref") or doc["source"],
                "note": (f"Intake {doc['meta'].get('method')}: {doc['clauses']} clauses, "
                         f"{doc['obligations']} obligation(s). "
                         + (doc['meta'].get('warning') or ''))})
            _inner, _seen = _resp, 0
            while isinstance(_inner, dict) and "document" not in _inner and "result" in _inner and _seen < 6:
                _inner, _seen = _inner["result"], _seen + 1
            stored = (_inner or {}).get("document", {}).get("doc_id") if isinstance(_inner, dict) else None

        referrals = []
        for domain, refs in (doc.get("clause_refs_by_domain") or {}).items():
            payload = {"document": title, "case_id": case_id,
                       "clause_refs": refs[:60],
                       "clause_count": len(refs),
                       "obligation_refs": [c["ref"] for c in doc["items"]
                                           if c["ref"] in refs and
                                           ("obligation" in c["kinds"] or
                                            "prohibition" in c["kinds"])][:40],
                       "deadlines": sorted({d for c in doc["items"] if c["ref"] in refs
                                            for d in c["deadlines"]})[:12],
                       "amounts": sorted({a for c in doc["items"] if c["ref"] in refs
                                          for a in c["amounts"]})[:12]}
            r = self.refer_finding(domain, "document_clauses", payload,
                                   why=(f"{len(refs)} clause(s) in {title} carry subject "
                                        f"matter this department owns."))
            referrals.append({"domain": domain, "clauses": len(refs),
                              "accepted": r.get("accepted")})
        out = {"source": doc["source"], "method": doc["meta"].get("method"),
               "clauses": doc["clauses"], "obligations": doc["obligations"],
               "segmented_by": doc.get("segmented_by"),
               "interested_domains": doc["interested_domains"],
               "referrals": referrals,
               "stored_to_case": stored or False}
        for k in ("warning", "legibility", "applied_rotation", "pages_without_text"):
            if doc["meta"].get(k) is not None:
                out[k] = doc["meta"][k]
        return out

    def _domain_for(self, prompt):
        """Which department owns this request.

        Intent is RESOLVED first and word-matched only as a fallback. Counting
        regex hits is why "How's the system today" reached a code model and why
        "my water is two inches below the net pot" matched nothing about the
        grow: the terms were written to be matched, not to describe a
        department. A person should be able to speak plainly.

        The resolver picks from the live registry and can return nothing but an
        id that exists, so a wrong answer is a wrong ROUTE - recoverable,
        because the department says it does not own the question - never a
        wrong fact. See core/intent.py."""
        pick = self._resolve_intent(prompt)
        keyword = self._domain_by_terms(prompt)
        if pick and pick != "UNCLEAR":
            if keyword and keyword != pick:
                self.log(f"routing: intent={pick} keywords={keyword} - took intent")
            return pick
        return keyword

    def _resolve_intent(self, prompt):
        try:
            from core.intent import IntentResolver
        except Exception:
            return None
        if not hasattr(self, "_intent"):
            self._intent = IntentResolver(log=self.log, roster_fn=self._roster)
        try:
            pick, why = self._intent.resolve(prompt)
            if pick == "UNCLEAR":
                self.log(f"routing: intent unresolved ({why})")
            return pick
        except Exception as e:
            self.log(f"routing: intent resolver failed, using terms: {e}")
            return None

    def _roster(self):
        """Departments as they describe THEMSELVES - declared terms plus the
        capability names they registered. Nothing about any domain is written
        down here; a new agent becomes routable by starting up."""
        out = {}
        caps = {}
        try:
            r = requests.post("http://localhost:8004/execute",
                              json={"task": "list_agents", "args": [],
                                    "sender": self.agent_id}, timeout=5)
            for a in (r.json().get("result", []) if r.ok else []):
                if a.get("agent_id"):
                    caps[a["agent_id"]] = a.get("capabilities") or []
        except Exception as e:
            self.log(f"routing: registry unreachable for roster: {e}")
        for aid, terms in self._domain_vocabulary().items():
            if terms:
                out[aid] = {"terms": terms, "capabilities": caps.get(aid, [])}
        return out

    def _domain_by_terms(self, prompt):
        """The old word count, kept as the fallback for when inference is
        down. A degraded route beats no route."""
        lp = (prompt or "").lower()
        best, best_n = None, 0
        for aid, terms in self._domain_vocabulary().items():
            n = 0
            for t in terms:
                try:
                    if re.search(t if ("\\b" in t or "?" in t or "*" in t) else r"\b" + t, lp):
                        n += 1
                except re.error:
                    continue
            if n > best_n:
                best, best_n = aid, n
        return best













    def _format_response(self, task, result, sender):
        if result is None:
            return "The request did not return a result."
        if isinstance(result, str):
            return result
        # The agent that produced a result says what it means. Boss used to
        # compose the prose itself, which is how the orchestrator came to hold
        # 171 lines describing reservoirs, deficiency signs and stage
        # transitions for domains it does not practise.
        if sender and sender != self.agent_id:
            try:
                said = self._unwrap(self.send_a2a(sender, "describe",
                                                  {"task": task, "payload": result},
                                                  timeout=30), key="text")
                if said and (said.get("text") or "").strip():
                    return said["text"]
            except Exception as e:
                self.log(f"{sender} could not describe {task}: {e}")
        if isinstance(result, dict):
            if "error" in result:
                return f"Error: {result['error']}"
            if task == "pending_decisions":
                def _inner(x, depth=3):
                    for _ in range(depth):
                        if isinstance(x, dict) and "result" in x:
                            x = x["result"]
                        else:
                            break
                    return x if isinstance(x, dict) else {}

                pend = _inner(result.get("pending_approvals"))
                mem = _inner(result.get("memory"))
                lines = []

                items = pend.get("pending") or []
                if items:
                    lines.append(f"{len(items)} thing(s) are waiting on your decision:")
                    for it in items[:5]:
                        what = f"{it.get('action','an action')} on {it.get('target','something')}".strip()
                        why = f" - {it['reason']}" if it.get("reason") else ""
                        lines.append(f"  \u2022 {what}{why}")
                    lines.append("Approve by setting status to 'approved' in the matching file under state/pending_requests/.")

                findings = mem.get("findings") or []
                reclaim = mem.get("reclaimable_mb") or 0
                if reclaim:
                    lines.append(
                        f"Separately, about {reclaim:.0f}MB is sitting in services nothing calls. "
                        "I can stop starting those at boot if you want that memory back - your call, "
                        "nothing is stopped without it."
                    )
                for f in findings:
                    if "holds" in f and "% of the swarm" in f:
                        lines.append(f)

                if not lines:
                    return "Nothing is waiting on you right now."
                return "\n".join(lines)

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
            elif task == "system_status":
                alive = result.get("alive", [])
                dead = result.get("dead", [])
                total = result.get("total_registered", 0)
                projects = result.get("projects", [])
                # Lead with whether anything needs the grower. This used to
                # open with "12 of 12 registered agents are up:" followed by
                # every internal agent id - a roster dump with no judgement in
                # it, in answer to "how's the system today". Nobody asking that
                # wants to read "pqa_agent".
                human = {"grow_agent": "the grow", "legal_agent": "legal",
                         "accounting_agent": "accounting", "trust_agent": "trust",
                         "security_agent": "security", "coding_agent": "engineering",
                         "maintenance_agent": "the machine itself",
                         "analyzer_agent": "analysis", "pqa_agent": "web search",
                         "hermes": "memory", "anansi": "the front desk",
                         "boss_agent": "orchestration"}
                say = lambda ids: ", ".join(human.get(i, i) for i in ids)
                lines = []
                if dead:
                    _is = "is" if len(dead) == 1 else "are"
                    lines.append(f"{len(dead)} of {total} {_is} down and that "
                                 f"needs you: {say(dead)}.")
                    lines.append(f"The other {len(alive)} are up.")
                else:
                    lines.append(f"Nothing needs you - all {total} departments "
                                 f"are answering.")
                if projects:
                    lines.append(f"Active projects: {', '.join(projects)}.")
                return " ".join(lines)
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
        if task == "ingest_document":
            return self.ingest_document(args if isinstance(args, dict) else {})

        self.log(f"Task: {task} from {sender} with args: {args}")

        # A case event is a TASK, not a sentence. It sat inside process_request,
        # which validates a prompt first, so every event was refused with
        # "Missing prompt" before the router saw it.
        if task == "case_event":
            payload = args.get("case_event") if isinstance(args, dict) else None
            return self.route_case_event(payload if payload is not None else args)

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

            # Batch upload: metadata.images is a list of {data, name}. The older
            # single image_base64/image_name pair is still accepted so a stale
            # cached client keeps working.
            images = metadata.get("images") if isinstance(metadata, dict) else None
            if not images and isinstance(metadata, dict) and metadata.get("image_base64"):
                images = [{"data": metadata["image_base64"], "name": metadata.get("image_name", "upload.jpg")}]
            image_base64 = images[0]["data"] if images else None

            if not prompt and not image_base64:
                return {"error": "Missing prompt"}

            self.log(f"Received user prompt: {prompt[:80]}...")

            # --- Image upload (plant photo) ---
            # Only Grow Agent has a vision pipeline today, so any uploaded image
            # routes there. Kept as its own branch (not folded into the generic
            # image_base64 handling below) so a future second vision-capable
            # agent can be added by branching on prompt content/metadata here
            # without touching the upload/save plumbing.
            if images:
                self.log(f"User uploaded {len(images)} image(s) - routing to grow_agent's vision pipeline")
                saved, failed = [], 0
                for im in images:
                    path = self._save_uploaded_image(im.get("data"), im.get("name", "upload.jpg"))
                    if path:
                        saved.append(path)
                    else:
                        failed += 1
                if not saved:
                    return {"result": "I couldn't process those images - they may be corrupted, empty, or over the 15MB limit."}

                # Which plant the photo is of is Grow's to decide - it holds
                # the roster. Defaulting to current_plant filed a photo the
                # grower labelled "Gsc 2" against a different, older plant, and
                # then assessed a 3-day seedling against a 26-day veg record.
                plant_id = metadata.get("plant_id")
                if not plant_id:
                    _r = self._unwrap(self.send_a2a("grow_agent", "resolve_plant",
                                                    {"prompt": prompt or ""}),
                                      key="plant_id")
                    plant_id = (_r or {}).get("plant_id") or "current_plant"
                    self.log(f"photo attributed to {plant_id} (resolved by grow_agent)")

                # A measurement sent with the photos is logged BEFORE they are
                # looked at. Vision is slow and can time out; the numbers must
                # not be lost with it.
                reading_text = None
                _r = self._unwrap(self.send_a2a("grow_agent", "log_from_text",
                                                {"prompt": prompt or "",
                                                 "plant_id": plant_id}), key="logged")
                if _r and _r.get("logged"):
                    self.log(f"Reading found alongside photos - logged first: {_r.get('reading')}")
                    reading_text = self._format_response("log_reading", _r.get("result"), "grow_agent")

                # Vision runs in the BACKGROUND and the upload returns at once.
                #
                # A single photo takes ~21s end to end - local perception plus an
                # escalation to the vision model - so three photos is over a
                # minute of a blocked HTTP request. A phone browser kills that
                # the moment the screen locks or the user switches apps, and the
                # app reports "Failed" while the server is working perfectly and
                # finishes the job nobody is left to receive. Measured, not
                # assumed: the same payload the webapp sends returns HTTP 200 in
                # 21s from a client that waits.
                #
                # The photo is already on disk before this point and the reading
                # is already logged, so nothing is lost by answering immediately.
                def _run_vision(paths, pid):
                    for pth in paths:
                        try:
                            r = self.send_a2a("grow_agent", "evaluate_leaf",
                                              {"plant_id": pid, "photo_path": pth}, timeout=300)
                            self.log(f"background vision done for {os.path.basename(pth)}: "
                                     f"{str(r)[:120]}")
                        except Exception as e:
                            self.log(f"background vision failed for {pth}: {e}")

                threading.Thread(target=_run_vision, args=(list(saved), plant_id),
                                 daemon=True).start()
                results = [{"photo": os.path.basename(p_), "assessment": None, "raw": None}
                           for p_ in saved]

                n = len(results)
                text = (f"Got {'the photo' if n == 1 else f'{n} photos'} - saved and being looked "
                        f"at now. Assessment takes about {20 * n}s; ask me about the plant in a "
                        "moment and I'll have it.")
                if failed:
                    text += f"\n({failed} could not be read and were skipped.)"
                if reading_text:
                    text = reading_text + "\n\n" + text
                return {"result": text, "evidence": {"photos": results, "reading": (_r or {}).get("reading")}}

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

            # Runs BEFORE the task branches below, because an agent's own
            # declared vocabulary outranks a word Boss happens to recognise.
            # "What's your analysis on GSC number two plant with the photo I
            # just uploaded" contains "analyze", so it matched the code-review
            # branch and spent 120s in a 1.5b code model before timing out -
            # while grow_agent, which declares "gsc_auto_2" and "plant" and had
            # the photo, was never asked. Those branches still catch what no
            # domain claims.
            # --- The domain that claims this request answers it ---
            #
            # Boss decides WHICH AGENT. It does not decide which of that agent's
            # capabilities apply - that is domain reasoning, and Boss practises
            # no domain. Roughly 600 lines of horticulture intent patterns and
            # 171 of horticulture prose used to live here, and every failure had
            # the same shape: a new ability in the domain agent was reachable
            # from exactly one branch of this router, and the user phrased the
            # question some other way. Patching in another keyword fixed the
            # sentence and never the class.
            #
            # No agent is named below. The domain is whichever agent's own
            # declared vocabulary claims the request, and adding a capability
            # there requires no change here.
            # ORCHESTRATION QUESTIONS ARE BOSS'S OWN, and are settled before
            # anything is offered to a domain.
            #
            # The dashboard asks "system status" and "catch me up on progress".
            # Domain routing sits above those branches, and grow_agent declares
            # a get_status capability - so once capability names went into the
            # routing briefs, "status" read as a grow question and the System
            # and Progress cards both filled with "that does not name one of the
            # plants I track". Two cards showing a third card's refusal.
            #
            # A department cannot own a question about the departments. This is
            # not domain vocabulary in the orchestrator; it is the orchestrator
            # recognising its own subject.
            _lp = (prompt or "").lower()
            _orchestration = (
                any(w in _lp for w in
                    ("system status", "agent status", "all agents", "everything running",
                     "how is everything", "status update", "progress",
                     "catch me up", "recap", "what have you done",
                     "what's been done", "what needs my approval",
                     "needs my approval", "awaiting approval"))
                or _lp.strip() in ("status", "status?"))
            _domain = None if _orchestration else self._domain_for(prompt)
            if _orchestration:
                self.log(f"orchestration question - not offered to any domain: {prompt[:60]}")
            if _domain:
                # Anything recordable in the raw input is captured first - only
                # the domain agent knows what counts as data.
                _got = self._unwrap(self.send_a2a(_domain, "ingest",
                                                  {"prompt": prompt}), key="logged")
                if _got and _got.get("logged"):
                    self.log(f"{_domain} recorded something from the raw request")
                    text = self._format_response("log_reading", _got.get("result"), _domain)
                    return {"result": text, "evidence": _got}

                self.log(f"{_domain} claims this request - asking it to answer")
                res = self._unwrap(self.send_a2a(_domain, "answer",
                                                 {"prompt": prompt}, timeout=120))
                if res and (res.get("text") or "").strip():
                    return {"result": res["text"],
                            "evidence": {"agent": _domain,
                                         "answered_as": res.get("answered_as"),
                                         "facts": res.get("facts")}}
                # An agent that has not implemented answer() yet falls through
                # to the branches below rather than failing the request.
                self.log(f"{_domain} had no answer - continuing")

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
            # Match the SHAPE of a status question, not a list of phrasings.
            # The list used to be six exact strings, so "How's the system
            # today" matched none of them and fell all the way through to a
            # code model, which answered "As a language model, I don't have
            # feelings." Adding that sentence to the list would have fixed the
            # sentence and not the class - which is the failure CLAUDE.md
            # opens with. A subject word plus a state word is the class.
            _p = prompt.lower()
            _subject = ("system", "mycos", "mycelial", "agents", "everything",
                        "services", "stack", "swarm", "platform", "things",
                        "anything", "departments")
            _state = ("status", "health", "healthy", "running", "up", "down",
                      "ok", "okay", "alright", "doing", "how is", "how's",
                      "hows", "look", "going", "wrong", "broken", "issues")
            # Some state words carry the subject on their own: "is anything
            # broken" names no system but is unmistakably about one.
            _standalone = ("anything broken", "anything down", "anything wrong",
                           "something broken", "something wrong", "all good",
                           "everything ok", "everything okay", "any issues",
                           "anything need", "anything i need")
            if ((any(w in _p for w in _subject) and any(w in _p for w in _state))
                    or any(w in _p for w in _standalone)
                    or _p.strip() in ("status", "status?")):
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

            # --- What needs a human decision ---
            # Approval-needing items exist in several places and surface in none
            # of them: security holds files in state/pending_requests/ that
            # nobody looks at, and maintenance findings only appear if you happen
            # to ask for a cleanup. This gathers them into one answer.
            if any(k in prompt.lower() for k in
                   ("approval", "approve", "permission", "waiting on me", "needs my ok",
                    "need my ok", "pending", "sign off", "sign-off", "authorize", "authorise")):
                self.log("User asking what needs a decision - gathering pending items")
                pend = self.send_a2a("security_agent", "list_pending_approvals", {}, timeout=30)
                mem = self.send_a2a("maintenance_agent", "analyze_memory_usage", {}, timeout=90)
                gathered = {"pending_approvals": pend, "memory": mem}
                text = self._format_response("pending_decisions", gathered, "boss_agent")
                return {"result": text, "evidence": gathered}

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

            # --- Before the generic model: try the domain agent ---
            #
            # There used to be a grow fallback here: any unmatched QUESTION was
            # sent to grow_agent before the generic model. It was a reasonable
            # patch when word-counting was the only routing mechanism and this
            # was a grow assistant - the comment it carried listed five real
            # failures it fixed.
            #
            # It is removed because intent is resolved now, and because it had
            # started causing the thing it was meant to prevent: "how much do I
            # still owe on rent" routed correctly to accounting, accounting had
            # no answer, and this caught it and replied about ppm and veg
            # bands. A domain-specific default inside the orchestrator is the
            # violation this architecture exists to remove - Boss practises no
            # domain, so it has no business having a favourite one.
            #
            # If the department that claimed a request cannot answer it, that
            # is the finding. Say it.

            # --- Default: a GENERAL model, never the code model ---
            #
            # This used to send every unclaimed request to coding_agent, whose
            # model is deepseek-coder. That is how "how long until it falls to
            # 238" came back as a physics free-fall problem, how "DWC" became
            # "Direct Water Cooker", and how "How's the system today" reached
            # the grower as "As a language model, I don't have feelings."
            #
            # A code model is a specialist. Handing it every question nobody
            # claimed is not a fallback, it is a misroute with a default
            # attached. Ask for the `reasoning` capability instead and let the
            # Inference Service resolve it.
            # If a department CLAIMED this and simply had no answer, no model
            # speaks on its behalf. A generic model answering a question that a
            # domain owns is the two-sources-of-truth failure in miniature: the
            # grower cannot tell whether they heard from the department that
            # holds the evidence or from a 1.5B model improvising around it.
            # "Is the disk filling up" came back as a numbered essay on why
            # disks fill up, from a model with no access to the disk.
            _claimed_but_silent = locals().get("_domain")
            if _claimed_but_silent:
                _h = {"grow_agent": "the grow", "legal_agent": "legal",
                      "accounting_agent": "accounting",
                      "maintenance_agent": "the machine itself",
                      "trust_agent": "trust", "security_agent": "security",
                      "coding_agent": "engineering"}.get(_claimed_but_silent,
                                                        _claimed_but_silent)
                self.log(f"{_claimed_but_silent} claimed it and had no answer - "
                         f"not substituting a model")
                return {"result": f"That one belongs to {_h}, which took the "
                                  f"question and had no answer for it. The "
                                  f"routing is right and the capability is "
                                  f"missing - worth building rather than "
                                  f"rephrasing.",
                        "evidence": {"claimed_by": _claimed_but_silent,
                                     "answered": False}}

            self.log("Nothing claimed this - answering with the general reasoning model")
            text = ""
            try:
                r = requests.post("http://localhost:8005/reason",
                                  json={"prompt": prompt, "capability": "reasoning"},
                                  timeout=120)
                data = r.json() if r.ok else {}
                if data.get("success"):
                    text = (data.get("result") or "").strip()
            except Exception as e:
                self.log(f"general reasoning model unreachable: {e}")

            # Assistant boilerplate is not an answer. "As a language model I
            # don't have feelings" reached the grower on a phone in response to
            # "How's the system today" - a deflection about the model's own
            # nature, standing in for the status of their system. If that is
            # what came back, throw it away and say plainly that nothing
            # claimed the question.
            _boiler = ("as a language model", "as an ai", "i don't have feelings",
                       "i do not have feelings", "i'm just a", "i am just a",
                       "i don't have personal", "i cannot feel")
            if text and any(b in text.lower() for b in _boiler):
                self.log("Discarded assistant boilerplate from the general model")
                text = ""
            if not text or len(str(text).strip()) < 2:
                # Two different findings, and collapsing them is a lie. Nobody
                # claiming a request is a routing gap; a department claiming it
                # and having nothing to say is a CAPABILITY gap, and the person
                # deserves to know which one they hit - it tells them whether
                # to rephrase or to stop asking.
                _claimed = locals().get("_domain")
                if _claimed:
                    _human = {"grow_agent": "the grow", "legal_agent": "legal",
                              "accounting_agent": "accounting",
                              "maintenance_agent": "the machine itself",
                              "trust_agent": "trust", "security_agent": "security",
                              "coding_agent": "engineering"}.get(_claimed, _claimed)
                    text = (f"That one belongs to {_human}, which took the "
                            f"question and had no answer for it. So the routing "
                            f"is right and the capability is missing - worth "
                            f"building rather than rephrasing.")
                else:
                    text = ("Nothing in the system claimed that one, and I "
                            "would rather say so than guess at it. Try naming "
                            "the thing you mean - a plant, a case, the ledger, "
                            "the machine itself - and it will reach whoever "
                            "actually owns it.")
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
