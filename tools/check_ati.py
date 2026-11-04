#!/usr/bin/env python3
"""Build gate for ATI (ati/). Proves the build spec's contract:

  - the refusal rule: no timestamp / event_type / natal record -> refusal with
    missing_fields and recommendation null, never a partial score
  - an unmapped event type or house is refused, never guessed
  - direct / adjacent / background on the organ clock, including the 23:00-01:00
    window that wraps midnight and adjacency across index 11 -> 0
  - local time comes from the event's place, not the birth place
  - the natal record is written once
  - on demand only: nothing in ati/ starts a thread, a timer or a loop

Uses an in-memory store and test tables passed in - nothing reaches the live
memory service, and the shipped tables (which are the principal's to fill) are
not edited.
"""
import ast
import re
import os
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ati.score import score, score_event, score_snapshot, tables  # noqa: E402
from ati.store import MemoryStore, Refused  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


cfg = tables()
cfg = dict(cfg, event_type_to_house={"test_meal": 6}, house_to_organ={"6": "stomach", "2": "lung"})
natal = {"entity_id": "e1", "birth_datetime_utc": "2000-01-01T00:00:00Z", "birth_location": "x",
         "default_tz": "America/Chicago",
         "bazi_pillars": {"year": {"stem": "geng", "branch": "chen"}, "month": {"stem": "x", "branch": "you"},
                          "day": {"stem": "x", "branch": "si"}, "hour": {"stem": "x", "branch": "zi"}}}

S = score_snapshot
r = S({"event_id": "a"}, None, cfg=cfg)
check("refusal names timestamp, event_type and fixed_natal, recommendation null",
      r["refusal"] and r["recommendation"] is None
      and set(r["missing_fields"]) == {"timestamp", "event_type", "fixed_natal"})
r = S({"event_id": "b", "timestamp_utc": "2026-11-03T14:00:00Z", "event_type": "unknown"}, natal, cfg=cfg)
check("unmapped event type refused, not guessed",
      r["refusal"] and r["missing_fields"] == ["event_type_to_house:unknown"])
r = S({"event_id": "c", "timestamp_utc": "2026-11-03T14:00:00Z", "event_type": "test_meal"},
      natal, cfg=dict(cfg, house_to_organ={}))
check("unmapped house refused", r["refusal"] and r["missing_fields"] == ["house_to_organ:6"])
# 2026-11-03 14:00Z = 08:00 Chicago (CST) -> stomach window
r = S({"event_id": "d", "timestamp_utc": "2026-11-03T14:00:00Z", "event_type": "test_meal"}, natal, cfg=cfg)
check("direct: stomach event in the stomach window",
      not r["refusal"] and r["active_window"]["organ"] == "Stomach" and r["match_level"] == "direct")
check("contract shape: range with en dash, hanzi branch, title element, ordinal house",
      r["active_window"] == {"range": "07:00\u201309:00", "branch": "\u8fb0 Chen", "organ": "Stomach",
                             "element": "Earth"} and r["event_house"] == "6th" and r["event_organ"] == "Stomach")
check("provenance is a list of present layers and confidence counts them",
      r["provenance"] == ["natal", "clock", "house_map"] and r["confidence"] == "3 of 4 layers present")
check("natal pressure names the chen-branch year pillar", "year pillar shares" in r["natal_pressure_note"])
r = S({"event_id": "e", "timestamp_utc": "2026-11-03T16:00:00Z", "event_type": "test_meal"}, natal, cfg=cfg)
check("adjacent: one window away", r["match_level"] == "adjacent")
r = S({"event_id": "f", "timestamp_utc": "2026-11-03T05:30:00Z", "event_type": "test_meal"}, natal, cfg=cfg)
check("23:00-01:00 window wraps midnight", r["active_window"]["organ"] == "Gallbladder")
check("background: far window", r["match_level"] == "background")
check("adjacency wraps across index 11 -> 0", S(
    {"event_id": "g", "timestamp_utc": "2026-11-03T05:30:00Z", "event_type": "test_meal"},
    natal, cfg=dict(cfg, house_to_organ={"6": "triple_burner"}))["match_level"] == "adjacent")
