"use strict";
// ============================================================================
// Tab "So sánh": chọn N trứng, gán nhóm Trống / Mái / Không phôi, so sánh
// hình dạng phổ, đường phát triển, khác biệt giữa nhóm và đặc trưng từng trứng.
// Dùng chung state/helper của index.html (S, $, css, smooth, baseLayout, ...).
// ============================================================================
const GROUPS = [
  {k: "male", name: t("Trống"), slot: 0},
  {k: "female", name: t("Mái"), slot: 1},
  {k: "infertile", name: t("Không phôi"), slot: 2},
  {k: "fertile", name: t("Có phôi"), slot: 3},     // có phôi, chưa rõ giới
];
const GROUP_NAME = Object.fromEntries(GROUPS.map(g => [g.k, g.name]));
const C = {
  cache: new Map(), data: null, sel: null,
  end: localGet("c.end", "blunt"), batch: "auto", mode: localGet("c.mode", "norm"),
  get smooth() { return S.smooth; }, view: localGet("c.view", "eggs"), grow: localGet("c.grow", "dA"),
  fx: localGet("c.fx", "pc1"), fy: localGet("c.fy", "pc2"), sort: {k: "label", dir: 1},
};
S.tags = {}; S.tagsSession = null; S.tab = "egg";
S.hatched = []; S.hatchedSession = null;   // trứng có tình trạng hiện tại "Đã nở" (tab Tình trạng)

const catColor = (i) => css("--cat").split(",")[i].trim();
const groupOf = (l) => S.tags[l] || null;
function groupColor(g) { const G = GROUPS.find(x => x.k === g); return G ? catColor(G.slot) : css("--muted"); }

// ----------------------------------------------------------------------------
// Nhóm (tags) - lưu trên server theo session
// ----------------------------------------------------------------------------
async function ensureTags(force = false) {
  if (!S.src || (!force && S.tagsSession === S.data?.session)) return;
  const j = await getJSON(`/api/tags?src=${enc(S.src)}`);
  S.tags = j.groups || {}; S.tagsSession = j.session;
}
async function ensureHatched(force = false) {
  if (!S.src || (!force && S.hatchedSession === S.data?.session)) return S.hatched;
  const j = await getJSON(`/api/hatched?src=${enc(S.src)}`);
  S.hatched = j.labels || []; S.hatchedSession = j.session;
  return S.hatched;
}
async function setTags(changes) {
  const j = await postJSON("/api/tags", {src: S.src, groups: changes});
  S.tags = j.groups; S.tagsSession = j.session;
  renderEggList(); eggGroupHook();
  refreshTab();
}
function groupDot(l) {
  const g = groupOf(l);
  return g ? `<span class="gdot" style="background:${groupColor(g)}" title="${esc(GROUP_NAME[g])}"></span>` : "";
}
function groupSelect(l, attr) {
  const g = groupOf(l) || "";
  return `<select class="gsel" ${attr}><option value="">${t("Chưa gán")}</option>${GROUPS.map(x => `<option value="${x.k}" ${x.k === g ? "selected" : ""}>${x.name}</option>`).join("")}</select>`;
}
// nhóm của trứng đang xem ở tab "Từng trứng"
function eggGroupHook() {
  const box = $("eggGroup"); if (!box || !S.egg) return;
  box.innerHTML = groupSelect(S.egg.label, `id="eggGroupSel" aria-label="${esc(t("Nhóm"))}"`);
  $("eggGroupSel").onchange = (e) => setTags({[S.egg.label]: e.target.value || null});
}
// gọi từ applySummary (index.html) mỗi khi có tóm tắt session mới
async function onSummary() {
  try { await ensureTags(); } catch {}
  renderEggList(); eggGroupHook();
  if (typeof shapeOnSummary === "function") shapeOnSummary();
  if (S.tab === "status") renderStatus();
  else if (S.tab === "sex") renderSex();
  else if (S.tab !== "egg" && S.tab !== "shape") { await loadCompare(); }
}

