"""Ward: the relation axis. How hard is this message leaning on a boundary?

Three axes, three questions, and none of them answers another's:

    Pulse  - the REGIME. May anything actuate now?          (domain agents)
    Veto   - the RULE.   Is there a ground to refuse?       (domain agents)
    Ward   - the PERSON. How is the asker pressing?         (Anansi only)

Ward scores the incoming message and a short window of the turns before it for
pressure patterns, and returns a MODULATION SIGNAL. That signal is consumed by
the voice layer (agents/anansi/voice.py) and by nothing else. It is not a block:
it never refuses, never approves, never moves a device, and it does not appear
in domain dispatch. Veto decides the ACTION; Ward decides the SURFACE - what the
refusal is allowed to sound like, and above all what it is not allowed to say.

WHY A SURFACE AXIS EXISTS. A refusal that explains itself exactly is a map of
the boundary. "Refused: no_604_permissible_purpose" tells a person who is
probing which fact to forge. The domain must be able to say precisely why
(that is an audit record); the person pressing on it must not be handed the
blueprint. Ward is the thing that knows the difference.

PRECEDENCE (resolve()):
    Pulse cold      -> Ward is irrelevant: nothing proceeds, there is no surface
                       worth shaping. Neutral.
    Veto refuses    -> the action is already decided. Ward chooses how it is spoken.
    Ward hard, no veto -> the action is allowed; the voice modulates / redirects.
                       Nothing is blocked on Ward's say-so.

Deterministic and dependency-free, like voice.py: nothing here calls a model.
Every fabrication this system has produced came from handing a small model a gap
and room to fill it, and a scorer that decides how a person is treated is the
last place to allow one. The vocabulary and thresholds are config
(config/anansi_ward.json), so they change without a code edit and
tools/check_ward.py can hold them to the card.
"""
import json
import os
import re

CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "config", "anansi_ward.json")

CLASSES = ("persistence", "reframe", "affect", "meta", "injection", "leak")

_STOP = frozenset("""the a an and or but to of in on at for with from is are was were be been it its this that
these those i me my you your we our they them he she his her do does did can could would should will just not no
so if as by about into than then there here have has had""".split())

_cache = {"mtime": None, "cfg": None, "compiled": None}


def load():
    """Config + compiled lexicon, reloaded when the file changes - the same
    contract as the voice config, so the vocabulary can be tuned live."""
    try:
        m = os.path.getmtime(CONFIG)
    except OSError:
        m = None
    if _cache["cfg"] is None or _cache["mtime"] != m:
        with open(CONFIG, encoding="utf-8") as fh:
            cfg = json.load(fh)
        comp = {c: [(re.compile(p, re.I), float(w)) for p, w in cfg["lexicon"].get(c, [])]
                for c in CLASSES}
        _cache.update(mtime=m, cfg=cfg, compiled=comp)
    return _cache["cfg"], _cache["compiled"]


def neutral(reason=None, implemented=True):
    out = {"pressure_class": "none", "intensity": "soft", "modulation": "neutral",
           "classes": {}, "implemented": implemented}
    if reason:
        out["reason"] = reason
    return out


def _tokens(text):
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(t) > 2 and t not in _STOP}


def _similar(a, b, floor):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= floor


def _window(turn_window, n):
    """Normalise the window to [(user_text, refused)], newest last, at most n.
    Accepts plain strings and the session entries Anansi stores
    ({"prompt": ..., "refused": bool})."""
    out = []
    for t in list(turn_window or [])[-n:]:
        if isinstance(t, str):
            out.append((t, False))
        elif isinstance(t, dict):
            txt = t.get("prompt") or t.get("message") or t.get("text") or ""
            if isinstance(txt, str) and txt:
                out.append((txt, bool(t.get("refused") or t.get("refusal_ids"))))
    return out


def score(message, turn_window=None, last_refusal_ids=None):
    """-> the card's outputs (+ `classes`, which classes fired and how hard).

    `message` is the incoming user text. `turn_window` is the few turns before
    it. `last_refusal_ids` are the refusal ids the previous reply carried -
    persistence means leaning on a boundary that was JUST held, so a repeat of
    an earlier question counts only when something was refused around it."""
    cfg, comp = load()
    sc = cfg["scoring"]
    msg = message if isinstance(message, str) else ""
    if not msg.strip():
        return neutral()

    raw = {c: sum(w for rx, w in comp[c] if rx.search(msg)) for c in CLASSES}

    win = _window(turn_window, int(sc["window_turns"]))
    ids = [i for i in (last_refusal_ids or []) if isinstance(i, str) and i]
    refused_ctx = bool(ids) or any(r for _, r in win)
    if refused_ctx:
        raw["persistence"] += sum(1 for txt, _ in win
                                  if _similar(msg, txt, float(sc["similarity_min"])))

    soft_min, hard_min = float(sc["soft_min"]), float(sc["hard_min"])
    fired = {c: ("hard" if v >= hard_min else "soft")
             for c, v in raw.items() if v >= soft_min}
    if not fired:
        return neutral()

    primary = next(c for c in sc["priority"] if c in fired)
    hard = (any(b == "hard" for b in fired.values())
            or len(fired) >= int(sc["hard_if_classes"]))
    m = cfg["modulation"]
    if not hard:
        mod = m["soft"]
    elif any(c in fired for c in sc["probing_classes"]):
        mod = m["hard_probing"]
    else:
        mod = m["hard_other"]
    return {"pressure_class": primary, "intensity": "hard" if hard else "soft",
            "modulation": mod, "classes": fired, "implemented": True}


def resolve(signal, regime=None):
    """Apply the precedence rules to a scored signal.

    Pulse cold makes Ward irrelevant. This is the ONLY place pulse enters: it is
    deliberately not an input of the verb (the card's inputs are message,
    turn_window, last_refusal_ids), because Ward does not read the regime - it
    is simply overruled by it."""
    if str(regime or "").lower() == "cold":
        return neutral("pulse_cold: nothing proceeds, so there is no surface to shape")
    return signal if isinstance(signal, dict) else neutral()


def known_refusal_ids():
    """Every refusal id the surface must keep quiet, from config."""
    cfg, _ = load()
    out = []
    for group, ids in cfg["refusal_ids"].items():
        if group != "_" and isinstance(ids, list):
            out.extend(ids)
    return out