r = S({"event_id": "h", "timestamp_utc": "2026-11-03T14:00:00Z", "event_type": "test_meal",
       "location": {"lat": 35.7, "lon": 139.7, "tz": "Asia/Tokyo"}}, natal, cfg=cfg)
check("local time from the event's place (Tokyo 23:00 -> gallbladder)", r["active_window"]["organ"] == "Gallbladder")
r = S({"event_id": "i", "timestamp_utc": "2026-11-03T14:00:00Z", "event_type": "test_meal"},
      dict(natal, default_tz=None), cfg=cfg)
check("no timezone anywhere is refused, never assumed",
      r["refusal"] and r["missing_fields"][0].startswith("local_timezone"))
check("no sky feed: sky_note null, not in provenance", (lambda x: x["sky_note"] is None and "sky" not in x["provenance"])(
    S({"event_id": "j", "timestamp_utc": "2026-11-03T14:00:00Z", "event_type": "test_meal"}, natal, cfg=cfg)))

st = MemoryStore()
st.put_natal(natal)
try:
    st.put_natal(dict(natal, birth_location="y"))
    check("natal record is write-once", False)
except Refused:
    check("natal record is write-once", st.get_natal("e1")["birth_location"] == "x")
check("score_event refuses an unknown event_id", score_event(st, event_id="nope")["refusal"])
# The Anansi call shape (phase 2): ati.score(entity_id, timestamp_utc, event_type=None, location=None)
r = score("e1", "2026-11-03T14:00:00Z", None, None, store=st, cfg=cfg)
check("contract: event_type null returns window + pressure, event fields null, not a refusal",
      not r["refusal"] and r["active_window"]["organ"] == "Stomach" and r["event_house"] is None
      and r["event_organ"] is None and r["match_level"] is None and r["provenance"] == ["natal", "clock"])
r = score("e1", "2026-11-03T14:00:00Z", "test_meal", {"lat": 41.9, "lon": -87.6, "tz": "America/Chicago"},
          store=st, cfg=cfg)
check("contract: typed call scores", r["match_level"] == "direct")
check("contract: response keys are exactly the contract's", set(r) == {
    "refusal", "active_window", "event_house", "event_organ", "match_level", "natal_pressure_note",
    "sky_note", "confidence", "provenance"})
r = score("nobody", "2026-11-03T14:00:00Z", None, None, store=st, cfg=cfg)
check("contract: no natal record -> refusal with fixed_natal",
      set(r) == {"refusal", "missing_fields", "recommendation"} and r["missing_fields"] == ["fixed_natal"])

# BaZi derivation (ati/bazi.py). Checked against dates whose pillars are known
# independently, and the Li Chun boundary on both sides of it.
from ati.bazi import pillars, sun_longitude  # noqa: E402
from ati.store import with_pillars  # noqa: E402
from datetime import datetime as _dt, timezone as _tz  # noqa: E402


def _hz(p):
    return [p[k]["hanzi"] for k in ("year", "month", "day", "hour")]


check("solar longitude: the Sun is at 315 deg at Li Chun 2024 (2024-02-04 08:27 UTC)",
      abs(sun_longitude(_dt(2024, 2, 4, 8, 27, tzinfo=_tz.utc)) - 315.0) < 0.05)
check("2000-01-01: 己卯 year (before Li Chun), 丙子 month, 戊午 day",
      _hz(pillars("2000-01-01T12:00", "UTC"))[:3] == ["己卯", "丙子", "戊午"])
check("2024-02-10 (Lunar New Year): 甲辰 甲辰-day, 丙寅 month, 庚午 hour",
      _hz(pillars("2024-02-10T12:00", "Asia/Shanghai")) == ["甲辰", "丙寅", "甲辰", "庚午"])
check("the year turns at Li Chun, not 1 January: 2024-02-03 is still 癸卯",
      _hz(pillars("2024-02-03T12:00", "Asia/Shanghai"))[0] == "癸卯")
birth = {"entity_id": "x", "birth_datetime_utc": "2024-02-10T04:00:00Z", "birth_location": {"lon": 121.5},
         "birth_datetime_local": "2024-02-10T12:00", "birth_timezone": "Asia/Shanghai"}
