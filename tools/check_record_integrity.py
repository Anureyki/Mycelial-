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
