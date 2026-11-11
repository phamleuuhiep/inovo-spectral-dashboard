"use strict";
// ============================================================================
// Tab "Hình dạng": đường viền trứng dựng từ chiều dài L / chiều rộng B (outputs_shape/*.csv, đo bằng src/egg_size.py)
// theo mô hình Hügelschäffer, và các tích phân trên đường viền (chu vi, diện tích, thể tích, diện tích vỏ).
// Server (shape.py) chỉ đọc số đo, bỏ lần đo lệch, lấy trung vị mỗi trứng và khớp w/L từ ảnh hiệu chuẩn.
// Dùng chung state/helper của index.html và compare.js (S, C, $, css, baseLayout, groupOf, groupsIn, cohenD, ...).
// ============================================================================
const SH = {data: null, key: null, loading: null, egg: localGet("sh.egg", ""), wL: null, unit: localGet("sh.unit", "mm"),
  fx: localGet("sh.fx", "L"), fy: localGet("sh.fy", "SI"), sort: {k: "label", dir: 1}, prof: {}};
const SH_M = [
  {k: "L", short: "L (mm)", name: t("Chiều dài L (mm)"), d: 2},
  {k: "B", short: "B (mm)", name: t("Chiều rộng B (mm)"), d: 2},
  {k: "SI", short: "SI", name: t("Chỉ số hình dạng SI = B/L·100"), d: 1},
  {k: "P", short: t("Chu vi (mm)"), name: t("Chu vi hình chiếu P = ∮ds (mm)"), d: 1},
  {k: "A", short: t("Diện tích (mm²)"), name: t("Diện tích hình chiếu A = ½∮(x dy − y dx) (mm²)"), d: 0},
  {k: "V", short: t("Thể tích (cm³)"), name: t("Thể tích V = π∫y² dx (cm³)"), d: 2},
  {k: "S", short: t("Diện tích vỏ (cm²)"), name: t("Diện tích vỏ S = 2π∫y ds (cm²)"), d: 2},
];
const SH_F = Object.fromEntries(SH_M.map(m => [m.k, m]));

// ---------- mô hình Hügelschäffer ----------
function hugY(x, L, B, w) { const n = L * L - 4 * x * x; return n <= 0 ? 0 : B / 2 * Math.sqrt(n / (L * L + 8 * w * x + 4 * w * w)); }
// Đường viền khép kín, đầu tù bên trái (x < 0). x = −L/2·cos φ để điểm dày ở 2 đầu trứng.
function eggContour(L, B, w, n = 400) {
  const h = n >> 1, xs = [], ys = [];
  for (let i = 0; i <= h; i++) { const x = -L / 2 * Math.cos(Math.PI * i / h); xs.push(x); ys.push(hugY(x, L, B, w)); }
  return {x: [...xs, ...xs.slice(0, -1).reverse()], y: [...ys, ...ys.slice(0, -1).reverse().map(v => -v)]};
}
// Tích phân trên đa giác khép kín: chu vi, diện tích (Green), thể tích & diện tích vỏ (nửa trên quay quanh trục dài, nón cụt)
function eggIntegrals(L, B, w) {
  const {x, y} = eggContour(L, B, w, 2000);
  let P = 0, A = 0, V = 0, Sf = 0, xb = 0, yb = 0;
  for (let i = 0; i < x.length - 1; i++) {
    const dx = x[i + 1] - x[i], ds = Math.hypot(dx, y[i + 1] - y[i]);
    P += ds; A += x[i] * y[i + 1] - x[i + 1] * y[i];
    if (y[i] >= 0 && y[i + 1] >= 0) {
      const r1 = y[i], r2 = y[i + 1];
      V += Math.PI * Math.abs(dx) * (r1 * r1 + r1 * r2 + r2 * r2) / 3; Sf += Math.PI * (r1 + r2) * ds;
    }
    if (y[i] > yb) { yb = y[i]; xb = x[i]; }
  }
  return {P, A: Math.abs(A) / 2, V: V / 1000, S: Sf / 100, xb};
}

