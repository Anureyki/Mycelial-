"""Anansi's voice: a policy layer that cannot touch the facts.

The personality is configuration (`config/anansi_voice.json`), not code, so it
can evolve without an agent changing. What lives here is the machinery that
applies it - and, more importantly, the machinery that proves it did no damage.

THE GUARANTEE. Voice may reorder, join and open. It may never add or remove a
fact. That is enforced rather than intended: every number, date, unit, currency
amount, citation and percentage in the input is extracted before the telling and
checked afterwards. If one is lost, altered or invented, the telling is
DISCARDED and the plain text is returned. Humour that costs a decimal point is
not humour, it is a defect.

Deliberately deterministic. Every fabrication this system has produced came from
handing a small model a gap and room to fill it. Nothing here calls a model, so
there is no gap and no room.

Anansi narrates a determination; he never makes one.
"""
import json
import os
import random
import re

CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "config", "anansi_voice.json")

# What counts as a fact for the preservation check. Deliberately greedy: it is
# far better to refuse a good telling than to ship one that quietly dropped a
# dose, a date or a section number.
FACT_PATTERNS = [
    r'\$\s?\d[\d,]*(?:\.\d+)?',                       # money
    r'\b\d{4}-\d{2}-\d{2}\b',                         # ISO dates
    r'§+\s*[\d.\-]+[A-Za-z]?(?:\([a-z0-9]+\))*',      # section citations
    r'\b\d+(?:\.\d+)?\s?(?:ml|mL|L|l|g|kg|ppm|pH|°C|°F|%|W|cm|mm|in)\b',
    r'\b\d+(?:\.\d+)?\b',                             # bare numbers, last
]
_FACTS = re.compile("|".join(f"(?:{p})" for p in FACT_PATTERNS))


def facts_in(text):
    """Multiset of factual tokens. A multiset, not a set: 'two 5.9 readings'
    losing one of them is a real loss even though the value still appears."""
    out = {}
    for m in _FACTS.finditer(text or ""):
        k = m.group(0).strip().lower().replace(" ", "")
        out[k] = out.get(k, 0) + 1
    return out


