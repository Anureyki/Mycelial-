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
    "It is an extraction/structuring of the provided text (or of previously stored, "
    "cached, or locally-uploaded records), not tax or financial advice, and should be "
    "reviewed by a qualified professional before being relied upon."
)

INSTRUMENT_FIELDS = [
    "instrument_type", "creditor", "debtor", "account_id", "principal_amount",
    "interest_rate", "payment_schedule", "maturity_date", "tax_treatment",
    "applicable_forms", "obligations", "rights", "governing_rules"
]

STATUTE_CITATION_RE = re.compile(r"\b\d+\s*U\.?S\.?C\.?\s*§*\s*\d+[a-zA-Z0-9\-]*", re.IGNORECASE)
FORM_CITATION_RE = re.compile(r"\b(?:Form\s*)?(1099(?:-[A-Z]+)?|W-?2|W-?9|1040(?:-[A-Z]+)?|1120|1065|K-?1)\b", re.IGNORECASE)


class AccountingAgent(AgentBase):
    # Words that claim a request for this agent. Declared here, not in
    # Boss - the orchestrator holds no domain vocabulary.
    ROUTING_TERMS = (
        "ledger", "journal entry", "balance sheet", "income statement",
        "cash ?flow", "accrual", "depreciat", "amortiz", "audit",
        "\\bgaap\\b", "\\bifrs\\b", "\\basc\\b", "\\bedgar\\b", "10-?k", "10-?q",
        "beneficial interest", "equitable interest", "custodian", "trustee",
        "disbursement", "receivable", "payable", "reconcil", "invoice",
    )

    def __init__(self):
        super().__init__(
            agent_id="accounting_agent",
            port=9012,
            capabilities=[
                "parse_financial_instrument", "assess_tax_liability", "track_account_balance",
                "lookup", "list_relationships", "get_relationship", "find_relationships",
                "find_relationships_by_project",
                "refresh_cache", "query_cache", "cache_stats", "cache_manifest",
                "map_transaction_roles", "log_transaction", "check_ledger_integrity",
                "map_assets_liabilities", "prepare_documentation_package", "check_budget_constraint",
                "forecast_cash_flow", "build_budget"
            ],
            role="agent"
        )
        # CAG: source docs live in knowledge_base/accounting_agent/{irs_forms,gaap_ifrs,
        # instruments,statements,trust_estate}/. instruments/statements/trust_estate are
        # expected to be the owner's own private records - never fetched or fabricated,
        # only picked up if manually placed there.
        self.init_cag(cache_ttl=86400, watch_interval=300)
        self.subscribe_project_events()
        self.log("Accounting Agent initialized (extraction/structuring only - no tax or financial advice).")

    def on_project_event(self, project_id, event_type, data, sender):
        """Example event-driven reaction: when a project moves to the 'payment' stage,
        Accounting Agent notes that an invoice/payment record should be prepared.
        Illustrative logging, not an autonomous invoicing pipeline - see
        scripts/demo_workflow.py."""
        stage = data.get("data", {}).get("stage") if isinstance(data, dict) else None
        if event_type == "stage" and stage == "payment":
            self.log_to_audit(
                "project_event_reaction",
                f"project={project_id}: payment stage reached - invoice/payment record needed",
                level="info", metadata={"namespace": f"project_{project_id}"}
            )
            self.log(f"Reacting to project {project_id} entering payment: flagging for invoice preparation")
        else:
            self.log(f"Project event {project_id}/{event_type} from {sender} (no reaction configured)")

    # ---------- Model / Inference helpers ----------
    def _capability_for_task(self, requirements="reasoning"):
        """Map this agent's internal notion of task weight onto a routed
        capability. Returns a capability name, never a model name."""
        return CAPABILITY_FOR.get(requirements, "reasoning")

    def _call_inference(self, prompt, model_name=None, timeout=None, capability=None,
                        status=None, temperature=None):
        """Call the Inference Service, falling back to an alternate model
        via the Model Service if the primary call is slow or unavailable."""
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
        # strict=False allows literal control characters (raw newlines, tabs) inside
        # string values - small local models frequently wrap long field values across
        # lines without escaping them as \n, which strict JSON parsing rejects outright.
        try:
            return json.loads(text, strict=False), False
        except Exception:
            pass
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0), strict=False), False
            except Exception:
                pass
        return None, True

    # ---------- CAG-backed lookups ----------
    def _extract_citations(self, text):
        cites = {m.group(0).strip() for m in STATUTE_CITATION_RE.finditer(text)}
        cites |= {m.group(0).strip() for m in FORM_CITATION_RE.finditer(text)}
        return list(cites)

    def _cache_context_for(self, text, top_k=3):
        """Cache-first context gathering (never calls inference itself)."""
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

    # ---------- Instrument extraction ----------
    def _extract_instrument(self, text, model=None):
        cache_hits = self._cache_context_for(text)
        context_block = self._format_context_block(cache_hits)
        prompt = (
            context_block +
            "You are a financial-document-structuring assistant. Read the text below and "
            "extract ONLY the following fields as a single valid JSON object (no markdown "
            "fences, no commentary, no tax or financial advice):\n"
            "{\n"
            '  "instrument_type": "",\n'
            '  "creditor": "",\n'
            '  "debtor": "",\n'
            '  "account_id": "",\n'
            '  "principal_amount": "",\n'
            '  "interest_rate": "",\n'
            '  "payment_schedule": "",\n'
            '  "maturity_date": "",\n'
            '  "tax_treatment": "",\n'
            '  "applicable_forms": [],\n'
            '  "obligations": [],\n'
            '  "rights": [],\n'
            '  "governing_rules": []\n'
            "}\n"
            "instrument_type should be a short label such as promissory_note, loan_agreement, "
            "bank_statement, credit_statement, utility_statement, trust_agreement, or a specific "
            "IRS form number. If a field cannot be determined from the text, use an empty string "
            "or empty list. Only extract and structure what is explicitly stated - do not infer "
            "facts, and do not provide tax or financial advice. If the cached reference material "
            "above names an applicable IRS form or accounting standard, you may reflect it in "
            "applicable_forms/governing_rules - otherwise leave them as stated in the text.\n\n"
            f"Document text:\n\"\"\"\n{text}\n\"\"\"\n\nJSON:"
        )
        raw = self._call_inference(prompt, model_name=model)
        parsed, parse_error = self._safe_parse_json(raw)
        if parsed is None:
            parsed = {field: ([] if field in ("applicable_forms", "obligations", "rights", "governing_rules") else "") for field in INSTRUMENT_FIELDS}
        else:
            for field in INSTRUMENT_FIELDS:
                parsed.setdefault(field, [] if field in ("applicable_forms", "obligations", "rights", "governing_rules") else "")
        parsed["parse_error"] = parse_error
        if parse_error:
            parsed["raw_model_output"] = raw
        parsed["cache_sources"] = [h["id"] for h in cache_hits]
        parsed["disclaimer"] = DISCLAIMER
        return parsed

    # ---------- Instrument storage helpers ----------
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
        raw = self._get_stored_value(self.retrieve_own_memory("instrument_index"))
        if not raw:
            return []
        try:
            index = json.loads(raw)
            return index if isinstance(index, list) else []
        except Exception:
            return []

    def _append_to_index(self, instrument_id):
        index = self._load_index()
        if instrument_id not in index:
            index.append(instrument_id)
            self.store_own_memory("instrument_index", json.dumps(index))

    def _load_instrument(self, instrument_id):
        raw = self._get_stored_value(self.retrieve_own_memory(instrument_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def _find_instruments(self, predicate):
        matches = []
        for instrument_id in self._load_index():
            doc = self._load_instrument(instrument_id)
            if doc and predicate(doc):
                matches.append(doc)
        return matches

    def _push_to_graph(self, doc, project_id):
        """Best-effort: keep Boss's relationship graph in sync when an instrument is
        parsed. Failure here doesn't fail the caller - the record is already stored
        in this agent's own memory regardless."""
        try:
            graph_rel = from_legacy_fields(doc, domain="financial", project_id=project_id)
            resp = self.send_a2a("boss_agent", "update_graph", {
                "action": "ingest_relationship",
                "relationship": graph_rel,
                "project_id": project_id,
            })
            if not resp or (isinstance(resp, dict) and resp.get("result", {}).get("error")):
                self.log(f"Graph push for {doc.get('id')} did not confirm success: {resp}")
        except Exception as e:
            self.log(f"Graph push failed for {doc.get('id')}: {e}")

    # ---------- Transaction ledger (new storage - kept local, not graph-pushed;
    # transaction-level detail would flood a graph meant for durable relationships,
    # not granular line items. Instruments keep pushing as they already do.) ----------
    def _load_transaction_index(self):
        raw = self._get_stored_value(self.retrieve_own_memory("transaction_index"))
        if not raw:
            return []
        try:
            index = json.loads(raw)
            return index if isinstance(index, list) else []
        except Exception:
            return []

    def _append_to_transaction_index(self, transaction_id):
        index = self._load_transaction_index()
        if transaction_id not in index:
            index.append(transaction_id)
            self.store_own_memory("transaction_index", json.dumps(index))

    def _load_record(self, record_id):
        raw = self._get_stored_value(self.retrieve_own_memory(record_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def _get_transactions(self, entity_or_project=None):
        transactions = []
        for txn_id in self._load_transaction_index():
            txn = self._load_record(txn_id)
            if not txn:
                continue
            if entity_or_project:
                needle = entity_or_project.strip().lower()
                haystacks = [str(txn.get("payor", "")), str(txn.get("payee", "")), str(txn.get("project_id", ""))]
                if not any(needle in h.lower() for h in haystacks if h):
                    continue
            transactions.append(txn)
        transactions.sort(key=lambda t: t.get("timestamp", ""))
        return transactions

    # ---------- Task handling ----------
    def receive_finding(self, kind, payload, sender):
        """Act on a finding another domain surfaced.

        Grow reads an equipment invoice because the grower shows it one - the
        light matters to the grow. The money side is not Grow's to hold, and a
        purchase that lives only in a plant's notes is invisible to every
        question about what was spent on this grow.

        Only the fields a ledger needs cross the boundary. The referral carries
        no shipping address, no phone number and no order email; personal data
        has no business in a transaction record just because it appeared in the
        same screenshot."""
        if kind not in ("equipment_purchase", "expense"):
            return super().receive_finding(kind, payload, sender)

        amount = payload.get("amount")
        item = payload.get("item") or "unspecified item"
        if amount is None:
            return {"recorded": False,
                    "why": f"a {kind} referral needs an amount; got {sorted(payload)}"}
        txn = self.handle_task("log_transaction", {
            "payor": payload.get("payor") or "principal",
            "payee": payload.get("payee") or "unknown vendor",
            "amount": amount,
            "date": payload.get("date", ""),
            "purpose": f"{kind}: {item}",
            "documentation_ref": payload.get("documentation_ref", ""),
            "category": payload.get("category") or "grow_equipment",
            "project_id": payload.get("project_id", ""),
        }, sender)
        inner = txn.get("transaction") if isinstance(txn, dict) else None
        if not inner:
            return {"recorded": False, "why": f"ledger rejected it: {txn}"}
        return {"recorded": inner["id"], "acted": True,
                "note": f"Logged to the ledger as {inner['id']}: "
                        f"{payload.get('payee') or 'vendor'} ${amount} for {item}."}

    def handle_task(self, task, args, sender):
        self.log(f"Task {task} from {sender}")

        cag_result = self.try_handle_cag_task(task, args)
        if cag_result is not None:
            return cag_result

        if task == "lookup":
            if not args or not args[0]:
                return {"error": "Usage: lookup <term_or_form_or_citation>", "disclaimer": DISCLAIMER}
            term = args[0]
            # The corpus comes first. This path was cache -> web -> model, so
            # a section of the Exchange Act sitting in this agent's own
            # reference/ reached the open web before it reached the books the
            # agent owns - the same fault already corrected in Legal.
            passages = self.lookup_reference(term)
            if passages:
                return {"term": term, "source": "reference/accounting_agent corpus",
                        "results": passages, "disclaimer": DISCLAIMER}
            hits = self.query_cache(term, top_k=3)
            if hits:
                return {"term": term, "source": "cache", "results": hits, "disclaimer": DISCLAIMER}
            # Ask Legal before asking the open web.
            #
            # Accounting owns ASC, IFRS and the reporting regulations. Legal
            # owns the statutes, the CFR, the state codes and the canons. A
            # figure in these books is routinely governed by an authority that
            # lives in Legal's corpus, and Accounting has no business holding a
            # second copy of it - that is two sources of truth, which is the
            # failure this whole architecture is built to avoid.
            #
            # So Accounting asks. Note there is no keyword test for "is this a
            # legal question": guessing a subject from vocabulary is the exact
            # router failure CLAUDE.md documents. It simply prefers a sibling
            # agent's verified corpus over an unverified web search, every
            # time. Legal answers [] when it holds nothing, which costs one
            # local call.
            borrowed = self.ask_peer_corpus("legal_agent", term)
            if borrowed:
                return {"term": term, "source": "legal_agent corpus (cross-domain)",
                        "note": "Answered from the Legal Agent's reference corpus. "
                                "Accounting does not hold this authority and did "
                                "not interpret it - the citation is reproduced as "
                                "Legal returned it.",
                        "results": borrowed, "disclaimer": DISCLAIMER}
            # Nothing local and nothing from a sibling - try a public web search
            # via PQA Agent before falling back to raw inference.
            web = self.search_public(f"{term} accounting tax IRS GAAP definition")
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
                f"Briefly define or explain the following accounting/tax term, form, or "
                f"standard, in one or two sentences, without giving tax or financial advice: {term}"
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

        elif task == "parse_financial_instrument":
            if not args or not args[0]:
                return {"error": "Missing document_text", "disclaimer": DISCLAIMER}
            document_text = args[0]
            project_id = args[1] if len(args) > 1 and args[1] else ""
            extraction = self._extract_instrument(document_text)
            instrument_id = f"instrument_{uuid.uuid4().hex[:12]}"
            doc = {
                "id": instrument_id,
                "created": datetime.now().isoformat(),
                "source_excerpt": document_text[:500],
                "project_id": project_id,
            }
            doc.update(extraction)
            self.store_own_memory(instrument_id, json.dumps(doc))
            self._append_to_index(instrument_id)
            self.log(f"Parsed and stored instrument {instrument_id}")
            self._push_to_graph(doc, project_id)
            return doc

        elif task == "list_relationships":
            return {"relationship_ids": self._load_index(), "disclaimer": DISCLAIMER}

        elif task == "get_relationship":
            if not args or not args[0]:
                return {"error": "Usage: get_relationship <instrument_id>", "disclaimer": DISCLAIMER}
            doc = self._load_instrument(args[0])
            if doc is None:
                return {"error": f"Instrument {args[0]} not found", "disclaimer": DISCLAIMER}
            return doc

        elif task == "find_relationships":
            if not args or not args[0]:
                return {"error": "Missing entity_identifier", "disclaimer": DISCLAIMER}
            needle = args[0].strip().lower()
            matches = self._find_instruments(
                lambda d: needle in str(d.get("creditor", "")).lower()
                or needle in str(d.get("debtor", "")).lower()
                or needle in str(d.get("account_id", "")).lower()
            )
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
            matches = self._find_instruments(lambda d: d.get("project_id") == project_id)
            return {
                "project_id": project_id,
                "count": len(matches),
                "relationships": matches,
                "disclaimer": DISCLAIMER
            }

        elif task == "track_account_balance":
            if not args or not args[0]:
                return {"error": "Usage: track_account_balance <account_id>", "disclaimer": DISCLAIMER}
            account_id = args[0].strip().lower()
            matches = self._find_instruments(
                lambda d: account_id in str(d.get("account_id", "")).strip().lower()
            )
            total = 0.0
            counted = 0
            for doc in matches:
                amount = doc.get("principal_amount", "")
                try:
                    total += float(re.sub(r"[^0-9.\-]", "", str(amount)) or 0)
                    counted += 1
                except ValueError:
                    pass
            return {
                "account_id": args[0],
                "instruments_found": len(matches),
                "amounts_parsed": counted,
                "best_effort_total": total if counted else None,
                "instruments": matches,
                "note": "Balance tracking is Phase 1 (locally stored/uploaded records only - no "
                        "live bank API connection). best_effort_total sums principal_amount fields "
                        "that could be parsed as numbers; it is not a reconciled ledger balance.",
                "disclaimer": DISCLAIMER
            }

        elif task == "assess_tax_liability":
            if not args or len(args) < 2:
                return {"error": "Usage: assess_tax_liability <entity> <year>", "disclaimer": DISCLAIMER}
            entity, year = args[0], args[1]
            needle = entity.strip().lower()
            matches = self._find_instruments(
                lambda d: needle in str(d.get("creditor", "")).lower()
                or needle in str(d.get("debtor", "")).lower()
            )
            cache_hits = self._cache_context_for(f"{entity} tax liability {year} IRS", top_k=5)
            if not matches and not cache_hits:
                return {
                    "entity": entity,
                    "year": year,
                    "error": "Insufficient cached or stored data to assess tax liability - no "
                             "matching instruments and no relevant knowledge-base entries found. "
                             "Store relevant instruments via parse_financial_instrument and/or add "
                             "applicable IRS materials to the knowledge base first.",
                    "disclaimer": DISCLAIMER
                }
            context_block = self._format_context_block(cache_hits)
            instruments_summary = "\n".join(
                f"- {d.get('instrument_type', 'unknown')}: creditor={d.get('creditor', '')}, "
                f"debtor={d.get('debtor', '')}, amount={d.get('principal_amount', '')}, "
                f"forms={d.get('applicable_forms', [])}"
                for d in matches
            ) or "(none stored)"
            prompt = (
                context_block +
                f"Entity: {entity}\nTax year: {year}\n\n"
                f"Stored financial instruments involving this entity:\n{instruments_summary}\n\n"
                "Based ONLY on the cached reference material and stored instruments above, "
                "list the tax-relevant items you can identify (e.g. reportable interest, forms "
                "likely required) as a JSON object: "
                '{"taxable_items": [], "likely_forms": [], "notes": ""}. '
                "If the available information is insufficient to say anything concrete, say so "
                "in notes and leave the lists empty. Do not estimate a dollar liability figure "
                "and do not give tax advice - only structure what the cached/stored data shows."
            )
            raw = self._call_inference(prompt)
            parsed, parse_error = self._safe_parse_json(raw)
            if parsed is None:
                parsed = {"taxable_items": [], "likely_forms": [], "notes": ""}
            return {
                "entity": entity,
                "year": year,
                "instruments_considered": [d["id"] for d in matches],
                "cache_sources": [h["id"] for h in cache_hits],
                "parse_error": parse_error,
                **parsed,
                "disclaimer": DISCLAIMER
            }

        elif task == "map_transaction_roles":
            instrument_id = args.get("instrument_id") if isinstance(args, dict) else (args[0] if args else None)
            if not instrument_id:
                return {"error": "Usage: map_transaction_roles <instrument_id>", "disclaimer": DISCLAIMER}
            doc = self._load_instrument(instrument_id)
            if doc is None:
                return {"error": f"Instrument {instrument_id} not found", "disclaimer": DISCLAIMER}
            return {
                "instrument_id": instrument_id,
                "roles": {
                    "payor": doc.get("debtor", ""),
                    "payee": doc.get("creditor", ""),
                    "custodian": doc.get("account_id", ""),
                    "purpose": doc.get("instrument_type", ""),
                    "documentation": doc.get("applicable_forms", []),
                },
                "note": "Roles are derived from the stored instrument extraction, not assumed.",
                "disclaimer": DISCLAIMER,
            }

        elif task == "case_add_obligation":
            # Beyond bookkeeping. A ledger answers "what was paid"; an
            # obligation answers what is OWED, on what cadence, and WHO IS
            # AUTHORISED to pay it. A rent obligation met by the wrong payor,
            # or met with no receipt, looks identical to one in good standing
            # if all you keep is the amount.
            if not args.get("case_id") or not args.get("name") or args.get("amount") is None:
                return {"error": "Usage: {case_id, name, amount, [cadence], [due_day], "
                                 "[authorized_payors], [note]}", "disclaimer": DISCLAIMER}
            out = self.case(args["case_id"]).add_obligation(
                args["name"], args["amount"], args.get("cadence", "monthly"),
                args.get("due_day"), args.get("authorized_payors"), args.get("note", ""))
            out["disclaimer"] = DISCLAIMER
            return out

        elif task == "case_record_payment":
            if not args.get("case_id") or not args.get("obligation_id"):
                return {"error": "Usage: {case_id, obligation_id, amount, paid_on, payor, "
                                 "[evidence_ref]}", "disclaimer": DISCLAIMER}
            out = self.case(args["case_id"]).record_payment(
                args["obligation_id"], args.get("amount"), args.get("paid_on", ""),
                args.get("payor", ""), args.get("evidence_ref", ""), args.get("note", ""))
            if isinstance(out, dict) and out.get("payment"):
                p = out["payment"]
                if not p["has_evidence"]:
                    out["warning"] = ("Recorded with NO evidence reference. A payment that "
                                      "cannot be evidenced is contestable - attach the "
                                      "receipt, statement line or ledger entry.")
                if not p["payor_authorized"]:
                    out["warning"] = ((out.get("warning", "") + " ").strip() +
                                      f" Payor {p['payor']!r} is not on this obligation's "
                                      "authorised list.").strip()
            out["disclaimer"] = DISCLAIMER
            return out

        elif task == "case_obligation_status":
            if not args.get("case_id"):
                return {"error": "case_obligation_status needs a case_id",
                        "disclaimer": DISCLAIMER}
            out = self.case(args["case_id"]).obligation_status()
            out["disclaimer"] = DISCLAIMER
            return out

        elif task == "log_transaction":
            if not isinstance(args, dict) or not args.get("payor") or not args.get("payee") or args.get("amount") is None:
                return {"error": "Usage: {payor, payee, amount, [date], [purpose], [documentation_ref], [category], [project_id]}", "disclaimer": DISCLAIMER}
            txn_id = f"transaction_{uuid.uuid4().hex[:12]}"
            txn = {
                "id": txn_id,
                "timestamp": datetime.now().isoformat(),
                "payor": args["payor"],
                "payee": args["payee"],
                "amount": args["amount"],
                "date": args.get("date", ""),
                "purpose": args.get("purpose", ""),
                "documentation_ref": args.get("documentation_ref", ""),
                "category": args.get("category", ""),
                "project_id": args.get("project_id", ""),
            }
            self.store_own_memory(txn_id, json.dumps(txn))
            self._append_to_transaction_index(txn_id)
            return {"transaction": txn, "disclaimer": DISCLAIMER}

        elif task == "check_ledger_integrity":
            entity_or_project = args.get("entity_or_project") if isinstance(args, dict) else (args[0] if args else None)
            transactions = self._get_transactions(entity_or_project)
            flags = []

            seen = {}
            for t in transactions:
                key = (str(t.get("payee", "")).strip().lower(), str(t.get("amount", "")), str(t.get("date", "")))
                if key in seen:
                    flags.append({"type": "duplicate", "transaction_ids": [seen[key], t["id"]], "note": "Same payee/amount/date as another logged transaction."})
                else:
                    seen[key] = t["id"]
                if not t.get("documentation_ref"):
                    flags.append({"type": "unsupported_balance", "transaction_id": t["id"], "note": "No documentation_ref - balance is unsupported by a record."})
                if not t.get("category"):
                    flags.append({"type": "inconsistent_classification", "transaction_id": t["id"], "note": "No category assigned."})

            return {
                "entity_or_project": entity_or_project,
                "transactions_reviewed": len(transactions),
                "flags": flags,
                "recommendation": {
                    "observation": f"{len(transactions)} transaction(s) reviewed, {len(flags)} flag(s) raised.",
                    "reason": "Missing documentation or categorization, and duplicate-looking entries, are the most common sources of reconciliation failures.",
                    "action": "Add missing documentation_ref/category fields and resolve flagged duplicates." if flags else "No integrity issues found.",
                    "confidence": "medium",
                },
                "disclaimer": DISCLAIMER,
            }

        elif task == "map_assets_liabilities":
            entity = args.get("entity") if isinstance(args, dict) else (args[0] if args else None)
            if not entity:
                return {"error": "Missing entity", "disclaimer": DISCLAIMER}
            needle = entity.strip().lower()
            assets, liabilities = [], []
            for doc in self._find_instruments(lambda d: True):
                creditor = str(doc.get("creditor", "")).lower()
                debtor = str(doc.get("debtor", "")).lower()
                if needle in creditor:
                    assets.append(doc)
                elif needle in debtor:
                    liabilities.append(doc)
            transactions = self._get_transactions(entity)
            return {
                "entity": entity,
                "assets": assets,
                "liabilities": liabilities,
                "related_transactions": transactions,
                "note": "Assets = instruments where entity is creditor/beneficiary; liabilities = instruments where entity is debtor. Derived from stored data, not a reconciled balance sheet.",
                "disclaimer": DISCLAIMER,
            }

        elif task == "check_budget_constraint":
            # Direct-consultation endpoint for other agents (e.g. Grow Agent
            # checking before recommending a purchase) - returns a minimal
            # structured constraint, never the underlying transaction/ledger
            # data. Callers get a yes/no and a number, not 10,000 lines of
            # transactions to interpret themselves.
            if not isinstance(args, dict) or args.get("estimated_cost") is None:
                return {"error": "Usage: {estimated_cost, [purpose], [entity]}", "disclaimer": DISCLAIMER}
            try:
                estimated_cost = float(args["estimated_cost"])
            except (TypeError, ValueError):
                return {"error": "estimated_cost must be a number", "disclaimer": DISCLAIMER}
            entity = args.get("entity")
            transactions = self._get_transactions(entity)
            if not transactions:
                return {
                    "within_budget": None,
                    "available_discretionary": None,
                    "note": "No logged transactions to evaluate against - insufficient data for a budget constraint check.",
                    "disclaimer": DISCLAIMER,
                }
            income = sum(float(t["amount"]) for t in transactions if str(t.get("category", "")).lower() in ("income", "revenue"))
            expenses = sum(float(t["amount"]) for t in transactions if str(t.get("category", "")).lower() not in ("income", "revenue"))
            available_discretionary = income - expenses
            return {
                "within_budget": estimated_cost <= available_discretionary,
                "available_discretionary": round(available_discretionary, 2),
                "note": f"Estimated cost {estimated_cost} vs {available_discretionary:.2f} available (from {len(transactions)} logged transaction(s)).",
                "disclaimer": DISCLAIMER,
            }

        elif task == "forecast_cash_flow":
            entity_or_project = args.get("entity_or_project") if isinstance(args, dict) else (args[0] if args else None)
            horizon_days = args.get("horizon_days", 30) if isinstance(args, dict) else 30
            transactions = self._get_transactions(entity_or_project)
            if not transactions:
                return {
                    "entity_or_project": entity_or_project,
                    "forecast": None,
                    "note": "No logged transactions to forecast from.",
                    "disclaimer": DISCLAIMER,
                }
            timestamps = [datetime.fromisoformat(t["timestamp"]) for t in transactions]
            span_days = max((max(timestamps) - min(timestamps)).total_seconds() / 86400, 1)
            income = sum(float(t["amount"]) for t in transactions if str(t.get("category", "")).lower() in ("income", "revenue"))
            expenses = sum(float(t["amount"]) for t in transactions if str(t.get("category", "")).lower() not in ("income", "revenue"))
            net_per_day = (income - expenses) / span_days
            projected_net_change = net_per_day * horizon_days
            return {
                "entity_or_project": entity_or_project,
                "horizon_days": horizon_days,
                "observed_span_days": round(span_days, 1),
                "income_to_date": round(income, 2),
                "expenses_to_date": round(expenses, 2),
                "net_per_day": round(net_per_day, 2),
                "projected_net_change": round(projected_net_change, 2),
                "note": f"Based on {len(transactions)} transaction(s) over {span_days:.1f} day(s) - projection assumes the recent rate continues, not a guarantee.",
                "disclaimer": DISCLAIMER,
            }

        elif task == "build_budget":
            entity_or_project = args.get("entity_or_project") if isinstance(args, dict) else (args[0] if args else None)
            if not entity_or_project:
                return {"error": "Missing entity_or_project", "disclaimer": DISCLAIMER}
            transactions = self._get_transactions(entity_or_project)
            if not transactions:
                return {"entity_or_project": entity_or_project, "budget_by_category": {}, "note": "No logged transactions to build a budget from.", "disclaimer": DISCLAIMER}
            spending_by_category = {}
            for t in transactions:
                cat = t.get("category") or "uncategorized"
                spending_by_category[cat] = spending_by_category.get(cat, 0) + float(t["amount"])
            prompt = (
                "Based on this spending breakdown by category, suggest a simple monthly budget "
                "allocation. Produce a single valid JSON object:\n"
                '{"budget_by_category": {}, "notes": ""}\n\n'
                f"Spending by category (all logged transactions, not necessarily one month):\n{json.dumps(spending_by_category)}\n\nJSON:"
            )
            raw = self._call_inference(prompt)
            parsed, parse_error = self._safe_parse_json(raw)
            if parsed is None:
                parsed = {"budget_by_category": spending_by_category, "notes": ""}
            parsed["entity_or_project"] = entity_or_project
            parsed["spending_by_category"] = spending_by_category
            parsed["parse_error"] = parse_error
            if parse_error:
                parsed["raw_model_output"] = raw
            parsed["disclaimer"] = DISCLAIMER
            return parsed

        elif task == "prepare_documentation_package":
            entity_or_project = args.get("entity_or_project") if isinstance(args, dict) else (args[0] if args else None)
            audience = args.get("audience", "accountant") if isinstance(args, dict) else "accountant"
            if not entity_or_project:
                return {"error": "Missing entity_or_project", "disclaimer": DISCLAIMER}
            matches = self._find_instruments(
                lambda d: entity_or_project.strip().lower() in str(d.get("creditor", "")).lower()
                or entity_or_project.strip().lower() in str(d.get("debtor", "")).lower()
                or d.get("project_id") == entity_or_project
            )
            transactions = self._get_transactions(entity_or_project)
            cache_hits = self._cache_context_for(f"{entity_or_project} documentation {audience}", top_k=3)
            instruments_summary = "\n".join(
                f"- {d.get('instrument_type', 'unknown')}: creditor={d.get('creditor', '')}, debtor={d.get('debtor', '')}, amount={d.get('principal_amount', '')}"
                for d in matches
            ) or "(none stored)"
            transactions_summary = "\n".join(
                f"- {t.get('date', t.get('timestamp', ''))}: {t.get('payor', '')} -> {t.get('payee', '')}, {t.get('amount', '')}, doc_ref={t.get('documentation_ref', '(none)')}"
                for t in transactions
            ) or "(none logged)"
            prompt = (
                f"Prepare a documentation readiness checklist for a {audience} reviewing records "
                f"for '{entity_or_project}'. Based only on the material below, produce a single "
                "valid JSON object:\n"
                "{\n"
                '  "package_summary": "",\n'
                '  "included_items": [],\n'
                '  "missing_items": [],\n'
                '  "readiness_notes": ""\n'
                "}\n\n"
                f"Stored instruments:\n{instruments_summary}\n\nLogged transactions:\n{transactions_summary}\n\nJSON:"
            )
            raw = self._call_inference(prompt)
            parsed, parse_error = self._safe_parse_json(raw)
            if parsed is None:
                parsed = {"package_summary": "", "included_items": [], "missing_items": [], "readiness_notes": ""}
            parsed["entity_or_project"] = entity_or_project
            parsed["audience"] = audience
            parsed["parse_error"] = parse_error
            if parse_error:
                parsed["raw_model_output"] = raw
            parsed["cache_sources"] = [h["id"] for h in cache_hits]
            parsed["disclaimer"] = DISCLAIMER
            return parsed

        else:
            return {"error": f"Unknown task: {task}", "disclaimer": DISCLAIMER}


if __name__ == "__main__":
    agent = AccountingAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
