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

INFERENCE_SERVICE_URL = "http://localhost:8005/reason"
MODEL_SERVICE_URL = "http://localhost:8006/models/select"
DEFAULT_MODEL = "qwen2.5:1.5b"

DISCLAIMER = (
    "This output is generated automatically for informational purposes only. "
    "It is an extraction/structuring of the provided text, not legal advice, "
    "and should be reviewed by a qualified professional before being relied upon."
)

RELATIONSHIP_FIELDS = [
    "entity_a", "entity_b", "asset", "obligations", "rights",
    "beneficiary", "service_provider", "fee_recipient",
    "governing_law", "applicable_statutes"
]


class LegalAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="legal_agent",
            port=9011,
            capabilities=["parse_contract", "model_relationship", "extract_parties", "analyze_roles"],
            role="agent"
        )
        self.log("Legal Agent initialized (extraction/structuring only - no legal advice).")

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
        """Use the Inference Service to extract structured fields from contract text."""
        prompt = (
            "You are a contract-structuring assistant. Read the contract text below and "
            "extract ONLY the following fields as a single valid JSON object (no markdown "
            "fences, no commentary, no legal analysis or opinions):\n"
            "{\n"
            '  "entity_a": "",\n'
            '  "entity_b": "",\n'
            '  "asset": "",\n'
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
            "infer facts, and do not provide legal advice or opinions.\n\n"
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

    # ---------- Task handling ----------
    def handle_task(self, task, args, sender):
        self.log(f"Task {task} from {sender}")

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
            extraction = self._extract_relationship(contract_text)
            relationship_id = f"relationship_{uuid.uuid4().hex[:12]}"
            doc = {
                "id": relationship_id,
                "created": datetime.now().isoformat(),
                "source_excerpt": contract_text[:500],
            }
            doc.update(extraction)
            self.store_own_memory(relationship_id, json.dumps(doc))
            self._append_to_index(relationship_id)
            self.log(f"Modeled and stored relationship {relationship_id}")
            return doc

        elif task == "query_relationship":
            if not args or not args[0]:
                return {"error": "Missing entity_identifier", "disclaimer": DISCLAIMER}
            entity_identifier = args[0]
            needle = entity_identifier.strip().lower()
            matches = []
            for relationship_id in self._load_index():
                raw_value = self._get_stored_value(self.retrieve_own_memory(relationship_id))
                if not raw_value:
                    continue
                try:
                    doc = json.loads(raw_value)
                except Exception:
                    continue
                haystacks = [
                    str(doc.get("entity_a", "")), str(doc.get("entity_b", "")),
                    str(doc.get("beneficiary", "")), str(doc.get("service_provider", "")),
                    str(doc.get("fee_recipient", ""))
                ]
                if any(needle in h.lower() for h in haystacks if h):
                    matches.append(doc)
            return {
                "entity_identifier": entity_identifier,
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

            diff_fields = ["entity_a", "entity_b", "asset", "beneficiary", "service_provider",
                           "fee_recipient", "obligations", "rights", "governing_law", "applicable_statutes"]
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

        else:
            return {"error": f"Unknown task: {task}", "disclaimer": DISCLAIMER}


if __name__ == "__main__":
    agent = LegalAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
