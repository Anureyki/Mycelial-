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
# Capability routing: this agent names the KIND of thinking it needs and
# Model Service picks the model. It never names a vendor or a checkpoint,
# so swapping brains is a config edit in config/model_routing.json.
#
# The previous /models/select call passed `requirements` as a string where
# the service does requirements.items(), so every call 500'd and silently
# fell back to the hardcoded DEFAULT_MODEL. Model Service has never actually
# selected a model for this agent, and the lightweight/reasoning distinction
# below was decorative - both got the same 1.5b model.
CAPABILITY_FOR = {"reasoning": "synthesis", "lightweight": "reasoning"}

# Inference timeouts, sized for the hardware this actually runs on rather than
# for a GPU. Measured on the deployment box (i5-4570T, 4 threads, no GPU):
# llama3.2:3b generates at ~6.3 tokens/sec. A structured extraction asking for a
# 30-field JSON object is 400-600 tokens of output, so 80-95s of generation
# before prompt evaluation - against the previous 60s default, which meant those
# calls could never complete. They timed out, returned empty, and were reported
# to the caller as parse_error: the model looked like it had answered badly when
# it had not answered at all.
INFERENCE_TIMEOUT = int(os.getenv("AGENT_INFERENCE_TIMEOUT", "240"))
FALLBACK_TIMEOUT = int(os.getenv("AGENT_FALLBACK_TIMEOUT", "120"))
DEFAULT_MODEL = "qwen2.5:1.5b"

DISCLAIMER = (
    "This output is generated automatically for informational purposes only. "
    "It is an extraction/structuring of the provided text, not legal or fiduciary "
    "advice, and should be reviewed by a qualified professional before being relied upon."
)

TRUST_FIELDS = [
    "trust_type", "settlor", "trustee", "beneficiary", "trust_property",
    "duties", "powers", "obligations", "rights", "governing_law",
    "termination_conditions"
]

STATUTE_CITATION_RE = re.compile(r"\b\d+\s*U\.?S\.?C\.?\s*§*\s*\d+[a-zA-Z0-9\-]*", re.IGNORECASE)


