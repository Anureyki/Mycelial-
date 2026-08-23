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
# Vocabulary that routes a request to Grow Agent. Deliberately broad, and
# matched on word boundaries so short terms ("res", "ec", "veg") don't fire on
# unrelated words. The narrow original list - plant/grow/garden/reservoir/
# seedling/nutrient - missed "what's the nutrition in the DWC" entirely
# ("nutrient" is not a substring of "nutrition", and "dwc" was absent), so a
# question squarely about the grow fell through to the generic reasoning
# fallback and a 1.5b model answered about a "Direct Water Cooker".
GROW_TERMS = (
    # systems and hardware
    "dwc", "lwc", "hydro", "hydroponic", "reservoir", "res", "bucket", "tent",
    "net pot", "clay pebble", "pebbles", "leca", "air stone", "airstone",
    "top feed", "air pump",
    # measurements and inputs
    "ppm", "\bph\b", "\bec\b", "tds", "nutrient", "nutrition", "feed", "feeding",
    "cal-?mag", "calmag", "flora ?(micro|gro|bloom)", "runoff",
    # plant and lifecycle
    "plant", "grow", "garden", "seedling", "germinat", "sprout", "veg\b",
    "vegetative", "flower", "bloom", "pistil", "calyx", "trichome", "harvest",
    "leaf", "leaves", "canopy", "node", "root", "roots", "strain",
    "autoflower", "auto-?flower", "photoperiod", "cultivar",
    # dosing phrasings that name no plant, no unit and no equipment. "How much
    # do I add to reach 800" is unambiguous inside a grow assistant and was
    # being answered by a CODE model as "add 200".
    "how much.*add", "add.*to reach", "to reach \\d", "reach \\d{3}",
    "top ?up", "how much more",
    # the act of keeping the record itself - asking how often to log is a grow
    # question even when it names no plant, no measurement and no equipment
    "reading", "readings", "log\b", "logging", "cadence", "how often",
    # actions
    "transplant", "defoliat", "lollipop", "topping", "water change", "top ?off",
)

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

    def _extract_reading(self, prompt):
        """Pull a plain-language reading out of a prompt, or None.

        Factored out because the same numbers can arrive alongside photos, and
        the image branch used to return before ever reaching the reading parser -
        so "19.7c 6.15ph 688ppm" sent WITH three photos had its numbers silently
        discarded while the photos went through. Losing a measurement is worse
        than losing the photo: the photo can be retaken, the reservoir at that
        moment cannot."""
        ppm = re.search(r'(\d+(?:\.\d+)?)\s*ppm', prompt, re.IGNORECASE)
        ph = re.search(r'(\d+(?:\.\d+)?)\s*ph\b', prompt, re.IGNORECASE) or \
            re.search(r'\bph\s*(?:of|is|:)?\s*(\d+(?:\.\d+)?)', prompt, re.IGNORECASE)
        tc = re.search(r'(\d+(?:\.\d+)?)\s*(?:°|deg(?:rees)?)?\s*c\b', prompt, re.IGNORECASE)
        tf = re.search(r'(\d+(?:\.\d+)?)\s*(?:°|deg(?:rees)?)?\s*f\b', prompt, re.IGNORECASE)
        if sum(1 for m in (ppm, ph, tc, tf) if m) < 2:
            return None
        temp_c = float(tc.group(1)) if tc else ((float(tf.group(1)) - 32) * 5 / 9 if tf else None)
        out = {}
        if ppm:
            out["ppm"] = float(ppm.group(1))
        if ph:
            out["ph"] = float(ph.group(1))
        if temp_c is not None:
            out["temp"] = round(temp_c, 1)
        return out or None

    def _log_reading(self, reading_args):
        status = self.send_a2a("grow_agent", "get_status", {})
        r = status.get("result", {}) if isinstance(status, dict) else {}
        r = r.get("result", r) if isinstance(r, dict) else {}
        stage = r.get("current_stage") or "seedling"
        reading_args = dict(reading_args, stage="seedling" if stage == "unknown" else stage)
        return self.send_a2a("grow_agent", "log_reading", reading_args)

    _plant_terms_cache = {"terms": None, "at": 0}

    def _known_plant_terms(self):
        """Names, ids and strains of the plants Grow Agent is actually tracking.

        Routing on a fixed keyword list cannot know that "gsc" means a plant
        here. "Anansi what is my gsc# 1 specs" matched no grow term and was sent
        to the CODE agent, which replied that Anansi is a character from African
        folklore. The strain name was in the prompt and in the agent's own
        records, and the router had no way to connect them.

        Asking the agent what it is tracking means routing follows the data:
        register a plant and questions about it route correctly from then on,
        with no keyword list to remember to update. Cached briefly because this
        runs on every prompt."""
        now = time.time()
        c = self._plant_terms_cache
        if c["terms"] is not None and now - c["at"] < 300:
            return c["terms"]
        terms = {}          # term -> plant_id it identifies
        try:
            st = self.send_a2a("grow_agent", "get_status", {}, timeout=20)
            for _ in range(3):
                st = st.get("result", st) if isinstance(st, dict) else st
            if isinstance(st, dict):
                v = st.get("current_strain")
                if isinstance(v, str) and v.strip():
                    terms[v.strip().strip('"').lower()] = "current_plant"
                for pl in st.get("other_plants") or []:
                    pid = pl.get("plant_id")
                    for k in ("plant_id", "strain", "species"):
                        v = pl.get(k)
                        if isinstance(v, str) and v.strip():
                            # A term already claimed by another plant is ambiguous
                            # (both cannabis plants share a strain), so it routes
                            # to grow without naming one.
                            key = v.strip().lower()
                            terms[key] = None if key in terms and terms[key] != pid else pid
        except Exception as e:
            self.log(f"could not fetch plant terms: {e}")
        # Also index the distinctive words inside a strain name, so "gsc",
        # "girl scout cookies" and "cookies" all reach the right agent. Short and
        # generic tokens are dropped - "auto" and "1" would match anything.
        extra = {}
        # Words that are common English before they are plant names. "girl" on
        # its own would match unrelated text, and an initialism built from an id
        # like "gsc_auto_2" produced "ga2", which means nothing to anyone.
        STOP = {"the", "and", "auto", "autoflower", "plant", "current", "cannabis",
                "vera", "girl", "scout", "seed", "seeds", "pot", "mother", "clone"}
        for t in list(terms):
            words = [w for w in re.split(r'[^a-z0-9]+', t) if w]
            for w in words:
                if len(w) >= 4 and w not in STOP and not w.isdigit():
                    extra[w] = terms[t] if extra.get(w, terms[t]) == terms[t] else None
            # Initials only from a multi-word NAME, not from an underscored id -
            # "girl scout cookies" gives gsc, "gsc_auto_2" gives nothing useful.
            # Initials from the NAME only. "Girl Scout Cookies (autoflower)"
            # must give gsc, not gsca - the parenthetical is a qualifier, not
            # part of what anyone calls the plant.
            if "_" not in t:
                base = re.sub(r'\(.*?\)', '', t)
                bw = [w for w in re.split(r'[^a-z]+', base) if w]
                if len(bw) >= 2:
                    initials = "".join(w[0] for w in bw)
                    if len(initials) >= 3:
                        extra[initials] = terms[t] if extra.get(initials, terms[t]) == terms[t] else None
        for k, v in extra.items():
            terms.setdefault(k, v)
        c["terms"], c["at"] = terms, now
        if terms:
            self.log(f"plant routing terms: {sorted(terms)[:12]}")
        return terms

    # "plant one", "plant #2", "my first autoflower". People refer to plants by
    # position at least as often as by name, and a strain shared between two
    # plants cannot disambiguate them at all - both of these are Girl Scout
    # Cookies.
    _ORDINALS = {"one": 1, "first": 1, "1st": 1, "two": 2, "second": 2, "2nd": 2,
                 "three": 3, "third": 3, "3rd": 3, "four": 4, "fourth": 4}

    # What KIND of grow question this is. Reaching the right agent and the right
    # plant still leaves the question unanswered if every reply is the same
    # status card - "when is my next nutrient upgrade", "what is my ppm right
    # now" and "how is plant one" all returned identical text.
    GROW_INTENTS = (
        # Asked before "schedule" so "how often should I log" is answered with
        # the cadence the analyses need, not with the next reservoir change.
        # "Why can't I do it NOW" is a different question from "why is it like
        # this", and answering the second when the first was asked reads as
        # evasion. Declared first because it is the more specific of the two.
        ("blockers", r"\bwhy (can'?t|cant|not|no|won'?t|shouldn'?t|couldn'?t)\b|"
                     r"\bwhat'?s (stopping|blocking|in the way)\b|"
                     r"\bwhy not (now|yet|today)\b|\bwhy (do|should) i (have to )?wait\b|"
                     r"\bcan'?t (i|we)\b.*\bnow\b|\bstopping (me|us)\b"),
        # "Why is it like this" is answerable from what was recorded at the
        # time, and is a different question from "what should I do next".
        ("why", r"\bwhy\b|\bwhat made\b|\bhow come\b|\bwhat was the (reason|thinking)\b|"
                r"\bdid we (stop|choose|pick|settle|decide)\b|\breasoning behind\b"),
        ("cadence", r"\bhow often\b.*\b(log|read|check|measure|record|take)\b|"
                    r"\b(log|reading|readings)\b.*\b(how often|frequency|cadence|expectancy)\b|"
                    r"\b(reading|logging) (frequency|cadence|schedule|expectancy)\b|"
                    r"\bhow many (readings|logs)\b"),
        ("schedule", r"\bwhen\b|\bhow (long|soon|often)\b|\bnext\b|\bdue\b|\bschedule\b|"
                     r"\bupcoming\b|\bshould i .*(change|feed|water|top ?up)\b"),
        ("measurement", r"\bwhat('| i)?s? (my|the) (ppm|ph|ec|tds|temp|temperature|humidity|volume)\b|"
                        r"\b(ppm|ph|ec|temp|humidity) (right now|currently|reading|level)\b|"
                        r"\bhow (much|many) (ppm|ml)\b"),
        # "What did that cost me" - asked before "feed" so that a question about
        # the loss from an underfeed is not answered with the current recipe.
        # "How much do I add to reach 800" is a dosing calculation, not a status
        # question. Declared before measurement so a prompt containing "ppm" is
        # not answered with the current reading.
        ("dosing", r"\bhow much\b.*\b(add|need|more|raise|reach|get to|bring)\b|"
                   r"\b(add|raise|bring|get)\b.*\bto (reach|hit|get to)\b|"
                   r"\breach\s*\d{2,4}\b|\bto\s*\d{3,4}\s*ppm\b|"
                   r"\btarget\b.*\d{3,4}|\d{3,4}\s*ppm\b.*\b(target|reach|goal)\b"),
        ("impact", r"\b(loss|lost|cost|impact|damage|set ?back|setback|stagnant|stunted|"
                   r"behind|deficit|underfed|under-?feed|how bad|make up for|catch up)\b"),
        ("feed", r"\bfeed\b|\bnutrient|\brecipe\b|\bdose\b|\bhow much .*(cal|flora|nute)"),
    )

    def _grow_intent(self, prompt):
        lp = (prompt or "").lower()
        for name, pattern in self.GROW_INTENTS:
            if re.search(pattern, lp):
                # "next nutrient upgrade" is a schedule question even though it
                # says nutrient, so the first match wins by declaration order.
                return name
        return "status"

    def _plant_order(self):
        """Plants in the order a person would count them. current_plant is #1
        because it is the one that was here first."""
        # ACTIVE plants only. A position is not a name: once plant one is
        # harvested or given away, "plant one" means whatever is still growing
        # and started earliest. The underlying id never changes and never gets
        # reused, so the history stays attached to the plant that earned it.
        order = []
        try:
            lp = self.send_a2a("grow_agent", "list_plants", {}, timeout=20)
            for _ in range(3):
                lp = lp.get("result", lp) if isinstance(lp, dict) else lp
            for pl in (lp or {}).get("active") or []:
                pid = pl.get("plant_id")
                if pid and pid not in order:
                    order.append(pid)
        except Exception:
            pass
        if not order:
            order = ["current_plant"]
        return order

    def _ordinal_from_prompt(self, prompt):
        lp = (prompt or "").lower()
        m = re.search(r'\b(?:plant|grow)\s*#?\s*(\d+)\b', lp) or \
            re.search(r'#\s*(\d+)\b', lp)
        n = int(m.group(1)) if m else None
        if n is None:
            for word, val in self._ORDINALS.items():
                if re.search(r'\b(?:plant|grow|auto ?flower)\s+' + word + r'\b', lp) or \
                   re.search(r'\b' + word + r'\s+(?:plant|grow|auto ?flower)\b', lp) or \
                   re.search(r'\bmy\s+' + word + r'\b', lp):
                    n = val
                    break
        if not n:
            return None
        order = self._plant_order()
        return order[n - 1] if 1 <= n <= len(order) else None

    # Which facet leads, by what the question actually asked. Every answer
    # carries the others behind it - the question chooses emphasis, not content.
    FACET_LEAD = (
        ("blocked_by", r"\bwhy (can'?t|cant|not|won'?t|shouldn'?t)\b|\bwhat'?s (stopping|blocking)\b|"
                       r"\bwhy not (now|yet)\b|\bwhy .*wait\b|\bstopping (me|us)\b"),
        ("why",        r"\bwhy\b|\bhow come\b|\bwhat made\b|\bdid we (stop|choose|pick|decide)\b|"
                       r"\breasoning\b"),
        ("how",        r"\bhow (much|do i|would i)\b|\bwhat do i (need|have) to\b|\badd\b|\bget to\b"),
        ("when",       r"\bwhen\b|\bhow (long|soon)\b|\bwhat point\b|\bready\b|\btiming\b"),
        ("what",       r"\bwhat (is|are|'?s)\b|\bwhere (is|are|do)\b|\bstatus\b|\bright now\b"),
    )

    def _answer_from_situation(self, plant_id, prompt):
        """One situation, ordered by what was asked.

        The grower asked why, how and when about the same reservoir and each one
        needed its own intent, task and regex. There is one situation - the
        question only decides which facet leads. This means a phrasing nobody
        anticipated still gets a complete answer, arranged differently, rather
        than falling to a status card because no pattern matched."""
        def peel(x, n=3):
            for _ in range(n):
                x = x.get("result", x) if isinstance(x, dict) else x
            return x if isinstance(x, dict) else {}

        lp = (prompt or "").lower()
        nums = [float(x) for x in re.findall(r'\b(\d{3,4})\b', prompt or "")]
        target = max([n for n in nums if 300 <= n <= 2000], default=None)

        sit = peel(self.send_a2a("grow_agent", "situation",
                                 {"plant_id": plant_id, "target_ppm": target}, timeout=90))
        facets = sit.get("facets") or {}
        if not facets:
            return None

        lead = next((name for name, pat in self.FACET_LEAD
                     if re.search(pat, lp) and name in facets), "what")
        # Everything else follows in a fixed, readable order.
        rest = [f for f in ("what", "blocked_by", "why", "how", "when")
                if f != lead and f in facets]
        order = [lead] + rest

        out = []
        for name in order:
            f = facets.get(name) or {}
            summary = f.get("summary")
            if not summary:
                continue
            if name == "blocked_by" and f.get("items"):
                out.append(summary)
                for x in f["items"][:3]:
                    out.append(f"{x['detail']} {x['why']} Clears when: {x['clears_when']}")
            elif name == "how" and f.get("caution"):
                out.append(summary + " " + f["caution"])
            else:
                out.append(summary)
        return " ".join(out) if out else None

    def _target_note(self, plant_id, prompt):
        """If the prompt names a ppm target, say where it sits and what it costs.

        Folded into any answer rather than replacing it, because "when do you
        recommend pushing to 800" is a timing question AND a target question and
        the grower should not have to ask twice."""
        nums = [float(x) for x in re.findall(r'\b(\d{3,4})\b', prompt or "")]
        targets = [n for n in nums if 300 <= n <= 2000]
        if not targets:
            return None
        target = max(targets)

        def peel(x, n=3):
            for _ in range(n):
                x = x.get("result", x) if isinstance(x, dict) else x
            return x if isinstance(x, dict) else {}

        drift = peel(self.send_a2a("grow_agent", "check_target_drift",
                                   {"plant_id": plant_id}, timeout=30))
        cur = drift.get("ppm")
        band = drift.get("target") or []
        if cur is not None and abs(cur - target) < 15:
            return None                      # already there; nothing to say
        dose = peel(self.send_a2a("grow_agent", "adjust_to_target_ppm",
                                  {"plant_id": plant_id, "target_ppm": target}, timeout=60))
        add = dose.get("add_now") or {}
        bits = []
        if cur is not None and band:
            where = ("the low end of" if cur < band[0] + (band[1] - band[0]) / 3
                     else "mid" if cur < band[0] + 2 * (band[1] - band[0]) / 3 else "the top of")
            bits.append(f"You are at {cur:.0f}, {where} the {band[0]}-{band[1]} band; "
                        f"{target:.0f} is also inside it.")
        if add:
            bits.append("Getting to " + f"{target:.0f}" + " costs "
                        + ", ".join(f"{k} {v}ml" for k, v in add.items()) + ".")
        if dose.get("top_fed_caution"):
            bits.append(dose["top_fed_caution"])
        return " ".join(bits) or None

    def _compose_grow_answer(self, plant_id, prompt):
        """Assemble an answer for a grow question that matched no known intent.

        The alternative was the status card, and that is what made five
        different questions today read as being ignored. Adding a regex per
        phrasing does not converge - the grower keeps finding wordings nobody
        thought of, which is the correct outcome for a person talking normally.

        So the fallback stops being a summary and starts being an answer built
        from whatever the prompt actually touches: a number is treated as a
        target, timing words pull in how recently things changed, and the
        in-band check is always relevant because it is the question under most
        others."""
        def peel(x, n=3):
            for _ in range(n):
                x = x.get("result", x) if isinstance(x, dict) else x
            return x if isinstance(x, dict) else {}

        lp = (prompt or "").lower()
        bits = []

        # A "why can't I" reaching the composer instead of the blockers intent
        # still deserves the blocker list. The gate is not reliable enough to be
        # the only route to an answer this specific.
        if re.search(r"\bwhy (can'?t|cant|not|won'?t|shouldn'?t)\b|\bwhat'?s (stopping|blocking)\b|"
                     r"\bwhy not (now|yet)\b|\bwait\b", lp):
            nums_b = [float(x) for x in re.findall(r'\b(\d{3,4})\b', prompt or "")]
            b = peel(self.send_a2a("grow_agent", "blockers_for_change",
                                   {"plant_id": plant_id,
                                    "target_ppm": max([n for n in nums_b if 300 <= n <= 2000],
                                                      default=None)}, timeout=45))
            items = b.get("blockers") or []
            if items:
                out = [b.get("verdict") or ""]
                for x in items:
                    out.append(f"{x['detail']} {x['why']} Clears when: {x['clears_when']}")
                return " ".join(v for v in out if v)

        drift = peel(self.send_a2a("grow_agent", "check_target_drift",
                                   {"plant_id": plant_id}, timeout=30))
        if drift.get("applicable"):
            bits.append(drift.get("message") or "")

        # Any three or four digit number in a grow question is almost always a
        # ppm target being aimed at.
        nums = [float(x) for x in re.findall(r'\b(\d{3,4})\b', prompt or "")]
        cand = [n for n in nums if 300 <= n <= 2000]
        target = max(cand) if cand else None
        if target:
            dose = peel(self.send_a2a("grow_agent", "adjust_to_target_ppm",
                                      {"plant_id": plant_id, "target_ppm": target}, timeout=60))
            add = dose.get("add_now") or {}
            band = drift.get("target") or []
            if band and not (band[0] <= target <= band[1]):
                bits.append(f"{target:.0f} sits outside the {band[0]}-{band[1]} band this stage "
                            f"wants, so that is a deliberate push rather than a correction.")
            if add:
                bits.append("To get there: "
                            + ", ".join(f"{k} {v}ml" for k, v in add.items()) + ".")
            if dose.get("top_fed_caution"):
                bits.append(dose["top_fed_caution"])

        # "Is now a good time" needs to know what changed recently.
        if re.search(r"\b(now|today|yet|good time|should i|safe to|ready|after)\b", lp):
            hist = peel(self.send_a2a("grow_agent", "get_nutrient_history",
                                      {"plant_id": plant_id}, timeout=40))
            changes = hist.get("recipe_changes") or []
            if changes:
                last = changes[-1].get("changed_at") or ""
                try:
                    hrs = (datetime.now() - datetime.fromisoformat(last[:19])).total_seconds() / 3600
                except Exception:
                    hrs = None
                if hrs is not None and hrs < 72:
                    bits.append(
                        f"The feed was last changed {hrs:.0f}h ago. Changing strength again this "
                        "soon means the next reading cannot tell you which change caused what, "
                        "and after a system move the roots are still re-establishing - raise it "
                        "once new white root growth is visible and the current level has held "
                        "steady for a couple of readings.")
                elif hrs is not None:
                    bits.append(f"Last feed change was {hrs/24:.0f} day(s) ago, so a change now "
                                "is cleanly attributable.")

        for t in (peel(self.send_a2a("grow_agent", "check_in",
                                     {"plant_id": plant_id}, timeout=60)).get("triggers") or [])[:2]:
            bits.append(t)
        return " ".join(b for b in bits if b) or None

    def _answer_grow_question(self, intent, plant_id, prompt):
        """Answer the question that was asked. Returns None to fall through to
        the general status card when this cannot do better."""
        def peel(x, n=3):
            for _ in range(n):
                x = x.get("result", x) if isinstance(x, dict) else x
            return x if isinstance(x, dict) else {}

        if intent == "measurement":
            st = peel(self.send_a2a("grow_agent", "get_status", {}, timeout=30))
            hist = peel(self.send_a2a("grow_agent", "get_grow_history",
                                      {"plant_id": plant_id}, timeout=30))
            series = hist.get("ppm_series") or []
            lp = prompt.lower()
            drift = peel(self.send_a2a("grow_agent", "check_target_drift",
                                       {"plant_id": plant_id}, timeout=30))
            bits = []
            if series:
                last = series[-1]
                if "ppm" in lp or not any(k in lp for k in ("ph", "temp", "humid")):
                    bits.append(f"Last ppm reading: {last.get('ppm'):.0f}, taken "
                                f"{str(last.get('at'))[:10]}.")
            if drift.get("applicable"):
                bits.append(drift.get("message") or "")
                if drift.get("action") and drift.get("status") != "in_band":
                    bits.append(drift["action"])
            return " ".join(b for b in bits if b) or None

        if intent == "schedule":
            ci = peel(self.send_a2a("grow_agent", "check_in", {"plant_id": plant_id}, timeout=60))
            drift = peel(self.send_a2a("grow_agent", "check_target_drift",
                                       {"plant_id": plant_id}, timeout=30))
            st = peel(self.send_a2a("grow_agent", "get_status", {}, timeout=30))
            bits = []
            # A feed change is not a date - it is triggered by the stage moving
            # or the reading drifting. Say what the trigger is.
            if drift.get("applicable") and drift.get("status") != "in_band":
                bits.append("Now: " + (drift.get("message") or ""))
                bits.append(drift.get("action") or "")
            elif drift.get("applicable"):
                bits.append(drift.get("message") or "")
                bits.append("No change needed on that front until the stage moves or the "
                            "reading drifts out of band.")
            for t in (ci.get("triggers") or [])[:3]:
                bits.append(t)
            rem = st.get("pending_reminders") or []
            if rem:
                bits.append("Scheduled: " + "; ".join(
                    f"{r.get('title')} (due {r.get('target_date')})" for r in rem[:3]) + ".")
            return " ".join(b for b in bits if b) or None

        if intent == "dosing":
            m = re.search(r'(\d{3,4})\s*(?:ppm)?', prompt or "")
            if not m:
                return None
            target = float(m.group(1))
            # A prompt can carry several numbers ("800 high 700 low 800"); the
            # largest three-or-four digit figure is the target being aimed at.
            nums = [float(x) for x in re.findall(r'\b(\d{3,4})\b', prompt or "")]
            if nums:
                target = max(nums)
            d = peel(self.send_a2a("grow_agent", "adjust_to_target_ppm",
                                   {"plant_id": plant_id, "target_ppm": target}, timeout=60))
            if not d or d.get("error"):
                return None
            add = d.get("add_now") or {}
            bits = [d.get("observation") or ""]
            if add:
                bits.append("Add: " + ", ".join(f"{k} {v}ml" for k, v in add.items()) + ".")
            else:
                bits.append(f"Scale what is already in there by {d.get('factor')}x.")
            if d.get("top_fed_caution"):
                bits.append(d["top_fed_caution"])
            bits.append(d.get("action") or "")
            return " ".join(b for b in bits if b)

        if intent == "blockers":
            nums = [float(x) for x in re.findall(r'\b(\d{3,4})\b', prompt or "")]
            tgt = max([n for n in nums if 300 <= n <= 2000], default=None)
            b = peel(self.send_a2a("grow_agent", "blockers_for_change",
                                   {"plant_id": plant_id, "target_ppm": tgt}, timeout=45))
            items = b.get("blockers") or []
            if not items:
                return (b.get("verdict") or "Nothing is blocking it.") + " Go ahead."
            bits = [b.get("verdict") or ""]
            for x in items:
                bits.append(f"{x['detail']} {x['why']} That clears when: {x['clears_when']}")
            bits.append(b.get("note") or "")
            return " ".join(v for v in bits if v)

        if intent == "why":
            # "Why did we stop at 688 instead of 800" is history AND a question
            # about what is in the way of 800 now. Answer both, blockers first,
            # because that is the part the grower is actually acting on.
            nums = [float(x) for x in re.findall(r'\b(\d{3,4})\b', prompt or "")]
            cand = [n for n in nums if 300 <= n <= 2000]
            lead = ""
            if len(cand) >= 2 or (cand and re.search(r"instead of|rather than|not\s+\d", prompt.lower())):
                b = peel(self.send_a2a("grow_agent", "blockers_for_change",
                                       {"plant_id": plant_id, "target_ppm": max(cand)}, timeout=45))
                for x in (b.get("blockers") or [])[:3]:
                    lead += f"{x['detail']} {x['why']} Clears when: {x['clears_when']} "
            d = peel(self.send_a2a("grow_agent", "explain_decision",
                                   {"plant_id": plant_id, "topic": prompt}, timeout=40))
            if not d.get("found"):
                return d.get("note")
            bits = []
            for e in (d.get("decisions") or [])[-2:]:
                when = str(e.get("at"))[:10]
                line = f"On {when}: {e.get('reason')}"
                if e.get("decision"):
                    line += f" Decision was: {e['decision']}"
                if e.get("expected"):
                    line += f" Expected: {e['expected']}"
                if e.get("measured_after"):
                    line += f" Measured afterwards: {e['measured_after']} ppm."
                bits.append(line)
            # Where it landed against where it was aimed is the actual answer to
            # "why are we here rather than there".
            drift = peel(self.send_a2a("grow_agent", "check_target_drift",
                                       {"plant_id": plant_id}, timeout=30))
            if drift.get("applicable"):
                bits.append(drift.get("message") or "")
            return (lead + " ".join(b for b in bits if b)).strip() or None

        if intent == "cadence":
            c = peel(self.send_a2a("grow_agent", "reading_cadence",
                                   {"plant_id": plant_id}, timeout=40))
            if not c or c.get("error"):
                return None
            bits = [f"Every {c.get('recommended_days')} days at {c.get('stage')} stage.",
                    c.get("recommended_because") or ""]
            o = c.get("observed") or {}
            if o.get("median_gap_days") is not None:
                bits.append(f"You are averaging one every {o['median_gap_days']} days across "
                            f"{o['readings']} readings, with a longest gap of "
                            f"{o['longest_gap_days']} days"
                            + (f" and {o['gaps_over_target']} gap(s) past target."
                               if o.get("gaps_over_target") else "."))
            bits.append(c.get("maximum_because") or "")
            bits.append(c.get("minimum_because") or "")
            sens = c.get("sensors") or {}
            if sens:
                bits.append("With sensors: " + (sens.get("ph_temp") or "")
                            + " " + (sens.get("ppm_volume") or ""))
            return " ".join(b for b in bits if b)

        if intent == "impact":
            d = peel(self.send_a2a("grow_agent", "analyze_deficit",
                                   {"plant_id": plant_id}, timeout=90))
            if d.get("error") or not d.get("deficit_periods"):
                return d.get("finding") or d.get("error") or None
            bits = [d.get("consequence") or ""]
            # The refusal is part of the answer, not a footnote to it.
            if d.get("why_no_number"):
                bits.append("No yield figure: " + d["why_no_number"])
            if d.get("what_would_make_it_answerable"):
                bits.append(d["what_would_make_it_answerable"])
            return " ".join(b for b in bits if b)

        if intent == "feed":
            rec = peel(self.send_a2a("grow_agent", "recommend_feed",
                                     {"plant_id": plant_id}, timeout=120))
            if not rec:
                return None
            cur, sug = rec.get("current") or {}, rec.get("suggested") or {}
            unit, litres = rec.get("unit") or "ml", rec.get("reservoir_liters")
            q = f" per {litres:g}L" if litres else ""
            bits = []
            if cur:
                bits.append("In the reservoir now: "
                            + ", ".join(f"{k} {v}{unit}" for k, v in cur.items()) + q + ".")
            if sug and sug != cur:
                bits.append("Suggested: "
                            + ", ".join(f"{k} {v}{unit}" for k, v in sug.items()) + q + ".")
            if rec.get("action"):
                bits.append(str(rec["action"])[:400])
            return " ".join(bits) or None
        return None

    def _plant_from_prompt(self, prompt):
        """Which plant this prompt is about, or None for "the grow" generally.

        Without this, "how is the aloe" routed to Grow Agent correctly and then
        asked it about the cannabis, because the branch hardcoded current_plant.
        Reaching the right agent is only half of routing."""
        by_ordinal = self._ordinal_from_prompt(prompt)
        if by_ordinal:
            return by_ordinal
        lp = (prompt or "").lower()
        hit = None
        for term, pid in self._known_plant_terms().items():
            if pid and re.search(r"\b" + re.escape(term) + r"\b", lp):
                # Prefer the most specific term that matched.
                if hit is None or len(term) > hit[0]:
                    hit = (len(term), pid)
        return hit[1] if hit else None

    def _format_response(self, task, result, sender):
        if result is None:
            return "The request did not return a result."
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            if "error" in result:
                return f"Error: {result['error']}"
            if task == "assess_care":
                inner = result.get("result", {}) if isinstance(result, dict) else {}
                inner = inner.get("result", inner) if isinstance(inner, dict) else {}
                if not isinstance(inner, dict) or not inner:
                    return "I could not get a reading on that plant."
                name = inner.get("profile") or inner.get("species") or "that plant"
                bits = [f"Your {name}:"]
                if inner.get("signs"):
                    bits.append(inner.get("assessment") or "")
                else:
                    bits.append(f"Nothing flags a care problem right now.")
                t = inner.get("temperature") or {}
                if t.get("note"):
                    bits.append(t["note"])
                if inner.get("action") and "No urgent" not in inner["action"]:
                    bits.append(inner["action"])
                c = inner.get("care") or {}
                if c.get("water"):
                    bits.append(f"Watering: {c['water']}")
                return " ".join(b for b in bits if b)

            if task == "resource_reclaim":
                def _peel(x, depth=3):
                    for _ in range(depth):
                        if isinstance(x, dict) and "result" in x:
                            x = x["result"]
                        else:
                            break
                    return x if isinstance(x, dict) else {}
                lines = []
                mem = _peel(result.get("memory"))
                if mem:
                    total = mem.get("swarm_total_mb")
                    avail = mem.get("system_available_mb")
                    if total:
                        lines.append(f"The swarm is holding {total:.0f} MB; "
                                     f"{avail:.0f} MB free on the machine.")
                    for f in (mem.get("findings") or [])[:3]:
                        lines.append(f)
                    idle = mem.get("idle_services") or []
                    if idle:
                        lines.append("")
                        for c in idle[:8]:
                            # services/evaluation/service.py - the meaningful
                            # part is the directory, not the filename, which is
                            # "service" for every one of them.
                            parts = [x for x in c.get("cmd", "?").split() if "/" in x or x.endswith(".py")]
                            path = parts[-1] if parts else c.get("cmd", "?")
                            seg = [x for x in path.replace(".py", "").split("/") if x]
                            name = seg[-2] if len(seg) > 1 and seg[-1] == "service" else seg[-1]
                            lines.append(f"  - {name}: {c.get('rss_mb','?')} MB - {c.get('reason','idle')}")
                        # Stopping a process is an action, not a report - it goes
                        # through authorisation like any other actuation.
                        lines.append(f"\nThat is {mem.get('reclaimable_mb', 0):.0f} MB reclaimable. "
                                     "Stopping them needs your OK - say the word and I'll "
                                     "route it through authorisation.")
                    elif total:
                        lines.append("Nothing is sitting idle right now.")
                disk = _peel(result.get("disk"))
                if disk:
                    freed = disk.get("freed_mb") or disk.get("reclaimed_mb")
                    lines.append(f"Disk cleanup: {freed} MB reclaimed." if freed
                                 else "Disk cleanup ran; nothing significant to reclaim.")
                return "\n".join(lines) if lines else "Nothing reclaimable was found."

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
                # Narration has to carry the confidence, not just the verdict.
                # "Looks healthy" was being said over a low-confidence result
                # reached by detecting nothing - which reads to the grower as a
                # clean bill of health when the system in fact could not assess
                # the plant at all.
                conf = (inner.get("confidence") or "").lower()
                if classification == "problem":
                    text = f"Heads up - {text}"
                elif classification == "productive" and conf == "low":
                    text = f"I couldn't really assess this one. {text}"
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
                    # Always state the unit and what the dose is measured against -
                    # a bare "FloraMicro 3.0" is ambiguous by ~3.79x.
                    unit = nutrients.get("unit") or ""
                    n_str = ", ".join(f"{k} {v}{unit}" for k, v in nutrients["nutrients"].items())
                    basis, litres = nutrients.get("basis"), nutrients.get("reservoir_liters")
                    if basis == "total" and litres:
                        qualifier = f" per {litres:g}L reservoir"
                    elif basis == "per_liter":
                        qualifier = " per litre"
                    elif basis == "per_gallon":
                        qualifier = " per gallon"
                    else:
                        qualifier = ""
                    lines.append(f"Current feed: {n_str}{qualifier}.")

                # Only round up the rest of the garden when the question was
                # about the garden. Asked about one plant, answer about that
                # plant - a roundup buries the answer that was actually wanted.
                # The dashboard is where everything lives. In chat, a question
                # gets an answer - the roundup only appears when the roundup is
                # what was asked for.
                if isinstance(result, dict) and result.get("roundup"):
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

                plant_id = metadata.get("plant_id", "current_plant")

                # A measurement sent with the photos is logged BEFORE they are
                # looked at. Vision is slow and can time out; the numbers must
                # not be lost with it.
                reading_text = None
                reading = self._extract_reading(prompt or "")
                if reading:
                    self.log(f"Reading found alongside photos - logging first: {reading}")
                    rr = self._log_reading(reading)
                    reading_text = self._format_response("log_reading", rr, "grow_agent")

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
                return {"result": text, "evidence": {"photos": results, "reading": reading}}

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

            # --- Reclaiming resources: memory, disk, or unspecified ---
            #
            # "Recover 39 mb idle space" matched none of the old keywords - it has
            # "space" but not "free up space" or "disk space" - so it fell through
            # to the generic reasoner, which asked a CODE model about system
            # administration and got back invented Windows and macOS instructions.
            # The 39 MB it referred to was RAM held by idle services, a figure this
            # system produced itself via analyze_memory_usage.
            #
            # Two failures, both fixed here: the vocabulary was too narrow, and
            # there was no memory branch at all, only a disk one.
            lowered_p = prompt.lower()
            _RECLAIM = ("clean up", "cleanup", "free up", "clear", "reclaim", "recover",
                        "release", "reduce", "shrink", "idle", "unused", "wasted",
                        "wasting", "waste", "hogging", "eating", "bloat", "trim",
                        "not being used", "doing nothing")
            # Bare "memory" cannot trigger this: in this system it also means the
            # Memory Service and Hermes storage, so "store that in memory" must not
            # be read as a request to free RAM. Require RAM-specific phrasing.
            _MEMORY = ("ram", "memory usage", "memory footprint", "wasting memory",
                       "eating memory", "hogging memory", "free memory", "resident",
                       "memory hog", "using memory", "much memory", "footprint")
            _DISK = ("disk", "storage", "drive", "logs", "docker")
            # Things that HOLD a resource - reclaim language plus one of these is
            # a resource question even when no unit is named.
            _HOLDERS = ("service", "services", "process", "processes", "agent", "agents")
            has_reclaim = any(k in lowered_p for k in _RECLAIM)
            if has_reclaim and (any(k in lowered_p for k in _MEMORY + _DISK + _HOLDERS)
                                or "space" in lowered_p):
                wants_mem = any(k in lowered_p for k in _MEMORY) or \
                            any(k in lowered_p for k in _HOLDERS)
                wants_disk = any(k in lowered_p for k in _DISK)
                # "space" alone is genuinely ambiguous between RAM and disk, so
                # report both rather than guessing and acting on the wrong one.
                if not wants_mem and not wants_disk:
                    wants_mem = wants_disk = True
                gathered = {}
                if wants_mem:
                    self.log("Resource reclaim (memory) - delegating to maintenance_agent")
                    gathered["memory"] = self.send_a2a("maintenance_agent", "analyze_memory_usage", {}, timeout=90)
                if wants_disk:
                    self.log("Resource reclaim (disk) - delegating to maintenance_agent")
                    gathered["disk"] = self.send_a2a("maintenance_agent", "run_cleanup_routine", {}, timeout=90)
                text = self._format_response("resource_reclaim", gathered, "maintenance_agent")
                return {"result": text, "evidence": gathered}

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
            _lp = prompt.lower()
            if (any(re.search(t if "\\b" in t or "?" in t else r"\b" + t, _lp)
                    for t in GROW_TERMS)
                    or any(re.search(r"\b" + re.escape(t) + r"\b", _lp)
                           for t in self._known_plant_terms())):
                which = self._plant_from_prompt(prompt)
                self.log(f"User asking about the grow/plant - delegating to grow_agent"
                         + (f" (plant: {which})" if which else ""))
                if which and which != "current_plant":
                    care = self.send_a2a("grow_agent", "assess_care",
                                         {"plant_id": which, "description": prompt}, timeout=120)
                    text = self._format_response("assess_care", care, "grow_agent")
                    return {"result": text, "evidence": {"plant_id": which, "care": care}}
                # One situation, ordered by the question. Tried first because
                # it answers any angle on the reservoir without needing a
                # pattern per phrasing.
                if re.search(r"ppm|feed|nutrient|reservoir|raise|increase|\b\d{3,4}\b|"
                             r"why|when|how much|what'?s stopping|blocked", _lp) and \
                   re.search(r"\?|\b(why|when|how|what|can|should|do i|is it)\b", _lp):
                    ans = self._answer_from_situation(which or "current_plant", prompt)
                    if ans and len(ans) > 60:
                        return {"result": ans,
                                "evidence": {"answered_as": "situation", "plant": which}}

                intent = self._grow_intent(prompt)
                if intent != "status":
                    ans = self._answer_grow_question(intent, which or "current_plant", prompt)
                    # A question can be about timing AND about a number. "When do
                    # you recommend pushing to 800" matched the schedule intent
                    # and answered without ever mentioning 800, because the
                    # intents are mutually exclusive and the question was not.
                    if ans and intent not in ("dosing",):
                        extra = self._target_note(which or "current_plant", prompt)
                        if extra:
                            ans = extra + " " + ans
                    if ans:
                        return {"result": ans, "evidence": {"intent": intent, "plant": which}}
                # A question that matched no intent still deserves an answer
                # rather than a status card. Only a bare request for status
                # ("how is plant one") falls through to the summary.
                elif re.search(r"\?|\b(why|when|should|can|could|do i|did we|did i|is it|is now|"
                               r"how much|how many|how do|what if|what about|would|recommend|"
                               r"instead of|rather than)\b",
                               prompt.lower()):
                    ans = self._answer_from_situation(which or "current_plant", prompt) \
                          or self._compose_grow_answer(which or "current_plant", prompt)
                    if ans:
                        return {"result": ans, "evidence": {"intent": "composed", "plant": which}}
                response = self.send_a2a("grow_agent", "get_status", {})
                history = self.send_a2a("grow_agent", "get_grow_history", {"plant_id": "current_plant"})
                # Explicitly asking about the whole garden is the only thing
                # that opens it up.
                _round = (not which) and bool(re.search(
                    r"\b(all|every|everything|each|both|garden|plants|roundup|round-?up|"
                    r"overview|status of (the )?grow|how is (the |my )?grow)\b", prompt.lower()))
                text = self._format_response("grow_status",
                                             {"status": response, "history": history,
                                              "roundup": _round}, "grow_agent")
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

            # --- Before the generic model: try the domain agent ---
            #
            # The keyword gate above was the actual fault five separate times
            # today - DWC read as "Direct Water Cooker", a memory-reclaim request
            # sent to a code model, a strain name explained as African folklore,
            # a logging-cadence question answered with log rotation, and "how
            # much do I add to reach 800" answered as "add 200". Each time the
            # fix was another keyword, and each time the grower found a phrasing
            # nobody had thought of - which is what people talking normally do.
            #
            # So the default inverts. This is a grow assistant: an unmatched
            # QUESTION goes to the domain agent first, and only reaches the
            # generic model if the domain has nothing to say. A composed answer
            # that turns out not to fit is recoverable; a code model confidently
            # doing arithmetic on a number it does not understand is not.
            if re.search(r"\?|\b(why|when|should|can|could|do i|did we|did i|is it|is now|"
                         r"how much|how many|how do|what if|what about|would|recommend|"
                         r"instead of|rather than|is my|are my|my plant)\b", prompt.lower()):
                try:
                    which2 = self._plant_from_prompt(prompt)
                    ans = (self._answer_from_situation(which2 or "current_plant", prompt)
                           or self._compose_grow_answer(which2 or "current_plant", prompt))
                    if ans and len(ans) > 40:
                        self.log("Unmatched question - answered from the grow domain")
                        return {"result": ans,
                                "evidence": {"intent": "composed_fallback", "plant": which2}}
                except Exception as e:
                    self.log(f"grow fallback failed, using generic model: {e}")

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
