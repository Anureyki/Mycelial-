"use strict";
/* Body Interface v2 - prototype replacement for webapp/body.html.
   Kept from body.html: ask()/unwrap()/brief() (POST /execute, tasks "lighting" and "sector_briefing"), the 60 s refresh,
   drawClock / drawPillars / lightHouses / markMeridians, window.POINT_KINDS, the toggle ids (t-clock, t-houses, t-pillars,
   t-meridians, t-active), #clock #pillars #tags #brief #housenote, .glow[data-glow] .hit[data-house] #houses #glyphs #figmask,
   and #mer-<organ> ids on each meridian layer.  Nothing natal lives in this file: every value arrives from ATI's lighting call. */
const NS = "http://www.w3.org/2000/svg", TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;
const D = window.MBODY;
const $ = id => document.getElementById(id);
const unwrap = r => { while (r && typeof r === "object" && "result" in r && typeof r.result === "object") r = r.result; return r; };
function el(tag, attrs, parent) { const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v); if (parent) parent.appendChild(e); return e; }
async function ask(task, args) {
  if (location.protocol === "file:") throw new Error("opened from file:// - ATI is only reachable through the MycOS webapp");
  const r = await fetch("/execute", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({task, args: Object.assign({tz: TZ}, args || {})}), credentials: "same-origin"});
  if (!r.ok) throw new Error(`${r.status}`); return unwrap(await r.json());
}
async function brief(kind, id) {
  $("brief").textContent = "Asking ATI…";
  try { const b = await ask("sector_briefing", {kind, id}); $("brief").textContent = b.text || b.error; }
  catch (e) { $("brief").textContent = `Briefing unavailable (${e.message}).`; }
}
const EL = {lung:"metal", large_intestine:"metal", stomach:"earth", spleen:"earth", heart:"fire", small_intestine:"fire",
  bladder:"water", kidney:"water", pericardium:"fire", triple_burner:"fire", gallbladder:"wood", liver:"wood", ren:"extra", du:"extra"};
const MCOL = Object.fromEntries(Object.keys(D.meridians).map(k => [k, `var(--${EL[k]})`]));
const MCOL2 = {ren:"#6fd0c8", du:"#c9a0ff"}; // Ren / Du get their own hues so they read apart from the twelve
Object.assign(MCOL, MCOL2);
const ORD = ["1ST","2ND","3RD","4TH","5TH","6TH","7TH","8TH","9TH","10TH","11TH","12TH"];
const HUES = D.layers.hues;
const VIEWS = D.figure.views;
const PT = Object.fromEntries(D.points.map(p => [p.code, p]));
if (D.foot) { D.footm = D.foot.medial; D.footl = D.foot.lateral; }       // medial / lateral foot + ankle templates live inside foot.json
const EXTRAS = [...(D.hand.extras || []).map(x => Object.assign({frame: "hand"}, x)),
  ...((D.foot && D.foot.extras) || []).flatMap(x => { const o = []; if (x.front || x.back) o.push(Object.assign({frame: "foot"}, x)); if (x.medial) o.push(Object.assign({frame: "footm"}, x)); if (x.lateral) o.push(Object.assign({frame: "footl"}, x)); return o; })];
const BYMER = {}; D.points.forEach(p => (BYMER[p.meridian] = BYMER[p.meridian] || []).push(p)); Object.values(BYMER).forEach(a => a.sort((x, y) => x.number - y.number));
const strip = s => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const KIND_CLASS = {"Y": "kY", "L": "kL", "H": "kH", "Y·H": "kYH"};
window.POINT_KINDS = {};
const kindOf = code => window.POINT_KINDS[code] || (PT[code] && PT[code].repo_kind) || null;

/* ---------------------------------------------------------------- state */
const S = {view: "front", cx: 450, cy: 800, z: 1, sel: null, hov: null, off: new Set(), solo: null,
  layer: {bones: false, extra: true, houses: true, meridians: true, points: true, labels: true, organs: false, palm: false, land: true, both: true}, LAST: null, selHouse: null};
const BASE = {x0: -250, x1: 1150, y0: 60, y1: 1580};   // world box that "z = 1" fits (gutters hold the house badges and glyphs)
let VW = 800, VH = 800, UPP = 1;

/* ---------------------------------------------------------------- geometry helpers */
const hands = v => (VIEWS[v] || {}).hands || [];
/* Frames: "body" (canvas px), "hand" and "foot" (centimetres, see data/hand.json, data/foot.json).  A frame is placed by a 2x3 matrix
   that depends on the view: on the body views the template is glued to the figure's wrist / ankle anchor; on the detail views
   (Palm, Back of hand, Foot top, Sole ...) it sits at a fixed place at 40 px per cm.  Side "A" is the body's left (body views)
   / the right hand or foot at screen-left (detail views); side "B" is the mirror. */
const K = 40;
const DETAIL = {
  palm:   {frame: "hand", src: "front", surface: "palm",   label: "Palm",          sx: 1,  box: {x0: -440, x1: 1250, y0: -830, y1: 420}, B: 800},
  dorsum: {frame: "hand", src: "back",  surface: "dorsum", label: "Back of hand",  sx: -1, box: {x0: -440, x1: 1250, y0: -830, y1: 420}, B: 800},
  foot_top:  {frame: "foot", src: "front", surface: "dorsal",  label: "Foot top",  sx: 1,  box: {x0: -1090, x1: 330, y0: -800, y1: 340}, B: -760},
  foot_sole: {frame: "foot", src: "back",  surface: "plantar", label: "Sole",      sx: -1, box: {x0: -330, x1: 1090, y0: -800, y1: 340}, B: 760},
  foot_medial:  {frame: "footm", src: "medial",  surface: "medial",  label: "Foot medial",  sx: 1, box: {x0: -780, x1: 1500, y0: -520, y1: 400}, B: 720},
  foot_lateral: {frame: "footl", src: "lateral", surface: "lateral", label: "Foot lateral", sx: 1, box: {x0: -340, x1: 2040, y0: -520, y1: 400}, B: 1700}};
