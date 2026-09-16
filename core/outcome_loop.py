#!/usr/bin/env python3
"""Predict what a tribunal will do, then read what it did, then grade.

    from core.outcome_loop import predict, score, calibration

WHY THIS EXISTS. tools/check_inherited.py scores every domain on seven
rungs and two of them are `predict` and `grade`. Grow closes both: a feed
plan states the ppm it expects, the next reading confirms or refutes it,
and a miss is a finding about uptake rather than an embarrassment. Legal
carried 119 works and 4,168 sections and closed neither. It could cite,
refuse, and assemble - and it had never once said what it expected to
happen and then checked.

A CORPUS WITHOUT A LOOP IS A LIBRARY. The claim pipeline already scores a
claim against ten prerequisites; that measures whether a claim is
SUPPORTED. It does not measure whether this agent is any good at telling
which claims win. Those are different questions and only the second one
improves with use.

THE HONEST PART, and it is the whole design. A prediction is only a
prediction if the predictor could not see the answer:

  `forecast`      - made on a live matter whose outcome does not exist yet.
                    This is the real rung.
  `retrospective` - made from the FACTS of a decided case, with the
                    holding withheld, and graded against what the court
                    actually did. This is CALIBRATION, not forecasting,
                    and it is labelled so at every point. It is worth
                    doing - it is how you find out the agent over-weights
                    standing before you rely on it - but a retrospective
                    hit is not evidence of foresight and this module will
                    not let it be counted as one.

`calibration()` reports the two separately and refuses to pool them.

WHAT IS PREDICTED. Not "who wins" as a feeling: the DISPOSITION from a
closed set, and the killer defence expected to bite, from the set the
lane sheets already name. Both are checkable against a docket. A
prediction with no basis is refused, because "I think it loses" teaches
nothing when it turns out to be right.
"""
import hashlib
import json
import os
import re
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "private", "predictions")

KINDS = ("forecast", "retrospective")

# What a court actually does, from the mover's point of view. Checkable on
# a docket, which is the test for anything in this set.
DISPOSITIONS = (
    "motion_to_dismiss_granted",
    "motion_to_dismiss_denied",
    "motion_to_dismiss_granted_in_part",
    "summary_judgment_for_plaintiff",
    "summary_judgment_for_defendant",
    "summary_judgment_granted_in_part",
    "remanded",
    "settled",
    "voluntarily_dismissed",
    "judgment_after_trial",
    # APPELLATE DISPOSITIONS ARE THEIR OWN SET. Read against the shelf, five
    # of twelve decisions came back `unknown` because Spokeo, Ramirez,
    # Safeco, Henson and Duguid do not deny motions - they affirm, reverse
    # and vacate. Forcing an appellate outcome into a trial-court label
    # would have been the wrong answer wearing the right shape.
    "affirmed",
    "reversed",
    "vacated_and_remanded",
    "affirmed_in_part_reversed_in_part",
    "stayed",
    "unknown",
)

# The defences the lane sheets already name, plus the two that end a case
# before any of them are reached.
DEFENCES = (
    "no_1681s2a_private_right", "no_bureau_notice", "accuracy", "no_standing",
    "arbitration", "sol", "not_a_debt_collector", "consent", "not_an_atds",
    "not_a_solicitation", "no_publication", "none_expected",
)

# Who an appellate disposition favours depends on who appealed, which the
# label alone does not carry - so these are `unknown` rather than guessed.
OUTCOME_FOR_CONSUMER = {
    "affirmed": "unknown", "reversed": "unknown",
    "vacated_and_remanded": "unknown",
    "affirmed_in_part_reversed_in_part": "split", "stayed": "unknown",
    "motion_to_dismiss_denied": "consumer",
    "summary_judgment_for_plaintiff": "consumer",
    "judgment_after_trial": "unknown",
    "motion_to_dismiss_granted": "defendant",
    "summary_judgment_for_defendant": "defendant",
    "motion_to_dismiss_granted_in_part": "split",
    "summary_judgment_granted_in_part": "split",
    "remanded": "unknown", "settled": "unknown",
    "voluntarily_dismissed": "unknown", "unknown": "unknown",
}


class Refused(ValueError):
    pass


def _iso(d, what):
    if d is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d)):
        raise Refused(f"{what}: date must be YYYY-MM-DD, got {d!r}")
    return str(d)


