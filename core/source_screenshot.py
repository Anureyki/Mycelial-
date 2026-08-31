"""Judging a screenshot by what it says, never by where it came from.

The principal's own framing, and it is sharper than the usual advice: some
social-media cards are *fictional AI-generated fakes*; others are *AI-generated
notes from growers actually in operation* - second-hand, but with a practitioner
behind them. Those two look identical. Same layout, same font, same confident
voice, often the same generator.

SO THE IMAGE CANNOT DECIDE IT, AND NEITHER CAN THE ACCOUNT NAME.

That is the whole reason this module exists rather than a rule saying "distrust
Instagram". A 110-minute talk was once tagged `advocacy` from its title and
channel and turned out to argue the opposite; the same reflex applied to a grow
card would discard the one careful source in the pile. The test has to be what
the CONTENT does, because content is the only thing actually in evidence.

What can genuinely be tested from a card:

  - whether it discloses its own limits. A card labelling its own curve
    "CONCEPTUAL - NOT MEASURED DATA" has done something a fake has no reason to
    do. It is the strongest single signal available and it costs the author
    credibility they could have kept.
  - whether it holds together. Interleaved unit scales, figures that contradict
    the caption above them, a number that cannot be true given another number.
  - whether it explains a mechanism or only asserts an outcome.
  - whether anything in it can be opened and checked.
  - whether it overclaims - always, never, guaranteed, proven.

What CANNOT be determined, and is reported as such rather than guessed:

  - whether a real operator is behind it,
  - whether the numbers were measured,
  - whether the author has the experience implied.

Those stay `unknown`. This module RECOMMENDS a standing and never sets one -
`stance`, `source_class` and `evidence_kind` are filled by reading, and a
recommendation carrying its own evidence is a reading a person can check, which
is the point.
"""

import re

# Each marker: (name, pattern, weight, why it matters). Weight is a direction
# and a magnitude, never a probability - nothing here is calibrated against
# anything, and pretending otherwise would be the exact overclaim being tested
# for.
POSITIVE = (
    ("self_disclosing_conceptual",
     r"\b(conceptual|illustrative|not measured|not actual data|for illustration|"
     r"representative only|approximate|schematic)\b",
     3,
     "Labels its own figure as conceptual rather than measured. A fabricated "
     "card has no reason to give away that credibility."),
    ("scope_bounded",
     r"\b(varies by|depends on|depending on|will differ|not the only factor|"
     r"other variables|your (?:results|mileage)|in (?:my|our) (?:room|tent|setup))\b",
     2,
     "Limits its own claim rather than generalising it."),
    ("mechanism_stated",
     r"\b(because|which is why|the reason|due to|caused by|as a result of|"
     r"this happens when|the mechanism)\b",
     2,
     "Explains why, not only what. An assertion with a mechanism can be argued "
     "with; one without can only be believed."),
    ("distinguishes_confusables",
     r"\b(is not the same as|does not (?:automatically )?mean|"
     r"≠|!=|is not|rather than)\b",
     2,
     "Separates two things commonly conflated - the shape of a real correction."),
    ("operator_marker",
     r"\b(i (?:ran|run|tried|tested|noticed|lost|dried|pulled|harvested)|"
     r"my (?:last|first|current) (?:run|grow|batch|harvest|crop)|"
     r"we (?:ran|tested|observed))\b",
     2,
     "First-person operational detail. Weak on its own - it is trivially "
     "fabricated - but it is what a practitioner's note looks like."),
    ("denies_a_threshold",
     r"\b(?:do(?:n'?t| not) suddenly|no (?:magic|single|exact) (?:number|threshold|"
     r"temperature|point)|not a (?:switch|threshold|cliff)|"
     r"do(?:n'?t| not) (?:just )?(?:turn on|kick in)|is a (?:rate|continuum|spectrum))\b",
     3,
     "States that a phenomenon is continuous rather than triggered at a "
     "threshold. This is the shape of a correction to a popular error, and it "
     "costs the author the simpler story - which is why it counts for so much."),
    ("checkable_reference",
     r"\b(\d+\s*(?:u\.?s\.?c\.?|c\.?f\.?r\.?)|§\s*\d|"
     r"et al\.|doi:|journal of|ASTM|ISO \d)",
     3,
     "Points at something that can be opened. A citation is not authority until "
     "the text is in hand, but a card that offers one can be checked at all."),
)

