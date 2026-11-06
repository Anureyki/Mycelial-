#!/usr/bin/env python3
"""A timing calendar: what ATI can compute about each day of a window, against one natal record.

    from ati.calendar import timing_calendar
    timing_calendar(natal_record, start=date(2026, 11, 7), days=91, tz="America/Chicago")

Built for the principal's 2026-11-06 request ("a 3-month ... plan ... using projected
transits that would affect me"), option (a): the TIMING half only. This is calculation,
not advice - no workout, no rating of a day as good or bad. What to do with a day
belongs to whatever owns that subject; ATI only says what the day is.

Per day, at local noon in `tz`:
  pillars     the day's year, month and day pillars (ati/bazi.py). The hour pillar is
              left out - a calendar day has no single hour.
  interactions  each of those against each natal pillar, by the classical rules
              already in ati/lighting.py: stem generation and control, branch clash.
              Those touching the natal DAY pillar (the day master) are listed apart,
              because that is the pillar the classical reading centres on - the
              listing is the rule's, the weighting is nobody's yet.
  houses      which whole-sign house of the NATAL chart each body occupies
              (ati/ephemeris.py, via ati/lighting.houses - the same ascendant check).
  lunar       the Chinese lunisolar date (ati/lunar.py); day 1 is a new moon.
And over the window:
  ingresses   each date a body enters a different natal house - the house-level
              "transit" ATI can compute honestly. Aspects (a body's angle to a natal
              body) are NOT computed; there is no aspect engine here, and saying so
              is the point of this line.

PRIVACY, the same rule lighting runs: house numbers and body names only - never a
degree, a sign, the ascendant or a natal pillar's value. Computed per request,
nothing stored. An unavailable ephemeris leaves the houses unlit and says why; the
pillars do not depend on it.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ati.bazi import pillars, STEMS
from ati.lighting import interactions, houses, PILLARS
from ati.score import tables

MAX_DAYS = 120
DAY_PILLARS = ("year", "month", "day")


def _natal_pillars(rec):
    bp = (rec or {}).get("bazi_pillars") or {}
    if not isinstance(bp, dict):
        return {}
    return {k: {kk: bp[k].get(kk) for kk in ("stem", "branch")}
            for k in PILLARS if isinstance(bp.get(k), dict) and bp[k].get("stem")}


def _house_map(lit):
    return {b["name"]: h["house"] for h in lit for b in h["bodies"]}


def timing_calendar(natal_record, start=None, days=91, tz="America/Chicago"):
    try:
        zone = ZoneInfo(str(tz))
    except Exception:
        return {"error": f"unknown timezone {tz!r}"}
    try:
        days = int(days)
    except (TypeError, ValueError):
        return {"error": "days must be a whole number"}
    if not 1 <= days <= MAX_DAYS:
        return {"error": f"days must be 1-{MAX_DAYS}; a longer window is a longer guess about nothing"}
    if start is None:
        start = datetime.now(zone).date()
    elif isinstance(start, str):
        try:
            start = date.fromisoformat(start)
        except ValueError:
            return {"error": f"start {start!r} is not a YYYY-MM-DD date"}
    natal = _natal_pillars(natal_record)
    branch_elements = tables()["clock"]["branch_elements"]
    from ati.lunar import lunar_date

    out_days, ingresses, prev, hstatus = [], [], None, None
    for i in range(days):
        d = start + timedelta(days=i)
        noon = datetime(d.year, d.month, d.day, 12, 0, tzinfo=zone)
        cur = pillars(noon.replace(tzinfo=None).isoformat(timespec="minutes"), str(zone))
        current = {k: {kk: cur[k][kk] for kk in ("stem", "branch", "hanzi", "stem_element")} for k in PILLARS}
        inter = [x for x in interactions(current, natal, branch_elements) if x["current"] in DAY_PILLARS] \
            if natal else []
        lit, _ghosts, hstatus = houses(noon.astimezone(timezone.utc), natal_record)
        hmap = _house_map(lit)
        if prev is not None and hmap:
            for body, h in sorted(hmap.items()):
                if prev.get(body) not in (None, h):
                    ingresses.append({"date": d.isoformat(), "body": body,
                                      "from_house": prev[body], "to_house": h})
        prev = hmap or prev
        lun = lunar_date(noon, local_date=True)
        out_days.append({
            "date": d.isoformat(),
            "pillars": {k: {"hanzi": current[k]["hanzi"], "stem": current[k]["stem"],
                            "branch": current[k]["branch"], "element": current[k]["stem_element"]}
                        for k in DAY_PILLARS},
            "day_master": [{"type": x["type"], "from": x["current"], "tag": x["tag"]}
                           for x in inter if x["natal"] == "day"],
            "other_interactions": [{"type": x["type"], "from": x["current"], "to_natal": x["natal"],
                                    "tag": x["tag"]} for x in inter if x["natal"] != "day"],
            "houses": dict(sorted(hmap.items())),
            "lunar": {"month": lun["month"], "day": lun["day"], "leap": lun["leap"],
                      "new_moon": lun["day"] == 1},
        })

    def dates(kind):
        return [x["date"] for x in out_days if any(t["type"] == kind for t in x["day_master"])]
    slow = [g for g in ingresses if g["body"] != "moon"]
    return {
        "start": start.isoformat(), "days": days, "tz": str(zone),
        "natal_status": "present" if natal else "absent - no natal pillars on record, so no interactions",
        "houses_status": hstatus,
        "summary": {
            "day_master_supported": dates("generation"),
            "day_master_controlled": dates("control"),
            "day_branch_clashes": dates("clash"),
            "new_moons": [x["date"] for x in out_days if x["lunar"]["new_moon"]],
            "ingresses_excluding_moon": slow,
            "moon_ingress_count": len(ingresses) - len(slow),
        },
        "calendar": out_days,
        "ingresses": ingresses,
        "not_computed": ["aspects between current and natal bodies (no aspect engine)",
                         "any rating of a day as good or bad - the rules list, nobody weighs yet"],
        "basis": ("ati/calendar.py at local noon per day; pillars ati/bazi.py; interactions "
                  "ati/lighting.py rules; houses ati/ephemeris.py whole-sign from the natal "
                  "ascendant; lunar ati/lunar.py. Computed per request, nothing stored."),
    }