// ----------------------------------------------------------------------------
// Tabs
// ----------------------------------------------------------------------------
const TAB_PANES = {egg: "tabEgg", cmp: "tabCmp", unsup: "tabUnsup", ml: "tabMl", shape: "tabShape", status: "tabStatus", sex: "tabSex"};
// Tab "Phân tích" = 1 nút trên thanh tiêu đề, chọn câu hỏi ở #anaBar; mỗi câu hỏi vẫn là một khung riêng (S.tab = ml / sex / paper / unsup)
const ANA = ["ml", "sex", "unsup"];
const ANA_INTRO = {
  ml: t("Trứng không phôi gần như không đổi theo ngày ấp. Xếp hạng trứng theo mức phát triển của phổ; gán nhãn khi chắc chắn để mô hình học thêm."),
  sex: t("Chỉ xét trứng đã nở, không dùng nhãn: phổ có tự chia thành 2 nhóm rõ hơn ngẫu nhiên không? Kết luận ở khung bên trái; chi tiết kỹ thuật thu gọn ở cuối."),
  unsup: t("Công cụ khám phá cho người dùng quen phân cụm: tự chọn dữ liệu, thuật toán, số cụm."),
};
S.ana = ANA.includes(localGet("ana", "")) ? localGet("ana", "") : "ml";
function setTab(t) {
  if (t === "ana") t = S.ana;
  if (ANA.includes(t)) { S.ana = t; localSet("ana", t); }
  S.tab = t;
  const ana = ANA.includes(t);
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === t || (ana && b.dataset.tab === "ana")));
  $("anaBar").hidden = !ana;
  if (ana) {
    document.querySelectorAll("#anaSeg button").forEach(b => b.classList.toggle("on", b.dataset.ana === t));
    $("anaIntro").textContent = ANA_INTRO[t];
  }
  Object.entries(TAB_PANES).forEach(([k, id]) => { if ($(id)) $(id).hidden = k !== t; });
  if (t === "egg") { if (S.egg) { renderSpec(); renderTS(); renderMap(); } }
  else if (t === "shape") renderShape();
  else if (t === "status") renderStatus();
  else if (t === "sex") renderSex();
  else loadCompare();
}
// vẽ lại tab đang mở (sau khi đổi nhóm, đầu đo, ...)
function refreshTab() {
  if (S.tab === "shape") return renderShape();
  if (S.tab === "status") return renderStatus();
  if (S.tab === "sex") return renderSex();
  if (!C.data) return;
  if (S.tab === "cmp") { renderPicker(); renderCompare(); if (typeof renderPaper === "function") renderPaper(); }
  else if (S.tab === "unsup" && typeof renderUnsup === "function") renderUnsup();
  else if (S.tab === "ml" && typeof renderMl === "function") renderMl();
}
function setEnd(v) {
  C.end = v; localSet("c.end", v);
  document.querySelectorAll(".endseg button").forEach(x => x.classList.toggle("on", x.dataset.v === v));
  loadCompare();
}

// ----------------------------------------------------------------------------
// Dữ liệu
// ----------------------------------------------------------------------------
async function loadCompare() {
  if (!S.src || !S.data) return;
  const key = `${S.src}|${C.end}|${S.sig}`;
  if (!C.cache.has(key)) {
    busy(t("Đang tải dữ liệu so sánh…"));
    try { C.cache.set(key, await getJSON(`/api/compare?src=${enc(S.src)}&end=${C.end}`)); }
    catch (ex) { toast(t("Lỗi: ") + ex.message); return; }
    finally { busy(); }
  }
  C.data = C.cache.get(key);
  const labels = Object.keys(C.data.eggs).filter(l => C.data.eggs[l].series.length);
  if (!C.sel || C.selSession !== C.data.session) { C.sel = new Set(labels); C.selSession = C.data.session; }
  else C.sel = new Set([...C.sel].filter(l => labels.includes(l)));
  const bsel = $("cBatch"), prev = bsel.value || "auto";
  bsel.innerHTML = `<option value="auto">${t("Mới nhất của từng trứng")}</option>` +
    C.data.batches.map((b, i) => `<option value="${i}">B${String(b.batch_no).padStart(2, "0")} · ${fmt(b.age_h, 0)} h</option>`).join("");
  bsel.value = [...bsel.options].some(o => o.value === prev) ? prev : "auto"; C.batch = bsel.value;
  try { await ensureTags(); } catch {}
  refreshTab();
}

const wlC = () => C.data.wavelengths;
function meanIn(arr, lo, hi) {
  const wl = wlC(); let s = 0, n = 0;
  for (let i = 0; i < wl.length; i++) if (wl[i] >= lo && wl[i] <= hi && arr[i] != null && isFinite(arr[i])) { s += arr[i]; n++; }
  return n ? s / n : null;
}
const maxOf = (a) => a.reduce((m, v) => v != null && isFinite(v) && v > m ? v : m, -Infinity);
function snapOf(egg) { return C.batch === "auto" ? egg.series.at(-1) : egg.series.find(s => s.bi === +C.batch); }

