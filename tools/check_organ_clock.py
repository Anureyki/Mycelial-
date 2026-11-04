#!/usr/bin/env python3
"""Build gate for Anansi's organ-clock listener (config/organ_clock_listener.json).

calendar.event.starting arrives over AgentMail's WebSocket; Anansi handles it in
organ_clock_event. This proves, with notify replaced by a recorder and the log
pointed at a temp file (nothing reaches the principal, the live log or Hermes):

  - dry_run logs the event, ATI's answer and the text it WOULD send - and sends nothing
  - live sends only when ATI succeeds; an ATI refusal is logged, never narrated
  - ATI unavailable is logged, never a crash (ATI is not a dependency)
  - a redelivered event (same event_id) is handled once
  - an event that is not one of the 12 organ-clock series is ignored
  - the shipped config starts in dry_run, and only the principal switches it
"""
import json
import os
import sys
import tempfile

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agents.anansi.Anansi import Anansi  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


shipped = json.load(open(os.path.join(ROOT, "config", "organ_clock_listener.json"), encoding="utf-8"))
check("the shipped listener config is dry_run", shipped.get("mode") == "dry_run")

tmp = tempfile.mkdtemp(prefix="organclock_")
SID = "series-heart"


def agent(mode, ati):
    a = object.__new__(Anansi)
    a.agent_id, a.log = "anansi", (lambda *x, **k: None)
    a._clock_seen, a._clock_state = set(), {}
    cfg = dict(shipped, mode=mode, log=os.path.join(tmp, f"{mode}.jsonl"),
               organ_clock_series={"Heart": SID})
    a._clock_cfg = lambda: cfg
    a.sent = []
    a.notify = lambda args: (a.sent.append(args), {"sent": {"dashboard": "ok"}, "unsent": {}})[1]
    if ati == "raise":
        def boom(args):
            raise ImportError("No module named 'ati'")
        a.orientation = boom
    else:
        a.orientation = lambda args: {"text": "That falls in the Heart window (11:00–13:00, Fire).", "ati": ati}
    return a


ok_ati = {"refusal": False, "active_window": {"organ": "Heart"}}
refused = {"refusal": True, "missing_fields": ["fixed_natal"], "recommendation": None}


def ev(eid, sid=SID):
    return {"type": "event", "event_type": "calendar.event.starting", "event_id": eid,
            "scheduled_at": "2026-11-04T17:00:00Z", "calendar_event": {"series_id": sid, "title": "Heart (Fire)"}}


a = agent("dry_run", ok_ati)
r = a.organ_clock_event(ev("e1"))
check("dry_run: logged with ATI's answer and the would-send text, nothing sent",
      r["action"] == "logged only" and r["ati"] == ok_ati and r["would_send"] and a.sent == [])
check("redelivery of the same event_id is handled once", a.organ_clock_event(ev("e1")).get("duplicate") is True)
check("an event outside the 12 organ-clock series is ignored",
      a.organ_clock_event(ev("e2", sid="someone-elses-meeting"))["action"] == "ignored" and a.sent == [])
a = agent("live", ok_ati)
r = a.organ_clock_event(ev("e3"))
check("live + ATI success: delivered through notify, as Anansi", r["action"] == "delivered"
      and len(a.sent) == 1 and a.sent[0]["from_agent"] == "anansi")
a = agent("live", refused)
r = a.organ_clock_event(ev("e4"))
check("live + ATI refusal: logged, never narrated", r["action"] == "logged only" and a.sent == [])
a = agent("live", "raise")
r = a.organ_clock_event(ev("e5"))
check("ATI unavailable: logged as a refusal, no crash, nothing sent",
      r["ati"]["refusal"] and "ati unavailable" in r["ati"]["missing_fields"][0] and a.sent == [])
lines = open(os.path.join(tmp, "dry_run.jsonl")).read().splitlines()
check("every handled event leaves exactly one log line", len(lines) == 2)

# The hexagram briefing (principal, 2026-11-03): in every orientation's data for
# Anansi's reasoning, spoken only on request; ATI's contract shape untouched.
import ati.store as _st  # noqa: E402
_mem = _st.MemoryStore()
_mem.d["natal:p"] = {"entity_id": "p", "birth_datetime_utc": "x", "birth_location": "x",
                     "bazi_pillars": {"year": {"branch": "xu"}, "month": {"branch": "chou"},
                                      "day": {"branch": "zi"}, "hour": {"branch": "yin"}}}
_real = _st.HermesStore
_st.HermesStore = lambda agent: _mem
try:
    an = object.__new__(Anansi)
    an.agent_id, an.log = "anansi", (lambda *x, **k: None)
    call = {"entity_id": "p", "timestamp_utc": "2026-11-04T01:21:00Z", "event_type": None,
            "location": {"tz": "America/Chicago"}}
    quiet, spoken = an.orientation(dict(call)), an.orientation(dict(call, speak_hexagram=True))
finally:
    _st.HermesStore = _real
h = (quiet.get("briefing") or {}).get("hexagram") or {}
check("the moment's hexagram is in the orientation briefing (25 無妄, line 4 -> 42 益)",
      h.get("primary") == "25 無妄" and h.get("moving_line") == 4 and h.get("changed") == "42 益")
check("not spoken by default: the told text carries no cast", "cast" not in quiet["text"] and "無妄" not in quiet["text"])
check("spoken only when asked (speak_hexagram)", "25 無妄" in spoken["text"])
check("ATI's response keeps the contract's exact keys - the briefing sits beside it",
      set(quiet["ati"]) == {"refusal", "active_window", "event_house", "event_organ", "match_level",
                            "natal_pressure_note", "sky_note", "confidence", "provenance"})

# ui/organ_clock.html carries an embedded copy of the windows for when it is opened
# from disk; the config is the source, and the copy must equal it or the build fails.
import re as _re  # noqa: E402
page = open(os.path.join(ROOT, "ui", "organ_clock.html"), encoding="utf-8").read()
m = _re.search(r'<script type="application/json" id="windows-fallback">(.*?)</script>', page, _re.S)
embedded = json.loads(m.group(1)) if m else None
cfg_w = json.load(open(os.path.join(ROOT, "config", "ati", "organ_clock_windows.json"), encoding="utf-8"))["windows"]
keys = ("start", "end", "organ", "organ_display", "element", "branch", "branch_display")
check("ui/organ_clock.html's embedded windows equal config/ati/organ_clock_windows.json",
      embedded is not None and [{k: w[k] for k in keys} for w in embedded] == [{k: w[k] for k in keys} for w in cfg_w])
check("the page stays a static file under 200 lines with no backend call",
      page.count("\n") < 200 and "fetch(\"../config/ati/organ_clock_windows.json\")" in page
      and "localhost" not in page)

print("check_organ_clock: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
