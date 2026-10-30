#!/usr/bin/env python3
"""The grow journal as a page (and its weekly email) - presentation only.

    from core.journal_render import render_page

Every fact comes from Grow (journal + status_card per plant); this lays it out.
A value Grow did not give is absent here, never filled in.
"""
import html as _h
from datetime import datetime

KIND = {"reading": "Reading", "dose": "Feed", "refill": "Refill", "change": "Change-out",
        "level": "Level", "ph_adjust": "pH adjust", "stage": "Stage", "roots": "Roots",
        "training": "Training", "ripeness": "Ripeness", "lesson": "Lesson", "photo": "Photo"}


def _e(s):
    return _h.escape(str(s if s is not None else ""))


def _svg_chart(card, w=640, h=150, field="ppm", band=None, target=None, label=""):
    pts = [(datetime.fromisoformat(p["at"]), p[field]) for p in (card.get("series") or [])
           if p.get(field) is not None]
    if len(pts) < 2:
        return f"<p class='muted'>Not enough {label} readings to chart.</p>"
    t0, t1 = pts[0][0], pts[-1][0]
    span = max((t1 - t0).total_seconds(), 1)
    vals = [v for _, v in pts] + ([band[0], band[1]] if band else []) + ([target] if target else [])
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    L, R, T, B = 44, 10, 10, 22

    def X(t):
        return L + (w - L - R) * (t - t0).total_seconds() / span

    def Y(v):
        return T + (h - T - B) * (1 - (v - lo) / (hi - lo))
    out = [f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='{_e(card.get('label'))} {label}' class='chart'>"]
    if band:
        out.append(f"<rect x='{L}' y='{Y(band[1]):.1f}' width='{w - L - R}' height='{Y(band[0]) - Y(band[1]):.1f}' class='band'/>")
    if target:
        out.append(f"<line x1='{L}' x2='{w - R}' y1='{Y(target):.1f}' y2='{Y(target):.1f}' class='target'/>")
    for v in ([band[0], band[1]] if band else []) + ([target] if target else []):
        out.append(f"<text x='{L - 6}' y='{Y(v) + 4:.1f}' class='tick' text-anchor='end'>{v:g}</text>")
    path = " ".join(f"{X(t):.1f},{Y(v):.1f}" for t, v in pts)
    out.append(f"<polyline points='{path}' class='line {field}'/>")
    for t, v in pts:
        out.append(f"<circle cx='{X(t):.1f}' cy='{Y(v):.1f}' r='2.4' class='dot {field}'/>")
    for t in (t0, t1):
        out.append(f"<text x='{X(t):.1f}' y='{h - 6}' class='tick' text-anchor='{'start' if t == t0 else 'end'}'>"
                   f"{t:%m/%d}</text>")
    out.append("</svg>")
    return "".join(out)


def _plant_section(j, card):
    lt = (card or {}).get("latest") or {}

    def latest(k, nd=0, unit=""):
        x = lt.get(k)
        if not x:
            return "<span class='muted'>not taken</span>"
        v = x["value"] * 1000 if k == "ec" and x["value"] < 20 else x["value"]
        return f"<b>{v:.{nd}f}</b>{unit} <span class='muted'>{_e(x['at'][5:10])}</span>"
    head = (f"<section class='plant' id='{_e(j['plant_id'])}'>"
            f"<header class='phead'><h2>{_e(j['label'])}</h2>"
            f"<p class='sub'>{_e(card.get('stage') or j.get('stage') or '')}"
            + (f" · day {card['day']}" if card.get("day") else "")
            + (f" · target {card['target_ppm']:g} ppm" if card.get("target_ppm") else "") + "</p></header>"
            "<div class='stats'>"
            f"<div><span class='lbl'>ppm</span>{latest('ppm')}</div>"
            f"<div><span class='lbl'>EC</span>{latest('ec')}</div>"
            f"<div><span class='lbl'>pH</span>{latest('ph', 2)}</div></div>"
            f"<p class='proj'><span class='lbl'>Nutrient</span> {_e((card.get('nutrient') or {}).get('message') or '-')}</p>"
            f"<p class='proj'><span class='lbl'>Water</span> {_e((card.get('water') or {}).get('message') or '-')}</p>"
            + (f"<p class='sug'><span class='lbl'>Suggested</span> to {card['suggestion']['to_ppm']:g} ppm: "
               + _e(", ".join(f"{k} {v:g} ml" for k, v in card['suggestion']['add_ml'].items())) + "</p>"
               if card.get("suggestion") else "")
            + "<figure><figcaption>ppm against the stage band (shaded) and target (dashed)</figcaption>"
            + _svg_chart(card, field="ppm", band=card.get("band_ppm"), target=card.get("target_ppm"), label="ppm")
            + "</figure><figure><figcaption>pH against the 5.8–6.2 window</figcaption>"
            + _svg_chart(card, h=100, field="ph", band=card.get("ph_band") or [5.8, 6.2], label="pH")
            + "</figure>")
    days = []
    for d in reversed(j.get("journal") or []):
        if not d["entries"] and not d.get("light"):
            continue
        dt = datetime.fromisoformat(d["date"])
        lt_ = d.get("light")
        chip = (f"<span class='chip'>{lt_['hours_lit']:g} h light · DLI {lt_['dli']:g}"
                + (" (partial)" if lt_.get("partial") else "") + "</span>") if lt_ else ""
        items = "".join(f"<li><time>{_e(e['time'])}</time><span class='k k-{_e(e['kind'])}'>"
                        f"{_e(KIND.get(e['kind'], e['kind']))}</span><span class='t'>{_e(e['text'])}</span></li>"
                        for e in d["entries"]) or "<li class='muted'>No entries - light only.</li>"
        days.append(f"<article class='day'><h3>{dt:%a %b %d}"
                    + (f" <span class='pd'>day {d['plant_day']}</span>" if d.get("plant_day") is not None else "")
                    + f"</h3>{chip}<ul>{items}</ul></article>")
    return head + "<div class='days'>" + "".join(days) + "</div></section>"


def render_page(plants, generated_at=None):
    """plants: list of (journal, status_card) from Grow."""
    gen = generated_at or datetime.now().strftime("%b %d, %Y %H:%M")
    nav = "".join(f"<a href='#{_e(j['plant_id'])}'>{_e(j['label'])}</a>" for j, _ in plants)
    body = "".join(_plant_section(j, c or {}) for j, c in plants)
    return f"""<title>Grow Journal</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=IBM+Plex+Sans:wght@400;500&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: a single-column grow notebook - each plant a section: status strip, charts, then days newest first. */
:root {{
  --bg: #f4f6f3; --surface: #ffffff; --fg: #1d2620; --muted: #66726a; --rule: #d9e0da;
  --accent: #2f7d4a; --band: rgba(47,125,74,.10); --ph: #b5651d; --chip: #e6efe8;
  --display: "Bricolage Grotesque", "Segoe UI", system-ui, sans-serif;
  --body: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, Menlo, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #121714; --surface: #1a211c; --fg: #e3ebe5; --muted: #93a197; --rule: #2b352e;
  --accent: #6cc58a; --band: rgba(108,197,138,.12); --ph: #e3a35f; --chip: #223128; color-scheme: dark }} }}
:root[data-theme="dark"] {{
  --bg: #121714; --surface: #1a211c; --fg: #e3ebe5; --muted: #93a197; --rule: #2b352e;
  --accent: #6cc58a; --band: rgba(108,197,138,.12); --ph: #e3a35f; --chip: #223128; color-scheme: dark }}
body {{ background: var(--bg); color: var(--fg); font: 15px/1.55 var(--body); }}
.wrap {{ max-width: 760px; margin: 0 auto; padding-block: 24px 48px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }}
h1, h2, h3 {{ font-family: var(--display); text-wrap: balance; margin: 0; }}
h1 {{ font-size: 1.9rem; letter-spacing: -.01em; }}
.top p {{ color: var(--muted); margin: 4px 0 0; }}
nav {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 16px 0 8px; }}
nav a {{ color: var(--accent); text-decoration: none; border: 1px solid var(--rule); padding: 4px 12px; border-radius: 999px; font-weight: 500; }}
nav a:focus-visible, nav a:hover {{ border-color: var(--accent); outline: none; }}
.plant {{ margin-top: 28px; display: grid; gap: 12px; }}
.phead {{ display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; border-bottom: 2px solid var(--accent); padding-bottom: 6px; }}
.phead h2 {{ font-size: 1.5rem; }}
.sub {{ color: var(--muted); margin: 0; }}
.stats {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }}
.stats div {{ background: var(--surface); border: 1px solid var(--rule); border-radius: 8px; padding: 8px 10px; font-family: var(--mono); font-variant-numeric: tabular-nums; min-width: 0; }}
.stats b {{ font-size: 1.25rem; font-weight: 500; }}
.lbl {{ display: block; font: 500 .7rem/1.4 var(--body); text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }}
.proj, .sug {{ margin: 0; }}
.proj .lbl, .sug .lbl {{ display: inline; margin-right: 6px; }}
.sug {{ background: var(--chip); padding: 8px 10px; border-radius: 8px; }}
figure {{ margin: 4px 0 0; }}
figcaption {{ font-size: .78rem; color: var(--muted); }}
.chart {{ width: 100%; height: auto; display: block; }}
.chart .band {{ fill: var(--band); }}
.chart .target {{ stroke: var(--accent); stroke-dasharray: 4 4; stroke-width: 1; }}
.chart .line {{ fill: none; stroke-width: 1.6; }}
.chart .line.ppm {{ stroke: var(--accent); }} .chart .dot.ppm {{ fill: var(--accent); }}
.chart .line.ph {{ stroke: var(--ph); }} .chart .dot.ph {{ fill: var(--ph); }}
.chart .tick {{ fill: var(--muted); font: 10px var(--mono); }}
.days {{ display: grid; gap: 10px; margin-top: 8px; }}
.day {{ background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; padding: 10px 12px; min-width: 0; }}
.day h3 {{ font-size: 1.02rem; }}
.pd {{ font: 400 .85rem var(--body); color: var(--muted); margin-left: 6px; }}
.chip {{ display: inline-block; font: .75rem var(--mono); background: var(--chip); padding: 2px 8px; border-radius: 999px; margin-top: 4px; }}
.day ul {{ list-style: none; margin: 8px 0 0; padding: 0; display: grid; gap: 6px; }}
.day li {{ display: grid; grid-template-columns: 3.2rem 5.6rem minmax(0, 1fr); gap: 8px; align-items: baseline; }}
.day time {{ font: .82rem var(--mono); color: var(--muted); font-variant-numeric: tabular-nums; }}
.k {{ font: 500 .7rem/1.6 var(--body); text-transform: uppercase; letter-spacing: .06em; color: var(--accent); }}
.k-ph_adjust {{ color: var(--ph); }}
.t {{ min-width: 0; overflow-wrap: anywhere; }}
.muted {{ color: var(--muted); }}
footer {{ margin-top: 36px; color: var(--muted); font-size: .8rem; }}
@media (max-width: 480px) {{ .day li {{ grid-template-columns: 3rem minmax(0, 1fr); }} .day li .k {{ grid-column: 2; }} .day li .t {{ grid-column: 1 / -1; }} }}
</style>
<div class="wrap">
<div class="top"><h1>Grow Journal</h1><p>Every reading, feed, refill, pH change and check Grow holds, newest day first. Updated {_e(gen)}.</p></div>
<nav>{nav}</nav>
{body}
<footer>Built by Grow from its own records; nothing on this page was estimated. A value not measured shows as not taken.</footer>
</div>
"""
