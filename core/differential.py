"""Competing explanations, held open until the evidence separates them.

`claim_assessment.py` tests an assertion somebody MADE. This is its sibling for
something OBSERVED, where nobody has asserted anything yet and the failure mode
is the opposite: not accepting a bad claim, but collapsing a symptom straight
into a treatment.

    OBSERVATION -> EVIDENCE -> HYPOTHESES -> CONFIDENCE -> DECISION
                      ^                                      |
                      |                                      v
                  NEW EVIDENCE <------- OUTCOME <-------- ACTION

The whole point is the gap between HYPOTHESES and DECISION. "Leaves are yellow,
add Cal-Mag" fuses observation and action into one step, and once fused there is
no place to be wrong out loud - the treatment IS the diagnosis, so a wrong
diagnosis is invisible until the plant is worse.

Four rules make this a diagnostic engine rather than a formatted opinion:

- **One hypothesis is not a differential.** If only one explanation can be
  named, that is evidence about how hard anyone looked, not evidence about the
  world. Refused.
- **A hypothesis without a discriminator is untestable** and can never become
  leading, however well it fits. Fitting the evidence you already have is what
  every wrong theory also does; what separates them is predicting something not
  yet seen.
- **Time is evidence.** A discriminator names what to look for AND when the
  observation becomes meaningful. "Wait and watch" is a decision with a basis,
  not an absence of one.
- **Acting on an open differential destroys it.** Changing two things at once
  while explanations compete means the outcome cannot attribute to either. That
  is refused by default, because it is the single most common way a diagnostic
  loop is broken - and it is broken silently, since the plant does respond to
  something and the wrong lesson gets recorded as learned.

Confidence attaches to CONCLUSIONS, not only to measurements. A pH of 5.93 is
measured; "root uptake is impaired" is inferred; a system that carries one
number for both is lying about one of them.
"""

from datetime import datetime, timedelta

# How a fact got into the record. This is about provenance, not certainty:
# a measured value can still be wrong, but it is wrong in a different way than
# a reported one, and the reasoning layer needs to be able to tell.
EVIDENCE_KINDS = (
    "measured",     # an instrument produced it
    "observed",     # seen directly, not instrumented (leaf colour, root visible)
    "reported",     # a person said it happened (pump was off ~2h)
    "recorded",     # already in the system's own history
    "inferred",     # derived from other evidence - never a primary fact
    "absent",       # looked for and NOT found. Distinct from never looked.
)

# What a piece of evidence does to a hypothesis. `required_but_absent` is the
# one that carries weight nothing else does: a hypothesis that predicts
# something which is then looked for and missing is damaged, and that is a
# different state from a hypothesis nobody has tested.
STANCES = ("supports", "contradicts", "neutral", "required_but_absent")

# Confidence in an EXPLANATION. Deliberately not a number: a percentage on a
# differential invites arithmetic between incommensurable things.
HYPOTHESIS_CONFIDENCE = (
    "leading",      # best supported AND has survived at least one discriminator
    "plausible",    # fits the evidence, not yet distinguished from its rivals
    "weakened",     # evidence cuts against it, not yet excluded
    "excluded",     # a discriminator ran and ruled it out
    "untestable",   # no discriminator proposed - cannot be promoted, ever
)

DECISIONS = (
    "hold",             # DEFAULT. Act on trajectory, not on the existence of a symptom.
    "observe",          # actively gather the discriminating observation
    "intervene",        # change exactly one thing
    "escalate",         # outside this agent's competence or authority
)


def new_differential(subject, observation, observed_by="principal", domain=""):
    """Open a differential. Nothing is explained yet, and that is the state."""
    return {
        "subject": subject,
        "observation": observation,
        "observed_by": observed_by,
        "domain": domain,
        "opened_at": datetime.now().isoformat(),
        "evidence": [],
        "hypotheses": [],
        "decision": None,
        "outcomes": [],
        "status": "open",
    }


def add_evidence(diff, fact, kind="observed", value=None, note=""):
    """Record a fact. Facts are shared across hypotheses; stances are not."""
    if kind not in EVIDENCE_KINDS:
        return {"error": f"Unknown evidence kind '{kind}'. Use one of: "
                         f"{', '.join(EVIDENCE_KINDS)}."}
    diff["evidence"].append({
        "fact": fact, "kind": kind, "value": value, "note": note,
        "at": datetime.now().isoformat(),
    })
    return diff