// Vector phổ của 1 phép đo theo đại lượng đang chọn. raw = đúng ADC trong file, không làm mượt.
function cVec(egg, s, mode = C.mode) {
  if (mode === "raw") return s.raw;
  const c = smooth(s.counts, C.smooth), [lo, hi] = band();
  if (mode === "counts") return c;
  if (mode === "norm") { const m = meanIn(c, lo, hi); return c.map(v => v == null || !m ? null : v / m); }
  if (mode === "dA") {
    const c0 = smooth(egg.series[0].counts, C.smooth), thr = Math.max(MIN_COUNTS, 0.04 * maxOf(c0));
    return c.map((v, i) => v != null && c0[i] != null && v > thr && c0[i] > thr ? Math.log10(c0[i] / v) : null);
  }
  const L = smooth(C.data.batches[s.bi].lamp, C.smooth); if (!L) return null;
  const Lm = maxOf(L);
  return c.map((v, i) => {
    if (v == null || L[i] == null || L[i] < 0.03 * Lm) return null;
    const T = v / L[i]; return mode === "T" ? T : (T > 0 ? -Math.log10(T) : null);
  });
}
// Giá trị vô hướng cho đường phát triển
function cScalar(egg, s, kind = C.grow) {
  const [lo, hi] = band();
  switch (kind) {
    case "raw": return meanIn(s.raw, lo, hi);
    case "counts": return meanIn(s.counts, lo, hi);
    case "T": { const L = meanIn(C.data.batches[s.bi].lamp || [], lo, hi); const c = meanIn(s.counts, lo, hi); return L ? c / L : null; }
    case "dA": { const c0 = meanIn(egg.series[0].counts, lo, hi), c = meanIn(s.counts, lo, hi); return c0 > 0 && c > 0 ? Math.log10(c0 / c) : null; }
    case "rnir": { const r = meanIn(s.counts, 620, 680), n = meanIn(s.counts, 780, 880); return n ? r / n : null; }
    case "peak": return peakWl(s);
  }
}
function peakWl(s) {
  const c = smooth(s.counts, 11), wl = wlC(); let best = -Infinity, at = null;
  for (let i = 0; i < wl.length; i++) if (wl[i] >= 550 && wl[i] <= 1000 && c[i] != null && c[i] > best) { best = c[i]; at = wl[i]; }
  return at;
}
const ageS = (s) => s.age_h ?? ageOf(s.ts);
const MODE_C = {raw: t("Raw ADC (không xử lý)"), counts: t("Counts (trừ dark)"), norm: t("Hình dạng (counts / mean band)"), dA: t("ΔA so với lần đo đầu (OD)"), T: "T", A: "A (OD)"};
const GROW_C = {dA: "ΔA band (OD)", counts: "Counts band", raw: "Raw ADC band", T: "T band", rnir: "R/NIR (620–680 / 780–880)", peak: t("λ đỉnh (nm)")};

function allEggs() {
  return Object.keys(C.data.eggs).sort().map(l => ({label: l, egg: C.data.eggs[l], group: groupOf(l)})).filter(x => x.egg.series.length);
}
function selectedEggs() {
  return [...C.sel].sort().map(l => ({label: l, egg: C.data.eggs[l], group: groupOf(l)})).filter(x => x.egg?.series.length);
}
// Màu: theo nhóm; nếu chưa gán nhóm nào và ≤ 8 trứng -> mỗi trứng 1 màu (thứ tự cố định, không lặp)
function colorPlan(items) {
  const anyGroup = items.some(x => x.group);
  if (!anyGroup && items.length <= 8) { const m = new Map(items.map((x, i) => [x.label, catColor(i)])); return {by: "egg", color: (x) => m.get(x.label)}; }
  return {by: "group", color: (x) => groupColor(x.group)};
}
function groupsIn(items) {
  const out = [];
  for (const G of [...GROUPS, {k: null, name: t("Chưa gán")}]) {
    const m = items.filter(x => x.group === G.k);
    if (m.length) out.push({...G, items: m});
  }
  return out;
}
function meanSd(vecs) {
  const p = vecs[0].length, mean = new Array(p), sd = new Array(p);
  for (let i = 0; i < p; i++) {
    const v = vecs.map(a => a[i]).filter(x => x != null && isFinite(x));
    if (!v.length) { mean[i] = sd[i] = null; continue; }
    const m = v.reduce((a, b) => a + b, 0) / v.length;
    mean[i] = m; sd[i] = v.length > 1 ? Math.sqrt(v.reduce((a, b) => a + (b - m) ** 2, 0) / (v.length - 1)) : 0;
  }
  return {mean, sd, n: vecs.length};
}
function cohenD(a, b) { // a, b: mảng số
  a = a.filter(v => v != null && isFinite(v)); b = b.filter(v => v != null && isFinite(v));
  if (a.length < 2 || b.length < 2) return null;
  const m = (x) => x.reduce((s, v) => s + v, 0) / x.length, ma = m(a), mb = m(b);
  const va = a.reduce((s, v) => s + (v - ma) ** 2, 0) / (a.length - 1), vb = b.reduce((s, v) => s + (v - mb) ** 2, 0) / (b.length - 1);
  const sp = Math.sqrt(((a.length - 1) * va + (b.length - 1) * vb) / (a.length + b.length - 2));
  return sp > 0 ? (ma - mb) / sp : null;
}

// ----------------------------------------------------------------------------
// Render
// ----------------------------------------------------------------------------
function renderPicker() {
  const labels = Object.keys(C.data.eggs).filter(l => C.data.eggs[l].series.length).sort();
  const items = labels.map(l => ({label: l, group: groupOf(l)}));
  const plan = colorPlan(items.filter(x => C.sel.has(x.label)));
  $("cCount").textContent = `${C.sel.size}/${labels.length}`;
  $("cChips").innerHTML = [...GROUPS, {k: "", name: t("Chưa gán")}].map(G => {
    const n = items.filter(x => (x.group || "") === G.k).length;
    return n ? `<button class="chip x" data-gsel="${G.k}"><span class="gdot" style="background:${G.k ? groupColor(G.k) : css("--muted")}"></span>${G.name} ${n}</button>` : "";
  }).join("");
  $("cList").innerHTML = items.map(x => `
    <div class="pick-row${C.sel.has(x.label) ? " on" : ""}">
      <label><input type="checkbox" data-pick="${esc(x.label)}" ${C.sel.has(x.label) ? "checked" : ""}>
        <span class="sw" style="background:${C.sel.has(x.label) ? plan.color(x) : "transparent"}"></span>
        <b>${esc(x.label)}</b> <span class="muted small">id ${esc(C.data.eggs[x.label].egg_id ?? "?")}</span></label>
      ${groupSelect(x.label, `data-tag="${esc(x.label)}"`)}
    </div>`).join("");
}

