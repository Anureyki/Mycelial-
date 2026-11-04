#!/usr/bin/env python3
"""BaZi (Four Pillars) from birth data - derived, never hand-entered.

    from ati.bazi import pillars
    pillars("2024-02-10T12:00", "Asia/Shanghai", lon=121.47)   # -> 甲辰 丙寅 甲辰 庚午

The pillars turn on SOLAR TERMS, not the civil calendar. The year begins at Li
Chun - the Sun at apparent longitude 315 degrees, around 4 February - so a birth
in January or early February belongs to the PREVIOUS year's pillar. Months
begin at the twelve "jie" terms, every 30 degrees from 315 (Yin month first).
Both are read from the Sun's position at the birth instant, so the boundary is
astronomical rather than a fixed date.

The Sun's longitude uses Meeus, Astronomical Algorithms ch. 25 (low accuracy:
about 0.01 degree, ~15 minutes of time) - stdlib only, no ephemeris. A birth
within a day of a term boundary is flagged, because there the result should be
confirmed against a full ephemeris.

Day pillar: sexagenary index = (JDN + 49) mod 60 for the civil date of the birth
in local time (check: 2000-01-01 is wu-wu, index 54). Hour pillar: the two-hour
branch of the local clock time, stem by the Five Rats rule from the day stem.
School choices are recorded, not hidden: civil (zone) time is used; true solar
time is computed and reported beside it, and a disagreement is flagged.
"""
import math
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

STEMS = ("jia", "yi", "bing", "ding", "wu", "ji", "geng", "xin", "ren", "gui")
STEMS_HANZI = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = ("zi", "chou", "yin", "mao", "chen", "si", "wu", "wei", "shen", "you", "xu", "hai")
BRANCHES_HANZI = "子丑寅卯辰巳午未申酉戌亥"
STEM_ELEMENT = ("wood", "wood", "fire", "fire", "earth", "earth", "metal", "metal", "water", "water")
BOUNDARY_MARGIN_DAYS = 1.0


def _jd(dt_utc):
    """Julian Day of a UTC datetime (Meeus 7.1)."""
    y, m = dt_utc.year, dt_utc.month
    d = dt_utc.day + (dt_utc.hour + (dt_utc.minute + dt_utc.second / 60) / 60) / 24
    if m <= 2:
        y, m = y - 1, m + 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def sun_longitude(dt_utc):
    """Apparent geocentric longitude of the Sun, degrees (Meeus ch. 25)."""
    t = (_jd(dt_utc) - 2451545.0) / 36525.0
    l0 = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
    m = math.radians(357.52911 + 35999.05029 * t - 0.0001537 * t * t)
    c = ((1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(m)
         + (0.019993 - 0.000101 * t) * math.sin(2 * m) + 0.000289 * math.sin(3 * m))
    true_lon = l0 + c
    omega = math.radians(125.04 - 1934.136 * t)
    return (true_lon - 0.00569 - 0.00478 * math.sin(omega)) % 360.0


def _equation_of_time_minutes(dt_utc):
    """Approximate equation of time (true solar minus mean), minutes."""
    n = dt_utc.timetuple().tm_yday
    b = math.radians(360.0 / 365.0 * (n - 81))
    return 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)


def _pillar(stem, branch):
    return {"stem": STEMS[stem], "branch": BRANCHES[branch],
            "hanzi": STEMS_HANZI[stem] + BRANCHES_HANZI[branch], "stem_element": STEM_ELEMENT[stem]}


def _civil_jdn(y, m, d):
    a = (14 - m) // 12
    yy, mm = y + 4800 - a, m + 12 * a - 3
    return d + (153 * mm + 2) // 5 + 365 * yy + yy // 4 - yy // 100 + yy // 400 - 32045


def pillars(local_iso, tz, lon=None):
    """-> {"year","month","day","hour": pillar, "basis": {...}, "flags": [...]}.
    local_iso: birth clock time where it happened; tz: IANA zone of that clock."""
    local = datetime.fromisoformat(str(local_iso))
    if local.tzinfo is None:
        local = local.replace(tzinfo=ZoneInfo(tz))
    utc = local.astimezone(timezone.utc)
    lam = sun_longitude(utc)
    flags = []

    # month index from Li Chun: 0 = Yin month (315..345), ..., 10 = Zi, 11 = Chou
    k = int(((lam - 315.0) % 360.0) // 30.0)
    # distance to the nearest jie boundary, in days (Sun ~0.9856 deg/day)
    edge = ((lam - 315.0) % 30.0)
    days_to_edge = min(edge, 30.0 - edge) / 0.9856
    if days_to_edge < BOUNDARY_MARGIN_DAYS:
        flags.append(f"within {days_to_edge:.2f} days of a solar-term boundary - confirm with a full ephemeris")

    # year: before Li Chun (Zi or Chou month in Jan/Feb) belongs to the previous year
    year = local.year - 1 if (local.month <= 2 and k in (10, 11)) else local.year
    ys, yb = (year - 4) % 10, (year - 4) % 12
    # month: branch Yin(2)+k; stem by the Five Tigers rule from the year stem
    ms = (2 * (ys % 5) + 2 + k) % 10
    mb = (2 + k) % 12
    # day: civil date in local time
    idx = (_civil_jdn(local.year, local.month, local.day) + 49) % 60
    ds, db = idx % 10, idx % 12
    # hour: two-hour branch of the clock; stem by the Five Rats rule
    hb = ((local.hour + 1) // 2) % 12
    hs = (2 * (ds % 5) + hb) % 10
    if local.hour == 23:
        flags.append("23:00-24:00 birth: schools differ on whether the day pillar turns at 23:00 or midnight")

    basis = {"clock_time": local.isoformat(timespec="minutes"), "utc": utc.isoformat(timespec="minutes"),
             "sun_longitude_deg": round(lam, 4), "solar_year": year,
             "month_from_li_chun": k, "time_used": "civil (zone) time"}
    if lon is not None:
        off = local.utcoffset().total_seconds() / 3600.0
        lmt = local + timedelta(minutes=4 * (lon - 15 * off))
        tst = lmt + timedelta(minutes=_equation_of_time_minutes(utc))
        tst_branch = ((tst.hour + 1) // 2) % 12
        basis["true_solar_time"] = tst.strftime("%H:%M")
        basis["true_solar_hour_branch"] = BRANCHES[tst_branch]
        if tst_branch != hb or tst.date() != local.date():
            flags.append(f"true solar time ({tst:%H:%M}) gives a different hour or day than clock time - "
                         "schools differ; civil time is used")
    return {"year": _pillar(ys, yb), "month": _pillar(ms, mb), "day": _pillar(ds, db),
            "hour": _pillar(hs, hb), "basis": basis, "flags": flags,
            "method": "ati/bazi.py - Meeus solar longitude, jie terms from 315 deg, JDN day cycle, Five Tigers/Rats"}
