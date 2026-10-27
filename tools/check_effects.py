#!/usr/bin/env python3
"""Build gate: a card's `effects` say what its verbs do, and the code agrees.

Every capability stays declared (the card is how Anansi routes; CLAUDE.md's
82-undeclared failure). Beside the list, `effects` maps each verb to one of
core/effects.CLASSES. This gate fails the build when:

  - a declared verb has no effect class;
  - the declared class differs from what core/effects reads off the code -
    a verb that starts writing, or notifying, cannot keep an older label;
  - an `actuates` or `external` verb names no authorization in `effects_gated_by`
    (hardware sits behind Boss's authorization; a third party behind the
    principal's sign-off - CLAUDE.md).

Cards without an `effects` map are reported, not failed: Grow is first.

    python3 tools/check_effects.py              # the gate
    python3 tools/check_effects.py --write grow_agent   # regenerate a card's map
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
from core.effects import CLASSES, detect  # noqa: E402

# agent_id -> (source, class) for agents whose cards carry an effects map
AGENTS = {"grow_agent": ("agents/grow_agent/grow_agent.py", "GrowAgent")}


def card_path(aid):
    return os.path.join(ROOT, "config", "agent_cards", aid + ".json")


def detected(aid):
    src, cls = AGENTS[aid]
    _, of_verb = detect(os.path.join(ROOT, src), cls)
    return of_verb


def write(aid):
    p = card_path(aid)
    card = json.load(open(p, encoding="utf-8"))
    of_verb = detected(aid)
    card["effects"] = {v: of_verb(v, set())[0] for v in card["capabilities"]}
    card["effects_basis"] = ("read from the code by core/effects.py (dispatch path, self-calls, "
                             "inherited base methods; operational logging excluded); "
                             "kept equal to the code by tools/check_effects.py")
    card.setdefault("effects_gated_by", {})
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    counts = {c: sum(1 for e in card["effects"].values() if e == c) for c in CLASSES}
    print(f"{aid}: effects written - {counts}")


def gate():
    fails = []
    print("effect classes on agent cards")
    for fn in sorted(os.listdir(os.path.join(ROOT, "config", "agent_cards"))):
        if not fn.endswith(".json"):
            continue
        aid = fn[:-5]
        card = json.load(open(card_path(aid), encoding="utf-8"))
        eff = card.get("effects")
        if eff is None:
            print(f"  -     {aid}: no effects map yet ({len(card.get('capabilities') or [])} verbs)")
            continue
        if aid not in AGENTS:
            fails.append(f"{aid}: has an effects map but no source registered in AGENTS")
            continue
        of_verb = detected(aid)
        missing = [v for v in card["capabilities"] if v not in eff]
        bad = [v for v, c in eff.items() if c not in CLASSES]
        drift = [(v, eff[v], of_verb(v, set())[0]) for v in card["capabilities"]
                 if v in eff and eff[v] in CLASSES and eff[v] != of_verb(v, set())[0]]
        gated = card.get("effects_gated_by") or {}
        ungated = [v for v, c in eff.items() if c in ("actuates", "external") and not gated.get(v)]
        for msg, items in (("verbs with no effect class", missing), ("unknown classes", bad),
                           ("declared class differs from the code", drift),
                           ("actuates/external verbs naming no authorization", ungated)):
            if items:
                fails.append(f"{aid}: {msg}: {items[:8]}")
        counts = {c: sum(1 for x in eff.values() if x == c) for c in CLASSES}
        ok = not (missing or bad or drift or ungated)
        print(f"  {'PASS' if ok else 'FAIL'}  {aid}: {counts}")
    for f in fails:
        print(f"  FAIL  {f}")
    print("ALL PASS" if not fails else f"{len(fails)} FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--write":
        write(sys.argv[2])
        sys.exit(0)
    sys.exit(gate())
