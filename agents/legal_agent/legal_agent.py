#!/usr/bin/env python3
import sys
import os
import re
import json
import time
import uuid
import requests
from datetime import datetime, timedelta

# Add project root
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase
from core.schemas import from_legacy_fields
from core import claim_assessment

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
    "It is an extraction/structuring of the provided text, not legal advice, "
    "and should be reviewed by a qualified professional before being relied upon."
)

RELATIONSHIP_FIELDS = [
    "entity_a", "entity_b", "contract_type", "asset", "asset_owner", "custodian",
    "obligations", "rights", "beneficiary", "service_provider", "fee_recipient",
    "governing_law", "applicable_statutes"
]

# Additive fields for relationship types beyond a plain contract - all optional,
# empty-string/empty-list default, so existing stored relationships and
# compare_relationships (which diffs over RELATIONSHIP_FIELDS only) are unaffected.
RELATIONSHIP_TYPES = ["contractual", "trust", "lease", "fiduciary", "arbitration", "business"]
CONTRACT_STRUCTURE_FIELDS = [
    "offer", "acceptance", "consideration", "termination_conditions",
    "dispute_resolution", "performance_requirements"
]
TRUST_STRUCTURE_FIELDS = [
    "settlor", "trustee", "trust_property", "fiduciary_duties", "governing_documents"
]
JURISDICTION_FIELDS = ["jurisdiction", "venue", "court_authority", "procedural_rules_source"]
EXTENDED_RELATIONSHIP_FIELDS = (
    RELATIONSHIP_FIELDS + CONTRACT_STRUCTURE_FIELDS + TRUST_STRUCTURE_FIELDS + JURISDICTION_FIELDS
)

LESSON_CATEGORIES = {"procedural", "communication", "evidence", "outcome", "general"}

STATUTE_CITATION_RE = re.compile(r"\b\d+\s*U\.?S\.?C\.?\s*§*\s*\d+[a-zA-Z0-9\-]*", re.IGNORECASE)

# ---------------------------
# Privacy Protection Review - deterministic regex first, same style as
# STATUTE_CITATION_RE above. Categories that aren't reliably regex-able
# (medical information, general "protected personal information") fall back
# to an LLM classification pass - see scan_for_pii / _classify_pii_context.
# ---------------------------
PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "ein_tin": re.compile(r"\b\d{2}-\d{7}\b"),
    "financial_account": re.compile(r"\baccount[^.\n]{0,20}?(\d[\d\s-]{7,20}\d)\b", re.IGNORECASE),
    "birth_date": re.compile(r"\b(?:born|dob|date of birth)[^\n]{0,15}?(\d{1,2}/\d{1,2}/\d{2,4})\b", re.IGNORECASE),
    "drivers_license": re.compile(r"\bdriver'?s?\s*licen[sc]e[^\n]{0,15}?([A-Z0-9]{6,12})\b", re.IGNORECASE),
}
MEDICAL_KEYWORDS = ("diagnosis", "medical record", "treatment history", "prescription", "therapy session", "health condition")


# ---------------------------------------------------------------------------
# Pre-filing guard. Deterministic, and deliberately not a model call.
#
# Derived from what actually happened in 5:25-cv-00500-XR (W.D. Tex.), where a
# petition was dismissed with prejudice as frivolous and leave to amend denied
# as futile. The four failure modes below are the ones that decided that case.
#
# Regex rather than inference for three reasons: an inference pass costs minutes
# on this hardware and a guard that is slow gets skipped; a model that catches a
# fatal pattern 90% of the time is not a guard; and a pattern match can cite the
# authority that makes it fatal, which teaches rather than merely refuses.
# ---------------------------------------------------------------------------

# Theories that end a case before the merits are reached. Each carries the
# authority so the warning explains itself.
FATAL_THEORY_MARKERS = [
    (r'\bUCC[- ]?1\b|\bfinancing statement\b',
     "UCC-1 financing statements asserted as liens against parties or agencies",
     "Watson v. Tex. State Univ., 829 F. App'x 686 (5th Cir. 2020) - frivolous, appeal "
     "dismissed and sanction imposed. Dismissed on this basis in 5:25-cv-00500-XR."),
    (r'\bstrawman\b|\bstraw man\b|\bcestui que vie\b',
     "strawman / cestui que vie trust theory",
     "Burleson v. United States, 2022 WL 17732434 (W.D. Tex.) - hallmark of the "
     "sovereign-citizen theory courts treat as frivolous per se."),
    (r'\baccepted for value\b|\bA4V\b|\bredemption\b.{0,30}\btreasury\b',
     "accepted-for-value / redemption theory",
     "Uniformly rejected; see Wirsche v. Bank of Am., 2013 WL 6564657 (S.D. Tex.) "
     "(\"These teachings have never worked in a court of law - not a single time.\")"),
    (r'\bdiplomatic (military )?(administrative )?trustee\b|\bMoorish\b|\bsovereign citizen\b'
     r'|\bprivate military fiduciary\b',
     "sovereign / diplomatic capacity claim",
     "Bey v. Indiana, 847 F.3d 559 (7th Cir. 2017); dismissed on this basis in "
     "5:25-cv-00500-XR, leave to amend denied as FUTILE."),
    (r'\bsui juris\b|\bflesh and blood\b|\bnon-?domestic\b|\bfreeman on the land\b',
     "sovereign-citizen capacity or jurisdiction language",
     "Berman v. Stephens, 2015 WL 3622694 (N.D. Tex.) (collecting cases)."),
]

# Captions the Federal Rules actually recognise as asking a court for something.
RECOGNISED_VEHICLES = (
    "motion", "petition", "complaint", "objection", "response", "reply", "brief",
    "notice of appeal", "application", "declaration", "affidavit", "memorandum",
    "proposed order", "stipulation", "answer", "amended complaint",
)
# Captions that ask for nothing and therefore cannot be granted.
INERT_CAPTIONS = (r'\badvisory to the court\b', r'^\s*advisory\b', r'\bsupplement to\b',
                  r'\bstatement of\b(?!.*\bclaim\b)')

RR_OBJECTION_DAYS = 14          # 28 U.S.C. 636(b)(1); FRCP 72(b)
APPEAL_DAYS_US_PARTY = 60       # FRAP 4(a)(1)(B) - 60, not 30, when the US is a party
RULE59_DAYS = 28                # FRCP 59(e)
FILING_BURST_WINDOW_DAYS = 7
FILING_BURST_THRESHOLD = 5