const isDetail = v => !!DETAIL[v];
const placedIn = (q, v) => { if (isDetail(v)) { const p = q.place[DETAIL[v].src]; return !!p && p.frame === DETAIL[v].frame && !(p.proj && DETAIL[v].frame === "foot"); } return !!q.place[v]; };   // p.proj = border point projected onto the dorsal/plantar silhouette: body views only, its real place is the medial / lateral view
const srcOf = v => isDetail(v) ? DETAIL[v].src : v;
const anchorOf = (view, frame) => ((VIEWS[view] || {})[frame === "hand" ? "hands" : "feet"] || [])[0];
function frameMat(view, frame, side) {
  const dt = DETAIL[view];
  if (dt) { if (dt.frame !== frame) return null; const sx = side === "B" ? -dt.sx : dt.sx; return [sx * K, 0, 0, K, side === "B" ? dt.B : 0, 0]; }
  const h = anchorOf(view, frame); if (!h) return null; const T = D[frame];
  const s = h.scale_px_per_cm || h.forearm_width_px / T.wrist_width_cm, th = Math.atan2(h.axis[0], -h.axis[1]), c = Math.cos(th), sn = Math.sin(th), sx = frame === "hand" ? (view === "back" ? -s : s) : (view === "back" ? s : -s);   // hands: template = right palm; feet: template = right foot seen from above, side A = the body's LEFT foot
  let m = [c * sx, sn * sx, -sn * s, c * s, (h.wrist || h.ankle)[0], (h.wrist || h.ankle)[1]];
  if (side === "B") m = [-m[0], m[1], -m[2], m[3], 2 * VIEWS[view].mid_x - m[4], m[5]];
  return m;
}
const matStr = m => `matrix(${m.map(v => +v.toFixed(5)).join(" ")})`;
function frameXY(view, frame, side, x, y) { const m = frameMat(view, frame, side); return m ? [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]] : null; }
const frameTransform = (view, frame, side) => { const m = frameMat(view, frame, side); return m ? matStr(m) : null; };
const handXY = (view, hx, hy) => frameXY(view, "hand", "A", hx, hy);
const handTransform = (view, side) => frameTransform(view, "hand", side);
function placeXY(q, view, side) {
  const p = q.place[srcOf(view)]; if (!p) return null;
  if (p.proj && isDetail(view) && DETAIL[view].frame === "foot") return null;   // projected border point: body views only
  let xy;
  if (p.frame === "body") { if (isDetail(view)) return null; xy = [p.x, p.y]; if (side === "B") xy = [2 * VIEWS[view].mid_x - xy[0], xy[1]]; return xy; }
  return frameXY(view, p.frame, side, p.x, p.y);
}
const isMidline = (q, view) => { const p = q.place[srcOf(view)]; return !isDetail(view) && p && p.frame === "body" && Math.abs(p.x - VIEWS[view].mid_x) < 6; };
const sidesFor = (q, view) => (view === "side" || isMidline(q, view) || !S.layer.both) ? ["A"] : ["A", "B"];
const sideName = (q, view, side) => isDetail(view) ? (side === "A" ? "right" : "left") + (DETAIL[view].frame === "hand" ? " hand" : " foot") : view === "side" ? "side view (bilateral point, shown once)" : isMidline(q, view) ? "midline" : side === "A" ? "left side of body" : "right side of body";
function smooth(pts) {
  if (pts.length < 2) return "";
  const p = [pts[0], ...pts, pts[pts.length - 1]]; let d = `M${p[1][0].toFixed(1)} ${p[1][1].toFixed(1)}`;
  for (let i = 1; i < p.length - 2; i++) { const [a, b, c, e] = [p[i - 1], p[i], p[i + 1], p[i + 2]];
    d += ` C${(b[0] + (c[0] - a[0]) / 6).toFixed(1)} ${(b[1] + (c[1] - a[1]) / 6).toFixed(1)} ${(c[0] - (e[0] - b[0]) / 6).toFixed(1)} ${(c[1] - (e[1] - b[1]) / 6).toFixed(1)} ${c[0].toFixed(1)} ${c[1].toFixed(1)}`; }
  return d;
}

