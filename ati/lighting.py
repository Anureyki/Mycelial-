#!/usr/bin/env python3
"""Lighting instructions for the body interface - computed here, on demand, never on the client.

    from ati.lighting import lighting
    lighting(now_local, natal_record)

Returns what the page may light for this moment and nothing it could compute
from: the generic tables (windows, meridian points, house regions), the active
window, the current four pillars, the natal pillars for display (never stored by
the client), and the classical interactions between current and natal pillars:

  generation (生)  wood > fire > earth > metal > water > wood
  control (克)     wood > earth > water > fire > metal > wood
  clash (六冲)     子午 丑未 寅申 卯酉 辰戌 巳亥

An interaction is tagged with the element it acts on (the generated or the
controlled element; both elements for a clash) and that element's two organs,
the zang-fu pair: wood liver/gallbladder, fire heart/small intestine, earth
spleen/stomach, metal lung/large intestine, water kidney/bladder.

Natal pressure is recomputed for every moment - never a permanent overlay
(principal, 2026-11-03). Houses are NOT lit: there is no ephemeris yet, so
`houses_lit` is empty and says why rather than guessing.
"""
import json
import os

from ati.bazi import pillars, STEMS, BRANCHES, STEM_ELEMENT
from ati.score import tables, window_for

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATES = {"wood": "fire", "fire": "earth", "earth": "metal", "metal": "water", "water": "wood"}
CONTROLS = {"wood": "earth", "earth": "water", "water": "fire", "fire": "metal", "metal": "wood"}
CLASHES = {frozenset(p) for p in (("zi", "wu"), ("chou", "wei"), ("yin", "shen"), ("mao", "you"),
                                  ("chen", "xu"), ("si", "hai"))}
ORGANS = {"wood": ["liver", "gallbladder"], "fire": ["heart", "small_intestine"],
          "earth": ["spleen", "stomach"], "metal": ["lung", "large_intestine"], "water": ["kidney", "bladder"]}
PILLARS = ("year", "month", "day", "hour")


def _cfg(name):
    with open(os.path.join(ROOT, "config", "ati", name), encoding="utf-8") as fh:
        return json.load(fh)


def interactions(current, natal, branch_elements):
    """Each current pillar against each natal pillar: stem generation/control, branch clash."""
    out = []
    for cp in PILLARS:
        for np_ in PILLARS:
            c, n = current[cp], natal.get(np_) or {}
            if not n.get("stem"):
                continue
            ce, ne = STEM_ELEMENT[STEMS.index(c["stem"])], STEM_ELEMENT[STEMS.index(n["stem"])]
            if GENERATES[ce] == ne:
                out.append({"type": "generation", "current": cp, "natal": np_, "acts_on": ne,
                            "tag": f"{ce} generates {ne}", "organs": ORGANS[ne]})
            elif CONTROLS[ce] == ne:
                out.append({"type": "control", "current": cp, "natal": np_, "acts_on": ne,
                            "tag": f"{ce} controls {ne}", "organs": ORGANS[ne]})
            if frozenset((c["branch"], n.get("branch"))) in CLASHES:
                els = sorted({branch_elements[c["branch"]], branch_elements[n["branch"]]})
                out.append({"type": "clash", "current": cp, "natal": np_, "acts_on": els,
                            "tag": f"{c['branch']}-{n['branch']} clash",
                            "organs": [o for e in els for o in ORGANS[e]]})
    return out


def lighting(now_local, natal_record=None):
    """now_local: timezone-aware datetime in the viewer's zone. natal_record: from ati.store."""
    t = tables()
    clock = t["clock"]
    win = window_for(now_local, clock)
    cur = pillars(now_local.replace(tzinfo=None).isoformat(timespec="minutes"), str(now_local.tzinfo))
    current = {k: {kk: cur[k][kk] for kk in ("stem", "branch", "hanzi", "stem_element")} for k in PILLARS}
    natal = {}
    bp = (natal_record or {}).get("bazi_pillars") or {}
    if isinstance(bp, dict):
        natal = {k: {kk: bp[k].get(kk) for kk in ("stem", "branch", "hanzi", "stem_element")}
                 for k in PILLARS if isinstance(bp.get(k), dict)}
    return {
        "generated_at": now_local.isoformat(timespec="minutes"),
        "windows": [{k: w[k] for k in ("start", "end", "organ", "organ_display", "element", "branch",
                                       "branch_display")} for w in clock["windows"]],
        "points": _cfg("meridian_points.json"),
        "house_regions": _cfg("house_to_body_region.json")["houses"],
        "active_window": {"organ": win["organ"], "organ_display": win["organ_display"], "range":
                          f"{win['start']}–{win['end']}", "element": win["element"],
                          "branch": win["branch"], "branch_display": win["branch_display"]},
        "current_pillars": current,
        "natal_pillars": natal or None,
        "natal_status": "present" if natal else "absent - no natal pillars on record",
        "interactions": interactions(current, natal, clock["branch_elements"]) if natal else [],
        "houses_lit": [],
        "houses_status": "unlit - no ephemeris yet; transit house positions arrive when it is wired",
        "basis": "ati/lighting.py; pillars ati/bazi.py; tables config/ati/; computed per request, nothing stored",
    }
