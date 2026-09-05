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
    # Equity and trust doctrine are argued by Legal, applied to instruments
    # by Trust, and used to value positions by Accounting. Shared, not copied.
    SHARED_CORPORA = ("_shared",)

    # Words that claim a request for this agent. Declared here, not in
    # Boss - the orchestrator holds no domain vocabulary.
    ROUTING_TERMS = (
        "ledger", "journal entry", "balance sheet", "income statement",
        "cash ?flow", "accrual", "depreciat", "amortiz", "audit",
        "\\bgaap\\b", "\\bifrs\\b", "\\basc\\b", "\\bedgar\\b", "10-?k", "10-?q",
        "beneficial interest", "equitable interest", "custodian", "trustee",
        "disbursement", "receivable", "payable", "reconcil", "invoice",
        # A credit report is a LEDGER a third party keeps about the principal.
        # What it says, what the books say, and the divergence between them is
        # bookkeeping - so the terms that signal "compare the record" land here.
        # Whether a divergence is actionable is Legal's, and those terms are
        # declared there rather than duplicated.
        "credit report", "credit file", "tradeline", "trade ?line",
        "furnisher", "credit bureau", "equifax", "experian", "transunion",
        "charge-?off", "delinquen", "collection account", "credit score",
        # How a charge is CODED is a bookkeeping question even when the charge
        # arose from a dispute. What the code asserts about fault is Legal's;
        # what the entry ought to say about the transaction is this agent's.
        "transaction code", "coded as", "miscod", "mis-?label", "chart of accounts",
        "posted as", "line item", "trip charge", "chargeback", "charge-?back",
        "pass-?through", "expense classification", "classif\\w* (?:the )?charge",
        # "why is the pest control charge coded wrong" matched NOTHING in any
        # agent's vocabulary, so Boss guessed and sent it to Security. A
        # question nobody claims is routed by whatever the fallback happens to
        # do, which is the router failure this architecture exists to prevent -
        # so the several ways a person actually says it are declared here.
        "charge\\w*.{0,24}cod(?:e|ed|ing)", "cod(?:e|ed|ing).{0,24}charge",
        "charge\\w*.{0,24}(?:wrong|incorrect|mislabel|misfiled|shouldn'?t be)",
        "(?:wrong|incorrect).{0,24}(?:code|category|account|entry)",
        "what.{0,20}(?:code|category).{0,20}(?:should|ought)",
        "billed (?:me |us )?for", "\\bdamages\\b.{0,30}\\bledger\\b",
        "\\bledger\\b.{0,30}\\bdamages\\b",
    )

    # Definitive: a request about what a ledger entry OUGHT to be classified as
    # belongs here and nowhere else. Legal decides whether a mislabel is
    # actionable; it does not decide what the correct entry is.
    OWNS_TERMS = ("transaction code", "chart of accounts", "expense classification")

    def __init__(self):
        super().__init__(
            agent_id="accounting_agent",
            port=9012,
            capabilities=[
                "assess_assertion", "set_lease_terms", "reconcile", "parse_financial_instrument", "assess_tax_liability", "track_account_balance",
                "lookup", "list_relationships", "get_relationship", "find_relationships",
                "find_relationships_by_project",
                "refresh_cache", "query_cache", "cache_stats", "cache_manifest",
                "map_transaction_roles", "log_transaction", "check_ledger_integrity",
                "map_assets_liabilities", "prepare_documentation_package", "check_budget_constraint",
                "forecast_cash_flow", "build_budget",
                "classify_charge", "log_harm", "harm_summary"
            , "score_predictions"],
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


    # ---- answering for itself ---------------------------------------------
    #
    # Accounting held a rent ledger with two live obligations, eight evidenced
    # payments and every payor authorised, and could not answer "how much do I
    # owe my rent based on the ledger" - because it implemented no answer() and
    # the base returns nothing. Boss routed correctly and the department stood
    # there mute. That is a capability gap, and the fix is to build the
    # capability rather than to let something else speak for it.
    #
    # Deterministic. It reads the record and reports it; it never estimates a
    # balance it cannot derive, because a confident wrong number in a rent
    # dispute is worse than an honest gap.

    def _obligation_view(self):
        """Every live obligation across every case, with its payments."""
        out = []
        try:
            cases = (self.handle_case_task("case_list", {}) or {}).get("cases", [])
        except Exception as e:
            self.log(f"answer: case_list failed: {e}")
            return out
        for c in cases:
            cid = c.get("case_id")
            try:
                full = self.handle_case_task("case_get", {"case_id": cid}) or {}
            except Exception:
                continue
            case = full.get("case", full)
            for o in case.get("obligations", []) or []:
                if o.get("voided"):
                    continue
                out.append({"case_id": cid, "case_title": case.get("title", ""), **o})
        return out

    LEASE_KEY = "lease_terms"

    def set_lease_terms(self, args):
        """The two facts answer() said it was missing: when the tenancy starts
        and what the periodic amount is. Merges, never rebuilds."""
        cid = args.get("case_id")
        if not cid:
            return {"error": "set_lease_terms needs a case_id", "disclaimer": DISCLAIMER}
        raw = self._get_stored_value(self.retrieve_own_memory(f"{self.LEASE_KEY}::{cid}"))
        try:
            rec = json.loads(raw) if raw else {}
        except Exception:
            rec = {}
        for f in ("lease_start", "lease_end", "base_rent", "prorated_first",
                  "prorated_period", "due_day", "grace_day", "late_fee_percent",
                  "document_ref", "note"):
            if args.get(f) not in (None, ""):
                rec[f] = args[f]
        rec["updated"] = datetime.now().isoformat()
        self.store_own_memory(f"{self.LEASE_KEY}::{cid}", json.dumps(rec), pin=True)
        return {"case_id": cid, "lease_terms": rec, "disclaimer": DISCLAIMER}

    def _lease_terms(self, case_id):
        raw = self._get_stored_value(self.retrieve_own_memory(f"{self.LEASE_KEY}::{case_id}"))
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def reconcile(self, case_id, as_of=None):
        """Charged against paid, period by period. Arithmetic done HERE, in the
        department that owns the ledger - not handed to it by someone else."""
        terms = self._lease_terms(case_id)
        if not terms.get("lease_start") or not terms.get("base_rent"):
            return {"reconcilable": False,
                    "missing": [f for f in ("lease_start", "base_rent")
                                if not terms.get(f)],
                    "why": "A balance needs a start date and a periodic amount."}
        try:
            full = self.handle_case_task("case_get", {"case_id": case_id}) or {}
        except Exception as e:
            return {"reconcilable": False, "why": f"case unreadable: {e}"}
        case = full.get("case", full)

        start = datetime.fromisoformat(str(terms["lease_start"])[:10])
        today = datetime.fromisoformat(str(as_of)[:10]) if as_of else datetime.now()
        base = float(terms["base_rent"])
        prorated = float(terms.get("prorated_first") or 0)

        # Periods charged: the part-month at the start (if a prorated figure was
        # given) plus one full month for every 1st-of-month that has arrived.
        charges = []
        if prorated:
            charges.append({"period": start.strftime("%Y-%m"),
                            "amount": prorated, "basis": "prorated first period"})
        y, m = start.year, start.month
        while True:
            m += 1
            if m > 12:
                m, y = 1, y + 1
            if datetime(y, m, 1) > today:
                break
            charges.append({"period": f"{y:04d}-{m:02d}", "amount": base,
                            "basis": "full month"})
        charged = round(sum(c["amount"] for c in charges), 2)

        paid, by_ob = 0.0, []
        for o in case.get("obligations", []) or []:
            if o.get("voided"):
                continue
            ps = [x for x in (o.get("payments") or []) if not x.get("voided")]
            amt = round(sum(float(x.get("amount") or 0) for x in ps), 2)
            paid += amt
            by_ob.append({"name": o.get("name"), "paid": amt, "payments": len(ps)})
        paid = round(paid, 2)
        balance = round(charged - paid, 2)
        return {"reconcilable": True, "as_of": today.strftime("%Y-%m-%d"),
                "lease_start": terms["lease_start"], "base_rent": base,
                "periods_charged": len(charges), "charged": charged,
                "paid": paid, "by_obligation": by_ob,
                "balance": balance,
                "position": ("in credit" if balance < 0 else
                             "square" if balance == 0 else "owing"),
                "charges": charges}

    # A question about how a charge was CODED is not a question about a
    # balance, so the money test above rejected it and the department that
    # owns the answer said nothing. The classifications this agent has already
    # derived sat in memory, reachable only by calling classify_charge again
    # with the facts re-supplied by hand.
    _CHARGE_ASK = (
        r"\bcod(?:e|ed|ing)\b", r"\btransaction code\b", r"\bmiscod", r"\bmislabel",
        r"\bposted as\b", r"\bchart of accounts\b", r"\bclassif",
        r"\btrip charge\b", r"\bcharge\w*\b.{0,24}\b(?:wrong|right|correct|should)\b",
        r"\bwhy\b.{0,30}\bdamages\b", r"\bbilled (?:me|us)\b",
    )

    def _stored_classifications(self, case_id=None):
        """Every charge this agent has classified, newest first."""
        out = []
        try:
            raw = self._unwrap_value(self.retrieve_own_memory("charge_class_index"))
            idx = json.loads(raw) if raw else []
        except Exception:
            idx = []
        for cid in idx:
            r = self._unwrap_value(self.retrieve_own_memory(cid))
            if not r:
                continue
            try:
                rec = json.loads(r)
            except Exception:
                continue
            if case_id and rec.get("case_id") != case_id:
                continue
            out.append(rec)
        out.sort(key=lambda x: x.get("recorded_at") or "", reverse=True)
        return out

    def _describe_classification(self, rec):
        amt = rec.get("amount")
        head = (f"The ${amt:,.2f} charge" if isinstance(amt, (int, float))
                else "That charge")
        posted = rec.get("posted_as")
        nat = rec.get("nature") or {}
        recov = rec.get("recoverability") or {}
        lines = []
        verdict = rec.get("verdict")
        if verdict == "posted_label_unsupported":
            lines.append(f"{head} is posted as “{posted}” and that label is not "
                         f"supported by the facts on record.")
        elif verdict == "posted_label_contradicted":
            lines.append(f"{head} is posted as “{posted}” and the record says the "
                         f"opposite.")
        elif verdict == "posted_label_questionable":
            lines.append(f"{head} is posted as “{posted}”, which is questionable.")
        elif verdict == "posted_label_consistent":
            lines.append(f"{head} is posted as “{posted}” and that is consistent "
                         f"with what it is.")
        else:
            lines.append(f"{head}: {verdict}.")
        lines.append("")
        lines.append(f"What it is: {nat.get('classification')}. {nat.get('basis','')}")
        lines.append(f"Whether it can be billed to you: {recov.get('status')}. "
                     + " ".join(recov.get("basis") or []))
        for f in (rec.get("findings") or [])[:2]:
            lines.append("")
            lines.append(f)
        missing = [a["citation"] for a in (rec.get("governing_authority") or [])
                   if not a.get("in_corpus")]
        if missing:
            lines.append("")
            lines.append("Authority that would settle the consequence is not held here: "
                         + ", ".join(missing)
                         + ". Nothing is asserted about what those say.")
        lines.append("")
        lines.append("This is a derivation, not an attestation - it carries no licence. "
                     "Every step above can be checked.")
        return "\n".join(l for l in lines if l is not None)

    def answer(self, prompt):
        """Questions this department owns, answered from its own records."""
        p = (prompt or "").lower()

        if any(re.search(pat, p) for pat in self._CHARGE_ASK):
            recs = self._stored_classifications()
            if not recs:
                return {"answered_as": "no_classification_held",
                        "text": ("No charge has been classified yet, so there is nothing on "
                                 "record to explain. Give me the charge - who performed the "
                                 "work, what occasioned it, and what it was posted as - and "
                                 "I will derive what it actually is.")}
            return {"answered_as": "charge_classification",
                    "text": self._describe_classification(recs[0]),
                    "facts": {"verdict": recs[0].get("verdict"),
                              "amount": recs[0].get("amount"),
                              "also_held": len(recs) - 1}}

        wants_money = any(w in p for w in (
            "owe", "owed", "balance", "due", "rent", "obligation", "ledger",
            "pay", "paid", "payment", "arrears", "behind", "current"))
        if not wants_money:
            return {"text": "", "answered_as": None}

        obs = self._obligation_view()
        if not obs:
            return {"text": "I hold no live obligations, so there is nothing "
                            "to owe against. If there should be, the ledger has "
                            "not reached me yet.",
                    "answered_as": "no_obligations"}

        lines, total = [], 0
        for o in obs:
            pays = [x for x in (o.get("payments") or []) if not x.get("voided")]
            amt = o.get("amount") or 0
            total += amt
            months = sorted({(x.get("paid_on") or "")[:7] for x in pays if x.get("paid_on")})
            paid_sum = sum(x.get("amount") or 0 for x in pays)
            no_ev = [x for x in pays if not x.get("has_evidence")]
            unauth = [x for x in pays if not x.get("payor_authorized")]
            who = ", ".join(o.get("authorized_payors") or []) or "nobody named"
            bit = (f"{o.get('name')}: {amt} {o.get('cadence','monthly')}, "
                   f"payable by {who}. {len(pays)} payment(s) recorded totalling "
                   f"{paid_sum}")
            if months:
                bit += f", covering {months[0]} to {months[-1]}"
            bit += "."
            if no_ev:
                bit += f" {len(no_ev)} with no evidence reference, which is contestable."
            if unauth:
                bit += f" {len(unauth)} by an unauthorised payor, which is contestable."
            lines.append(bit)

        # The number NOT stated, and why. A running balance needs the periods
        # the tenancy actually covers, and that is not in the record - so the
        # arithmetic would be an assumption wearing a decimal point.
        lines.append(f"Together that is {total} a month across {len(obs)} obligation(s).")
        cids = sorted({o["case_id"] for o in obs})
        for cid in cids:
            rec = self.reconcile(cid)
            if not rec.get("reconcilable"):
                lines.append(
                    f"I still cannot give you an outstanding balance: that needs "
                    f"{' and '.join(rec.get('missing') or ['more of the record'])}, "
                    f"which I do not hold. Every payment recorded is evidenced "
                    f"and made by an authorised payor.")
                continue
            lines.append(
                f"Against the lease from {rec['lease_start']}: "
                f"{rec['periods_charged']} period(s) charged totalling "
                f"{rec['charged']}, {rec['paid']} paid, so you are "
                f"{rec['balance'] if rec['balance'] >= 0 else abs(rec['balance'])} "
                f"{rec['position']} as of {rec['as_of']}.")
        return {"text": " ".join(lines), "answered_as": "obligation_status",
                "obligations": len(obs)}

    # WHAT A HARM IS WORTH DEPENDS ON WHICH KIND IT IS.
    #
    # The principal: *"log harm that's being done to me, substantiate everything
    # into harm to get a just settlement or compensation, including attorney
    # fees and research."*
    #
    # Right in shape, and the categories are not interchangeable. 42 U.S.C.
    # 3613(c) - now in Legal's corpus - authorises two different things in two
    # different subsections, on two different conditions:
    #
    #   (c)(1) "the court may award to the plaintiff ACTUAL AND PUNITIVE
    #          DAMAGES" plus injunctive relief. No prevailing-party condition
    #          stated in the clause itself.
    #   (c)(2) "the court, IN ITS DISCRETION, may allow the PREVAILING PARTY...
    #          a reasonable ATTORNEY'S FEE AND COSTS."
    #
    # So fees are conditional on prevailing AND discretionary, while actual
    # damages are the substance of the claim. Recording them in one bucket
    # produces a number that cannot survive being asked what it is made of.
    HARM_KINDS = {
        "out_of_pocket": {
            "recoverable_as": "actual damages, 42 U.S.C. 3613(c)(1)",
            "needs": "a receipt, invoice, or statement",
            "note": "Money that actually left. The most durable category there is."},
        "lost_income": {
            "recoverable_as": "actual damages, 42 U.S.C. 3613(c)(1)",
            "needs": "pay records, a schedule, or a supervisor's confirmation",
            "note": "Time off work FOR the matter. Hours, dates, and rate."},
        "medical_or_treatment": {
            "recoverable_as": "actual damages, 42 U.S.C. 3613(c)(1)",
            "needs": "records or bills",
            "note": "Including mental-health treatment attributable to the conduct."},
        "emotional_distress": {
            "recoverable_as": "actual damages, 42 U.S.C. 3613(c)(1)",
            "needs": "testimony, a contemporaneous log, corroboration",
            "note": ("Real and compensable in fair-housing cases, and proved by "
                     "contemporaneous detail rather than by a round number. Log it as it "
                     "happens; reconstructed distress reads as reconstructed.")},
        "loss_of_housing_opportunity": {
            "recoverable_as": "actual damages, 42 U.S.C. 3613(c)(1)",
            "needs": "the accommodation request, the refusal, what it cost",
            "note": "The core injury in a denied-accommodation case."},
        "punitive": {
            "recoverable_as": "punitive damages, 42 U.S.C. 3613(c)(1)",
            "needs": "evidence of the state of mind, not of the loss",
            "note": ("Not a multiple of the other categories - it addresses the conduct. "
                     "Log the facts showing what they knew and when, not an amount.")},
        "attorney_fee": {
            "recoverable_as": "42 U.S.C. 3613(c)(2) - PREVAILING PARTY, court's discretion",
            "needs": "counsel's own time records",
            "note": ("An ATTORNEY's hours, including their legal research. Conditional on "
                     "prevailing and discretionary - not part of the damages figure.")},
        "cost": {
            "recoverable_as": "costs, 42 U.S.C. 3613(c)(2)",
            "needs": "receipts",
            "note": ("Filing fees, service, records, certified mail, transcripts. Separate "
                     "from fees and usually easier to recover.")},
        "own_time_unrecoverable": {
            "recoverable_as": "NOT recoverable as an attorney's fee",
            "needs": "log it anyway - it evidences burden and diligence",
            "note": ("A party's own research and preparation hours are not an 'attorney's "
                     "fee'. Kay v. Ehrler, 499 U.S. 432 (1991) held even a pro se ATTORNEY "
                     "cannot recover fees for self-representation. Recorded in its own "
                     "category so it never inflates a demand - a number that collapses under "
                     "one question damages the credible categories beside it.")},
    }

    def log_harm(self, case_id=None, occurred_on=None, kind=None, description=None,
                 amount=None, evidence_ref=None, hours=None, rate=None):
        """Record one harm, in the category that decides what it is worth.

        Substantiation is the whole exercise: a harm with a date, an amount and
        a document behind it survives a hostile reading, and a harm without one
        is an assertion. So `evidence_ref` is recorded and its ABSENCE is
        reported rather than passed over."""
        k = str(kind or "").strip().lower()
        if k not in self.HARM_KINDS:
            return {"error": ("kind must be one of: " + ", ".join(sorted(self.HARM_KINDS))),
                    "meanings": {n: v["recoverable_as"] for n, v in self.HARM_KINDS.items()}}
        if not description or not str(description).strip():
            return {"error": "A harm needs a description of what actually happened."}
        profile = self.HARM_KINDS[k]

        amt = None
        try:
            amt = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            return {"error": f"amount '{amount}' is not a number."}
        if amt is None and hours and rate:
            try:
                amt = round(float(hours) * float(rate), 2)
            except (TypeError, ValueError):
                pass

        entry = {
            "id": f"harm_{self._uid()}" if hasattr(self, "_uid")
                  else f"harm_{int(time.time()*1000000)}",
            "case_id": case_id,
            "occurred_on": occurred_on,
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
            "kind": k,
            "description": str(description).strip(),
            "amount": amt,
            "hours": hours, "rate": rate,
            "evidence_ref": evidence_ref,
            "recoverable_as": profile["recoverable_as"],
            "evidence_needed": profile["needs"],
            "note": profile["note"],
            "substantiated": bool(evidence_ref),
        }
        if not evidence_ref:
            entry["gap"] = (f"No evidence reference. This is currently an assertion. "
                            f"What would close it: {profile['needs']}.")
        if not occurred_on:
            entry["date_gap"] = ("No date. A harm without one cannot be tied to the "
                                 "conduct that caused it.")
        try:
            raw = self._unwrap_value(self.retrieve_own_memory("harm_index"))
            idx = json.loads(raw) if raw else []
        except Exception:
            idx = []
        self.store_own_memory(entry["id"], json.dumps(entry), pin=True)
        idx.append(entry["id"])
        self.store_own_memory("harm_index", json.dumps(idx))
        return entry

    def harm_summary(self, case_id=None):
        """Totals by category, and what is not yet substantiated.

        Deliberately does NOT produce one number. A demand figure that mixes
        actual damages with a discretionary fee award and a party's own hours
        invites exactly one question, and the answer unravels the credible parts
        along with the rest."""
        try:
            raw = self._unwrap_value(self.retrieve_own_memory("harm_index"))
            idx = json.loads(raw) if raw else []
        except Exception:
            idx = []
        rows = []
        for hid in idx:
            r = self._unwrap_value(self.retrieve_own_memory(hid))
            if not r:
                continue
            try:
                e = json.loads(r)
            except Exception:
                continue
            if case_id and e.get("case_id") != case_id:
                continue
            rows.append(e)

        buckets = {}
        for e in rows:
            b = buckets.setdefault(e["kind"], {"count": 0, "amount": 0.0,
                                               "substantiated": 0,
                                               "recoverable_as": e["recoverable_as"]})
            b["count"] += 1
            b["amount"] += e.get("amount") or 0.0
            b["substantiated"] += 1 if e.get("substantiated") else 0

        actual = sum(v["amount"] for k, v in buckets.items()
                     if v["recoverable_as"].startswith("actual"))
        fees = sum(v["amount"] for k, v in buckets.items() if k == "attorney_fee")
        costs = sum(v["amount"] for k, v in buckets.items() if k == "cost")
        own = sum(v["amount"] for k, v in buckets.items() if k == "own_time_unrecoverable")
        unsub = [e["id"] for e in rows if not e.get("substantiated")]

        return {
            "case_id": case_id, "entries": len(rows), "by_kind": buckets,
            "actual_damages_total": round(actual, 2),
            "attorney_fees_claimed": round(fees, 2),
            "costs_total": round(costs, 2),
            "own_time_logged_not_claimable": round(own, 2),
            "unsubstantiated": unsub,
            "unsubstantiated_count": len(unsub),
            "why_no_single_total": (
                "Actual damages are the claim. Attorney's fees and costs are a separate "
                "application under 42 U.S.C. 3613(c)(2), conditional on prevailing and "
                "discretionary. A party's own hours are neither. One combined number would "
                "be asked what it consists of, and the answer would discredit the parts that "
                "are sound."),
        }

    # ------------------------------------------------------------------
    # Charge classification
    #
    # A code on a ledger is a CLAIM about a transaction. The characteristics
    # of the transaction are the evidence. Where they diverge, the divergence
    # is the finding - the same rule the rest of this system runs on, applied
    # to a book of account.
    #
    # The specific failure this exists to catch: a label that asserts a fact
    # not in evidence. "Damages" is not a neutral bucket. It asserts that a
    # party caused physical harm, and it is the bucket a security deposit is
    # drawn against at move-out. Posting a contractor's trip charge there
    # decides causation, fault and recoverability in one keystroke, none of
    # which was proved, and the ledger then reads to a future landlord, a
    # screening bureau or a court as though all three were.
    #
    # So this deliberately answers TWO questions instead of one, because the
    # mislabel is what happens when they are answered together:
    #
    #   1. WHAT IS IT?        nature - what economic event actually occurred
    #   2. IS IT RECOVERABLE? recovery - whether it may be billed to this party
    #
    # A code that answers (2) in the poster's favour while pretending to
    # answer (1) is the whole problem.

    # Codes that assert fault. Posting one is an accusation, not a category.
    FAULT_BEARING_CODES = {
        "damages", "damage", "damage charge", "tenant damage", "resident damage",
        "tenant damages", "negligence", "negligent damage", "vandalism", "abuse",
        "destruction", "destruction of property", "malicious damage",
    }

    PERFORMED_BY = {
        "third_party_contractor": (
            "third_party_service_cost",
            "A vendor performed the work, so what the landlord incurred is a "
            "payable to that vendor. Billing it onward is a COST RECOVERY - a "
            "reimbursement of an expense already classified as a service cost. "
            "It does not become a damage assessment by being passed on."),
        "in_house_staff": (
            "internal_service_cost",
            "Own staff performed the work, so the cost is internal labour and "
            "overhead. Billing it onward is a service charge under whatever fee "
            "term the agreement provides."),
        "unknown": (
            "undetermined",
            "Who performed the work has not been established, and that is the "
            "fact the nature of the charge turns on. Not classifiable yet."),
    }

    OCCASIONED_BY = {"tenant_conduct", "landlord_condition", "routine_or_preventive", "unknown"}
    FAULT_STATUS = {"adjudicated", "admitted", "alleged", "unestablished"}

    def classify_charge(self, args):
        """What a charge actually is, derived from its characteristics rather
        than read off the code someone posted it under.

        Refuses to answer from the label. Refuses to invent the code it cannot
        derive - the inputs it lacks are named instead. And it never asserts
        what an authority says unless that authority is openable in a corpus
        this system can reach, which is the same rule `add_deadline` runs."""
        a = args if isinstance(args, dict) else {}
        posted = str(a.get("posted_as") or "").strip()
        performed_by = str(a.get("performed_by") or "unknown").strip().lower()
        occasioned_by = str(a.get("occasioned_by") or "unknown").strip().lower()
        fault_status = str(a.get("fault_status") or "unestablished").strip().lower()
        lease_authority = a.get("lease_authority")
        description = str(a.get("description") or "").strip()

        if not description:
            return {"error": "classify_charge needs a description of the charge.",
                    "disclaimer": DISCLAIMER}
        if performed_by not in self.PERFORMED_BY:
            return {"error": f"performed_by must be one of: {sorted(self.PERFORMED_BY)}",
                    "why": "Who did the work decides what the cost is. It is not optional.",
                    "disclaimer": DISCLAIMER}
        if occasioned_by not in self.OCCASIONED_BY:
            return {"error": f"occasioned_by must be one of: {sorted(self.OCCASIONED_BY)}",
                    "disclaimer": DISCLAIMER}
        if fault_status not in self.FAULT_STATUS:
            return {"error": f"fault_status must be one of: {sorted(self.FAULT_STATUS)}",
                    "disclaimer": DISCLAIMER}

        amount = None
        if a.get("amount") is not None:
            try:
                amount = round(float(a["amount"]), 2)
            except (TypeError, ValueError):
                return {"error": f"amount '{a['amount']}' is not a number.",
                        "disclaimer": DISCLAIMER}

        nature, nature_basis = self.PERFORMED_BY[performed_by]

        # --- question 2, kept separate on purpose -----------------------
        recovery, recovery_basis = "not_shown_recoverable", []
        if occasioned_by == "landlord_condition":
            recovery = "contradicted"
            recovery_basis.append(
                "The work was occasioned by a condition that is the landlord's own "
                "to remedy. A cost arising from a party's own duty is that party's "
                "cost; charging it to the counterparty reverses the obligation.")
        elif not lease_authority:
            recovery_basis.append(
                "No lease clause has been identified authorising this charge to be "
                "passed to the tenant. Without one there is no agreed basis for it, "
                "whatever it is called.")
        elif occasioned_by == "tenant_conduct" and fault_status in ("adjudicated", "admitted"):
            recovery = "supported"
            recovery_basis.append(
                f"A lease clause is identified ({lease_authority}) and causation is "
                f"{fault_status}.")
        elif occasioned_by == "routine_or_preventive":
            recovery = "depends_on_fee_schedule"
            recovery_basis.append(
                "Routine or preventive service is not fault-based. It is recoverable "
                "only if the agreement sets it out as a scheduled fee, at a stated "
                "amount, and not as a discretionary charge.")
        else:
            recovery_basis.append(
                f"A lease clause is identified ({lease_authority}) but causation is "
                f"'{fault_status}' and the occasion is '{occasioned_by}'. The clause "
                f"does not supply the facts the charge depends on.")

        # --- the verdict on what was posted -----------------------------
        posted_l = posted.lower().strip().rstrip(".")
        is_fault_code = posted_l in self.FAULT_BEARING_CODES
        findings, verdict = [], "posted_label_undetermined"

        if not posted:
            verdict = "no_posted_label_supplied"
        elif is_fault_code and fault_status not in ("adjudicated", "admitted"):
            verdict = "posted_label_unsupported"
            findings.append(
                f"'{posted}' is a fault-bearing code: posting it asserts that this "
                f"party caused the condition. Causation here is '{fault_status}'. An "
                f"assertion of fault that has been neither adjudicated nor admitted "
                f"does not become established by being written into a ledger.")
        elif is_fault_code and occasioned_by == "landlord_condition":
            verdict = "posted_label_contradicted"
            findings.append(
                f"'{posted}' assigns fault to the tenant while the recorded occasion "
                f"of the work is a landlord condition. The label states the opposite "
                f"of the evidence.")
        elif is_fault_code and performed_by == "third_party_contractor":
            verdict = "posted_label_questionable"
            findings.append(
                f"'{posted}' is a fault code, but the cost originated as a vendor "
                f"payable. Even where fault is established, the cost is a recovery of "
                f"a service expense; the fault question governs whether it may be "
                f"recharged, not what kind of cost it is.")
        elif nature != "undetermined" and posted_l:
            verdict = "posted_label_consistent"

        if is_fault_code:
            findings.append(
                "A fault-bearing code is not a neutral filing choice. It is the "
                "category a security deposit is drawn against at move-out, and it is "
                "read by later landlords, screening services and courts as a finding "
                "about the tenant rather than as one party's unreviewed entry.")
        if performed_by == "third_party_contractor" and not lease_authority:
            findings.append(
                "A contractor's trip or call-out fee is the vendor's charge for "
                "attending. Whether any part of it reaches the tenant is a term "
                "question, and no term has been produced.")

        # --- authority: named, never paraphrased from memory -------------
        authorities = []
        for cite, why in (
            ("92.104", "retention of a security deposit for damages and charges, and "
                       "the itemisation a landlord must give"),
            ("92.109", "landlord liability for retaining a deposit in bad faith"),
        ):
            located = []
            try:
                located = self.ask_peer_corpus("legal_agent", cite) or []
            except Exception:
                located = []
            authorities.append({
                "citation": f"Tex. Prop. Code § {cite}",
                "bears_on": why,
                "in_corpus": bool(located),
                "note": None if located else
                        ("NOT in any corpus this system can open, so nothing is "
                         "asserted about what it says. Acquire it before relying on it."),
            })

        result = {
            "description": description,
            "amount": amount,
            "posted_as": posted or None,
            "verdict": verdict,
            "findings": findings,
            "nature": {"classification": nature, "basis": nature_basis,
                       "question_answered": "What economic event occurred?"},
            "recoverability": {"status": recovery, "basis": recovery_basis,
                               "question_answered": "May this be billed to this party?"},
            "two_questions": (
                "These are separate and the posted code answered both at once. A code "
                "that decides recoverability while presenting itself as a description "
                "of the cost is not a classification, it is a conclusion."),
            "governing_authority": authorities,
            "inputs_used": {"performed_by": performed_by, "occasioned_by": occasioned_by,
                            "fault_status": fault_status,
                            "lease_authority": lease_authority or None},
            "principle": (
                "Substance over form: an entry must depict the transaction that "
                "occurred, not the one that is convenient to have occurred. This "
                "agent states it as its own operating rule - the FASB conceptual "
                "framework and the ASC are under copyright and are NOT in this "
                "corpus, so no citation to them is offered."),
            "not_an_attestation": (
                "This is a derivation, not an opinion a third party may rely on. It "
                "carries no licence and no attestation. Its value is that every step "
                "is shown and can be checked."),
            "disclaimer": DISCLAIMER,
        }

        cid = a.get("case_id")
        if cid:
            try:
                rec_id = f"charge_class_{self._uid()}"
                try:
                    _raw = self._unwrap_value(self.retrieve_own_memory("charge_class_index"))
                    _idx = json.loads(_raw) if _raw else []
                except Exception:
                    _idx = []
                _idx.append(rec_id)
                self.store_own_memory("charge_class_index", json.dumps(_idx))
                self.store_own_memory(rec_id, json.dumps(
                    {**{k: v for k, v in result.items() if k != "disclaimer"},
                     "case_id": cid, "document_ref": a.get("document_ref"),
                     "occurred_on": a.get("occurred_on"),
                     "recorded_at": datetime.now().isoformat(timespec="seconds")}), pin=True)
                result["recorded_as"] = rec_id
            except Exception as exc:
                result["record_failed"] = str(exc)
        return result

    # ---- prediction-scoring domain hooks ---------------------------------
    #
    # AN OBLIGATION IS A PREDICTION. It says an amount will be paid, on a
    # cadence, by a named payor, with evidence. A payment record is the
    # observation that tests it. The machinery for grading lives on AgentBase,
    # extracted from Grow, and what belongs here is only what an accountant
    # knows: what counts as an obligation met.
    #
    # THE LATENCY IS DIFFERENT AND THE SCORER DOES NOT CARE. Grow's outcomes
    # arrive in days and Trading's in a day; an obligation resolves on a
    # statement cycle. The base scorer imposes no window at all - each domain
    # sets its own - which is why one scorer serves a plant, a market and a
    # ledger without knowing anything about any of them.

    PREDICTION_SUBJECT_KEY = "case_id"

    def default_prediction_subject(self):
        cases = self._list_case_ids() if hasattr(self, "_list_case_ids") else []
        return cases[0] if cases else None

    def collect_predictions(self, subject=None):
        """Every obligation on this case, as the prediction it already is."""
        if not subject:
            return []
        try:
            status = self.case(subject).obligation_status()
        except Exception:
            return []
        out = []
        for ob in (status or {}).get("obligations", []) or []:
            out.append({
                "source": "obligation", "case_id": subject,
                "obligation_id": ob.get("obligation_id"), "name": ob.get("name"),
                "amount": ob.get("amount"), "cadence": ob.get("cadence"),
                "due_day": ob.get("due_day"),
                "authorized_payors": ob.get("authorized_payors") or [],
                "expected_effect": (
                    f"{ob.get('name')} of {ob.get('amount')} is paid {ob.get('cadence')}"
                    + (f" by day {ob.get('due_day')}" if ob.get("due_day") else "")
                    + ", by an authorised payor, with evidence attached"),
                "observed": ob,
            })
        return out

    def gather_observations(self, subject=None):
        """The ledger side. Already carried on the obligation record."""
        return {"case_id": subject}

    def score_one_prediction(self, pred, observations):
        """Was the obligation actually met - and can that be evidenced?

        THREE WAYS TO FAIL, and they are not the same failure. Unpaid is the
        obvious one. Paid by someone not on the authorised list, and paid with
        no evidence reference, both look identical to good standing if all you
        keep is the amount - which is the whole reason this ledger records them
        separately.

        UNSCORABLE, not failed, when the obligation carries no due day. Without
        one, "paid on time" states an intention rather than a measurable
        outcome, and grading it either way would invent a deadline nobody set.
        """
        ob = pred.get("observed") or {}
        if not pred.get("due_day"):
            return {**pred, "verdict": "unscorable",
                    "why": ("no due day on this obligation, so 'met on time' is not a "
                            "measurable outcome. Set due_day to make it gradeable.")}
        paid = int(ob.get("payments_recorded") or 0)
        if paid == 0:
            return {**pred, "verdict": "undetermined",
                    "why": "no payment recorded against this obligation yet"}
        unauth = int(ob.get("payments_by_unauthorized_payor") or 0)
        no_ev = int(ob.get("payments_without_evidence") or 0)
        faults = []
        if unauth:
            faults.append(f"{unauth} payment(s) by a payor not on the authorised list")
        if no_ev:
            faults.append(f"{no_ev} payment(s) with no evidence reference")
        if faults:
            return {**pred, "verdict": "failed",
                    "why": ("the obligation was paid but not cleanly: " + "; ".join(faults)
                            + ". A payment that cannot be evidenced, or that came from an "
                              "unauthorised payor, is contestable - it is not the same as "
                              "an obligation in good standing."),
                    "payments_recorded": paid}
        return {**pred, "verdict": "held",
                "why": (f"{paid} payment(s) recorded, all by authorised payors and all "
                        f"evidenced. Standing: {ob.get('standing')}."),
                "payments_recorded": paid}

    def handle_task(self, task, args, sender):
        self.log(f"Task {task} from {sender}")

        cag_result = self.try_handle_cag_task(task, args)
        if cag_result is not None:
            return cag_result

        if task == "classify_charge":
            return self.classify_charge(args if isinstance(args, dict) else {})

        if task == "set_lease_terms":
            return self.set_lease_terms(args if isinstance(args, dict) else {})

        if task == "reconcile":
            a = args if isinstance(args, dict) else {}
            if not a.get("case_id"):
                return {"error": "reconcile needs a case_id", "disclaimer": DISCLAIMER}
            r = self.reconcile(a["case_id"], a.get("as_of"))
            r["disclaimer"] = DISCLAIMER
            return r

        if task == "assess_assertion":
            # Legal asks whether the books bear out something it read in an
            # instrument. Accounting answers from its OWN records and is
            # expected to say no when they do not.
            #
            # The default is `null` - could not determine - not `true`. An
            # agent that agrees when it has nothing to check is worse than an
            # agent that abstains, because the agreement is indistinguishable
            # from corroboration and it is not corroboration.
            a = args if isinstance(args, dict) else {}
            assertion = (a.get("assertion") or "").strip()
            if not assertion:
                return {"agrees": None, "basis": "no assertion supplied",
                        "disclaimer": DISCLAIMER}
            ledger = self._load_index_key("transaction_index") \
                if hasattr(self, "_load_index_key") else []
            if not ledger:
                return {"agrees": None,
                        "basis": "Accounting holds no transaction records that "
                                 "bear on this, so it can neither corroborate "
                                 "nor contradict it. Not agreement.",
                        "records_examined": 0, "disclaimer": DISCLAIMER}
            return {"agrees": None,
                    "basis": f"Examined {len(ledger)} transaction record(s). "
                             f"Accounting has no automated test that maps this "
                             f"assertion onto them, so it declines to agree. "
                             f"A human must state which records would evidence "
                             f"it.",
                    "records_examined": len(ledger),
                    "undetermined_is_not_agreement": True,
                    "disclaimer": DISCLAIMER}

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

        elif task == "log_harm":
            return self.log_harm(**(args if isinstance(args, dict) else {}))
        elif task == "harm_summary":
            return self.harm_summary(**(args if isinstance(args, dict) else {}))
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