class LegalAgent(AgentBase):
    # Equity and trust doctrine are argued by Legal, applied to instruments
    # by Trust, and used to value positions by Accounting. Shared, not copied.
    SHARED_CORPORA = ("_shared",)

    # Words that claim a request for this agent. Declared here, not in
    # Boss - the orchestrator holds no domain vocabulary.
    ROUTING_TERMS = (
        "contract", "agreement", "clause", "statute", "regulation", "\\bcfr\\b",
        "\\busc\\b", "case ?law", "precedent", "docket", "plaintiff",
        "defendant", "court", "judge", "ruling", "opinion", "jurisdiction",
        "liability", "indemnif", "breach", "enforceab", "unconscionab",
        # The REMEDY side of a credit report. Accounting owns what the report
        # says against the books; whether that divergence is actionable is a
        # statute question and belongs here. Split by which question the words
        # signal, not by subject - both departments touch "credit".
        "\\bfcra\\b", "1681", "fair credit reporting", "reinvestigat",
        "adverse action", "\\bfurnisher (dut|obligation|responsib)",
        "dispute.{0,20}(credit|report|tradeline)", "regulation ?v",
        "consideration", "covenant", "lien", "easement", "tort", "negligen",
        "subpoena", "affidavit", "pleading", "motion to", "pro se", "equitable",
        "state law", "which state", "\\bucc\\b", "article 9", "blue ?sky",
        "regulation [tuzb]\\b", "\\breg [tuzb]\\b", "preempt", "national bank",
        "security interest", "receivable", "chattel paper", "perfect(ed|ion)",
        "secured party", "collateral", "assign(ment|ee|or)", "promissory note",
        "truth in lending", "\\btila\\b", "\\becoa\\b", "securitiz",
    )

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
                "search_cases", "monitor_user", "monitor_docket", "check_docket",
                "log_lesson", "query_lessons", "list_lessons",
                "analyze_case", "list_cases", "get_case", "assess_case_elements",
                "open_matter", "map_issues", "get_matter_view",
                "add_to_notebook", "add_to_evidence_binder", "add_to_filing_layer",
                "review_filing_draft", "compress_matter", "check_filing_frequency",
                "scan_for_pii", "reflect_on_matter", "map_authority",
                "set_operating_jurisdiction", "get_operating_jurisdiction",
                "cite_in_jurisdiction", "transaction_layers",
                "claim_open", "claim_cite", "claim_answer", "claim_set_right",
                "claim_evidence", "claim_observe", "claim_reproducibility",
                "claim_corroborate", "claim_get", "claim_list", "claim_ontology",
                "add_deadline", "deadlines",
                "open_action", "complete_action", "amend_action", "actions",
                "add_venue", "venues", "running_clocks", "complaint_path",
                "triage_source", "record_case_outcome"
            ],
            role="agent"
        )
        self.init_cag(cache_ttl=86400, watch_interval=300)
        self.subscribe_project_events()
        self.log("Legal Agent initialized (extraction/structuring only - no legal advice).")

    def on_project_event(self, project_id, event_type, data, sender):
        self.log(f"Project event {project_id}/{event_type} from {sender}")

    def _extract_citations(self, text):
        return list({m.group(0).strip() for m in STATUTE_CITATION_RE.finditer(text)})

    # Placeholder filtering now lives in AgentBase.query_cache so every
    # agent inherits it - see the note there.

    # ---- Legal dictionary -------------------------------------------------
    #
    # Deliberately NOT part of the CAG cache. That cache scores
    # len(overlap)/len(query_tokens) with no stopword filter, which means a long
    # document of boilerplate outscores a short, exactly-on-point definition -
    # measured: 0.040 vs 0.030 on this very case. Bag-of-words retrieval over
    # 11,000 dictionary entries would return noise, and the CAG loader would in
    # any event truncate a 5.5MB file at 200,000 chars.
    #
    # So definitions are looked up by EXACT headword: a definition reaches the
    # model because the instrument actually uses that term, not because they
    # share the word "the". Loaded lazily - most tasks never need it, and the
    # index costs memory this box does not have to spare.
    DICTIONARY_FILES = ("blacks_1910.json", "modern_supplement.json")
    _dictionary = None
    _aliases = None

    def _load_dictionary(self):
        if self._dictionary is not None:
            return self._dictionary
        merged = {}
        ref_dir = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "reference", "legal_agent")
        for fname in self.DICTIONARY_FILES:
            path = os.path.join(ref_dir, fname)
            try:
                with open(path) as fh:
                    entries = json.load(fh)
                # later files win, so the modern supplement can correct an
                # entry the 1910 edition lacks or states archaically
                merged.update({k.lower(): v for k, v in entries.items()})
            except FileNotFoundError:
                self.log(f"dictionary: {fname} not present, skipping")
            except Exception as e:
                self.log(f"dictionary: failed to load {fname}: {e}")
        self._dictionary = merged
        self.log(f"dictionary: {len(merged)} terms indexed")
        return merged

    # Reference files that carry `sections` - a treatise, a rule set - as
    # opposed to the term->definition dictionaries above. Both live in
    # reference/legal, and until now only the dictionaries were ever read:
    # federal_rules_of_civil_procedure.json sat there for five days and
    # express_trusts_..._chandler_1912.json arrived to join it, neither
    # reachable by anything. A corpus nothing loads is decoration.
    _refdocs = None

    def assess_case_elements(self, case_id):
        """Which elements are established by the evidence the case actually holds.

        The shift is from "which law applies" to "which elements are made out".
        A statute is not a finding - a claim stands or falls on its elements
        one at a time, and each one is short enough to answer honestly.

        INSUFFICIENT EVIDENCE IS A REAL OUTCOME. An element with nothing
        attached is not pending, not weak, and not a failure of the tool - it
        is a gap, named, with the thing that would close it. A case tool that
        cannot say "you have not shown this yet" tells its principal they are
        ready when they are not, which is the expensive way to find out."""
        cm = self.case(case_id)
        case = cm.get()
        if case.get("error"):
            return case

        by_id = {e["evidence_id"]: e for e in case.get("evidence", [])}
        findings, gaps = {}, []
        for name, el in (case.get("elements") or {}).items():
            items = [by_id[i] for i in el.get("evidence_ids", []) if i in by_id]
            documented = [e for e in items if e.get("doc_id")]
            state = el.get("state", "open")
            if state in ("disputed", "refuted", "not_applicable"):
                verdict = state
            elif not items:
                verdict = "insufficient_evidence"
            elif not documented:
                verdict = "insufficient_evidence"
            else:
                verdict = "established" if state == "established" else "supportable"
            entry = {"state_on_record": state, "verdict": verdict,
                     "evidence_count": len(items),
                     "documented": len(documented),
                     "evidence_kinds": sorted({e.get("kind", "?") for e in items})}
            if verdict == "insufficient_evidence":
                entry["what_would_close_it"] = (
                    "Nothing is attached to this element yet - add evidence with "
                    f"supports='{name}' and a doc_id pointing at the document that carries it."
                    if not items else
                    "Evidence is attached but none of it references a document. An "
                    "assertion with no document behind it is testimony, not proof.")
                gaps.append(name)
            findings[name] = entry

        ready = not gaps
        return {
            "case_id": case["case_id"], "title": case.get("title"),
            "case_state": case.get("state"),
            "elements": findings,
            "unestablished": gaps,
            "ready_to_advance": ready,
            "assessment": (
                "Every element has documented evidence attached. That is not a "
                "judgement that the claim succeeds - it is that nothing is missing "
                "on its face."
                if ready else
                f"{len(gaps)} element(s) are not established on what the case holds: "
                + ", ".join(gaps) + ". Insufficient evidence is the finding here, not "
                "an error - these are the gaps to close before advancing."),
            "disclaimer": DISCLAIMER,
        }

    def lookup_term(self, term, loose=True):
        """A headword, matched exactly - then by its parts.

        Black's runs compound headwords: "Cestui, Cestuy" is one key covering
        two spellings, so an exact-key lookup of "cestui" missed a term the
        dictionary plainly holds. Aliases are built once, and only from the
        commas the editor already used - this splits an existing headword, it
        does not invent one."""
        d = self._load_dictionary()
        key = (term or "").strip().lower()
        if not key:
            return None
        hit = d.get(key)
        if hit:
            return hit
        if self._aliases is None:
            aliases = {}
            for k, v in d.items():
                if "," in k:
                    for part in k.split(","):
                        part = part.strip()
                        if len(part) > 2:
                            aliases.setdefault(part, v)
            self._aliases = aliases
            self.log(f"dictionary: {len(aliases)} compound-headword aliases")
        hit = self._aliases.get(key)
        if hit:
            return hit
        # "cestui que trust" - a phrase whose first word is the headword. This
        # is a LAST resort: it turns any multi-word phrase into a single-word
        # definition, so "exclusive jurisdiction" came back as Black's entry for
        # "Exclusive" and the treatise sections that actually discuss the
        # doctrine were never reached. Callers try the corpus in between.
        if not loose:
            return None
        first = key.split()[0]
        return d.get(first) or self._aliases.get(first)

    def _definitions_for(self, text, terms, limit=6):
        """Definitions for the terms this extraction actually turns on, and only
        where the text genuinely uses the word. Returns [] rather than padding -
        an empty context block is better than an irrelevant one."""
        lowered = (text or "").lower()
        out = []
        for t in terms:
            if len(out) >= limit:
                break
            if t.lower() not in lowered:
                continue
            entry = self.lookup_term(t)
            if entry:
                out.append(entry)
        return out

    def _format_definitions(self, entries):
        if not entries:
            return ""
        lines = ["Definitions of terms used below, for reading the instrument "
                 "precisely. A definition inside the instrument itself always "
                 "overrides these."]
        for e in entries:
            lines.append(f"- {e['term']}: {e['definition']}")
            lines.append(f"  (source: {e['source']})")
        return "\n".join(lines) + "\n\n"

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
        lines = ["Relevant cached reference material:"]
        for h in hits:
            lines.append(f"- [{h['category'] or 'general'}/{h['id']}] {h['snippet']}")
        return "\n".join(lines) + "\n\n"

    def _check_theory(self, text):
        out = []
        for pattern, label, authority in FATAL_THEORY_MARKERS:
            m = re.search(pattern, text, re.I)
            if m:
                out.append({"severity": "BLOCK", "check": "vehicle_selection",
                            "found": m.group(0), "issue": label, "authority": authority,
                            "why": ("This is not a drafting problem. Courts reach this "
                                    "conclusion before considering the merits, so any "
                                    "legitimate underlying claim is dismissed with it.")})
        return out

    def _check_caption(self, text):
        head = "\n".join(text.strip().splitlines()[:12]).lower()
        for pattern in INERT_CAPTIONS:
            if re.search(pattern, head, re.I):
                return [{"severity": "BLOCK", "check": "filing_discipline",
                         "issue": "caption is not a vehicle that requests relief",
                         "authority": ("No Federal Rule authorises an 'Advisory to the Court'. "
                                       "56 such filings over 700+ pages in 5:25-cv-00500-XR were "
                                       "denied wholesale and became the basis for a Rule 11 and "
                                       "pre-filing-injunction warning."),
                         "why": ("A court cannot grant a document that asks for nothing. "
                                 "Re-caption as a motion stating the relief sought.")}]
        if not any(v in head for v in RECOGNISED_VEHICLES):
            return [{"severity": "WARN", "check": "filing_discipline",
                     "issue": "no recognised vehicle found in the caption",
                     "authority": "FRCP 7(b) - a request for a court order must be made by motion.",
                     "why": "State plainly what the document is and what it asks the court to do."}]
        return []

    def _check_citations(self, text):
        out = []
        # 28 U.S.C. 1651 is not a jurisdictional grant - the specific error on this docket.
        if re.search(r'1651', text) and not re.search(r'\b1361\b', text):
            out.append({"severity": "WARN", "check": "citation_accuracy",
                        "issue": "relies on 28 U.S.C. 1651 (All Writs Act) as a basis for relief",
                        "authority": ("Clinton v. Goldsmith, 526 U.S. 529 (1999) - the All Writs "
                                      "Act is not an independent grant of jurisdiction. Mandamus "
                                      "against a federal officer runs under 28 U.S.C. 1361 and "
                                      "requires a clear nondiscretionary duty."),
                        "why": "Confirm the statute invoked actually confers the jurisdiction asked for."})
        cites = re.findall(r'\b\d+\s+U\.?\s?S\.?\s?C\.?\s*(?:§+\s*)?\d+[a-zA-Z0-9\-]*', text)
        if cites:
            out.append({"severity": "VERIFY", "check": "citation_accuracy",
                        "issue": f"{len(cites)} statutory citation(s) to verify",
                        "citations": sorted(set(c.strip() for c in cites))[:12],
                        "authority": ("Both orders in 5:25-cv-00500-XR footnote that cited statutes "
                                      "were not at the U.S. Code sections given."),
                        "why": "A court that catches a miscitation discounts the whole filing."})
        return out

    def add_deadline(self, case_id=None, name=None, trigger_event=None,
                     trigger_date=None, period_days=None, citation=None,
                     consequence=None, note=None):
        """Register a deadline, computed from an authority that was READ.

        The principal's architecture puts *"track deadlines and procedural
        posture"* under Legal, and there was no register - only one hardcoded
        FRCP 72(b) objection window, reachable only while checking a draft.
        For a live matter that is the one irreversible gap: every other error
        here can be corrected, and a limitation period cannot.

        The rule that makes it safe is the same one governing everything else:
        **a period is not computed unless the authority stating it is in the
        corpus and openable.** A deadline invented from recollection is exactly
        the inference-become-legal-fact this system exists to prevent, and it
        would be the most dangerous instance of it - confidently wrong about
        the only thing that cannot be undone."""
        if not (name and trigger_date and period_days and citation):
            return {"error": ("Needs name, trigger_date, period_days and citation. The "
                              "citation is not optional: a period this agent cannot open is "
                              "a period it must not compute.")}
        located = self.lookup_reference(citation)
        if not located:
            return {"error": (f"'{citation}' is not in this corpus, so the period cannot be "
                              f"verified and no deadline was recorded. Acquire it with "
                              f"tools/ingest_law.py and try again."),
                    "recorded": False, "authority_located": False}
        try:
            start = datetime.fromisoformat(str(trigger_date)[:19])
            days = int(period_days)
        except Exception as exc:
            return {"error": f"trigger_date or period_days unusable: {exc}"}
        due = start + timedelta(days=days)
        left = (due - datetime.now()).days
        rec = {
            "id": f"deadline_{self._uid()}",
            "case_id": case_id, "name": name,
            "trigger_event": trigger_event, "trigger_date": start.date().isoformat(),
            "period_days": days, "due": due.date().isoformat(),
            "days_remaining": left,
            "status": ("PASSED" if left < 0 else "CRITICAL" if left <= 14
                       else "WARN" if left <= 45 else "OPEN"),
            "citation": citation,
            "authority_excerpt": re.sub(r"\s+", " ",
                                        str((located[0] or {}).get("text") or ""))[:400],
            "authority_work": (located[0] or {}).get("title"),
            "consequence": consequence or ("Not stated. A deadline whose consequence is "
                                           "unrecorded cannot be prioritised against another."),
            "note": note,
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            raw = self._unwrap_value(self.retrieve_own_memory("deadline_index"))
            idx = json.loads(raw) if raw else []
        except Exception:
            idx = []
        self.store_own_memory(rec["id"], json.dumps(rec), pin=True)
        idx.append(rec["id"])
        self.store_own_memory("deadline_index", json.dumps(idx))
        return rec

    def deadlines(self, case_id=None):
        """The register, soonest first. Passed deadlines stay - a limitation
        period that ran is a fact about the matter, not a row to tidy away."""
        try:
            raw = self._unwrap_value(self.retrieve_own_memory("deadline_index"))
            idx = json.loads(raw) if raw else []
        except Exception:
            idx = []
        out = []
        for did in idx:
            r = self._unwrap_value(self.retrieve_own_memory(did))
            if not r:
                continue
            try:
                d = json.loads(r)
            except Exception:
                continue
            if case_id and d.get("case_id") != case_id:
                continue
            try:
                left = (datetime.fromisoformat(d["due"]) - datetime.now()).days
                d["days_remaining"] = left
                d["status"] = ("PASSED" if left < 0 else "CRITICAL" if left <= 14
                               else "WARN" if left <= 45 else "OPEN")
            except Exception:
                pass
            out.append(d)
        out.sort(key=lambda x: x.get("due") or "9999")
        return {"case_id": case_id, "count": len(out), "deadlines": out,
                "passed": [d["name"] for d in out if d.get("status") == "PASSED"],
                "critical": [d["name"] for d in out if d.get("status") == "CRITICAL"],
                "note": ("Every period here was computed from an authority located in the "
                         "corpus at the time it was recorded. None was recalled.")}

    # ------------------------------------------------------------------
    # The action register: what a person still has to DO.
    #
    # Deliberately separate from `deadlines`. A deadline is a period computed
    # from an authority, and the register refuses to hold one it cannot open.
    # An action is a step somebody must take - send the notice, file the
    # complaint, request the ledger - and it is not law, so it needs no
    # citation. Merging them would force every errand to carry a statute or
    # make the deadline register accept things nobody verified. They travel
    # together instead: an action may point at the deadline it protects.
    #
    # One rule carries the whole thing: AN ACTION IS NOT DONE UNTIL SOMETHING
    # SHOWS IT WAS DONE. A certified-mail notice with no green card is not a
    # notice this principal can prove he sent, and under Tex. Prop. Code
    # 92.056(b) the repair duty turns on notice having been given. "I sent it"
    # and "I can show I sent it" are different states of the world, and a
    # to-do list that cannot tell them apart tells its owner he is covered
    # when he is not. So `complete_action` requires an evidence reference and
    # refuses without one - the same rule the case layer applies to a payment.

    ACTION_STATES = ("open", "in_progress", "blocked", "done", "not_needed")

    def open_action(self, args):
        """Record something that still has to be done, and what will show it was."""
        a = args if isinstance(args, dict) else {}
        what = str(a.get("what") or "").strip()
        if not what:
            return {"error": "An action needs `what` - the step somebody has to take."}
        proof = str(a.get("evidence_expected") or "").strip()
        if not proof:
            return {"error": ("An action needs `evidence_expected`: what will show this was "
                              "actually done. Deciding that at the end is how a step gets "
                              "marked complete with nothing behind it.")}
        owner = str(a.get("owner") or "principal").strip().lower()
        rec = {
            "id": f"action_{self._uid()}",
            "case_id": a.get("case_id"),
            "what": what,
            # More than one thing can prove a step was taken, and which ones
            # count is usually decided by an authority rather than by whoever
            # wrote the to-do item. A single string here quietly encoded one
            # method as if it were the only one - which is how a list starts
            # telling its owner to do more than the law asks.
            "evidence_alternatives": a.get("evidence_alternatives") or None,
            "why": str(a.get("why") or "").strip() or None,
            "owner": owner,
            "forum": a.get("forum"),
            "due": a.get("due"),
            "protects_deadline": a.get("protects_deadline"),
            "evidence_expected": proof,
            "evidence_ref": None,
            "status": "open",
            "blocked_by": None,
            "opened_at": datetime.now().isoformat(timespec="seconds"),
            "completed_at": None,
        }
        if not rec["why"]:
            rec["why"] = ("Not stated. An action with no stated purpose cannot be ranked "
                          "against another, and is the first thing to be dropped.")
        try:
            raw = self._unwrap_value(self.retrieve_own_memory("action_index"))
            idx = json.loads(raw) if raw else []
        except Exception:
            idx = []
        self.store_own_memory(rec["id"], json.dumps(rec), pin=True)
        idx.append(rec["id"])
        self.store_own_memory("action_index", json.dumps(idx))
        return rec

    def complete_action(self, args):
        """Close an action. Refuses without proof, and says why."""
        a = args if isinstance(args, dict) else {}
        aid = str(a.get("action_id") or "").strip()
        state = str(a.get("status") or "done").strip().lower()
        if state not in self.ACTION_STATES:
            return {"error": f"status must be one of {list(self.ACTION_STATES)}"}
        raw = self._unwrap_value(self.retrieve_own_memory(aid)) if aid else None
        if not raw:
            return {"error": f"no such action: {aid or '(none given)'}"}
        try:
            rec = json.loads(raw)
        except Exception:
            return {"error": f"action {aid} is unreadable"}

        ref = str(a.get("evidence_ref") or "").strip()
        if state == "done" and not ref:
            return {"error": ("Cannot mark this done without `evidence_ref`. Expected: "
                              f"{rec.get('evidence_expected')}. Doing a thing and being able "
                              f"to show it was done are different states, and only the second "
                              f"one survives a denial."),
                    "recorded": False, "action_id": aid, "status": rec.get("status")}
        if state == "blocked" and not a.get("blocked_by"):
            return {"error": "A blocked action needs `blocked_by` - what is in the way."}

        rec["status"] = state
        rec["evidence_ref"] = ref or rec.get("evidence_ref")
        rec["blocked_by"] = a.get("blocked_by") or rec.get("blocked_by")
        if a.get("note"):
            rec["note"] = a["note"]
        rec["completed_at"] = (datetime.now().isoformat(timespec="seconds")
                               if state in ("done", "not_needed") else None)
        self.store_own_memory(aid, json.dumps(rec), pin=True)
        if state == "done" and rec.get("case_id"):
            try:
                self.case(rec["case_id"]).complete_task(rec["what"], f"evidence: {ref}")
            except Exception as exc:
                rec["case_event_failed"] = str(exc)
        return rec

    def amend_action(self, args):
        """Correct an open action in place. Merges; never rebuilds.

        Amend rather than reopen, because the history of a step matters: an
        action whose proof requirement was WRONG and then corrected is a
        different record from one that always said the right thing, and the
        first is the one worth being able to see."""
        a = args if isinstance(args, dict) else {}
        aid = str(a.get("action_id") or "").strip()
        raw = self._unwrap_value(self.retrieve_own_memory(aid)) if aid else None
        if not raw:
            return {"error": f"no such action: {aid or '(none given)'}"}
        try:
            rec = json.loads(raw)
        except Exception:
            return {"error": f"action {aid} is unreadable"}
        touched = []
        for f in ("what", "why", "owner", "forum", "due", "protects_deadline",
                  "evidence_expected", "evidence_alternatives", "note"):
            if a.get(f) not in (None, ""):
                rec[f] = a[f]
                touched.append(f)
        if not touched:
            return {"error": "amend_action was given nothing to change.",
                    "amendable": ["what", "why", "owner", "forum", "due",
                                  "protects_deadline", "evidence_expected",
                                  "evidence_alternatives", "note"]}
        hist = rec.setdefault("amendments", [])
        hist.append({"at": datetime.now().isoformat(timespec="seconds"),
                     "fields": touched, "reason": a.get("reason") or "not stated"})
        self.store_own_memory(aid, json.dumps(rec), pin=True)
        return rec

    def actions(self, case_id=None, include_closed=False):
        """The open list, most urgent first. Closed items are kept but hidden
        by default - a completed step with its proof attached is the record
        that the step was taken."""
        try:
            raw = self._unwrap_value(self.retrieve_own_memory("action_index"))
            idx = json.loads(raw) if raw else []
        except Exception:
            idx = []
        out = []
        for aid in idx:
            r = self._unwrap_value(self.retrieve_own_memory(aid))
            if not r:
                continue
            try:
                rec = json.loads(r)
            except Exception:
                continue
            if case_id and rec.get("case_id") != case_id:
                continue
            if not include_closed and rec.get("status") in ("done", "not_needed"):
                continue
            if rec.get("due"):
                try:
                    rec["days_remaining"] = (
                        datetime.fromisoformat(str(rec["due"])[:19]) - datetime.now()).days
                except Exception:
                    pass
            out.append(rec)
        rank = {"blocked": 0, "in_progress": 1, "open": 2, "done": 3, "not_needed": 4}
        out.sort(key=lambda x: (x.get("due") or "9999", rank.get(x.get("status"), 9)))
        return {"case_id": case_id, "count": len(out), "actions": out,
                "blocked": [x["what"] for x in out if x.get("status") == "blocked"],
                "awaiting_proof": [x["what"] for x in out
                                   if x.get("status") == "in_progress" and not x.get("evidence_ref")],
                "note": ("Nothing here is closed without a reference to what shows it was "
                         "done. An action list that closes on assertion is a list of things "
                         "somebody believes happened.")}

    def _check_deadlines(self, text, matter):
        """Deadline exposure. Reports days remaining; never guesses a date it
        was not given."""
        out = []
        if re.search(r'report and recommendation|\bR&R\b', text, re.I) and \
           not re.search(r'\bobjection', text, re.I):
            out.append({"severity": "BLOCK", "check": "deadline",
                        "issue": "responds to a Report and Recommendation but is not captioned as objections",
                        "authority": ("28 U.S.C. 636(b)(1); FRCP 72(b) - 14 days for SPECIFIC "
                                      "WRITTEN objections. In 5:25-cv-00500-XR eleven filings "
                                      "landed in that window and the court held none were "
                                      "objections, so it conducted no de novo review. Under "
                                      "Douglass, 79 F.3d 1415 (5th Cir. en banc), appellate "
                                      "review of unobjected findings is then barred but for "
                                      "plain error."),
                        "why": ("Caption it OBJECTIONS TO REPORT AND RECOMMENDATION and identify "
                                "each finding objected to and the basis for the objection.")})
        rr = (matter or {}).get("rr_filed_date")
        if rr:
            try:
                due = datetime.fromisoformat(rr) + timedelta(days=RR_OBJECTION_DAYS)
                left = (due - datetime.now()).days
                out.append({"severity": "BLOCK" if left < 0 else ("WARN" if left <= 5 else "INFO"),
                            "check": "deadline",
                            "issue": (f"objections due {due.date().isoformat()} "
                                      f"({'PASSED' if left < 0 else str(left) + ' days left'})"),
                            "authority": "28 U.S.C. 636(b)(1); FRCP 72(b)",
                            "why": "Fourteen days from service of the R&R."})
            except Exception:
                pass
        return out

    def _check_volume(self, matter_id):
        if not matter_id:
            return []
        try:
            filings = self._get_layer_entries("filing_index", matter_id)
        except Exception:
            return []
        if not filings:
            return []
        cutoff = datetime.now() - timedelta(days=FILING_BURST_WINDOW_DAYS)
        recent = [f for f in filings
                  if (lambda t: t and t > cutoff)(self._safe_ts(f.get("timestamp")))]
        if len(recent) >= FILING_BURST_THRESHOLD:
            return [{"severity": "WARN", "check": "filing_discipline",
                     "issue": f"{len(recent)} filings in the last {FILING_BURST_WINDOW_DAYS} days",
                     "authority": ("FRCP 11; In re Stone, 986 F.2d 898 (5th Cir. 1993); Baum v. "
                                   "Blue Moon Ventures, 513 F.3d 181 (5th Cir. 2008). A "
                                   "pre-filing-injunction warning is already on this principal's "
                                   "record from 5:25-cv-00500-XR."),
                     "why": ("Volume was what drew the sanctions discussion in the prior case. "
                             "One properly captioned motion beats several advisories.")}]
        return []

    @staticmethod
    def _safe_ts(value):
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None

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
                    status["ok"] = True
                    return data.get("result", "")
                status["reason"] = f"inference_error: {data.get('message', 'unknown')}"
            else:
                status["reason"] = f"http_{resp.status_code}"
            self.log(f"Inference Service returned error (HTTP {resp.status_code}); trying fallback.")
        except requests.exceptions.Timeout:
            status["reason"] = f"timeout after {timeout}s"
            self.log(f"Inference Service timed out after {timeout}s; trying fallback.")
        except Exception as e:
            status["reason"] = f"transport: {e}"
            self.log(f"Inference Service call failed ({e}); trying fallback.")

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

    # The roles this schema most often confuses, and which a real instrument
    # turns on. Ordered so the trustee/custodian/beneficiary triangle - the one
    # the model demonstrably got wrong before definitions were available - is
    # resolved first.
    SCHEMA_TERMS = ("trustee", "custodian", "beneficiary", "settlor", "fiduciary",
                    "party in interest", "beneficial interest", "investment manager",
                    "consideration", "assignment", "indemnity", "lien", "escrow")

    def _extract_relationship(self, contract_text, model=None):
        cache_hits = self._cache_context_for(contract_text)
        definitions = self._definitions_for(contract_text, self.SCHEMA_TERMS)
        context_block = self._format_definitions(definitions) + self._format_context_block(cache_hits)
        list_fields = {"obligations", "rights", "applicable_statutes"}
        prompt = (
            context_block +
            "You are a private-ordering structuring assistant. Read the text below - it may be "
            "a contract, trust document, lease, arbitration agreement, or other private "
            "arrangement - and extract ONLY the following fields as a single valid JSON object. "
            f'"relationship_type" must be exactly one of: {", ".join(RELATIONSHIP_TYPES)}. '
            "Leave any field that doesn't apply to this relationship_type as an empty string "
            "or empty list rather than guessing.\n"
            "{\n"
            '  "relationship_type": "",\n'
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
            '  "applicable_statutes": [],\n'
            '  "offer": "",\n'
            '  "acceptance": "",\n'
            '  "consideration": "",\n'
            '  "termination_conditions": "",\n'
            '  "dispute_resolution": "",\n'
            '  "performance_requirements": "",\n'
            '  "settlor": "",\n'
            '  "trustee": "",\n'
            '  "trust_property": "",\n'
            '  "fiduciary_duties": "",\n'
            '  "governing_documents": "",\n'
            '  "jurisdiction": "",\n'
            '  "venue": "",\n'
            '  "court_authority": "",\n'
            '  "procedural_rules_source": ""\n'
            "}\n"
            "If a field cannot be determined, use empty string or empty list. "
            "Only extract what is explicitly stated. Do not determine enforceability or legal "
            "validity - structural extraction only.\n\n"
            f"Text:\n\"\"\"\n{contract_text}\n\"\"\"\n\nJSON:"
        )
        status = {}
        raw = self._call_inference(prompt, model_name=model, status=status, temperature=0)
        parsed, parse_error = self._safe_parse_json(raw)
        # An empty return means the model never answered - a timeout on this
        # hardware, most often. Reporting that as parse_error blames the model
        # for a bad answer when it gave none, and sends anyone debugging it to
        # the prompt instead of the clock.
        inference_error = None if status.get("ok") else (status.get("reason") or "no response")
        if parsed is None:
            parsed = {field: ([] if field in list_fields else "") for field in EXTENDED_RELATIONSHIP_FIELDS}
            parsed["relationship_type"] = ""
        else:
            for field in EXTENDED_RELATIONSHIP_FIELDS:
                parsed.setdefault(field, [] if field in list_fields else "")
            if parsed.get("relationship_type") not in RELATIONSHIP_TYPES:
                parsed["relationship_type"] = parsed.get("relationship_type") or "contractual"
        parsed["parse_error"] = parse_error and inference_error is None
        if inference_error:
            parsed["inference_error"] = inference_error
        if parse_error:
            parsed["raw_model_output"] = raw
        parsed["cache_sources"] = [h["id"] for h in cache_hits]
        parsed["disclaimer"] = DISCLAIMER
        return parsed

    def _extract_case(self, case_text, model=None):
        prompt = (
            "You are a legal case analyst. Read the following court case text and extract "
            "ONLY the following fields as a single valid JSON object:\n"
            "{\n"
            '  "parties": [{"name": "", "role": ""}],\n'
            '  "legal_issues": [],\n'
            '  "ruling": "",\n'
            '  "date": "",\n'
            '  "court": "",\n'
            '  "jurisdiction": "",\n'
            '  "cited_statutes": [],\n'
            '  "summary": ""\n'
            "}\n"
            "If a field cannot be determined, use an empty string or empty list. "
            "Only extract what is explicitly stated.\n\n"
            f"Case text:\n\"\"\"\n{case_text[:8000]}\n\"\"\"\n\nJSON:"
        )
        status = {}
        raw = self._call_inference(prompt, model_name=model, status=status, temperature=0)
        parsed, parse_error = self._safe_parse_json(raw)
        # An empty return means the model never answered - a timeout on this
        # hardware, most often. Reporting that as parse_error blames the model
        # for a bad answer when it gave none, and sends anyone debugging it to
        # the prompt instead of the clock.
        inference_error = None if status.get("ok") else (status.get("reason") or "no response")

        if parsed is None:
            parsed = {
                "parties": [],
                "legal_issues": [],
                "ruling": "",
                "date": "",
                "court": "",
                "jurisdiction": "",
                "cited_statutes": [],
                "summary": ""
            }

        if not isinstance(parsed.get("parties"), list):
            parsed["parties"] = []
        if not isinstance(parsed.get("legal_issues"), list):
            parsed["legal_issues"] = []
        if not isinstance(parsed.get("cited_statutes"), list):
            parsed["cited_statutes"] = []

        cleaned = {
            "parties": parsed.get("parties", []),
            "legal_issues": parsed.get("legal_issues", []),
            "ruling": parsed.get("ruling", "") or "",
            "date": parsed.get("date", "") or "",
            "court": parsed.get("court", "") or "",
            "jurisdiction": parsed.get("jurisdiction", "") or "",
            "cited_statutes": parsed.get("cited_statutes", []),
            "summary": parsed.get("summary", "") or "",
            "parse_error": parse_error and inference_error is None,
            "disclaimer": DISCLAIMER
        }
        if inference_error:
            cleaned["inference_error"] = inference_error

        if parse_error:
            cleaned["raw_model_output"] = raw

        return cleaned

    def _analyze_case_perspectives(self, case_text):
        """Four short, separate inference passes - kept out of _extract_case so the
        already-verified baseline extraction is untouched when perspectives isn't requested."""
        questions = {
            "judge": "What authority does the court have? What requirements must be satisfied? "
                     "What information is missing? What makes a filing difficult to evaluate?",
            "clerk": "Is the filing properly organized? Can deadlines and requested actions be "
                     "identified? Are documents easy to process?",
            "plaintiff": "What injury is alleged? What evidence supports it? What remedy is requested?",
            "defendant": "What arguments would challenge the claim? What weaknesses would be identified?",
        }
        perspectives = {}
        for role, questions_text in questions.items():
            prompt = (
                f"You are analyzing a legal case from the {role}'s perspective. Answer these "
                f"questions briefly, based only on what's in the text below - do not give legal "
                f"advice or an opinion on who should win:\n{questions_text}\n\n"
                f"Case text:\n\"\"\"\n{case_text[:6000]}\n\"\"\"\n\nAnswer:"
            )
            perspectives[role] = self._call_inference(prompt)
        return perspectives

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

    # ---------- Generic index helper (case/matter/evidence-layer storage) ----------
    def _load_index_key(self, key):
        raw = self._get_stored_value(self.retrieve_own_memory(key))
        if not raw:
            return []
        try:
            index = json.loads(raw)
            return index if isinstance(index, list) else []
        except Exception:
            return []

    def _append_to_index_key(self, key, item_id):
        index = self._load_index_key(key)
        if item_id not in index:
            index.append(item_id)
            self.store_own_memory(key, json.dumps(index))

    def _load_record(self, record_id):
        raw = self._get_stored_value(self.retrieve_own_memory(record_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    # ---------- Case index (fixes the bug: analyze_case previously wrote to
    # "case_index" but read from "relationship_index" via _load_index(), so
    # stored cases were never discoverable) ----------
    def _load_case_index(self):
        return self._load_index_key("case_index")

    def _append_to_case_index(self, case_id):
        self._append_to_index_key("case_index", case_id)

    # ---------- Matter lifecycle + evidence layers ----------
    def _get_layer_entries(self, index_key, matter_id):
        entries = []
        for entry_id in self._load_index_key(index_key):
            entry = self._load_record(entry_id)
            if entry and entry.get("matter_id") == matter_id:
                entries.append(entry)
        entries.sort(key=lambda e: e.get("timestamp", ""))
        return entries

    def _add_layer_entry(self, args, index_key, layer_name):
        if not isinstance(args, dict) or not args.get("matter_id") or not args.get("content"):
            return {"error": "Usage: {matter_id, content, [source_type]}", "disclaimer": DISCLAIMER}
        entry_id = f"{layer_name}_{uuid.uuid4().hex[:12]}"
        entry = {
            "id": entry_id,
            "matter_id": args["matter_id"],
            "layer": layer_name,
            "content": args["content"],
            "source_type": args.get("source_type", ""),
            "timestamp": datetime.now().isoformat(),
        }
        self.store_own_memory(entry_id, json.dumps(entry))
        self._append_to_index_key(index_key, entry_id)
        return {"entry": entry, "disclaimer": DISCLAIMER}

    # ---------- Explainable recommendation shape (observation/reason/action/confidence) ----------
    def _make_recommendation(self, observation, reason, action, confidence):
        return {"observation": observation, "reason": reason, "action": action, "confidence": confidence}

    # ---------- Lessons learned: shared writer, used by log_lesson task,
    # reflect_on_matter (item 11), and analyze_case's case-lesson filing (item 15) ----------
    def _write_lesson(self, lesson_text, strategy_type="general", case_id="", tags="", category="general"):
        if category not in LESSON_CATEGORIES:
            category = "general"
        lesson_id = f"lesson_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        front_matter = (
            "---\n"
            f"lesson_id: {lesson_id}\n"
            f"created: {datetime.now().isoformat()}\n"
            f"strategy_type: {strategy_type}\n"
            f"category: {category}\n"
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
        self.log(f"Logged lesson {lesson_id} (category={category}, strategy_type={strategy_type})")
        return {
            "lesson_id": lesson_id,
            "path": f"lessons_learned/{lesson_id}.md",
            "strategy_type": strategy_type,
            "category": category,
            "case_id": case_id,
            "tags": tags,
        }

    def _lesson_category(self, doc_id):
        doc = self.cache.get(doc_id) if hasattr(self, "cache") else None
        if not doc:
            return None
        m = re.search(r"^category:\s*(\S+)", doc.get("content", ""), re.MULTILINE)
        return m.group(1) if m else None

    # ---------- Privacy Protection Review ----------
    def _mask_pii(self, value):
        value = str(value)
        if len(value) <= 4:
            return "*" * len(value)
        return "*" * (len(value) - 4) + value[-4:]

    def _scan_for_pii(self, text):
        hits = []
        for category, pattern in PII_PATTERNS.items():
            for m in pattern.finditer(text):
                matched = m.group(1) if m.groups() else m.group(0)
                hits.append({
                    "category": category,
                    "matched_text": self._mask_pii(matched),
                    "recommendation": "Redact before filing.",
                    "method": "regex"
                })
        lowered = text.lower()
        for kw in MEDICAL_KEYWORDS:
            if kw in lowered:
                hits.append({
                    "category": "medical_information",
                    "matched_text": f"[context near '{kw}']",
                    "recommendation": "Redact before filing.",
                    "method": "keyword"
                })
                break
        llm_prompt = (
            "Does the following text contain any personally identifiable or protected "
            "information NOT limited to Social Security numbers, tax ID numbers, financial "
            "account numbers, or birth dates (e.g. minor identifiers, medical information, other "
            "protected identifiers)? Answer with either 'none found' or a brief comma-separated "
            "list of what you found, no explanation:\n\n" + text[:3000]
        )
        llm_result = self._call_inference(llm_prompt)
        if llm_result and "none found" not in llm_result.lower():
            hits.append({
                "category": "other_protected_information",
                "matched_text": llm_result.strip()[:300],
                "recommendation": "Review and redact before filing.",
                "method": "llm"
            })
        return hits

    def _unwrap_tool_result(self, tool_response, disclaimer=False):
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


    # ---- Operating jurisdiction ------------------------------------------
    #
    # Every state enacted UCC Article 9 with the SAME uniform section numbers
    # and then renumbered them into its own code, by conventions that differ:
    # 9-203 is A.R.S. 47-9203 in Arizona, Bus. & Com. Code 9.203 in Texas,
    # Com. Code 9203 in California and Fla. Stat. 679.2031 in Florida. So a
    # concept learned in one state's citations does not travel. The uniform
    # section number does, and it is what this agent reasons in.
    #
    # The principal moves between states. Nothing here hardcodes one.

    JURISDICTION_KEY = "operating_jurisdiction"
    UNIFORM_SECTION_RE = re.compile(r"(\d+)\s*[-.\u2010-\u2015]?\s*(\d+[A-Za-z]?)")

    def _load_jurisdictions(self):
        if getattr(self, "_jur", None) is not None:
            return self._jur
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "reference", "legal_agent", "jurisdictions.json")
        try:
            with open(path) as fh:
                self._jur = json.load(fh)
        except Exception as e:
            self.log(f"jurisdictions: could not load: {e}")
            self._jur = {"jurisdictions": {}, "uniform_sections_worth_knowing": {}}
        return self._jur

    def _resolve_state(self, name):
        """A postal abbreviation or a state name, however cased."""
        if not name:
            return None
        key = str(name).strip()
        table = self._load_jurisdictions().get("jurisdictions", {})
        if key.upper() in table:
            return key.upper()
        for abbr, e in table.items():
            if e.get("state", "").lower() == key.lower():
                return abbr
        return None

    def get_operating_jurisdiction(self):
        raw = self._get_stored_value(self.retrieve_own_memory(self.JURISDICTION_KEY))
        try:
            rec = json.loads(raw) if raw else {}
        except Exception:
            rec = {}
        return rec if isinstance(rec, dict) else {}

    def set_operating_jurisdiction(self, args):
        """MERGE, never rebuild. set_grow_system once silently dropped every
        field not passed; the same mistake here would erase a business state
        because someone updated a residential one."""
        rec = self.get_operating_jurisdiction()
        changed = {}
        for field in ("residential", "business", "transaction_situs", "note"):
            val = args.get(field)
            if val in (None, ""):
                continue
            if field == "note":
                rec.setdefault("notes", []).append(str(val))
                changed[field] = val
                continue
            abbr = self._resolve_state(val)
            if not abbr:
                return {"error": f"{val!r} is not a US state or DC I hold a "
                                 f"jurisdiction record for",
                        "disclaimer": DISCLAIMER}
            rec[field] = abbr
            changed[field] = abbr
        if not changed:
            return {"error": "Usage: set_operating_jurisdiction "
                             "residential=TX business=TX transaction_situs=AZ",
                    "disclaimer": DISCLAIMER}
        rec["updated"] = datetime.now().isoformat()
        self.store_own_memory(self.JURISDICTION_KEY, json.dumps(rec), pin=True)
        return {"updated": changed, "operating_jurisdiction": rec,
                "disclaimer": DISCLAIMER}

    def cite_in_jurisdiction(self, section, state=None, role="transaction_situs"):
        """Uniform section -> the citation the operating state actually uses.

        An unverified entry is returned as a CANDIDATE and says so. A wrong
        citation presented as authority is worse than none, because the
        reasoning layer trusts it."""
        doc = self._load_jurisdictions()
        table = doc.get("jurisdictions", {})
        m = self.UNIFORM_SECTION_RE.search(str(section or ""))
        if not m:
            return {"error": f"{section!r} is not a uniform section number "
                             f"(expected a shape like 9-203)",
                    "disclaimer": DISCLAIMER}
        uniform = f"{m.group(1)}-{m.group(2)}"

        abbr = self._resolve_state(state) if state else None
        rec = self.get_operating_jurisdiction()
        if not abbr:
            abbr = rec.get(role) or rec.get("business") or rec.get("residential")
        if not abbr:
            return {"uniform_section": uniform,
                    "unresolved": "No operating jurisdiction is on record and "
                                  "none was named. Set one with "
                                  "set_operating_jurisdiction, or pass state=.",
                    "disclaimer": DISCLAIMER}

        e = table.get(abbr, {})
        out = {"uniform_section": uniform, "state": e.get("state", abbr),
               "abbr": abbr, "commercial_code": e.get("commercial_code"),
               "subject": doc.get("uniform_sections_worth_knowing", {}).get(uniform),
               "resolved_from": "argument" if state else f"operating record ({role})"}

        # Renumbering is per-section, not per-state: Florida's 9-203 is
        # 679.2031, so the verified exemplar is only a citation when the
        # section asked for IS the one verified.
        verified_for = "9-203"
        if e.get("cite_9_203") and uniform == verified_for:
            out["citation"] = e["cite_9_203"]
            out["standing"] = e.get("standing", {})
        elif e.get("cite_9_203"):
            out["candidate"] = self._project_cite(e["cite_9_203"], verified_for, uniform)
            out["standing"] = {"verified": False,
                               "basis": f"projected from the verified {abbr} "
                                        f"citation for {verified_for} "
                                        f"({e['cite_9_203']}) - the renumbering "
                                        f"convention is confirmed, this "
                                        f"section is not"}
        else:
            out["candidate"] = self._project_cite(e.get("candidate_9_203", ""),
                                                 verified_for, uniform)
            out["standing"] = {"verified": False,
                               "basis": f"pattern only - no {abbr} citation has "
                                        f"been confirmed against the state's "
                                        f"published text"}
        if e.get("note"):
            out["convention"] = e["note"]

        # If this agent HOLDS the state's enactment and the resolved citation
        # is in it, that is stronger proof than any external check: the text is
        # sitting here under that exact key. Verifiable state outranks the
        # table's own bookkeeping, so the corpus is allowed to promote a
        # candidate to verified - and to demote nothing, since absence from a
        # partial corpus proves nothing either way.
        probe = out.get("citation") or out.get("candidate")
        if probe:
            bare = re.search(r'§\s*[\d.\-]+[A-Za-z]?', probe)
            hit = self.lookup_reference(bare.group(0) if bare else probe)
            if hit and (hit[0].get("title") or "").strip():
                out["citation"] = out.pop("candidate", out.get("citation"))
                out["standing"] = {
                    "verified": True,
                    "source": f"held in this agent's corpus: {hit[0]['title']}",
                    "basis": "the enactment itself is on the shelf under this "
                             "citation, which is what a citation being correct "
                             "means",
                }
                out["text_available"] = True
        out["disclaimer"] = DISCLAIMER
        return out

    @staticmethod
    def _project_cite(exemplar, from_uniform, to_uniform):
        """Carry a known citation's renumbering across to another section.

        Florida is the reason this is separated out and labelled unverified:
        679.2031 for 9-203 does not imply 679.3221 for 9-322."""
        if not exemplar:
            return None
        fa, fs = from_uniform.split("-")
        ta, ts = to_uniform.split("-")
        # Longest first, so "9203" is tried before "203".
        for probe in sorted({f"{fa}-{fs}", f"{fa}.{fs}", f"{fa}{fs}", fs},
                            key=len, reverse=True):
            if probe in exemplar:
                repl = {f"{fa}-{fs}": f"{ta}-{ts}", f"{fa}.{fs}": f"{ta}.{ts}",
                        f"{fa}{fs}": f"{ta}{ts}", fs: ts}[probe]
                return exemplar.replace(probe, repl, 1)
        return None

    def transaction_layers(self, stage=None, state=None):
        """Which body of law attaches at which stage of a financed transaction.

        The federal layer is uniform everywhere and sits in this agent's own
        corpus. The state layer is resolved to whatever jurisdiction is
        actually operating."""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "reference", "legal_agent",
            "transaction_layers.json")
        try:
            with open(path) as fh:
                doc = json.load(fh)
        except Exception as e:
            return {"error": f"transaction_layers not available: {e}",
                    "disclaimer": DISCLAIMER}
        rec = self.get_operating_jurisdiction()
        abbr = self._resolve_state(state) if state else (
            rec.get("transaction_situs") or rec.get("business") or rec.get("residential"))
        table = self._load_jurisdictions().get("jurisdictions", {})
        stages = doc["stages"]
        if stage is not None:
            try:
                n = int(stage)
            except (TypeError, ValueError):
                n = None
            stages = [s for s in stages if s["stage"] == n] or stages

        resolved = []
        for s in stages:
            s = dict(s)
            if abbr:
                s["state_layer"] = [
                    re.sub(r"[Uu]niform (\d+-\d+[A-Za-z]?)",
                           lambda m: (self.cite_in_jurisdiction(m.group(1), abbr)
                                      .get("citation")
                                      or self.cite_in_jurisdiction(m.group(1), abbr)
                                      .get("candidate")
                                      or m.group(0)) + f" [uniform {m.group(1)}]",
                           line)
                    for line in s.get("state_layer", [])
                ]
            resolved.append(s)
        return {"operating_state": (table.get(abbr, {}).get("state") if abbr
                                    else "not on record - set_operating_jurisdiction"),
                "state_citations_verified": bool(
                    table.get(abbr, {}).get("standing", {}).get("verified")),
                "standing": doc.get("standing"),
                "source": doc.get("source"),
                "how_to_use": doc.get("how_to_use"),
                "evaluation": doc.get("evaluation"),
                "stages": resolved, "disclaimer": DISCLAIMER}


    # ---- Claim assessment -------------------------------------------------
    #
    # A claim that quotes a real statute is still a claim. The pipeline is
    # claim -> source -> evidence -> observation -> analysis -> conclusion ->
    # confidence, and the default conclusion is "unsupported" at every step
    # that has not actually been filled in.
    #
    # Two rules keep this from becoming a confirmation engine:
    #   1. `located_in_corpus` is decided by LOOKING, never by the caller
    #      saying so. A citation nobody can open is not an authority.
    #   2. `asserted_by` is recorded and never scored. The principal's own
    #      claims run the identical gauntlet as a stranger's.

    CLAIM_INDEX_KEY = "claim_index"

    def _claim_key(self, claim_id):
        return f"claim::{claim_id}"

    def _load_claim(self, claim_id):
        raw = self._get_stored_value(self.retrieve_own_memory(self._claim_key(claim_id)))
        try:
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def _save_claim(self, claim):
        self.store_own_memory(self._claim_key(claim["claim_id"]),
                              json.dumps(claim), pin=True)
        idx = self._load_index_key(self.CLAIM_INDEX_KEY)
        if claim["claim_id"] not in idx:
            self._append_to_index_key(self.CLAIM_INDEX_KEY, claim["claim_id"])
        return claim

    def claim_open(self, args):
        stmt = (args.get("statement") or "").strip()
        if not stmt:
            return {"error": "claim_open needs a statement", "disclaimer": DISCLAIMER}
        bad = [r for r in (args.get("rights_asserted") or []) if r not in claim_assessment.RIGHTS]
        if bad:
            return {"error": f"unknown right(s): {bad}. Known: "
                             f"{sorted(claim_assessment.RIGHTS)}",
                    "disclaimer": DISCLAIMER}
        c = claim_assessment.new_claim(
            stmt, asserted_by=args.get("asserted_by", "unknown"),
            rights_asserted=args.get("rights_asserted"),
            source_of_claim=args.get("source_of_claim", ""))
        claim_assessment.assess(c)
        self._save_claim(c)
        return {"claim_id": c["claim_id"], "conclusion": c["conclusion"],
                "confidence": c["confidence"], "why": c["why"],
                "open_questions": c["open_questions"], "disclaimer": DISCLAIMER}

    def claim_cite(self, args):
        """Add an authority - and check whether this agent can actually open it.

        The caller does not get to declare that a citation exists. That is the
        whole difference between testing a claim and dressing one up."""
        c = self._load_claim(args.get("claim_id") or "")
        if not c:
            return {"error": "no such claim", "disclaimer": DISCLAIMER}
        cite = (args.get("citation") or "").strip()
        if not cite:
            return {"error": "claim_cite needs a citation", "disclaimer": DISCLAIMER}
        # A uniform section number is not a citation anywhere. The corpus
        # holds Texas's enactment keyed as "§ 9.322"; a claim reasons in
        # "9-322". Resolve through the operating jurisdiction before deciding
        # the authority is absent - otherwise the agent owns the text and
        # still reports it cannot be found.
        found = self.lookup_reference(cite)
        resolved_via = None
        if not found and re.fullmatch(r'\s*\d+\s*-\s*\d+[A-Za-z]?\s*', cite or ""):
            r = self.cite_in_jurisdiction(cite)
            local = r.get("citation") or r.get("candidate")
            if local:
                # Strip the code name; the corpus is keyed by the bare section.
                bare = re.search(r'§\s*[\d.\-]+[A-Za-z]?', local)
                probe = bare.group(0) if bare else local
                found = self.lookup_reference(probe)
                if found:
                    resolved_via = {"uniform": cite, "local_citation": local,
                                    "state": r.get("state"),
                                    "verified": r.get("standing", {}).get("verified")}
        entry = {"citation": cite,
                 "located_in_corpus": bool(found),
                 "governs": args.get("governs"),
                 "governs_basis": args.get("governs_basis", ""),
                 "checked_at": datetime.now().isoformat()}
        if resolved_via:
            entry["resolved_via_jurisdiction"] = resolved_via
        if found:
            entry["title"] = found[0].get("title")
            entry["excerpt"] = (found[0].get("text") or "")[:400]
        c["authorities"].append(entry)
        claim_assessment.assess(c); self._save_claim(c)
        return {"claim_id": c["claim_id"], "citation": cite,
                "located_in_corpus": entry["located_in_corpus"],
                "note": ("Text is in hand." if found else
                         "NOT in this agent's corpus - so it has not been read, "
                         "and cannot support the claim until it is."),
                "conclusion": c["conclusion"], "confidence": c["confidence"],
                "disclaimer": DISCLAIMER}

    def claim_answer(self, args):
        c = self._load_claim(args.get("claim_id") or "")
        if not c:
            return {"error": "no such claim", "disclaimer": DISCLAIMER}
        key, detail = args.get("prerequisite"), (args.get("detail") or "").strip()
        if key not in c["prerequisites"]:
            return {"error": f"unknown prerequisite {key!r}. Known: "
                             f"{[k for k, _ in claim_assessment.PREREQUISITES]}",
                    "disclaimer": DISCLAIMER}
        if not detail:
            return {"error": "answering a prerequisite requires a detail - an "
                             "empty answer is not an answer",
                    "disclaimer": DISCLAIMER}
        # What the answer BEARS on the claim. Default `supports` keeps every
        # existing caller working; `refutes` is what lets a claim get worse
        # when the text goes against it, which it previously could not do.
        bears = str(args.get("bears") or "supports").strip().lower()
        if bears not in ("supports", "refutes", "neutral"):
            return {"error": "bears must be supports, refutes or neutral",
                    "disclaimer": DISCLAIMER}
        c["prerequisites"][key] = {"state": "answered", "detail": detail,
                                   "bears": bears,
                                   "answered_at": datetime.now().isoformat()}
        claim_assessment.assess(c); self._save_claim(c)
        return {"claim_id": c["claim_id"], "answered": key,
                "conclusion": c["conclusion"], "confidence": c["confidence"],
                "still_open": c["open_questions"], "disclaimer": DISCLAIMER}

    def claim_set_right(self, args):
        c = self._load_claim(args.get("claim_id") or "")
        if not c:
            return {"error": "no such claim", "disclaimer": DISCLAIMER}
        right, state = args.get("right"), args.get("state")
        if right not in claim_assessment.RIGHTS:
            return {"error": f"unknown right {right!r}. Known: "
                             f"{sorted(claim_assessment.RIGHTS)}",
                    "disclaimer": DISCLAIMER}
        if state not in claim_assessment.RIGHT_STATES:
            return {"error": f"state must be one of {list(claim_assessment.RIGHT_STATES)}",
                    "disclaimer": DISCLAIMER}
        c["rights"][right] = state
        c.setdefault("rights_basis", {})[right] = args.get("basis", "")
        claim_assessment.assess(c); self._save_claim(c)
        return {"claim_id": c["claim_id"], "right": right, "state": state,
                "definition": claim_assessment.RIGHTS[right],
                "conclusion": c["conclusion"], "confidence": c["confidence"],
                "disclaimer": DISCLAIMER}

    def claim_record(self, args, field):
        """Evidence and observations - what is held, and what happened."""
        c = self._load_claim(args.get("claim_id") or "")
        if not c:
            return {"error": "no such claim", "disclaimer": DISCLAIMER}
        item = {k: v for k, v in args.items() if k != "claim_id"}
        item["recorded_at"] = datetime.now().isoformat()
        c[field].append(item)
        claim_assessment.assess(c); self._save_claim(c)
        return {"claim_id": c["claim_id"], field: len(c[field]),
                "conclusion": c["conclusion"], "confidence": c["confidence"],
                "disclaimer": DISCLAIMER}

    def claim_reproducibility(self, args):
        c = self._load_claim(args.get("claim_id") or "")
        if not c:
            return {"error": "no such claim", "disclaimer": DISCLAIMER}
        state = args.get("state")
        if state not in claim_assessment.REPRODUCIBILITY:
            return {"error": f"state must be one of {list(claim_assessment.REPRODUCIBILITY)}",
                    "disclaimer": DISCLAIMER}
        r = c["reproducibility"]
        r["state"] = state
        if args.get("procedure"):
            r["procedure"] = args["procedure"]
        if args.get("attempt"):
            r["attempts"].append({"note": args["attempt"], "by": args.get("by", "unknown"),
                                  "at": datetime.now().isoformat()})
        claim_assessment.assess(c); self._save_claim(c)
        return {"claim_id": c["claim_id"], "reproducibility": r,
                "conclusion": c["conclusion"], "confidence": c["confidence"],
                "disclaimer": DISCLAIMER}

    def claim_corroborate(self, args):
        """Ask another domain whether its records agree - and keep the answer
        whichever way it falls.

        Disagreement is the valuable outcome and is never reconciled here. If
        Legal reads an instrument as establishing something and Accounting's
        transaction records do not show it, that conflict IS the finding;
        collapsing it into a consensus would destroy the only signal that
        actually distinguishes analysis from agreement."""
        c = self._load_claim(args.get("claim_id") or "")
        if not c:
            return {"error": "no such claim", "disclaimer": DISCLAIMER}
        peer = args.get("agent") or "accounting_agent"
        question = args.get("question") or c["statement"]
        reply = self.send_a2a(peer, "assess_assertion",
                              {"assertion": question,
                               "claim_id": c["claim_id"]}, timeout=45)
        inner, seen = reply, 0
        while isinstance(inner, dict) and "agrees" not in inner and "result" in inner and seen < 6:
            inner, seen = inner["result"], seen + 1
        agrees = inner.get("agrees") if isinstance(inner, dict) else None
        rec = {"agent": peer, "question": question, "agrees": agrees,
               "basis": (inner or {}).get("basis") if isinstance(inner, dict) else None,
               "at": datetime.now().isoformat()}
        c["corroboration"].append(rec)
        claim_assessment.assess(c); self._save_claim(c)
        return {"claim_id": c["claim_id"], "corroboration": rec,
                "conflict": agrees is False,
                "note": ("CONFLICT recorded and left unresolved - two domains "
                         "disagree, which is a finding, not an error."
                         if agrees is False else
                         "Recorded." if agrees is True else
                         "The peer could not determine it either way."),
                "conclusion": c["conclusion"], "confidence": c["confidence"],
                "disclaimer": DISCLAIMER}

    def claim_get(self, claim_id):
        c = self._load_claim(claim_id or "")
        if not c:
            return {"error": "no such claim", "disclaimer": DISCLAIMER}
        c = claim_assessment.assess(c)
        c["disclaimer"] = DISCLAIMER
        return c

    # Phrases that ask for a meaning. A question is definitional or it is not,
    # and that is decided by shape rather than by a topic keyword - "what is X"
    # is the same request whether X is a lien or a light schedule.
    _DEFINITION_ASK = (
        r"^what (?:is|are|does .* mean)\b", r"^define\b", r"^definition of\b",
        r"^meaning of\b", r"\bwhat does\b.*\bmean\b", r"^who (?:is|are) a\b",
    )
    # A person with a live matter asks what they still have to do far more
    # often than they ask what a word means. `answer()` handled a citation and
    # a definition and returned None for everything else, so "what do I need to
    # do for my housing case" routed here correctly and got nothing back - the
    # register was full and unreachable by the only sentence anyone would
    # actually say.
    OWNS_TERMS = ("repair notice", "habitability", "reasonable accommodation")

    _MATTER_STATE_ASK = (
        r"\bwhat (?:do|should) i (?:need to |have to )?do\b",
        r"\bwhat(?:'s| is) (?:next|outstanding|left|pending|due)\b",
        r"\bwhat(?:'s| is) the (?:status|state|posture)\b",
        r"\bwhere (?:are we|do (?:i|we) stand|does (?:it|this|my case) stand)\b",
        r"\bto[- ]?do\b", r"\baction items?\b", r"\bmy (?:action|task)s?\b",
        r"\bwhat needs (?:to be )?(?:doing|done)\b",
        r"\b(?:any|my|the) deadlines?\b", r"\bwhen (?:is|are) .{0,24}\bdue\b",
        r"\bhow long (?:do i have|have i got)\b",
        r"\bam i (?:running out of time|late)\b",
        r"\bnext steps?\b", r"\bwhat(?:'s| is) open\b",
    )

    # A question about one deadline should not be answered with the whole
    # register. "How long do I have to file with HUD" returned four action
    # items and two periods - everything true, nothing asked for. A telling
    # that answers a narrower question with a wider one trains its reader to
    # stop reading it.
    _DEADLINE_FOCUS = (r"\bdeadlines?\b", r"\bhow long (?:do i have|have i got)\b",
                       r"\bwhen (?:is|are)\b.{0,24}\bdue\b", r"\btime (?:limit|bar)\b",
                       r"\bstatute of limitations?\b", r"\brunning out of time\b",
                       r"\bam i late\b", r"\bexpir")

    # HEARING IS RECORDING, BUT HEARING IS NOT PROOF.
    #
    # "I emailed the repair notice today" routed here correctly and hit no
    # capability, so the fact died in the conversation - the exact failure
    # CLAUDE.md documents for the grow (a clearance stated, agreed with, and
    # never written down, contradicted by an assumption two days later).
    #
    # What it must NOT do is close the item. The statement is the assertion;
    # the receipt is the proof, and this whole register exists because those
    # two are different states of the world. So a reported step moves to
    # in_progress with the date and the words recorded, and the reply names
    # exactly what would close it.
    _DID_IT_ASK = (
        r"\bi (?:have |just |already )?(?:sent|emailed|e-mailed|mailed|posted|filed|"
        r"submitted|delivered|dropped off|handed|photographed|took photos?|uploaded|"
        r"requested|asked for|called|phoned)\b",
        r"\bi'?ve (?:sent|emailed|mailed|filed|submitted|delivered|photographed|requested)\b",
        r"\b(?:that'?s|it'?s|this is) (?:done|sent|filed|handled|taken care of)\b",
        r"\bi did (?:that|the|it)\b", r"\bdone with\b",
    )

    # ------------------------------------------------------------------
    # Complaint venues, and the clock that keeps running while you use one.
    #
    # The principal asked what the CFPB, the OCC and California's DFPI take -
    # then widened it to every equivalent body, federal and state. So this is a
    # register rather than three hardcoded entries: a venue is added in one
    # call and becomes answerable from that moment.
    #
    # THE FINDING THAT MATTERS IS NOT THE PROCESSING TIME.
    #
    # An administrative complaint does not stop a private limitation period.
    # File with a regulator, wait for it to work through the queue, and the
    # one-year FDCPA and TILA clocks run out while the file is open - the
    # complaint was live the whole time and the right to sue quietly expired.
    # Every venue answer therefore carries the periods that are still running,
    # read from the corpus rather than recalled.
    #
    # WHAT THIS REFUSES: a processing timeframe is agency practice, not law.
    # It is recorded as `agency_policy` and never presented as a period this
    # agent verified - the same rule as add_deadline, which will not compute a
    # period whose authority it cannot open. Where a venue's statutory basis IS
    # openable, that is checked by looking, not by asserting.

    VENUE_LEVELS = ("federal", "state", "self_regulatory")

    def add_venue(self, args):
        """Record a complaint forum: who it covers and what it cannot do."""
        a = args if isinstance(args, dict) else {}
        name = str(a.get("name") or "").strip()
        level = str(a.get("level") or "").strip().lower()
        if not name:
            return {"error": "A venue needs a name."}
        if level not in self.VENUE_LEVELS:
            return {"error": f"level must be one of {list(self.VENUE_LEVELS)}"}
        if not str(a.get("covers") or "").strip():
            return {"error": ("A venue needs `covers` - who it has jurisdiction over. A "
                              "forum recorded without that is a name, and complaining to "
                              "the wrong regulator costs the time it takes to be told so.")}

        # Statutory basis is verified by LOOKING, never by the caller saying so.
        basis = []
        for cite in (a.get("statutory_basis") or []):
            found = self.lookup_reference(str(cite))
            basis.append({"citation": str(cite), "in_corpus": bool(found),
                          "work": (found[0] or {}).get("title") if found else None})

        rec = {
            "id": f"venue_{self._uid()}",
            "name": name,
            "level": level,
            "jurisdiction": a.get("jurisdiction") or ("United States" if level == "federal" else None),
            "covers": str(a["covers"]).strip(),
            "statutory_basis": basis,
            "authority_in_corpus": any(b["in_corpus"] for b in basis),
            # Agency practice. Recorded as what it is.
            "processing": {"stated": a.get("processing"),
                           "class": "agency_policy",
                           "verified_against_authority": False,
                           "note": ("Agency practice, not a statutory period. Nothing here "
                                    "verified it; treat it as what the body says it does.")}
                          if a.get("processing") else None,
            "filing_deadline": a.get("filing_deadline"),
            # STATE FIRST, THEN FEDERAL - the principal's sequence, recorded on
            # the venue rather than left in someone's head. State regulators
            # licence the entity directly and a documented state record is what
            # a federal complaint escalates FROM. Nothing here is a legal
            # exhaustion requirement and it is not presented as one: it is an
            # order of operations, and the cost of that order is time, which is
            # why complaint_path prices it against the clocks still running.
            "escalation_order": int(a.get("escalation_order")
                                    or (1 if level == "state" else 2)),
            "tolls_private_limitations": a.get("tolls_private_limitations", False),
            "how": a.get("how"),
            "source": a.get("source") or "unknown",
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
        }
        if rec["filing_deadline"] and not rec["authority_in_corpus"]:
            rec["filing_deadline_caveat"] = (
                "A filing deadline is stated but no authority for it is open in this corpus, "
                "so it is recorded as reported and must not be relied on as computed.")
        try:
            raw = self._unwrap_value(self.retrieve_own_memory("venue_index"))
            idx = json.loads(raw) if raw else []
        except Exception:
            idx = []
        self.store_own_memory(rec["id"], json.dumps(rec), pin=True)
        idx.append(rec["id"])
        self.store_own_memory("venue_index", json.dumps(idx))
        return rec

    # The private clocks a complaint does not stop. Each is answered from the
    # corpus at call time - if a section is not openable, it is reported as a
    # gap rather than recited.
    _PRIVATE_CLOCKS = (
        ("15 U.S.C. 1681p", "1681p", "FCRA - credit reporting"),
        ("15 U.S.C. 1640(e)", "1640", "TILA - truth in lending"),
        ("15 U.S.C. 1692k(d)", "1692k", "FDCPA - debt collection"),
        ("15 U.S.C. 1691e(f)", "1691e", "ECOA - credit discrimination"),
        ("42 U.S.C. 3613(a)", "3613", "FHA - housing discrimination"),
    )

    def running_clocks(self):
        """Limitation periods that keep running while a complaint sits open."""
        out = []
        for cite, key, label in self._PRIVATE_CLOCKS:
            found = self.lookup_reference(key)
            if not found:
                out.append({"citation": cite, "subject": label, "in_corpus": False,
                            "period": None,
                            "note": "Not openable here, so no period is stated for it."})
                continue
            text = re.sub(r"\s+", " ", str((found[0] or {}).get("text") or ""))
            m = re.search(r"(?:not later than|within)\s+(?:the\s+earlier\s+of\s*[—-]?\s*)?"
                          r"[^.]{0,320}?\byears?\b[^.]{0,160}", text, re.I)
            out.append({"citation": cite, "subject": label, "in_corpus": True,
                        "period": re.sub(r"\s+", " ", m.group(0)).strip()[:300] if m else None,
                        "note": None if m else ("Section is open but no period phrase was "
                                                "isolated; read it directly.")})
        return out

    def complaint_path(self, jurisdiction=None, subject=None):
        """The order to complain in, and what it costs in time.

        State before federal is the principal's strategy, so this returns a
        SEQUENCE rather than a list. The part worth reading is the last
        section: an escalation ladder spends days, and the private limitation
        periods do not pause while it is climbed. A path that would consume a
        clock is named as such rather than left for the person to notice.
        """
        v = self.venues(jurisdiction=jurisdiction) if jurisdiction else self.venues()
        vs = v["venues"]
        if jurisdiction:
            vs = [x for x in vs if x.get("level") == "federal"
                  or str(x.get("jurisdiction") or "").lower() == str(jurisdiction).lower()]
        if subject:
            sub = str(subject).lower()
            narrowed = [x for x in vs if sub in json.dumps(x).lower()]
            if narrowed:
                vs = narrowed
        vs.sort(key=lambda x: (x.get("escalation_order") or 9, x.get("name") or ""))

        steps, cumulative_note = [], []
        for i, x in enumerate(vs, 1):
            proc = (x.get("processing") or {}).get("stated")
            steps.append({
                "step": i,
                "venue": x.get("name"),
                "level": x.get("level"),
                "jurisdiction": x.get("jurisdiction"),
                "covers": x.get("covers"),
                "how": x.get("how"),
                "typical_time": proc,
                "time_is": ("agency practice, not a verified period" if proc
                            else "not recorded - ask the body directly"),
                "carry_forward": ("The record this produces is what the next step "
                                  "escalates from." if x.get("level") == "state" else None),
            })
            if proc:
                cumulative_note.append(f"{x.get('name')}: {proc}")

        clocks = self.running_clocks()
        return {
            "jurisdiction": jurisdiction, "subject": subject,
            "sequence": steps, "steps": len(steps),
            "order_rationale": (
                "State first, then federal. A state regulator licences the entity directly "
                "and its file is what a federal complaint escalates from. This is an order "
                "of operations, NOT a legal exhaustion requirement - no statute here "
                "conditions a federal complaint on a state one, and none is claimed to."),
            "what_it_costs": (cumulative_note or
                              ["No processing times recorded, so the ladder cannot be priced."]),
            "clocks_that_do_not_pause": clocks,
            "the_risk_in_this_order": (
                "Every day spent climbing the ladder is a day off the court deadline. A "
                "one-year FDCPA or TILA period can expire with the agency file still open, "
                "and the complaint being live is not a defence to the limitation. Open the "
                "court deadline in the deadline register on the day the conduct occurred, "
                "not on the day the agency answers."),
        }

    def venues(self, level=None, jurisdiction=None):
        """The register. Every answer carries the clocks it does not stop."""
        try:
            raw = self._unwrap_value(self.retrieve_own_memory("venue_index"))
            idx = json.loads(raw) if raw else []
        except Exception:
            idx = []
        out = []
        for vid in idx:
            r = self._unwrap_value(self.retrieve_own_memory(vid))
            if not r:
                continue
            try:
                v = json.loads(r)
            except Exception:
                continue
            if level and v.get("level") != level:
                continue
            if jurisdiction and str(v.get("jurisdiction") or "").lower() != str(jurisdiction).lower():
                continue
            out.append(v)
        out.sort(key=lambda v: (v.get("level") != "federal", v.get("jurisdiction") or "",
                                v.get("name") or ""))
        states = sorted({v.get("jurisdiction") for v in out
                         if v.get("level") == "state" and v.get("jurisdiction")})
        return {
            "count": len(out), "venues": out,
            "states_held": states,
            "coverage": (f"{len(states)} state jurisdiction(s) recorded. The rest are absent "
                         f"by design, not by failure - this register is filled on demand the "
                         f"way the corpus is, and a state agency named from memory would be "
                         f"exactly the recalled fact this system refuses elsewhere. Add one "
                         f"with add_venue."),
            "clocks_that_keep_running": self.running_clocks(),
            "the_thing_to_understand": (
                "Filing with a regulator does not stop a private limitation period. A "
                "complaint can sit open with an agency while the right to sue on the same "
                "facts expires. Track the court deadline separately, in the deadline "
                "register, from the day the conduct occurred."),
        }

    def routing_terms(self):
        """Declared vocabulary, plus the words of the matters actually open.

        The learned words go into `terms`, which are COUNTED against other
        agents, and never into `owns`, which is DEFINITIVE and stops the
        router. The first version put them in `owns` and Legal immediately
        claimed "pest control" and "damages entry" off its own action list -
        words that belong to Accounting, which owns what a charge is. An agent
        that learns a word from its own paperwork has a claim on it, not a
        certainty, and the difference is exactly what the two tiers are for.
        """
        base = super().routing_terms()
        base["terms"] = list(base.get("terms") or []) + self._matter_vocabulary()
        return base

    def _matter_vocabulary(self):
        """Two-word phrases taken from the open action register.

        Grow adds the names of the plants it is tracking, so registering a
        plant makes questions about it route correctly from that moment with
        no edit anywhere. The same reasoning applies here and was missing:
        "I emailed the repair notice today" named an item sitting in this
        agent's own action register and reached no agent's vocabulary, so
        Boss guessed - and guessed differently on different runs, because a
        fallback is not a routing decision.

        Only distinctive words are taken. A word already claimed generically,
        or too short to mean anything, would pull unrelated requests in, which
        is the opposite failure and harder to see.
        """
        terms = []
        generic = {"notice", "request", "record", "records", "date", "dated",
                   "send", "sent", "give", "given", "file", "filed", "form", "copy",
                   "case", "cases", "open", "close", "closed", "item",
                   "items", "step", "steps", "thing", "things", "with", "from",
                   "that", "this", "what", "when", "where", "which", "their", "your"}
        try:
            for a in (self.actions().get("actions") or []):
                phrase = str(a.get("what") or "")
                # Two-word phrases from the item's own wording: distinctive
                # enough to claim, short enough to say.
                words = [w for w in re.findall(r"[a-z]{4,}", phrase.lower())
                         if w not in generic]
                for i in range(len(words) - 1):
                    pair = f"{words[i]} {words[i + 1]}"
                    if pair not in terms:
                        terms.append(re.escape(pair))
                if a.get("forum") and len(str(a["forum"])) > 3:
                    f = re.escape(str(a["forum"]).lower())
                    if f not in terms:
                        terms.append(f)
        except Exception as exc:
            self.log(f"owns_terms: could not read the action register: {exc}")
        return terms

    def _record_reported_step(self, text):
        """Attach a reported step to the open action it names, or say it could
        not tell which. Never closes anything."""
        acts = self.actions().get("actions") or []
        if not acts:
            return {"answered_as": "reported_step_no_register",
                    "text": ("Noted, but there are no open actions recorded, so there is "
                             "nothing here for it to attach to. Nothing was written."),
                    "facts": {"matched": None}}
        low = text.lower()
        words = set(re.findall(r"[a-z]{4,}", low)) - {
            "have", "just", "already", "sent", "emailed", "mailed", "filed", "with",
            "them", "that", "this", "today", "yesterday", "morning", "afternoon",
            "night", "week", "been", "about", "from", "over", "done", "took"}
        best, score = None, 0
        for a in acts:
            hay = f"{a.get('what','')} {a.get('forum','')} {a.get('why','')}".lower()
            n = sum(1 for w in words if w in hay)
            if n > score:
                best, score = a, n
        if not best or score < 2:
            return {"answered_as": "reported_step_unmatched",
                    "text": ("I could not tell which open item that refers to, so I have "
                             "written nothing rather than attach it to the wrong one. The "
                             "open items are:\n"
                             + "\n".join(f"  - {a.get('what')}" for a in acts)
                             + "\nSay it again naming one of those."),
                    "facts": {"matched": None, "open": len(acts)}}

        stamp = datetime.now().strftime("%Y-%m-%d")
        prior = best.get("note") or ""
        self.amend_action({
            "action_id": best["id"],
            "reason": "Step reported by the principal in conversation.",
            "note": (prior + ("\n" if prior else "")
                     + f"[{stamp}] Principal reports: “{text.strip()}” "
                       f"Recorded as reported, not as evidenced.")})
        raw = self._unwrap_value(self.retrieve_own_memory(best["id"]))
        try:
            rec = json.loads(raw)
        except Exception:
            rec = best
        rec["status"] = "in_progress"
        self.store_own_memory(best["id"], json.dumps(rec), pin=True)

        alts = best.get("evidence_alternatives") or []
        closes = ("\n" + "\n".join(f"  - {x}" for x in alts)) if alts else \
                 f" {best.get('evidence_expected')}"
        return {"answered_as": "reported_step_recorded",
                "text": (f"Recorded against “{best.get('what')}”, dated {stamp}, "
                         f"as REPORTED rather than evidenced - so it is now in progress, not "
                         f"done. What closes it, any one:{closes}\n"
                         f"Tell me the receipt number or the confirmation and I will close it."),
                "facts": {"matched": best["id"], "status": "in_progress",
                          "closed": False, "reported_on": stamp}}

    def _matter_state(self, prompt=""):
        """What is outstanding and what periods are running, in one read.

        Narrowed two ways when the question is narrow: by shape (a deadline
        question leads with periods) and by subject (a word in the question
        that appears in an item's own text pulls that item to the front and
        drops the rest)."""
        acts = (self.actions().get("actions") or [])
        dls = (self.deadlines().get("deadlines") or [])
        text = (prompt or "").lower()

        focus = "deadlines" if any(re.search(p, text) for p in self._DEADLINE_FOCUS) else "actions"

        # Subject narrowing, with a guard that had to be added immediately:
        # "what do I need to do for my HOUSING case" matched "housing" and
        # returned 1 of 4 items. The broadest question got the narrowest
        # answer, which is worse than not narrowing at all - a partial answer
        # that looks complete is the failure this system is built against.
        # So a question that asks for everything is never narrowed, and a
        # word that matches most of the register is not a subject.
        subject = None
        broad = any(re.search(p, text) for p in (
            r"\bwhat (?:do|should) i (?:need to |have to )?do\b",
            r"\bwhat(?:'s| is) (?:outstanding|left|pending|open)\b",
            r"\bnext steps?\b", r"\beverything\b", r"\ball (?:of )?(?:it|my|the)\b",
            r"\bto[- ]?do\b", r"\baction items?\b", r"\bwhere (?:are we|do (?:i|we) stand)\b",
            r"\bwhat(?:'s| is) the (?:status|state|posture)\b"))
        total = len(acts) + len(dls)
        words = [w for w in re.findall(r"[a-z]{4,}", text)
                 if w not in ("what", "when", "have", "need", "should", "long", "with",
                              "does", "much", "time", "left", "this", "that", "case",
                              "there", "about", "from", "them", "they", "will", "into",
                              "your", "mine", "days", "week", "must", "want", "know")]
        if not broad:
            for w in words:
                hit_a = [a for a in acts if w in json.dumps(a).lower()]
                hit_d = [d for d in dls if w in json.dumps(d).lower()]
                n = len(hit_a) + len(hit_d)
                # A word matching half the register or more is describing the
                # matter, not naming a subject within it.
                if n and n * 2 < total:
                    acts, dls, subject = hit_a, hit_d, w
                    break

        return {"actions": acts, "deadlines": dls, "focus": focus, "subject": subject,
                "hidden": (total - len(acts) - len(dls)) if subject else 0,
                "blocked": [a.get("what") for a in acts if a.get("status") == "blocked"]}

    _CITATION = re.compile(
        r"\b\d+\s*(?:u\.?s\.?c\.?|c\.?f\.?r\.?)\s*(?:§+\s*)?[\d.\-()a-z]+",
        re.I)

    # A DECIDED CASE IS THIS AGENT'S LAB RESULT.
    #
    # The grower drew the parallel: "a published class action or case law with
    # an issued judgment from a trial, a judge making a judgment on the case,
    # settling in the benefit of either party or dismissal, or even if it comes
    # up in the appellate court because the judge didn't do its job - those are
    # akin to things happening in the laboratory for a grow agent."
    #
    # Exactly the structure Grow now has. A statute is `authority`: what the law
    # says. A treatise or a law-review article is `expert_commentary`: someone
    # writing ABOUT the law. A decided case is the experiment actually run - the
    # theory put in front of a tribunal to see what happens.
    #
    # But a disposition is not one thing, and collapsing them is how a
    # settlement gets cited as though it were a holding. What a court DID
    # determines what the outcome establishes, and these differ enormously:
    DISPOSITIONS = {
        "judgment_on_merits": {
            "reaches_merits": True, "weight": "high",
            "establishes": "a court decided the legal question on this record"},
        "summary_judgment": {
            "reaches_merits": True, "weight": "high",
            "establishes": "decided on facts the parties did not dispute"},
        "dismissal_merits": {
            "reaches_merits": True, "weight": "medium",
            "establishes": "the claim as pleaded did not state one - a ruling about the "
                           "pleading, not about what happened"},
        "dismissal_procedural": {
            "reaches_merits": False, "weight": "low",
            "establishes": "NOTHING about the merits. Standing, jurisdiction or timeliness "
                           "ended it before the question was reached"},
        "settlement": {
            "reaches_merits": False, "weight": "low",
            "establishes": "NOTHING about the law. The parties agreed; no court held "
                           "anything. A settlement cited as a holding is the commonest "
                           "misuse of a docket"},
        "default_judgment": {
            "reaches_merits": False, "weight": "low",
            "establishes": "that one side did not appear. The legal theory was never tested"},
        "consent_decree": {
            "reaches_merits": False, "weight": "low-medium",
            "establishes": "terms a court entered by agreement - enforceable between the "
                           "parties, not a ruling on the law"},
        "appellate_affirmed": {
            "reaches_merits": True, "weight": "high",
            "establishes": "a reviewing court let the result stand"},
        "appellate_reversed": {
            "reaches_merits": True, "weight": "high",
            "establishes": "the lower result was WRONG. It supersedes rather than joins - "
                           "the experiment was re-run and came out differently"},
        "appellate_vacated": {
            "reaches_merits": False, "weight": "medium",
            "establishes": "the lower result is undone. Often no ruling replaces it"},
        "pending": {
            "reaches_merits": False, "weight": "none",
            "establishes": "nothing yet. A filed complaint is an allegation"},
    }

    # Whether it can be cited. An unpublished disposition is a real outcome and
    # usually not precedent, and treating the two alike overstates what a case
    # is worth to anyone but its own parties.
    PRECEDENTIAL = ("published", "unpublished", "per_curiam", "unknown")

    def triage_source(self, text=None, source_class="unknown", note=None):
        """Sort source material into what is worth ingesting and what is not.

        The principal's actual need, stated plainly: *"all I'm doing is looking
        for things that my agents can ingest - help me discern correct
        information from wrong information, and maintain a relationship of
        what's operable because it's provable by governing statutes."*

        So this does not grade the prose. It pulls the CITATIONS out and reports,
        for each, whether this agent can already open it, whether it is
        acquirable, and what class of authority it is. A claim is worth keeping
        to the exact degree it points at something checkable - which is why an
        AI infographic and a law review article get the same treatment here.

        Three outcomes per citation, and the middle one is the productive one:

          held      - already in the corpus. Testable right now.
          acquire   - a real citation this agent does not hold. Worth fetching;
                      the source EARNED its keep by naming it.
          unparsed  - looks like a citation and could not be resolved. Reported,
                      never guessed at.

        And prose with no citation at all is reported as exactly that. A
        confident paragraph naming no authority is not a lead; it is an opinion
        with formatting."""
        raw = str(text or "")
        if not raw.strip():
            return {"error": "Pass the text of the source."}

        # Citation shapes this corpus can actually act on.
        PATTERNS = [
            (r"\b(\d{1,2})\s*U\.?\s*S\.?\s*C\.?\s*(?:\u00a7+\s*)?([\w.\-]+)", "usc"),
            (r"\b(\d{1,2})\s*C\.?\s*F\.?\s*R\.?\s*(?:part\s*)?(?:\u00a7+\s*)?([\w.\-]+)", "cfr"),
            (r"\bA\.?R\.?S\.?\s*(?:\u00a7+\s*)?([\w.\-]+)", "ars"),
            (r"\bU\.?C\.?C\.?\s*(?:\u00a7+\s*)?([\d\-.]+)", "ucc"),
            (r"\bRestatement\s*\((?:Second|Third)\)[^,;.]{0,40}", "restatement"),
            (r"\b([A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+)*)\s+v\.\s+"
             r"([A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+)*)", "case"),
        ]
        found, seen = [], set()
        for pat, kind in PATTERNS:
            for m in re.finditer(pat, raw, re.I if kind != "case" else 0):
                cite = re.sub(r"\s+", " ", m.group(0)).strip().rstrip(".,;")
                if cite.lower() in seen or len(cite) < 4:
                    continue
                seen.add(cite.lower())
                found.append({"citation": cite, "kind": kind})

        jur = None
        try:
            jur = (self.handle_task("get_operating_jurisdiction", {}, self.agent_id) or {})
            jur = (jur.get("operating_jurisdiction") or {}) if isinstance(jur, dict) else {}
        except Exception:
            jur = {}

        held, acquire, unparsed, foreign = [], [], [], []
        for f in found:
            kind, cite = f["kind"], f["citation"]
            # A state code from a state this principal does not operate in is
            # not "wrong law" - it is law about somebody else, and saying so is
            # more useful than calling it false.
            if kind == "ars":
                foreign.append({**f, "jurisdiction": "AZ",
                                "operating": jur.get("business"),
                                "why": ("Arizona Revised Statutes. The operating jurisdiction "
                                        f"on record is {jur.get('business')}. Find the local "
                                        f"equivalent rather than citing this one.")})
                continue
            if kind in ("restatement", "case"):
                unparsed.append({**f, "why": ("Not fetchable by tools/ingest_law.py. A "
                                              "Restatement is a treatise and a case needs "
                                              "CourtListener - both are secondary or "
                                              "case-outcome material, not statute.")})
                continue
            hits = self.lookup_reference(cite)
            if hits:
                # A MENTION IS NOT A HOLDING.
                #
                # "28 U.S.C. 1746" resolved against the Federal Rules of Civil
                # Procedure, because the FRCP text CITES 1746 and the index
                # keyed that mention as a section. The passage is real and it is
                # not the statute - reporting it as held would tell the
                # principal they can open a section they do not have.
                #
                # So the matched work has to be the right KIND of work for the
                # citation. A U.S.C. cite answered by a rules volume is a
                # cross-reference: useful, and a different thing.
                work = str((hits[0] or {}).get("title") or "")
                klass = (hits[0] or {}).get("authority_class")
                wl = work.lower()
                right_work = (("u.s.c" in wl or "u. s. c" in wl) if kind == "usc"
                              else ("cfr" in wl) if kind == "cfr" else True)
                entry = {**f, "authority_class": klass, "work": work}
                if right_work:
                    held.append({**entry, "in_corpus": True})
                else:
                    acquire.append({**entry, "in_corpus": False,
                                    "found_as": "cross-reference only",
                                    "why": (f"'{cite}' appears inside {work}, which cites it. "
                                            f"The section itself is not held."),
                                    "how": (f"tools/ingest_law.py usc-section --title "
                                            f"{cite.split()[0]} --section <sec> "
                                            f"--agent legal_agent" if kind == "usc" else
                                            f"tools/ingest_law.py cfr --title "
                                            f"{cite.split()[0]} --part <part> "
                                            f"--agent legal_agent")})
            else:
                title = cite.split()[0] if cite.split() else ""
                acquire.append({**f, "in_corpus": False,
                                "how": (f"tools/ingest_law.py usc-section --title {title} "
                                        f"--section <sec> --agent legal_agent"
                                        if kind == "usc" else
                                        f"tools/ingest_law.py cfr --title {title} "
                                        f"--part <part> --agent legal_agent")})

        return {
            "source_class": source_class,
            "note": note,
            "citations_found": len(found),
            "held": held,
            "acquire": acquire,
            "wrong_jurisdiction": foreign,
            "not_fetchable": unparsed,
            "verdict": ("no citations - opinion with formatting, nothing to ingest"
                        if not found else
                        f"{len(held)} testable now, {len(acquire)} worth acquiring, "
                        f"{len(foreign)} for another state, {len(unparsed)} not fetchable"),
            "principle": ("A source earns its keep by naming something checkable. Its prose "
                          "is never evidence and its citations always are - verify the "
                          "pointer, not the paragraph."),
        }

    def record_case_outcome(self, citation=None, disposition=None, court=None,
                            precedential="unknown", holding=None, in_favor_of=None,
                            supersedes=None, docket=None, notes=None):
        """Record what a court actually DID, with what that establishes.

        Legal's equivalent of an instrumented result. The corpus is the floor -
        what the law says - and this is the lived column: what happened when the
        theory met a tribunal."""
        if not citation or not disposition:
            return {"error": "Needs citation and disposition. Disposition must be one of: "
                             + ", ".join(sorted(self.DISPOSITIONS)),
                    "dispositions": {k: v["establishes"]
                                     for k, v in self.DISPOSITIONS.items()}}
        d = str(disposition).strip().lower()
        if d not in self.DISPOSITIONS:
            return {"error": f"Unknown disposition '{disposition}'.",
                    "accepted": sorted(self.DISPOSITIONS)}
        prec = str(precedential or "unknown").strip().lower()
        if prec not in self.PRECEDENTIAL:
            return {"error": f"precedential must be one of: {', '.join(self.PRECEDENTIAL)}"}
        profile = self.DISPOSITIONS[d]

        # A holding is only meaningful where the court reached the merits.
        # Recording one on a settlement is how a settlement becomes a "holding"
        # three citations later.
        if holding and not profile["reaches_merits"]:
            return {"error": (f"A '{d}' does not reach the merits, so it has no holding. "
                              f"It establishes: {profile['establishes']}. Put what happened "
                              f"in `notes` instead - a holding recorded here would be cited "
                              f"as one later."),
                    "reaches_merits": False}

        rec = {
            "id": f"case_outcome_{self._uid()}" if hasattr(self, "_uid")
                  else f"case_outcome_{int(time.time()*1000000)}",
            "citation": citation, "court": court, "docket": docket,
            "disposition": d,
            "reaches_merits": profile["reaches_merits"],
            "weight": profile["weight"],
            "establishes": profile["establishes"],
            "precedential": prec,
            "holding": holding if profile["reaches_merits"] else None,
            "in_favor_of": in_favor_of,
            "notes": notes,
            "source_class": "case_outcome",
            "evidence_kind": "observed",
            "evidence_note": ("A court did this. It is an outcome, not a claim about the "
                              "law - which is what makes it the lived column rather than "
                              "the corpus."),
            "supersedes": supersedes,
            "recorded": datetime.now().isoformat(timespec="seconds"),
        }
        if prec == "unpublished":
            rec["citation_caveat"] = ("Unpublished. Usually not citable as precedent - a "
                                      "real outcome that binds its own parties and little "
                                      "else. Check the circuit's rule before relying on it.")
        if d == "appellate_reversed" and not supersedes:
            rec["warning"] = ("A reversal supersedes a lower result. Pass `supersedes` with "
                              "that outcome's id so the record shows one question answered "
                              "twice, rather than two independent cases agreeing.")

        key = f"case_outcome_{rec['id']}"
        try:
            idx_raw = self._unwrap_value(self.retrieve_own_memory("case_outcome_index"))
            idx = json.loads(idx_raw) if idx_raw else []
        except Exception:
            idx = []
        self.store_own_memory(rec["id"], json.dumps(rec), pin=True)
        idx.append(rec["id"])
        self.store_own_memory("case_outcome_index", json.dumps(idx))
        if supersedes:
            try:
                raw = self._unwrap_value(self.retrieve_own_memory(supersedes))
                if raw:
                    prior = json.loads(raw)
                    prior["superseded_by"] = rec["id"]
                    prior["superseded_note"] = ("Kept, not deleted - the record should show a "
                                                "question answered twice, and which answer "
                                                "stood.")
                    self.store_own_memory(supersedes, json.dumps(prior))
                    rec["superseded_prior"] = supersedes
            except Exception as e:
                rec["supersede_error"] = str(e)
        return rec

    def describe(self, task, payload):
        """Put this agent's own results into words.

        `answer()` has five branches and two of them - `citation_lookup` and
        `definition` - delegated their wording to `describe`, which this agent
        never implemented. The base returns None, so those two computed the
        right passage and handed back empty text, and Boss reported the
        capability as MISSING. The same fault Grow's flowering answer had hours
        earlier: a capability that exists, runs, and says nothing.

        The other three branches built their text inline, which is why "what is
        legal tender" worked and "what does 15 USC 1681i require" did not."""
        p = payload if isinstance(payload, dict) else {}

        if task == "matter_state":
            acts = p.get("actions") or []
            dls = p.get("deadlines") or []
            if not acts and not dls:
                return ("Nothing is recorded as outstanding and no periods are running. "
                        "That is a statement about the register, not about the matter - "
                        "if a step has not been written down, this cannot see it.")
            lines = []
            # Say that it narrowed, and by how much. A partial list that
            # does not announce itself as partial is the same error as a
            # check that found nothing and reported health.
            if p.get("subject") and p.get("hidden"):
                lines.append(f"Showing only what mentions \u201c{p['subject']}\u201d - "
                             f"{p['hidden']} other item(s) not shown. Ask what needs doing "
                             f"to see everything.")
            if p.get("focus") == "deadlines" and dls:
                lines.append("Periods running:")
                for d in dls:
                    left = d.get("days_remaining")
                    when = ("PASSED" if d.get("status") == "PASSED"
                            else f"{left} days left" if isinstance(left, int) else d.get("due"))
                    lines.append(f"  - {d.get('name')}: {when}, due {d.get('due')} "
                                 f"({d.get('citation')}).")
                    if d.get("consequence"):
                        lines.append(f"    If it passes: {d['consequence']}")
                lines.append("Computed from authority open in the corpus, not recalled.")
                if acts:
                    lines.append("")
                    lines.append(f"{len(acts)} action{'s' if len(acts) != 1 else ''} also open"
                                 + (f", soonest: {acts[0].get('what')}." if acts else "."))
                return "\n".join(lines)
            if acts:
                lines.append(f"{len(acts)} thing{'s' if len(acts) != 1 else ''} still to do"
                             + (", soonest first:" if len(acts) > 1 else ":"))
                for a in acts:
                    d = a.get("days_remaining")
                    when = ("overdue by %d days" % abs(d)) if isinstance(d, int) and d < 0 \
                        else "due today" if d == 0 \
                        else ("%d days left" % d) if isinstance(d, int) \
                        else (a.get("due") or "no date")
                    lines.append(f"  - {a.get('what')} ({when}).")
                    # What closes the item is the part worth saying out loud. A
                    # step somebody believes they did, and a step they can show
                    # they did, are different states of the world.
                    alts = a.get("evidence_alternatives")
                    if a.get("evidence_ref"):
                        lines.append(f"    Proof on file: {a['evidence_ref']}.")
                    elif alts:
                        lines.append(f"    Any one of these closes it: {alts[0]}"
                                     + (f" - or {len(alts) - 1} other accepted "
                                        f"method{'s' if len(alts) > 2 else ''}."
                                        if len(alts) > 1 else "."))
                    elif a.get("evidence_expected"):
                        lines.append(f"    Closes on: {a['evidence_expected']}.")
                    if a.get("blocked_by"):
                        lines.append(f"    Blocked by {a['blocked_by']}.")
            if dls:
                lines.append("")
                lines.append("Periods running:")
                for d in dls:
                    left = d.get("days_remaining")
                    when = ("PASSED" if d.get("status") == "PASSED"
                            else f"{left} days left" if isinstance(left, int) else d.get("due"))
                    lines.append(f"  - {d.get('name')}: {when} ({d.get('citation')}).")
                lines.append("Each was computed from an authority that was open in the "
                             "corpus at the time. None was recalled.")
            return "\n".join(lines)

        if task == "citation_lookup":
            secs = p.get("sections") or []
            if not secs:
                return ""
            out = []
            for sec in secs[:2]:
                cite = str(sec.get("citation") or "").strip()
                body = re.sub(r"\s+", " ", str(sec.get("text") or "")).strip()
                src = str(sec.get("source") or "")
                out.append(f"{p.get('citation') or cite}\n\n{body}"
                           + (f"\n\n({src})" if src else ""))
            if len(secs) > 2:
                out.append(f"({len(secs) - 2} further section(s) match this citation.)")
            return "\n\n".join(out)

        if task == "definition":
            entry = p.get("entry")
            term = p.get("term")
            if isinstance(entry, dict):
                body = entry.get("definition") or entry.get("text") or ""
                if isinstance(body, dict):
                    body = body.get("definition") or body.get("text") or ""
            else:
                body = str(entry or "")
            body = re.sub(r"\s+", " ", str(body)).strip()
            if not body:
                return ""
            return (f"{term}: {body}"
                    f"\n\n(Corpus headword. Black's 2nd edition is a floor, not a boundary - "
                    f"where statute, regulation or case law disagree with it, they win.)")
        return None

    def answer(self, prompt):
        """Pick this agent's own capability for a legal question.

        Boss holds no legal vocabulary and must not; this is where a plain
        question becomes a corpus lookup. Returns None for anything outside the
        domain, so Boss falls through rather than receiving a confident
        non-answer.

        The rule that matters most here is what it REFUSES. `lookup_term` falls
        back to the first word of a phrase, so "legal tender" returned the
        dictionary entry for "Legal" - *conforming to the law* - which is a
        real definition of a different thing, presented as though it answered.
        A term of art is not the sum of its words. For a multi-word term this
        takes the exact headword or reports that it holds nothing, and a
        one-word gloss standing in for a phrase is treated as no answer at
        all."""
        text = (prompt or "").strip()
        if not text:
            return None

        cite = self._CITATION.search(text)
        if cite:
            found = self.lookup_reference(cite.group(0))
            if found:
                return {"answered_as": "citation_lookup",
                        "text": self.describe("citation_lookup", {"citation": cite.group(0),
                                                                  "sections": found}),
                        "facts": {"citation": cite.group(0), "sections": found[:3]}}
            return {"answered_as": "citation_not_held",
                    "text": (f"{cite.group(0)} is not in this agent's corpus. The corpus is "
                             f"fetch-on-demand rather than a mirror, so an uncited title is "
                             f"absent by design, not by failure. It can be acquired with "
                             f"tools/ingest_law.py."),
                    "facts": {"citation": cite.group(0), "in_corpus": False}}

        if any(re.search(p, text.lower()) for p in self._DID_IT_ASK):
            return self._record_reported_step(text)

        if any(re.search(p, text.lower()) for p in self._MATTER_STATE_ASK):
            st = self._matter_state(text)
            return {"answered_as": "matter_state",
                    "text": self.describe("matter_state", st),
                    "facts": {"open_actions": len(st["actions"]),
                              "deadlines": len(st["deadlines"]),
                              "blocked": st["blocked"]}}

        if not any(re.search(p, text.lower()) for p in self._DEFINITION_ASK):
            return None

        term = re.sub(r"^(what (is|are)|define|definition of|meaning of)\s+", "",
                      text.lower()).strip().rstrip("?.").strip()
        term = re.sub(r"^(a|an|the)\s+", "", term)
        if not term:
            return None
        words = term.split()

        exact = self.lookup_term(term, loose=False)
        if exact:
            return {"answered_as": "definition",
                    "text": self.describe("definition", {"term": term, "entry": exact}),
                    "facts": {"term": term, "source": "corpus headword", "entry": exact}}

        # Not a headword. Look for the PHRASE in the text this agent actually
        # holds - that is evidence about the corpus rather than a guess about
        # the term, and it names the authority that uses it.
        hits = self._phrase_in_corpus(term)
        if hits:
            top = hits[0]
            body = top.get("text") or ""
            return {"answered_as": "term_in_authority",
                    "text": (f"{top['work']}, {top['section']}\n\n{body}"
                             + (f"\n\nAlso appears in: "
                                + "; ".join(f"{h['work']} {h['section']}" for h in hits[1:4])
                                if len(hits) > 1 else "")
                             + f"\n\n('{term}' is not a headword in the 1910 dictionary; this "
                               f"is the passage in the corpus that uses it.)"),
                    "facts": {"term": term, "source": "corpus full-text",
                              "authority": True,
                              "work": top["work"], "citation": top["section"],
                              "hits": [{k: v for k, v in h.items() if k != "text"}
                                       for h in hits[:5]]}}

        # THE CORPUS IS THE FLOOR, NOT THE BOUNDARY.
        #
        # Stopping at "not in my corpus" while holding a working web search is
        # the agent declining to use a capability it has. The corpus is
        # authority; the web is DISCOVERY - and the distinction is carried in
        # the `source` field rather than in the phrasing, so nothing downstream
        # can mistake one for the other.
        #
        # The useful part is not the web summary. It is the CITATION inside it:
        # once the web says a term is defined at 31 U.S.C. 5103, that title can
        # be ingested and the question re-asked against real authority. Search
        # finds where the law lives; the corpus is what gets to speak.
        web = None
        try:
            web = self.search_public(f"{term} legal definition statute United States Code")
        except Exception as exc:
            self.log(f"answer: web search failed for '{term}': {exc}")
        wr = (web or {}).get("result")
        if wr and not (isinstance(wr, dict) and wr.get("error")):
            blob = json.dumps(wr) if not isinstance(wr, str) else wr
            cites = []
            for m in self._CITATION.finditer(blob):
                c = re.sub(r"\s+", " ", m.group(0)).strip()
                if c.lower() not in [x.lower() for x in cites]:
                    cites.append(c)
            held = [c for c in cites if self.lookup_reference(c)]
            missing = [c for c in cites if c not in held]
            return {
                "answered_as": "web_discovery",
                "text": (f"'{term}' is not in this agent's corpus. A public search suggests it "
                         f"is governed by: {', '.join(cites[:4]) if cites else 'no clear citation'}. "
                         + (f"Of those, {', '.join(held[:3])} IS held here. "
                            if held else "")
                         + (f"{', '.join(missing[:3])} is not - it can be fetched with "
                            f"tools/ingest_law.py and the question re-asked against the "
                            f"actual text. " if missing else "")
                         + "This paragraph is an unverified web result and is NOT authority; "
                           "it is a pointer to where the authority lives."),
                "facts": {"term": term, "source": "web_unverified",
                          "authority": False,
                          "search_via": (web or {}).get("source"),
                          "citations_found": cites[:6],
                          "citations_held": held, "citations_missing": missing,
                          "next": (f"python3 tools/ingest_law.py usc --title "
                                   f"{missing[0].split()[0]} --agent legal_agent"
                                   if missing and missing[0].split()[0].isdigit() else None)},
            }

        gloss = self.lookup_term(words[0], loose=False) if len(words) > 1 else None
        return {"answered_as": "term_not_held",
                "text": (f"This agent holds no definition of '{term}'."
                         + (f" The dictionary defines '{words[0]}' alone, but a term of art is "
                            f"not the sum of its words and that entry is not an answer to this "
                            f"question - so it is not offered as one."
                            if gloss else "")
                         + " The corpus is fetch-on-demand: whichever title or part defines it "
                           "can be acquired with tools/ingest_law.py and the question re-asked."),
                "facts": {"term": term, "in_corpus": False,
                          "single_word_gloss_available": bool(gloss),
                          "single_word_gloss_refused": bool(gloss)}}

    def _phrase_in_corpus(self, phrase, limit=8):
        """Where this exact phrase appears in the works on the shelf."""
        needle = (phrase or "").strip().lower()
        if len(needle) < 4:
            return []
        out = []
        try:
            idx = self._load_reference_docs()
        except Exception:
            return []
        # The index is by_citation -> [entry], each entry carrying its work,
        # citation and text. Scanning it is a full-text pass over exactly what
        # this agent holds - slow-ish and honest, and only reached when the
        # exact headword has already missed.
        seen = set()
        for entries in (idx.get("by_citation") or {}).values():
            for e in (entries if isinstance(entries, list) else [entries]):
                if not isinstance(e, dict):
                    continue
                if needle in str(e.get("text", "")).lower():
                    key = (e.get("title") or e.get("work"), e.get("citation"))
                    if key in seen:
                        continue
                    seen.add(key)
                    # The index entry keys the work as `title`; reading `work`
                    # returned "?" for every hit. And a pointer is not an
                    # answer to "what is X" - the passage that defines it has
                    # to come back with it.
                    out.append({"work": e.get("title") or e.get("work") or "?",
                                "section": e.get("citation") or e.get("page") or "?",
                                "text": re.sub(r"\s+", " ", str(e.get("text", ""))).strip()})
                    if len(out) >= limit:
                        return out
        return out

    def handle_task(self, task, args, sender):
        self.log(f"Task {task} from {sender}")

        cag_result = self.try_handle_cag_task(task, args)
        if cag_result is not None:
            return cag_result

        if task == "assess_case_elements":
            cid = args.get("case_id") if isinstance(args, dict) else (args[0] if args else None)
            if not cid:
                return {"error": "assess_case_elements needs a case_id", "disclaimer": DISCLAIMER}
            return self.assess_case_elements(cid)

        if task in ("claim_open", "claim_cite", "claim_answer", "claim_set_right",
                    "claim_evidence", "claim_observe", "claim_reproducibility",
                    "claim_corroborate"):
            a = args if isinstance(args, dict) else {}
            if task == "claim_open":            return self.claim_open(a)
            if task == "claim_cite":            return self.claim_cite(a)
            if task == "claim_answer":          return self.claim_answer(a)
            if task == "claim_set_right":       return self.claim_set_right(a)
            if task == "claim_evidence":        return self.claim_record(a, "evidence")
            if task == "claim_observe":         return self.claim_record(a, "observations")
            if task == "claim_reproducibility": return self.claim_reproducibility(a)
            return self.claim_corroborate(a)

        if task == "claim_get":
            a = args if isinstance(args, dict) else {}
            cid = a.get("claim_id") or (args[0] if isinstance(args, list) and args else "")
            return self.claim_get(cid)

        if task == "claim_list":
            ids = self._load_index_key(self.CLAIM_INDEX_KEY)
            out = []
            for cid in ids:
                c = self._load_claim(cid)
                if c:
                    out.append({"claim_id": cid, "statement": c["statement"][:110],
                                "conclusion": c["conclusion"],
                                "confidence": c["confidence"],
                                "asserted_by": c.get("asserted_by")})
            return {"claims": out, "disclaimer": DISCLAIMER}

        if task == "claim_ontology":
            return {"rights": claim_assessment.RIGHTS,
                    "right_states": list(claim_assessment.RIGHT_STATES),
                    "prerequisites": [{"key": k, "question": q}
                                      for k, q in claim_assessment.PREREQUISITES],
                    "conclusions": list(claim_assessment.CONCLUSIONS),
                    "reproducibility": list(claim_assessment.REPRODUCIBILITY),
                    "disclaimer": DISCLAIMER}

        if task == "set_operating_jurisdiction":
            payload = args if isinstance(args, dict) else {}
            if not payload and isinstance(args, list) and args:
                payload = {"residential": args[0]}
            return self.set_operating_jurisdiction(payload)

        if task == "get_operating_jurisdiction":
            rec = self.get_operating_jurisdiction()
            if not rec:
                return {"operating_jurisdiction": None,
                        "note": "Nothing on record. This agent will not assume "
                                "a state - set one with set_operating_jurisdiction.",
                        "disclaimer": DISCLAIMER}
            return {"operating_jurisdiction": rec, "disclaimer": DISCLAIMER}

        if task == "cite_in_jurisdiction":
            a = args if isinstance(args, dict) else {}
            if not a and isinstance(args, list) and args:
                a = {"section": args[0], "state": args[1] if len(args) > 1 else None}
            if not a.get("section"):
                return {"error": "Usage: cite_in_jurisdiction section=9-203 [state=TX]",
                        "disclaimer": DISCLAIMER}
            return self.cite_in_jurisdiction(a.get("section"), a.get("state"),
                                             a.get("role", "transaction_situs"))

        if task == "transaction_layers":
            a = args if isinstance(args, dict) else {}
            if not a and isinstance(args, list) and args:
                a = {"stage": args[0]}
            return self.transaction_layers(a.get("stage"), a.get("state"))

        if task == "triage_source":
            return self.triage_source(**(args if isinstance(args, dict) else {}))

        if task == "open_action":
            return self.open_action(args if isinstance(args, dict) else {})
        if task == "complete_action":
            return self.complete_action(args if isinstance(args, dict) else {})
        if task == "add_venue":
            return self.add_venue(args if isinstance(args, dict) else {})
        if task == "complaint_path":
            a = args if isinstance(args, dict) else {}
            return self.complaint_path(a.get("jurisdiction"), a.get("subject"))
        if task == "venues":
            a = args if isinstance(args, dict) else {}
            return self.venues(a.get("level"), a.get("jurisdiction"))
        if task == "running_clocks":
            return {"clocks": self.running_clocks()}
        if task == "amend_action":
            return self.amend_action(args if isinstance(args, dict) else {})
        if task == "actions":
            a = args if isinstance(args, dict) else {}
            return self.actions(a.get("case_id"), bool(a.get("include_closed")))
        if task == "add_deadline":
            return self.add_deadline(**(args if isinstance(args, dict) else {}))
        if task == "deadlines":
            return self.deadlines(**(args if isinstance(args, dict) else {}))

        if task == "record_case_outcome":
            return self.record_case_outcome(**(args if isinstance(args, dict) else {}))

        if task == "lookup":
            # Positional-only meant a dict payload raised KeyError(0), which
            # surfaced to the caller as the entire message {"error": "0"} - a
            # cryptic number where a usage line belonged, and indistinguishable
            # from a real failure. Both shapes are accepted now.
            if isinstance(args, dict):
                term = (args.get("term") or args.get("query")
                        or args.get("citation") or args.get("prompt") or "")
            elif isinstance(args, (list, tuple)):
                term = args[0] if args else ""
            else:
                term = str(args or "")
            if not str(term).strip():
                return {"error": "Usage: lookup <term_or_citation>, or {term: ...}",
                        "disclaimer": DISCLAIMER}
            # The corpus comes first. It was consulted nowhere in this path:
            # lookup went cache -> web -> model, so a term defined in Black's
            # and a case discussed in a treatise on the shelf both reached the
            # open web before they reached the books this agent owns.
            # Exact headword first, then the corpus, then the loose
            # first-word definition. A doctrine phrase must reach the treatises
            # before it is reduced to a one-word gloss.
            # AUTHORITY LEADS; THE DICTIONARY ANNOTATES.
            #
            # The dictionary was consulted first and the statute attached to it,
            # which inverts what each is for. The principal: *"almost everything
            # it is going to look up is going to be in the statutes, the CFRs,
            # the laws, the codes. The dictionaries are just there to help with
            # the definitions in case further understanding is needed."*
            #
            # Right, and it matters beyond ordering: Black's is the 1910
            # edition, in the corpus because its copyright term expired rather
            # than because it is current. Leading with it meant a term that BOTH
            # a live statute and a 116-year-old dictionary define was answered
            # by the dictionary. `lien`, `trustee`, `custodian` are all in both.
            #
            # So the statute leads and the dictionary rides along as plain
            # English - which is the job it is actually good at, helping the
            # agent reason about what a contract means in a case.
            defined = self.lookup_term(term, loose=False)
            passages = self.lookup_reference(term)
            if passages:
                out = {"term": term, "source": "reference/legal_agent corpus",
                       "results": passages, "disclaimer": DISCLAIMER}
                if defined:
                    out["plain_english"] = defined
                    out["source"] = "reference/legal_agent corpus + dictionary"
                    out["note"] = ("The authority governs; the dictionary entry is "
                                   "attached as plain English only. Black's 2nd "
                                   "edition is 1910 - where it and a live statute "
                                   "disagree, the statute wins.")
                return out
            if defined:
                # Only the dictionary holds it. This is the case the dictionary
                # exists for - a term of art with no statutory definition, like
                # laches - and here it leads properly.
                return {"term": term, "source": "reference/legal_agent dictionary",
                        "definition": defined, "disclaimer": DISCLAIMER,
                        "note": ("No statute or regulation in this corpus defines this "
                                 "term, so the dictionary is the answer rather than a "
                                 "gloss. Black's 2nd edition, 1910 - its absence of a "
                                 "headword is a fact about that edition and nothing else.")}
            loose_def = self.lookup_term(term, loose=True)
            if loose_def:
                return {"term": term, "source": "reference/legal_agent dictionary (nearest headword)",
                        "definition": loose_def, "disclaimer": DISCLAIMER}
            hits = self.query_cache(term, top_k=3)
            if hits:
                return {"term": term, "source": "cache", "results": hits, "disclaimer": DISCLAIMER}
            web = self.search_public(f"{term} legal definition statute")
            web_result = web.get("result") if isinstance(web, dict) else None
            if web_result and not (isinstance(web_result, dict) and web_result.get("error")):
                return {
                    "term": term,
                    "source": web.get("source", "pqa_agent"),
                    "note": "Public web search result, not verified.",
                    "answer": web_result,
                    "disclaimer": DISCLAIMER
                }
            raw = self._call_inference(f"Briefly define or explain: {term}, without giving legal advice.")
            return {
                "term": term,
                "source": "inference_fallback",
                "note": "Generated by model, not verified.",
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
            if isinstance(args, dict):
                lesson_text = args.get("lesson_text")
                strategy_type = args.get("strategy_type", "general")
                case_id = args.get("case_id", "")
                tags = args.get("tags", "")
                category = args.get("category", "general")
            else:
                lesson_text = args[0] if args else None
                strategy_type = args[1] if len(args) > 1 and args[1] else "general"
                case_id = args[2] if len(args) > 2 and args[2] else ""
                tags = args[3] if len(args) > 3 and args[3] else ""
                category = args[4] if len(args) > 4 and args[4] else "general"
            if not lesson_text:
                return {"error": "Usage: log_lesson {lesson_text, [strategy_type], [case_id], [tags], [category]}", "disclaimer": DISCLAIMER}
            result = self._write_lesson(lesson_text, strategy_type=strategy_type, case_id=case_id, tags=tags, category=category)
            result["disclaimer"] = DISCLAIMER
            return result

        elif task == "query_lessons":
            query = args.get("query") if isinstance(args, dict) else (args[0] if args else None)
            if not query:
                return {"error": "Missing query", "disclaimer": DISCLAIMER}
            top_k = args.get("top_k", 5) if isinstance(args, dict) else (int(args[1]) if len(args) > 1 and args[1] else 5)
            category_filter = args.get("category") if isinstance(args, dict) else None
            results = self.query_cache(query, top_k=top_k, category="lessons_learned")
            if category_filter:
                results = [r for r in results if self._lesson_category(r["id"]) == category_filter]
            return {"query": query, "category_filter": category_filter, "results": results, "disclaimer": DISCLAIMER}

        elif task == "list_lessons":
            category_filter = args.get("category") if isinstance(args, dict) else None
            lessons = [doc for doc in self.cache.values() if doc["category"] == "lessons_learned"] if hasattr(self, "cache") else []
            if category_filter:
                lessons = [d for d in lessons if self._lesson_category(d["id"]) == category_filter]
            lessons.sort(key=lambda d: d["mtime"], reverse=True)
            return {
                "count": len(lessons),
                "category_filter": category_filter,
                "lessons": [
                    {"id": d["id"], "modified": datetime.fromtimestamp(d["mtime"]).isoformat()}
                    for d in lessons
                ],
                "disclaimer": DISCLAIMER,
            }

        elif task == "analyze_case":
            case_text = args.get("case_text") if isinstance(args, dict) else args[0] if args else None
            if not case_text:
                return {"error": "Missing case_text", "disclaimer": DISCLAIMER}
            if isinstance(args, dict) and args.get("file_path"):
                try:
                    with open(args["file_path"], "r") as f:
                        case_text = f.read()
                except Exception as e:
                    return {"error": f"Could not read file: {e}", "disclaimer": DISCLAIMER}
            analysis = self._extract_case(case_text)
            if isinstance(args, dict) and args.get("perspectives"):
                analysis["perspectives"] = self._analyze_case_perspectives(case_text)
            case_id = f"case_{uuid.uuid4().hex[:12]}"
            self.store_own_memory(case_id, json.dumps(analysis))
            self._append_to_case_index(case_id)
            analysis["case_id"] = case_id

            if isinstance(args, dict) and args.get("file_lesson"):
                lesson_body = (
                    f"Source: {analysis.get('court') or 'Court opinion/order'}\n"
                    f"Issue: {', '.join(analysis.get('legal_issues', [])) or 'Not specified'}\n"
                    f"Lesson: {args.get('lesson_text', '')}\n"
                    f"Classification: Observed legal outcome"
                )
                analysis["filed_lesson"] = self._write_lesson(
                    lesson_body, strategy_type="case_study", case_id=case_id,
                    tags=args.get("tags", ""), category=args.get("lesson_category", "outcome")
                )
            return analysis

        elif task == "list_cases":
            return {"case_ids": self._load_case_index(), "disclaimer": DISCLAIMER}

        elif task == "get_case":
            case_id = args.get("case_id") if isinstance(args, dict) else (args[0] if args else None)
            if not case_id:
                return {"error": "Usage: get_case <case_id>", "disclaimer": DISCLAIMER}
            doc = self._load_record(case_id)
            if doc is None:
                return {"error": f"Case {case_id} not found", "disclaimer": DISCLAIMER}
            return doc

        elif task == "open_matter":
            if not isinstance(args, dict):
                return {"error": "Usage: open_matter {parties, jurisdiction, court, requested_outcome, documents, deadlines, procedural_posture}", "disclaimer": DISCLAIMER}
            matter_id = f"matter_{uuid.uuid4().hex[:12]}"
            matter = {
                "id": matter_id,
                "created": datetime.now().isoformat(),
                "status": "open",
                "parties": args.get("parties", []),
                "jurisdiction": args.get("jurisdiction", ""),
                "court": args.get("court", ""),
                "requested_outcome": args.get("requested_outcome", ""),
                "documents": args.get("documents", []),
                "deadlines": args.get("deadlines", []),
                "procedural_posture": args.get("procedural_posture", ""),
                "facts": [],
                "user_interpretation": [],
                "legal_framework": [],
            }
            self.store_own_memory(matter_id, json.dumps(matter))
            self._append_to_index_key("matter_index", matter_id)
            return {"matter": matter, "disclaimer": DISCLAIMER}

        elif task == "map_issues":
            matter_id = args.get("matter_id") if isinstance(args, dict) else None
            if not matter_id:
                return {"error": "Missing matter_id", "disclaimer": DISCLAIMER}
            matter = self._load_record(matter_id)
            if matter is None:
                return {"error": f"Matter {matter_id} not found", "disclaimer": DISCLAIMER}
            # Facts, user interpretation, and legal framework are stored as separate
            # fields and never merged - this is what actually enforces the separation,
            # not just a prompt instruction.
            for field in ("facts", "user_interpretation", "legal_framework"):
                incoming = args.get(field)
                if incoming:
                    existing = matter.get(field, [])
                    existing.extend(incoming if isinstance(incoming, list) else [incoming])
                    matter[field] = existing
            matter["updated"] = datetime.now().isoformat()
            self.store_own_memory(matter_id, json.dumps(matter))
            return {"matter": matter, "disclaimer": DISCLAIMER}

        elif task == "add_to_notebook":
            return self._add_layer_entry(args, "notebook_index", "notebook")

        elif task == "add_to_evidence_binder":
            return self._add_layer_entry(args, "evidence_index", "evidence_binder")

        elif task == "add_to_filing_layer":
            return self._add_layer_entry(args, "filing_index", "filing")

        elif task == "get_matter_view":
            matter_id = args.get("matter_id") if isinstance(args, dict) else None
            if not matter_id:
                return {"error": "Missing matter_id", "disclaimer": DISCLAIMER}
            matter = self._load_record(matter_id)
            if matter is None:
                return {"error": f"Matter {matter_id} not found", "disclaimer": DISCLAIMER}
            return {
                "matter": matter,
                "notebook": self._get_layer_entries("notebook_index", matter_id),
                "evidence_binder": self._get_layer_entries("evidence_index", matter_id),
                "filing_layer": self._get_layer_entries("filing_index", matter_id),
                "disclaimer": DISCLAIMER,
            }

        elif task == "check_filing":
            # Deterministic pre-filing guard. Runs in milliseconds without a
            # matter, because a check that needs setup and minutes gets skipped
            # at exactly the moment it is most needed.
            draft_text = args.get("draft_text") if isinstance(args, dict) else (args[0] if args else None)
            matter_id = args.get("matter_id") if isinstance(args, dict) else None
            if not draft_text:
                return {"error": "Usage: check_filing {draft_text, [matter_id]}", "disclaimer": DISCLAIMER}
            matter = self._load_record(matter_id) if matter_id else None

            findings = (self._check_theory(draft_text)
                        + self._check_caption(draft_text)
                        + self._check_deadlines(draft_text, matter)
                        + self._check_citations(draft_text)
                        + self._check_volume(matter_id))
            blocks = [f for f in findings if f["severity"] == "BLOCK"]
            warns = [f for f in findings if f["severity"] == "WARN"]
            verdict = "DO NOT FILE" if blocks else ("REVIEW BEFORE FILING" if warns else "no blocking issues found")
            result = {
                "verdict": verdict,
                "blocking": len(blocks), "warnings": len(warns),
                "findings": findings,
                # Stated plainly so a clean result is not read as approval. This
                # checks four documented failure modes, not the merits.
                "scope": ("Checks the four failure modes that decided 5:25-cv-00500-XR: "
                          "fatal legal theory, filing vehicle, the 14-day R&R objection "
                          "window, and citation accuracy. A clean result means those four "
                          "were not triggered - it is not a view on whether the filing "
                          "should succeed."),
                "disclaimer": DISCLAIMER,
            }
            self._write_lesson(
                f"check_filing run: {verdict} ({len(blocks)} blocking, {len(warns)} warnings)",
                strategy_type="pre_filing_check", case_id=matter_id or "", category="procedural"
            ) if blocks else None
            return result

        elif task == "review_filing_draft":
            matter_id = args.get("matter_id") if isinstance(args, dict) else None
            draft_text = args.get("draft_text") if isinstance(args, dict) else None
            if not matter_id or not draft_text:
                return {"error": "Usage: {matter_id, draft_text}", "disclaimer": DISCLAIMER}
            matter = self._load_record(matter_id)
            if matter is None:
                return {"error": f"Matter {matter_id} not found", "disclaimer": DISCLAIMER}
            requested_outcome = matter.get("requested_outcome", "")

            remedy_prompt = (
                "Review this filing draft against the case's requested outcome. Answer these 5 "
                "questions briefly:\n"
                "1. What specific harm is being identified?\n"
                "2. Who has authority to address it?\n"
                "3. What action is being requested?\n"
                "4. Does this forum have authority to provide that action?\n"
                "5. What evidence supports the request?\n\n"
                f"Requested outcome: {requested_outcome}\n\nDraft:\n\"\"\"\n{draft_text[:6000]}\n\"\"\"\n\nAnswers:"
            )
            remedy_alignment = self._call_inference(remedy_prompt)

            checklist_items = [
                "What is the single primary issue?",
                "What relief is requested?",
                "Does every section support that relief?",
                "Is this introducing a new issue?",
                "Does this belong in another proceeding?",
                "Is information repeated?",
                "Are citations relevant?",
                "Are exhibits organized and referenced?",
                "Is the document unnecessarily long?",
            ]
            checklist_prompt = (
                "Review this filing draft. For each item below, answer briefly (one short line per item):\n"
                + "\n".join(f"{i+1}. {q}" for i, q in enumerate(checklist_items))
                + f"\n\nDraft:\n\"\"\"\n{draft_text[:6000]}\n\"\"\"\n\nAnswers (one per line, in order):"
            )
            checklist_raw = self._call_inference(checklist_prompt)
            checklist_lines = [l.strip() for l in checklist_raw.split("\n") if l.strip()][:len(checklist_items)]
            discipline_checklist = [
                {"item": checklist_items[i], "note": checklist_lines[i] if i < len(checklist_lines) else ""}
                for i in range(len(checklist_items))
            ]

            paragraphs = [p.strip() for p in draft_text.split("\n\n") if p.strip()][:30]
            scope_results = []
            for para in paragraphs:
                scope_prompt = (
                    f"Does this paragraph directly assist resolving the following requested issue: "
                    f"\"{requested_outcome}\"? Answer with exactly one word - keep, move_to_notebook, "
                    f"or remove - then a brief reason on the same line.\n\nParagraph:\n\"\"\"\n{para[:1000]}\"\"\""
                )
                result = self._call_inference(scope_prompt)
                verdict = "keep"
                lowered = (result or "").lower()
                if "remove" in lowered:
                    verdict = "remove"
                elif "move_to_notebook" in lowered or "move to notebook" in lowered:
                    verdict = "move_to_notebook"
                scope_results.append({"paragraph": para[:200], "verdict": verdict, "note": result})

            pii_hits = self._scan_for_pii(draft_text)
            needs_action = any(r["verdict"] != "keep" for r in scope_results) or bool(pii_hits)

            return {
                "matter_id": matter_id,
                "remedy_alignment": remedy_alignment,
                "discipline_checklist": discipline_checklist,
                "scope_filter": scope_results,
                "pii_findings": pii_hits,
                "recommendation": self._make_recommendation(
                    observation=f"Reviewed {len(paragraphs)} paragraph(s) against requested outcome '{requested_outcome}'.",
                    reason="Filing discipline, remedy alignment, scope, and privacy were all checked before this draft is considered filing-ready.",
                    action="Address any 'remove'/'move_to_notebook' paragraphs and any PII findings before filing." if needs_action else "No scope or privacy issues found; review the discipline checklist and remedy alignment notes above.",
                    confidence="medium"
                ),
                "disclaimer": DISCLAIMER,
            }

        elif task == "compress_matter":
            matter_id = args.get("matter_id") if isinstance(args, dict) else None
            if not matter_id:
                return {"error": "Missing matter_id", "disclaimer": DISCLAIMER}
            matter = self._load_record(matter_id)
            if matter is None:
                return {"error": f"Matter {matter_id} not found", "disclaimer": DISCLAIMER}
            notebook = self._get_layer_entries("notebook_index", matter_id)
            evidence = self._get_layer_entries("evidence_index", matter_id)
            combined_text = "\n\n".join(e["content"] for e in notebook + evidence)[:10000]
            prompt = (
                "You are compressing a large legal research archive into a usable filing format. "
                "Read the material below and produce a single valid JSON object with these fields:\n"
                "{\n"
                '  "executive_summary": "",\n'
                '  "timeline": [],\n'
                '  "key_facts": [],\n'
                '  "issue_statement": "",\n'
                '  "evidence_list": [],\n'
                '  "requested_relief": ""\n'
                "}\n\n"
                f"Requested outcome (from intake): {matter.get('requested_outcome', '')}\n\n"
                f"Research material:\n\"\"\"\n{combined_text}\n\"\"\"\n\nJSON:"
            )
            raw = self._call_inference(prompt)
            parsed, parse_error = self._safe_parse_json(raw)
            if parsed is None:
                parsed = {"executive_summary": "", "timeline": [], "key_facts": [], "issue_statement": "", "evidence_list": [], "requested_relief": ""}
            parsed["parse_error"] = parse_error
            if parse_error:
                parsed["raw_model_output"] = raw
            parsed["matter_id"] = matter_id
            parsed["disclaimer"] = DISCLAIMER
            return parsed

        elif task == "check_filing_frequency":
            matter_id = args.get("matter_id") if isinstance(args, dict) else None
            if not matter_id:
                return {"error": "Missing matter_id", "disclaimer": DISCLAIMER}
            filings = self._get_layer_entries("filing_index", matter_id)
            matter = self._load_record(matter_id) or {}
            flags = []

            seen = {}
            for f in filings:
                normalized = re.sub(r"\s+", " ", f["content"].strip().lower())[:500]
                if normalized in seen:
                    flags.append({"type": "duplicate", "filing_ids": [seen[normalized], f["id"]], "note": "Near-identical filing content."})
                else:
                    seen[normalized] = f["id"]

            from datetime import timedelta
            timestamps = sorted(datetime.fromisoformat(f["timestamp"]) for f in filings)
            window = timedelta(days=7)
            for i in range(len(timestamps)):
                count = sum(1 for t in timestamps[i:] if t - timestamps[i] <= window)
                if count > 3:
                    flags.append({"type": "high_volume", "note": f"{count} filings within 7 days starting {timestamps[i].isoformat()}."})
                    break

            deadlines = matter.get("deadlines", [])
            deadline_note = None
            if deadlines and filings:
                deadline_note = f"{len(filings)} filing(s) logged against {len(deadlines)} tracked deadline(s) - cross-check manually that timing relative to each deadline is appropriate."

            return {
                "matter_id": matter_id,
                "filing_count": len(filings),
                "flags": flags,
                "deadline_note": deadline_note,
                "recommendation": self._make_recommendation(
                    observation=f"{len(filings)} filing(s) reviewed, {len(flags)} flag(s) raised.",
                    reason="Duplicate content and high filing volume in a short window often indicate scattered or premature filings.",
                    action="Consolidate flagged filings or wait for a court response before filing again." if flags else "No frequency issues found.",
                    confidence="medium"
                ),
                "disclaimer": DISCLAIMER,
            }

        elif task == "check_docket":
            docket_id_raw = args.get("docket_id") if isinstance(args, dict) else (args[0] if args else None)
            if not docket_id_raw:
                return {"error": "Usage: check_docket <docket_id>", "disclaimer": DISCLAIMER}
            try:
                docket_id = int(docket_id_raw)
            except (TypeError, ValueError):
                return {"error": "docket_id must be an integer", "disclaimer": DISCLAIMER}
            tool_result = self.call_tool("courtlistener", "search", {"q": str(docket_id), "type": "r"})
            docket_data = self._unwrap_tool_result(tool_result)
            if isinstance(docket_data, dict) and docket_data.get("error"):
                return {"error": docket_data["error"], "disclaimer": DISCLAIMER}

            entries = docket_data.get("results", []) if isinstance(docket_data, dict) else (docket_data if isinstance(docket_data, list) else [])
            formatted_alerts = []
            for entry in entries[:5]:
                entry_prompt = (
                    "Format this docket entry into a Docket Alert. Produce a single valid JSON object:\n"
                    "{\n"
                    '  "type": "",\n'
                    '  "action_required": true,\n'
                    '  "deadline": "",\n'
                    '  "priority": "",\n'
                    '  "summary": "",\n'
                    '  "recommended_next_step": ""\n'
                    "}\n\n"
                    f"Docket entry:\n\"\"\"\n{json.dumps(entry)[:2000]}\n\"\"\"\n\nJSON:"
                )
                raw = self._call_inference(entry_prompt)
                parsed, _ = self._safe_parse_json(raw)
                if parsed is None:
                    parsed = {"type": "", "action_required": False, "deadline": "", "priority": "", "summary": str(entry)[:300], "recommended_next_step": ""}
                formatted_alerts.append(parsed)

            return {
                "docket_id": docket_id,
                "alerts": formatted_alerts,
                "note": "On-demand check only - not a background poller. Re-run this task periodically to catch new entries.",
                "disclaimer": DISCLAIMER,
            }

        elif task == "scan_for_pii":
            text = args.get("text") if isinstance(args, dict) else (args[0] if args else None)
            if not text:
                return {"error": "Missing text", "disclaimer": DISCLAIMER}
            hits = self._scan_for_pii(text)
            return {
                "findings": hits,
                "count": len(hits),
                "recommendation": self._make_recommendation(
                    observation=f"{len(hits)} potential privacy finding(s)." if hits else "No PII patterns or protected information detected.",
                    reason="SSNs, tax IDs, financial account numbers, birth dates, and other protected identifiers should not appear in filings.",
                    action="Redact all flagged items before filing." if hits else "No redaction needed.",
                    confidence="medium" if hits else "high"
                ),
                "disclaimer": DISCLAIMER,
            }

        elif task == "reflect_on_matter":
            matter_id = args.get("matter_id") if isinstance(args, dict) else None
            if not matter_id:
                return {"error": "Missing matter_id", "disclaimer": DISCLAIMER}
            matter = self._load_record(matter_id)
            if matter is None:
                return {"error": f"Matter {matter_id} not found", "disclaimer": DISCLAIMER}
            view = {
                "matter": matter,
                "notebook": self._get_layer_entries("notebook_index", matter_id),
                "evidence_binder": self._get_layer_entries("evidence_index", matter_id),
                "filing_layer": self._get_layer_entries("filing_index", matter_id),
            }
            prompt = (
                "Reflect on this completed (or in-progress) legal matter. Answer these 5 questions "
                "briefly, focused on process improvement, not legal conclusions:\n"
                "1. What worked?\n2. What failed?\n3. What procedural mistakes occurred?\n"
                "4. What assumptions were incorrect?\n5. What workflow improvements should be applied?\n\n"
                f"Matter:\n{json.dumps(view, default=str)[:8000]}\n\nAnswers:"
            )
            reflection = self._call_inference(prompt)
            result = {"matter_id": matter_id, "reflection": reflection, "disclaimer": DISCLAIMER}
            if isinstance(args, dict) and args.get("file_lesson"):
                result["filed_lesson"] = self._write_lesson(
                    reflection, strategy_type="reflection", case_id="",
                    tags=args.get("tags", ""), category=args.get("lesson_category", "procedural")
                )
            return result

        elif task == "map_authority":
            relationship_id = args.get("relationship_id") if isinstance(args, dict) else (args[0] if args else None)
            if not relationship_id:
                return {"error": "Usage: map_authority <relationship_id>", "disclaimer": DISCLAIMER}
            doc = self._load_relationship(relationship_id)
            if doc is None:
                return {"error": f"Relationship {relationship_id} not found", "disclaimer": DISCLAIMER}
            asset = doc.get("asset") or doc.get("trust_property") or "unspecified asset"
            roles = {
                "owner": doc.get("asset_owner") or doc.get("settlor") or "",
                "custodian": doc.get("custodian") or doc.get("trustee") or "",
                "manager": doc.get("service_provider") or "",
                "beneficiary": doc.get("beneficiary") or "",
                "service_provider": doc.get("service_provider") or "",
            }
            return {
                "relationship_id": relationship_id,
                "asset": asset,
                "roles": roles,
                "note": "Roles are identified from the stored extraction, not assumed.",
                "disclaimer": DISCLAIMER,
            }

        else:
            return {"error": f"Unknown task: {task}", "disclaimer": DISCLAIMER}


if __name__ == "__main__":
    agent = LegalAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
