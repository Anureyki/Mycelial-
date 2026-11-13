"use strict";
/* Anatomical art for the hand and foot templates (centimetre frames). Pure drawing - no app state.
   Shapes come from data/hand.json / data/foot.json (traced from public-domain / CC BY references, see SOURCES.md). */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const el = (t, a, p) => { const e = document.createElementNS(NS, t); for (const k in a || {}) e.setAttribute(k, a[k]); if (p) p.appendChild(e); return e; };
  function defs(svg) {
    if (svg.querySelector("#artdefs")) return;
    const d = el("defs", {id: "artdefs"}, svg);
    const lg = (id, stops, x1 = 0, y1 = 0, x2 = 1, y2 = 1) => { const g = el("linearGradient", {id, x1, y1, x2, y2}, d); stops.forEach(([o, c]) => el("stop", {offset: o, "stop-color": c}, g)); };
    const rg = (id, stops) => { const g = el("radialGradient", {id, cx: .5, cy: .5, r: .5}, d); stops.forEach(([o, c, op]) => el("stop", {offset: o, "stop-color": c, "stop-opacity": op}, g)); };
    lg("skinG", [[0, "#46566a"], [.55, "#35435a"], [1, "#27303f"]]);
    lg("nailG", [[0, "#7f93a9"], [1, "#576b83"]], 0, 0, 0, 1);
    { const f = el("filter", {id: "rimblur", x: "-5%", y: "-5%", width: "110%", height: "110%"}, d); el("feGaussianBlur", {stdDeviation: 0.17}, f); }
    rg("padHi", [[0, "#dfe9f5", .13], [.6, "#dfe9f5", .05], [1, "#dfe9f5", 0]]);
    rg("padLo", [[0, "#05080c", .35], [1, "#05080c", 0]]);
    rg("padArch", [[0, "#e9f1fb", .17], [.7, "#e9f1fb", .07], [1, "#e9f1fb", 0]]);
    rg("padBand", [[0, "#05080c", .22], [1, "#05080c", 0]]);
    rg("malG", [[0, "#e8f0fa", .30], [.55, "#dfe9f5", .12], [1, "#dfe9f5", 0]]);
  }
  /* ---- hand ------------------------------------------------------------- */
  const skinPath = (H, cut) => cut ? H.skin_cut : H.skin;
  function handOutline(g, H, cut) { return el("path", {d: skinPath(H, cut), class: "bodyline"}, g); }
  function handFill(g, H, cut) { return el("path", {d: skinPath(H, cut), class: "bodyfill skinfill"}, g); }
  function handDetail(g, H, surface, o) {
    o = o || {}; const svg = g.ownerSVGElement; defs(svg);
    const id = "hclip" + Math.random().toString(36).slice(2, 7);
    const cp = el("clipPath", {id}, el("defs", {}, g)); el("path", {d: skinPath(H, o.cut)}, cp);
    const root = el("g", {"clip-path": `url(#${id})`, class: "hdetail"}, g);
    if (o.bones) { const bg = el("g", {class: "bones"}, root); Object.values(H.bones).forEach(d => el("path", {d, class: "bone"}, bg)); }
    // soft form: dark inner rim + lighter pads
    { const rg = el("g", {filter: "url(#rimblur)"}, root); ["r1", "r2", "r3", "r4", "r5"].forEach(c => el("path", {d: skinPath(H, o.cut), class: "rim " + c}, rg)); }
    if (surface === "palm") {
      H.pads.forEach(p => el("ellipse", {cx: p.cx, cy: p.cy, rx: p.rx, ry: p.ry, transform: `rotate(${p.rot} ${p.cx} ${p.cy})`, fill: "url(#padHi)", class: "pad " + p.kind}, root));
      H.creases.forEach(c => el("path", {d: c.path, class: "crease " + c.kind}, root));
    } else {
      H.knuckles.forEach(k => { el("circle", {cx: k.x, cy: k.y + .12, r: k.r * 1.25, fill: "url(#padHi)", class: "pad"}, root); });
      H.tendons.forEach(d => el("path", {d, class: "tendon"}, root));
      H.dorsal_creases.forEach(d => el("path", {d, class: "crease digit"}, root));
    }
    el("path", {d: skinPath(H, o.cut), class: "skinedge"}, root);
    if (surface === "dorsum") Object.keys(H.nails).forEach(f => { el("path", {d: H.nails[f], class: "nailp"}, g); el("path", {d: H.lunulae[f], class: "lunula"}, g); });
    return root;
  }
  /* ---- foot ------------------------------------------------------------- */
  const fskin = (T, cut, surface) => surface === "plantar" ? (cut ? T.skin_cut_plantar : T.skin_plantar) : (cut ? T.skin_cut : T.skin_dorsal);
  function footOutline(g, T, cut, surface) { return el("path", {d: fskin(T, cut, surface), class: "bodyline"}, g); }
  function footFill(g, T, cut, surface) { return el("path", {d: fskin(T, cut, surface), class: "bodyfill skinfill"}, g); }
  function footDetail(g, T, surface, o) {
    o = o || {}; const svg = g.ownerSVGElement; defs(svg); const plantar = surface === "plantar";
    const id = "fclip" + Math.random().toString(36).slice(2, 7);
    const cp = el("clipPath", {id}, el("defs", {}, g)); el("path", {d: fskin(T, o.cut, surface)}, cp);
    const root = el("g", {"clip-path": `url(#${id})`, class: "hdetail"}, g);
    if (o.bones && T.bones) { const bg = el("g", {class: "bones"}, root);       // schematic vector bones (frame coordinates; the plantar view is mirrored by the frame matrix)
      Object.keys(T.bones).forEach(k => el("path", {d: T.bones[k], class: "bone"}, bg));
      if (plantar) (T.sesamoids || []).forEach(d => el("path", {d, class: "bone"}, bg)); }
    { const rg = el("g", {filter: "url(#rimblur)"}, root); ["r1", "r2", "r3", "r4", "r5"].forEach(c => el("path", {d: fskin(T, o.cut, surface), class: "rim " + c}, rg)); }
    T.pads.forEach(p => { if (!plantar && p.kind !== "pulp") return; el("ellipse", {cx: p.cx, cy: p.cy, rx: p.rx, ry: p.ry, transform: `rotate(${p.rot} ${p.cx} ${p.cy})`, fill: p.kind === "arch" ? "url(#padArch)" : p.kind === "latband" ? "url(#padBand)" : "url(#padHi)", class: "pad " + p.kind}, root); });
    if (plantar) T.plantar_creases.forEach(d => el("path", {d, class: "crease digit"}, root));
    else { T.tendons.forEach(d => el("path", {d, class: "tendon"}, root)); T.dorsal_creases.forEach(d => el("path", {d, class: "crease digit"}, root)); }
    el("path", {d: fskin(T, o.cut, surface), class: "skinedge"}, root);
    if (!plantar) Object.keys(T.nails).forEach(f => { el("path", {d: T.nails[f], class: "nailp"}, g); el("path", {d: T.lunulae[f], class: "lunula"}, g); });
    return root;
  }
  /* ---- foot, medial / lateral views (own frames "footm" / "footl": x horizontal, y down, cm) ----------------------------- */
  function sideOutline(g, T) { return el("path", {d: T.skin, class: "bodyline"}, g); }
  function sideFill(g, T) { return el("path", {d: T.skin, class: "bodyfill skinfill"}, g); }
  function sideDetail(g, T, surface, o) {
    o = o || {}; const svg = g.ownerSVGElement; defs(svg);
    const id = "sclip" + Math.random().toString(36).slice(2, 7);
    const cp = el("clipPath", {id}, el("defs", {}, g)); el("path", {d: T.skin}, cp);
    const root = el("g", {"clip-path": `url(#${id})`, class: "hdetail"}, g);
    if (o.bones && T.bones) { const bg = el("g", {class: "bones"}, root); Object.keys(T.bones).forEach(k => el("path", {d: T.bones[k], class: "bone"}, bg)); }
    { const rg = el("g", {filter: "url(#rimblur)"}, root); ["r1", "r2", "r3", "r4", "r5"].forEach(c => el("path", {d: T.skin, class: "rim " + c}, rg)); }
    // malleolus: shaded dome + the soft crescent under it
    const m = T.malleolus;
    el("ellipse", {cx: m.cx, cy: m.cy + .25, rx: m.rx * 1.15, ry: m.ry * 1.1, fill: "url(#padLo)", class: "pad mal-shade"}, root);
    el("ellipse", {cx: m.cx, cy: m.cy, rx: m.rx, ry: m.ry, fill: "url(#malG)", class: "pad mal"}, root);
    { const pts = []; for (let i = 0; i <= 24; i++) { const t = Math.PI * (.08 + .84 * i / 24); pts.push(`${(m.cx + m.rx * Math.cos(t)).toFixed(3)} ${(m.cy + m.ry * Math.sin(t) * .96).toFixed(3)}`); }
      el("path", {d: "M" + pts.join("L"), class: "malline"}, root); }
    T.tendons.forEach(d => el("path", {d, class: "tendon"}, root));
    (T.creases || []).forEach(d => el("path", {d, class: "crease"}, root));
    el("path", {d: T.skin, class: "skinedge"}, root);
    if (T.nail) el("path", {d: T.nail, class: "nailp"}, g);
    return root;
  }
  window.ART = {defs, handOutline, handFill, handDetail, footOutline, footFill, footDetail, footmOutline: sideOutline, footmFill: sideFill, footmDetail: sideDetail, footlOutline: sideOutline, footlFill: sideFill, footlDetail: sideDetail, el};
})();
