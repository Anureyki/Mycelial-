#!/usr/bin/env python3
"""The Chinese lunisolar calendar, derived astronomically - never from a lookup table.

    from ati.lunar import lunar_date
    lunar_date(datetime(2024, 2, 10, tzinfo=timezone.utc))   # -> year 2024, month 1, day 1

Rules (the modern calendar, reckoned in China Standard Time, UTC+8):
  - a month begins on the date of a new moon;
  - the month containing the winter solstice (Sun at 270 deg) is month 11;
  - a year from one month 11 to the next holding 13 months has a leap month: the
    first month in it that contains no principal term (zhongqi: Sun at a multiple
    of 30 deg) repeats the previous month's number;
  - the lunar year's number turns at month 1 (Lunar New Year), not at Li Chun.

New moons: Meeus, Astronomical Algorithms ch. 49 (mean lunation + 25 periodic and
14 planetary terms), accurate to a minute or two. The Sun: ati/bazi.py's Meeus
ch. 25 longitude. A new moon or principal term within an hour of midnight China
time is flagged, because there the date could fall either side.
"""
import math
from datetime import datetime, timedelta, timezone

from ati.bazi import sun_longitude

CST = timezone(timedelta(hours=8))
DELTA_T_DAYS = 69.0 / 86400.0      # TT - UT, ~69 s for this era; seconds, so it never moves a date by itself
EDGE_MINUTES = 60


def _jde_to_utc(jde):
    jd = jde - DELTA_T_DAYS
    return datetime(2000, 1, 1, 12, tzinfo=timezone.utc) + timedelta(days=jd - 2451545.0)


def new_moon(k):
    """UTC instant of new moon number k (k=0 is 2000-01-06). Meeus 49.1 + corrections."""
    t = k / 1236.85
    jde = (2451550.09766 + 29.530588861 * k + 0.00015437 * t ** 2
           - 0.000000150 * t ** 3 + 0.00000000073 * t ** 4)
    e = 1 - 0.002516 * t - 0.0000074 * t ** 2
    r = math.radians
    m = r(2.5534 + 29.10535670 * k - 0.0000014 * t ** 2 - 0.00000011 * t ** 3)
    mp = r(201.5643 + 385.81693528 * k + 0.0107582 * t ** 2 + 0.00001238 * t ** 3
           - 0.000000058 * t ** 4)
    f = r(160.7108 + 390.67050284 * k - 0.0016118 * t ** 2 - 0.00000227 * t ** 3
          + 0.000000011 * t ** 4)
    om = r(124.7746 - 1.56375588 * k + 0.0020672 * t ** 2 + 0.00000215 * t ** 3)
    s = math.sin
    corr = (-0.40720 * s(mp) + 0.17241 * e * s(m) + 0.01608 * s(2 * mp) + 0.01039 * s(2 * f)
            + 0.00739 * e * s(mp - m) - 0.00514 * e * s(mp + m) + 0.00208 * e * e * s(2 * m)
            - 0.00111 * s(mp - 2 * f) - 0.00057 * s(mp + 2 * f) + 0.00056 * e * s(2 * mp + m)
            - 0.00042 * s(3 * mp) + 0.00042 * e * s(m + 2 * f) + 0.00038 * e * s(m - 2 * f)
            - 0.00024 * e * s(2 * mp - m) - 0.00017 * s(om) - 0.00007 * s(mp + 2 * m)
            + 0.00004 * s(2 * mp - 2 * f) + 0.00004 * s(3 * m) + 0.00003 * s(mp + m - 2 * f)
            + 0.00003 * s(2 * mp + 2 * f) - 0.00003 * s(mp + m + 2 * f) + 0.00003 * s(mp - m + 2 * f)
            - 0.00002 * s(mp - m - 2 * f) - 0.00002 * s(3 * mp + m) + 0.00002 * s(4 * mp))
    a = [299.77 + 0.107408 * k - 0.009173 * t ** 2, 251.88 + 0.016321 * k, 251.83 + 26.651886 * k,
         349.42 + 36.412478 * k, 84.66 + 18.206239 * k, 141.74 + 53.303771 * k, 207.14 + 2.453732 * k,
         154.84 + 7.306860 * k, 34.52 + 27.261239 * k, 207.19 + 0.121824 * k, 291.34 + 1.844379 * k,
         161.72 + 24.198154 * k, 239.56 + 25.513099 * k, 331.55 + 3.592518 * k]
    w = [325, 165, 164, 126, 110, 62, 60, 56, 47, 42, 40, 37, 35, 23]
    corr += sum(wi * 1e-6 * math.sin(math.radians(ai)) for wi, ai in zip(w, a))
    return _jde_to_utc(jde + corr)