// ---------- dữ liệu ----------
async function loadShape(show = false) {
  if (!S.src || !S.data) return null;
  const key = `${S.src}|${S.sig}`;
  if (SH.key === key && SH.data) return SH.data;
  if (SH.loading?.key === key) {               // đang nạp nền (từ shapeOnSummary): chờ cùng promise
    if (show) busy(t("Đang đọc số đo kích thước…"));
    try { return await SH.loading.p; } finally { if (show) busy(); }
  }
  const p = (async () => {
    if (show) busy(t("Đang đọc số đo kích thước…"));
    try {
      const d = await getJSON(`/api/shape?src=${enc(S.src)}`);
      SH.data = d; SH.key = key;
      if (SH.wL == null || SH.wlSrc !== S.src) { SH.wL = d.fit?.wL ?? d.wl_default; SH.wlSrc = S.src; }
      computeShape();
      return d;
    } finally { if (show) busy(); SH.loading = null; }
  })();
  SH.loading = {key, p};
  return p;
}
function computeShape() {
  const d = SH.data; SH.prof = {};
  if (!d?.available) return;
  for (const [l, e] of Object.entries(d.eggs)) {
    const I = eggIntegrals(e.L, e.B, SH.wL * e.L);
    SH.prof[l] = {...e, SI: 100 * e.B / e.L, ...I};
  }
}
const shLabels = () => Object.keys(SH.prof).sort();

// ---------- vẽ ----------
async function renderShape() {
  if ($("tabShape").hidden) return;
  try { await loadShape(true); } catch (ex) { toast(t("Lỗi: ") + ex.message); return; }
  if ($("tabShape").hidden) return;
  try { await ensureTags(); } catch {}
  const d = SH.data, ok = d?.available && shLabels().length;
  $("shEmpty").hidden = !!ok; $("shBody").hidden = !ok;
  if (!ok) return;
  const ls = shLabels();
  if (!ls.includes(SH.egg)) SH.egg = ls.includes(S.label) ? S.label : ls[0];
  $("shEgg").innerHTML = ls.map(l => `<option value="${esc(l)}">${esc(t("Trứng {l}", {l}))}</option>`).join("");
  $("shEgg").value = SH.egg;
  $("shWL").value = SH.wL.toFixed(3);
  const fit = d.fit || {};
  $("shSrc").textContent = fit.source === "captures"
    ? t("w/L tự động = {w} (trung vị {n} ảnh hiệu chuẩn)", {w: fmt(fit.wL, 3), n: fit.fits.length})
    : t("w/L mặc định = {w}", {w: fmt(d.wl_default, 3)});
  document.querySelectorAll("#shUnit button").forEach(b => b.classList.toggle("on", b.dataset.v === SH.unit));
  const nUsed = d.meas.filter(m => m.used).length, nOk = d.meas.filter(m => m.status === "ok").length;
  $("shTblSub").textContent = t("{n} trứng · {s} lần đo ({u}/{o} lần đo ok được dùng)", {n: ls.length, s: d.sets.length, u: nUsed, o: nOk});
  drawShapeEgg(); drawShapeAll(); drawShapeTs(); drawShapeScatter(); drawShapeTable();
  loadPx(SH.egg);
}
function shColor(l) { const g = groupOf(l); return g ? groupColor(g) : css("--muted"); }