def add_hypothesis(diff, name, mechanism, discriminator=None,
                   discriminator_ready_in_hours=None, stances=None):
    """Propose an explanation.

    `mechanism` is required and is not decoration: an explanation that cannot
    say HOW the cause produces the observation is a label, and a label cannot
    be tested. `discriminator` is what future observation would separate this
    hypothesis from its rivals - without one the hypothesis is `untestable` and
    is barred from ever leading, no matter how well it fits."""
    if not str(mechanism).strip():
        return {"error": "A hypothesis needs a mechanism - how the proposed cause "
                         "produces this observation. Without one it is a label, "
                         "and a label cannot be tested."}
    h = {
        "name": name,
        "mechanism": mechanism,
        "discriminator": discriminator or None,
        "discriminator_ready_in_hours": discriminator_ready_in_hours,
        "stances": stances or [],
        "confidence": "untestable" if not discriminator else "plausible",
        "added_at": datetime.now().isoformat(),
    }
    if discriminator and discriminator_ready_in_hours:
        h["reassess_after"] = (
            datetime.now() + timedelta(hours=float(discriminator_ready_in_hours))
        ).isoformat()
    diff["hypotheses"].append(h)
    return diff


def weigh(diff, hypothesis_name, fact, stance):
    """Attach a stance to one hypothesis. The same fact weighs differently on
    different explanations - that asymmetry IS the differential."""
    if stance not in STANCES:
        return {"error": f"Unknown stance '{stance}'. Use one of: {', '.join(STANCES)}."}
    for h in diff["hypotheses"]:
        if h["name"] == hypothesis_name:
            h["stances"].append({"fact": fact, "stance": stance})
            if stance == "contradicts" and h["confidence"] in ("plausible", "leading"):
                h["confidence"] = "weakened"
            return diff
    return {"error": f"No hypothesis named '{hypothesis_name}'."}


def assess(diff):
    """Read the differential's state without resolving it.

    Returns what is live, what is excluded, and - the useful part - what
    observation would actually move things. A differential that cannot say what
    would change its mind is not reasoning, it is a position."""
    hyps = diff.get("hypotheses", [])
    out = {
        "subject": diff.get("subject"),
        "observation": diff.get("observation"),
        "evidence_count": len(diff.get("evidence", [])),
    }
    if len(hyps) < 2:
        out.update({
            "status": "not_a_differential",
            "confidence": "low",
            "reason": ("Fewer than two explanations are on the table. A single "
                       "hypothesis is not a diagnosis - it is the first thing "
                       "that came to mind, and the evidence has had nothing to "
                       "choose between. This says how hard anyone looked, not "
                       "what is happening."),
            "action": "Name at least one rival explanation, including the boring "
                      "one (nothing is wrong; this is normal variation).",
        })
        return out

    live = [h for h in hyps if h["confidence"] in ("leading", "plausible", "weakened")]
    excluded = [h for h in hyps if h["confidence"] == "excluded"]
    untestable = [h for h in hyps if h["confidence"] == "untestable"]

    out["live"] = [{"name": h["name"], "confidence": h["confidence"],
                    "supports": sum(1 for s in h["stances"] if s["stance"] == "supports"),
                    "contradicts": sum(1 for s in h["stances"] if s["stance"] == "contradicts")}
                   for h in live]
    out["excluded"] = [h["name"] for h in excluded]
    out["untestable"] = [h["name"] for h in untestable]

    pending = [{"hypothesis": h["name"], "look_for": h["discriminator"],
                "meaningful_after": h.get("reassess_after")}
               for h in live if h.get("discriminator")]
    out["discriminators"] = pending

    if len(live) == 1 and not untestable:
        h = live[0]
        out.update({"status": "converged", "leading": h["name"],
                    "confidence": "moderate" if h["confidence"] != "leading" else "high",
                    "reason": f"One explanation remains live: {h['name']}. "
                              f"{h['mechanism']}"})
    elif not pending:
        out.update({
            "status": "undecidable_as_posed", "confidence": "low",
            "reason": ("Several explanations are live and NONE of them proposes an "
                       "observation that would separate them. Every one of them fits "
                       "what is already known, which is exactly what a wrong theory "
                       "also does."),
            "action": "For each hypothesis, name the thing that would be true if it "
                      "were right AND false if a rival were - then go look for it.",
        })
    else:
        soonest = min((p["meaningful_after"] for p in pending if p["meaningful_after"]),
                      default=None)
        out.update({
            "status": "open", "confidence": "low",
            "reason": (f"{len(live)} explanations remain live and are not yet "
                       f"distinguished. Confidence belongs to the differential, "
                       f"not to whichever one is currently most appealing."),
            "action": "Run the discriminators before choosing.",
            "next_meaningful_observation": soonest,
        })
    return out


