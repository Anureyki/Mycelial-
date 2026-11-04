#!/usr/bin/env python3
"""Deterministic I Ching casts for ATI. Same moment in, same hexagram out.

    from ati.ichi import cast
    cast(datetime(2026, 11, 3, 19, 21, tzinfo=ZoneInfo("America/Chicago")))

PLUM BLOSSOM, TIME METHOD (Mei Hua Yi Shu, attributed to Shao Yong): the numbers
are the lunar YEAR's branch (子=1 ... 亥=12), the LUNAR month, the LUNAR day and the
hour's branch.  upper = (Y+M+D) mod 8, lower = (Y+M+D+H) mod 8 (0 counts as 8),
moving line = (Y+M+D+H) mod 6 (0 counts as 6), trigrams in Earlier Heaven order
乾1 兌2 離3 震4 巽5 坎6 艮7 坤8. The lunar date comes from ati/lunar.py, derived
astronomically and verified against published Lunar New Years and leap months.
The book's own worked example - year 辰, month 12, day 17, hour 申 - must give
澤火革 (49) with the first line moving, and the gate holds it to that.

NAJIA: stems and branches attached to the six lines by the standard table (Jing
Fang): each trigram carries a stem and three branches as the inner (lower) trigram
and as the outer (upper) one - 乾 takes 甲 within and 壬 without, 坤 乙/癸, 震 庚,
巽 辛, 坎 戊, 離 己, 艮 丙, 兌 丁.

Not here, on purpose: a "Wen Wang" cast. Wen Wang's six-line method is cast with
coins or yarrow; a time formula under that name would claim a method it is not.
"""
from datetime import datetime

from ati.lunar import lunar_date

BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
# Earlier Heaven numbers; lines are bottom -> top, 1 = yang (solid), 0 = yin (broken)
TRIGRAMS = {
    1: ("乾", "heaven", (1, 1, 1)), 2: ("兌", "lake", (1, 1, 0)), 3: ("離", "fire", (1, 0, 1)),
    4: ("震", "thunder", (1, 0, 0)), 5: ("巽", "wind", (0, 1, 1)), 6: ("坎", "water", (0, 1, 0)),
    7: ("艮", "mountain", (0, 0, 1)), 8: ("坤", "earth", (0, 0, 0)),
}
_BY_NAME = {v[0]: k for k, v in TRIGRAMS.items()}
_BY_LINES = {v[2]: k for k, v in TRIGRAMS.items()}

# King Wen order: (number, name, upper trigram, lower trigram)
KING_WEN = [
    (1, "乾", "乾", "乾"), (2, "坤", "坤", "坤"), (3, "屯", "坎", "震"), (4, "蒙", "艮", "坎"),
    (5, "需", "坎", "乾"), (6, "訟", "乾", "坎"), (7, "師", "坤", "坎"), (8, "比", "坎", "坤"),
    (9, "小畜", "巽", "乾"), (10, "履", "乾", "兌"), (11, "泰", "坤", "乾"), (12, "否", "乾", "坤"),
    (13, "同人", "乾", "離"), (14, "大有", "離", "乾"), (15, "謙", "坤", "艮"), (16, "豫", "震", "坤"),
    (17, "隨", "兌", "震"), (18, "蠱", "艮", "巽"), (19, "臨", "坤", "兌"), (20, "觀", "巽", "坤"),
    (21, "噬嗑", "離", "震"), (22, "賁", "艮", "離"), (23, "剝", "艮", "坤"), (24, "復", "坤", "震"),
    (25, "無妄", "乾", "震"), (26, "大畜", "艮", "乾"), (27, "頤", "艮", "震"), (28, "大過", "兌", "巽"),
    (29, "坎", "坎", "坎"), (30, "離", "離", "離"), (31, "咸", "兌", "艮"), (32, "恆", "震", "巽"),
    (33, "遯", "乾", "艮"), (34, "大壯", "震", "乾"), (35, "晉", "離", "坤"), (36, "明夷", "坤", "離"),
    (37, "家人", "巽", "離"), (38, "睽", "離", "兌"), (39, "蹇", "坎", "艮"), (40, "解", "震", "坎"),
    (41, "損", "艮", "兌"), (42, "益", "巽", "震"), (43, "夬", "兌", "乾"), (44, "姤", "乾", "巽"),
    (45, "萃", "兌", "坤"), (46, "升", "坤", "巽"), (47, "困", "兌", "坎"), (48, "井", "坎", "巽"),
    (49, "革", "兌", "離"), (50, "鼎", "離", "巽"), (51, "震", "震", "震"), (52, "艮", "艮", "艮"),
    (53, "漸", "巽", "艮"), (54, "歸妹", "震", "兌"), (55, "豐", "震", "離"), (56, "旅", "離", "艮"),
    (57, "巽", "巽", "巽"), (58, "兌", "兌", "兌"), (59, "渙", "巽", "坎"), (60, "節", "坎", "兌"),
    (61, "中孚", "巽", "兌"), (62, "小過", "震", "艮"), (63, "既濟", "坎", "離"), (64, "未濟", "離", "坎"),
]
HEXAGRAMS = {(_BY_NAME[u], _BY_NAME[l]): (n, name) for n, name, u, l in KING_WEN}