def _k_near(dt):
    years = (dt - datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)).total_seconds() / (365.2425 * 86400)
    return round(years * 12.3685)


def new_moon_on_or_before(day):
    """The China-time DATE of the last new moon on or before `day` (a date), and its instant."""
    k = _k_near(datetime(day.year, day.month, day.day, tzinfo=CST)) + 1
    for _ in range(4):             # the estimate is within a lunation; four steps is ample
        nm = new_moon(k)
        if nm.astimezone(CST).date() <= day:
            return nm.astimezone(CST).date(), nm, k
        k -= 1
    raise RuntimeError(f"no new moon found on or before {day} within four lunations")


def _term_instant(target_deg, near_utc):
    """When the Sun reaches target_deg, searched within 20 days of near_utc (bisection)."""
    lo, hi = near_utc - timedelta(days=20), near_utc + timedelta(days=20)

    def ahead(t):
        return ((sun_longitude(t) - target_deg + 180.0) % 360.0) - 180.0
    for _ in range(60):
        mid = lo + (hi - lo) / 2
        if ahead(mid) < 0:
            lo = mid
        else:
            hi = mid
    return lo + (hi - lo) / 2


def winter_solstice(year):
    return _term_instant(270.0, datetime(year, 12, 21, 12, tzinfo=timezone.utc))


def _cst_midnight_utc(day):
    return datetime(day.year, day.month, day.day, tzinfo=CST).astimezone(timezone.utc)


def _has_zhongqi(start, nxt):
    """Does the month [start, nxt) contain a principal term (Sun crossing a multiple of 30 deg)?"""
    a = sun_longitude(_cst_midnight_utc(start))
    b = sun_longitude(_cst_midnight_utc(nxt))
    return int(a // 30) != int(b // 30)


def _sui(year):
    """Months of the year from the month-11 holding the winter solstice of `year`-1
    to the one holding `year`'s, numbered, with the leap month marked."""
    m11a, _, ka = new_moon_on_or_before(winter_solstice(year - 1).astimezone(CST).date())
    m11b, _, kb = new_moon_on_or_before(winter_solstice(year).astimezone(CST).date())
    starts, k = [m11a], ka + 1
    for _ in range(14):            # a year holds 12 or 13 months
        d = new_moon(k).astimezone(CST).date()
        if d >= m11b:
            break
        starts.append(d)
        k += 1
    else:
        raise RuntimeError(f"more than 13 months between the solstices of {year - 1} and {year}")
    starts.append(m11b)
    leap_year = len(starts) - 1 == 13
    months, num, leap_used = [], 11, False
    for i in range(len(starts) - 1):
        is_leap = (leap_year and not leap_used and i > 0
                   and not _has_zhongqi(starts[i], starts[i + 1]))
        if i > 0 and not is_leap:
            num = num % 12 + 1
        if is_leap:
            leap_used = True
        months.append({"start": starts[i], "end": starts[i + 1], "month": num, "leap": is_leap})
    return months


def lunar_date(dt, local_date=False):
    """-> {"year", "month", "day", "leap", "year_branch_number", "basis", "flags"} for an
    instant (timezone-aware). The months are reckoned in China time; the DAY looked up
    is China's date by default, or the instant's own local date with local_date=True
    (a cast at 19:21 in Chicago belongs to that Chicago date)."""
    day = dt.date() if local_date else dt.astimezone(CST).date()
    months = [m for y in (day.year - 1, day.year, day.year + 1) for m in _sui(y)]
    m = next(x for x in months if x["start"] <= day < x["end"])
    # the lunar year is the year of the most recent (non-leap) month 1 - Lunar New Year
    lunar_year = max(x["start"] for x in months if x["month"] == 1 and not x["leap"]
                     and x["start"] <= day).year
    flags = []
    nm_day, nm_instant, _ = new_moon_on_or_before(day)
    mins = (nm_instant.astimezone(CST) - datetime(nm_day.year, nm_day.month, nm_day.day, tzinfo=CST))
    edge = min(mins.total_seconds() / 60, 1440 - mins.total_seconds() / 60)
    if edge < EDGE_MINUTES:
        flags.append(f"the month's new moon fell {edge:.0f} min from midnight China time - confirm with a full ephemeris")
    return {"year": lunar_year, "month": m["month"], "day": (day - m["start"]).days + 1, "leap": m["leap"],
            "year_branch_number": (lunar_year - 4) % 12 + 1, "month_start": m["start"].isoformat(),
            "basis": "China Standard Time (UTC+8) date; Meeus ch.49 new moons, Meeus ch.25 Sun",
            "flags": flags}
