#!/usr/bin/env python3
"""Build gate for Wasserstein k-means market-regime detection (2026-11-17).

Pure standard library (no numpy) - runs in the minimal CI env. Proves:
- RECOVERY: a synthetic 2-regime series (low-vol vs high-vol) is split correctly;
- DISTRIBUTIONAL: shuffling a window's returns does not change its regime - it
  clusters the DISTRIBUTION, not the path (W2 is order-invariant within a window);
- W2 SEMANTICS: a pure shift of a sample moves it by ~the shift in W2;
- FAIL-CLOSED: too little data returns ok:False with a reason, never a fake clustering;
- CLAIM, NOT VERDICT: an unsupervised observation, contestable, confidence = separation,
  carrying NO execution surface (no order/buy/sell);
- REPRODUCIBLE: same input + seed -> identical assignments;
- VERB WIRING: the Trading agent dispatches to it and fail-closes on bad input.
"""
import os
import random
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import regime_detection as rd  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


def allclose(a, b, tol=1e-9):
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


rng = random.Random(42)
low = [rng.gauss(0.001, 0.004) for _ in range(200)]    # calm, slight drift up
high = [rng.gauss(-0.002, 0.04) for _ in range(200)]   # volatile, drift down (10x vol gap)
series = low + high

res = rd.detect_regimes(series, n_regimes=2, window=20, step=20, n_quantiles=16, seed=0)
check("a clean 2-regime series clusters (ok)", res.get("ok") is True)

# RECOVERY.
asg = {a["window"]: a["regime"] for a in res["assignments"]}
first = [asg[i] for i in range(10)]        # windows 0-9 = calm half
second = [asg[i] for i in range(10, 20)]   # windows 10-19 = volatile half
check("recovers the two regimes (calm half one label, volatile half the other)",
      len(set(first)) == 1 and len(set(second)) == 1 and set(first) != set(second))
check("separation is high for well-separated regimes (>0.5)", res["separation"] > 0.5)

# DISTRIBUTIONAL: shuffling within windows keeps the split.
shuffled = []
for a in range(0, len(series), 20):
    w = series[a:a + 20]
    rng.shuffle(w)
    shuffled.extend(w)
res2 = rd.detect_regimes(shuffled, n_regimes=2, window=20, step=20, n_quantiles=16, seed=0)
asg2 = {a["window"]: a["regime"] for a in res2["assignments"]}
check("clusters the DISTRIBUTION not the path (shuffling within windows keeps the split)",
      {asg2[i] for i in range(10)} != {asg2[i] for i in range(10, 20)})
check("quantile_profile is order-invariant (a window and its reverse have equal profiles)",
      allclose(rd.quantile_profile(series[:20], 16),
               rd.quantile_profile(list(reversed(series[:20])), 16)))

# W2 SEMANTICS.
sample = [rng.gauss(0, 1) for _ in range(500)]
c = 0.7
check("W2 of a sample vs itself is 0", abs(rd.w2_distance(sample, sample)) < 1e-9)
check("W2 of a sample vs a +c shift is ~c (distributional translation)",
      abs(rd.w2_distance(sample, [x + c for x in sample]) - c) < 0.05)

# FAIL-CLOSED.
check("too few points for even one window -> ok:False",
      rd.detect_regimes([0.1, 0.2, 0.3], n_regimes=2, window=20).get("ok") is False)
check("more regimes than windows -> ok:False",
      rd.detect_regimes(series, n_regimes=99, window=20, step=20).get("ok") is False)

# CLAIM, NOT VERDICT + NO EXECUTION SURFACE.
claim = res.get("claim", {})
check("result is an unsupervised observation, contestable, confidence = separation",
      claim.get("kind") == "unsupervised_observation" and claim.get("contestable") is True
      and claim.get("confidence") == res["separation"])
check("no execution surface (no order/buy/sell/execute keys) - safe side of the capital boundary",
      not any(k in res for k in ("order", "buy", "sell", "execute", "trade")))

# REPRODUCIBLE.
again = rd.detect_regimes(series, n_regimes=2, window=20, step=20, n_quantiles=16, seed=0)
check("same input + seed -> identical assignments (reproducible)",
      [a["regime"] for a in res["assignments"]] == [a["regime"] for a in again["assignments"]])

# VERB WIRING.
import agents.trading_agent.trading_agent as ta  # noqa: E402
agent = object.__new__(ta.TradingAgent)
check("trading_agent.detect_regimes verb returns a clustering",
      agent.detect_regimes({"returns": series, "n_regimes": 2, "window": 20, "step": 20}).get("ok") is True)
check("the verb fail-closes on a missing/short series",
      agent.detect_regimes({"returns": [0.1]}).get("ok") is False)

print("check_regime_detection: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