function drawShapeEgg() {
  const l = SH.egg, p = SH.prof[l]; if (!p) return;
  const w = SH.wL * p.L, tr = [];
  // từng lần đo: đường mảnh (lần bị loại: đỏ, chấm)
  SH.data.meas.filter(m => m.label === l && m.L && m.B && m.status === "ok").forEach(m => {
    const c = eggContour(m.L, m.B, SH.wL * m.L, 300);
    tr.push({x: c.x, y: c.y, mode: "lines", line: {color: m.used ? hexA(css("--accent"), 0.35) : css("--critical"), width: 1, dash: m.used ? "solid" : "dot"},
      hovertemplate: `${esc(m.set)} · ${fmt(m.L, 2)} × ${fmt(m.B, 2)} mm${m.used ? "" : " · " + t("bị loại")}<extra></extra>`, showlegend: false});
  });
  const el = eggContour(p.L, p.B, 0);
  tr.push({x: el.x, y: el.y, mode: "lines", name: t("Ellipse cùng L, B"), line: {color: css("--ink-2"), width: 1.2, dash: "dash"}, hoverinfo: "skip"});
  const c = eggContour(p.L, p.B, w);
  tr.push({x: c.x, y: c.y, mode: "lines", name: t("Hügelschäffer (trung vị)"), fill: "toself", fillcolor: hexA(css("--accent"), 0.12),
    line: {color: css("--accent"), width: 2.5}, hovertemplate: "x %{x:.1f} · y %{y:.1f} mm<extra></extra>"});
  const yb = hugY(p.xb, p.L, p.B, w);
  const ax = {zeroline: false, gridcolor: css("--grid")};
  Plotly.react("shPlot", tr, baseLayout({
    xaxis: {...ax, title: {text: "x (mm)"}}, yaxis: {...ax, title: {text: "y (mm)"}, scaleanchor: "x", scaleratio: 1},
    shapes: [{type: "line", x0: -p.L / 2, x1: p.L / 2, y0: 0, y1: 0, line: {color: css("--axis"), width: 1}},
      {type: "line", x0: p.xb, x1: p.xb, y0: -yb, y1: yb, line: {color: css("--axis"), width: 1, dash: "dot"}}],
    annotations: [{x: -p.L / 2, y: 0, xanchor: "right", text: t("đầu tù") + " ", showarrow: false, font: {size: 11, color: css("--muted")}},
      {x: p.L / 2, y: 0, xanchor: "left", text: " " + t("đầu nhọn"), showarrow: false, font: {size: 11, color: css("--muted")}}],
    legend: {orientation: "h", x: 0, y: 1.1}, margin: {l: 56, r: 12, t: 30, b: 44}}), PCFG);
  $("shEggSub").textContent = `${t("Trứng {l}", {l})}${groupOf(l) ? " · " + GROUP_NAME[groupOf(l)] : ""}`;
  const sd = (v) => v == null ? "" : ` (SD ${fmt(v, 2)})`;
  $("shKv").innerHTML = [
    [t("Chiều dài L"), `${fmt(p.L, 2)} mm${sd(p.L_sd)}`], [t("Chiều rộng B"), `${fmt(p.B, 2)} mm${sd(p.B_sd)}`],
    [t("Chỉ số hình dạng"), fmt(p.SI, 1)], [t("Chu vi hình chiếu"), `${fmt(p.P, 1)} mm`],
    [t("Diện tích hình chiếu"), `${fmt(p.A, 0)} mm²`], [t("Thể tích"), `${fmt(p.V, 2)} cm³`],
    [t("Diện tích vỏ"), `${fmt(p.S, 2)} cm²`], [t("Bề rộng lớn nhất lệch về đầu tù"), `${fmt(-p.xb, 2)} mm`],
    [t("Số lần đo dùng"), `${p.n_used}/${p.n_total}`],
  ].map(([k, v]) => `<span>${esc(k)}</span><b>${v}</b>`).join("");
}

function drawShapeAll() {
  const rel = SH.unit === "rel", tr = [];
  const ls = shLabels().sort((a, b) => (a === SH.egg) - (b === SH.egg));     // trứng đang chọn vẽ sau cùng (nằm trên)
  for (const l of ls) {
    const p = SH.prof[l], c = eggContour(p.L, p.B, SH.wL * p.L, 300), k = rel ? 1 / p.L : 1, on = l === SH.egg;
    tr.push({x: c.x.map(v => v * k), y: c.y.map(v => v * k), mode: "lines", name: l, showlegend: false,
      line: {color: on ? css("--accent") : shColor(l), width: on ? 3 : 1}, opacity: on ? 1 : 0.65,
      hovertemplate: `${esc(t("Trứng {l}", {l}))} · ${fmt(p.L, 1)} × ${fmt(p.B, 1)} mm · SI ${fmt(p.SI, 1)}<extra></extra>`});
  }
  const unit = rel ? "/ L" : "(mm)";
  Plotly.react("shAll", tr, baseLayout({xaxis: {title: {text: `x ${unit}`}, zeroline: false}, yaxis: {title: {text: `y ${unit}`}, scaleanchor: "x", zeroline: false},
    margin: {l: 56, r: 12, t: 8, b: 44}}), PCFG);
  const el = $("shAll"); el.removeAllListeners?.("plotly_click");
  el.on("plotly_click", ev => { const l = ev.points?.[0]?.data?.name; if (l) selectShapeEgg(l); });
}

