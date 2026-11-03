#!/usr/bin/env python3
"""ATI scoring - one event in, one recommendation or one refusal out.

    from ati import score                     # the Anansi <-> ATI contract (phase 2)
    score(entity_id, timestamp_utc, event_type=None, location=None, store=HermesStore(agent))
    score_event(store, event_id)              # a stored EventSnapshot (phase 1)
    score_snapshot(event, natal)              # records already in hand

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
    """The contract's refusal shape. A refusal names what is missing and carries
    no recommendation - a partial score would read as a whole one."""
    out = {"refusal": True, "missing_fields": list(missing_fields), "recommendation": None}
    if event_id:
        out["event_id"] = event_id
    return out


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
        return (f"No natal pillar shares this window's {window.get('branch_display') or window['branch']} "
                f"branch or {window['element']} element.")
    bits = []
    if same_branch:
        bits.append(f"{', '.join(same_branch)} pillar{'s' if len(same_branch) > 1 else ''} "
                    f"share{'' if len(same_branch) > 1 else 's'} this window's "
                    f"{window.get('branch_display') or window['branch']} branch")
    if same_element:
        bits.append(f"{', '.join(same_element)} pillar{'s' if len(same_element) > 1 else ''} "
                    f"share{'' if len(same_element) > 1 else 's'} its {window['element']} element")
    return "; ".join(bits) + "."


def _ordinal(n):
    n = int(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _display(clock, organ):
    return next((w.get("organ_display") or w["organ"] for w in clock["windows"] if w["organ"] == organ), organ)


def score_snapshot(event, natal, sky=None, cfg=None, type_optional=False):
    """Score one EventSnapshot dict against one FixedNatalRecord dict.

    Returns the Anansi contract's shapes (phase 2): success with active_window
    {range, branch, organ, element}, event_house / event_organ / match_level,
    natal_pressure_note, sky_note, confidence and provenance as a list - or the
    refusal {refusal, missing_fields, recommendation: null}.

    type_optional: the Anansi call may pass event_type null to ask only for the
    window and pressure; then the event fields come back null, not refused. A
    stored snapshot keeps phase 1's rule - no event type, no score."""
    cfg = cfg or tables()
    clock = cfg["clock"]
    event = event or {}
    eid = event.get("event_id")
    missing = []
    if not event.get("timestamp_utc"):
        missing.append("timestamp")
    if not event.get("event_type") and not type_optional:
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
        return refusal(["local_timezone (location.tz, or the natal record's default_tz)"], eid)
    local = utc.astimezone(tz)
    window = window_for(local, clock)

    house = organ = level = None
    etype = event.get("event_type")
    if etype:
        house = cfg["event_type_to_house"].get(str(etype).lower())
        if house is None:
            missing.append(f"event_type_to_house:{etype}")
        organ = cfg["house_to_organ"].get(str(house)) if house is not None else None
        if house is not None and organ is None:
            missing.append(f"house_to_organ:{house}")
        if missing:
            return refusal(missing, eid)
        level = match_level(organ, window, clock)
        if level is None:
            return refusal([f"house_to_organ:{house} names {organ!r}, which is not on the organ clock"], eid)

    pressure = natal_pressure(window, natal, clock) or \
        "No natal pillars on record; no pressure applied."
    sky_note = None
    if sky is not None:
        try:
            sky_note = sky(utc, house, natal)
        except Exception:
            sky_note = None
    # Which layers actually contributed, as a count - not a probability. A
    # number dressed as likelihood would claim a calibration nobody did.
    provenance = (["natal"] if natal.get("bazi_pillars") else []) + ["clock"] \
        + (["house_map"] if house is not None else []) + (["sky"] if sky_note else [])
    out = {
        "refusal": False,
        "active_window": {"range": f"{window['start']}\u2013{window['end']}",
                          "branch": window.get("branch_display") or window["branch"],
                          "organ": window.get("organ_display") or window["organ"],
                          "element": window["element"].title()},
        "event_house": _ordinal(house) if house is not None else None,
        "event_organ": _display(clock, organ) if organ else None,
        "match_level": level,
        "natal_pressure_note": pressure,
        "sky_note": sky_note,
        "confidence": f"{len(provenance)} of 4 layers present",
        "provenance": provenance,
    }
    if eid:
        out["event_id"] = eid
    return out


def score(entity_id, timestamp_utc, event_type=None, location=None, store=None, sky=None, cfg=None):
    """The Anansi <-> ATI contract's single entry point. One call, one response.

    store: where the fixed natal record is read from (ati.store.HermesStore(agent)
    for the live one). ATI receives only these fields - it never reads chat or
    calendar state - and writes nothing."""
    if not entity_id:
        return refusal(["entity_id", "fixed_natal"])
    natal = store.get_natal(entity_id) if store is not None else None
    return score_snapshot({"entity_id": entity_id, "timestamp_utc": timestamp_utc,
                           "event_type": event_type, "location": location},
                          natal, sky=sky, cfg=cfg, type_optional=True)


def score_event(store, event_id, sky=None):
    """A stored EventSnapshot by id (phase 1's trigger). Its event type is required."""
    event = store.get_event(event_id)
    if not event:
        return refusal(["event (no snapshot with that event_id)"], event_id)
    if not event.get("entity_id"):
        return refusal(["entity_id", "fixed_natal"], event_id)
    return score_snapshot(event, store.get_natal(event["entity_id"]), sky=sky)