function renderCompare() {
  if (!C.data || $("tabCmp").hidden) return;
  if (typeof paperDefaults === "function") paperDefaults();
  const items = selectedEggs(), plan = colorPlan(items), wl = wlC();
  const isRaw = C.mode === "raw";
  // ---------- phổ ----------
  const snaps = items.map(x => ({...x, s: snapOf(x.egg)})).filter(x => x.s);
  const traces = [];
  if (C.view === "groups") {
    for (const G of groupsIn(snaps)) {
      const ms = meanSd(G.items.map(x => cVec(x.egg, x.s)).filter(Boolean)), col = G.k ? groupColor(G.k) : css("--muted");
      const up = ms.mean.map((m, i) => m == null ? null : m + ms.sd[i]), lo = ms.mean.map((m, i) => m == null ? null : m - ms.sd[i]);
      traces.push({x: wl, y: lo, mode: "lines", line: {width: 0}, hoverinfo: "skip", showlegend: false, legendgroup: G.name});
      traces.push({x: wl, y: up, mode: "lines", line: {width: 0}, fill: "tonexty", fillcolor: hexA(col, 0.18), hoverinfo: "skip", showlegend: false, legendgroup: G.name});
      traces.push({x: wl, y: ms.mean, mode: "lines", name: `${G.name} (n=${G.items.length})`, legendgroup: G.name, line: {color: col, width: 2.5},
        hovertemplate: `${G.name}: %{y:.4g} ± %{customdata:.3g}<extra>%{x:.0f} nm</extra>`, customdata: ms.sd});
    }
  } else {
    for (const x of snaps) {
      const b = C.data.batches[x.s.bi];
      traces.push({x: wl, y: cVec(x.egg, x.s), type: "scattergl", mode: "lines", name: `${x.label}${x.group ? " · " + GROUP_NAME[x.group] : ""}`,
        line: {color: plan.color(x), width: 1.6}, opacity: snaps.length > 12 ? 0.75 : 1,
        hovertemplate: `${x.label} · B${b.batch_no} · ${fmt(ageS(x.s), 1)} h<br>%{x:.0f} nm: %{y:.4g}<extra></extra>`});
    }
  }
  const [blo, bhi] = band();
  $("cSpecSub").textContent = `${MODE_C[C.mode]} · ${END_LABEL[C.end]} · ${C.batch === "auto" ? t("lần đo mới nhất") : "B" + C.data.batches[+C.batch].batch_no} · ${t("{n} trứng", {n: snaps.length})}`;
  Plotly.react("cSpec", traces, baseLayout({
    xaxis: {title: {text: t("Bước sóng (nm)")}, range: isRaw ? [wl[0], wl.at(-1)] : [+$("xLo").value, +$("xHi").value]},
    yaxis: {title: {text: MODE_C[C.mode]}, ...(isRaw ? {} : (() => { const r = robustRange(traces.filter(t => t.name), +$("xLo").value, +$("xHi").value); return r ? {range: r} : {}; })())},
    legend: {font: {size: 11, color: css("--ink-2")}}, showlegend: traces.length <= 40, margin: {l: 60, r: 8, t: 8, b: 44},
    shapes: [{type: "rect", xref: "x", yref: "paper", x0: blo, x1: bhi, y0: 0, y1: 1, fillcolor: css("--accent"), opacity: 0.05, line: {width: 0}}],
  }), PCFG);

  // ---------- đường phát triển ----------
  const gt = [];
  const pts = items.map(x => ({...x, p: x.egg.series.map(s => ({x: ageS(s), y: cScalar(x.egg, s), s})).filter(p => p.y != null)}));
  if (C.view === "groups") {
    for (const G of groupsIn(pts)) {
      const col = G.k ? groupColor(G.k) : css("--muted");
      for (const x of G.items) gt.push({x: x.p.map(p => p.x), y: x.p.map(p => p.y), mode: "lines", line: {color: hexA(col, 0.35), width: 1}, hoverinfo: "skip", showlegend: false});
      const byB = new Map();
      G.items.forEach(x => x.p.forEach(p => { const a = byB.get(p.s.bi) || []; a.push(p); byB.set(p.s.bi, a); }));
      const rows = [...byB.entries()].sort((a, b) => a[0] - b[0]).map(([, a]) => ({x: a.reduce((s, p) => s + p.x, 0) / a.length, ys: a.map(p => p.y)}));
      gt.push({x: rows.map(r => r.x), y: rows.map(r => r.ys.reduce((s, v) => s + v, 0) / r.ys.length), mode: "lines+markers", name: `${G.name} (n=${G.items.length})`,
        line: {color: col, width: 3}, marker: {size: 8, color: col, line: {color: css("--surface"), width: 2}},
        error_y: {type: "data", array: rows.map(r => meanSd(r.ys.map(v => [v])).sd[0] ?? 0), color: hexA(col, 0.6), thickness: 1, width: 3},
        hovertemplate: `${G.name}: %{y:.4g}<br>%{x:.1f} h<extra></extra>`});
    }
  } else {
    for (const x of pts) gt.push({x: x.p.map(p => p.x), y: x.p.map(p => p.y), mode: "lines+markers", name: x.label,
      line: {color: plan.color(x), width: 1.6}, marker: {size: 6, color: plan.color(x)},
      customdata: x.p.map(p => [C.data.batches[p.s.bi].batch_no, fmtTs(p.s.ts)]),
      hovertemplate: `${x.label} · B%{customdata[0]} · %{customdata[1]}<br>%{x:.1f} h: %{y:.4g}<extra></extra>`});
  }
  $("cGrowSub").textContent = `${GROW_C[C.grow]}${["dA", "counts", "raw", "T"].includes(C.grow) ? ` · band ${blo}–${bhi} nm` : ""}`;
  Plotly.react("cGrowPlot", gt, baseLayout({xaxis: {title: {text: t("Tuổi ấp (h)")}}, yaxis: {title: {text: GROW_C[C.grow]}},
    showlegend: gt.filter(t => t.name).length <= 40, margin: {l: 60, r: 8, t: 8, b: 44}}), PCFG);

  renderEffect(snaps);
  renderFeatures(items, plan);
  if (typeof renderPaper === "function") renderPaper();
}
function hexA(hex, a) { if (!hex.startsWith("#")) return hex; const [r, g, b] = hexToRgb(hex); return `rgba(${r},${g},${b},${a})`; }