class TrustAgent(AgentBase):
    # Words that claim a request for this agent. Declared here, not in
    # Boss - the orchestrator holds no domain vocabulary.
    ROUTING_TERMS = (
        "trust", "beneficiar", "settlor", "grantor", "corpus", "fiduciary",
        "estate", "probate", "\\bwill\\b", "testament",
    )

    def __init__(self):
        super().__init__(
            agent_id="trust_agent",
            port=9013,
            capabilities=[
                "parse_trust_document", "model_trust_relationship", "lookup",
                "list_relationships", "get_relationship", "find_relationships",
                "find_relationships_by_project", "compare_relationships",
                "refresh_cache", "query_cache", "cache_stats", "cache_manifest"
            ],
            role="agent"
        )
        # CAG: source docs live in knowledge_base/trust_agent/{statutes,trust_templates,
        # dictionary}/. Same poll-based refresh pattern as Legal/Accounting Agents.
        self.init_cag(cache_ttl=86400, watch_interval=300)
        self.subscribe_project_events()
        self.log("Trust Agent initialized (extraction/structuring only - no legal or fiduciary advice).")

    def on_project_event(self, project_id, event_type, data, sender):
        """Example event-driven reaction: when a project moves to the 'signature'
        stage, Trust Agent notes that trustee signature/authority should be
        confirmed. Illustrative logging, not an autonomous signing pipeline - see
        scripts/demo_workflow.py."""
        stage = data.get("data", {}).get("stage") if isinstance(data, dict) else None
        if event_type == "stage" and stage == "signature":
            self.log_to_audit(
                "project_event_reaction",
                f"project={project_id}: signature stage reached - trustee authority confirmation needed",
                level="info", metadata={"namespace": f"project_{project_id}"}
            )
            self.log(f"Reacting to project {project_id} entering signature: flagging for trustee authority check")
        else:
            self.log(f"Project event {project_id}/{event_type} from {sender} (no reaction configured)")

    # ---------- Model / Inference helpers ----------
    def _capability_for_task(self, requirements="reasoning"):
        """Map this agent's internal notion of task weight onto a routed
        capability. Returns a capability name, never a model name."""
        return CAPABILITY_FOR.get(requirements, "reasoning")

    def _call_inference(self, prompt, model_name=None, timeout=None, capability=None,
                        status=None, temperature=None):
        """Returns the model's text, or "" if it never answered.

        `status` is an optional dict the caller passes in to learn WHY it got "".
        Without it an empty return is ambiguous - a timeout and a model replying
        with nothing are indistinguishable from a genuinely unparseable answer,
        which is how a starved model gets misreported as a badly-behaved one."""
        if status is None:
            status = {}
        status["ok"] = False
        status["reason"] = None
        timeout = timeout or INFERENCE_TIMEOUT
        if model_name is None and capability is None:
            capability = self._capability_for_task("reasoning")
        try:
            resp = requests.post(
                INFERENCE_SERVICE_URL,
                json=dict({"prompt": prompt, "model": model_name} if model_name
                          else {"prompt": prompt, "capability": capability},
                          **({"temperature": temperature} if temperature is not None else {})),
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

        # Second chance on a deliberately cheaper capability - a small model
        # answering beats no answer when the primary route is down.
        fallback_cap = self._capability_for_task("lightweight")
        if fallback_cap and fallback_cap != capability:
            try:
                resp = requests.post(
                    INFERENCE_SERVICE_URL,
                    json=dict({"prompt": prompt, "capability": fallback_cap},
                              **({"temperature": temperature} if temperature is not None else {})),
                    timeout=FALLBACK_TIMEOUT
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success"):
                        status["ok"] = True
                        status["reason"] = f"served by fallback capability '{fallback_cap}'"
                        return data.get("result", "")
            except Exception as e:
                self.log(f"Fallback inference call failed: {e}")

        self.log("Inference unavailable after fallback attempt.")
        return ""

    # ---------- JSON extraction helpers ----------
    def _safe_parse_json(self, raw):
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

    # ---------- CAG-backed lookups ----------
    def _extract_citations(self, text):
        return list({m.group(0).strip() for m in STATUTE_CITATION_RE.finditer(text)})

    def _cache_context_for(self, text, top_k=3):
        hits = self.query_cache(text[:1000], top_k=top_k)
        for citation in self._extract_citations(text):
            hits.extend(self.query_cache(citation, top_k=1))
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

    def _extract_trust_relationship(self, trust_text, model=None):
        cache_hits = self._cache_context_for(trust_text)
        context_block = self._format_context_block(cache_hits)
        prompt = (
            context_block +
            "You are a trust-document-structuring assistant. Read the trust document text "
            "below and extract ONLY the following fields as a single valid JSON object (no "
            "markdown fences, no commentary, no legal or fiduciary advice):\n"
            "{\n"
            '  "trust_type": "",\n'
            '  "settlor": "",\n'
            '  "trustee": "",\n'
            '  "beneficiary": "",\n'
            '  "trust_property": "",\n'
            '  "duties": [],\n'
            '  "powers": [],\n'
            '  "obligations": [],\n'
            '  "rights": [],\n'
            '  "governing_law": "",\n'
            '  "termination_conditions": ""\n'
            "}\n"
            "trust_type should be a short label such as revocable, irrevocable, testamentary, "
            "or living_trust. duties/powers describe the trustee specifically; obligations/"
            "rights may cover other parties. If a field cannot be determined from the text, "
            "use an empty string or empty list. Only extract and structure what is explicitly "
            "stated - do not infer facts, and do not provide legal or fiduciary advice.\n\n"
            f"Trust document text:\n\"\"\"\n{trust_text}\n\"\"\"\n\nJSON:"
        )
        raw = self._call_inference(prompt, model_name=model)
        parsed, parse_error = self._safe_parse_json(raw)
        if parsed is None:
            parsed = {field: ([] if field in ("duties", "powers", "obligations", "rights") else "") for field in TRUST_FIELDS}
        else:
            for field in TRUST_FIELDS:
                parsed.setdefault(field, [] if field in ("duties", "powers", "obligations", "rights") else "")
        parsed["parse_error"] = parse_error
        if parse_error:
            parsed["raw_model_output"] = raw
        parsed["cache_sources"] = [h["id"] for h in cache_hits]
        parsed["disclaimer"] = DISCLAIMER
        return parsed

    # ---------- Relationship storage helpers ----------
    def _get_stored_value(self, retrieval_result):
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
        """Best-effort: keep Boss's relationship graph in sync. Failure here doesn't
        fail the caller - the record is already stored in this agent's own memory."""
        try:
            graph_rel = from_legacy_fields(doc, domain="trust", project_id=project_id)
            resp = self.send_a2a("boss_agent", "update_graph", {
                "action": "ingest_relationship",
                "relationship": graph_rel,
                "project_id": project_id,
            })
            if not resp or (isinstance(resp, dict) and resp.get("result", {}).get("error")):
                self.log(f"Graph push for {doc.get('id')} did not confirm success: {resp}")
        except Exception as e:
            self.log(f"Graph push failed for {doc.get('id')}: {e}")

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
            # The corpus comes first, before the cache and well before the web.
            # This agent has no reference/trust_agent/ yet, so today this is a
            # no-op - which is exactly why it is worth wiring now. The same
            # omission in accounting_agent left 2,108 sections unreachable for
            # weeks, because a corpus dropped into an agent that never calls
            # lookup_reference is silently ignored rather than reported.
            passages = self.lookup_reference(term)
            if passages:
                return {"term": term, "source": "reference/trust_agent corpus",
                        "results": passages, "disclaimer": DISCLAIMER}
            hits = self.query_cache(term, top_k=3)
            if hits:
                return {"term": term, "source": "cache", "results": hits, "disclaimer": DISCLAIMER}
            # Cache had nothing - try a public web search via PQA Agent before
            # falling back to raw inference (which can hallucinate).
            web = self.search_public(f"{term} trust fiduciary law definition")
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

            raw = self._call_inference(
                f"Briefly define or explain the following trust/fiduciary term or statute "
                f"citation, in one or two sentences, without giving legal advice: {term}"
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

        elif task == "parse_trust_document":
            if not args or not args[0]:
                return {"error": "Missing trust_text", "disclaimer": DISCLAIMER}
            return self._extract_trust_relationship(args[0])

        elif task == "model_trust_relationship":
            if not args or not args[0]:
                return {"error": "Missing trust_text", "disclaimer": DISCLAIMER}
            trust_text = args[0]
            project_id = args[1] if len(args) > 1 and args[1] else ""
            extraction = self._extract_trust_relationship(trust_text)
            relationship_id = f"trust_{uuid.uuid4().hex[:12]}"
            doc = {
                "id": relationship_id,
                "created": datetime.now().isoformat(),
                "source_excerpt": trust_text[:500],
                "project_id": project_id,
            }
            doc.update(extraction)
            self.store_own_memory(relationship_id, json.dumps(doc))
            self._append_to_index(relationship_id)
            self.log(f"Modeled and stored trust relationship {relationship_id}")
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

        elif task == "find_relationships":
            if not args or not args[0]:
                return {"error": "Missing entity_identifier", "disclaimer": DISCLAIMER}
            needle = args[0].strip().lower()
            matches = []
            for relationship_id in self._load_index():
                doc = self._load_relationship(relationship_id)
                if not doc:
                    continue
                haystacks = [
                    str(doc.get("settlor", "")), str(doc.get("trustee", "")),
                    str(doc.get("beneficiary", "")),
                ]
                if any(needle in h.lower() for h in haystacks if h):
                    matches.append(doc)
            return {
                "entity_identifier": args[0],
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
            doc1, doc2 = self._load_relationship(id1), self._load_relationship(id2)
            if not doc1 or not doc2:
                return {"error": f"Could not load one or both relationships ({id1}, {id2})", "disclaimer": DISCLAIMER}
            differences = {}
            for field in TRUST_FIELDS:
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

        else:
            return {"error": f"Unknown task: {task}", "disclaimer": DISCLAIMER}


if __name__ == "__main__":
    agent = TrustAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