/* ---------------------------------------------------------------- scene */
let LABELS = [], GROUPS = {};
function renderScene() {
  const svg = $("viewer"); svg.innerHTML = ""; LABELS = []; GROUPS = {};
  const view = S.view, dt = DETAIL[view], V = VIEWS[view] || {}, mid = V.mid_x, H = D.hand, F = D.foot;
  const defs = el("defs", {}, svg); ART.defs(svg);
  const sh = el("linearGradient", {id: "sheen", x1: 0, y1: 0, x2: 1, y2: 0}, defs);
  [["0", "rgba(255,255,255,.00)"], ["0.5", "rgba(160,190,255,.07)"], ["1", "rgba(255,255,255,.00)"]].forEach(([o, c]) => el("stop", {offset: o, "stop-color": c}, sh));
  const og = el("filter", {id: "oglow", x: "-80%", y: "-80%", width: "260%", height: "260%"}, defs); el("feGaussianBlur", {stdDeviation: 4}, og);
  const rim = el("filter", {id: "rim", x: "-5%", y: "-5%", width: "110%", height: "110%"}, defs);
  el("feGaussianBlur", {stdDeviation: 6, result: "b"}, rim); const fm = el("feMerge", {}, rim); el("feMergeNode", {in: "b"}, fm); el("feMergeNode", {in: "SourceGraphic"}, fm);
  // forearm / shin stubs fade out on the detail views (local cm frames)
  [["hand", 3.0, 7.0], ["foot", 3.0, 9.0], ["footm", -5.0, -11.5], ["footl", -5.0, -11.5]].forEach(([fr, y1, y2]) => { const gg = el("linearGradient", {id: "fadeG-" + fr, gradientUnits: "userSpaceOnUse", x1: 0, y1, x2: 0, y2}, defs);
    el("stop", {offset: 0, "stop-color": "#fff"}, gg); el("stop", {offset: 1, "stop-color": "#000"}, gg);
    const m = el("mask", {id: "fade-" + fr, maskUnits: "userSpaceOnUse", x: -40, y: -60, width: 80, height: 120}, defs); el("rect", {x: -40, y: -60, width: 80, height: 120, fill: `url(#fadeG-${fr})`}, m); });
  const cutOf = (frame, T) => frame === "foot" && view === "back" ? T.skin_cut_plantar : T.skin_cut;
  const anch = {hand: !dt && !!anchorOf(view, "hand"), foot: !dt && !!(F && anchorOf(view, "foot"))};
  const templates = [];                                  // body views: [{frame, T, t}]
  if (!dt) ["hand", "foot"].forEach(fr => { if (anch[fr]) ["A", "B"].forEach(sd => templates.push({frame: fr, T: D[fr], t: frameTransform(view, fr, sd), sd})); });
  // mask for the house glow: the figure's silhouette (+ hands, feet), id kept from body.html
  const mk = el("mask", {id: "figmask", maskUnits: "userSpaceOnUse", x: -300, y: 0, width: 1500, height: 1650}, defs);
  if (!dt) { el("path", {d: V.silhouette, fill: "#fff"}, mk); templates.forEach(({frame, T, t}) => { const g = el("g", {transform: t}, mk); el("path", {d: cutOf(frame, T), fill: "#fff"}, g); }); }
  const root = el("g", {id: "scene"}, svg);
  if (dt) {
    const T = D[dt.frame];
    ["A", "B"].forEach(sd => { if (sd === "B" && !S.layer.both) return;
      const g = el("g", {transform: frameTransform(view, dt.frame, sd), mask: `url(#fade-${dt.frame})`, class: "detailart"}, root);
      ART[dt.frame + "Outline"](g, T, false, dt.surface); ART[dt.frame + "Fill"](g, T, false, dt.surface); ART[dt.frame + "Detail"](g, T, dt.surface, {bones: S.layer.bones}); });
    GROUPS.houses = el("g", {id: "houses", style: "display:none"}, root);
  } else {
    // figure: outline pass, then fill pass (so hands/feet and body share one clean contour)
    const fg = el("g", {id: "figure", filter: "url(#rim)"}, root);
    el("path", {d: V.silhouette, class: "bodyline"}, fg);
    templates.forEach(({frame, T, t}) => { const g = el("g", {transform: t}, fg); el("path", {d: cutOf(frame, T), class: "bodyline"}, g); });
    el("path", {d: V.silhouette, class: "bodyfill"}, root);
    templates.forEach(({frame, T, t}) => { const g = el("g", {transform: t}, root); el("path", {d: cutOf(frame, T), class: "bodyfill"}, g); });
    el("path", {d: V.silhouette, class: "sheen"}, root);
    // houses (not on the face close-up)
    const hb = el("g", {id: "houses", style: S.layer.houses && view !== "face" ? "" : "display:none"}, root); GROUPS.houses = hb;
    if (view !== "face") buildHouses(hb);
    el("path", {d: V.ink, class: "ink"}, root);
    // hand / foot surface detail (palm side on the front view, back of hand / heel side on the back view)
    templates.forEach(({frame, T, t}) => { const g = el("g", {transform: t}, root);
      const surf = frame === "hand" ? (view === "back" ? "dorsum" : "palm") : (view === "back" ? "plantar" : "dorsal");
      ART[frame + "Detail"](g, T, surf, {cut: true, bones: S.layer.bones}); });
    // landmarks derived from the data (nipple = ST17, umbilicus = CV8)
    const lm = el("g", {id: "landmarks", style: S.layer.land ? "" : "display:none"}, root); GROUPS.land = lm;
    if (view === "front") [["ST17", "nipple"], ["CV8", "umbilicus"]].forEach(([c, n]) => { const q = PT[c]; sidesFor(q, view).forEach(sd => { const xy = placeXY(q, view, sd); if (!xy) return;
        el("circle", {cx: xy[0], cy: xy[1], r: n === "nipple" ? 9 : 7, class: "landmark"}, lm); }); });
    // organs
    const og2 = el("g", {id: "organs", style: S.layer.organs && (view === "front" || view === "back") ? "" : "display:none"}, root); GROUPS.organs = og2;
    if (view === "front" || view === "back") buildOrgans(og2);
  }
  // palmistry (palm side = front view or the Palm detail view)
  const palmView = view === "front" || view === "palm";
  const pg = el("g", {id: "palmistry", style: S.layer.palm && palmView ? "" : "display:none"}, root); GROUPS.palm = pg;
  if (palmView) buildPalmistry(pg);
  // meridians
  const mg = el("g", {id: "meridians", style: S.layer.meridians ? "" : "display:none", "aria-label": "Meridians - reference only, placements approximate, not for clinical use"}, root); GROUPS.mer = mg;
  buildMeridians(mg, view);
  // points
  const ptg = el("g", {id: "points", style: S.layer.points ? "" : "display:none"}, root); GROUPS.pts = ptg;
  const lg = el("g", {id: "labels", style: S.layer.labels ? "" : "display:none"}, root); GROUPS.labels = lg;
  buildPoints(ptg, lg, view);
  GROUPS.glyphs = el("g", {id: "glyphs", style: S.layer.houses && view !== "face" && !dt ? "" : "display:none"}, root);
  if (S.sel) markSelected();
  if (S.LAST) { lightHouses(S.LAST); markMeridians(S.LAST); }
  applyMerFilter();
  updateView();
}
function houseBands() { const L = D.layers.houses, h = (L.bottom - L.top) / 12; return Array.from({length: 13}, (_, i) => L.top + i * h); }
function buildHouses(g) {
  const b = houseBands(), reg = D.layers.houses.regions;
  for (let i = 0; i < 12; i++) {
    const y = b[i], h = b[i + 1] - y, hr = i === 11 ? Math.max(b[12], 1580) - y : h;   // band 12 reaches down over the toes (they extend past the old figure bottom)
    el("rect", {class: "band", x: -300, y, width: 1500, height: hr, fill: HUES[i], mask: "url(#figmask)"}, g);
    el("rect", {class: "glow", x: -300, y, width: 1500, height: hr, mask: "url(#figmask)", "data-glow": i + 1}, g);
    el("line", {class: "bandline", x1: -240, x2: 1140, y1: y, y2: y}, g);
    const r = el("rect", {class: "hit", x: -250, y, width: 1400, height: hr, tabindex: 0, "data-house": i + 1, "aria-label": `House ${i + 1}: ${reg[i + 1]}`}, g);
    r.addEventListener("click", () => selectHouse(i + 1));
    r.addEventListener("keydown", e => { if (e.key === "Enter") selectHouse(i + 1); });
    const cy = y + h / 2; const gg = el("g", {class: "hgutter"}, g);
    el("circle", {class: "hnum", cx: -215, cy, r: 24, stroke: HUES[i]}, gg);
    el("text", {class: "hnumt", x: -215, y: cy + 1, fill: HUES[i]}, gg).textContent = i + 1;
    const t1 = el("text", {class: "htitle", x: -180, y: cy - 7, fill: HUES[i]}, gg); t1.textContent = ORD[i] + " HOUSE";
    const words = reg[i + 1].toUpperCase().split(" "), lines = []; let cur = "";
    words.forEach(w => { if ((cur + " " + w).trim().length > 18) { lines.push(cur); cur = w; } else cur = (cur + " " + w).trim(); }); lines.push(cur);
    lines.forEach((ln, k) => el("text", {class: "hregion", x: -180, y: cy + 14 + k * 17}, gg).textContent = ln);
  }
}
function buildOrgans(g) {
  const kidneyOnly = S.view === "front";
  for (const [key, o] of Object.entries(D.layers.organs)) {
    const col = MCOL[o.organ === "pericardium" ? "pericardium" : o.organ], back = o.organ === "kidney";
    const show = S.view === "front" ? !back : true;   // front: kidneys are posterior - drawn dimmed, ringed
    if (o.organ === "pericardium") { el("circle", {cx: o.x, cy: o.y, r: o.r, fill: "none", stroke: col, "stroke-width": 1.5, opacity: .7, class: "organ"}, g); continue; }
    const dim = (S.view === "front" && back) || (S.view === "back" && !back);
    const x = S.view === "back" ? 2 * VIEWS.back.mid_x - o.x : o.x;
    el("circle", {cx: x, cy: o.y, r: o.r * 1.7, fill: col, opacity: dim ? .08 : .26, filter: "url(#oglow)"}, g);
    const c = el("circle", {cx: x, cy: o.y, r: o.r, fill: col, opacity: dim ? .35 : .9, stroke: dim ? col : "none", "stroke-dasharray": dim ? "3 3" : "none", class: "organ"}, g);
    el("title", {}, c).textContent = key.replace(/_[lr]$/, "").replace(/_/g, " ") + (dim ? " (posterior - dimmed)" : "") + " · approximate position";
  }
}
function buildPalmistry(g) {
  const H = D.hand, P = H.palmistry, view = S.view;
  ["A", "B"].forEach(sd => {
    if (sd === "B" && isDetail(view) && !S.layer.both) return;
    const t = frameTransform(view, "hand", sd); if (!t) return; const hg = el("g", {transform: t}, g);
    P.mounts.forEach(m => { const e = el("ellipse", {cx: m.cx, cy: m.cy, rx: m.rx, ry: m.ry, transform: `rotate(${m.rot} ${m.cx} ${m.cy})`, class: "pl-mount", "data-pal": "mount:" + m.id}, hg);
      e.dataset.tip = `<b>${m.name}</b><br><span class="m">Palmistry · traditional reading, not science</span><br>${m.note}`; });
    P.lines.forEach(l => { el("path", {d: l.path, class: "pl-line-hit"}, hg).dataset.tip = `<b>${l.name}</b><br><span class="m">Palmistry · traditional reading, not science</span><br>${l.note}`;
      el("path", {d: l.path, class: "pl-line", stroke: l.color, style: "pointer-events:none"}, hg); });
    // labels live in canvas space (so text is never mirrored) and take part in collision culling
    const toW = (x, y) => frameXY(view, "hand", sd, x, y);
    P.mounts.forEach(m => { const [X, Y] = toW(m.cx, m.cy); addLabel(g, X, Y, m.name.replace("Mount of ", "").replace(" (upper / positive)", " ↑").replace(" (lower / negative)", " ↓"), {cls: "pal-label", prio: 5, minZ: 5, center: true}); });
    P.lines.forEach(l => { const pts = l.path.match(/-?\d+\.?\d*/g).map(Number); const i = Math.floor(pts.length / 6) * 2;
      const [X, Y] = toW(pts[Math.min(i, pts.length - 2)], pts[Math.min(i + 1, pts.length - 1)]); addLabel(g, X, Y, l.name.split(" (")[0].replace(" line", ""), {cls: "pal-label", prio: 4, minZ: 5.5}); });
    P.fingers.forEach(f => { const [X, Y] = toW(f.at[0], f.at[1]); addLabel(g, X, Y, f.label, {cls: "pal-label", prio: 6, minZ: 4.5, center: true}); });
  });
}
const FOOT_LABEL_Z = 6;      // zoom at which foot/ankle labels appear on the whole-body front/back views
function addLabel(g, x, y, text, o) {
  const t = el("text", {class: o.cls || "plabel", "text-anchor": o.center ? "middle" : "start", style: "display:none"}, g); t.textContent = text;
  LABELS.push({e: t, x, y, text, prio: o.prio, minZ: o.minZ, center: !!o.center, w: text.length * 6.4 + 6, code: o.code});
  return t;
}
function buildMeridians(g, view) {
  for (const [mk, m] of Object.entries(D.meridians)) {
    const grp = el("g", {class: "mer" + (m.posterior_primary && view === "back" ? "" : ""), id: `mer-${mk}`, "data-meridian": mk}, g);
    el("title", {}, grp).textContent = `${m.name} - reference only, placements approximate, not for clinical use`;
    const col = MCOL[mk];
    sideLoop: for (const sd of ["A", "B"]) {
      if (sd === "B" && (!S.layer.both || view === "side")) continue;
      for (const run of m.runs) {
        let seg = [];
        const flush = () => { if (seg.length >= 2) {
            // midline channels (Ren/Du) would draw twice; draw side B only if the run is not on the midline
            const p = el("path", {d: smooth(seg), stroke: col, "data-meridian": mk}, grp); }
          seg = []; };
        for (const code of run) { const q = PT[code]; const xy = q && placeXY(q, view, sd);
          if (xy && !(sd === "B" && isMidline(q, view))) seg.push(xy); else flush(); }
        flush();
      }
    }
  }
}
function buildPoints(g, lg, view) {
  const kinds = {};
  D.points.forEach(q => {
    if (!placedIn(q, view)) return;
    const m = D.meridians[q.meridian], col = MCOL[q.meridian], k = kindOf(q.code);
    sidesFor(q, view).forEach(sd => {
      const xy = placeXY(q, view, sd); if (!xy) return;
      const pt = el("g", {class: "pt", "data-point": q.code, "data-side": sd, "data-meridian": q.meridian}, g);
      el("circle", {class: "pthit", cx: xy[0], cy: xy[1]}, pt);
      const kc = k && KIND_CLASS[k] ? " " + KIND_CLASS[k] : "";
      const hollow = k === "Y" || k === "L" ? " hollow" : "";
      el("circle", {class: "mpt" + kc + hollow, cx: xy[0], cy: xy[1], fill: col, stroke: col}, pt);
      const run = m.runs.find(r => r.includes(q.code)) || [], endp = q.code === run[0] || q.code === run[run.length - 1];
      const prio = k ? 2 : endp ? 3 : 7;
      // whole-body views: the ankle/foot/toe labels are the densest on the figure, so they only appear from FOOT_LABEL_Z (hover/select always shows them); dots and meridian lines stay
      const pf = q.place[srcOf(view)], footish = !isDetail(view) && pf && (pf.frame === "foot" || (pf.frame === "body" && xy[1] > 1368));
      if (sd === "A" || !isMidline(q, view)) addLabel(lg, xy[0], xy[1], q.code, {prio, minZ: footish ? FOOT_LABEL_Z : (k || endp ? 1.3 : 2.6), code: q.code, side: sd, pin: q.pinyin});
    });
  });
  // extra points on the hand / foot (outside the WHO 361) and the alternative PC9 location
  const eg = el("g", {id: "extras", style: S.layer.extra ? "" : "display:none"}, g); GROUPS.extra = eg;
  const src = srcOf(view);
  EXTRAS.forEach(x => { const c = x[["front", "back", "medial", "lateral"].includes(src) ? src : "none"]; if (!c) return;
    if (isDetail(view) && DETAIL[view].frame !== x.frame) return;
    if (!isDetail(view) && x.frame !== "hand" && x.frame !== "foot") return;
    ["A", "B"].forEach(sd => { if (sd === "B" && !S.layer.both) return; const xy = frameXY(view, x.frame, sd, c[0], c[1]); if (!xy) return;
      const pt = el("g", {class: "pt xt", "data-extra": x.id, "data-side": sd}, eg);
      el("circle", {class: "pthit", cx: xy[0], cy: xy[1]}, pt);
      el("text", {class: "xpt", x: xy[0], y: xy[1], "text-anchor": "middle", "dominant-baseline": "central"}, pt).textContent = "◆";
      if (sd === "A") addLabel(lg, xy[0], xy[1], x.name, {prio: 5, minZ: x.frame === "hand" ? 5 : (isDetail(view) ? 3.5 : FOOT_LABEL_Z + 2), code: null}); }); });
  D.points.forEach(q => { const p = q.place[src]; if (!p || !p.alt || p.frame === "body") return; if (isDetail(view) && DETAIL[view].frame !== p.frame) return;
    ["A", "B"].forEach(sd => { if (sd === "B" && !S.layer.both) return; const xy = frameXY(view, p.frame, sd, p.alt[0], p.alt[1]); if (!xy) return;
      const r = el("circle", {class: "altpt", cx: xy[0], cy: xy[1]}, eg); el("title", {}, r).textContent = `${q.code} - alternative WHO location (approximate)`;
      if (sd === "A") addLabel(lg, xy[0], xy[1], q.code + " alt", {prio: 6, minZ: 9, code: null}); }); });
}