st2 = MemoryStore()
st2.put_natal(dict(birth))
check("a natal record with birth data gets its pillars derived on write - never left for hand entry",
      st2.get_natal("x")["bazi_pillars"]["day"]["hanzi"] == "甲辰")
try:
    st2.put_natal({"entity_id": "y", "birth_datetime_utc": "2024-02-10T04:00:00Z", "birth_location": "x"})
    check("birth data present but pillars underivable -> the record is refused", False)
except Refused:
    check("birth data present but pillars underivable -> the record is refused", st2.get_natal("y") is None)
st3 = MemoryStore()
st3.d["natal:z"] = dict(birth, entity_id="z", bazi_pillars=None)
v2 = st3.amend_natal("z", {k: v for k, v in with_pillars(st3.get_natal("z")).items()
                           if k in ("bazi_pillars", "bazi_derivation")}, "test")
check("pillars added as an append-only version: v2 names v1, v1 unchanged",
      v2["version"] == 2 and v2["supersedes"] == "natal:z" and st3.d["natal:z"]["bazi_pillars"] is None
      and st3.get_natal("z")["bazi_pillars"])
try:
    st3.amend_natal("z", {"birth_location": "elsewhere"}, "test")
    check("an amendment cannot change a recorded field", False)
except Refused:
    check("an amendment cannot change a recorded field", True)

# Lunar calendar (ati/lunar.py): verified against published values before anything
# builds on it - new moons, every Lunar New Year 2017-2027 and 1995, five leap months.
from datetime import date as _date, timedelta as _td  # noqa: E402
from ati.lunar import lunar_date, new_moon, _sui, CST, _k_near  # noqa: E402
nm_err = []
for iso in ("2024-02-09T22:59", "2023-01-21T20:53", "2025-01-29T12:36", "2000-01-06T18:14"):
    t = _dt.fromisoformat(iso).replace(tzinfo=_tz.utc)
    k = _k_near(t)
    best = min((new_moon(kk) for kk in (k - 1, k, k + 1)), key=lambda x: abs((x - t).total_seconds()))
    nm_err.append(abs((best - t).total_seconds()) / 60)
check(f"new moons within 2 min of published instants (worst {max(nm_err):.1f} min)", max(nm_err) < 2)
cny_bad = []
for y, d in {1995: "1995-01-31", 2017: "2017-01-28", 2018: "2018-02-16", 2019: "2019-02-05",
             2020: "2020-01-25", 2021: "2021-02-12", 2022: "2022-02-01", 2023: "2023-01-22",
             2024: "2024-02-10", 2025: "2025-01-29", 2026: "2026-02-17", 2027: "2027-02-06"}.items():
    dd = _date.fromisoformat(d)
    t = _dt(dd.year, dd.month, dd.day, 12, tzinfo=CST)
    L, E = lunar_date(t), lunar_date(t - _td(days=1))
    if (L["year"], L["month"], L["day"], L["leap"]) != (y, 1, 1, False) or (E["year"], E["month"]) != (y - 1, 12):
        cny_bad.append(y)
check("every Lunar New Year 2017-2027 and 1995 falls on its published date, the eve in month 12"
      + (f" (wrong: {cny_bad})" if cny_bad else ""), not cny_bad)
leap_bad = []
for y, (mon, start) in {1995: (8, "1995-09-25"), 2017: (6, "2017-07-23"), 2020: (4, "2020-05-23"),
                        2023: (2, "2023-03-22"), 2025: (6, "2025-07-25")}.items():
    got = [(m["month"], m["start"].isoformat()) for m in _sui(y) + _sui(y + 1) if m["leap"] and m["start"].year == y]
    if got != [(mon, start)]:
        leap_bad.append(y)
check("leap months 1995/2017/2020/2023/2025 on their published months and dates"
      + (f" (wrong: {leap_bad})" if leap_bad else ""), not leap_bad)