class Voice:
    def __init__(self, log=None):
        self.log = log or (lambda *a: None)
        self._cfg = None
        self._mtime = None

    @property
    def cfg(self):
        """Reloaded when the file changes, so the personality can be tuned
        without restarting an agent."""
        try:
            m = os.path.getmtime(CONFIG)
        except OSError:
            return self._cfg or {}
        if self._cfg is None or m != self._mtime:
            try:
                with open(CONFIG) as fh:
                    self._cfg = json.load(fh)
                self._mtime = m
            except Exception as e:
                self.log(f"voice: config unreadable, running plain: {e}")
                self._cfg = {}
        return self._cfg

    # -- register ---------------------------------------------------------
    def register_for(self, text, hint=None):
        """How serious is this? Most serious match wins - a legal answer that
        happens to mention a plant is still a legal answer."""
        cfg = self.cfg
        regs = cfg.get("registers", {})
        if hint and hint in regs:
            return hint, regs[hint]
        low = (text or "").lower()
        for rule in cfg.get("register_rules", []):
            if any(t in low for t in rule.get("any", [])):
                r = rule["register"]
                return r, regs.get(r, {"voice": 0.5, "dominant": "clarity"})
        return "low_stakes", regs.get("low_stakes", {"voice": 1.0,
                                                     "dominant": "personality"})

    # -- situation --------------------------------------------------------
    def situation(self, lead):
        low = lead.lower()
        if "in the way" in low or "clears when" in low or low.startswith("not yet"):
            return "blocked"
        if low.startswith("nothing is blocking") or "go ahead" in low:
            return "clear"
        if re.search(r"\bon 20\d\d-\d\d-\d\d\b", lead):
            return "history"
        if re.search(r"\badd\b.*\d+(\.\d+)?\s?ml", low):
            return "action"
        if low.startswith("when:") or low.startswith("no condition"):
            return "timing"
        if any(w in low for w in ("unsupported", "insufficient", "undetermined",
                                  "contested", "unverified", "untested")):
            return "unresolved"
        if re.search(r"\bworks out at\b|\bday\(s\)\b|\bcomes to\b|\d+\s*ppm/day", low):
            return "estimate"
        # "steady" is not a default. Its openers - "All quiet", "Everything is
        # sitting where it should" - ASSERT A STATE, and Anansi has no
        # authority to determine that anything is fine. It got applied to a
        # contestable $550 obligation, which is exactly the boundary this
        # architecture exists to hold. It must be earned by the payload
        # actually saying so.
        if any(w in low for w in ("nothing needs", "no action", "all within",
                                  "nothing to do", "no issues", "healthy")):
            return "steady"
        return "plain"

    # -- the telling ------------------------------------------------------
    def tell(self, text, hint=None):
        if not text or not isinstance(text, str) or len(text) < 40:
            return text
        if text.lstrip().startswith(("Got it", "I couldn't", "I could not",
                                     "Hold", "Not yet,")):
            return text
        cfg = self.cfg
        if not cfg:
            return text

        name, reg = self.register_for(text, hint)
        strength = float(reg.get("voice", 0.5))

        parts = [p.strip() for p in re.split(r'(?<=[.!?])\s+', text) if p.strip()]
        if len(parts) < 2:
            return text
        kind = self.situation(parts[0])

        # Deterministic per input, so the same question does not reword itself
        # each time it is asked. That reads as instability, not personality.
        rnd = random.Random(hash(text) & 0xffff)

        openers = cfg.get("openers", {}).get(kind) or []
        # Below a third strength the situation is too serious to be introduced
        # by anything but the facts themselves.
        opener = rnd.choice(openers) if (openers and strength >= 0.34) else ""

        # A sentence carrying a fact is never dropped, however redundant the
        # opener makes it read. The old code dropped the "Not yet - 2 thing(s)"
        # lead and took the count with it; the guarantee caught it, which is
        # the guarantee doing its job, but the fix belongs here.
        if (kind == "blocked" and opener and re.match(r'^not yet\b', parts[0], re.I)
                and not facts_in(parts[0])):
            parts = parts[1:]

        conns = cfg.get("connectives", [])
        # Connectives are the main carrier of "spoken, not printed", so they
        # thin out as the register gets more serious rather than vanishing.
        every = 3 if strength >= 0.8 else 4 if strength >= 0.5 else 0
        body = []
        for i, p in enumerate(parts):
            if (every and conns and i and i % every == 0 and len(p) > 30
                    and not re.match(r'^(A|An|The|This|That|It|They|Clears|And|At)\b', p)):
                body.append(rnd.choice(conns) + p[0].lower() + p[1:])
            else:
                body.append(p)
        told = self._despecify(" ".join(body))
        told = ((opener + " ") if opener else "") + told

        # THE GUARANTEE. Anything lost or invented and the telling is thrown
        # away. Note the check is against the ORIGINAL text, not against the
        # post-processed one, so a substitution that eats a number is caught.
        before, after = facts_in(text), facts_in(told)
        if before != after:
            lost = {k: before[k] - after.get(k, 0) for k in before
                    if before[k] != after.get(k, 0)}
            gained = {k: after[k] for k in after if k not in before}
            self.log(f"voice: telling DISCARDED, facts changed "
                     f"(lost={lost} gained={gained}) - returning plain text")
            return text
        return told

    @staticmethod
    def _despecify(told):
        """Machine phrasing out, spoken phrasing in. Substitutions only - no
        clause here may introduce a word that carries a fact."""
        told = re.sub(r'(Clears when:\s+)([A-Z])',
                      lambda m: "That lifts once " + m.group(2).lower(), told)
        told = re.sub(r'(clears when:\s+)([A-Z])',
                      lambda m: "which lifts once " + m.group(2).lower(), told)
        told = re.sub(r'\bWhen:\s+', '', told)
        told = re.sub(r'\bAnd:\s+([A-Z])', lambda m: "and " + m.group(1).lower(), told)
        told = re.sub(r'\.\s+and ', ', and ', told)
        told = told.replace("Clears when:", "That lifts once")
        told = told.replace("clears when:", "which lifts once")
        # Self-contained, because this sentence does not always follow the
        # opener that gave "them" a referent. Mid-paragraph, "There are 2 of
        # them." dangles - it read as a non-sequitur straight after a ppm
        # figure on the dashboard.
        told = re.sub(r'\bNot yet - (\d+) thing\(s\) in the way\.',
                      lambda m: (f"There is {m.group(1)} thing in the way."
                                 if m.group(1) == "1" else
                                 f"There are {m.group(1)} things in the way."),
                      told)
        return told.replace(" thing(s)", " things")

    # -- the trickster ----------------------------------------------------
    def contradiction(self, claim_said, observed_said, resolution=None):
        """CLAIM -> OBSERVATION -> CONTRADICTION -> EXPLANATION.

        Both sides must be supplied as facts. Anansi exposes a contradiction;
        he does not go hunting for irony that is not in the payload."""
        if not claim_said or not observed_said:
            return None
        cfg = self.cfg.get("contradiction", {})
        rnd = random.Random(hash(claim_said + observed_said) & 0xffff)
        res = resolution or (rnd.choice(cfg.get("resolutions", []))
                             if cfg.get("resolutions") else
                             "The system went with what it could check.")
        frame = cfg.get("frame", "{claim_said}. {observed_said}. {resolution}")
        out = frame.format(claim_said=claim_said.rstrip("."),
                           observed_said=observed_said.rstrip("."),
                           resolution=res)
        before = facts_in(claim_said + " " + observed_said)
        after = facts_in(out)
        for k, n in before.items():
            if after.get(k, 0) < n:
                self.log("voice: contradiction telling dropped a fact - plain")
                return f"{claim_said} {observed_said}"
        return out