/* ---------------------------------------------------------------- view / zoom / labels */
const baseBox = () => isDetail(S.view) ? DETAIL[S.view].box : BASE;
function fitUPP() { const B = baseBox(); return Math.max((B.x1 - B.x0) / VW, (B.y1 - B.y0) / VH); }
function updateView() {
  const svg = $("viewer"), r = $("stage").getBoundingClientRect(); VW = Math.max(200, r.width); VH = Math.max(200, r.height);
  UPP = fitUPP() / S.z;
  const w = VW * UPP, h = VH * UPP; svg.setAttribute("viewBox", `${S.cx - w / 2} ${S.cy - h / 2} ${w} ${h}`);
  svg.style.setProperty("--upp", UPP.toFixed(4));
  svg.style.setProperty("--bandop", String(Math.max(.05, .2 - Math.max(0, S.z - 2.5) * .03).toFixed(3))); svg.classList.toggle("zoomed", S.z > 2.2);
  $("zinfo").textContent = `${(DETAIL[S.view] ? DETAIL[S.view].label : S.view).toUpperCase()} · ×${S.z.toFixed(1)}`;
  scheduleLabels();
}
let labelRaf = 0; function scheduleLabels() { if (!labelRaf) labelRaf = requestAnimationFrame(() => { labelRaf = 0; cullLabels(); }); }
function cullLabels() {
  const x0 = S.cx - VW * UPP / 2, y0 = S.cy - VH * UPP / 2, placed = []; const cell = 48, grid = new Map();
  const key = (i, j) => i + "," + j;
  const hit = b => { for (let i = Math.floor(b[0] / cell); i <= Math.floor(b[2] / cell); i++) for (let j = Math.floor(b[1] / cell); j <= Math.floor(b[3] / cell); j++) {
      const a = grid.get(key(i, j)); if (a) for (const o of a) if (!(b[2] < o[0] || b[0] > o[2] || b[3] < o[1] || b[1] > o[3])) return true; } return false; };
  const add = b => { for (let i = Math.floor(b[0] / cell); i <= Math.floor(b[2] / cell); i++) for (let j = Math.floor(b[1] / cell); j <= Math.floor(b[3] / cell); j++) { const k = key(i, j); (grid.get(k) || grid.set(k, []).get(k)).push(b); } };
  const vis = [];
  for (const L of LABELS) {
    const sx = (L.x - x0) / UPP, sy = (L.y - y0) / UPP;
    const forced = !L.hidden && (S.sel && L.code === S.sel || S.hov && L.code === S.hov);
    if (L.hidden || (!forced && (S.z < L.minZ || sx < -20 || sy < -20 || sx > VW + 20 || sy > VH + 20))) { if (L.on !== false) { L.e.style.display = "none"; L.on = false; } continue; }
    vis.push({L, sx, sy, forced});
  }
  vis.sort((a, b) => (b.forced - a.forced) || (a.L.prio - b.L.prio));
  for (const {L, sx, sy, forced} of vis) {
    const full = L.code && S.z >= 9 && L.pin ? `${L.code} ${L.pin}` : L.text, w = (full.length) * 6.4 + 6;
    const bx = L.center ? sx - w / 2 : sx + 6, b = [bx, sy - 9, bx + w, sy + 5];
    if (!forced && hit(b)) { if (L.on !== false) { L.e.style.display = "none"; L.on = false; } continue; }
    add(b);
    if (L.e.textContent !== full) L.e.textContent = full;
    L.e.setAttribute("x", (L.center ? L.x : L.x + 6 * UPP).toFixed(1)); L.e.setAttribute("y", (L.y + 4 * UPP).toFixed(1)); L.e.style.display = ""; L.on = true;
  }
}
let anim = 0;
function flyTo(cx, cy, z, ms = 450) {
  cancelAnimationFrame(anim); const a = {cx: S.cx, cy: S.cy, lz: Math.log(S.z)}, b = {cx, cy, lz: Math.log(Math.min(60, Math.max(.7, z)))}, t0 = performance.now();
  if (ms <= 0) { S.cx = cx; S.cy = cy; S.z = Math.exp(b.lz); updateView(); return; }
  const step = t => { const u = Math.min(1, (t - t0) / ms), e = u < .5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2;
    S.cx = a.cx + (b.cx - a.cx) * e; S.cy = a.cy + (b.cy - a.cy) * e; S.z = Math.exp(a.lz + (b.lz - a.lz) * e); updateView(); if (u < 1) anim = requestAnimationFrame(step); };
  anim = requestAnimationFrame(step);
}
function zoomAt(f, px, py) {
  const x0 = S.cx - VW * UPP / 2, y0 = S.cy - VH * UPP / 2, wx = x0 + px * UPP, wy = y0 + py * UPP;
  S.z = Math.min(60, Math.max(.7, S.z * f)); const u2 = fitUPP() / S.z; S.cx = wx - (px - VW / 2) * u2; S.cy = wy - (py - VH / 2) * u2; updateView();
}
function jump(name) {
  const dt = DETAIL[S.view];
  if (dt) {   // detail views: both / one hand or foot / fingertips-toes / wrist-ankle
    const B = dt.box, cx = (B.x0 + B.x1) / 2, cy = (B.y0 + B.y1) / 2, hand = dt.frame === "hand", one = side => { const m = frameMat(S.view, dt.frame, side); return [m[4], m[5]]; };
    if (name === "all") return flyTo(cx, cy, 1);
    if (dt.frame === "footm" || dt.frame === "footl") {   // side views: local frame x = -a (medial) / +a (lateral), y down
      const sg = dt.frame === "footm" ? -1 : 1, at = (side, a, y) => frameXY(S.view, dt.frame, side, sg * a, y);
      if (name === "handA" || name === "handB") { const xy = at(name === "handA" ? "A" : "B", 5, 1.5); return flyTo(xy[0], xy[1], 1.7); }
      if (name === "tips") { const xy = at("A", 15.5, 6.5); return flyTo(xy[0], xy[1], 4.6); }
      if (name === "wrist") { const xy = at("A", -2.6, 1.6); return flyTo(xy[0], xy[1], 3.4); }
    }
    if (name === "handA" || name === "handB") { const [x, y] = one(name === "handA" ? "A" : "B"); return flyTo(x, hand ? y - 9 * K : y - (S.view === "foot_sole" ? 5.4 : 7.6) * K, hand ? 1.75 : (S.view === "foot_sole" ? 1.06 : 1.3)); }
    if (name === "tips") { const [x, y] = one("A"); return flyTo(x + (hand ? -.9 : 0) * K, y + (hand ? -16.5 : -15.8) * K, hand ? 7 : 4.6); }
    if (name === "wrist") { const [x, y] = one("A"); return flyTo(x, y + (hand ? 1.5 : 0.8) * K, hand ? 5 : 3.2); }
    return flyTo(cx, cy, 1);
  }
  const V = VIEWS[S.view], top = V.top, bot = V.bottom, mid = V.mid_x;
  const spot = {all: [mid, (BASE.y0 + BASE.y1) / 2, 1], head: [mid, top + (bot - top) * .07, 4.2], torso: [mid, top + (bot - top) * .38, 2.1], feet: [mid, bot - (bot - top) * .06, 3.6]}[name];
  if (name === "handA" || name === "handB") {
    const h = hands(S.view)[0]; if (!h) { flyTo(mid, 620, 3); return; }
    const s = h.forearm_width_px / D.hand.wrist_width_cm; let x = h.wrist[0] + h.axis[0] * 9 * s, y = h.wrist[1] + h.axis[1] * 9 * s;
    if (name === "handB") x = 2 * mid - x; flyTo(x, y, S.view === "face" ? 1 : 5.8); return;
  }
  if (S.view === "face") { flyTo(449.5, 800, 1.25); return; }
  flyTo(...spot);
}
function setView(v, then) {
  S.view = v; document.querySelectorAll("#viewtabs button").forEach(b => b.classList.toggle("on", b.dataset.view === v));
  const dt = DETAIL[v]; document.querySelectorAll("#jumps .bodyjump").forEach(b => b.hidden = !!dt); document.querySelectorAll("#jumps .detailjump").forEach(b => b.hidden = !dt);
  if (dt) { S.cx = (dt.box.x0 + dt.box.x1) / 2; S.cy = (dt.box.y0 + dt.box.y1) / 2; S.z = 1; }
  else if (v === "face") { S.cx = 449.5; S.cy = 800; S.z = 1.25; } else { S.cx = VIEWS[v].mid_x; S.cy = 800; S.z = 1; }
  renderScene(); if (then) then();
}

