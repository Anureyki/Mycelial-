#!/usr/bin/env python3
"""What a training source contains, and what that obliges. Declared, not argued.

    from core.data_sensitivity import classify, requires_dp, gate

    gate("security_eval", dp_engaged=False)   # -> allowed or refused, with why

WHY THIS EXISTS. The harness's own run record says:

    "dp": false,
    "epsilon_spent": null,
    "epsilon_note": "No DP-SGD on this path. The data is the system's own
                     security decisions, not the principal's records."

That note is correct today and it is a CLAIM IN A COMMENT. Nothing checks it,
nothing re-checks it when the source changes, and the financial programme is
about to point the same machinery at an asset registry containing his
institutions, his balances and his benefit records. The moment that happens the
sentence stays true-looking and stops being true.

So sensitivity is DECLARED per source in config/data_sensitivity.json, and the
obligation follows from the declaration rather than from anybody's memory.

THREE CLASSES, AND THE MIDDLE ONE IS THE POINT

    system_operational   decisions ABOUT the system - which agent may read
                         which resource. No human's records are in it. A
                         model that memorised all of it would have learned
                         the ACL, which is already in the repository.

    personal_financial   accounts, balances, counterparties, obligations.
                         Memorisation here leaks the principal's finances,
                         and a model cannot be made to forget a fact the way
                         a record can be corrected.

    personal_record      benefits, medical, housing, identifiers. The VA
                         entitlement, the fiduciary appointment, the HUD-VASH
                         case. Strictly worse than financial: these describe
                         a protected status, and 38 U.S.C. 5301 and the
                         Privacy Act are not privacy preferences.

UNDECLARED IS REFUSED, NOT ASSUMED OPERATIONAL. A source nobody classified is
the one most likely to be new, and new is exactly when somebody has just
pointed the trainer at something personal. `unknown` blocks here for the same
reason it blocks everywhere else in this system.

WHY DP AND NOT JUST "DON'T TRAIN ON IT". Because the useful version of this
system does learn from his own matters - that is the point of a personal OS -
and the honest way to do that is a measured privacy budget rather than a
promise. privacy/accountant.py in mycelial-core already computes epsilon from
(noise_multiplier, sample_rate, steps, delta) rather than storing a chosen
number. This decides WHEN that is mandatory.

FEDERATED IS A SEPARATE AXIS AND NOT A SUBSTITUTE. Keeping data on the device
limits who holds the rows; it does nothing about what the weights memorise,
and a federated round still ships gradients computed from those rows. So a
personal source requires DP whether or not it is federated, and
`federated_only` marks sources that must additionally never leave this
machine in raw form.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "data_sensitivity.json")

CLASSES = ("system_operational", "personal_financial", "personal_record",
           "unknown")

# Classes that may not become training pairs without differential privacy.
REQUIRES_DP = ("personal_financial", "personal_record")
# Classes whose raw rows must never leave this machine, federated or not.
NEVER_LEAVES = ("personal_financial", "personal_record")


class Refused(PermissionError):
    """Training was refused. Raised, never returned as a status."""


def _config():
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        raise Refused(
            f"the data sensitivity declaration is unreadable ({exc}). Nothing "
            f"is classified, so nothing may be trained on - an unreadable "
            f"policy is not an absent obligation.") from exc


def classify(source):
    """-> {class, why, citation}. An undeclared source is `unknown`."""
    rec = (_config().get("sources") or {}).get(source)
    if not rec:
        # NO CLAIM IS NOT A CLAIM OF `unknown`. A name lookup that finds
        # nothing has said nothing - it has not asserted that the data is
        # unclassified, it has failed to recognise a path. Treating that as a
        # claim made the lattice refuse every store not sitting at a canonical
        # location, including one carrying a perfectly good explicit manifest,
        # because "unknown" outranks everything.
        #
        # The distinction matters in the other direction too: an explicit
        # `"class": "unknown"`, or a manifest that exists and is malformed, IS
        # a claim and must keep blocking. Silence and a declaration of
        # ignorance are different findings - the same rule this system applies
        # to every absence it records.
        return {"class": "unknown", "claimed": False, "source": source,
                "why": (f"{source!r} is not declared in "
                        f"config/data_sensitivity.json. A source nobody "
                        f"classified is the one most likely to be new, and "
                        f"new is exactly when somebody has just pointed the "
                        f"trainer at something personal."),
                "citation": None}
    c = rec.get("class")
    if c not in CLASSES:
        return {"class": "unknown", "source": source,
                "why": f"declared class {c!r} is not one of {list(CLASSES)}",
                "citation": rec.get("citation")}
    return {"class": c, "claimed": True, "source": source,
            "why": rec.get("why"),
            "citation": rec.get("citation"),
            "federated_only": bool(rec.get("federated_only"))}


# RESTRICTION IS A LATTICE AND ONLY MOVES UP.
#
# Ordered least to most restrictive. `unknown` is not the bottom of this
# scale - it BLOCKS, because "nobody classified it" is not "it is safe".
RESTRICTION = {"system_operational": 1, "personal_financial": 2,
               "personal_record": 3, "unknown": 99}


def resolve(*claims):
    """-> the binding classification from every available claim.

    THE RULE THIS ENFORCES, and the hole it closes. Inference may INCREASE
    restriction and must never silently decrease an explicit one. A source's
    classification was being read from a manifest inside its own directory,
    and that manifest won outright - so a directory declared
    personal_financial by name, carrying a manifest that said
    system_operational, was ALLOWED. Measured, not theorised: a personal
    source downgraded itself and the trainer accepted it.

    Data that can lower its own restriction is not classified, it is
    self-certified - the same shape as an agent writing its own security
    record, which core/security_events.py exists to prevent.

    So every claim is collected and the MOST RESTRICTIVE binds. A genuine
    disagreement is reported as `conflicting` alongside the binding answer,
    because two sources disagreeing about how sensitive something is is worth
    a human look even when the safe reading is obvious."""
    seen = []
    for c in claims:
        if not c:
            continue
        if isinstance(c, dict) and c.get("claimed") is False:
            continue                      # silence, not a claim of unknown
        k = c if isinstance(c, str) else c.get("class")
        if k not in RESTRICTION:
            k = "unknown"                 # a malformed claim IS a claim
        seen.append(k)
    if not seen:
        return {"class": "unknown", "claims": [], "conflicting": False,
                "why": "no classification was available at all"}
    binding = max(seen, key=lambda k: RESTRICTION[k])
    return {
        "class": binding,
        "claims": seen,
        "conflicting": len(set(seen)) > 1,
        "why": (f"{len(set(seen))} differing claims {sorted(set(seen))}; the "
                f"most restrictive binds. Inference may tighten a "
                f"classification and may never loosen one."
                if len(set(seen)) > 1 else f"all claims agree: {binding}"),
    }


def requires_dp(source):
    return classify(source)["class"] in REQUIRES_DP or \
        classify(source)["class"] == "unknown"


def gate(source, dp_engaged, epsilon_spent=None, also=()):
    """-> True, or raise Refused. The one place the obligation is enforced.

    `also` carries any additional classification claims - a manifest found
    beside the data, for instance. They can only make the answer stricter."""
    c = classify(source)
    r = resolve(c, *[classify(a) if isinstance(a, str) else a for a in also])
    kind = r["class"]
    if r["conflicting"]:
        c = dict(c, why=(c.get("why") or "") + " CONFLICTING CLAIMS: "
                 + r["why"])
    if kind == "unknown":
        raise Refused(
            f"REFUSED: {c['why']} Declare it in config/data_sensitivity.json "
            f"before training on it.")
    if kind in REQUIRES_DP:
        if not dp_engaged:
            raise Refused(
                f"REFUSED: {source!r} is classified {kind} and differential "
                f"privacy is not engaged. {c.get('why') or ''} A model cannot "
                f"be made to forget the way a record can be corrected, so the "
                f"budget is spent before the weights move, not after."
                + (f" [{c['citation']}]" if c.get("citation") else ""))
        if epsilon_spent is None:
            raise Refused(
                f"REFUSED: {source!r} is {kind}, DP is claimed engaged, and no "
                f"epsilon was accounted. `dp: true` with no measured budget is "
                f"the same shape as `accuracy: 0.92` hardcoded - a reassuring "
                f"field that measures nothing.")
    return True


def federated_constraint(source):
    """-> what may leave this machine for this source, and what may not."""
    c = classify(source)
    if c["class"] in NEVER_LEAVES:
        return {"raw_rows_may_leave": False,
                "why": ("Federated training keeps rows local and ships "
                        "gradients. That limits who HOLDS the data and does "
                        "nothing about what the weights memorise, so it is "
                        "not a substitute for DP - it is a second, "
                        "independent requirement.")}
    return {"raw_rows_may_leave": True,
            "why": f"{c['class']} contains no personal record."}