# I Ching (ati/ichi.py)
from ati.ichi import HEXAGRAMS, KING_WEN, lines_of, najia, plum_blossom, cast, _BY_NAME  # noqa: E402
from zoneinfo import ZoneInfo as _ZI  # noqa: E402
check("64-hexagram table: all 64 trigram pairs, each King Wen number once",
      len(HEXAGRAMS) == 64 and sorted(n for n, _ in HEXAGRAMS.values()) == list(range(1, 65)))
kw_bad = []
for i in range(0, 64, 2):
    a_ = lines_of(_BY_NAME[KING_WEN[i][2]], _BY_NAME[KING_WEN[i][3]])
    b_ = lines_of(_BY_NAME[KING_WEN[i + 1][2]], _BY_NAME[KING_WEN[i + 1][3]])
    if not ((b_ == a_[::-1] and a_ != a_[::-1]) or (a_ == a_[::-1] and b_ == tuple(1 - x for x in a_))):
        kw_bad.append(KING_WEN[i][0])
check("King Wen pairing rule holds for all 32 pairs (turned over, or inverted when symmetric)", not kw_bad)
check("anchors: heaven over thunder is 25 無妄, earth over thunder 24 復, 大壯 34 is thunder over heaven",
      HEXAGRAMS[(1, 4)] == (25, "無妄") and HEXAGRAMS[(8, 4)] == (24, "復") and HEXAGRAMS[(4, 1)] == (34, "大壯"))
ex = plum_blossom(5, 12, 17, 9)
check("the classical worked example (辰 year, month 12, day 17, 申 hour) gives 49 革, line 1 moving, to 咸",
      (ex["primary"]["number"], ex["moving_line"], ex["changed"]["name"], ex["mutual"]["name"]) == (49, 1, "咸", "姤"))
check("Najia by the standard table: 乾 甲子..壬戌, 坤 乙未..癸酉",
      najia(1, 1) == ["甲子", "甲寅", "甲辰", "壬午", "壬申", "壬戌"]
      and najia(8, 8) == ["乙未", "乙巳", "乙卯", "癸丑", "癸亥", "癸酉"])
m = _dt(2026, 11, 3, 19, 21, tzinfo=_ZI("America/Chicago"))
check("a cast is deterministic and uses the moment's own lunar date and hour", cast(m) == cast(m)
      and cast(m)["lunar"] == {"year": 2026, "month": 9, "day": 25, "leap": False} and cast(m)["hour_branch"] == "戌")

# Lighting (ati/lighting.py): the classical tables, and nothing natal leaks into the page.
from ati.lighting import GENERATES, CONTROLS, CLASHES, ORGANS, interactions as _inter, lighting as _lighting  # noqa: E402
check("generation cycle wood>fire>earth>metal>water>wood and control cycle wood>earth>water>fire>metal",
      GENERATES == {"wood": "fire", "fire": "earth", "earth": "metal", "metal": "water", "water": "wood"}
      and CONTROLS == {"wood": "earth", "earth": "water", "water": "fire", "fire": "metal", "metal": "wood"})
check("the six clashes are 子午 丑未 寅申 卯酉 辰戌 巳亥", CLASHES == {frozenset(p) for p in (
    ("zi", "wu"), ("chou", "wei"), ("yin", "shen"), ("mao", "you"), ("chen", "xu"), ("si", "hai"))})
check("each element maps to its zang-fu pair", ORGANS["water"] == ["kidney", "bladder"] and ORGANS["metal"] == ["lung", "large_intestine"])
_be = tables()["clock"]["branch_elements"]
_cur = {k: {"stem": s_, "branch": b_} for k, (s_, b_) in zip(("year", "month", "day", "hour"),
        (("bing", "wu"), ("wu", "xu"), ("xin", "si"), ("ji", "hai")))}
_nat = {"year": {"stem": "jia", "branch": "zi"}}
_got = {(i["type"], i["current"]) for i in _inter(_cur, _nat, _be)}
check("interactions: 午 now clashes natal 子; 辛 metal controls natal 甲 wood; 丙 fire generates nothing in wood",
      ("clash", "year") in _got and ("control", "day") in _got and ("generation", "year") not in _got)