NEGATIVE = (
    # AN ABSOLUTE ABOUT A PROPERTY IS NOT AN OVERCLAIM.
    #
    # The first version of this pattern scored "TERPENES ARE ALWAYS VOLATILE"
    # as the worst thing on a card - and that sentence is the best thing on it.
    # It is not a guarantee about an outcome; it is a statement that there is
    # no threshold, which is the opposite of overclaiming and is precisely the
    # error the card exists to correct.
    #
    # So an absolute counts only when it attaches to a RESULT: what you will
    # get, what works, what is better. "always volatile" is a property.
    # "always gives louder flower" is a promise.
    ("absolute_claim",
     r"\b(?:always|never|every time|guaranteed|100%)\b[^.]{0,60}?"
     r"\b(?:get|gives?|works?|produces?|yields?|results?|better|best|"
     r"win|beat|fix|cure|solve)\b"
     r"|\b(?:guaranteed|proven fact|scientifically proven|the only way|"
     r"all growers|no exceptions|works every time)\b",
     -3,
     "An absolute attached to an OUTCOME. Real process claims have conditions, "
     "and a promise without any is either not thinking about them or hiding "
     "them. An absolute about a physical PROPERTY - 'always volatile' - is a "
     "different thing and is not scored here."),
    ("authority_without_source",
     r"\b(studies show|science says|research proves|experts agree|it'?s been proven|"
     r"studies have shown)\b(?![^.]{0,80}(?:et al|doi|journal|§|\d{4}\)))",
     -3,
     "Invokes evidence without naming any. The form of a citation with none of "
     "the function."),
    ("unfalsifiable",
     r"\b(trust me|you just know|if you know you know|iykyk|real (?:growers|ones) know|"
     r"they don'?t want you to know|what they won'?t tell you)\b",
     -3,
     "Nothing here could turn out to be wrong. An assertion no procedure can "
     "test cannot be relied on - the claim pipeline calls this `untestable` and "
     "reports it as a reason for suspicion."),
    ("sales_pressure",
     r"\b(link in bio|dm me|use code|shop now|limited time|buy (?:now|here)|"
     r"my (?:course|program|ebook))\b",
     -2,
     "The author has an interest in the answer."),
)

# Unit families that must not be mixed on one axis without conversion.
_TEMP_F = re.compile(r"(\d{2,3})\s*°?\s*F\b", re.I)
_TEMP_C = re.compile(r"(\d{1,3})\s*°?\s*C\b", re.I)


def _internal_consistency(text):
    """Contradictions the card makes with itself. Findings, not scores."""
    found = []

    # Fahrenheit and Celsius on the same scale, unconverted. This is the fault
    # in the terpene thermometer: a column reading 40..400 F beside a column
    # reading 4..260 C, with lines drawn between them as though they were one
    # axis. The ORDERING survives that; the numbers do not.
    f = [int(x) for x in _TEMP_F.findall(text)]
    c = [int(x) for x in _TEMP_C.findall(text)]
    if len(f) >= 3 and len(c) >= 3:
        overlap = set(f) & set(c)
        if overlap:
            found.append({
                "marker": "mixed_unit_scale",
                "detail": (f"Both °F and °C values appear, and {len(overlap)} "
                           f"number(s) occur in both: {sorted(overlap)[:6]}. A figure "
                           f"reading as one scale beside a figure reading as the other "
                           f"cannot be taken as data. Use the ORDERING, not the values."),
            })

    # A card that says volatility is not boiling point and then prints a table
    # of boiling points is arguing with itself - worth surfacing rather than
    # scoring, because which half is right depends on what is being taken from it.
    if re.search(r"volatility\s*(?:≠|is not|!=)\s*boiling", text, re.I) and \
            re.search(r"boiling point", text, re.I):
        found.append({
            "marker": "self_contradicting_frame",
            "detail": ("States that volatility is not boiling point while presenting "
                       "boiling-point-shaped figures. The caveat is correct and the "
                       "figures invite exactly the reading it warns against."),
        })
    return found


