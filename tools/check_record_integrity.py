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

    print("named top-up water carries its measured ppm into the prediction")
    store = {"grow_system_p10": json.dumps({"plant_id": "p10", "reservoir_liters": 15.0,
                                            "reservoir_capacity_liters": 20.0,
                                            "dehumidifier_water_ppm": 20})}
    g = grow(store)
    g._water_alert = lambda *a, **k: None
    g._get_readings_for_plant = lambda pid: [{"timestamp": "2026-10-01T10:00:00",
                                              "volume_liters": 15.0, "ppm": 900}]
    r = g.record_refill("p10", to_liters=18, added_liters=3, water="dehumidifier",
                        at="2026-10-02T10:00:00")
    ck("dehumidifier water's 20 ppm is in the prediction (900x15 + 20x3)/18 = 753",
       (r.get("prediction") or {}).get("ppm_after_refill") == 753.0, str(r.get("prediction")))
    ck("and the refill says which water it was", r.get("water") == "dehumidifier")
    r = g.record_refill("p10", to_liters=20, added_liters=2, water="di", at="2026-10-03T10:00:00")
    r2 = g.record_refill("p10", to_liters=18, from_liters=16, at="2026-10-04T10:00:00")
    ck("a stated start works like a stated amount", r2.get("from_liters") == 16.0 and r2.get("added_liters") == 2.0,
       f"{r2.get('from_liters')} -> {r2.get('to_liters')}")
    ck("DI is 0 ppm", r.get("water_basis") == "DI water taken as 0 ppm", str(r.get("water_basis")))

    print("an unmeasured pH addition is kept but never becomes the response")
    store = {"grow_system_p13": json.dumps({"plant_id": "p13", "reservoir_liters": 18.0,
                                            "ph_down_response": {"ml": 0.75, "delta_ph": -1.01}})}
    g = grow(store)
    r = g.record_ph_adjustment("p13", "down", 0.5)
    rec = json.loads(store["grow_system_p13"])
    ck("recorded as unmeasured", r.get("unmeasured") and rec["ph_down_response_history"][-1]["unmeasured"])
    ck("the working response is unchanged", rec["ph_down_response"]["ml"] == 0.75)

    print("a simulated water alert sends the real words and touches no record")
    store = {"grow_system_p15": json.dumps({"plant_id": "p15", "instance_label": "GSC1"})}
    g = grow(store)
    g._alerts_cfg = lambda: {"mode": "dry_run", "low_water_liters": 12, "danger_liters": 10}
    g.ALERT_LOG = os.path.join(__import__("tempfile").mkdtemp(), "alert_log.jsonl")
    sent = []
    g.send_a2a = lambda agent, task, payload: sent.append(payload) or {"result": {"sent_any": True}}
    before = dict(store)
    r = g.simulate_water_alert("p15", "low", hours=18)
    real_subject, _ = g._water_alert_message("GSC1", {"status": "ok", "low_water_liters": 12,
                                                     "hours_to_crossing": 18, "message": "x"})
    ck("it sends even in dry_run, through notify", r["sent_any"] and len(sent) == 1)
    ck("the subject is the real alert's subject, tagged SIMULATION",
       sent[0]["subject"].startswith("[SIMULATION") and sent[0]["subject"].endswith(real_subject))
    ck("no plant record and no sent-today key were written", store == before, str(set(store) - set(before)))
    g.project_water = lambda pid: {"projected_now": 18.0}
    sent.clear()
    g.simulate_water_alert("p15", "low", hours=18)
    ck("the simulation states the plant's real level before the hypothetical",
       "actually at about 18.0 L" in sent[0]["body"]
       and sent[0]["body"].index("actually") < sent[0]["body"].index("Projected"), sent[0]["body"][:120])

    print("ripeness reminders: due with no reading, dated after one, silent in veg")
    from datetime import date as _d
    g = grow({})
    g._active_plants = lambda: ["pf", "pv"]
    g._stage_for_plant = lambda pid: "flower" if pid == "pf" else "veg"
    obs = {"pf": []}
    g._ripeness_obs = lambda pid: obs.get(pid, [])
    g.assess_ripeness = lambda pid: {"projection": {"days_to_window_estimate": None}}
    r = g._ripeness_reminders(today=_d(2026, 10, 18))
    ck("a flowering plant with no reading is due now", [i["key"].split(":")[2] for i in r["items"]] == ["first"],
       str([i["key"] for i in r["items"]]))
    obs["pf"] = [{"observed_by": "principal", "at": "2026-10-18T09:00:00", "pistils_brown_pct": 10}]
    r = g._ripeness_reminders(today=_d(2026, 10, 19))
    ck("after a reading the next check is announced, dated a week on",
       r["items"] and r["items"][0]["key"] == "ripeness:pf:next:2026-10-25", str([i["key"] for i in r["items"]]))
    r = g._ripeness_reminders(today=_d(2026, 10, 26))
    ck("on and after that date it is due", r["items"][0]["urgency"] == "due")
    obs["pf"] = [{"observed_by": "claude_from_photo", "at": "2026-10-18T09:00:00", "pistils_brown_pct": 10}]
    ck("a model's read does not count as the grower's reading",
       g._ripeness_reminders(today=_d(2026, 10, 19))["items"][0]["key"].split(":")[2] == "first")

    print("feeds and change-outs: no full feed into a change-out, and water prep ahead of it")
    from datetime import datetime as _dt, timedelta as _td
    lc = (_dt.now() - _td(days=13)).isoformat(timespec="seconds")
    store = {"grow_system_p16": json.dumps({"plant_id": "p16", "system_type": "dwc",
                                            "reservoir_capacity_liters": 20.0, "reservoir_liters": 20.0}),
             "volume_events_p16": json.dumps([{"at": "2026-10-10T10:00:00", "kind": "dose", "add_now_ml": {"A": 4},
                                               "reconciled": True, "reconciled_result": "matches_prediction",
                                               "ppm_predicted_at_final": 900.0}])}
    g = grow(store)
    g._active_plants = lambda: ["p16"]
    g._last_full_change = lambda pid: {"timestamp": lc}
    g._get_readings_for_plant = lambda pid: []
    g._stage_for_plant = lambda pid: "flower"
    g.handle_task = lambda task, args, src=None: {"result": {"ppm": [800, 1200]}}
    calls = []
    g.plan_feed_for_target = lambda pid, **k: calls.append(k) or {"add_now_ml": {"A": 1.0}, "target_ppm_after_rounding": k["target_ppm"]}
    sug = g.feed_suggestion("p16", ppm=850, volume_liters=20)
    ck("in band with a change-out a day away: no top-up, and it says why",
       sug and not sug["add_ml"] and "change_out_conflict" in sug and not calls, str(sug)[:120])
    sug = g.feed_suggestion("p16", ppm=700, volume_liters=20)
    ck("below band with a change-out due: dosed only to the band floor",
       sug and calls and calls[-1]["target_ppm"] == 800 and "change_out_conflict" in sug, str(calls[-1:]))
    lc = (_dt.now() - _td(days=10)).isoformat(timespec="seconds")
    m = [i for i in g.maintenance_due("p16")["due"] if i["kind"] == "full_change"]
    ck("four days out the reminder says prepare, with litres", m and m[0]["status"] == "prepare"
       and "20.0 L" in m[0]["text"], str(m[:1]))
    g.defer_full_change("p16", (_dt.now() + _td(days=4)).date().isoformat(), "just fed; water not ready")
    lc = (_dt.now() - _td(days=15)).isoformat(timespec="seconds")
    m = [i for i in g.maintenance_due("p16")["items"] if i["kind"] == "full_change"]
    ck("a deferred change-out is 'deferred' with its reason, not overdue",
       m and m[0]["status"] == "deferred" and "just fed" in m[0]["text"], str(m[:1]))

    print("drink-down: no top-ups, change-out at the target level, concentration watched")
    store = {"grow_system_p17": json.dumps({"plant_id": "p17", "system_type": "dwc",
                                            "reservoir_capacity_liters": 20.0, "reservoir_liters": 19.0})}
    g = grow(store)
    g._alerts_cfg = lambda: {"low_water_liters": 12, "danger_liters": 10}
    g._active_plants = lambda: ["p17"]
    g._last_full_change = lambda pid: {"timestamp": "2026-10-04T10:00:00"}
    g._stage_for_plant = lambda pid: "flower"
    g.handle_task = lambda task, args, src=None: {"result": {"ppm": [800, 1200]}}
    level = {"v": 19.0}
    g.project_water = lambda pid: {"projected_now": level["v"], "rate_l_per_day": 1.4}
    g._get_readings_for_plant = lambda pid: [{"timestamp": "2026-10-19T08:00:00", "ppm": 918}]
    ck("a target at or below the danger level is refused",
       "error" in g.start_drink_down("p17", 10, "test"))
    r = g.start_drink_down("p17", 12, "just fed - use the nutrients before the change-out")
    m = [i for i in g.maintenance_due("p17")["items"] if i["kind"] == "full_change"]
    ck("15-day-old reservoir in drink-down is not 'due' - it is drinking down, dated",
       m and m[0]["status"] == "drink_down" and "12 L in about 5.0 days" in m[0]["text"], str(m[:1])[:160])
    ck("no feed is suggested during the drink-down",
       (g.feed_suggestion("p17", ppm=700, volume_liters=19) or {}).get("add_ml") == {})
    level["v"] = 11.8
    m = [i for i in g.maintenance_due("p17")["items"] if i["kind"] == "full_change"]
    ck("at the target level the change-out is due", m and m[0]["status"] == "due", str(m[:1])[:120])
    g._get_readings_for_plant = lambda pid: [{"timestamp": "2026-10-21T08:00:00", "ppm": 1290}]
    st, txt = g._drink_down_status("p17", {"target_liters": 12})
    ck("concentration above the band top is called out", "ABOVE the 1200" in txt)
    subj, _ = g._water_alert_message("GSC1", {"status": "at_or_below_low_water", "message": "x",
                                              "low_water_liters": 12}, drink_down=True)
    ck("the water alert says change-out, not refill, during a drink-down", "change-out" in subj, subj)

    print("a flower photo goes to the vision model, and its read is never the grower's")
    import tempfile as _tf
    store = {}
    g = grow(store)
    import agents.grow_agent.grow_agent as _gm
    _gm.VISION_AVAILABLE = True
    g._vision_prompt_for = lambda pid: "This is a cannabis plant. Describe it."
    answers = {"orange": "No.", "fresh white": "Yes.", "mold": "Yes, there is gray fuzz.", "seed": "No."}
    g._call_inference_vision = lambda q, p, timeout=90: next((v for k, v in answers.items() if k in q.lower()),
                                                             "The pistils are white.")
    g._vocab = lambda pid: {"ripeness": {"method": "trichome_shares"}}
    photo = _tf.NamedTemporaryFile(suffix=".jpg", delete=False).name
    r = g.read_flower_photo("p18", photo)
    ck("the read is recorded as the model's", r.get("recorded")
       and json.loads(store["ripeness_p18"])[-1]["observed_by"] == "moondream")
    ck("a mould yes becomes 'check by eye', not a diagnosis",
       len(r.get("check_by_eye") or []) == 1 and "look closely" in r["check_by_eye"][0])
    ck("it is never counted for ripeness", r.get("counted_for_ripeness") is False)

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

    print("a reading days after a dose is not the dose's result")
    store = {"grow_system_p11": json.dumps({"plant_id": "p11", "reservoir_liters": 20.0,
                                            "reservoir_capacity_liters": 20.0}),
             "volume_events_p11": json.dumps([{
                 "at": "2026-10-12T05:00:00", "poured_at": "2026-10-12T05:12:00", "kind": "dose",
                 "volume_liters": 20.0, "final_liters": 20.0, "ppm_before": 449.0,
                 "ppm_predicted": 873.0, "ppm_predicted_at_final": 873.0,
                 "add_now_ml": {"A": 30.25}, "coefficient_ppm_l_per_ml": 280.2,
                 "ratio_from": "caller (stage ratio)", "ratio_matches_measured_mix": True,
                 "blend": "stage_ratio:veg", "reconciled": False}])}
    g = grow(store)
    g.calibrate_blend = lambda *a, **k: {}
    g._get_readings_for_plant = lambda pid: [{"timestamp": "2026-10-16T10:00:00", "ppm": 493,
                                              "volume_liters": 20.0}]
    r = g.reconcile_dose("p11")
    ev = json.loads(store["volume_events_p11"])[-1]
    ck("four days later it is no_settled_reading", r.get("classification") == "no_settled_reading",
       str(r.get("classification")))
    ck("and the late reading is never stored as the dose's result",
       "measured_ppm_after" not in ev and ev.get("late_reading_ppm") == 493)

    print("calibration never learns from an unmixed reading")
    store = {"volume_events_p14": json.dumps([{
        "at": "2026-10-16T10:00:00", "poured_at": "2026-10-16T10:05:00", "kind": "dose",
        "volume_liters": 18.2, "final_liters": 18.2, "ppm_before": 493.0, "add_now_ml": {"A": 22.75},
        "blend": "stage_ratio:veg", "reconciled": True, "reconciled_result": "no_settled_reading"}])}
    g = grow(store)
    g._get_readings_for_plant = lambda pid: [{"timestamp": "2026-10-16T10:06:00", "ppm": 733,
                                              "volume_liters": 18.2}]
    c = g.calibrate_blend("p14", "stage_ratio:veg", write=False)
    ck("a reading one minute after the pour is not the dose's result", not c.get("n"), str(c)[:120])

    print("a carried-forward level is not a measurement")
    g = grow({})
    g._get_readings_for_plant = lambda pid: [
        {"timestamp": "2026-10-12T05:00:00", "volume_liters": 20.0, "ppm": 751},
        {"timestamp": "2026-10-16T07:11:00", "volume_liters": 20.0, "ppm": 493,
         "volume_source": "carried forward"}]
    g._volume_events = lambda pid: []
    pts = g._volume_points("p12")
    ck("the water projection ignores a carried-forward volume",
       [p["at"][:10] for p in pts if p["kind"] == "reading"] == ["2026-10-12"], str(pts))

    print("the deadline register counts the way the statutes do")
    store = {"deadline_index": "[]"}
    lg = LegalAgent.__new__(LegalAgent)
    lg.agent_id = "legal_agent"
    lg.log = lambda *a, **k: None
    lg.retrieve_own_memory = lambda k: store.get(k)
    lg.store_own_memory = lambda k, v, pin=False: store.__setitem__(k, v) or {"success": True}
    lg._unwrap_value = lambda v: v
    lg.lookup_reference = lambda c: [{"text": "held", "title": c}]
    f = lg.add_deadline(name="FOIA appeal", trigger_date="2026-10-13", period=20,
                        period_unit="business_days", citation="5 U.S.C. 552")
    ck("FOIA: 20 business days from 10/13/2026 is 11/10 (weekends, 6103 holidays out)",
       f.get("due") == "2026-11-10", str(f.get("due")))
    sf = lg.add_deadline(name="SF-95", trigger_date="2026-10-13", period=6, period_unit="months",
                         kind="opens_option", citation="28 U.S.C. 2675",
                         watch_for=[{"event": "denial_mailed", "creates": {
                             "name": "FTCA suit", "period": 6, "period_unit": "months",
                             "citation": "28 U.S.C. 2401"}}])
    ck("six months' silence is an option opening, not a deadline",
       sf.get("kind") == "opens_option" and sf.get("due") == "2027-04-13")
    ch = lg.record_deadline_event(sf["id"], "denial_mailed", "2027-05-03").get("created") or {}
    ck("a mailed denial starts six months from the mailing date (2401(b))",
       ch.get("due") == "2027-11-03" and ch.get("kind") == "deadline", str(ch.get("due")))
    un = lg.add_deadline(name="Privacy Act", kind="unknown", period=24, period_unit="months",
                         citation="5 U.S.C. 552a", unknown_reason="date the harm was known")
    chk = lg.check_deadline_register()
    ck("an unknown start is flagged, never skipped",
       any(x["id"] == un["id"] and "unknown" in x["why"] for x in chk["flagged"]))
    ck("every check stamps last_checked",
       all(json.loads(store[d["id"]]).get("last_checked") for d in chk["register"]))
    ck("a dated deadline with no start is refused",
       "error" in lg.add_deadline(name="x", period=5, citation="5 U.S.C. 552"))

    if fails:
        print(f"\nFAIL: {len(fails)} check(s): {fails}")
        return 1
    print("\nOK - stated facts and observations outrank the record they correct.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
