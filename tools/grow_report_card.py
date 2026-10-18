#!/usr/bin/env python3
"""A visual report of one plant's RECORDED readings, for the media library.

    python3 tools/grow_report_card.py [plant_id] [out.png]

Reads Grow's store (read-only - nothing is written to any record). Measured
values and Grow's own assessments are drawn in separate, labelled panels; no
number is computed here except the min/max/latest of what was measured.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def grow(task, args):
    r = json.loads(urllib.request.urlopen(urllib.request.Request(
        "http://localhost:9009/execute", data=json.dumps({"task": task, "args": args}).encode(),
        headers={"Content-Type": "application/json"}), timeout=120).read())
    while isinstance(r, dict) and "result" in r and isinstance(r["result"], dict) and len(r) <= 2:
        r = r["result"]
    return r.get("result", r) if isinstance(r, dict) else r


def main():
    plant = sys.argv[1] if len(sys.argv) > 1 else "current_plant"
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        ROOT, "state", "artifacts", f"grow_report_{plant}_{datetime.now():%Y%m%d_%H%M}.png")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from agents.grow_agent.grow_agent import GrowAgent
    g = object.__new__(GrowAgent)
    g.agent_id = "grow_agent"
    g.log = lambda *a, **k: None
    rows = [r for r in g._get_readings_for_plant(plant) if not r.get("voided")]
    pts = [(datetime.fromisoformat(str(r["timestamp"])[:19]), r.get("ppm"), r.get("ph")) for r in rows
           if r.get("timestamp")]
    since = datetime.fromisoformat("2026-10-04T00:00:00")      # this reservoir, since its change-out
    pts = [p for p in pts if p[0] >= since]
    ppm = [(t, v) for t, v, _ in pts if v is not None]
    ph = [(t, v) for t, _, v in pts if v is not None]
    rip = grow("assess_ripeness", {"plant_id": plant})
    label = {"current_plant": "GSC1", "gsc_auto_2": "GSC2"}.get(plant, plant)
    fig = plt.figure(figsize=(16, 9), dpi=120, facecolor="#0f1412")
    fig.suptitle(f"{label} - reservoir readings since the 2026-10-04 change-out", color="#e8efe9",
                 fontsize=22, x=0.05, ha="left", y=0.965)
    fig.text(0.05, 0.915, f"Generated {datetime.now():%Y-%m-%d %H:%M} by Anansi from Grow Agent records",
             color="#9fb3a6", fontsize=12)
    for i, (series, name, colour, band) in enumerate(((ppm, "ppm (TDS, 0.5 scale)", "#7bd389", (800, 1200)),
                                                      (ph, "pH", "#6fb7ff", (5.8, 6.2)))):
        ax = fig.add_axes([0.06, 0.52 - i * 0.37, 0.58, 0.32], facecolor="#151c19")
        if series:
            ax.plot([t for t, _ in series], [v for _, v in series], "o-", color=colour, lw=2, ms=5)
        ax.axhspan(*band, color=colour, alpha=0.08)
        ax.set_ylabel(name, color="#cfe0d4", fontsize=12)
        ax.tick_params(colors="#9fb3a6", labelsize=10)
        for sp in ax.spines.values():
            sp.set_color("#2a3530")
        ax.grid(color="#22302a", lw=0.6)
    def stat(series):
        if not series:
            return "no readings"
        v = [x for _, x in series]
        return f"latest {v[-1]:g} ({series[-1][0]:%m/%d %H:%M}) - range {min(v):g} to {max(v):g} - {len(v)} readings"
    fig.text(0.68, 0.84, "MEASURED  (recorded readings, unchanged)", color="#7bd389", fontsize=13, weight="bold")
    fig.text(0.68, 0.78, f"ppm: {stat(ppm)}", color="#e8efe9", fontsize=10.5, wrap=True)
    fig.text(0.68, 0.74, f"pH:  {stat(ph)}", color="#e8efe9", fontsize=10.5)
    fig.text(0.68, 0.70, "Shaded bands: Grow's flower band (800-1200 ppm, pH 5.8-6.2)", color="#9fb3a6", fontsize=9.5)
    fig.text(0.68, 0.58, "GROW AGENT ASSESSMENT  (interpretation)", color="#f0c674", fontsize=13, weight="bold")
    fig.text(0.68, 0.53, f"Ripeness: {rip.get('classification')} (confidence {rip.get('confidence')})",
             color="#e8efe9", fontsize=10.5)
    fig.text(0.68, 0.49, str(rip.get("reason") or "")[:70], color="#e8efe9", fontsize=10.5)
    fig.text(0.68, 0.45, f"Next: {str(rip.get('action') or '')[:64]}", color="#e8efe9", fontsize=10.5)
    fig.text(0.68, 0.12, "Interpretation is the agent's, from the readings and the grower's\n"
             "observations. Measurements are not altered or smoothed.", color="#7d8c83", fontsize=9)
    os.makedirs(os.path.dirname(out), mode=0o700, exist_ok=True)
    fig.savefig(out, facecolor=fig.get_facecolor())
    os.chmod(out, 0o600)
    print(json.dumps({"out": out, "ppm_points": len(ppm), "ph_points": len(ph)}))


if __name__ == "__main__":
    main()
