"""Wasserstein k-means market-regime detection (2026-11-17).

Clusters windows of a return series by the 2-Wasserstein distance between their
return DISTRIBUTIONS, not Euclidean distance on raw values - the right geometry
for finance, where a regime is a distribution, not a point. On 1-D the W2
distance between two empirical distributions equals the Euclidean distance
between their quantile functions, and the W2 barycentre is the quantile-wise
mean, so W2 k-means reduces EXACTLY to Euclidean k-means on quantile vectors.
(Horvath, Issa, Muguruza-Gonzalez, "Clustering Market Regimes using the
Wasserstein Distance", SSRN 3947905.)

Pure standard library - NO numpy - so it runs in the minimal CI env and on any
remote/FL node, not only where the heavy ML stack is installed. This is
ANALYSIS, not actuation: it labels regimes and holds no order verb, so it sits
on the safe side of the capital-actuation boundary. A regime label is a CLAIM -
an unsupervised observation with a separation-based confidence, contestable,
never a verdict. Deterministic given `seed` (reproducibility is first-class).
"""
import math
import random


def quantile_profile(window, n_quantiles):
    """The window's return distribution as a quantile vector - its W2 coordinate.
    Order-invariant within the window: a regime is the distribution, not the path."""
    xs = sorted(float(x) for x in window)
    n = len(xs)
    out = []
    for i in range(n_quantiles):
        p = i / (n_quantiles - 1) if n_quantiles > 1 else 0.0
        if n == 1:
            out.append(xs[0])
            continue
        pos = p * (n - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        out.append(xs[lo] * (1.0 - frac) + xs[hi] * frac)
    return out


def w2_distance(a, b, n_quantiles=64):
    """2-Wasserstein distance between two 1-D samples = L2 distance of their
    quantile functions. The identity this whole method rests on."""
    qa, qb = quantile_profile(a, n_quantiles), quantile_profile(b, n_quantiles)
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(qa, qb)) / len(qa))


def _dist2(u, v):
    return sum((a - b) ** 2 for a, b in zip(u, v))


def _mean_vec(vectors):
    m, n = len(vectors[0]), len(vectors)
    return [sum(v[i] for v in vectors) / n for i in range(m)]


def _kmeans(X, k, seed, iters=100):
    """Lloyd's k-means with k-means++ init, pure Python, deterministic by seed.
    On quantile vectors this IS W2 k-means (Euclidean here = W2 on 1-D)."""
    rng = random.Random(seed)
    n = len(X)
    centres = [X[rng.randrange(n)]]
    for _ in range(1, k):
        d2 = [min(_dist2(x, c) for c in centres) for x in X]
        s = sum(d2)
        if s <= 0:
            centres.append(X[rng.randrange(n)])
            continue
        r, acc, chosen = rng.random() * s, 0.0, X[-1]
        for x, w in zip(X, d2):
            acc += w
            if acc >= r:
                chosen = x
                break
        centres.append(chosen)
    C = [list(c) for c in centres]
    labels = [-1] * n
    for _ in range(iters):
        new = [min(range(k), key=lambda j, x=x: _dist2(x, C[j])) for x in X]
        if new == labels:
            break
        labels = new
        for j in range(k):
            members = [X[i] for i in range(n) if labels[i] == j]
            if members:
                C[j] = _mean_vec(members)
    inertia = sum(_dist2(X[i], C[labels[i]]) for i in range(n))
    return labels, C, inertia


def detect_regimes(returns, n_regimes=3, window=20, step=None, n_quantiles=16, seed=0):
    """Cluster windows of `returns` into `n_regimes` market regimes by W2.

    Fail-closed: too little data returns {'ok': False, 'reason': ...}, never a
    fabricated clustering. The result carries each regime's distributional
    profile and a `claim` block making the status explicit (observation, not
    verdict; confidence is clustering SEPARATION, not a probability the regime is
    'real')."""
    r = [float(x) for x in returns]
    step = step or window
    if len(r) < window:
        return {"ok": False, "reason": f"need a return series of length >= window ({window}); got {len(r)}"}
    if n_regimes < 1:
        return {"ok": False, "reason": "n_regimes must be >= 1"}
    wins, i = [], 0
    while i + window <= len(r):
        wins.append((i, i + window))
        i += step
    if len(wins) < n_regimes:
        return {"ok": False, "reason": f"only {len(wins)} window(s) for {n_regimes} regimes; "
                                       f"reduce window/step or n_regimes"}
    Q = [quantile_profile(r[a:b], n_quantiles) for a, b in wins]
    labels, C, inertia = _kmeans(Q, n_regimes, seed)
    gmean = _mean_vec(Q)
    total = sum(_dist2(q, gmean) for q in Q) or 1.0
    separation = max(0.0, min(1.0, 1.0 - inertia / total))

    regimes = []
    for j in range(n_regimes):
        prof = C[j]
        m = len(prof)
        mean = sum(prof) / m
        var = sum((x - mean) ** 2 for x in prof) / m
        lo = prof[max(0, int(0.05 * (m - 1)))]
        hi = prof[min(m - 1, int(0.95 * (m - 1)))]
        regimes.append({
            "regime": j,
            "n_windows": labels.count(j),
            "mean_return": mean,
            "dispersion": math.sqrt(var),      # distributional spread ~ volatility
            "downside_q05": lo,
            "upside_q95": hi,
        })
    return {
        "ok": True,
        "method": "wasserstein2_kmeans_1d",
        "reference": "Horvath/Issa/Muguruza, SSRN 3947905",
        "n_regimes": int(n_regimes),
        "n_windows": len(wins),
        "window": window, "step": step, "n_quantiles": n_quantiles, "seed": seed,
        "separation": separation,
        "assignments": [{"window": i, "start": a, "end": b, "regime": labels[i]}
                        for i, (a, b) in enumerate(wins)],
        "regimes": regimes,
        "claim": {
            "kind": "unsupervised_observation",
            "confidence": separation,
            "contestable": True,
            "note": ("A regime label is a clustering RESULT, not a validated regime. Confidence is "
                     "the clustering separation, not a probability the regime is real. It reduces "
                     "uncertainty; it does not validate itself, and it opens no position."),
        },
    }