function drawShapeTs() {
  const l = SH.egg, p = SH.prof[l], ms = SH.data.meas.filter(m => m.label === l && m.L && m.B && m.status === "ok" && m.age_h != null);
  const tr = [], col = {L: css("--s-blunt"), B: css("--s-pointed")};
  for (const k of ["L", "B"]) {
    const nm = k === "L" ? t("Chiều dài L") : t("Chiều rộng B");
    tr.push({x: ms.map(m => m.age_h), y: ms.map(m => m[k]), mode: "lines+markers", name: nm, line: {color: col[k], width: 1.5},
      marker: {size: ms.map(m => m.used ? 8 : 11), symbol: ms.map(m => m.used ? "circle" : "x"), color: ms.map(m => m.used ? col[k] : css("--critical")),
        line: {color: css("--surface"), width: 1}},
      text: ms.map(m => m.set + (m.used ? "" : " · " + t("bị loại"))), hovertemplate: `%{text}<br>${t("Tuổi ấp")} %{x:.1f} h<br>${nm}: %{y:.2f} mm<extra></extra>`});
  }
  Plotly.react("shTs", tr, baseLayout({xaxis: {title: {text: t("Tuổi ấp (h)")}}, yaxis: {title: {text: "mm"}},
    shapes: p ? ["L", "B"].map(k => ({type: "line", xref: "paper", x0: 0, x1: 1, y0: p[k], y1: p[k], line: {color: col[k], width: 1, dash: "dot"}})) : [],
    legend: {orientation: "h", x: 0, y: 1.12}, margin: {l: 56, r: 12, t: 26, b: 44}}), PCFG);
}

function drawShapeScatter() {
  const fx = SH_F[SH.fx], fy = SH_F[SH.fy], ls = shLabels(), items = ls.map(l => ({label: l, group: groupOf(l)}));
  const tr = groupsIn(items).map(G => ({x: G.items.map(x => SH.prof[x.label][fx.k]), y: G.items.map(x => SH.prof[x.label][fy.k]),
    text: G.items.map(x => x.label), mode: "markers+text", name: G.name, textposition: "top center", textfont: {size: 10, color: css("--ink-2")},
    marker: {size: G.items.map(x => x.label === SH.egg ? 15 : 10), color: G.k ? groupColor(G.k) : css("--muted"),
      line: {color: G.items.map(x => x.label === SH.egg ? css("--accent") : css("--surface")), width: G.items.map(x => x.label === SH.egg ? 3 : 1.5)}},
    hovertemplate: `%{text}<br>${esc(fx.short)}: %{x:.4g}<br>${esc(fy.short)}: %{y:.4g}<extra></extra>`}));
  Plotly.react("shScatter", tr, baseLayout({xaxis: {title: {text: fx.name}}, yaxis: {title: {text: fy.name}},
    showlegend: tr.length > 1, margin: {l: 60, r: 8, t: 8, b: 44}}), PCFG);
  const el = $("shScatter"); el.removeAllListeners?.("plotly_click");
  el.on("plotly_click", ev => { const l = ev.points?.[0]?.text; if (l) selectShapeEgg(l); });
}

