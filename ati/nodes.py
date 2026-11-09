#!/usr/bin/env python3
"""The Moon's nodes - mean and true - for any instant (Meeus, Astronomical Algorithms ch. 47).

    from ati.nodes import nodes
    nodes(dt_utc)  # -> {"north_true", "north_mean", "south_true", "south_mean"} ecliptic longitudes

Added 2026-11-09 at the principal's request, after his South Node (Taurus, 6th house -
work and service) turned out to carry his Navy story, and an outside reading's
North Node sextile could not be checked because ATI had no nodes.

MEAN node: Meeus 47.7. TRUE node: the mean plus the five largest periodic terms Meeus
gives with it (amplitude to 1.5 deg). Measured against the osculating node computed from
DE421's lunar position and velocity: within 0.24 deg at worst, 0.06 on average (check_ati
proves it) - ample for signs, houses and aspects. The true node swings up to ~1.9 deg
from the mean. TT-UT (~69 s) moves the node by about a
thousandth of an arcsecond and is ignored. The SOUTH node is the north plus 180.
"""
import math
from datetime import datetime, timezone

J2000 = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)


def _t(dt_utc):
    return (dt_utc - J2000).total_seconds() / 86400 / 36525


def mean_node(dt_utc):
    T = _t(dt_utc)
    return (125.0445479 - 1934.1362891 * T + 0.0020754 * T ** 2 + T ** 3 / 467441
            - T ** 4 / 60616000) % 360


def true_node(dt_utc):
    T = _t(dt_utc)
    r = math.radians
    D = r(297.8501921 + 445267.1114034 * T - 0.0018819 * T ** 2 + T ** 3 / 545868 - T ** 4 / 113065000)
    M = r(357.5291092 + 35999.0502909 * T - 0.0001536 * T ** 2 + T ** 3 / 24490000)
    Mp = r(134.9633964 + 477198.8675055 * T + 0.0087414 * T ** 2 + T ** 3 / 69699 - T ** 4 / 14712000)
    F = r(93.2720950 + 483202.0175233 * T - 0.0036539 * T ** 2 - T ** 3 / 3526000 + T ** 4 / 863310000)
    corr = (-1.4979 * math.sin(2 * (D - F)) - 0.1500 * math.sin(M) - 0.1226 * math.sin(2 * D)
            + 0.1176 * math.sin(2 * F) - 0.0801 * math.sin(2 * (Mp - F)))
    return (mean_node(dt_utc) + corr) % 360


def nodes(dt_utc):
    n_t, n_m = true_node(dt_utc), mean_node(dt_utc)
    return {"north_true": n_t, "south_true": (n_t + 180) % 360,
            "north_mean": n_m, "south_mean": (n_m + 180) % 360}