function renderEffect(snaps) {
  const wl = wlC(), gs = groupsIn(snaps).filter(G => G.k && G.items.length >= 2);
  const dashes = ["solid", "dash", "dot"], traces = [];
  for (let i = 0; i < gs.length; i++) for (let j = i + 1; j < gs.length; j++) {
    const A = gs[i].items.map(x => cVec(x.egg, x.s)), B = gs[j].items.map(x => cVec(x.egg, x.s));
    const d = wl.map((_, k) => cohenD(A.map(v => v?.[k]), B.map(v => v?.[k])));
    traces.push({x: wl, y: d, mode: "lines", name: `${gs[i].name} − ${gs[j].name}`, line: {color: css("--ink-2"), width: 2, dash: dashes[traces.length % 3]},
      hovertemplate: `${gs[i].name} − ${gs[j].name}: d = %{y:.2f}<extra>%{x:.0f} nm</extra>`});
  }
  $("cEffectCard").hidden = !traces.length;   // chỉ hiện khi đã gán ≥ 2 nhóm, mỗi nhóm ≥ 2 trứng
  if (!traces.length) return;
  const isRaw = C.mode === "raw";
  Plotly.react("cEffect", traces, baseLayout({
    xaxis: {title: {text: t("Bước sóng (nm)")}, range: isRaw ? [wl[0], wl.at(-1)] : [+$("xLo").value, +$("xHi").value]},
    yaxis: {title: {text: "Cohen's d"}, zeroline: true},
    shapes: [0.8, -0.8].map(v => ({type: "line", xref: "paper", x0: 0, x1: 1, y0: v, y1: v, line: {color: css("--axis"), width: 1, dash: "dot"}})),
    margin: {l: 60, r: 8, t: 30, b: 44}, legend: {orientation: "h", y: 1.02, yanchor: "bottom", x: 0},
  }), PCFG);
}