function drawShapeTable() {
  const rows = shLabels().map(l => ({label: l, group: groupOf(l), f: SH.prof[l]}));
  const {k, dir} = SH.sort;
  rows.sort((a, b) => {
    const va = k === "label" ? a.label : k === "group" ? (a.group || "~") : a.f[k], vb = k === "label" ? b.label : k === "group" ? (b.group || "~") : b.f[k];
    return (va < vb ? -1 : va > vb ? 1 : 0) * dir;
  });
  const th = (key, txt, title = "") => `<th data-shsort="${key}" title="${esc(title)}" class="sortable">${esc(txt)}${k === key ? (dir > 0 ? " ▲" : " ▼") : ""}</th>`;
  const gs = groupsIn(rows).filter(G => G.k);
  const stat = gs.map(G => `<tr class="grp"><td colspan="2"><span class="gdot" style="background:${groupColor(G.k)}"></span>${esc(t("{g} · TB ± SD (n={n})", {g: G.name, n: G.items.length}))}</td>${SH_M.map(m => {
    const v = G.items.map(r => r.f[m.k]), mu = v.reduce((a, b) => a + b, 0) / v.length;
    const sd = v.length > 1 ? Math.sqrt(v.reduce((a, b) => a + (b - mu) ** 2, 0) / (v.length - 1)) : 0;
    return `<td class="num">${fmt(mu, m.d)} ± ${fmt(sd, m.d)}</td>`; }).join("")}<td></td></tr>`).join("");
  const sep = gs.length >= 2 ? `<tr class="grp"><td colspan="2" title="${esc(t("Cohen's d lớn nhất giữa hai nhóm; |d| ≥ 0.8 = khác biệt lớn"))}">${esc(t("Mức tách nhóm |d| lớn nhất"))}</td>${SH_M.map(m => {
    let best = null;
    for (let i = 0; i < gs.length; i++) for (let j = i + 1; j < gs.length; j++) {
      const dd = cohenD(gs[i].items.map(r => r.f[m.k]), gs[j].items.map(r => r.f[m.k]));
      if (dd != null && (!best || Math.abs(dd) > Math.abs(best))) best = dd;
    }
    return best == null ? `<td class="muted">—</td>` : `<td class="num${Math.abs(best) >= 0.8 ? " strong" : ""}">${fmt(best, 2)}</td>`; }).join("")}<td></td></tr>` : "";
  $("shTable").innerHTML = `<thead><tr>${th("label", t("Trứng"))}${th("group", t("Nhóm"))}${SH_M.map(m => th(m.k, m.short, m.name)).join("")}
      ${th("n_used", t("Lần đo"), t("Số lần đo dùng / tổng số"))}</tr></thead>
    <tbody>${rows.map(r => `<tr${r.label === SH.egg ? ' class="on"' : ""}><td><a class="link" data-sh-egg="${esc(r.label)}">${esc(r.label)}</a></td>
      <td>${r.group ? `<span class="gdot" style="background:${groupColor(r.group)}"></span>${GROUP_NAME[r.group]}` : `<span class="muted">—</span>`}</td>
      ${SH_M.map(m => `<td class="num">${fmt(r.f[m.k], m.d)}</td>`).join("")}<td class="num">${r.f.n_used}/${r.f.n_total}</td></tr>`).join("")}</tbody>
    <tfoot>${stat}${sep}</tfoot>`;
}
function selectShapeEgg(l) {
  if (!SH.prof[l]) return;
  SH.egg = l; localSet("sh.egg", l); $("shEgg").value = l;
  drawShapeEgg(); drawShapeAll(); drawShapeTs(); drawShapeScatter(); drawShapeTable();
  loadPx(l);
}
function drawShapeFits() {
  const f = SH.data?.fit;
  $("shFitBox").innerHTML = !f?.fits?.length ? `<p class="muted small">${esc(t("Không có ảnh hiệu chuẩn (outputs_shape/egg_size/captures) hoặc thiếu OpenCV / scipy — dùng w/L mặc định."))}</p>`
    : `<table class="ctab"><thead><tr><th>${esc(t("Ảnh hiệu chuẩn"))}</th><th>w/L</th><th>${esc(t("Sai số Hügelschäffer (px)"))}</th><th>${esc(t("Sai số ellipse (px)"))}</th></tr></thead>
      <tbody>${f.fits.map(x => `<tr><td>${esc(x.file)}</td><td class="num">${fmt(x.wL, 3)}</td><td class="num">${fmt(x.err_hug_px, 1)}</td><td class="num">${fmt(x.err_ellipse_px, 1)}</td></tr>`).join("")}</tbody></table>`;
}

// ---------- tích hợp với các tab khác ----------
// Đặc trưng hình dạng vào bảng / scatter "Đặc trưng từng trứng" (tab So sánh) và "Bảng đặc trưng" (tab Phân cụm)
FEATS.push(
  {k: "sh_L", name: t("Chiều dài trứng L (mm, trung vị các lần đo)"), short: "L (mm)", d: 2},
  {k: "sh_B", name: t("Chiều rộng trứng B (mm, trung vị các lần đo)"), short: "B (mm)", d: 2},
  {k: "sh_SI", name: t("Chỉ số hình dạng SI = B/L·100"), short: "SI", d: 1},
  {k: "sh_V", name: t("Thể tích trứng (cm³, mô hình Hügelschäffer)"), short: t("Thể tích (cm³)"), d: 2},
);
$("cFx").innerHTML = $("cFy").innerHTML = FEATS.map(f => `<option value="${f.k}" title="${esc(f.name)}">${esc(f.short)}</option>`).join("");
$("cFx").value = C.fx; $("cFy").value = C.fy;
function shapeFeatures(rows) {
  rows.forEach(r => { const p = SH.prof[r.label]; if (!p) return; r.f.sh_L = p.L; r.f.sh_B = p.B; r.f.sh_SI = p.SI; r.f.sh_V = p.V; });
}
// chip kích thước ở tab "Từng trứng"
function shapeEggHook() {
  const p = S.egg && SH.prof[S.egg.label]; if (!p) return;
  $("eggChips").insertAdjacentHTML("beforeend", `<button class="chip x" data-tab-go="shape" title="${esc(t("Mở tab Hình dạng trứng"))}">${fmt(p.L, 1)} × ${fmt(p.B, 1)} mm · SI ${fmt(p.SI, 0)} · ${fmt(p.V, 1)} cm³</button>`);
}
// nạp nền khi có session mới để chip và đặc trưng có sẵn; vẽ lại phần phụ thuộc khi xong
function shapeOnSummary() {
  loadShape().then(d => {
    if (S.tab === "shape") return renderShape();
    if (!d?.available) return;
    if (S.egg) renderEgg();
    if (S.tab === "cmp" || S.tab === "unsup") refreshTab();
  }).catch(() => {});
}

