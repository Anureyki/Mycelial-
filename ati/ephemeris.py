#!/usr/bin/env python3
"""Planetary positions and whole-sign houses - Skyfield (MIT) over NASA/JPL DE421 (public domain).

    from ati.ephemeris import available, longitudes, angles, whole_sign_house

DE421 lives in state/ati/ephem/ (owner-only, never in the repository) and covers
1899-2053. Longitudes are tropical, ecliptic of date, apparent and geocentric.
Houses are WHOLE-SIGN, the system the principal stated: a body's house is its
sign counted from the ascendant's sign. Everything here is computed on request
and returned to ATI; nothing is stored, and degrees never leave the server -
the lighting verb sends house numbers only.

If Skyfield or the file is missing, available() says so and nothing is guessed.
"""
import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EPHEM_DIR = os.path.join(ROOT, "state", "ati", "ephem")
DE421_SHA256 = "a20a7139da04cbc462454634918e9a9ca69127044e2cc9d4f9c16e238d2deedc"
BODIES = (("sun", "sun", "☉"), ("moon", "moon", "☽"), ("mercury", "mercury", "☿"), ("venus", "venus", "♀"),
          ("mars", "mars", "♂"), ("jupiter", "jupiter barycenter", "♃"), ("saturn", "saturn barycenter", "♄"),
          ("uranus", "uranus barycenter", "♅"), ("neptune", "neptune barycenter", "♆"), ("pluto", "pluto barycenter", "♇"))
SIGNS = ("Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio", "Sagittarius",
         "Capricorn", "Aquarius", "Pisces")
_cache = {}


def available():
    """-> (True, "") or (False, why). Never raises."""
    try:
        import skyfield  # noqa: F401
    except ImportError:
        return False, "the skyfield library is not installed"
    if not os.path.exists(os.path.join(EPHEM_DIR, "de421.bsp")):
        return False, "state/ati/ephem/de421.bsp is missing"
    return True, ""


def _load():
    if "eph" not in _cache:
        from skyfield.api import Loader
        load = Loader(EPHEM_DIR, verbose=False)
        _cache["ts"], _cache["eph"] = load.timescale(builtin=True), load("de421.bsp")
    return _cache["ts"], _cache["eph"]


def longitudes(dt_utc):
    """{body: tropical ecliptic longitude of date, degrees} for the ten bodies."""
    ts, eph = _load()
    t = ts.from_datetime(dt_utc)
    earth = eph["earth"].at(t)
    out = {}
    for name, key, _ in BODIES:
        _, lon, _ = earth.observe(eph[key]).apparent().ecliptic_latlon(epoch="date")
        out[name] = lon.degrees % 360.0
    return out


def angles(dt_utc, lat, lon):
    """Ascendant and Midheaven (degrees) for a moment and place (lat N+, lon E+)."""
    ts, _ = _load()
    t = ts.from_datetime(dt_utc)
    ramc = math.radians((t.gast * 15.0 + lon) % 360.0)
    tc = (t.tt - 2451545.0) / 36525.0
    eps = math.radians(23.4392911 - 0.0130042 * tc)
    phi = math.radians(lat)
    asc = math.degrees(math.atan2(math.cos(ramc), -(math.sin(ramc) * math.cos(eps) + math.tan(phi) * math.sin(eps))))
    mc = math.degrees(math.atan2(math.sin(ramc), math.cos(ramc) * math.cos(eps)))
    return {"ascendant": asc % 360.0, "midheaven": mc % 360.0}


def sign_of(longitude):
    return SIGNS[int(longitude // 30) % 12]


def whole_sign_house(body_longitude, ascendant_longitude):
    return (int(body_longitude // 30) - int(ascendant_longitude // 30)) % 12 + 1