/* ---------------------------------------------------------------- selection, tooltip, card, search */
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
function tipHTML(q, sd) {
  const m = D.meridians[q.meridian], k = kindOf(q.code);
  return `<b>${q.code}</b> · ${esc(q.pinyin)} ${esc(q.hanzi)}<br><span class="m">${esc(m.name)}${k ? " · " + ({Y: "Yuan-source", L: "Luo-connecting", H: "Horary", "Y·H": "Yuan-source + Horary"}[k] || k) : ""}</span><br>${esc(q.location.length > 170 ? q.location.slice(0, 168) + "…" : q.location)}`;
}
function showTip(html, ev) {
  const t = $("tip"), r = $("stage").getBoundingClientRect(); t.innerHTML = html; t.style.display = "block";
  let x = ev.clientX - r.left + 14, y = ev.clientY - r.top + 14; const w = t.offsetWidth, h = t.offsetHeight;
  if (x + w > r.width - 6) x = ev.clientX - r.left - w - 14; if (y + h > r.height - 6) y = ev.clientY - r.top - h - 14; t.style.left = Math.max(4, x) + "px"; t.style.top = Math.max(4, y) + "px";
}
const hideTip = () => { $("tip").style.display = "none"; };
function setHover(code) {
  if (S.hov === code) return;
  document.querySelectorAll(".pt.hov").forEach(n => n.classList.remove("hov")); document.querySelectorAll(".mer.hl").forEach(n => n.classList.remove("hl"));
  S.hov = code;
  if (code) { document.querySelectorAll(`.pt[data-point="${code}"]`).forEach(n => n.classList.add("hov")); const mk = PT[code].meridian; $(`mer-${mk}`)?.classList.add("hl"); }
  scheduleLabels();
}
function markSelected() {
  document.querySelectorAll(".pt.sel").forEach(n => n.classList.remove("sel")); document.querySelectorAll(".selring").forEach(n => n.remove());
  if (!S.sel) return;
  document.querySelectorAll(`.pt[data-point="${S.sel}"]`).forEach(n => { n.classList.add("sel"); const c = n.querySelector(".mpt"); el("circle", {class: "selring", cx: c.getAttribute("cx"), cy: c.getAttribute("cy")}, GROUPS.pts); });
}
function selectPoint(code, o = {}) {
  const q = PT[code]; if (!q) return; S.sel = code;
  let view = S.view; if (!placedIn(q, view)) view = ["front", "back", "side", "face", "palm", "dorsum", "foot_top", "foot_sole", "foot_medial", "foot_lateral"].find(v => placedIn(q, v));
  const go = () => { markSelected(); renderCard(q);
    const xy = placeXY(q, S.view, "A"); if (xy && o.fly !== false) { const pl0 = q.place[srcOf(S.view)], local = pl0 && pl0.frame !== "body"; flyTo(xy[0], xy[1], local ? (isDetail(S.view) ? 12 : 16) : S.view === "face" ? 3.2 : 6.5); } scheduleLabels(); };
  if (view !== S.view) setView(view, go); else go();
  const mk = q.meridian; if (EL[mk] && mk !== "ren" && mk !== "du") brief("organ", mk);
}
function renderCard(q) {
  const m = D.meridians[q.meridian], k = kindOf(q.code), v = q.validation || {}, views = Object.keys(q.place);
  const pl = q.place[srcOf(S.view)] || q.place[views[0]];
  const detailViews = Object.keys(DETAIL).filter(d => placedIn(q, d)).map(d => DETAIL[d].label);
  const run = m.runs.find(r => r.includes(q.code)) || [], i = run.indexOf(q.code), prev = run[i - 1], next = run[i + 1];
  $("card").innerHTML = `<div><span class="code">${q.code}</span><span class="hz">${esc(q.hanzi)}</span></div>
  <div class="py"><b>${esc(q.pinyin)}</b> · ${esc(q.english)}</div>
  <dl><dt>Meridian</dt><dd><span style="color:${MCOL[q.meridian]}">●</span> ${esc(m.name)} <span class="badge">${m.polarity}${m.element !== "extraordinary" ? " · " + m.element : ""}</span></dd>
  <dt>Location</dt><dd>${esc(q.location)}</dd>
  ${k ? `<dt>Category</dt><dd>${({Y: "Yuan-source", L: "Luo-connecting", H: "Horary", "Y·H": "Yuan-source and horary"})[k] || k} (ATI table)</dd>` : ""}
  ${q.category_note ? `<dt>Notes</dt><dd>${esc(q.category_note)}</dd>` : ""}
  <dt>Aliases</dt><dd>${q.alias.join(" · ")}</dd>
  <dt>On views</dt><dd>${views.map(x => x[0].toUpperCase() + x.slice(1)).concat(detailViews).join(", ")}</dd>
  <dt>Placement</dt><dd><span class="badge warn">approximate placement</span> ${esc(pl.basis)}</dd>
  <dt>Source check</dt><dd>${v.who_code_found ? `<span class="badge ok">WHO 2008: code ${v.pinyin_match ? "+ pinyin " : ""}match</span>` : `<span class="badge warn">not matched in WHO text</span>`} ${q.who_ref ? esc(q.who_ref) : ""}</dd></dl>
  <div class="nav"><button class="chip" ${prev ? `data-go="${prev}"` : "disabled"}>← ${prev || "—"}</button><button class="chip" ${next ? `data-go="${next}"` : "disabled"}>${next || "—"} →</button></div>`;
  $("card").querySelectorAll("[data-go]").forEach(b => b.onclick = () => selectPoint(b.dataset.go));
}
const EX = Object.fromEntries(EXTRAS.map(x => [x.id, x]));
const extraTip = x => `<b>${x.code}</b> · ${x.name} ${x.hanzi}<br><span class="m">Extra point (not one of the WHO 2008 361) · ${x.finger}</span><br>${esc(x.location)}`;
function selectExtra(id, o = {}) {
  const x = EX[id]; if (!x) return; S.sel = null;
  const view = (isDetail(S.view) && DETAIL[S.view].frame === x.frame && x[DETAIL[S.view].src]) ? S.view : x.frame === "footm" ? "foot_medial" : x.frame === "footl" ? "foot_lateral" : (x.front ? "front" : "back");
  const go = () => { markSelected(); $("card").innerHTML = `<div><span class="code">${x.code}</span><span class="hz">${esc(x.hanzi)}</span></div><div class="py"><b>${x.name}</b> · instance ${id.split(".")[1]} (${x.finger})</div>
    <dl><dt>Location</dt><dd>${esc(x.location)}</dd><dt>Placement</dt><dd><span class="badge warn">approximate placement</span> ${esc(x.basis)}</dd><dt>Source</dt><dd><span class="badge warn">secondary source</span> ${esc(x.source)}</dd></dl>`;
    if (o.fly !== false) { const xy = frameXY(S.view, x.frame, "A", (x[srcOf(S.view)] || x.front || x.back)[0], (x[srcOf(S.view)] || x.front || x.back)[1]); if (xy) flyTo(xy[0], xy[1], isDetail(S.view) ? 10 : 14); } };
  if (S.view !== view) setView(view, go); else go();
}
function selectHouse(n) {
  S.selHouse = n; document.querySelectorAll(".hit").forEach(r => r.classList.toggle("sel", +r.dataset.house === n));
  const b = houseBands(), reg = D.layers.houses.regions[n];
  if (S.view !== "face") { const V = VIEWS[S.view]; flyTo(V.mid_x, (b[n - 1] + b[n]) / 2, 2.4); }
  const inBand = D.points.filter(q => q.place.front && q.place.front.frame === "body" && q.place.front.y >= b[n - 1] && q.place.front.y < b[n]).length;
  $("housenote").textContent = `House ${n}: ${reg}. ${inBand} point placements on the front view fall in this band.`;
  brief("house", String(n));
}
function searchPoints(qs) {
  const raw = qs.trim(); if (!raw) return [];
  const code = raw.replace(/[^a-z0-9]/gi, "").toUpperCase(), t = strip(raw), out = [];
  const seen = new Set(); const push = (p, s) => { if (!seen.has(p.code)) { seen.add(p.code); out.push([p, s]); } };
  D.points.forEach(p => { if (p.alias.some(a => a.replace(/[^A-Z0-9]/g, "") === code)) push(p, 0); });
  if (/^[A-Z]{1,3}$/.test(code)) D.points.forEach(p => { if (p.alias.some(a => a.replace(/[^A-Z0-9]/g, "").startsWith(code) && /^[A-Z]+\d+$/.test(a.replace(/-/g, "")))) push(p, 1); });
  D.points.forEach(p => { if (/\d$/.test(code) && p.alias.some(a => a.replace(/[^A-Z0-9]/g, "").startsWith(code))) push(p, 2); });
  D.points.forEach(p => { const hay = strip(p.pinyin + " " + p.english + " " + D.meridians[p.meridian].name); if (hay.includes(t) || p.hanzi.includes(raw)) push(p, 3); });
  const ex = {}; EXTRAS.forEach(x => { if (!ex[x.code]) ex[x.code] = x; });
  Object.values(ex).forEach(x => { if (x.code.replace(/[^A-Z0-9]/g, "") === code || strip(x.name).includes(t) || x.hanzi.includes(raw) || (/^EX/.test(code) && x.code.replace(/[^A-Z0-9]/g, "").startsWith(code))) out.push([{code: x.code, extra: x.id, pinyin: x.name, hanzi: x.hanzi, meridian: "extra", number: 0}, 2]); });
  return out.sort((a, b) => a[1] - b[1] || a[0].number - b[0].number).map(x => x[0]);
}
let RES = [], RI = 0;
function showResults() {
  RES = searchPoints($("q").value); RI = 0; const ul = $("results"); ul.innerHTML = "";
  RES.slice(0, 14).forEach((p, i) => { const li = document.createElement("li"); if (!i) li.className = "act";
    li.innerHTML = `<b>${p.code}</b><span>${esc(p.pinyin)} ${esc(p.hanzi)} · ${p.extra ? "extra point" : esc(D.meridians[p.meridian].abbr)}</span>`; li.onclick = () => { p.extra ? selectExtra(p.extra) : selectPoint(p.code); }; ul.appendChild(li); });
  if (RES.length > 14) { const li = document.createElement("li"); li.innerHTML = `<span>+${RES.length - 14} more - refine the search</span>`; ul.appendChild(li); }
}

