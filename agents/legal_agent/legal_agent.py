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
    # Words that claim a request for this agent. Declared here, not in
    # Boss - the orchestrator holds no domain vocabulary.
    ROUTING_TERMS = (
        "contract", "agreement", "clause", "statute", "regulation", "\\bcfr\\b",
        "\\busc\\b", "case ?law", "precedent", "docket", "plaintiff",
        "defendant", "court", "judge", "ruling", "opinion", "jurisdiction",
        "liability", "indemnif", "breach", "enforceab", "unconscionab",
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
                "claim_corroborate", "claim_get", "claim_list", "claim_ontology"
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
        found = self.lookup_reference(cite)
        entry = {"citation": cite,
                 "located_in_corpus": bool(found),
                 "governs": args.get("governs"),
                 "governs_basis": args.get("governs_basis", ""),
                 "checked_at": datetime.now().isoformat()}
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
        c["prerequisites"][key] = {"state": "answered", "detail": detail,
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

        if task == "lookup":
            if not args or not args[0]:
                return {"error": "Usage: lookup <term_or_citation>", "disclaimer": DISCLAIMER}
            term = args[0]
            # The corpus comes first. It was consulted nowhere in this path:
            # lookup went cache -> web -> model, so a term defined in Black's
            # and a case discussed in a treatise on the shelf both reached the
            # open web before they reached the books this agent owns.
            # Exact headword first, then the corpus, then the loose
            # first-word definition. A doctrine phrase must reach the treatises
            # before it is reduced to a one-word gloss.
            defined = self.lookup_term(term, loose=False)
            if defined:
                return {"term": term, "source": "reference/legal_agent dictionary",
                        "definition": defined, "disclaimer": DISCLAIMER}
            passages = self.lookup_reference(term)
            if passages:
                return {"term": term, "source": "reference/legal_agent corpus",
                        "results": passages, "disclaimer": DISCLAIMER}
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