def assess(text, source_hint=None):
    """Read a card's text and report what is testable about it.

    Returns markers WITH the line that triggered each, because a classification
    whose evidence is not quoted is a classification nobody can check.
    """
    # NORMALISE TYPOGRAPHY BEFORE MATCHING.
    #
    # OCR returns the curly apostrophe the card was set in, and "don\u2019t"
    # does not match a pattern written with "don't". The best line on the
    # volatility card - *they don't suddenly turn on at a certain temperature* -
    # was invisible to `denies_a_threshold` for exactly that reason, which is
    # the same failure as a routing term written with a backspace instead of a
    # word boundary: a pattern that can never match, and nothing says so.
    t = str(text or "")
    for bad, good in (("\u2019", "'"), ("\u2018", "'"), ("\u201c", '"'),
                      ("\u201d", '"'), ("\u2013", "-"), ("\u2014", "-"),
                      ("\u2260", "!="), ("\u00a0", " ")):
        t = t.replace(bad, good)
    t = re.sub(r"[ \t]+", " ", t)
    low = t.lower()
    if len(t.strip()) < 40:
        return {"assessable": False,
                "reason": ("Too little text was read to assess anything. This says "
                           "nothing about the card - only that OCR did not get enough "
                           "off it."),
                "recommended": {"source_class": "unknown", "evidence_kind": "unknown"}}

    hits, score = [], 0
    for group, sign in ((POSITIVE, 1), (NEGATIVE, 1)):
        for name, pat, weight, why in group:
            m = re.search(pat, low, re.I)
            if not m:
                continue
            # Quote the line, per the rule that a standing field is filled by
            # reading and the reading is shown.
            start = max(0, low.rfind(".", 0, m.start()) + 1)
            end = low.find(".", m.end())
            quote = t[start:(end + 1 if end > 0 else min(len(t), m.end() + 120))].strip()
            hits.append({"marker": name, "weight": weight, "why": why,
                         "quoted": quote[:240]})
            score += weight

    inconsistencies = _internal_consistency(t)
    for i in inconsistencies:
        i["weight"] = -2
        score -= 2

    # A RECOMMENDATION, and the words matter. Nothing here concludes "authentic"
    # or "fake" - neither is determinable from a picture, and a field that says
    # so falsely is worse than a blank one.
    if score >= 5:
        rec_class, note = "expert_commentary", (
            "Reads as a considered note: it bounds its own claims and gives "
            "mechanisms. That makes it usable as REFERENCE - the floor to reason "
            "from - and never as evidence about this grow.")
    elif score >= 1:
        rec_class, note = "peer_account", (
            "Reads as a practitioner's account with some care in it. Second-hand "
            "either way: what someone reports having observed is not what this "
            "system observed.")
    elif score <= -4:
        rec_class, note = "unreliable", (
            "Overclaims, or invokes evidence it does not name, or cannot be "
            "tested. Not worth shelving. Recording WHY is worth more than the "
            "card was.")
    else:
        rec_class, note = "unknown", (
            "Nothing in the text moves it either way. Unknown is the honest "
            "value and is not the same as fine.")

    return {
        "assessable": True,
        "chars_read": len(t),
        "score": score,
        "markers": hits,
        "internal_inconsistencies": inconsistencies,
        "recommended": {
            "source_class": rec_class,
            "evidence_kind": "reported",
            "stance": "unknown",
            "note": note,
        },
        # The refusal, stated every time so it cannot be forgotten downstream.
        "cannot_determine": [
            "whether a real operator is behind this, or whether it is entirely "
            "fabricated - a fictional card and a practitioner's note rendered by "
            "the same generator are identical on the page",
            "whether any number in it was measured",
            "whether the author has the experience the card implies",
        ],
        "how_to_settle_it": (
            "Not by looking harder at the card. By testing one of its claims "
            "against this grow and recording what happened - at which point the "
            "observation outranks the card and the divergence is the finding."),
        "source_hint_recorded_not_scored": source_hint,
        **{"verification": pointers(t)},
    }