// ---------- events ----------
$("shEgg").addEventListener("change", e => selectShapeEgg(e.target.value));
$("shWL").addEventListener("change", e => {
  const v = +e.target.value; if (!(v >= 0 && v <= 0.2)) { e.target.value = SH.wL.toFixed(3); return; }
  SH.wL = v; computeShape(); renderShape(); if (S.egg) renderEgg();
});
$("shWLAuto").addEventListener("click", () => { SH.wL = SH.data?.fit?.wL ?? SH.data?.wl_default ?? 0.046; computeShape(); renderShape(); });
$("shUnit").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; SH.unit = b.dataset.v; localSet("sh.unit", SH.unit);
  document.querySelectorAll("#shUnit button").forEach(x => x.classList.toggle("on", x === b)); drawShapeAll(); });
$("shFx").innerHTML = $("shFy").innerHTML = SH_M.map(m => `<option value="${m.k}" title="${esc(m.name)}">${esc(m.short)}</option>`).join("");
$("shFx").value = SH.fx; $("shFy").value = SH.fy;
[["shFx", "fx"], ["shFy", "fy"]].forEach(([id, k]) => $(id).addEventListener("change", e => { SH[k] = e.target.value; localSet("sh." + k, SH[k]); drawShapeScatter(); }));
$("shTable").addEventListener("click", e => {
  const h = e.target.closest("[data-shsort]");
  if (h) { const k = h.dataset.shsort; SH.sort = {k, dir: SH.sort.k === k ? -SH.sort.dir : 1}; drawShapeTable(); return; }
  const a = e.target.closest("[data-sh-egg]"); if (a) selectShapeEgg(a.dataset.shEgg);
});
$("shHelp").addEventListener("click", () => { drawShapeFits(); openModal("shapeModal"); });
$("eggChips").addEventListener("click", e => { if (e.target.closest("[data-tab-go]")) { SH.egg = S.egg?.label || SH.egg; setTab("shape"); window.scrollTo({top: 0}); } });