/* ---------------------------------------------------------------- meridian chips / filters / layers */
function applyMerFilter() {
  document.querySelectorAll(".mer").forEach(g => { const k = g.dataset.meridian; g.style.display = (S.solo ? S.solo === k : !S.off.has(k)) ? "" : "none"; });
  document.querySelectorAll(".pt").forEach(g => { const k = g.dataset.meridian; g.style.display = (S.solo ? S.solo === k : !S.off.has(k)) ? "" : "none"; });
  LABELS.forEach(L => { if (L.code) { const k = PT[L.code].meridian; L.hidden = S.solo ? S.solo !== k : S.off.has(k); } });
  document.querySelectorAll("#merchips .mc").forEach(c => { const k = c.dataset.m; c.classList.toggle("off", S.solo ? S.solo !== k : S.off.has(k)); });
  scheduleLabels();
}
function buildChips() {
  const box = $("merchips"); box.innerHTML = "";
  Object.entries(D.meridians).forEach(([k, m]) => { const c = document.createElement("div"); c.className = "mc"; c.dataset.m = k;
    c.innerHTML = `<i style="background:${MCOL[k]}"></i>${m.abbr} <span style="color:var(--muted)">${m.count}</span>`; c.title = m.name;
    c.onclick = e => { if (e.shiftKey) S.solo = S.solo === k ? null : k; else { S.solo = null; S.off.has(k) ? S.off.delete(k) : S.off.add(k); } applyMerFilter(); };
    c.onmouseenter = () => { $(`mer-${k}`)?.classList.add("hl"); }; c.onmouseleave = () => { $(`mer-${k}`)?.classList.remove("hl"); };
    box.appendChild(c); });
  const hc = $("hchips"); hc.innerHTML = "";
  for (let i = 1; i <= 12; i++) { const d = document.createElement("div"); d.innerHTML = `<b style="color:${HUES[i - 1]}">${i}</b>${esc(D.layers.houses.regions[i])}`; d.onclick = () => selectHouse(i); hc.appendChild(d); }
}
function wireLayerToggles() {
  [["t-clock", "p-clock"], ["t-pillars", "p-pillars"]].forEach(([t, p]) => $(t).onchange = () => $(p).hidden = !$(t).checked);
  const show = (id, on) => { const g = $(id); if (g) g.style.display = on ? "" : "none"; };
  $("t-houses").onchange = () => { S.layer.houses = $("t-houses").checked; ["houses", "glyphs"].forEach(id => show(id, S.layer.houses && S.view !== "face")); $("p-houses").hidden = !S.layer.houses; };
  $("t-meridians").onchange = () => { S.layer.meridians = $("t-meridians").checked; show("meridians", S.layer.meridians); $("p-meridians").hidden = !S.layer.meridians; };
  $("t-points").onchange = () => { S.layer.points = $("t-points").checked; show("points", S.layer.points); show("labels", S.layer.points && S.layer.labels); };
  $("t-labels").onchange = () => { S.layer.labels = $("t-labels").checked; show("labels", S.layer.labels); };
  $("t-bones").onchange = () => { S.layer.bones = $("t-bones").checked; renderScene(); };
  $("t-organs").onchange = () => { S.layer.organs = $("t-organs").checked; show("organs", S.layer.organs && (S.view === "front" || S.view === "back")); };
  $("t-palm").onchange = () => { S.layer.palm = $("t-palm").checked; const pv = S.view === "front" || S.view === "palm"; show("palmistry", S.layer.palm && pv); if (S.layer.palm && pv) { /* nudge toward a hand if zoomed out */ if (S.z < (S.view === "palm" ? 1.5 : 4)) jump(S.view === "palm" ? "handA" : "handA"); } };
  $("t-land").onchange = () => { S.layer.land = $("t-land").checked; show("landmarks", S.layer.land); };
  $("t-extra").onchange = () => { S.layer.extra = $("t-extra").checked; show("extras", S.layer.extra); };
  $("t-both").onchange = () => { S.layer.both = $("t-both").checked; renderScene(); };
  $("t-active").onchange = () => { if (S.LAST) markMeridians(S.LAST); };
}

