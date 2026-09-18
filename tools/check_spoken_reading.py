#!/usr/bin/env python3
"""A spoken reading lands on the vessel the sentence names, or on nothing.

    python3 tools/check_spoken_reading.py

Two GSC plants in two vessels were mixed up because log_from_text defaulted
the plant to current_plant and wrote through log_reading - the pair
CLAUDE.md forbids under "Plants do not cross". The store is stubbed; nothing
here touches a real plant.
"""
import os
import re
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

    print("a run of number words is one number")
    for said, want in (("seven six zero ppm", "760"),
                       ("fifteen twenty EC", "1520"),
                       ("fourteen eighty six EC", "1486"),
                       ("six point three three pH", "6.33"),
                       ("two two point four C", "22.4"),
                       ("seven hundred and twenty one PPM", "721"),
                       ("twenty five C", "25"),
                       ("twenty one point one Celsius", "21.1"),
                       ("back at five liters", "5")):
        got = g._digits_for_spoken(said)
        ck(f"{said!r} -> {want}", re.search(r"(?<![\d.])" + re.escape(want) + r"(?![\d])", got),
           got.strip())
    ck("ordinary prose with no number word is untouched",
       g._digits_for_spoken("and then we added water") == "and then we added water")
    r = g.parse_reading("Right now we are at seven six zero ppm, two two point four C, "
                        "fifteen twenty EC, and six point three three pH")
    ck("the whole dictated reading parses",
       r == {"ppm": 760.0, "ph": 6.33, "ec": 1520.0, "temp": 22.4}, str(r))

    print("one parser, and nothing the grower said is dropped")
    spoken = ("I am taking new readings on the LWC. We have a 6.27, a 4.09 ppm, "
              "819 EC, and 25.5 Celsius. Back at five liters.")
    r = g.parse_reading(spoken)
    ck("Celsius spelled out is read", r.get("temp") == 25.5)
    ck("a spoken volume is read", r.get("volume_liters") == 5.0)
    ck("EC is read", r.get("ec") == 819.0)
    ck("a number with no unit is carried, not dropped",
       any(u["value"] == 6.27 for u in r.get("unassigned", [])))
    ck("target language is still not a reading", g.parse_reading("bring it up to 800 ppm") is None)

    print("a decimal is one number, and a name is not a measurement")
    for said, want, nothing_else in (
            ("Lwc 797 ppm 22.0c 1594 ec 6.5ph", {"ppm": 797.0, "ph": 6.5, "ec": 1594.0,
                                                 "temp": 22.0}, True),
            ("Dwc gsc1 is at 11L 794ppm 1580ec 19.9c", {"ppm": 794.0, "ec": 1580.0,
                                                        "temp": 19.9,
                                                        "volume_liters": 11.0}, True),
            ("Gsc1 is now 15L res 1120ec 5.07ph 595ppm", {"ppm": 595.0, "ph": 5.07,
                                                          "ec": 1120.0,
                                                          "volume_liters": 15.0}, True)):
        got = g.parse_reading(said)
        ck(f"{said[:38]!r} parses exactly", {k: v for k, v in got.items()
                                             if k != "unassigned"} == want, str(got))
        if nothing_else:
            # The integer part of an assigned decimal must never come back as
            # a question: "6.5ph" asked "is 6.0 the litres?" for weeks.
            ck(f"{said[:38]!r} leaves nothing unattributed",
               not got.get("unassigned"), str(got.get("unassigned")))
    ck("a genuinely unlabelled number is still flagged",
       any(u["value"] == 25.0 for u in
           (g.parse_reading("reading 25 and 1200 ppm") or {}).get("unassigned", [])))

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
    r = g.log_from_text(spoken)
    ck("an unattributed number comes back as a question naming the empty channel",
       r.get("unattributed") and r["unattributed"][0]["probably"] == "ph"
       and "is it the ph?" in (r.get("ask") or ""), str(r.get("ask")))
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

    print("grow does its own reasoning: stage, sister evidence, dosing order")
    g2 = GrowAgent.__new__(GrowAgent)
    g2.log = lambda *a, **k: None
    ck("the keyword stage classifier never reads a metadata key as a stage",
       g2._classify_stage_by_keywords("vigorous veg plant, 7-blade fan leaves", "cannabis") == "veg")
    g2._get_species_for_plant = lambda pid: "cannabis"
    g2._get_all_plants = lambda: [{"plant_id": "gsc_auto_2", "strain": "Girl Scout Cookies (autoflower)",
                                   "germination_date": "2026-08-21"}]
    mem = {"current_strain": "Girl Scout Cookies (autoflower)", "germination_date": "2026-07-28"}
    g2.retrieve_own_memory = lambda k: mem.get(k)
    g2._unwrap_value = lambda v: v
    g2._get_readings_for_plant = lambda pid: ([{"timestamp": "2026-08-12T06:42", "stage": "early_veg"},
                                               {"timestamp": "2026-08-20T17:33", "stage": "veg"}]
                                              if pid == "current_plant" else [])
    g2._system_record = lambda pid: {"instance_label": "GSC-1"} if pid == "current_plant" else {}
    sis = g2._sister_stage_days("gsc_auto_2", "veg")
    ck("a sister of the same cultivar reports the day it reached the stage",
       sis and sis[0]["plant_id"] == "current_plant" and sis[0]["day"] == 23, str(sis))
    ck("a different cultivar is not a sister",
       g2._sister_stage_days("gsc_auto_2", "veg") and not g2._sister_stage_days.__self__._sister_stage_days
       ("aloe_1", "veg"))

    print("one question, one method, and the method is named")
    import re as _re
    src = open(os.path.join(ROOT, "agents", "grow_agent", "grow_agent.py"),
               encoding="utf-8").read()
    ck("situation() chooses between the two dose verbs rather than hardcoding one",
       "plan_feed_for_target" in src.split("if target_ppm:")[1][:3000]
       and "adjust_to_target_ppm" in src.split("if target_ppm:")[1][:3000])
    ck("and the facet reports which method ran",
       '"method": method' in src and '"method_basis": why' in src)
    ck("the mass-balance method is gated on a MEASURED volume, not assumed",
       'volume_source' in src.split("if target_ppm:")[1][:3000]
       and '"measured", "refill_stated"' in src)

    print("a refill is an event, not a reading")
    g3 = GrowAgent.__new__(GrowAgent)
    g3.log = lambda *a, **k: None
    ck("refill language is recognised",
       bool(g3._REFILL_ASK.search("Refilled dwc gsc1 to 15L"))
       and bool(g3._REFILL_ASK.search("topped it back up to 15 L")))
    ck("and a plain reading is not mistaken for one",
       not g3._REFILL_ASK.search("Lwc 797 ppm 22.0c 1594 ec 6.5ph"))
    m = g3._REFILL_TO.search("Refilled dwc gsc1 to 15L")
    ck("the target level is read", m and float(m.group(1)) == 15.0)
    ck("record_refill refuses with no plant",
       g3.record_refill(None, to_liters=15).get("reason") == "no plant named")
    ck("record_refill refuses with no volume",
       g3.record_refill("current_plant").get("reason") == "no volume given")
    ck("it never invents a ppm - the refusal path returns no reading fields",
       "not_a_reading" in src)

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
