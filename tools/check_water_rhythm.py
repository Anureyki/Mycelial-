#!/usr/bin/env python3
"""Build gate: Grow learns the refill rhythm and the water alert defers to it (2026-11-09).

Rebuilds the 2026-11-05 false alarm on synthetic data relative to now: GSC2 topped up
every ~2 days, last level 36 h old, projected through 10 L. Before the fix that sent
"DANGER - refill now". Now (principal: "give me a projected date"): a stale projection
is a top-up DATE, sent only while upcoming; low/DANGER need a MEASURED level; one
"cleared" notice when a reported level ends a recent alert. No live store is touched: the agent is built bare and every
read and write is stubbed.
"""
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agents.grow_agent.grow_agent import GrowAgent  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


def iso(dt):
    return dt.isoformat(timespec="seconds")


now = datetime.now()


def agent(events, readings, cfg=None):
    g = GrowAgent.__new__(GrowAgent)
    g.agent_id = "grow_agent"
    g.log = lambda *a, **k: None
    mem, sent = {}, []
    g._volume_events = lambda pid: list(events)
    g._get_readings_for_plant = lambda pid: list(readings)
    g._measured_volume = lambda r: r.get("volume_liters")
    g.record_gaps = lambda pid, since_days=21: {"gaps": []}
    g._girdles = lambda pid: []
    g._alerts_cfg = lambda: dict(cfg or {"mode": "live", "low_water_liters": 12, "danger_liters": 10,
                                         "alert_within_hours": 24})
    g._jload = lambda k, d=None: mem.get(k, d if d is not None else {})
    g.store_own_memory = lambda k, v: mem.__setitem__(k, __import__("json").loads(v))
    g.maintenance_due = lambda pid: {"items": []}
    g.ALERT_LOG = os.path.join(__import__("tempfile").mkdtemp(), "alerts.jsonl")

    def a2a(agent_id, task, payload, **k):
        sent.append(payload)
        return {"result": {"sent_any": True}}
    g.send_a2a = a2a
    return g, mem, sent


def fill(at, frm, to):
    return {"at": iso(at), "kind": "refill", "from_liters": frm, "liters": to}


# Fills every ~2 days to 20 L; between fills it drew down; last level 36 h ago at 16 L.
t0 = now - timedelta(days=9)
events = [fill(t0 + timedelta(days=2 * i), 12, 20) for i in range(4)]          # days -9,-7,-5,-3
readings = [{"timestamp": iso(t0 + timedelta(days=2 * i, hours=20)), "volume_liters": 16} for i in range(4)]
readings.append({"timestamp": iso(now - timedelta(hours=36)), "volume_liters": 16})
g, mem, sent = agent(events, readings)

rh = g.refill_rhythm("p")
check("the rhythm is learned from the refill events: ~2 days, to 20 L",
      rh["status"] == "learned" and abs(rh["median_days"] - 2) < 0.05 and rh["usual_to_liters"] == 20)
check("fills within a few hours count as one session",
      GrowAgent.refill_rhythm.__code__ and agent([fill(now - timedelta(days=2, hours=3), 8, 14),
                                                 fill(now - timedelta(days=2), 14, 20)], [])[0]
      .refill_rhythm("p")["sessions"] == 1)
check("two intervals are not a rhythm - it says not_learned rather than guess",
      agent(events[:3], [])[0].refill_rhythm("p")["status"] == "not_learned")

pr = g.project_water("p")
check("a 36 h old level is labelled unconfirmed", pr["unconfirmed"] and pr["level_age_hours"] > 30)
check("a stale projection is a top-up DATE, never a low level or DANGER",
      pr["status"] == "projected_refill" and pr["refill_by"] and "Top-up projected" in pr["message"]
      and "A projection, not a measurement" in pr["message"])
subject, body = g._water_alert_message("GSC-2", pr)
check("its email names the date and never says DANGER, 'refill now' or a projected litre figure",
      "top-up projected around" in subject and "DANGER" not in subject + body
      and "Refill now" not in body and "projected at" not in body)
check("the routine rides along when it is learned", "Your usual: a top-up about every 2" in pr["message"])
fresh = list(readings) + [{"timestamp": iso(now - timedelta(hours=1)), "volume_liters": 9}]
gf, _, _ = agent(events, fresh)
prf = gf.project_water("p")
check("a FRESH low level still raises DANGER - the routine never hides a real one",
      prf["status"] == "danger" and not prf["unconfirmed"]
      and gf._water_alert_message("GSC-2", prf)[0].startswith("DANGER"))

# A date that has already passed sends nothing - the routine covers it (principal).
g2, mem2, sent2 = agent(events, readings)
g2._water_alert("p", "scheduled_backstop")
check("a projected date that has passed sends nothing", pr["refill_by_passed"] and sent2 == [])
# An upcoming date within the window is sent once, with the date.
soon_events = events + [fill(now - timedelta(hours=30), 15, 20)]
soon_reads = readings + [{"timestamp": iso(now - timedelta(hours=14)), "volume_liters": 16}]
g4, mem4, sent4 = agent(soon_events, soon_reads)
p4 = g4.project_water("p")
g4._water_alert("p", "scheduled_backstop")
g4._water_alert("p", "scheduled_backstop")
check("an upcoming projected date within 24 h is sent once, as a date",
      p4["status"] == "projected_refill" and not p4["refill_by_passed"] and len(sent4) == 1
      and "top-up projected around" in sent4[0]["subject"])
# A measured low that fires, then a reported refill: one 'cleared' notice.
g5, mem5, sent5 = agent(events, fresh)
g5._water_alert("p", "intake_reading")
check("a measured low fires", len(sent5) == 1 and sent5[0]["subject"].startswith("DANGER"))
ev5 = events + [fill(now - timedelta(minutes=5), 9, 20)]
g5._volume_events = lambda pid: list(ev5)
g5._get_readings_for_plant = lambda pid: list(fresh) + [{"timestamp": iso(now - timedelta(minutes=4)), "volume_liters": 20}]
g5._water_alert("p", "record_refill")
check("a reported refill that ends it sends one 'cleared' notice",
      len(sent5) == 2 and "cleared" in sent5[-1]["subject"] and "no longer applies" in sent5[-1]["body"])
g5._water_alert("p", "record_level")
check("and only once", len(sent5) == 2)

g3, mem3, sent3 = agent(events, readings, cfg={"mode": "dry_run", "low_water_liters": 12,
                                               "danger_liters": 10, "alert_within_hours": 24})
mem3["water_alerts_sent"] = {f"p:danger:{now.strftime('%Y-%m-%d')}": iso(now)}
g3._volume_events = lambda pid: list(ev5)
g3._get_readings_for_plant = lambda pid: list(fresh) + [{"timestamp": iso(now - timedelta(minutes=4)), "volume_liters": 20}]
g3._water_alert("p", "record_refill")
check("in dry run nothing is sent, and the clearing is logged", sent3 == [] and mem3.get("water_alerts_cleared"))

print("check_water_rhythm: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
