#!/usr/bin/env python3
"""Anansi's reply email: received, routed, recorded, where things stand - with a chart.

    from core.ack_email import render

2026-10-29, the principal: "Anansi needs to develop an email reply so that way I
know that it received and routed. And also the reply of record updates or
nutrient recommendations. If Anansi can create a charted report..."

PRESENTATION ONLY. Every fact here arrives from the department that owns it
(Grow's status_card for a plant); this lays it out and draws it. Nothing is
computed about the plant, and a fact a department did not give is shown as
absent, never filled in.
"""
import base64
import html as _h
import io
from datetime import datetime


def _chart_png(card):
    """ppm with the target and stage band, and pH with its band, last weeks."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    pts = card.get("series") or []
    pp = [(datetime.fromisoformat(p["at"]), p["ppm"]) for p in pts if p.get("ppm") is not None]
    ph = [(datetime.fromisoformat(p["at"]), p["ph"]) for p in pts if p.get("ph") is not None]
    if not pp and not ph:
        return None
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(6.4, 3.8), sharex=True,
                                 gridspec_kw={"height_ratios": [2, 1]})
    band, tgt = card.get("band_ppm"), card.get("target_ppm")
    if band:
        a1.axhspan(band[0], band[1], color="#2e7d32", alpha=0.08, label=f"{card.get('stage')} band")
    if tgt:
        a1.axhline(tgt, color="#2e7d32", lw=1, ls="--", label=f"target {tgt:.0f}")
    if pp:
        a1.plot([t for t, _ in pp], [v for _, v in pp], "o-", color="#1565c0", ms=3, lw=1.4, label="ppm")
    a1.set_ylabel("ppm")
    a1.legend(loc="upper left", fontsize=7, frameon=False)
    a1.set_title(f"{card.get('label')} - last readings", fontsize=10)
    pb = card.get("ph_band") or [5.8, 6.2]
    a2.axhspan(pb[0], pb[1], color="#2e7d32", alpha=0.08)
    if ph:
        a2.plot([t for t, _ in ph], [v for _, v in ph], "o-", color="#ef6c00", ms=3, lw=1.2)
    a2.set_ylabel("pH")
    a2.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    for ax in (a1, a2):
        ax.grid(alpha=0.25)
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return buf.getvalue()


def _fmt(v, nd=0):
    return "-" if v is None else (f"{v:.{nd}f}" if isinstance(v, (int, float)) else str(v))


def render(received_at, routed_to, recorded, cards, preview=False):
    """-> {"text", "html", "attachments"}. `recorded` is the department's own
    receipt (text); `cards` are Grow status cards for the plants concerned."""
    head = "PREVIEW - nothing was recorded" if preview else "Recorded"
    lines = [f"Received {received_at}. Routed to {routed_to or 'no department'}.", "",
             f"{head}:", recorded or "(no receipt returned)", ""]
    parts = [f"<p style='margin:0 0 8px'><b>Received</b> {_h.escape(received_at)} &middot; "
             f"<b>Routed to</b> {_h.escape(routed_to or 'no department')}</p>",
             f"<p style='margin:0 0 4px;color:{'#b26a00' if preview else '#2e7d32'}'><b>{head}</b></p>",
             f"<pre style='white-space:pre-wrap;font-family:inherit;margin:0 0 14px'>"
             f"{_h.escape(recorded or '(no receipt returned)')}</pre>"]
    atts = []
    for i, c in enumerate(cards or []):
        lt = c.get("latest") or {}

        def cell(k, nd=0):
            x = lt.get(k)
            return (f"{_fmt(x['value'], nd)} <span style='color:#888'>({x['at'][5:]})</span>"
                    if x else "-")
        rows = [("Stage", f"{c.get('stage') or '-'}" + (f", day {c['day']}" if c.get("day") else "")),
                ("Target / band", f"{_fmt(c.get('target_ppm'))} ppm / "
                                  f"{'-'.join(str(b) for b in c['band_ppm']) if c.get('band_ppm') else '-'}"),
                ("Latest ppm", cell("ppm")), ("Latest EC", cell("ec", 2)), ("Latest pH", cell("ph", 2)),
                ("Nutrient", (c.get("nutrient") or {}).get("message") or "-"),
                ("Water", (c.get("water") or {}).get("message") or "-")]
        def plain(k, nd=0):
            x = lt.get(k)
            return f"{_fmt(x['value'], nd)} ({x['at'][5:]})" if x else "-"
        lines += [f"== {c.get('label')} =="] + [
            f"{k}: {plain(k.split()[-1].lower(), 2 if k != 'Latest ppm' else 0) if k.startswith('Latest') else v}"
            for k, v in rows]
        parts.append(f"<h3 style='margin:14px 0 6px'>{_h.escape(c.get('label') or '')}</h3>"
                     "<table style='border-collapse:collapse;font-size:14px'>" +
                     "".join(f"<tr><td style='padding:3px 12px 3px 0;color:#555'>{_h.escape(k)}</td>"
                             f"<td style='padding:3px 0'>{v if '<span' in v else _h.escape(v)}</td></tr>"
                             for k, v in rows) + "</table>")
        sug = c.get("suggestion")
        if sug:
            amt = ", ".join(f"{k} {v:g} ml" for k, v in sug["add_ml"].items())
            lines += [f"Suggested: to reach {sug['to_ppm']:.0f} ppm at {sug['liters']:g} L, add {amt}."]
            parts.append(f"<p style='margin:8px 0;padding:8px;background:#f1f8e9'><b>Suggested</b> - to "
                         f"reach {sug['to_ppm']:.0f} ppm at {sug['liters']:g} L: {_h.escape(amt)}</p>")
        png = _chart_png(c)
        if png:
            cid = f"chart{i}"
            atts.append({"filename": f"{c.get('plant_id')}_chart.png", "content_type": "image/png",
                         "content_disposition": "inline", "content_id": cid,
                         "content": base64.b64encode(png).decode()})
            parts.append(f"<img src='cid:{cid}' alt='{_h.escape(c.get('label') or '')} chart' "
                         f"style='max-width:100%;margin-top:8px'>")
        lines.append("")
    lines.append("Reply with a correction if anything is wrong.")
    parts.append("<p style='color:#888;font-size:12px;margin-top:16px'>Reply with a correction if "
                 "anything is wrong. - Anansi, MycOS</p>")
    html = ("<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:640px'>"
            + "".join(parts) + "</div>")
    return {"text": "\n".join(lines), "html": html, "attachments": atts}
