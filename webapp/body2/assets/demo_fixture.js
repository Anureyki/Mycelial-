/* Offline preview fixture - used ONLY when the ATI "lighting" call cannot be reached.
   Generic tables only (organ-clock windows, house regions). No natal data, no pillars, no planets.
   Append ?demo=houses to the URL to light SAMPLE houses (invented positions, clearly labelled) for UI checks. */
window.DEMO_LIGHTING = function () {
  const W = window.MBODY.clock_windows, R = window.MBODY.layers.houses.regions, now = new Date(), m = now.getHours() * 60 + now.getMinutes();
  const mins = s => { const [h, mm] = s.split(":").map(Number); return h * 60 + mm; };
  const inWin = w => { const a = mins(w.start), b = mins(w.end); return a < b ? (m >= a && m < b) : (m >= a || m < b); };
  const act = W.find(inWin) || W[0];
  const L = {
    windows: W, house_regions: R, points: {points: {}, kinds: {}},
    active_window: {organ: act.organ, organ_display: act.organ_display, range: act.start + "–" + act.end, element: act.element, branch: act.branch, branch_display: act.branch_display},
    current_pillars: {year: {hanzi: "—"}, month: {hanzi: "—"}, day: {hanzi: "—"}, hour: {hanzi: "—"}},
    natal_pillars: null, natal_status: "absent - offline preview, ATI not reachable", interactions: [],
    houses_lit: [], natal_ghosts: [], houses_status: "unlit - offline preview (ATI not reachable)", _demo: true
  };
  if (/[?&]demo=houses/.test(location.search)) {
    L.houses_lit = [{house: 3, bodies: [{name: "sample", glyph: "☉"}, {name: "sample", glyph: "☽"}]}, {house: 8, bodies: [{name: "sample", glyph: "♂"}]}];
    L.natal_ghosts = [{house: 5, bodies: [{name: "sample", glyph: "♀"}]}];
    L.houses_status = "SAMPLE houses lit for a UI check - not real data";
  }
  return L;
};
