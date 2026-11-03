#!/usr/bin/env python3
"""ATI scoring - one event in, one recommendation or one refusal out.

    from ati import score
    score(event, natal)                       # records as dicts
    score_event(store, event_id="...")        # or read them from shared memory
    score_event(store, entity_id="...", timestamp_utc="...", event_type="...")

Steps, in the order of the build spec:
  1. timestamp -> local time -> the active window of the 12-window organ clock
  2. event_type -> primary house          (config/ati/event_type_to_house.json)
  3. house -> organ                       (config/ati/house_to_organ.json)
  4. event organ vs window organ: same -> direct, one window either side ->
     adjacent, otherwise background
  5. natal pressure: which natal pillars share the window's branch or element
  6. sky overlay, only if an ephemeris feed is passed in
  7. anything required missing -> refusal, missing_fields named, no recommendation

On demand only. Nothing here runs in the background, writes anything, or
speaks to the user.
"""
import json
import os
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9
    ZoneInfo = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "ati")
PILLARS = ("year", "month", "day", "hour")


def _load(name):
    with open(os.path.join(CFG, name), encoding="utf-8") as fh:
        return json.load(fh)


def tables():
    return {"clock": _load("organ_clock_windows.json"),
            "house_to_organ": (_load("house_to_organ.json").get("houses") or {}),
            "event_type_to_house": {str(k).lower(): v for k, v in
                                    (_load("event_type_to_house.json").get("event_types") or {}).items()}}


def refusal(missing_fields, event_id=None):
    """The spec's refusal shape. A refusal names what is missing and carries
    no recommendation - a partial score would read as a whole one."""
    return {"refusal": True, "event_id": event_id, "missing_fields": list(missing_fields),
            "recommendation": None}


def _parse_utc(ts):
    if isinstance(ts, datetime):
        dt = ts
    else:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _local_tz(event, natal):
    """The event's own place decides local time. The natal record's optional
    default_tz is the fallback (where the entity usually is) - never the birth
    place, which is where they were once, not where the event happened."""
    loc = event.get("location")
    tz = (loc.get("tz") if isinstance(loc, dict) else None) or (natal or {}).get("default_tz")
    if not tz or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(tz)
    except Exception:
        return None


def window_for(local_dt, clock):
    """The organ-clock window holding this local time. 23:00-01:00 wraps midnight."""
    minutes = local_dt.hour * 60 + local_dt.minute
    for w in clock["windows"]:
        sh, sm = map(int, w["start"].split(":"))
        eh, em = map(int, w["end"].split(":"))
        s, e = sh * 60 + sm, eh * 60 + em
        if (s <= minutes < e) if s < e else (minutes >= s or minutes < e):
            return w
    return None


def match_level(event_organ, window, clock):
    ws = clock["windows"]
    idx = next((w["index"] for w in ws if w["organ"] == event_organ), None)
    if idx is None:
        return None
    gap = abs(idx - window["index"]) % len(ws)
    gap = min(gap, len(ws) - gap)
    return "direct" if gap == 0 else "adjacent" if gap == 1 else "background"


def natal_pressure(window, natal, clock):
    """Which natal pillars share the active window's branch (strong) or its
    element (weak). A note, not a score: the spec asks for pressure applied as
    a note alongside the match level, and it does not change the match."""
    pillars = (natal or {}).get("bazi_pillars") or {}
    be = clock.get("branch_elements") or {}
    same_branch, same_element = [], []
    for p in PILLARS:
        branch = str(((pillars.get(p) or {}).get("branch")) or "").lower()
        if not branch:
            continue
        if branch == window["branch"]:
            same_branch.append(p)
        elif be.get(branch) == window["element"]:
            same_element.append(p)
    if not pillars:
        return None
    if not same_branch and not same_element:
        return f"No natal pillar shares the {window['branch']} branch or {window['element']} element of this window."
    bits = []
    if same_branch:
        bits.append(f"{', '.join(same_branch)} pillar{'s' if len(same_branch) > 1 else ''} "
                    f"share{'' if len(same_branch) > 1 else 's'} this window's {window['branch']} branch")
    if same_element:
        bits.append(f"{', '.join(same_element)} pillar{'s' if len(same_element) > 1 else ''} "
                    f"share{'' if len(same_element) > 1 else 's'} its {window['element']} element")
    return "; ".join(bits) + "."


