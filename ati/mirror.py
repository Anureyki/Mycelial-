#!/usr/bin/env python3
"""The tropical/sidereal mirror, and the sky read against both selves.

    from ati.mirror import mirror, sky_read
    mirror(natal_record)                         # the two selves and how they meet
    sky_read(natal_record, moment, location="SW", facing="NW")

Principal, 2026-11-09: the tropical and sidereal charts are "mirrors ... looking in a
mirror at your opposite ... the synastry of the energy between the two" - an Aquarius
Sun and a Capricorn Sun are two different selves. This computes that the same way
every time (settings in config/ati/mirror.json), so a journal entry made today and one
made in a month are read by the same rule.

THE MIRROR: every placement (ten bodies and the ascendant) in both zodiacs - sign,
element, modality, ruler; which stay in the same sign; and every aspect between a
tropical placement and a sidereal one, exactly as a synastry between two charts. The
growth point is the principal's rule: the tightest square or opposition across it.

THE SKY: the moment's planets against BOTH selves - sky to the tropical self (ordinary
transits) and sky to the sidereal self (the same longitudes against the sidereal
placements, the method the mirror uses) - with the natal houses (whole-sign; the same
number in both frames by construction), the lunar day and the Moon's phase, the four
pillars and organ-clock window, and the Four Symbols for where it happened and which
way it faced.

PRIVACY, the rule lighting runs: signs, houses, aspect names and orbs - never a degree
or a natal longitude. A placement within cusp_degrees of a sign line is FLAGGED rather
than its degree shown. Computed per request; a journal entry stores this snapshot in
Hermes (ATI's namespace), never in the repo.

STANDING: positions and aspects are calculation; reading two zodiacs as two selves is
the principal's framework; meanings are tradition. All three are labelled where they
are returned.
"""
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNS = ("Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio",
         "Sagittarius", "Capricorn", "Aquarius", "Pisces")
ELEMENT = ("fire", "earth", "air", "water")
MODALITY = ("cardinal", "fixed", "mutable")
RULER = {"Aries": "Mars", "Taurus": "Venus", "Gemini": "Mercury", "Cancer": "Moon", "Leo": "Sun",
         "Virgo": "Mercury", "Libra": "Venus", "Scorpio": "Mars / Pluto", "Sagittarius": "Jupiter",
         "Capricorn": "Saturn", "Aquarius": "Saturn / Uranus", "Pisces": "Jupiter / Neptune"}


def _cfg():
    with open(os.path.join(ROOT, "config", "ati", "mirror.json"), encoding="utf-8") as fh:
        return json.load(fh)


