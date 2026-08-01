#!/usr/bin/env python3
import sys
import os
import re
import json
import time
import uuid
import requests
from datetime import datetime

# Add project root
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase
from core.schemas import from_legacy_fields

INFERENCE_SERVICE_URL = "http://localhost:8005/reason"
MODEL_SERVICE_URL = "http://localhost:8006/models/select"
DEFAULT_MODEL = "qwen2.5:1.5b"

DISCLAIMER = (
    "This output is generated automatically for informational purposes only. "
    "It is an extraction/structuring of the provided text, not legal advice, "
    "and should be reviewed by a qualified professional before being relied upon."
)

RELATIONSHIP_FIELDS = [
    "entity_a", "entity_b", "contract_type", "asset", "asset_owner", "custodian",
    "obligations", "rights", "beneficiary", "service_provider", "fee_recipient",
    "governing_law", "applicable_statutes"
]

STATUTE_CITATION_RE = re.compile(r"\b\d+\s*U\.?S\.?C\.?\s*§*\s*\d+[a-zA-Z0-9\-]*", re.IGNORECASE)


class LegalAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="legal_agent",
            port=9011,
            capabilities=[
                "parse_contract", "model_relationship", "extract_parties", "analyze_roles",
                "query_relationship", "compare_relationships", "lookup",
                "list_relationships", "get_relationship", "find_relationships",
                "find_relationships_by_project",
                "refresh_cache", "query_cache", "cache_stats", "cache_manifest",
                "search_cases", "monitor_user", "monitor_docket",
                "log_lesson", "query_lessons", "list_lessons"
            ],
            role="agent"
        )
        # CAG: source docs live in knowledge_base/legal_agent/{statutes,irs_publications,
        # dictionary,contract_templates}/. Poll every 5 min for changed/added files; a
        # cron-driven refresh_cache task call works too (see hooks/ for the pattern).
        self.init_cag(cache_ttl=86400, watch_interval=300)
        self.subscribe_project_events()
        self.log("Legal Agent initialized (extraction/structuring only - no legal advice).")

    def on_project_event(self, project_id, event_type, data, sender):
        """Example event-driven reaction: when a project moves to the 'negotiation'
        stage, Legal Agent notes that contract terms should be reviewed. This is
        illustrative logging, not an autonomous drafting pipeline - see the demo
        workflow in scripts/demo_workflow.py."""
        stage = data.get("data", {}).get("stage") if isinstance(data, dict) else None
        if event_type == "stage" and stage == "negotiation":
            self.log_to_audit(
                "project_event_reaction",
                f"project={project_id}: negotiation stage reached - contract terms review needed",
                level="info", metadata={"namespace": f"project_{project_id}"}
            )
            self.log(f"Reacting to project {project_id} entering negotiation: flagging for contract review")
        else:
            self.log(f"Project event {project_id}/{event_type} from {sender} (no reaction configured)")

    # ---------- CAG-backed lookups ----------
    def _extract_citations(self, text):
        return list({m.group(0).strip() for m in STATUTE_CITATION_RE.finditer(text)})

    def _cache_context_for(self, text, top_k=3):
        """Cache-first context gathering: pull relevant statute/definition snippets
        for the given text, to ground the extraction prompt. Never calls inference."""
        hits = self.query_cache(text[:1000], top_k=top_k)
        for citation in self._extract_citations(text):
            hits.extend(self.query_cache(citation, top_k=1))
        # de-dupe by doc id, keep highest score
        best = {}
        for h in hits:
            if h["id"] not in best or h["score"] > best[h["id"]]["score"]:
                best[h["id"]] = h
        return sorted(best.values(), key=lambda h: h["score"], reverse=True)[:top_k]

    def _format_context_block(self, hits):
        if not hits:
            return ""
        lines = ["Relevant cached reference material (from the local knowledge base - use only if applicable, do not assume it is exhaustive):"]
        for h in hits:
            lines.append(f"- [{h['category'] or 'general'}/{h['id']}] {h['snippet']}")
        return "\n".join(lines) + "\n\n"

    # ---------- Model / Inference helpers ----------
    def _get_model_for_task(self, requirements="reasoning"):
        try:
            resp = requests.post(MODEL_SERVICE_URL, json={"requirements": requirements}, timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    return data.get("model", DEFAULT_MODEL)
            return DEFAULT_MODEL
        except Exception:
            return DEFAULT_MODEL

    def _call_inference(self, prompt, model_name=None, timeout=60):
        """Call the Inference Service, falling back to an alternate model
        via the Model Service if the primary call is slow or unavailable."""
        if model_name is None:
            model_name = self._get_model_for_task("reasoning")
        try:
            resp = requests.post(
                INFERENCE_SERVICE_URL,
                json={"prompt": prompt, "model": model_name},
                timeout=timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    return data.get("result", "")
            self.log(f"Inference Service returned an error (HTTP {resp.status_code}); trying fallback model.")
        except requests.exceptions.Timeout:
            self.log("Inference Service timed out; trying fallback model.")
        except Exception as e:
            self.log(f"Inference Service call failed ({e}); trying fallback model.")

        # Small fallback: ask the Model Service for a lighter/alternate model and retry once
        fallback_model = self._get_model_for_task("lightweight")
        if fallback_model and fallback_model != model_name:
            try:
                resp = requests.post(
                    INFERENCE_SERVICE_URL,
                    json={"prompt": prompt, "model": fallback_model},
                    timeout=30
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success"):
                        return data.get("result", "")
            except Exception as e:
                self.log(f"Fallback inference call failed: {e}")

        self.log("Inference unavailable after fallback attempt.")
        return ""

    # ---------- JSON extraction helpers ----------
    def _safe_parse_json(self, raw):
        """Return (parsed_dict_or_None, parse_error_bool)."""
        if not raw or not raw.strip():
            return None, True
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.IGNORECASE | re.MULTILINE).strip()
        try:
            return json.loads(text), False
        except Exception:
            pass
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0)), False
            except Exception:
                pass
        return None, True

    def _extract_relationship(self, contract_text, model=None):
        """Cache-Augmented Generation: pull relevant statute/definition context from the
        local knowledge base first, then ask the Inference Service to extract structured
        fields from contract text, grounded in that context where applicable."""
        cache_hits = self._cache_context_for(contract_text)
        context_block = self._format_context_block(cache_hits)
        prompt = (
            context_block +
            "You are a contract-structuring assistant. Read the contract text below and "
            "extract ONLY the following fields as a single valid JSON object (no markdown "
            "fences, no commentary, no legal analysis or opinions):\n"
            "{\n"
            '  "entity_a": "",\n'
            '  "entity_b": "",\n'
            '  "contract_type": "",\n'
            '  "asset": "",\n'
            '  "asset_owner": "",\n'
            '  "custodian": "",\n'
            '  "obligations": [],\n'
            '  "rights": [],\n'
            '  "beneficiary": "",\n'
            '  "service_provider": "",\n'
            '  "fee_recipient": "",\n'
            '  "governing_law": "",\n'
            '  "applicable_statutes": []\n'
            "}\n"
            "If a field cannot be determined from the text, use an empty string or empty "
            "list. Only extract and structure what is explicitly stated in the text - do not "
            "infer facts, and do not provide legal advice or opinions. If the cached reference "
            "material above names a governing statute that matches this contract, you may use it "
            "for the governing_law/applicable_statutes fields - otherwise leave them as stated in "
            "the contract text itself.\n\n"
            f"Contract text:\n\"\"\"\n{contract_text}\n\"\"\"\n\nJSON:"
        )
        raw = self._call_inference(prompt, model_name=model)
        parsed, parse_error = self._safe_parse_json(raw)
        if parsed is None:
            parsed = {field: ([] if field in ("obligations", "rights", "applicable_statutes") else "") for field in RELATIONSHIP_FIELDS}
        else:
            for field in RELATIONSHIP_FIELDS:
                parsed.setdefault(field, [] if field in ("obligations", "rights", "applicable_statutes") else "")
        parsed["parse_error"] = parse_error
        if parse_error:
            parsed["raw_model_output"] = raw
        parsed["cache_sources"] = [h["id"] for h in cache_hits]
        parsed["disclaimer"] = DISCLAIMER
        return parsed

    # ---------- Relationship storage helpers ----------
    def _get_stored_value(self, retrieval_result):
        """Unwrap the A2A response from retrieve_own_memory down to the stored value string."""
        if not isinstance(retrieval_result, dict):
            return None
        result = retrieval_result.get("result")
        if not isinstance(result, dict):
            return None
        entry = result.get("entry")
        if not isinstance(entry, dict):
            return None
        return entry.get("value")

    def _load_index(self):
        raw = self._get_stored_value(self.retrieve_own_memory("relationship_index"))
        if not raw:
            return []
        try:
            index = json.loads(raw)
            return index if isinstance(index, list) else []
        except Exception:
            return []

    def _append_to_index(self, relationship_id):
        index = self._load_index()
        if relationship_id not in index:
            index.append(relationship_id)
            self.store_own_memory("relationship_index", json.dumps(index))

    def _load_relationship(self, relationship_id):
        raw = self._get_stored_value(self.retrieve_own_memory(relationship_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def _push_to_graph(self, doc, project_id):
        """Best-effort: keep Boss's relationship graph in sync when a relationship is
        created. Failure here doesn't fail the caller - the record is already stored
        in this agent's own memory regardless."""
        try:
            graph_rel = from_legacy_fields(doc, domain="legal", project_id=project_id)
            resp = self.send_a2a("boss_agent", "update_graph", {
                "action": "ingest_relationship",
                "relationship": graph_rel,
                "project_id": project_id,
            })
            if not resp or (isinstance(resp, dict) and resp.get("result", {}).get("error")):
                self.log(f"Graph push for {doc.get('id')} did not confirm success: {resp}")
        except Exception as e:
            self.log(f"Graph push failed for {doc.get('id')}: {e}")

    # ---------- CourtListener helpers ----------
    def _unwrap_tool_result(self, tool_response, disclaimer=False):
        """Unwrap a call_tool() response down to the JSON payload the MCP tool
        returned. call_tool() can fail at three levels: the Tool Service itself
        ({"error": ...} at the top), the MCP JSON-RPC call ({"result": {"error": ...}}),
        or the tool's own handler (JSON-encoded {"error": ...} in the text content)."""
        if not isinstance(tool_response, dict):
            out = {"error": f"Unexpected tool response: {tool_response}"}
        elif tool_response.get("error"):
            out = {"error": tool_response["error"]}
        else:
            mcp_result = tool_response.get("result", {})
            if isinstance(mcp_result, dict) and mcp_result.get("error"):
                out = {"error": mcp_result["error"]}
            else:
                content = mcp_result.get("content", []) if isinstance(mcp_result, dict) else []
                text = content[0].get("text", "") if content else ""
                try:
                    out = json.loads(text) if text else {"error": "Empty tool response"}
                except Exception:
                    out = {"error": "Could not parse tool response", "raw": text}
        if disclaimer:
            out["disclaimer"] = DISCLAIMER
        return out

    # ---------- Task handling ----------
    def handle_task(self, task, args, sender):
        self.log(f"Task {task} from {sender}")

        cag_result = self.try_handle_cag_task(task, args)
        if cag_result is not None:
            return cag_result

        if task == "lookup":
            if not args or not args[0]:
                return {"error": "Usage: lookup <term_or_citation>", "disclaimer": DISCLAIMER}
            term = args[0]
            hits = self.query_cache(term, top_k=3)
            if hits:
                return {
                    "term": term,
                    "source": "cache",
                    "results": hits,
                    "disclaimer": DISCLAIMER
                }
            # Cache had nothing - try a public web search via PQA Agent before
            # falling back to raw inference (which can hallucinate).
            web = self.search_public(f"{term} legal definition statute")
            web_result = web.get("result") if isinstance(web, dict) else None
            if web_result and not (isinstance(web_result, dict) and web_result.get("error")):
                return {
                    "term": term,
                    "source": web.get("source", "pqa_agent"),
                    "note": "No match in the local knowledge base cache; this answer came from "
                            "a public web search and is NOT verified against a cached authoritative source.",
                    "answer": web_result,
                    "disclaimer": DISCLAIMER
                }

            # Web search unavailable/empty - fall back to inference, but say so plainly.
            raw = self._call_inference(
                f"Briefly define or explain the following legal term or statute citation, "
                f"in one or two sentences, without giving legal advice: {term}"
            )
            return {
                "term": term,
                "source": "inference_fallback",
                "note": "No match in the local knowledge base cache or public web search; this "
                        "answer was generated by the model and is NOT verified against a cached "
                        "authoritative source.",
                "answer": raw,
                "disclaimer": DISCLAIMER
            }

        if task == "parse_contract":
            if not args or not args[0]:
                return {"error": "Missing contract_text", "disclaimer": DISCLAIMER}
            return self._extract_relationship(args[0])

        elif task == "extract_parties":
            if not args or not args[0]:
                return {"error": "Missing contract_text", "disclaimer": DISCLAIMER}
            extraction = self._extract_relationship(args[0])
            return {
                "entity_a": extraction.get("entity_a", ""),
                "entity_b": extraction.get("entity_b", ""),
                "parse_error": extraction.get("parse_error", False),
                "disclaimer": DISCLAIMER
            }

        elif task == "analyze_roles":
            if not args or not args[0]:
                return {"error": "Missing contract_text", "disclaimer": DISCLAIMER}
            extraction = self._extract_relationship(args[0])
            return {
                "beneficiary": extraction.get("beneficiary", ""),
                "service_provider": extraction.get("service_provider", ""),
                "fee_recipient": extraction.get("fee_recipient", ""),
                "parse_error": extraction.get("parse_error", False),
                "disclaimer": DISCLAIMER
            }

        elif task == "model_relationship":
            if not args or not args[0]:
                return {"error": "Missing contract_text", "disclaimer": DISCLAIMER}
            contract_text = args[0]
            project_id = args[1] if len(args) > 1 and args[1] else ""
            extraction = self._extract_relationship(contract_text)
            relationship_id = f"relationship_{uuid.uuid4().hex[:12]}"
            doc = {
                "id": relationship_id,
                "created": datetime.now().isoformat(),
                "source_excerpt": contract_text[:500],
                "project_id": project_id,
            }
            doc.update(extraction)
            self.store_own_memory(relationship_id, json.dumps(doc))
            self._append_to_index(relationship_id)
            self.log(f"Modeled and stored relationship {relationship_id}")
            self._push_to_graph(doc, project_id)
            return doc

        elif task == "list_relationships":
            return {"relationship_ids": self._load_index(), "disclaimer": DISCLAIMER}

        elif task == "get_relationship":
            if not args or not args[0]:
                return {"error": "Usage: get_relationship <relationship_id>", "disclaimer": DISCLAIMER}
            doc = self._load_relationship(args[0])
            if doc is None:
                return {"error": f"Relationship {args[0]} not found", "disclaimer": DISCLAIMER}
            return doc

        elif task in ("query_relationship", "find_relationships"):
            if not args or not args[0]:
                return {"error": "Missing entity_identifier", "disclaimer": DISCLAIMER}
            entity_identifier = args[0]
            needle = entity_identifier.strip().lower()
            matches = []
            for relationship_id in self._load_index():
                doc = self._load_relationship(relationship_id)
                if not doc:
                    continue
                haystacks = [
                    str(doc.get("entity_a", "")), str(doc.get("entity_b", "")),
                    str(doc.get("beneficiary", "")), str(doc.get("service_provider", "")),
                    str(doc.get("fee_recipient", "")), str(doc.get("asset_owner", "")),
                    str(doc.get("custodian", ""))
                ]
                if any(needle in h.lower() for h in haystacks if h):
                    matches.append(doc)
            return {
                "entity_identifier": entity_identifier,
                "count": len(matches),
                "relationships": matches,
                "disclaimer": DISCLAIMER
            }

        elif task == "find_relationships_by_project":
            if not args or not args[0]:
                return {"error": "Usage: find_relationships_by_project <project_id>", "disclaimer": DISCLAIMER}
            project_id = args[0]
            matches = [
                doc for doc in (self._load_relationship(rid) for rid in self._load_index())
                if doc and doc.get("project_id") == project_id
            ]
            return {
                "project_id": project_id,
                "count": len(matches),
                "relationships": matches,
                "disclaimer": DISCLAIMER
            }

        elif task == "compare_relationships":
            if not args or len(args) < 2:
                return {"error": "Usage: compare_relationships <relationship_id_1> <relationship_id_2>", "disclaimer": DISCLAIMER}
            id1, id2 = args[0], args[1]
            raw1 = self._get_stored_value(self.retrieve_own_memory(id1))
            raw2 = self._get_stored_value(self.retrieve_own_memory(id2))
            if not raw1 or not raw2:
                return {"error": f"Could not load one or both relationships ({id1}, {id2})", "disclaimer": DISCLAIMER}
            try:
                doc1, doc2 = json.loads(raw1), json.loads(raw2)
            except Exception as e:
                return {"error": f"Failed to parse stored relationships: {e}", "disclaimer": DISCLAIMER}

            diff_fields = RELATIONSHIP_FIELDS
            differences = {}
            for field in diff_fields:
                v1, v2 = doc1.get(field), doc2.get(field)
                if v1 != v2:
                    differences[field] = {"relationship_1": v1, "relationship_2": v2}

            return {
                "relationship_1_id": id1,
                "relationship_2_id": id2,
                "differences": differences,
                "identical": len(differences) == 0,
                "disclaimer": DISCLAIMER
            }

        elif task == "search_cases":
            if not args or not args[0]:
                return {"error": "Usage: search_cases <query> [type]", "disclaimer": DISCLAIMER}
            query = args[0]
            case_type = args[1] if len(args) > 1 and args[1] else "r"
            tool_result = self.call_tool("courtlistener", "search", {"q": query, "type": case_type})
            return self._unwrap_tool_result(tool_result, disclaimer=True)

        elif task == "monitor_user":
            if not args or not args[0]:
                return {"error": "Usage: monitor_user <name> [rate: rt|dly|wly|mly]", "disclaimer": DISCLAIMER}
            name = args[0]
            rate = args[1] if len(args) > 1 and args[1] else "dly"
            tool_result = self.call_tool("courtlistener", "create_alert", {
                "name": f"Mycelial monitor: {name}",
                "query": {"type": "r", "party_name": name},
                "rate": rate,
            })
            return self._unwrap_tool_result(tool_result, disclaimer=True)

        elif task == "monitor_docket":
            if not args or not args[0]:
                return {"error": "Usage: monitor_docket <docket_id>", "disclaimer": DISCLAIMER}
            try:
                docket_id = int(args[0])
            except (TypeError, ValueError):
                return {"error": "docket_id must be an integer", "disclaimer": DISCLAIMER}
            tool_result = self.call_tool("courtlistener", "subscribe_docket_alert", {"docket": docket_id})
            return self._unwrap_tool_result(tool_result, disclaimer=True)

        elif task == "log_lesson":
            if not args or not args[0]:
                return {"error": "Usage: log_lesson <lesson_text> [strategy_type] [case_id] [tags_csv]", "disclaimer": DISCLAIMER}
            lesson_text = args[0]
            strategy_type = args[1] if len(args) > 1 and args[1] else "general"
            case_id = args[2] if len(args) > 2 and args[2] else ""
            tags = args[3] if len(args) > 3 and args[3] else ""
            lesson_id = f"lesson_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
            front_matter = (
                "---\n"
                f"lesson_id: {lesson_id}\n"
                f"created: {datetime.now().isoformat()}\n"
                f"strategy_type: {strategy_type}\n"
                f"case_id: {case_id}\n"
                f"tags: {tags}\n"
                "---\n\n"
            )
            lessons_dir = os.path.join(self.knowledge_dir, "lessons_learned")
            os.makedirs(lessons_dir, exist_ok=True)
            path = os.path.join(lessons_dir, f"{lesson_id}.md")
            with open(path, "w") as f:
                f.write(front_matter + lesson_text.strip() + "\n")
            self.refresh_cache()
            self.log(f"Logged lesson {lesson_id} (strategy_type={strategy_type})")
            return {
                "lesson_id": lesson_id,
                "path": f"lessons_learned/{lesson_id}.md",
                "strategy_type": strategy_type,
                "case_id": case_id,
                "tags": tags,
                "disclaimer": DISCLAIMER,
            }

        elif task == "query_lessons":
            if not args or not args[0]:
                return {"error": "Usage: query_lessons <query> [top_k]", "disclaimer": DISCLAIMER}
            top_k = int(args[1]) if len(args) > 1 and args[1] else 5
            results = self.query_cache(args[0], top_k=top_k, category="lessons_learned")
            return {"query": args[0], "results": results, "disclaimer": DISCLAIMER}

        elif task == "list_lessons":
            lessons = [doc for doc in self.cache.values() if doc["category"] == "lessons_learned"] if hasattr(self, "cache") else []
            lessons.sort(key=lambda d: d["mtime"], reverse=True)
            return {
                "count": len(lessons),
                "lessons": [
                    {"id": d["id"], "modified": datetime.fromtimestamp(d["mtime"]).isoformat()}
                    for d in lessons
                ],
                "disclaimer": DISCLAIMER,
            }

        else:
            return {"error": f"Unknown task: {task}", "disclaimer": DISCLAIMER}


if __name__ == "__main__":
    agent = LegalAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