# ---------------------------------------------------------------------------
# What the card points at, and what following that pointer costs.
#
# The principal's observation, and it is the one that makes this module worth
# more than a credibility score: *"it'll either point to a court case, a
# statute, or an article."* A post is second-hand because it reports somebody
# else's finding - but THE THING IT POINTS AT MAY BE FIRST-HAND, and openable.
# The card is then a finding aid, not a source, and its own standing stops
# mattering the moment the pointer resolves.
#
# And the cost of following it is wildly different by domain, which is the
# other half of his point:
#
#   *"Grow cannot verify things unless it's tested... we won't know about that
#    until it's harvest time. But if there are statutes, research sources, laws,
#    CFRs, laboratories that post - information can be verified easily."*
#
# A legal card resolves in seconds against a corpus. A terpene-retention card
# resolves at harvest, months out, or costs a lab panel. Same second-hand
# status, opposite economics - so the assessment says WHERE EFFORT PAYS rather
# than only how much the card deserves.

POINTER_KINDS = (
    ("statute_or_regulation",
     r"\b\d+\s*(?:u\.?s\.?c\.?|c\.?f\.?r\.?)\b|§\s*\d|"
     r"\b(?:section|title)\s+\d+[\d.\-]*\b",
     "immediate",
     "Open it in the corpus, or acquire it with tools/ingest_law.py. Seconds to "
     "minutes, and it either says what the card claims or it does not."),
    ("court_decision",
     r"\b[A-Z][A-Za-z.'\-]+\s+v\.?\s+[A-Z][A-Za-z.'\-]+|"
     r"\b\d+\s+(?:U\.S\.|F\.\d?d|S\. ?Ct\.)\s+\d+",
     "immediate",
     "Look the case up. A decision either holds what is claimed or it does not, "
     "and a mention is not a holding."),
    ("published_article",
     r"\bet al\.|\bdoi:|\bjournal of\b|\b(?:19|20)\d{2}\s*\)|"
     r"\bpublished in\b|\bpreprint\b",
     "near_term",
     "Find the paper and read what it measured. Cheap in time, and often the "
     "abstract alone contradicts the card."),
    ("laboratory_result",
     r"\b(?:coa|certificate of analysis|lab (?:test|result|panel)|"
     r"terpene (?:panel|profile|test)|gc-?ms|hplc)\b",
     "costs_money",
     "A panel on this material would settle it directly. Money rather than time, "
     "and it is the only thing that turns a terpene claim into evidence."),
    ("own_observation_required",
     r"\b(?:dry|drying|cure|curing|harvest|yield|aroma|smell|taste|"
     r"retention|terpene)\b",
     "deferred_to_outcome",
     "Nothing external settles this. It is settled by running it on this grow "
     "and recording what happened, which is not available until harvest."),
)

VERIFIABILITY_ORDER = ("immediate", "near_term", "costs_money", "deferred_to_outcome")


def pointers(text):
    """What this card points at, and what following each pointer would take."""
    t = str(text or "")
    found, seen = [], set()
    for name, pat, cost, how in POINTER_KINDS:
        m = re.search(pat, t, re.I)
        if not m or name in seen:
            continue
        seen.add(name)
        found.append({"points_at": name, "verifiability": cost, "how_to_follow": how,
                      "example": " ".join(t[max(0, m.start() - 40):m.end() + 40].split())[:160]})
    # Cheapest route first: tell the principal where an hour actually buys
    # something.
    found.sort(key=lambda p: VERIFIABILITY_ORDER.index(p["verifiability"]))
    if not found:
        return {"pointers": [], "cheapest": None,
                "note": ("This card points at nothing outside itself. It cannot be "
                         "checked without reproducing whatever it describes, which "
                         "makes it an assertion rather than a finding aid.")}
    return {"pointers": found, "cheapest": found[0]["verifiability"],
            "note": ("A card is second-hand because it reports someone else's "
                     "finding. What it POINTS AT may be first-hand and openable - "
                     "and once the pointer resolves, the card's own standing stops "
                     "mattering. Follow the cheapest pointer first.")}

