"""Inward translation: what does this person actually want?

Routing used to be a word count. Each agent declared a list of terms, Boss
counted regex hits, and whoever matched most won. That is why "How's the system
today" reached a code model - it contained none of the six phrasings anybody
had thought to write down, and "adding the missing sentence" fixes the sentence
and never the class.

A person should be able to speak plainly. So intent is RESOLVED rather than
matched, by asking the reasoning model which department owns the request.

WHY THIS IS SAFE, WHEN NOTHING ELSE HERE LETS A MODEL DECIDE ANYTHING.

The model picks a ROUTE, not an ANSWER. Three properties make that different in
kind from letting one generate a fact:

  1. CLOSED SET. It chooses from the live registry - departments that actually
     exist right now. Anything it returns that is not in that set is discarded
     as UNCLEAR. It cannot invent a department any more than it can invent a
     port number.
  2. RECOVERABLE. A wrong route means the wrong department is asked, and that
     department answers that it does not own the question. Nobody is told
     something false; they are told the wrong thing was asked. A fabricated
     fact has no equivalent recovery.
  3. NO CONTENT CROSSES. This returns an agent id and nothing else. The model
     never sees, summarises or rewrites the answer - the domain agent produces
     that, and Anansi narrates it under the voice guarantee.

The word count survives as a FALLBACK for when inference is down, because a
degraded route beats no route. Both are computed and logged when they disagree,
which is the only way to find out whether this is actually better.
"""
import json
import re
import requests

INFERENCE = "http://localhost:8005/reason"
UNCLEAR = "UNCLEAR"


class IntentResolver:
    def __init__(self, log=None, roster_fn=None):
        self.log = log or (lambda *a: None)
        self._roster_fn = roster_fn

    def roster(self):
        """Departments that exist right now, described by what they THEMSELVES
        declare. Nothing about any domain is written down here - a new agent
        becomes routable by starting up, which is the whole point of the
        inversion."""
        if self._roster_fn:
            try:
                return self._roster_fn() or {}
            except Exception as e:
                self.log(f"intent: roster unavailable: {e}")
        return {}

    @staticmethod
    def _clean(term):
        """Regex scaffolding out. Order matters: strip the \\b anchors BEFORE
        removing backslashes, or '\\bgaap\\b' becomes 'bgaapb' and the model is
        shown a word that does not exist."""
        w = str(term).replace("\\b", " ")
        w = re.sub(r'[\\^$*+?()\[\]{}|]', '', w)
        return re.sub(r'\s+', ' ', w).strip()

    def _brief(self, terms, caps=None, limit=16):
        """What a department handles, in its own declarations.

        CAPABILITIES FIRST. Routing terms were written to be matched, not read:
        grow_agent's first fourteen are 'stage, how old, taproot, cotyledon,
        photo...' - nothing about water, reservoir or nutrients, so a request
        about a net pot looked like nobody's. Its capability names say
        log_reading, adjust_nutrients, log_water_change, which is what the
        department actually is."""
        out = []
        for c in (caps or [])[:limit]:
            w = str(c).replace("_", " ").strip()
            if w and w not in out:
                out.append(w)
        for t in (terms or []):
            if len(out) >= limit:
                break
            w = self._clean(t)
            if w and len(w) > 2 and w not in out:
                out.append(w)
        return ", ".join(out)

    def resolve(self, prompt, timeout=25):
        """-> (agent_id or UNCLEAR, why). Never raises; never invents an id."""
        roster = self.roster()
        if not roster or not prompt:
            return UNCLEAR, "no roster available"
        ids = sorted(roster)
        lines = []
        for aid, d in sorted(roster.items()):
            terms = d.get("terms") if isinstance(d, dict) else d
            caps = d.get("capabilities") if isinstance(d, dict) else None
            lines.append(f"- {aid}: {self._brief(terms, caps)}")
        ask = (
            "You are routing one request to exactly one department. "
            "Choose the department whose subject matter the request belongs to.\n\n"
            "Departments and the subjects each one handles:\n"
            + "\n".join(lines) +
            "\n\nRequest: " + str(prompt).strip() +
            "\n\nAnswer with the department id alone, exactly as written above. "
            "If the request does not clearly belong to any of them, answer "
            f"{UNCLEAR}. Answer with one word and nothing else."
        )
        try:
            r = requests.post(INFERENCE, json={"prompt": ask, "capability": "reasoning",
                                               "temperature": 0}, timeout=timeout)
            data = r.json() if r.ok else {}
        except Exception as e:
            self.log(f"intent: inference unreachable: {e}")
            return UNCLEAR, "inference unreachable"
        if not data.get("success"):
            return UNCLEAR, "inference failed"

        raw = (data.get("result") or "").strip()
        # Validate against the CLOSED SET. A model that answers with prose, or
        # with a department that does not exist, has answered nothing.
        token = re.sub(r'[^a-z0-9_]', '', raw.lower().split()[0]) if raw.split() else ""
        if token in roster:
            return token, f"resolved from {raw[:60]!r}"
        for aid in ids:                      # tolerate "the grow_agent." etc
            if aid in raw.lower():
                return aid, f"resolved from {raw[:60]!r}"
        return UNCLEAR, f"model said {raw[:60]!r}, which is not a department"

    def resolve_with_fallback(self, prompt, keyword_pick=None):
        """Intent first, word count second. Disagreements are logged, because
        that is the only evidence for whether resolving beats matching."""
        pick, why = self.resolve(prompt)
        if pick != UNCLEAR:
            if keyword_pick and keyword_pick != pick:
                self.log(f"intent: resolved={pick} but keywords said "
                         f"{keyword_pick} - went with intent ({why})")
            return pick, why
        if keyword_pick:
            self.log(f"intent: unresolved ({why}); falling back to keyword "
                     f"match {keyword_pick}")
            return keyword_pick, f"keyword fallback after: {why}"
        return UNCLEAR, why
