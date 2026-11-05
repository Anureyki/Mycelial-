#!/usr/bin/env python3
"""Build gate for Grow's per-day environment series (the growth-response work, increment 1).

Synthetic tent rows only - nothing reads or writes the live store:
  - a full day and a partial day of export rows give per-day light and climate,
    with the partial day flagged rather than read as a whole day
  - VPD is computed from temperature and humidity when the export has no VPD column
  - light and climate are each judged by their own sensor's cadence
  - the backfill's dry run writes nothing; apply writes only derived fields
"""
import csv
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agents.grow_agent.grow_agent import GrowAgent  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


tmp = tempfile.mkdtemp(prefix="growdata_")
path = os.path.join(tmp, "export.csv")
start = datetime(2030, 1, 1, 0, 0)
with open(path, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh)
    w.writerow(["deviceSerialnum", "Timestamp", "temperature(°C)", "humidity", "ppfd"])
    for i in range(1440 + 600):                      # day 1 full (1-min), day 2 only 600 minutes
        t = start + timedelta(minutes=i)
        lit = 6 <= t.hour < 24
        w.writerow(["SYN", t.strftime("%Y-%m-%d %H:%M:%S"), 25.0, 50.0, 500 if lit else 0])

g = object.__new__(GrowAgent)
g.agent_id, g.log = "grow_agent", (lambda *a, **k: None)
light, climate = g._daily_series(g._export_rows(path))
l1, l2 = light["by_day"]
c1, c2 = climate["by_day"]
check("per-day light: 18 h lit at 500 PPFD gives DLI 32.4 on the full day",
      l1["hours_lit"] == 18.0 and l1["dli_mol_m2"] == 32.4 and not l1["partial"])
check("a day the export only partly covers is flagged partial", l2["partial"] and c2["partial"])
check("climate per day: temperature and humidity min/mean/max", c1["temp_c"]["mean"] == 25.0 and c1["humidity_pct"]["max"] == 50.0)
check("VPD computed from temperature and humidity when the export has no VPD column (25 C, 50% -> ~1.58 kPa)",
      abs(c1["vpd_kpa"]["mean"] - 1.58) < 0.02)

ev = [{"source_file": path, "since": None, "light": {"by_day": None}, "climate": None}]
g._environment_events = lambda pid: ev if pid == "current_plant" else []
g._get_all_plants = lambda: []
writes = []
g.store_own_memory = lambda k, v: writes.append((k, json.loads(v)))
r = g.backfill_environment_days(apply=False)
check("backfill dry run reports what it would fill, writes nothing, and leaves the events it read untouched",
      r["plants"][0]["climate_filled"] == 1 and r["plants"][0]["light_filled"] == 1 and writes == []
      and ev[0]["climate"] is None)
r = g.backfill_environment_days(apply=True)
st = writes[0][1][0] if writes and writes[0][0] == "environment_events_current_plant" else {}
check("backfill apply stores derived per-day fields only, from the event's own raw file",
      len(writes) == 1 and st.get("climate", {}).get("by_day") and st.get("light", {}).get("by_day")
      and st.get("source_file") == path)

print("check_growth_data: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