/* ---------------------------------------------------------------- pointer interaction */
function wireViewer() {
  const stage = $("stage"), ptrs = new Map(); let moved = 0, startC = null, pinch0 = null;
  stage.addEventListener("wheel", e => { e.preventDefault(); const r = stage.getBoundingClientRect(); cancelAnimationFrame(anim); zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? .01 : .0015)), e.clientX - r.left, e.clientY - r.top); }, {passive: false});
  stage.addEventListener("pointerdown", e => { if (e.target.closest(".zoom")) return; ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY}); moved = 0; cancelAnimationFrame(anim);
    if (ptrs.size === 1) startC = {x: e.clientX, y: e.clientY, cx: S.cx, cy: S.cy}; if (ptrs.size === 2) { const [a, b] = [...ptrs.values()]; pinch0 = {d: Math.hypot(a.x - b.x, a.y - b.y), z: S.z}; } });
  addEventListener("pointermove", e => {
    if (ptrs.has(e.pointerId)) { ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY});
      if (ptrs.size === 2 && pinch0) { const [a, b] = [...ptrs.values()], r = stage.getBoundingClientRect(); const d = Math.hypot(a.x - b.x, a.y - b.y);
        const f = (pinch0.z * d / pinch0.d) / S.z; zoomAt(f, (a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top); moved = 99; }
      else if (ptrs.size === 1 && startC) { const dx = e.clientX - startC.x, dy = e.clientY - startC.y; moved = Math.max(moved, Math.hypot(dx, dy));
        if (moved > 4) { stage.classList.add("drag"); hideTip(); S.cx = startC.cx - dx * UPP; S.cy = startC.cy - dy * UPP; updateView(); } }
      return; }
    if (!stage.contains(e.target)) return;
    const n = e.target.closest && e.target.closest(".pt"), pal = e.target.dataset && e.target.dataset.tip;
    if (n && n.dataset.extra) { setHover(null); showTip(extraTip(EX[n.dataset.extra]), e); }
    else if (n) { const q = PT[n.dataset.point]; setHover(q.code); showTip(tipHTML(q, n.dataset.side), e); }
    else if (pal) { setHover(null); showTip(pal, e); } else { setHover(null); hideTip(); }
  });
  addEventListener("pointerup", e => { const was = ptrs.has(e.pointerId); ptrs.delete(e.pointerId); stage.classList.remove("drag"); if (ptrs.size < 2) pinch0 = null; if (ptrs.size === 1) { const [p] = [...ptrs.values()]; startC = {x: p.x, y: p.y, cx: S.cx, cy: S.cy}; }
    if (was && ptrs.size === 0 && moved <= 4 && stage.contains(e.target)) { const n = e.target.closest && e.target.closest(".pt"); if (n && n.dataset.extra) selectExtra(n.dataset.extra, {fly: false}); else if (n) selectPoint(n.dataset.point, {fly: false}); } });
  addEventListener("pointercancel", e => { ptrs.delete(e.pointerId); stage.classList.remove("drag"); });
  stage.addEventListener("pointerleave", () => { setHover(null); hideTip(); });
  stage.addEventListener("dblclick", e => { if (e.target.closest(".zoom") || e.target.closest(".hit")) return; const r = stage.getBoundingClientRect(); const x0 = S.cx - VW * UPP / 2, y0 = S.cy - VH * UPP / 2; flyTo(x0 + (e.clientX - r.left) * UPP, y0 + (e.clientY - r.top) * UPP, S.z * 2.2, 300); });
  $("zin").onclick = () => zoomAt(1.4, VW / 2, VH / 2); $("zout").onclick = () => zoomAt(1 / 1.4, VW / 2, VH / 2); $("zreset").onclick = () => jump("all");
  document.querySelectorAll("#viewtabs button").forEach(b => b.onclick = () => setView(b.dataset.view));
  document.querySelectorAll("#jumps button").forEach(b => b.onclick = () => jump(b.dataset.jump));
  addEventListener("resize", updateView);
  const q = $("q"); q.addEventListener("input", showResults);
  q.addEventListener("keydown", e => { const lis = [...$("results").querySelectorAll("li")]; 
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); RI = Math.max(0, Math.min(Math.min(RES.length, 14) - 1, RI + (e.key === "ArrowDown" ? 1 : -1))); lis.forEach((l, i) => l.classList.toggle("act", i === RI)); }
    if (e.key === "Enter" && RES[RI]) { const p = RES[RI]; p.extra ? selectExtra(p.extra) : selectPoint(p.code); } if (e.key === "Escape") { q.value = ""; showResults(); } });
}