def predict(pred_id, matter, disposition, kind="forecast", defence="none_expected",
            basis=None, lane=None, confidence="medium", made_on=None,
            facts_seen=None, holding_withheld=None):
    """Record what is expected, before the answer is known. -> the prediction.

    `basis` is required. A prediction with no reasoning is a coin toss that
    cannot teach anything when it lands - and a coin toss that lands right
    is the single most misleading record a system can keep."""
    if kind not in KINDS:
        raise Refused(f"kind {kind!r} is not one of {KINDS}")
    if disposition not in DISPOSITIONS:
        raise Refused(f"disposition {disposition!r} is not one of {DISPOSITIONS}")
    if defence not in DEFENCES:
        raise Refused(f"defence {defence!r} is not one of {DEFENCES}")
    if not str(basis or "").strip():
        raise Refused("a prediction needs a basis - which fact, against which element. "
                      "Without one a hit teaches nothing.")
    if confidence not in ("low", "medium", "high"):
        raise Refused("confidence is low | medium | high")
    if kind == "retrospective" and holding_withheld is not True:
        raise Refused(
            "a retrospective prediction must declare holding_withheld=True. If the "
            "holding was read first this is not a prediction, it is a summary, and "
            "recording it as calibration would corrupt the only number here worth "
            "having.")
    rec = {
        "prediction_id": str(pred_id), "matter": str(matter), "lane": lane,
        "kind": kind, "predicted_disposition": disposition,
        "predicted_defence": defence, "predicted_favours":
            OUTCOME_FOR_CONSUMER.get(disposition, "unknown"),
        "basis": str(basis), "confidence": confidence,
        "facts_seen": facts_seen or [],
        "holding_withheld": bool(holding_withheld),
        "made_on": _iso(made_on, "made_on") or date.today().isoformat(),
        "actual": None, "graded": None,
        "counts_as_foresight": kind == "forecast",
        "note": ("Calibration only. The outcome already existed when this was made, so a "
                 "hit is not evidence of foresight." if kind == "retrospective" else
                 "A forecast: the outcome did not exist when this was recorded."),
    }
    rec["sha256"] = hashlib.sha256(json.dumps(
        {k: rec[k] for k in ("matter", "predicted_disposition", "predicted_defence",
                             "basis", "made_on")}, sort_keys=True).encode()).hexdigest()
    return rec


def record_actual(pred, disposition, on=None, source=None, holding=None):
    """What the tribunal did. Never edits the prediction - that is the point."""
    if disposition not in DISPOSITIONS:
        raise Refused(f"disposition {disposition!r} is not one of {DISPOSITIONS}")
    if not str(source or "").strip():
        raise Refused("an outcome needs a source - the docket entry or opinion it is "
                      "read from. An outcome nobody can check is not an outcome.")
    pred["actual"] = {"disposition": disposition, "on": _iso(on, "outcome date"),
                      "source": str(source), "holding": holding,
                      "favours": OUTCOME_FOR_CONSUMER.get(disposition, "unknown")}
    return pred


def score(pred):
    """Grade it, and say what the miss teaches. -> the prediction, graded."""
    a = pred.get("actual")
    if not a:
        pred["graded"] = {"state": "pending", "why": "no outcome recorded yet"}
        return pred
    exact = a["disposition"] == pred["predicted_disposition"]
    direction = a["favours"] == pred["predicted_favours"]
    # A disposition can be wrong while the DIRECTION is right - predicting
    # dismissal and getting summary judgment for the defendant is a miss on
    # the mechanism and a hit on who wins. Scoring only exactness would throw
    # away the more useful half; scoring only direction would flatter it.
    if exact:
        state = "hit"
    elif direction and a["favours"] != "unknown":
        state = "direction_right_mechanism_wrong"
    else:
        state = "miss"
    lesson = None
    if state != "hit":
        lesson = (f"expected {pred['predicted_disposition']} on "
                  f"{pred['predicted_defence']}; the court did {a['disposition']}. "
                  f"The basis given was: {pred['basis']}")
    pred["graded"] = {
        "state": state, "exact": exact, "direction_right": direction,
        "predicted": pred["predicted_disposition"], "actual": a["disposition"],
        "counts_as_foresight": pred.get("counts_as_foresight", False),
        "lesson": lesson,
        "why": ("The divergence is the finding. A corpus tells you what the rule is; "
                "this tells you whether this agent reads it the way tribunals do."),
    }
    return pred


