#!/usr/bin/env python3
"""Build gate for ATI (ati/). Proves the build spec's contract:

  - the refusal rule: no timestamp / event_type / natal record -> refusal with
    missing_fields and recommendation null, never a partial score
  - an unmapped event type or house is refused, never guessed
  - direct / adjacent / background on the organ clock, including the 23:00-01:00
    window that wraps midnight and adjacency across index 11 -> 0
  - local time comes from the event's place, not the birth place
  - the natal record is written once
  - on demand only: nothing in ati/ starts a thread, a timer or a loop

Uses an in-memory store and test tables passed in - nothing reaches the live
memory service, and the shipped tables (which are the principal's to fill) are
not edited.
"""
import ast
import os
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ati.score import score, score_event, score_snapshot, tables  # noqa: E402
from ati.store import MemoryStore, Refused  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


cfg = tables()
cfg = dict(cfg, event_type_to_house={"test_meal": 6}, house_to_organ={"6": "stomach", "2": "lung"})
natal = {"entity_id": "e1", "birth_datetime_utc": "2000-01-01T00:00:00Z", "birth_location": "x",
         "default_tz": "America/Chicago",
         "bazi_pillars": {"year": {"stem": "geng", "branch": "chen"}, "month": {"stem": "x", "branch": "you"},
                          "day": {"stem": "x", "branch": "si"}, "hour": {"stem": "x", "branch": "zi"}}}

S = score_snapshot
r = S({"event_id": "a"}, None, cfg=cfg)
check("refusal names timestamp, event_type and fixed_natal, recommendation null",
      r["refusal"] and r["recommendation"] is None
      and set(r["missing_fields"]) == {"timestamp", "event_type", "fixed_natal"})
r = S({"event_id": "b", "timestamp_utc": "2026-11-03T14:00:00Z", "event_type": "unknown"}, natal, cfg=cfg)
check("unmapped event type refused, not guessed",
      r["refusal"] and r["missing_fields"] == ["event_type_to_house:unknown"])
r = S({"event_id": "c", "timestamp_utc": "2026-11-03T14:00:00Z", "event_type": "test_meal"},
      natal, cfg=dict(cfg, house_to_organ={}))
check("unmapped house refused", r["refusal"] and r["missing_fields"] == ["house_to_organ:6"])
# 2026-11-03 14:00Z = 08:00 Chicago (CST) -> stomach window
r = S({"event_id": "d", "timestamp_utc": "2026-11-03T14:00:00Z", "event_type": "test_meal"}, natal, cfg=cfg)
check("direct: stomach event in the stomach window",
      not r["refusal"] and r["active_window"]["organ"] == "Stomach" and r["match_level"] == "direct")
check("contract shape: range with en dash, hanzi branch, title element, ordinal house",
      r["active_window"] == {"range": "07:00\u201309:00", "branch": "\u8fb0 Chen", "organ": "Stomach",
                             "element": "Earth"} and r["event_house"] == "6th" and r["event_organ"] == "Stomach")
check("provenance is a list of present layers and confidence counts them",
      r["provenance"] == ["natal", "clock", "house_map"] and r["confidence"] == "3 of 4 layers present")
check("natal pressure names the chen-branch year pillar", "year pillar shares" in r["natal_pressure_note"])
r = S({"event_id": "e", "timestamp_utc": "2026-11-03T16:00:00Z", "event_type": "test_meal"}, natal, cfg=cfg)
check("adjacent: one window away", r["match_level"] == "adjacent")
r = S({"event_id": "f", "timestamp_utc": "2026-11-03T05:30:00Z", "event_type": "test_meal"}, natal, cfg=cfg)
check("23:00-01:00 window wraps midnight", r["active_window"]["organ"] == "Gallbladder")
check("background: far window", r["match_level"] == "background")
check("adjacency wraps across index 11 -> 0", S(
    {"event_id": "g", "timestamp_utc": "2026-11-03T05:30:00Z", "event_type": "test_meal"},
    natal, cfg=dict(cfg, house_to_organ={"6": "triple_burner"}))["match_level"] == "adjacent")
r = S({"event_id": "h", "timestamp_utc": "2026-11-03T14:00:00Z", "event_type": "test_meal",
       "location": {"lat": 35.7, "lon": 139.7, "tz": "Asia/Tokyo"}}, natal, cfg=cfg)
check("local time from the event's place (Tokyo 23:00 -> gallbladder)", r["active_window"]["organ"] == "Gallbladder")
r = S({"event_id": "i", "timestamp_utc": "2026-11-03T14:00:00Z", "event_type": "test_meal"},
      dict(natal, default_tz=None), cfg=cfg)
check("no timezone anywhere is refused, never assumed",
      r["refusal"] and r["missing_fields"][0].startswith("local_timezone"))
check("no sky feed: sky_note null, not in provenance", (lambda x: x["sky_note"] is None and "sky" not in x["provenance"])(
    S({"event_id": "j", "timestamp_utc": "2026-11-03T14:00:00Z", "event_type": "test_meal"}, natal, cfg=cfg)))

st = MemoryStore()
st.put_natal(natal)
try:
    st.put_natal(dict(natal, birth_location="y"))
    check("natal record is write-once", False)
except Refused:
    check("natal record is write-once", st.get_natal("e1")["birth_location"] == "x")
check("score_event refuses an unknown event_id", score_event(st, event_id="nope")["refusal"])
# The Anansi call shape (phase 2): ati.score(entity_id, timestamp_utc, event_type=None, location=None)
r = score("e1", "2026-11-03T14:00:00Z", None, None, store=st, cfg=cfg)
check("contract: event_type null returns window + pressure, event fields null, not a refusal",
      not r["refusal"] and r["active_window"]["organ"] == "Stomach" and r["event_house"] is None
      and r["event_organ"] is None and r["match_level"] is None and r["provenance"] == ["natal", "clock"])
r = score("e1", "2026-11-03T14:00:00Z", "test_meal", {"lat": 41.9, "lon": -87.6, "tz": "America/Chicago"},
          store=st, cfg=cfg)
check("contract: typed call scores", r["match_level"] == "direct")
check("contract: response keys are exactly the contract's", set(r) == {
    "refusal", "active_window", "event_house", "event_organ", "match_level", "natal_pressure_note",
    "sky_note", "confidence", "provenance"})
r = score("nobody", "2026-11-03T14:00:00Z", None, None, store=st, cfg=cfg)
check("contract: no natal record -> refusal with fixed_natal",
      set(r) == {"refusal", "missing_fields", "recommendation"} and r["missing_fields"] == ["fixed_natal"])

banned = {"threading", "sched", "asyncio", "multiprocessing"}
loops = []
for fn in os.listdir(os.path.join(ROOT, "ati")):
    if not fn.endswith(".py"):
        continue
    tree = ast.parse(open(os.path.join(ROOT, "ati", fn)).read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + ([node.module] if getattr(node, "module", None) else [])
            if any(n.split(".")[0] in banned for n in names):
                loops.append(f"{fn}: imports {names}")
        # A bounded loop (store._unwrap digs through at most 8 envelopes) is not a
        # background process; an unconditional one is how a watcher starts.
        if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) and node.test.value:
            loops.append(f"{fn}:{node.lineno} while True")
check("on demand only: no threads, timers or loops in ati/" + (f" ({loops})" if loops else ""), not loops)

print("check_ati: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