_L = _lighting(_dt(2026, 11, 3, 19, 40, tzinfo=_ZI("America/Chicago")), None)
check("no natal record: natal pillars absent and no interactions, houses unlit and saying why",
      _L["natal_pillars"] is None and _L["interactions"] == [] and _L["houses_lit"] == []
      and _L["houses_status"].startswith("unlit - ") and len(_L["houses_status"]) > 12)
import json as _json  # noqa: E402
_hr = _json.load(open(os.path.join(ROOT, "config", "ati", "house_to_body_region.json"), encoding="utf-8"))["houses"]
check("house_to_body_region holds the twelve regions verbatim, head to feet; house_to_organ stays unset",
      _hr["1"] == "Head and face" and _hr["12"] == "Feet" and len(_hr) == 12
      and _json.load(open(os.path.join(ROOT, "config", "ati", "house_to_organ.json"), encoding="utf-8"))["houses"] == {})
_page = open(os.path.join(ROOT, "webapp", "body.html"), encoding="utf-8").read()
check("webapp/body.html holds no pillar, natal or window values - they arrive from the lighting call",
      not re.search(r"[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]", _page) and "\"lighting\"" in _page
      and "11:00" not in _page)

# Houses (ati/ephemeris.py + ati/lighting.houses): whole-sign arithmetic everywhere;
# accuracy wherever the ephemeris is installed, SKIPPED out loud where it is not.
from ati.ephemeris import whole_sign_house, available as _eph_ok  # noqa: E402
from ati.lighting import houses as _houses  # noqa: E402
check("whole-sign houses: same sign as the ascendant is house 1, the sign before it house 12",
      whole_sign_house(245.0, 250.0) == 1 and whole_sign_house(235.0, 250.0) == 12 and whole_sign_house(5.0, 250.0) == 5)
_ok, _why = _eph_ok()
if not _ok:
    print(f"  SKIP ephemeris accuracy checks - {_why} (they run where Skyfield and DE421 are installed)")
    _lit, _gh, _st = _houses(_dt(2026, 11, 3, 23, 0, tzinfo=_tz.utc), {"birth_datetime_utc": "x"})
    check("without the ephemeris nothing is lit, and the status says why", _lit == [] and "unavailable" in _st)
else:
    from ati.ephemeris import longitudes, angles, sign_of  # noqa: E402
    _t = _dt(2024, 2, 4, 8, 27, tzinfo=_tz.utc)
    check("ephemeris: Skyfield's Sun agrees with the verified Meeus Sun at Li Chun 2024 (within 0.02 deg)",
          abs(longitudes(_t)["sun"] - sun_longitude(_t)) < 0.02)
    _n = longitudes(_dt(2024, 2, 9, 22, 59, tzinfo=_tz.utc))
    check("ephemeris: the Moon sits on the Sun at the verified new moon 2024-02-09 22:59Z (within 0.1 deg)",
          abs(((_n["moon"] - _n["sun"] + 180) % 360) - 180) < 0.1)
    _syn = {"birth_datetime_utc": "1990-06-15T20:05:00Z", "birth_location": {"lat": 39.7392, "lon": -104.9903}}
    _asc = sign_of(angles(_dt(1990, 6, 15, 20, 5, tzinfo=_tz.utc), 39.7392, -104.9903)["ascendant"])
    _lit, _gh, _st = _houses(_dt(2026, 11, 3, 23, 0, tzinfo=_tz.utc), dict(_syn, ascendant={"tropical_whole_sign": _asc}))
    _blob = _json.dumps({"lit": _lit, "ghosts": _gh})
    check("houses: a synthetic chart lights house numbers only - no degrees, no sign names",
          _lit and _gh and not re.search(r"\d+\.\d", _blob) and not any(x in _blob for x in (
              "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio", "Sagittarius",
              "Capricorn", "Aquarius", "Pisces")))
    _wrong = "Aries" if _asc != "Aries" else "Taurus"
    _lit, _gh, _st = _houses(_dt(2026, 11, 3, 23, 0, tzinfo=_tz.utc), dict(_syn, ascendant={"tropical_whole_sign": _wrong}))
    check("houses: a record whose stated ascendant disagrees with the computed one lights nothing",
          _lit == [] and _gh == [] and "disagrees" in _st)