// ---------- đặc trưng từng trứng ----------
const FEATS = [
  {k: "bright", name: t("Độ sáng vỏ — counts band ở lần đo đầu"), short: t("Sáng vỏ"), d: 0},
  {k: "dA_last", name: t("ΔA band ở lần đo cuối (OD)"), short: t("ΔA cuối"), d: 3},
  {k: "dA_rate", name: t("Tốc độ ΔA band (OD/ngày, hồi quy tuyến tính)"), short: t("ΔA/ngày"), d: 3},
  {k: "rnir", name: t("Tỷ số R/NIR = counts 620–680 / 780–880 nm (batch đang chọn)"), short: "R/NIR", d: 3},
  {k: "rnir_chg", name: t("Thay đổi R/NIR (lần cuối − lần đầu)"), short: "ΔR/NIR", d: 3},
  {k: "peak", name: t("Bước sóng đỉnh của phổ (nm, batch đang chọn)"), short: t("λ đỉnh"), d: 0},
  {k: "peak_adc", name: t("Peak ADC (batch đang chọn)"), short: "Peak ADC", d: 0},
  {k: "pc1", name: t("PC1 — thành phần chính 1 của hình dạng phổ (band, batch đang chọn)"), short: "PC1", d: 2},
  {k: "pc2", name: t("PC2 — thành phần chính 2 của hình dạng phổ"), short: "PC2", d: 2},
];
function slope(xs, ys) {
  const n = xs.length; if (n < 2) return null;
  const mx = xs.reduce((a, b) => a + b, 0) / n, my = ys.reduce((a, b) => a + b, 0) / n;
  const sxx = xs.reduce((a, x) => a + (x - mx) ** 2, 0); if (!sxx) return null;
  return xs.reduce((a, x, i) => a + (x - mx) * (ys[i] - my), 0) / sxx;
}
// PCA qua ma trận Gram n×n (n = số trứng nhỏ) + Jacobi
function pca2(rows) {
  const n = rows.length; if (n < 3) return rows.map(() => [null, null]);
  const p = rows[0].length, mean = new Array(p).fill(0);
  rows.forEach(r => r.forEach((v, j) => mean[j] += v / n));
  const X = rows.map(r => r.map((v, j) => v - mean[j]));
  const G = X.map(a => X.map(b => a.reduce((s, v, j) => s + v * b[j], 0)));
  const V = G.map((_, i) => G.map((_, j) => +(i === j)));
  for (let sweep = 0; sweep < 60; sweep++) {
    let off = 0;
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) off += G[i][j] ** 2;
    if (off < 1e-18) break;
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      if (Math.abs(G[i][j]) < 1e-30) continue;
      const th = 0.5 * Math.atan2(2 * G[i][j], G[j][j] - G[i][i]), c = Math.cos(th), s = Math.sin(th);
      for (let k = 0; k < n; k++) { const a = G[k][i], b = G[k][j]; G[k][i] = c * a - s * b; G[k][j] = s * a + c * b; }
      for (let k = 0; k < n; k++) { const a = G[i][k], b = G[j][k]; G[i][k] = c * a - s * b; G[j][k] = s * a + c * b; }
      for (let k = 0; k < n; k++) { const a = V[k][i], b = V[k][j]; V[k][i] = c * a - s * b; V[k][j] = s * a + c * b; }
    }
  }
  const ev = G.map((r, i) => [r[i], i]).sort((a, b) => b[0] - a[0]);
  const tot = ev.reduce((s, e) => s + Math.max(e[0], 0), 0);
  C.pcVar = ev.slice(0, 2).map(e => tot ? e[0] / tot : 0);
  return rows.map((_, r) => ev.slice(0, 2).map(([lam, k]) => V[r][k] * Math.sqrt(Math.max(lam, 0))));
}
function featureRows(items) {
  const [lo, hi] = band();
  const rows = items.map(x => {
    const ser = x.egg.series, s0 = ser[0], sl = ser.at(-1), sn = snapOf(x.egg);
    const dA = ser.map(s => cScalar(x.egg, s, "dA"));
    const ok = ser.map((s, i) => [ageS(s), dA[i]]).filter(([a, v]) => a != null && v != null);
    const rn = (s) => s && cScalar(x.egg, s, "rnir");
    return {...x, f: {
      bright: meanIn(s0.counts, lo, hi), dA_last: dA.at(-1), dA_rate: (slope(ok.map(o => o[0]), ok.map(o => o[1])) ?? NaN) * 24,
      rnir: rn(sn), rnir_chg: rn(sl) != null && rn(s0) != null ? rn(sl) - rn(s0) : null,
      peak: sn ? peakWl(sn) : null, peak_adc: sn?.peak_adc, pc1: null, pc2: null}, snap: sn};
  });
  // PCA trên phổ hình dạng (counts/mean band) trong band, chỉ các trứng có phép đo ở batch đang chọn
  const wl = wlC(), idx = wl.map((w, i) => w >= lo && w <= hi ? i : -1).filter(i => i >= 0);
  const withSnap = rows.filter(r => r.snap);
  const mats = withSnap.map(r => { const v = cVec(r.egg, r.snap, "norm"); return idx.map(i => v[i] ?? 1); });
  pca2(mats).forEach(([a, b], i) => { withSnap[i].f.pc1 = a; withSnap[i].f.pc2 = b; });
  if (typeof paperFeatures === "function") paperFeatures(rows);
  if (typeof shapeFeatures === "function") shapeFeatures(rows);
  rows.forEach(r => Object.keys(r.f).forEach(k => { if (r.f[k] != null && !isFinite(r.f[k])) r.f[k] = null; }));
  return rows;
}
function renderFeatures(items, plan) {
  const rows = featureRows(items);
  // ---- scatter ----
  const F = Object.fromEntries(FEATS.map(f => [f.k, f]));
  const fx = F[C.fx], fy = F[C.fy], traces = [];
  const pcName = (f) => f.k.startsWith("pc") && C.pcVar ? `${f.short} (${fmt(C.pcVar[f.k === "pc1" ? 0 : 1] * 100, 0)}%)` : f.short;
  const groups = plan.by === "group" ? groupsIn(rows) : rows.map(r => ({k: null, name: r.label, items: [r], col: plan.color(r)}));
  for (const G of groups) {
    const pts = G.items.filter(r => r.f[fx.k] != null && r.f[fy.k] != null);
    if (!pts.length) continue;
    const col = G.col || (G.k ? groupColor(G.k) : css("--muted"));
    traces.push({x: pts.map(r => r.f[fx.k]), y: pts.map(r => r.f[fy.k]), text: pts.map(r => r.label), mode: "markers+text", name: G.name,
      textposition: "top center", textfont: {size: 10, color: css("--ink-2")},
      marker: {size: 10, color: col, line: {color: css("--surface"), width: 2}},
      hovertemplate: `%{text}${G.k ? " · " + G.name : ""}<br>${fx.short}: %{x:.4g}<br>${fy.short}: %{y:.4g}<extra></extra>`});
  }
  Plotly.react("cScatter", traces, baseLayout({xaxis: {title: {text: pcName(fx)}}, yaxis: {title: {text: pcName(fy)}},
    showlegend: traces.length > 1 && traces.length <= 12, margin: {l: 60, r: 8, t: 8, b: 44}}), PCFG);

  // ---- bảng ----
  const k = C.sort.k, dir = C.sort.dir;
  rows.sort((a, b) => {
    const va = k === "label" ? a.label : k === "group" ? (a.group || "~") : a.f[k], vb = k === "label" ? b.label : k === "group" ? (b.group || "~") : b.f[k];
    if (va == null) return 1; if (vb == null) return -1;
    return (va < vb ? -1 : va > vb ? 1 : 0) * dir;
  });
  const th = (key, txt, title = "") => `<th data-sort="${key}" title="${esc(title)}" class="sortable">${txt}${k === key ? (dir > 0 ? " ▲" : " ▼") : ""}</th>`;
  const gs = groupsIn(rows).filter(G => G.k);
  const sepCell = (f) => {
    let best = null;
    for (let i = 0; i < gs.length; i++) for (let j = i + 1; j < gs.length; j++) {
      const d = cohenD(gs[i].items.map(r => r.f[f.k]), gs[j].items.map(r => r.f[f.k]));
      if (d != null && (!best || Math.abs(d) > Math.abs(best.d))) best = {d, a: gs[i].name, b: gs[j].name};
    }
    return best ? `<td class="num${Math.abs(best.d) >= 0.8 ? " strong" : ""}" title="${esc(best.a)} − ${esc(best.b)}">${fmt(best.d, 2)}</td>` : `<td class="muted">—</td>`;
  };
  const statRows = gs.map(G => `<tr class="grp"><td colspan="2"><span class="gdot" style="background:${groupColor(G.k)}"></span>${esc(t("{g} · TB ± SD (n={n})", {g: G.name, n: G.items.length}))}</td>${FEATS.map(f => {
    const v = G.items.map(r => r.f[f.k]).filter(x => x != null); if (!v.length) return "<td></td>";
    const m = v.reduce((a, b) => a + b, 0) / v.length, sd = v.length > 1 ? Math.sqrt(v.reduce((a, b) => a + (b - m) ** 2, 0) / (v.length - 1)) : 0;
    return `<td class="num">${fmt(m, f.d)} ± ${fmt(sd, f.d)}</td>`;
  }).join("")}</tr>`).join("");
  const sepRow = gs.length >= 2 ? `<tr class="grp"><td colspan="2" title="${esc(t("Cohen's d lớn nhất giữa hai nhóm; |d| ≥ 0.8 = khác biệt lớn"))}">${esc(t("Mức tách nhóm |d| lớn nhất"))}</td>${FEATS.map(sepCell).join("")}</tr>` : "";
  $("cTable").innerHTML = `<thead><tr>${th("label", t("Trứng"))}${th("group", t("Nhóm"))}${FEATS.map(f => th(f.k, f.short, f.name)).join("")}</tr></thead>
    <tbody>${rows.map(r => `<tr><td><a class="link" data-open-egg="${esc(r.label)}">${esc(r.label)}</a></td><td>${r.group ? `<span class="gdot" style="background:${groupColor(r.group)}"></span>${GROUP_NAME[r.group]}` : `<span class="muted">—</span>`}</td>
      ${FEATS.map(f => `<td class="num">${fmt(r.f[f.k], f.d)}</td>`).join("")}</tr>`).join("")}</tbody>
    <tfoot>${statRows}${sepRow}</tfoot>`;
}