/* ---------------------------------------------------------------- ATI lighting hooks (as in body.html) */
const angle = m => (m / 1440) * 2 * Math.PI - Math.PI / 2;
const mins = s => { const [h, m] = s.split(":").map(Number); return h * 60 + m; };
function drawClock(L) {
  const svg = $("clock"); svg.innerHTML = ""; const R = 190;
  el("circle", {class: "ring", cx: 0, cy: 0, r: R}, svg);
  const now = new Date(), m = now.getHours() * 60 + now.getMinutes(); const tags = {};
  (L.interactions || []).forEach(i => i.organs.forEach(o => (tags[o] = tags[o] || new Set()).add(i.type)));
  L.windows.forEach(w => {
    const a = angle(mins(w.start) + 60), x = R * Math.cos(a), y = R * Math.sin(a);
    const g = el("g", {style: `--el: var(--${w.element})`}, svg);
    const c = el("circle", {class: "oc" + (w.organ === L.active_window.organ ? " active" : ""), id: `organ-${w.organ}`, cx: x, cy: y, r: 34, tabindex: 0, "aria-label": `${w.organ_display} ${w.start} to ${w.end}`}, g);
    c.addEventListener("click", () => brief("organ", w.organ)); c.addEventListener("keydown", e => { if (e.key === "Enter") brief("organ", w.organ); });
    el("text", {class: "oname", x, y: y - 1}, g).textContent = w.organ_display.replace("Large ", "L. ").replace("Small ", "S. ");
    el("text", {class: "otime", x, y: y + 12}, g).textContent = `${w.start}–${w.end}`;
    if (tags[w.organ]) el("text", {class: "tag", x: 1.27 * x, y: 1.27 * y + 3}, g).textContent = [...tags[w.organ]].join(" · ");
  });
  el("line", {class: "hand", x1: 0, y1: 0, x2: 0, y2: -R + 44, transform: `rotate(${angle(m) * 180 / Math.PI + 90})`}, svg);
  el("text", {class: "oname", x: 0, y: 4}, svg).textContent = `${L.active_window.organ_display} · ${L.active_window.branch_display}`;
}
function drawPillars(L) {
  const box = $("pillars"); box.innerHTML = "";
  ["year", "month", "day", "hour"].forEach(k => {
    const row = document.createElement("div"); row.className = "pill" + (k === "hour" ? " hour" : "");
    const n = L.natal_pillars ? L.natal_pillars[k].hanzi : "—";
    row.innerHTML = `<span class="k">${k.toUpperCase()}</span><span class="n" title="natal - locked">${n}</span><span class="c">${L.current_pillars[k].hanzi}</span>`; box.appendChild(row); });
  const ul = $("tags"); ul.innerHTML = "";
  if (!L.natal_pillars) { ul.innerHTML = `<li>${L.natal_status}</li>`; return; }
  if (!L.interactions.length) { ul.innerHTML = "<li>No current pillar acts on your chart this hour.</li>"; return; }
  L.interactions.forEach(i => { const li = document.createElement("li");
    li.innerHTML = `<b>${i.tag}</b> · now ${i.current} on natal ${i.natal} → ${i.organs.join(", ").replace(/_/g, " ")}`; ul.appendChild(li); });
}
function markMeridians(L) {
  document.querySelectorAll(".mer").forEach(p => p.classList.remove("active", "pressure"));
  if ($("t-active").checked) $(`mer-${L.active_window.organ}`)?.classList.add("active");
  (L.interactions || []).forEach(i => i.organs.forEach(o => $(`mer-${o}`)?.classList.add("pressure")));
}
// House lighting: today's planets bright in the house they occupy, natal planets ghosted. House numbers and names only.
function lightHouses(L) {
  document.querySelectorAll(".glow").forEach(b => b.classList.remove("lit"));
  const gl = $("glyphs"); if (!gl) return; gl.innerHTML = ""; const b = houseBands();
  const place = (groups, cls, dy) => (groups || []).forEach(h => {
    const y = (b[h.house - 1] + b[h.house]) / 2 + dy, n = h.bodies.length;
    h.bodies.forEach((bd, i) => { const t = el("text", {class: `glyph ${cls}`, x: 1000 + (i - (n - 1) / 2) * 34, y}, gl); t.textContent = bd.glyph;
      el("title", {}, t).textContent = `${bd.name} (${cls === "now" ? "now" : "natal"}) - house ${h.house}`; }); });
  place(L.natal_ghosts, "natal", -12); place(L.houses_lit, "now", 22);
  (L.houses_lit || []).forEach(h => document.querySelector(`.glow[data-glow="${h.house}"]`)?.classList.add("lit"));
  $("housenote").textContent = L.houses_lit && L.houses_lit.length
    ? `Lit: houses ${L.houses_lit.map(h => h.house).join(", ")} - gold glyphs are now, grey glyphs are natal.`
    : `Houses unlit: ${L.houses_status}.`;
}
async function refresh() {
  try { const L = await ask("lighting");
    if (L.error) throw new Error(L.error);
    window.POINT_KINDS = Object.fromEntries(Object.values(L.points.points).flat().map(([c, , k]) => [c, k]));
    $("banner").hidden = true; S.live = true;
    drawClock(L); drawPillars(L); const first = !S.LAST; S.LAST = L; if (first || S.kindsChanged !== JSON.stringify(window.POINT_KINDS)) { S.kindsChanged = JSON.stringify(window.POINT_KINDS); renderScene(); } else { lightHouses(L); markMeridians(L); }
  } catch (e) {
    $("brief").textContent = `Lighting unavailable (${e.message}). Open this page from the MycOS webapp, where ATI answers.`;
    if (!S.live) { const L = window.DEMO_LIGHTING(); $("banner").hidden = false; $("banner").textContent = "Offline preview - ATI is not reachable from here. Showing the generic organ clock and body only; no natal, pillar or planet data.";
      drawClock(L); drawPillars(L); window.POINT_KINDS = {}; const first = !S.LAST; S.LAST = L; if (first) renderScene(); else { lightHouses(L); markMeridians(L); } }
  }
}

/* ---------------------------------------------------------------- boot */
buildChips(); wireLayerToggles(); wireViewer();
renderScene(); refresh(); setInterval(refresh, 60000);
// URL hooks for review: ?view=back&zoom=6&at=x,y&sel=LU11&palm=1
(function () { const q = new URLSearchParams(location.search);
  if (q.get("view") && (VIEWS[q.get("view")] || DETAIL[q.get("view")])) setView(q.get("view"));
  if (q.get("bones") === "1") { $("t-bones").checked = true; $("t-bones").onchange(); }
  if (q.get("palm") === "1") { $("t-palm").checked = true; $("t-palm").onchange(); }
  if (q.get("organs") === "1") { $("t-organs").checked = true; $("t-organs").onchange(); }
  if (q.get("sel") && PT[q.get("sel")]) selectPoint(q.get("sel"));
  else if (q.get("at")) { const [x, y] = q.get("at").split(",").map(Number); flyTo(x, y, +q.get("zoom") || 4, 0); }
})();
window.__body = {S, D, searchPoints, jump, flyTo, selectPoint, setView, renderScene};