def score(event, natal, sky=None, cfg=None):
    """event: an EventSnapshot dict. natal: a FixedNatalRecord dict or None.
    sky: optional callable(utc_datetime, event_house, natal) -> note or None."""
    cfg = cfg or tables()
    event = event or {}
    eid = event.get("event_id")
    missing = []
    if not event.get("timestamp_utc"):
        missing.append("timestamp")
    if not event.get("event_type"):
        missing.append("event_type")
    if not natal:
        missing.append("fixed_natal")
    if missing:
        return refusal(missing, eid)

    try:
        utc = _parse_utc(event["timestamp_utc"])
    except Exception:
        return refusal(["timestamp (unparseable)"], eid)
    tz = _local_tz(event, natal)
    if tz is None:
        return refusal(["local_timezone (event location.tz, or the natal record's default_tz)"], eid)
    local = utc.astimezone(tz)
    window = window_for(local, cfg["clock"])

    house = cfg["event_type_to_house"].get(str(event["event_type"]).lower())
    if house is None:
        missing.append(f"event_type_to_house:{event['event_type']}")
    organ = cfg["house_to_organ"].get(str(house)) if house is not None else None
    if house is not None and organ is None:
        missing.append(f"house_to_organ:{house}")
    if missing:
        return refusal(missing, eid)
    level = match_level(organ, window, cfg["clock"])
    if level is None:
        return refusal([f"house_to_organ:{house} names {organ!r}, which is not on the organ clock"], eid)

    pressure = natal_pressure(window, natal, cfg["clock"])
    sky_note, sky_err = None, None
    if sky is not None:
        try:
            sky_note = sky(utc, house, natal)
        except Exception as exc:
            sky_err = str(exc)[:200]
    provenance = {
        "organ_clock": "config/ati/organ_clock_windows.json (generic reference, local clock time)",
        "event_type_to_house": "config/ati/event_type_to_house.json",
        "house_to_organ": "config/ati/house_to_organ.json",
        "natal_pillars": "present" if (natal.get("bazi_pillars")) else "absent",
        "sky": ("present" if sky_note else
                f"feed failed: {sky_err}" if sky is not None and sky_err else
                "feed returned nothing" if sky is not None else "absent - no ephemeris feed"),
        "local_time": f"{local.isoformat(timespec='minutes')} ({tz.key})",
    }
    layers = ["organ_clock", "house_map"] + (["natal_pillars"] if natal.get("bazi_pillars") else []) \
        + (["sky"] if sky_note else [])
    return {
        "refusal": False,
        "event_id": eid,
        "active_window": {"organ": window["organ"], "element": window["element"],
                          "branch": window["branch"], "time_range": f"{window['start']}-{window['end']}"},
        "event_house": house,
        "event_organ": organ,
        "match_level": level,
        "natal_pressure_note": pressure,
        "sky_note": sky_note,
        # Which layers actually contributed, as a count - not a probability. A
        # number dressed as likelihood would claim a calibration nobody did.
        "confidence": f"{len(layers)} of 4 layers present ({', '.join(layers)})",
        "missing_fields": [] if natal.get("bazi_pillars") else ["bazi_pillars (natal pressure not applied)"],
        "provenance": provenance,
    }


def score_event(store, event_id=None, entity_id=None, timestamp_utc=None, event_type=None,
                location=None, sky=None):
    """The on-demand trigger: by event_id, or by entity_id + timestamp + type."""
    if event_id:
        event = store.get_event(event_id)
        if not event:
            return refusal(["event (no snapshot with that event_id)"], event_id)
    else:
        event = {"event_id": None, "entity_id": entity_id, "timestamp_utc": timestamp_utc,
                 "event_type": event_type, "location": location}
    if not event.get("entity_id"):
        return refusal(["entity_id", "fixed_natal"], event.get("event_id"))
    return score(event, store.get_natal(event["entity_id"]), sky=sky)