// ----------------------------------------------------------------------------
// Nhập nhóm hàng loạt: "5 mái", "0006, trống", "7 khong"
// ----------------------------------------------------------------------------
function parseGroup(w) {
  w = w.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/g, "d").toLowerCase().trim();
  if (/^(trong|male|m|duc|t|雄|公)$/.test(w)) return "male";
  if (/^(mai|female|f|cai|雌|母)$/.test(w)) return "female";
  if (/^(khong( phoi)?|khongphoi|infertile|none|x|0|kp|无胚|無胚)$/.test(w)) return "infertile";
  if (/^(co( phoi)?|cophoi|fertile|cp|有胚)$/.test(w)) return "fertile";
  if (/^(xoa|bo|-|clear|删除|刪除)$/.test(w)) return "";
  return undefined;
}
function importTags() {
  const width = Math.max(...Object.keys(C.data?.eggs || {"0000": 1}).map(l => l.length));
  const changes = {}, bad = [];
  $("cImportTxt").value.split(/\n+/).map(s => s.trim()).filter(Boolean).forEach(line => {
    const m = line.match(/^(\d+)[\s,;:\t]+(.+)$/);
    const g = m && parseGroup(m[2]);
    if (!m || g === undefined) { bad.push(line); return; }
    changes[String(+m[1]).padStart(width, "0")] = g || null;
  });
  if (bad.length) { $("cImportMsg").innerHTML = `<div class="msg err">${esc(t("Không hiểu {n} dòng: {l}", {n: bad.length, l: bad.slice(0, 5).join(" | ")}))}</div>`; return; }
  setTags(changes).then(() => { closeModal("importModal"); toast(t("Đã gán nhóm cho {n} trứng", {n: Object.keys(changes).length})); });
}