def propose_decision(diff, decision, basis="", changes=None, reassess_in_hours=None):
    """Decide what to DO, separately from what is believed.

    The separation is the point. A decision carries its own confidence and its
    own basis, and 'hold' is a real one - the reason to hold is usually that
    acting now would cost more diagnostic value than the intervention is worth."""
    if decision not in DECISIONS:
        return {"error": f"Unknown decision '{decision}'. Use one of: {', '.join(DECISIONS)}."}
    state = assess(diff)
    changes = list(changes or [])
    rec = {"decision": decision, "basis": basis, "changes": changes,
           "differential_status": state.get("status"),
           "at": datetime.now().isoformat()}

    # Changing more than one thing while explanations compete means the outcome
    # attributes to nothing. This is the most common way a diagnostic loop
    # breaks, and it breaks quietly - the subject does respond to something, and
    # the wrong lesson gets filed as learned.
    if decision == "intervene" and len(changes) > 1 and state.get("status") == "open":
        rec.update({
            "allowed": False,
            "confidence": "low",
            "refusal": (f"{len(changes)} variables would change at once while "
                        f"{len(state.get('live', []))} explanations are still live. "
                        f"Whatever happens next could not be attributed to any of "
                        f"them, so this spends the experiment without buying an "
                        f"answer."),
            "instead": "Change one thing, or hold until a discriminator resolves.",
        })
        diff["decision"] = rec
        return rec

    if decision == "intervene" and state.get("status") == "open":
        rec.update({
            "allowed": True, "confidence": "low",
            "caution": ("Intervening on an open differential. The symptom exists, "
                        "but its cause does not - not yet. If this works, it will "
                        "not be clear which hypothesis it confirmed."),
        })
    elif decision == "hold":
        rec.update({
            "allowed": True,
            "confidence": "moderate-high",
            "note": ("Holding is an action with a basis, not a failure to act. "
                     "The trajectory is the evidence being waited on."),
        })
    else:
        rec.update({"allowed": True, "confidence": "moderate"})

    if reassess_in_hours:
        rec["reassess_after"] = (
            datetime.now() + timedelta(hours=float(reassess_in_hours))).isoformat()
    diff["decision"] = rec
    return rec


def record_outcome(diff, observation, supports=None, contradicts=None, closes=False):
    """Close the loop. An outcome is new evidence, weighed like any other.

    Without this the pipeline is a nicely-structured opinion. A hypothesis that
    was never checked against what actually happened has not been tested, and a
    system that only records its predictions is keeping a diary."""
    entry = {"observation": observation, "at": datetime.now().isoformat(),
             "supports": list(supports or []), "contradicts": list(contradicts or [])}
    diff["outcomes"].append(entry)
    add_evidence(diff, observation, kind="observed", note="outcome of a decision")
    for name in entry["contradicts"]:
        for h in diff["hypotheses"]:
            if h["name"] == name:
                h["confidence"] = "excluded"
    for name in entry["supports"]:
        for h in diff["hypotheses"]:
            # Surviving a discriminator is what promotes a hypothesis. Merely
            # fitting more evidence does not - a wrong theory accumulates
            # confirmations too.
            if h["name"] == name and h.get("discriminator") \
                    and h["confidence"] in ("plausible", "weakened"):
                h["confidence"] = "leading"
    if closes:
        diff["status"] = "closed"
        diff["closed_at"] = datetime.now().isoformat()
    return diff