def ayanamsa(dt_utc, cfg=None):
    a = (cfg or _cfg())["ayanamsa"]
    years = (dt_utc - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / (365.25 * 86400)
    return a["j2000_degrees"] + years * a["arcsec_per_year"] / 3600


def _sign(lon):
    i = int((lon % 360) // 30)
    return {"sign": SIGNS[i], "element": ELEMENT[i % 4], "modality": MODALITY[i % 3], "ruler": RULER[SIGNS[i]]}


def _near_cusp(lon, cfg):
    d = (lon % 360) % 30
    return bool(min(d, 30 - d) < cfg["cusp_degrees"])


def _aspects(a, b, orbs, cfg, a_label, b_label):
    out = []
    for an, al in a.items():
        for bn, bl in b.items():
            d = abs((al - bl + 180) % 360 - 180)
            for name, ang in cfg["aspects"].items():
                if abs(d - ang) <= orbs[name]:
                    out.append({a_label: an, "aspect": name, b_label: bn, "orb": round(abs(d - ang), 1)})
    return sorted(out, key=lambda x: x["orb"])


def _natal_longitudes(rec):
    from ati.ephemeris import longitudes, angles
    loc = rec.get("birth_location") if isinstance(rec.get("birth_location"), dict) else {}
    if not (rec.get("birth_datetime_utc") and loc.get("lat") is not None and loc.get("lon") is not None):
        return None, None
    birth = datetime.fromisoformat(str(rec["birth_datetime_utc"]).replace("Z", "+00:00"))
    # Plain floats at the boundary: the ephemeris hands back numpy values, whose bools
    # do not serialise and would break any record this is stored in.
    lons = {k: float(v) for k, v in longitudes(birth).items()}
    lons["ascendant"] = float(angles(birth, loc["lat"], loc["lon"])["ascendant"])
    return birth, lons


def mirror(natal_record, cfg=None):
    cfg = cfg or _cfg()
    from ati.ephemeris import available
    ok, why = available()
    if not ok:
        return {"status": "unavailable", "why": f"ephemeris unavailable ({why})"}
    birth, trop = _natal_longitudes(natal_record or {})
    if trop is None:
        return {"status": "absent", "why": "no natal birth time and place on record"}
    ay = ayanamsa(birth, cfg)
    sid = {k: (v - ay) % 360 for k, v in trop.items()}
    placements = []
    for k in trop:
        t, s = _sign(trop[k]), _sign(sid[k])
        placements.append({"body": k, "tropical": t, "sidereal": s, "same_sign": t["sign"] == s["sign"],
                           "near_cusp": {"tropical": _near_cusp(trop[k], cfg), "sidereal": _near_cusp(sid[k], cfg)}})
    inter = _aspects(trop, sid, cfg["orbs"]["mirror"], cfg, "tropical", "sidereal")
    hard = [x for x in inter if x["aspect"] in ("square", "opposite")]
    return {"status": "computed", "placements": placements,
            "anchors": [p["body"] for p in placements if p["same_sign"]],
            "interaspects": inter,
            "growth_point": dict(hard[0], rule=cfg["growth_point_rule"], basis=cfg["growth_point_basis"]) if hard else None,
            "ayanamsa": cfg["ayanamsa"]["name"],
            "standing": {"computed": "signs, same-sign anchors, interaspects and orbs",
                         "framework": "two zodiacs read as two selves in synastry - the principal's",
                         "tradition": "what each sign, ruler and aspect means"}}


def _phase(sun, moon):
    e = (moon - sun) % 360
    names = ((22.5, "new"), (67.5, "waxing crescent"), (112.5, "first quarter"), (157.5, "waxing gibbous"),
             (202.5, "full"), (247.5, "waning gibbous"), (292.5, "last quarter"), (337.5, "waning crescent"), (360.1, "new"))
    return next(n for lim, n in names if e < lim), round(e)


CHALDEAN = ("Saturn", "Jupiter", "Mars", "Sun", "Venus", "Mercury", "Moon")
DAY_RULER = ("Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Sun")   # Monday .. Sunday


def planetary_hours(day, lat, lon, tz):
    """The twelve day and twelve night hours of a civil date at a place (Chaldean order;
    the first day hour belongs to the weekday's ruler). Unequal hours: sunrise to sunset
    split in twelve, sunset to the next sunrise split in twelve. -> None if the sun does
    not both rise and set that day there."""
    from datetime import timedelta
    from skyfield import almanac
    from skyfield.api import wgs84
    from ati.ephemeris import _load
    from zoneinfo import ZoneInfo
    zone = ZoneInfo(str(tz))
    ts, eph = _load()
    f = almanac.sunrise_sunset(eph, wgs84.latlon(lat, lon))
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    times, ev = almanac.find_discrete(ts.from_datetime(start), ts.from_datetime(start + timedelta(days=2)), f)
    ev_list = [(t.utc_datetime().astimezone(zone), bool(e)) for t, e in zip(times, ev)]
    rise = next((t for t, up in ev_list if up and t.date() == day), None)
    if rise is None:
        return None
    sset = next((t for t, up in ev_list if not up and t > rise), None)
    nrise = next((t for t, up in ev_list if up and sset and t > sset), None)
    if not (sset and nrise):
        return None
    first = CHALDEAN.index(DAY_RULER[day.weekday()])
    hours, d_len, n_len = [], (sset - rise) / 12, (nrise - sset) / 12
    for i in range(24):
        a = rise + d_len * i if i < 12 else sset + n_len * (i - 12)
        b = a + (d_len if i < 12 else n_len)
        hours.append({"ruler": CHALDEAN[(first + i) % 7], "part": "day" if i < 12 else "night",
                      "start": a.isoformat(timespec="minutes"), "end": b.isoformat(timespec="minutes")})
    return {"day_ruler": DAY_RULER[day.weekday()], "sunrise": rise.isoformat(timespec="minutes"),
            "sunset": sset.isoformat(timespec="minutes"), "hours": hours}


def _hour_at(ph, moment):
    for h in (ph or {}).get("hours") or []:
        if datetime.fromisoformat(h["start"]) <= moment < datetime.fromisoformat(h["end"]):
            return h
    return None


def event_chart(moment, lat, lon, natal_longitudes=None, until=None, cfg=None):
    """The moment's own chart where it happened: rising sign, midheaven, the sky's
    whole-sign houses from that rising sign, and the angles' aspects to the natal chart.
    With `until`, the minutes the rising sign changed during the practice."""
    from datetime import timedelta
    from ati.ephemeris import angles, longitudes, whole_sign_house
    cfg = cfg or _cfg()
    utc = moment.astimezone(timezone.utc)
    an = angles(utc, lat, lon)
    asc, mc = float(an["ascendant"]), float(an["midheaven"])
    sky = {k: float(v) for k, v in longitudes(utc).items()}
    out = {"rising": _sign(asc)["sign"], "midheaven": _sign(mc)["sign"],
           "houses": {k: whole_sign_house(v, asc) for k, v in sky.items()}}
    if natal_longitudes:
        out["angles_to_natal"] = _aspects({"rising": asc, "midheaven": mc}, natal_longitudes,
                                          cfg["orbs"]["sky"], cfg, "angle", "natal")
    if until and until > moment:
        changes, t, prev = [], moment, out["rising"]
        while t <= until:
            t += timedelta(minutes=1)
            sg = _sign(float(angles(t.astimezone(timezone.utc), lat, lon)["ascendant"]))["sign"]
            if sg != prev:
                changes.append({"at": t.isoformat(timespec="minutes"), "rising": sg})
                prev = sg
        out["rising_changes"] = changes
    return out


def sky_read(natal_record, moment, location=None, facing=None, cfg=None, place=None, until=None):
    """moment: timezone-aware local datetime. location / facing: a direction (N, NE ... NW).
    place: (lat, lon) of where it happened - adds the event chart and planetary hours.
    until: when the practice ended - adds the rising-sign changes and the hours it spanned."""
    cfg = cfg or _cfg()
    from ati.ephemeris import available, longitudes, whole_sign_house
    from ati.lighting import lighting
    from ati.lunar import lunar_date
    ok, why = available()
    if not ok:
        return {"status": "unavailable", "why": f"ephemeris unavailable ({why})"}
    utc = moment.astimezone(timezone.utc)
    sky = {k: float(v) for k, v in longitudes(utc).items()}
    birth, trop = _natal_longitudes(natal_record or {})
    L = lighting(moment, natal_record)
    lun = lunar_date(moment, local_date=True)
    phase, elong = _phase(sky["sun"], sky["moon"])
    out = {"status": "computed", "moment": moment.isoformat(timespec="minutes"),
           "pillars": {k: v["hanzi"] for k, v in L["current_pillars"].items()},
           "organ_window": L["active_window"],
           "pillar_interactions": [{"from": x["current"], "to_natal": x["natal"], "type": x["type"], "tag": x["tag"]}
                                   for x in L["interactions"]],
           "lunar": {"month": lun["month"], "day": lun["day"], "leap": lun["leap"]},
           "moon_phase": phase, "sun_moon_elongation_deg": elong,
           "sky": {k: {"tropical": _sign(v)["sign"], "sidereal": _sign(v - ayanamsa(utc, cfg))["sign"]}
                   for k, v in sky.items()}}
    for key, d in (("location", location), ("facing", facing)):
        if d:
            info = cfg["directions"].get(str(d).upper())
            out[key] = dict(info, direction=str(d).upper()) if info else {"direction": d, "unknown": True}
    if trop is None:
        out["natal_status"] = "absent - no natal birth time and place, so no houses or transits"
        return out
    ay_n = ayanamsa(birth, cfg)
    sid_self = {k: (v - ay_n) % 360 for k, v in trop.items()}
    natal_bodies = {k: v for k, v in trop.items()}
    out["houses"] = {k: whole_sign_house(v, trop["ascendant"]) for k, v in sky.items()}
    out["houses_note"] = "whole-sign; the house number is the same in both zodiacs by construction"
    out["sky_to_tropical_self"] = _aspects(sky, natal_bodies, cfg["orbs"]["sky"], cfg, "sky", "natal")
    out["sky_to_sidereal_self"] = _aspects(sky, sid_self, cfg["orbs"]["sky"], cfg, "sky", "natal")
    m = mirror(natal_record, cfg)
    out["mirror_growth_point"] = m.get("growth_point")
    if place:
        lat, lon = place
        out["event_chart"] = event_chart(moment, lat, lon, natal_bodies, until=until, cfg=cfg)
        ph = planetary_hours(moment.date(), lat, lon, moment.tzinfo)
        if ph:
            out["planetary_hour"] = _hour_at(ph, moment)
            out["day_ruler"], out["sunrise"], out["sunset"] = ph["day_ruler"], ph["sunrise"], ph["sunset"]
            if until:
                out["planetary_hours_spanned"] = [h for h in ph["hours"]
                                                  if datetime.fromisoformat(h["end"]) > moment
                                                  and datetime.fromisoformat(h["start"]) < until]
    out["not_computed"] = ["the lunar nodes (North/South Node)"]
    out["standing"] = {"computed": "pillars, organ window, lunar day, phase, signs, houses, aspects, "
                                   "event chart and planetary hours when a place is given",
                       "framework": "sky against two selves - the principal's mirror",
                       "tradition": "meanings of hours, directions, Four Symbols, signs and aspects"}
    return out