# Layer 2 - SCHEMATIC meridians (webapp/body.html's geometry block): the principal's
# direction rules and labelling, enforced so a later edit cannot drift from them.
_bp = open(os.path.join(ROOT, "webapp", "body.html"), encoding="utf-8").read()
_gm = re.search(r'<script type="application/json" id="geometry">(.*?)</script>', _bp, re.S)
_G = _json.loads(_gm.group(1)) if _gm else {}
_M, _F = _G.get("meridians") or {}, _G.get("frame") or {}
check("twelve schematic pathways, the layer and every path labelled 'schematic reference — not for clinical use'",
      len(_M) == 12 and _G.get("_label") == "schematic reference — not for clinical use"
      and "${GEO._label}" in _bp
      and not re.search(r"follows? classical texts|huangdi|classically accurate|anatomically accurate", _bp, re.I))
check("posterior channels - Bladder, Small Intestine, Triple Burner - are the ones drawn dashed",
      {k for k, m in _M.items() if m.get("posterior")} == {"bladder", "small_intestine", "triple_burner"})
_mid = _F.get("midline_x", 200)
check("Spleen and Stomach stay off the midline (never within 12 units of it)",
      all(abs(x - _mid) >= 12 for k in ("spleen", "stomach") for x, _ in _M[k]["path"]))
_st = _M["stomach"]["points"]
check("ST36 sits below the knee and above the ankle, and above ST42 on the foot",
      _F["knee_y"] < _st["ST36"][1] < _F["ankle_y"] and _st["ST36"][1] < _st["ST42"][1])
_pts = _json.load(open(os.path.join(ROOT, "config", "ati", "meridian_points.json"), encoding="utf-8"))["points"]
check("every yuan, luo and rule-derived horary point in the config is placed on its own channel",
      all(c in _M[m]["points"] for m, p in _pts.items() for c, _, _ in p))
_out = [tuple(q) for q in _G.get("outline") or []]
_hd = (_F.get("hand") or {})
def _digit_tips(seg):
    """Digit tips are the local extremes along the hand's edge: a point further from the wrist
    than both neighbours. Five per hand - thumb and four fingers - or the hands have collapsed."""
    wy = min(p[1] for p in seg)
    d = [((p[0] - seg[0][0]) ** 2 + (p[1] - wy) ** 2) ** .5 for p in seg]
    return sum(1 for i in range(1, len(seg) - 1) if d[i] > d[i - 1] and d[i] >= d[i + 1] and d[i] > 30)
try:
    _a, _b = _out.index(tuple(_hd["wrist_outer"])), _out.index(tuple(_hd["wrist_inner"]))
    _right = _out[_a:_b + 1]
    _left = [(400 - x, y) for x, y in _right]
    _tips = (_digit_tips(_right), _digit_tips(_left[::-1]))
except (KeyError, ValueError):
    _tips = (0, 0)
check(f"each hand is open with five separate digits (found {_tips[0]} and {_tips[1]})", _tips == (5, 5))
check("the Meridians toggle is enabled now that the paths are drawn",
      re.search(r'id="t-meridians"[^>]*\bdisabled\b', _bp) is None and 'id="t-meridians"' in _bp)

banned = {"threading", "sched", "asyncio", "multiprocessing"}
loops = []
for fn in os.listdir(os.path.join(ROOT, "ati")):
    if not fn.endswith(".py"):
        continue
    tree = ast.parse(open(os.path.join(ROOT, "ati", fn)).read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + ([node.module] if getattr(node, "module", None) else [])
            if any(n.split(".")[0] in banned for n in names):
                loops.append(f"{fn}: imports {names}")
        # A bounded loop (store._unwrap digs through at most 8 envelopes) is not a
        # background process; an unconditional one is how a watcher starts.
        if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) and node.test.value:
            loops.append(f"{fn}:{node.lineno} while True")
check("on demand only: no threads, timers or loops in ati/" + (f" ({loops})" if loops else ""), not loops)

print("check_ati: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
