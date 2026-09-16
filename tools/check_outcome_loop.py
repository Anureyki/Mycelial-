#!/usr/bin/env python3
"""A prediction is only a prediction if the answer was not visible, and a
retrospective hit is never counted as foresight.

    python3 tools/check_outcome_loop.py

Synthetic; temp store. What the agent's calibration rate IS on this shelf is
a measurement, not an assertion, and is not re-run here.
"""
import os
import sys
import tempfile

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    from core import outcome_loop as O
    O.STORE = tempfile.mkdtemp()

    print("a prediction without reasoning is refused")
    for bad, why in (
        (lambda: O.predict("p", "m", "motion_to_dismiss_denied", basis=""), "no basis"),
        (lambda: O.predict("p", "m", "it_loses", basis="x"), "disposition off the set"),
        (lambda: O.predict("p", "m", "motion_to_dismiss_denied", basis="x",
                           defence="vibes"), "defence off the set"),
        (lambda: O.predict("p", "m", "motion_to_dismiss_denied", basis="x",
                           confidence="certain"), "confidence off the set"),
        (lambda: O.predict("p", "m", "motion_to_dismiss_denied", basis="x",
                           kind="retrospective"), "retrospective without holding_withheld"),
    ):
        try:
            bad()
            ck(f"refuses: {why}", False)
        except O.Refused:
            ck(f"refuses: {why}", True)

    print("an outcome nobody can check is not an outcome")
    p = O.predict("p1", "M", "motion_to_dismiss_denied", basis="pleaded on its face")
    try:
        O.record_actual(p, "motion_to_dismiss_granted", source="")
        ck("refuses an outcome with no source", False)
    except O.Refused:
        ck("refuses an outcome with no source", True)

    print("grading separates the mechanism from the direction")
    p = O.predict("p2", "M", "motion_to_dismiss_denied", basis="b")
    O.record_actual(p, "motion_to_dismiss_denied", source="docket 14")
    ck("same disposition -> hit", O.score(p)["graded"]["state"] == "hit")
    p = O.predict("p3", "M", "motion_to_dismiss_granted", basis="b")
    O.record_actual(p, "summary_judgment_for_defendant", source="docket 52")
    g = O.score(p)["graded"]
    ck("wrong mechanism, right winner -> direction_right_mechanism_wrong",
       g["state"] == "direction_right_mechanism_wrong" and g["direction_right"])
    p = O.predict("p4", "M", "motion_to_dismiss_granted", basis="b")
    O.record_actual(p, "motion_to_dismiss_denied", source="docket 14")
    g = O.score(p)["graded"]
    ck("wrong winner -> miss, with the lesson naming the basis",
       g["state"] == "miss" and "The basis given was: b" in g["lesson"])
    p = O.predict("p5", "M", "motion_to_dismiss_denied", basis="b")
    ck("ungraded is pending, not a miss", O.score(p)["graded"]["state"] == "pending")

    print("retrospective is never pooled with forecast")
    rows = []
    for i in range(4):
        r = O.predict(f"r{i}", "decided", "motion_to_dismiss_granted", kind="retrospective",
                      basis="b", holding_withheld=True)
        O.record_actual(r, "motion_to_dismiss_granted", source="opinion")
        rows.append(O.score(r))
    c = O.calibration(rows)
    ck("four retrospective hits do not create a forecast rate",
       c["retrospective"]["exact"] == 4 and c["forecast"]["graded"] == 0)
    ck("and the honest state says so out loud",
       "has not yet been shown to predict anything it could not already see"
       in c.get("honest_state", ""))
    ck("retrospective rows are marked as not foresight",
       all(not r["counts_as_foresight"] for r in rows))
    f = O.predict("f1", "live", "motion_to_dismiss_denied", basis="b")
    O.record_actual(f, "motion_to_dismiss_denied", source="docket")
    c = O.calibration(rows + [O.score(f)])
    ck("one graded forecast gives a forecast rate, separate from the other",
       c["forecast"]["graded"] == 1 and c["retrospective"]["graded"] == 4
       and "honest_state" not in c)

    print("reading a disposition off an opinion, or saying unknown")
    ck("a trial-court denial is read",
       O.read_disposition("for the reasons stated below, NCS's Motion is DENIED."
                          )["disposition"] == "motion_to_dismiss_denied")
    ck("an appellate reversal is read, and is not forced into a trial label",
       O.read_disposition("The judgment of the Court of Appeals is reversed."
                          )["disposition"] == "reversed")
    ck("a vacatur is read",
       O.read_disposition("we vacate and remand for further proceedings"
                          )["disposition"] == "vacated_and_remanded")
    r = O.read_disposition("The parties dispute whether the clause applies.")
    ck("no disposition sentence -> unknown, never a guess",
       r["disposition"] == "unknown" and r["quote"] is None)
    r = O.read_disposition("Accordingly, the complaint is dismissed.")
    ck("a reading quotes what it was taken from", bool(r["quote"]))
    ck("an appellate label does not claim to know who it favours",
       O.OUTCOME_FOR_CONSUMER["affirmed"] == "unknown")

    print("the store")
    p = O.predict("s1", "M", "motion_to_dismiss_denied", basis="b")
    path = O.save(p)
    ck("a prediction file is written 0600", (os.stat(path).st_mode & 0o777) == 0o600)
    ck("and reads back", O.load("s1")["prediction_id"] == "s1")
    try:
        O.save(O.predict("s2", "SSN 123-45-6789", "motion_to_dismiss_denied", basis="b"))
        ck("a prediction carrying an identifier is refused before it is written", False)
    except O.Refused as exc:
        ck("a prediction carrying an identifier is refused before it is written",
           "9-digit" in str(exc) or "ssn" in str(exc).lower())

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails[:6]))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