// ---------- từ pixel ảnh: vi phân -> mép -> tích phân (shape.pixel_egg ở server, cache theo ảnh) ----------
SH.px = {}; SH.pxSeq = 0; SH.pxBatch = null;
async function loadPx(label) {
  if (!label || !S.src) return;
  const key = `${S.src}|${label}`, seq = ++SH.pxSeq;
  if (!SH.px[key]) {
    $("pxBody").hidden = true;
    $("pxStatus").textContent = t("Đang tìm mép trên ảnh của trứng {l}… (lần đầu khoảng 15 giây, sau đó dùng cache)", {l: label});
    try { SH.px[key] = await getJSON(`/api/shape_px?src=${enc(S.src)}&label=${enc(label)}`); }
    catch (ex) { if (seq === SH.pxSeq) $("pxStatus").textContent = t("Lỗi: ") + ex.message; return; }
  }
  if (seq !== SH.pxSeq || label !== SH.egg) return;
  drawPx(SH.px[key]);
}
const pxOk = (d) => (d.images || []).filter(r => !r.error);
function drawPx(d) {
  const ims = pxOk(d);
  if (!d.available || !ims.length) {
    $("pxBody").hidden = true;
    $("pxStatus").textContent = !d.available ? t("Chưa có hiệu chuẩn (outputs_shape/egg_size/calibration.json) — không đổi được pixel sang mm.")
      : t("Không tìm được đường viền trên ảnh nào của trứng này.") + (d.images?.[0]?.error ? " " + tMsg(d.images[0].error) : "");
    return;
  }
  const sel = $("pxBatch"), prev = SH.pxBatch;
  sel.innerHTML = ims.map(r => `<option value="${esc(r.folder)}">B${String(r.batch_no).padStart(2, "0")} · ${fmt(r.age_h, 0)} h</option>`).join("");
  sel.value = ims.some(r => r.folder === prev) ? prev : ims.at(-1).folder;
  const r = ims.find(x => x.folder === sel.value);
  const errs = (d.images || []).filter(x => x.error).length;
  $("pxStatus").textContent = errs ? t("{n} ảnh không tìm được đường viền", {n: errs}) : "";
  $("pxSub").textContent = t("{n} ảnh · {p}% điểm mép được dùng", {n: ims.length, p: fmt(r.ok_frac * 100, 0)});
  $("pxBody").hidden = false;
  $("pxImg").src = `/api/shape_pximg?src=${enc(S.src)}&b=${enc(r.folder)}&k=${enc(r.key)}`;
  $("pxImg").alt = t("Trứng {l}", {l: d.label});

  // đường viền mm: pixel, Hügelschäffer khớp vào pixel (w/L riêng), Hügelschäffer từ CSV (w/L chung)
  const p = SH.prof[d.label], tr = [];
  if (p) { const c = eggContour(p.L, p.B, SH.wL * p.L);
    tr.push({x: c.x, y: c.y, mode: "lines", name: t("Hügelschäffer (CSV, w/L chung)"), line: {color: css("--ink-2"), width: 1.3, dash: "dash"}, hoverinfo: "skip"}); }
  if (r.hug) { const c = eggContour(r.L, r.hug.B, r.hug.wL * r.L);
    tr.push({x: c.x, y: c.y, mode: "lines", name: t("Hügelschäffer khớp pixel (w/L = {w})", {w: fmt(r.hug.wL, 3)}), line: {color: css("--s-pointed"), width: 1.3, dash: "dot"}, hoverinfo: "skip"}); }
  tr.push({x: [...r.contour.u, r.contour.u[0]], y: [...r.contour.v, r.contour.v[0]], mode: "lines", name: t("Đường viền từ pixel"), fill: "toself",
    fillcolor: hexA(css("--accent"), 0.10), line: {color: css("--accent"), width: 2.5}, hovertemplate: "x %{x:.2f} · y %{y:.2f} mm<extra></extra>"});
  Plotly.react("pxPlot", tr, baseLayout({xaxis: {title: {text: "x (mm)"}, zeroline: false}, yaxis: {title: {text: "y (mm)"}, scaleanchor: "x", zeroline: false},
    legend: {orientation: "h", x: 0, y: 1.14}, margin: {l: 56, r: 12, t: 40, b: 44}}), PCFG);

  // vi phân: dI/dr theo 4 tia, 0 = mép
  const cols = css("--cat").split(",").map(x => x.trim());
  const dtr = r.profiles.flatMap((q, i) => [
    {x: q.dr, y: q.dI, mode: "lines", name: `θ = ${q.deg}°`, line: {color: cols[i], width: 2}, hovertemplate: `θ = ${q.deg}° · %{x:.1f} px: dI/dr %{y:.2f}<extra></extra>`},
    {x: q.dr, y: q.I, mode: "lines", yaxis: "y2", showlegend: false, line: {color: hexA(cols[i], 0.45), width: 1, dash: "dot"}, hovertemplate: `θ = ${q.deg}° · %{x:.1f} px: I %{y:.1f}<extra></extra>`}]);
  Plotly.react("pxDeriv", dtr, baseLayout({xaxis: {title: {text: t("r − r_mép (px)")}}, yaxis: {title: {text: "dI/dr"}},
    yaxis2: {title: {text: t("I = R − B (chấm)"), font: {color: css("--ink-2"), size: 12}}, overlaying: "y", side: "right", showgrid: false, tickfont: {color: css("--ink-2"), size: 11}},
    shapes: [{type: "line", x0: 0, x1: 0, yref: "paper", y0: 0, y1: 1, line: {color: css("--axis"), dash: "dot"}}],
    legend: {orientation: "h", x: 0, y: 1.14}, margin: {l: 56, r: 56, t: 30, b: 44}}), PCFG);

  // r(θ) từ vi phân, chuỗi Fourier và dr/dθ
  const P = r.polar, okI = P.ok.map((v, i) => v ? i : -1).filter(i => i >= 0), bad = P.ok.map((v, i) => v ? -1 : i).filter(i => i >= 0);
  Plotly.react("pxPolar", [
    {x: okI.map(i => P.th[i]), y: okI.map(i => P.r[i]), mode: "markers", name: t("mép từ vi phân"), marker: {size: 3, color: css("--good")}, type: "scattergl"},
    {x: bad.map(i => P.th[i]), y: bad.map(i => P.r[i]), mode: "markers", name: t("bị loại"), marker: {size: 5, color: css("--critical")}},
    {x: P.th, y: P.fit, mode: "lines", name: t("chuỗi Fourier"), line: {color: css("--accent"), width: 2}},
    {x: P.th, y: P.drdth, mode: "lines", name: "dr/dθ", yaxis: "y2", line: {color: css("--s-pointed"), width: 1.3, dash: "dash"}}],
    baseLayout({xaxis: {title: {text: "θ (°)"}, dtick: 90}, yaxis: {title: {text: "r (px)"}},
      yaxis2: {title: {text: "dr/dθ (px/rad)", font: {color: css("--ink-2"), size: 12}}, overlaying: "y", side: "right", showgrid: false, tickfont: {color: css("--ink-2"), size: 11}},
      legend: {orientation: "h", x: 0, y: 1.14}, margin: {l: 56, r: 56, t: 30, b: 44}}), PCFG);

  // bảng so sánh tích phân
  const mean = (k) => ims.reduce((a, x) => a + x[k], 0) / ims.length;
  const sd = (k) => { const m = mean(k); return ims.length > 1 ? Math.sqrt(ims.reduce((a, x) => a + (x[k] - m) ** 2, 0) / (ims.length - 1)) : 0; };
  const rows = SH_M.map(m => {
    const mv = p?.[m.k], dp = mv ? (r[m.k] - mv) / mv * 100 : null;
    return `<tr><td title="${esc(m.name)}">${esc(m.short)}</td><td class="num">${fmt(r[m.k], m.d)}</td><td class="num">${fmt(mean(m.k), m.d)} ± ${fmt(sd(m.k), m.d)}</td>
      <td class="num">${fmt(mv, m.d)}</td><td class="num">${dp == null ? "—" : (dp >= 0 ? "+" : "") + fmt(dp, 1) + "%"}</td></tr>`;
  });
  const wls = ims.filter(x => x.hug).map(x => x.hug.wL), wm = wls.reduce((a, b) => a + b, 0) / Math.max(1, wls.length);
  rows.push(`<tr><td title="${esc(t("Hügelschäffer khớp vào nửa bề rộng đo từ pixel"))}">w/L</td><td class="num">${fmt(r.hug?.wL, 3)}</td><td class="num">${fmt(wm, 3)}</td><td class="num">${fmt(SH.wL, 3)}</td><td></td></tr>`);
  $("pxTable").innerHTML = `<thead><tr><th></th><th>${esc(t("Pixel (ảnh này)"))}</th><th>${esc(t("Pixel TB ± SD các ảnh"))}</th><th>${esc(t("Hügelschäffer (CSV)"))}</th><th>${esc(t("Lệch"))}</th></tr></thead><tbody>${rows.join("")}</tbody>`;

  // thể tích từ pixel theo batch
  Plotly.react("pxTs", [{x: ims.map(x => x.age_h), y: ims.map(x => x.V), mode: "lines+markers", name: t("Thể tích"), line: {color: css("--accent"), width: 2},
    marker: {size: ims.map(x => x.folder === r.folder ? 12 : 8), color: css("--accent"), line: {color: css("--surface"), width: 1.5}},
    customdata: ims.map(x => [x.batch_no, x.L, x.B]), hovertemplate: `B%{customdata[0]} · %{x:.0f} h<br>V %{y:.2f} cm³ · L %{customdata[1]:.2f} · B %{customdata[2]:.2f} mm<extra></extra>`}],
    baseLayout({xaxis: {title: {text: t("Tuổi ấp (h)")}}, yaxis: {title: {text: t("Thể tích (cm³)")}},
      shapes: p ? [{type: "line", xref: "paper", x0: 0, x1: 1, y0: p.V, y1: p.V, line: {color: css("--ink-2"), width: 1, dash: "dot"}}] : [],
      showlegend: false, margin: {l: 56, r: 12, t: 8, b: 44}}), PCFG);
  const el = $("pxTs"); el.removeAllListeners?.("plotly_click");
  el.on("plotly_click", ev => { const i = ev.points?.[0]?.pointIndex; if (i != null) { SH.pxBatch = ims[i].folder; drawPx(d); } });
}
$("pxBatch").addEventListener("change", e => { SH.pxBatch = e.target.value; const d = SH.px[`${S.src}|${SH.egg}`]; if (d) drawPx(d); });