# Najia: trigram -> (inner stem, inner branches lines 1-3, outer stem, outer branches lines 4-6)
NAJIA = {
    "乾": ("甲", "子寅辰", "壬", "午申戌"), "坤": ("乙", "未巳卯", "癸", "丑亥酉"),
    "震": ("庚", "子寅辰", "庚", "午申戌"), "巽": ("辛", "丑亥酉", "辛", "未巳卯"),
    "坎": ("戊", "寅辰午", "戊", "申戌子"), "離": ("己", "卯丑亥", "己", "酉未巳"),
    "艮": ("丙", "辰午申", "丙", "戌子寅"), "兌": ("丁", "巳卯丑", "丁", "亥酉未"),
}


def lines_of(upper, lower):
    """Six lines, bottom -> top."""
    return TRIGRAMS[lower][2] + TRIGRAMS[upper][2]


def from_lines(lines):
    lower, upper = _BY_LINES[tuple(lines[:3])], _BY_LINES[tuple(lines[3:])]
    return upper, lower


def hexagram(upper, lower):
    n, name = HEXAGRAMS[(upper, lower)]
    return {"number": n, "name": name, "upper": TRIGRAMS[upper][0], "lower": TRIGRAMS[lower][0],
            "upper_number": upper, "lower_number": lower}


def najia(upper, lower):
    """Stem and branch on each line, bottom -> top, by the standard table."""
    lo, up = NAJIA[TRIGRAMS[lower][0]], NAJIA[TRIGRAMS[upper][0]]
    return [lo[0] + b for b in lo[1]] + [up[2] + b for b in up[3]]


def plum_blossom(year_branch_number, lunar_month, lunar_day, hour_branch_number):
    """The time method, from its four numbers."""
    s = year_branch_number + lunar_month + lunar_day
    upper, lower, moving = s % 8 or 8, (s + hour_branch_number) % 8 or 8, (s + hour_branch_number) % 6 or 6
    lines = list(lines_of(upper, lower))
    lines[moving - 1] ^= 1
    cu, cl = from_lines(lines)
    mut = lines_of(upper, lower)
    mu, ml = from_lines(mut[1:4] + mut[2:5])
    return {"primary": hexagram(upper, lower), "moving_line": moving, "changed": hexagram(cu, cl),
            "mutual": hexagram(mu, ml), "najia": najia(upper, lower),
            "numbers": {"year_branch": year_branch_number, "lunar_month": lunar_month,
                        "lunar_day": lunar_day, "hour_branch": hour_branch_number}}


def cast(moment):
    """A Plum Blossom time cast for a timezone-aware moment, in its own local date and hour."""
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise ValueError("cast needs a timezone-aware datetime - the hour and date are local")
    lun = lunar_date(moment, local_date=True)
    hour_branch = ((moment.hour + 1) // 2) % 12 + 1
    out = plum_blossom(lun["year_branch_number"], lun["month"], lun["day"], hour_branch)
    out.update(method="plum_blossom_time", moment=moment.isoformat(timespec="minutes"),
               lunar={k: lun[k] for k in ("year", "month", "day", "leap")},
               year_branch=BRANCHES[lun["year_branch_number"] - 1], hour_branch=BRANCHES[hour_branch - 1],
               flags=lun["flags"] + (["leap month: its number is the month it repeats"] if lun["leap"] else []),
               basis="local date and hour; lunar months reckoned in China time (ati/lunar.py)")
    return out