def calibration(preds):
    """How good is this agent, separated by kind. Never pooled.

    Pooling a retrospective hit with a forecast hit would let a shelf full
    of decided cases manufacture a record of foresight, which is the
    training-signal failure this project already guards in the eval
    harness."""
    out = {}
    for kind in KINDS:
        rows = [p for p in preds if p.get("kind") == kind and (p.get("graded") or {}).get("state")
                not in (None, "pending")]
        hits = sum(1 for p in rows if p["graded"]["state"] == "hit")
        dirs = sum(1 for p in rows if p["graded"]["state"] == "direction_right_mechanism_wrong")
        out[kind] = {
            "graded": len(rows), "exact": hits,
            "direction_only": dirs, "missed": len(rows) - hits - dirs,
            "exact_rate": (round(hits / len(rows), 3) if rows else None),
            "direction_rate": (round((hits + dirs) / len(rows), 3) if rows else None),
            "means": ("foresight: the outcome did not exist when these were made"
                      if kind == "forecast" else
                      "calibration only: these were graded against decisions that already "
                      "existed, and a hit here is not evidence of foresight"),
        }
    pending = [p["prediction_id"] for p in preds
               if (p.get("graded") or {}).get("state") == "pending"]
    out["pending"] = pending
    out["rule"] = ("Forecast and retrospective are never pooled. A shelf of decided cases "
                   "can manufacture a record of foresight if they are.")
    if out["forecast"]["graded"] == 0:
        out["honest_state"] = ("No forecast has been graded. Whatever the retrospective "
                               "rate says, this agent has not yet been shown to predict "
                               "anything it could not already see.")
    return out


# ----------------------------------------------------------------------
# Store
# ----------------------------------------------------------------------

def _path(pred_id):
    pid = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(pred_id)).strip("_")
    if not pid:
        raise Refused("prediction_id is empty after sanitising")
    return os.path.join(STORE, pid + ".json")


def save(pred):
    from core.fs_boundary import ensure_dir
    from core.asset_registry import guard_fields
    try:
        guard_fields({"matter": str(pred.get("matter") or ""),
                      "basis": str(pred.get("basis") or "")})
    except ValueError as exc:
        raise Refused(str(exc))
    ensure_dir(STORE, 0o700)
    p = _path(pred["prediction_id"])
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(pred, fh, indent=2, sort_keys=True)
    return p


def load(pred_id):
    p = _path(pred_id)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def load_all():
    if not os.path.isdir(STORE):
        return []
    out = []
    for f in sorted(os.listdir(STORE)):
        if f.endswith(".json"):
            try:
                with open(os.path.join(STORE, f), encoding="utf-8") as fh:
                    out.append(json.load(fh))
            except Exception:                       # noqa: BLE001
                continue
    return out


# ----------------------------------------------------------------------
# Reading what a shelved opinion actually did
# ----------------------------------------------------------------------

DISPOSITION_CUES = (
    ("summary_judgment_granted_in_part",
     r"summary judgment[^.]{0,80}granted[- ]in[- ]part|granted-in-part and denied-in-part"),
    ("motion_to_dismiss_granted_in_part",
     r"motion to dismiss[^.]{0,60}granted in part"),
    ("summary_judgment_for_plaintiff",
     r"summary judgment is entered in[^.]{0,40}plaintiff|plaintiff'?s? motion for (?:partial )?summary judgment is granted"),
    ("summary_judgment_for_defendant",
     r"defendant'?s? motion for summary judgment is granted(?! ?-? ?in part)"),
    ("motion_to_dismiss_denied",
     r"motion to dismiss is denied|\bmotion is denied\b"),
    ("motion_to_dismiss_granted",
     r"motion to dismiss is granted(?! in part)|complaint is dismissed"),
    ("affirmed_in_part_reversed_in_part",
     r"affirmed in part(?:,| and) revers|revers\w+ in part(?:,| and) affirm"),
    ("vacated_and_remanded",
     r"\bvacated? and remand|judgment[^.]{0,40}vacated"),
    ("reversed", r"\bwe reverse\b|is reversed\b|judgment[^.]{0,30}reversed"),
    ("affirmed", r"\bwe affirm\b|is affirmed\b|judgment[^.]{0,30}affirmed"),
    ("stayed", r"\bthis (?:case|action) is stayed\b|\bstay(?:ed)? pending\b"),
)


def read_disposition(text):
    """What a shelved opinion says it did. -> {disposition, quote} or unknown.

    Cue-based and deliberately shallow: it reads the court's own disposition
    sentence and quotes it, so a caller can see what the label was taken
    from. Where no cue matches it returns `unknown` rather than guessing,
    because a wrong outcome poisons every calibration number computed after
    it."""
    flat = re.sub(r"\s+", " ", text or "")
    for label, pat in DISPOSITION_CUES:
        m = re.search(pat, flat, re.I)
        if m:
            i = m.start()
            return {"disposition": label,
                    "quote": flat[max(0, i - 90): i + 190].strip(),
                    "basis": "the court's own disposition sentence"}
    return {"disposition": "unknown", "quote": None,
            "basis": "no disposition sentence matched; read it directly"}