// ----------------------------------------------------------------------------
// Events
// ----------------------------------------------------------------------------
$("tabs").addEventListener("click", e => { const b = e.target.closest("[data-tab]"); if (b) setTab(b.dataset.tab); });
$("anaSeg").addEventListener("click", e => { const b = e.target.closest("[data-ana]"); if (b) setTab(b.dataset.ana); });
document.querySelectorAll(".endseg").forEach(seg => seg.addEventListener("click", e => { const b = e.target.closest("button"); if (b) setEnd(b.dataset.v); }));
$("cViewSeg").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; C.view = b.dataset.v; localSet("c.view", C.view);
  document.querySelectorAll("#cViewSeg button").forEach(x => x.classList.toggle("on", x === b)); renderCompare(); });
[["cMode", "mode"], ["cGrow", "grow"], ["cFx", "fx"], ["cFy", "fy"]].forEach(([id, k]) => $(id).addEventListener("change", e => { C[k] = e.target.value; localSet("c." + k, C[k]); renderCompare(); }));
$("cBatch").addEventListener("change", e => { C.batch = e.target.value; renderCompare(); });
$("cList").addEventListener("change", e => {
  const cb = e.target.closest("[data-pick]");
  if (cb) { cb.checked ? C.sel.add(cb.dataset.pick) : C.sel.delete(cb.dataset.pick); renderPicker(); renderCompare(); return; }
  const sel = e.target.closest("[data-tag]");
  if (sel) setTags({[sel.dataset.tag]: sel.value || null});
});
$("cAll").addEventListener("click", () => { C.sel = new Set(Object.keys(C.data.eggs).filter(l => C.data.eggs[l].series.length)); renderPicker(); renderCompare(); });
$("cNone").addEventListener("click", () => { C.sel.clear(); renderPicker(); renderCompare(); });
$("cHatched").addEventListener("click", async () => {
  let h; try { h = await ensureHatched(true); } catch (ex) { return toast(t("Lỗi: ") + ex.message); }
  const ls = h.filter(l => C.data.eggs[l]?.series.length);
  if (!ls.length) return toast(t("Chưa có trứng nào được ghi Đã nở ở tab Tình trạng"));
  C.sel = new Set(ls); renderPicker(); renderCompare();
});
$("cChips").addEventListener("click", e => {
  const b = e.target.closest("[data-gsel]"); if (!b) return;
  const g = b.dataset.gsel, ls = Object.keys(C.data.eggs).filter(l => (groupOf(l) || "") === g && C.data.eggs[l].series.length);
  const all = ls.every(l => C.sel.has(l));
  ls.forEach(l => all ? C.sel.delete(l) : C.sel.add(l)); renderPicker(); renderCompare();
});
$("cAssignBtn").addEventListener("click", () => {
  if (!C.sel.size) return toast(t("Chưa chọn trứng nào"));
  const g = $("cAssign").value; setTags(Object.fromEntries([...C.sel].map(l => [l, g || null])));
});
$("cImportBtn").addEventListener("click", () => { $("cImportMsg").innerHTML = ""; openModal("importModal"); $("cImportTxt").focus(); });
$("cImportGo").addEventListener("click", importTags);
$("cTable").addEventListener("click", e => {
  const h = e.target.closest("[data-sort]");
  if (h) { const k = h.dataset.sort; C.sort = {k, dir: C.sort.k === k ? -C.sort.dir : 1}; renderCompare(); return; }
  const a = e.target.closest("[data-open-egg]");
  if (a) { setTab("egg"); loadEgg(a.dataset.openEgg); window.scrollTo({top: 0}); }
});
(function initCompare() {
  $("cMode").value = C.mode; $("cGrow").value = C.grow;
  $("cFx").innerHTML = $("cFy").innerHTML = FEATS.map(f => `<option value="${f.k}" title="${esc(f.name)}">${f.short}</option>`).join("");
  $("cFx").value = C.fx; $("cFy").value = C.fy;
  document.querySelectorAll(".endseg button").forEach(x => x.classList.toggle("on", x.dataset.v === C.end));
  document.querySelectorAll("#cViewSeg button").forEach(x => x.classList.toggle("on", x.dataset.v === C.view));
})();
