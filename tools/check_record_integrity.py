#!/usr/bin/env python3
"""What the grower states outranks what Grow's record already held.

    python3 tools/check_record_integrity.py

Three bugs of one class, all found 2026-10-05, each a place where Grow
trusted its own record over what it was told and the record was wrong:

  - amend_grow_system replaced a prose note given as a change label, and
    GSC1's drying constraint and GSC2's dosing-instrument note were gone.
  - record_refill(to=15, added=3) kept the stale 15 L start from the last
    refill and recorded 15 -> 15, added 0.
  - assess_stage called GSC1 "likely veg" at 69 days while pistils were
    recorded on 2026-09-26.

A memory rule tells the next session to be careful; this gate stops the
code from doing it again. The store is stubbed; nothing here touches a
real plant.
"""
import json
import os
import sys
from datetime import datetime, timedelta

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def grow(store):
    from agents.grow_agent.grow_agent import GrowAgent
    g = GrowAgent.__new__(GrowAgent)
    g.agent_id = "grow_agent"
    g.log = lambda *a, **k: None
    g.retrieve_own_memory = lambda k: store.get(k)
    g.store_own_memory = lambda k, v, pin=False: store.__setitem__(k, v) or {"success": True}
    g._unwrap_value = lambda v: v
    return g


def main():
    print("a note is appended, never silently replaced")
    store = {"grow_system_p1": json.dumps({"plant_id": "p1", "location": "",
                                           "note": "DRYING CONSTRAINT: 72 F floor"})}
    g = grow(store)
    r = g.amend_grow_system("p1", location="tent", note="label for a location change")
    rec = json.loads(store["grow_system_p1"])
    ck("the old note survives", "DRYING CONSTRAINT" in rec["note"], rec["note"][:60])
    ck("the new text is added under it", "label for a location change" in rec["note"])
    ck("the response says it appended", r.get("appended_not_replaced") == ["note"])
    ck("a fact field still updates in place", rec["location"] == "tent")
    g.amend_grow_system("p1", note="replaced on purpose", replace_notes=True)
    rec = json.loads(store["grow_system_p1"])
    ck("replace_notes=true replaces", rec["note"] == "replaced on purpose")
    ck("and the replaced text goes to superseded",
       any("DRYING CONSTRAINT" in str(s.get("value")) for s in rec.get("superseded", [])))

    print("a stated refill start outranks the level on record")
    store = {"grow_system_p2": json.dumps({"plant_id": "p2", "reservoir_liters": 15.0,
                                           "reservoir_capacity_liters": 15.5})}
    g = grow(store)
    g._get_readings_for_plant = lambda pid: []
    r = g.record_refill("p2", to_liters=15, added_liters=3)
    ck("recorded 12 -> 15, added 3", r.get("from_liters") == 12.0 and r.get("added_liters") == 3.0,
       f"{r.get('from_liters')} -> {r.get('to_liters')}, added {r.get('added_liters')}")
    ck("and says the record was overridden",
       (r.get("stated_start_overrode_record") or {}).get("record_said") == 15.0)

    print("a newer volume reading outranks the record's level")
    store = {"grow_system_p5": json.dumps({"plant_id": "p5", "reservoir_liters": 15.0,
                                           "reservoir_capacity_liters": 15.5,
                                           "volume_measured_on": "2026-10-07T12:19:00"})}
    g = grow(store)
    g._get_readings_for_plant = lambda pid: [{"timestamp": "2026-10-09T11:11:00",
                                              "volume_liters": 12.0, "ppm": 918}]
    r = g.record_refill("p5", to_liters=15)
    ck("refill to 15 after a 12 L reading records 3 L added, not 0",
       r.get("from_liters") == 12.0 and r.get("added_liters") == 3.0,
       f"{r.get('from_liters')} -> {r.get('to_liters')}, added {r.get('added_liters')}")

    print("a reading taken after a refill is never its baseline")
    store = {"grow_system_p6": json.dumps({"plant_id": "p6", "reservoir_liters": 12.0,
                                           "reservoir_capacity_liters": 15.5})}
    g = grow(store)
    g._water_alert = lambda *a, **k: None
    g._get_readings_for_plant = lambda pid: [
        {"timestamp": "2026-10-05T02:35:00", "volume_liters": 15.0, "ppm": 864},
        {"timestamp": "2026-10-11T17:34:00", "volume_liters": 15.0, "ppm": 603}]
    r = g.record_refill("p6", to_liters=15, added_liters=3, at="2026-10-11T17:30:00")
    pr = r.get("prediction") or {}
    ck("the prediction starts from the reading before the refill, not the one after",
       (pr.get("from") or {}).get("ppm") == 864, str(pr.get("from")))
    ck("the refill is stamped at the time given", r.get("at", "").startswith("2026-10-11T17:30"))

    print("a pH adjustment belongs to one reservoir and must fit its direction")
    store = {"grow_system_p7": json.dumps({"plant_id": "p7", "reservoir_liters": 15.0})}
    g = grow(store)
    ck("no plant is refused", "error" in g.record_ph_adjustment(None, "down", 0.75, 6.96, 5.95))
    ck("pH Down that raised pH is refused",
       "error" in g.record_ph_adjustment("p7", "down", 0.75, 5.9, 6.5))
    r = g.record_ph_adjustment("p7", "down", 0.75, 6.96, 5.95, 15, 603)
    ck("a real pH Down is learned per ml",
       r.get("recorded") and abs(r["response"]["ph_per_ml"] - 1.347) < 0.01, str(r)[:100])

    print("a dose in the measured proportions is not called an unverified blend")
    store = {"grow_system_p8": json.dumps({"plant_id": "p8", "reservoir_liters": 20.0,
                                           "reservoir_capacity_liters": 20.0}),
             "volume_events_p8": json.dumps([{
                 "at": "2026-10-12T10:00:00", "kind": "dose", "volume_liters": 20.0,
                 "final_liters": 20.0, "ppm_before": 449.0, "ppm_predicted": 866.0,
                 "ppm_predicted_at_final": 866.0, "add_now_ml": {"A": 29.75},
                 "coefficient_ppm_l_per_ml": 280.2, "ratio_from": "caller (stage ratio)",
                 "blend": None, "ratio_matches_measured_mix": True, "reconciled": False}])}
    g = grow(store)
    g._get_readings_for_plant = lambda pid: [{"timestamp": "2026-10-12T10:20:00", "ppm": 751,
                                              "volume_liters": 20.0}]
    r = g.reconcile_dose("p8")
    ck("reconcile finds the logged reading itself", r.get("ppm_after") == 751.0, str(r.get("ppm_after")))
    ck("same-proportion dose is not 'blend_unverified'",
       r.get("classification") != "below_prediction_blend_unverified", str(r.get("classification")))
    ck("20 minutes after dosing, a shortfall is 'not settled', not uptake",
       r.get("classification") == "below_prediction_not_settled", str(r.get("classification")))
    ck("and the dose stays open for the settled reading",
       json.loads(store["volume_events_p8"])[-1].get("reconciled") is False)
    ev8 = json.loads(store["volume_events_p8"])
    g.record_dose_poured("p8", at="2026-10-12T10:00", poured_at="2026-10-12T12:55:00")
    r = g.reconcile_dose("p8")
    ck("mixing time counts from the pour, not from when the plan was written",
       r.get("classification") == "below_prediction_not_settled" and r.get("hours_counted_from") == "pour",
       f"{r.get('classification')} {r.get('hours_since_dose')} h from {r.get('hours_counted_from')}")
    store["volume_events_p8"] = json.dumps([dict(json.loads(store["volume_events_p8"])[-1],
                                                 reconciled=False, poured_at=None)])
    g._get_readings_for_plant = lambda pid: [{"timestamp": "2026-10-12T13:00:00", "ppm": 751,
                                              "volume_liters": 20.0}]
    r = g.reconcile_dose("p8")
    ck("settled after 3 h, the dose is reconciled", r.get("classification") != "below_prediction_not_settled",
       str(r.get("classification")))
    ck("an implied volume above capacity is not read as the volume",
       "more than this vessel holds" in str((r.get("volume_check") or {}).get("reading")),
       str((r.get("volume_check") or {}).get("reading")))

    print("a reading below where the plant is run answers with the dose, and records nothing")
    poured = {"at": "2026-10-10T15:00:00", "kind": "dose", "volume_liters": 18.0,
              "final_liters": 18.0, "ppm_before": 740.0, "ppm_predicted_at_final": 899.0,
              "add_now_ml": {"A": 3.5, "B": 6.25}, "coefficient_ppm_l_per_ml": 173.2,
              "reconciled": True, "reconciled_result": "matches_prediction"}
    store = {"grow_system_p9": json.dumps({"plant_id": "p9", "reservoir_liters": 18.0,
                                           "reservoir_capacity_liters": 20.0}),
             "volume_events_p9": json.dumps([poured])}
    g = grow(store)
    calls = []
    g.plan_feed_for_target = lambda pid, **k: calls.append(k) or {
        "add_now_ml": {"A": 1.0, "B": 2.0}, "target_ppm_after_rounding": 899.0}
    sug = g.feed_suggestion("p9", ppm=732, volume_liters=18)
    ck("a low reading gets a sized dose aimed at the last poured feed's level",
       bool(sug) and sug["target_ppm"] == 899.0 and sug["recorded"] is False, str(sug)[:90])
    ck("the suggestion is never persisted as a plan",
       bool(calls) and calls[0].get("persist") is False)
    ck("an in-spec reading gets no suggestion", g.feed_suggestion("p9", ppm=905, volume_liters=18) is None)

    print("a recorded sighting outranks the age")
    germ = (datetime.now() - timedelta(days=69)).date().isoformat()
    store = {"current_stage": "flower", "germination_date": germ,
             "morphology_index": json.dumps(["m1"]),
             "m1": json.dumps({"plant_id": "current_plant", "derived_stage": "preflower",
                               "lifecycle_stage": "flower", "at": "2026-09-26T10:00:00",
                               "id": "m1"})}
    g = grow(store)
    g._get_readings_for_plant = lambda pid: []
    g._sister_stage_days = lambda pid, stage: []
    r = g.assess_stage("current_plant")
    ck("flower with pistils on record is not 'likely veg'",
       "observed" in str(r.get("assessment")) and not r.get("suggested"), str(r.get("assessment")))

    print("a projection never stands on a gap or on a previous vessel")
    store = {"volume_events_p3": json.dumps([
        {"at": "2026-09-01T10:00:00", "kind": "refill", "from_liters": 3.0, "liters": 5.0},
        {"at": "2026-10-04T21:45:00", "kind": "full_change", "liters": 15.0}])}
    g = grow(store)
    rd = [{"timestamp": "2026-09-01T12:00:00", "volume_liters": 5.0, "ppm": 700},
          {"timestamp": "2026-09-08T12:00:00", "volume_liters": 3.0, "ppm": 720},
          {"timestamp": "2026-09-20T12:00:00", "volume_liters": 5.0, "ppm": 700}]
    g._get_readings_for_plant = lambda pid: rd
    g._get_nutrient_history = lambda pid: []
    gaps = g.record_gaps("p3", since_days=3650)["gaps"]
    ck("a volume rise with no refill logged is a gap",
       any(x["kind"] == "unrecorded_refill" for x in gaps), str([x["kind"] for x in gaps]))
    pr = g.project_water("p3")
    ck("no interval from before the change-out is used, and with none left it refuses",
       pr.get("status") == "refused" and not pr.get("segments_used"), str(pr.get("status")))

    print("girdling: never the main stem, never a full ring")
    from agents.grow_agent.grow_agent import GrowAgent
    gp = GrowAgent.CANNABIS_GIRDLING
    ck("the main stem is forbidden and a full ring is forbidden",
       "main_stem" in gp["forbidden_locations"] and "full_ring" in gp["forbidden_methods"]
       and "main_stem" not in gp["allowed_locations"] and gp["method"] == "partial_score")
    store = {}
    g = grow(store)
    g._vocab = lambda pid: {"girdling": gp}
    g._ripeness_obs = lambda pid: [{"observed_by": "principal", "pistils_brown_pct": 60,
                                    "at": "2026-11-01T10:00:00"}]
    g._volume_events = lambda pid: []
    adv = g.girdling_advice("p4")
    ck("a recommendation says partial score and never a full ring or the main stem",
       adv.get("recommend") and "partial score" in adv["how"] and "never a full ring" in adv["how"]
       and "never the main stem" in adv["how"], str(adv.get("how")))

    print("a stated dependency on a Legal action is kept, not dropped")
    from agents.legal_agent.legal_agent import LegalAgent
    store = {}
    lg = LegalAgent.__new__(LegalAgent)
    lg.agent_id = "legal_agent"
    lg.log = lambda *a, **k: None
    lg.retrieve_own_memory = lambda k: store.get(k)
    lg.store_own_memory = lambda k, v, pin=False: store.__setitem__(k, v) or {"success": True}
    lg._unwrap_value = lambda v: v
    r = lg.open_action({"what": "build the template", "evidence_expected": "a draft",
                        "blocked_by": "the complaint PDF"})
    ck("open_action keeps blocked_by", r.get("blocked_by") == "the complaint PDF",
       str(r.get("blocked_by")))
    r2 = lg.amend_action({"action_id": r.get("id"), "blocked_by": "the PDF, now requested",
                          "reason": "test"})
    ck("amend_action can change blocked_by",
       isinstance(r2, dict) and r2.get("blocked_by") == "the PDF, now requested", str(r2)[:120])

    if fails:
        print(f"\nFAIL: {len(fails)} check(s): {fails}")
        return 1
    print("\nOK - stated facts and observations outrank the record they correct.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
