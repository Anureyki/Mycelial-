#!/usr/bin/env python3
"""A spoken reading lands on the vessel the sentence names, or on nothing.

    python3 tools/check_spoken_reading.py

Two GSC plants in two vessels were mixed up because log_from_text defaulted
the plant to current_plant and wrote through log_reading - the pair
CLAUDE.md forbids under "Plants do not cross". The store is stubbed; nothing
here touches a real plant.
"""
import os
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    from agents.grow_agent.grow_agent import GrowAgent
    g = GrowAgent.__new__(GrowAgent)

    print("parse_reading reads all four channels and the volume")
    r = g.parse_reading("the DWC is at 775 ppm, 1550 EC, pH 5.8, 20.6 C at 15 L")
    ck("ppm, EC, pH, temp, volume all parsed",
       r == {"ppm": 775.0, "ph": 5.8, "temp": 20.6, "ec": 1550.0, "volume_liters": 15.0}, str(r))
    ck("target language is not a reading", g.parse_reading("bring the DWC up to 800 ppm") is None)

    print("log_from_text never defaults the plant")
    records = {"current_plant": {"instance_label": "GSC-1", "vessel": "DWC bucket", "alive": True},
               "gsc_auto_2": {"instance_label": "GSC-2", "vessel": "LWC nursery", "alive": True},
               "dead_1": {"instance_label": "Dead-1", "vessel": "gone", "alive": False}}
    g._plant_from_text = lambda t: ("current_plant" if "dwc" in t.lower() else
                                    "gsc_auto_2" if "lwc" in t.lower() else
                                    "dead_1" if "dead" in t.lower() else None)
    g._system_record = lambda pid: records.get(pid, {})
    calls = []

    def fake_intake(**kw):
        calls.append(kw)
        return {"stored": True, "cleaned": {k: v for k, v in kw.items() if v is not None}, "problems": []}
    g.intake_reading = fake_intake

    r = g.log_from_text("775 ppm and pH 5.8")
    ck("no vessel named -> nothing stored, and it says so", not r["logged"] and r["reason"] == "no plant named"
       and "Nothing was stored" in r["ask"])
    ck("intake was not called", calls == [])
    r = g.log_from_text("LWC 600 ppm pH 6.0")
    ck("vessel named -> stored on that plant, with the receipt",
       r["logged"] and r["plant_id"] == "gsc_auto_2" and r["receipt"].startswith("GSC-2 in LWC"))
    ck("intake was called with the named plant, not current_plant",
       calls and calls[-1]["plant_id"] == "gsc_auto_2")
    r = g.log_from_text("the dead one reads 500 ppm pH 6")
    ck("a plant recorded as not alive takes no reading", not r["logged"] and r["reason"] == "plant not alive")

    print("answer() writes the reading down before anything else")
    r = g.answer("775 ppm and pH 5.8")
    ck("answer refuses a vessel-less reading rather than answering about current_plant",
       r.get("answered_as") == "reading_refused" and r.get("plant_id") is None)
    r = g.answer("the DWC is at 775 ppm, pH 5.8")
    ck("answer logs a named reading and echoes the receipt",
       r.get("answered_as") == "reading_logged" and r.get("receipt", "").startswith("GSC-1 in DWC"))

    print("the receipt verb is declared everywhere it is dispatched")
    import json
    for p in ("config/agent_configs/grow_agent.json", "config/agent_cards/grow_agent.json"):
        with open(os.path.join(ROOT, p)) as fh:
            ck(f"which_plant declared in {p}", "which_plant" in json.load(fh)["capabilities"])

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails[:6]))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
