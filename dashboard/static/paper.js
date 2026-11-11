"use strict";
// ============================================================================
// Mô phỏng Li et al. 2026 (Animals 16:2796) trong tab So sánh:
//   T = (S−D)/(L−D) · ΔS = S_B − S_A · R = ΔS/(S_A + ε) · vùng 594–620 & 660–675 nm
//   Hình 7 (phổ TB theo ngày), Hình 8 (chênh lệch nhóm theo ngày), ΔS/R theo nhóm,
//   Hình 10/12 (sàng lọc batch & cặp batch bằng SVM + AUC) + kiểm định hoán vị toàn bộ.
// Dùng chung state/helper của index.html và compare.js (S, C, $, css, ...).
// ============================================================================
const PAPER_REGIONS = [[594, 620], [660, 675]];
const PG = {all: null, male: t("Trống"), female: t("Mái"), infertile: t("Không phôi"), fertile: t("Có phôi (Trống + Mái + Có phôi)")};
const PP = {a: null, b: null, basis: localGet("p.basis", "T"), g1: localGet("p.g1", "male"), g2: localGet("p.g2", "female"),
  nperm: localGet("p.nperm", "20"), eggs: localGet("p.eggs", "all"), dview: localGet("p.dview", "eggs"), heatKind: "all", job: null, result: null, timer: null};
// trứng dùng cho mọi hình và sàng lọc: tất cả, hoặc chỉ trứng đã nở (S.hatched, từ tab Tình trạng)
function paperEggs() { return PP.eggs === "hatched" ? allEggs().filter(x => S.hatched.includes(x.label)) : allEggs(); }

function inGroup(l, g) { const x = groupOf(l); return g === "fertile" ? x === "male" || x === "female" || x === "fertile" : x === g; }
function regionShapes() {
  return PAPER_REGIONS.map(([a, b]) => ({type: "rect", xref: "x", yref: "paper", x0: a, x1: b, y0: 0, y1: 1,
    fillcolor: css("--warning"), opacity: 0.13, line: {width: 0}, layer: "below"}));
}
function regionNotes() {
  return PAPER_REGIONS.map(([a, b]) => ({x: (a + b) / 2, y: 1, xref: "x", yref: "paper", yanchor: "bottom", showarrow: false,
    text: `${a}–${b}`, font: {size: 10, color: css("--muted")}}));
}
// Phổ cơ sở như bài báo: không làm mượt. T dùng lamp của chính batch; counts = trừ dark.
function pVec(s) {
  if (PP.basis !== "T") return s.counts;
  const L = C.data.batches[s.bi].lamp; if (!L) return null;
  const Lm = maxOf(L);
  return s.counts.map((v, i) => v == null || L[i] == null || L[i] < 0.03 * Lm ? null : v / L[i]);
}
function pSeries(egg, bi) { return egg.series.find(s => s.bi === bi); }
function pEps(items) {
  if (PP.basis === "T") return 0.02;
  const [lo, hi] = band();
  const m = items.map(x => { const s = pSeries(x.egg, PP.a); return s ? meanIn(s.counts, lo, hi) : null; }).filter(v => v != null).sort((a, b) => a - b);
  return m.length ? 0.02 * m[Math.floor(m.length / 2)] : 1;
}
function pDelta(egg, eps) {
  const sa = pSeries(egg, PP.a), sb = pSeries(egg, PP.b); if (!sa || !sb) return null;
  const A = pVec(sa), B = pVec(sb); if (!A || !B) return null;
  const dS = B.map((v, i) => v == null || A[i] == null ? null : v - A[i]);
  return {dS, R: dS.map((v, i) => v == null ? null : v / (A[i] + eps))};
}
const bLabel = (i) => { const b = C.data.batches[i]; return `B${String(b.batch_no).padStart(2, "0")} · ${fmt(b.age_h, 0)} h (D${fmt(b.age_h / 24, 1)})`; };

function paperDefaults() {
  const bs = C.data.batches, near = (h) => bs.reduce((bi, b, i) => Math.abs((b.age_h ?? 1e9) - h) < Math.abs((bs[bi].age_h ?? 1e9) - h) ? i : bi, 0);
  if (PP.a == null || PP.a >= bs.length) PP.a = near(36);      // D2 ≈ 24–48 h
  if (PP.b == null || PP.b >= bs.length) PP.b = near(108);     // D5 ≈ 96–120 h
  const opts = bs.map((_, i) => `<option value="${i}">${bLabel(i)}</option>`).join("");
  $("pA").innerHTML = $("pB").innerHTML = opts; $("pA").value = PP.a; $("pB").value = PP.b;
  const gopt = Object.entries(PG).filter(([k]) => k !== "all").map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
  $("pG1").innerHTML = $("pG2").innerHTML = gopt; $("pG1").value = PP.g1; $("pG2").value = PP.g2;
  $("pNperm").value = PP.nperm;
  document.querySelectorAll("#pBasis button").forEach(b => b.classList.toggle("on", b.dataset.v === PP.basis));
  document.querySelectorAll("#pEggs button").forEach(b => b.classList.toggle("on", b.dataset.v === PP.eggs));
  document.querySelectorAll("#pDView button").forEach(b => b.classList.toggle("on", b.dataset.v === PP.dview));
}

function renderPaper() {
  if (!C.data || $("tabCmp").hidden) return;
  paperDefaults();
  if (PP.eggs === "hatched" && S.hatchedSession !== S.data?.session) {
    ensureHatched().then(renderPaper, ex => toast(t("Lỗi: ") + ex.message)); return;
  }
  // như bài báo: tất cả trứng hoặc chỉ trứng đã nở (không phụ thuộc lựa chọn ở tab So sánh)
  const items = paperEggs(), wl = wlC(), nb = C.data.batches.length, [x0, x1] = [+$("xLo").value, +$("xHi").value];
  const yname = PP.basis === "T" ? t("T (truyền qua tương đối)") : t("Counts (trừ dark)");
  // trục y theo phân vị 1–99% trong cửa sổ λ: ở mép dải, lamp/S_A gần 0 làm T và R vọt lên vô nghĩa
  const lay = (yt, extra = {}) => baseLayout({xaxis: {title: {text: t("Bước sóng (nm)")}, range: [x0, x1]},
    yaxis: {title: {text: yt}, ...(extra.tr && robustRange(extra.tr.filter(t => t.name), x0, x1) ? {range: robustRange(extra.tr.filter(t => t.name), x0, x1)} : {})},
    shapes: [...regionShapes(), ...(extra.zero ? [{type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: {color: css("--axis"), width: 1}}] : [])],
    annotations: regionNotes(), margin: {l: 60, r: 8, t: 22, b: 44}, legend: {font: {size: 11, color: css("--ink-2")}}});

  // ---- Hình 7: phổ trung bình các trứng đang chọn theo từng batch ----
  const t7 = [];
  for (let bi = 0; bi < nb; bi++) {
    const vs = items.map(x => pSeries(x.egg, bi)).filter(Boolean).map(pVec).filter(Boolean);
    if (!vs.length) continue;
    t7.push({x: wl, y: meanSd(vs).mean, mode: "lines", name: `${bLabel(bi)} (n=${vs.length})`, line: {color: batchColor(bi, nb), width: 2},
      hovertemplate: `${bLabel(bi)}<br>%{x:.0f} nm: %{y:.4g}<extra></extra>`});
  }
  Plotly.react("pFig7", t7, lay(yname, {tr: t7}), PCFG);

  // ---- Hình 8: chênh lệch trung bình g1 − g2 theo từng batch ----
  const t8 = [];
  const G1 = items.filter(x => inGroup(x.label, PP.g1)), G2 = items.filter(x => inGroup(x.label, PP.g2) && !inGroup(x.label, PP.g1));
  for (let bi = 0; bi < nb; bi++) {
    const a = G1.map(x => pSeries(x.egg, bi)).filter(Boolean).map(pVec).filter(Boolean);
    const b = G2.map(x => pSeries(x.egg, bi)).filter(Boolean).map(pVec).filter(Boolean);
    if (a.length < 1 || b.length < 1) continue;
    const ma = meanSd(a).mean, mb = meanSd(b).mean;
    t8.push({x: wl, y: ma.map((v, i) => v == null || mb[i] == null ? null : v - mb[i]), mode: "lines", name: `${bLabel(bi)} (${a.length}/${b.length})`,
      line: {color: batchColor(bi, nb), width: 1.6}, hovertemplate: `${bLabel(bi)}<br>%{x:.0f} nm: %{y:.4g}<extra></extra>`});
  }
  $("pFig8Sub").textContent = `${PG[PP.g1]} − ${PG[PP.g2]} · ${t("{a}/{b} trứng", {a: G1.length, b: G2.length})}`;
  $("pFig8Empty").hidden = t8.length > 0; $("pFig8").hidden = !t8.length;
  if (t8.length) Plotly.react("pFig8", t8, lay(t("Δ trung bình ({b})", {b: PP.basis === "T" ? "T" : "counts"}), {zero: true, tr: t8}), PCFG);

  // ---- ΔS và R (A → B): từng trứng (màu theo nhóm nếu đã gán, chưa gán thì mỗi trứng 1 màu) hoặc TB ± SD mỗi nhóm ----
  const eps = pEps(items);
  $("pDeltaSub").textContent = `${bLabel(PP.a)} → ${bLabel(PP.b)} · ε = ${fmt(eps, PP.basis === "T" ? 2 : 0)}`;
  for (const [key, div] of [["dS", "pDS"], ["R", "pR"]]) {
    const tr = [];
    if (PP.dview === "eggs") {
      const anyGroup = items.some(x => x.group);
      items.forEach((x, i) => {
        const v = pDelta(x.egg, eps)?.[key]; if (!v) return;
        const col = anyGroup ? (x.group ? groupColor(x.group) : css("--muted")) : catColor(i % 8);
        const nm = `${x.label}${x.group ? " · " + GROUP_NAME[x.group] : ""}`;
        tr.push({x: wl, y: v, mode: "lines", name: nm, line: {color: col, width: 1.4, dash: !anyGroup && i >= 8 ? "dash" : "solid"},
          hovertemplate: `${esc(nm)}: %{y:.4g}<extra>%{x:.0f} nm</extra>`});
      });
      Plotly.react(div, tr, lay(key === "dS" ? "ΔS = S_B − S_A" : "R = ΔS / (S_A + ε)", {zero: true, tr}), PCFG);
      continue;
    }
    for (const G of groupsIn(items)) {
      const vs = G.items.map(x => pDelta(x.egg, eps)?.[key]).filter(Boolean);
      if (!vs.length) continue;
      const ms = meanSd(vs), col = G.k ? groupColor(G.k) : css("--muted");
      tr.push({x: wl, y: ms.mean.map((m, i) => m == null ? null : m - ms.sd[i]), mode: "lines", line: {width: 0}, hoverinfo: "skip", showlegend: false});
      tr.push({x: wl, y: ms.mean.map((m, i) => m == null ? null : m + ms.sd[i]), mode: "lines", line: {width: 0}, fill: "tonexty", fillcolor: hexA(col, 0.16), hoverinfo: "skip", showlegend: false});
      tr.push({x: wl, y: ms.mean, mode: "lines", name: `${G.name} (n=${vs.length})`, line: {color: col, width: 2.2},
        hovertemplate: `${G.name}: %{y:.4g}<extra>%{x:.0f} nm</extra>`});
    }
    Plotly.react(div, tr, lay(key === "dS" ? "ΔS = S_B − S_A" : "R = ΔS / (S_A + ε)", {zero: true, tr}), PCFG);
  }
  renderScreen();
}

// ---- Sàng lọc SVM (server, chạy nền) ----
async function runScreen() {
  if (PP.job) return;
  try {
    const j = await postJSON("/api/paper_screen", {src: S.src, end: C.end, g1: PP.g1, g2: PP.g2, basis: PP.basis,
      labels: PP.eggs === "hatched" ? S.hatched : null,
      lo: +$("xLo").value, hi: +$("xHi").value, n_perm: +PP.nperm});
    PP.job = j.id; PP.result = null; pollScreen();
  } catch (ex) { $("pStatus").innerHTML = `<span class="flag">${esc(ex.message)}</span>`; }
}
async function pollScreen() {
  clearTimeout(PP.timer);
  if (!PP.job) return;
  let j;
  try { j = await getJSON(`/api/job?id=${PP.job}`); } catch (ex) { PP.job = null; $("pStatus").textContent = ex.message; renderScreenCtl(); return; }
  $("pProg").style.width = (100 * j.done / Math.max(1, j.total)) + "%";
  if (j.result) PP.result = j.result;
  if (j.status === "running") { $("pStatus").textContent = `${tMsg(j.phase)} · ${j.done}/${j.total}`; PP.timer = setTimeout(pollScreen, 1500); }
  else { PP.job = null; $("pStatus").textContent = j.status === "done" ? t("Xong") : j.status === "cancelled" ? t("Đã huỷ") : t("Lỗi: ") + tMsg(j.error); }
  renderScreenCtl(); renderScreen();
}
function renderScreenCtl() { $("pRun").hidden = !!PP.job; $("pCancel").hidden = !PP.job; }

function renderScreen() {
  const r = PP.result; $("pScreenOut").hidden = !r; if (!r) return;
  const nb = r.batches.length, lab = r.batches.map(b => `B${String(b.batch_no).padStart(2, "0")}<br>${fmt(b.age_h, 0)} h`);
  // Hình 10: ma trận AUC cặp batch, đường chéo = AUC từng batch
  const z = r.batches.map((_, i) => r.batches.map((_, j) => i === j ? r.single[i] : r.pairs[PP.heatKind][i][j]));
  Plotly.react("pScrHeat", [{type: "heatmap", x: lab, y: lab, z, zmin: 0.3, zmax: 1, colorscale: seqScale(), xgap: 2, ygap: 2,
    text: z.map(row => row.map(v => v == null ? "" : v.toFixed(2))), texttemplate: "%{text}", textfont: {size: 11},
    hovertemplate: "%{y} × %{x}<br>AUC %{z:.3f}<extra></extra>", colorbar: {thickness: 10, outlinewidth: 0, tickfont: {color: css("--ink-2"), size: 11}}}],
    baseLayout({xaxis: {type: "category"}, yaxis: {type: "category", autorange: "reversed"}, margin: {l: 64, r: 8, t: 8, b: 48}}), {...PCFG, displayModeBar: false});
  // Hình 12: các bộ đặc trưng cho cặp A, B đang chọn
  const a = PP.a, b = PP.b, rows = [[t("S_A (chỉ batch A)"), r.single[a]], [t("S_B (chỉ batch B)"), r.single[b]],
    ...Object.entries(r.feature_sets).map(([k, nm]) => [tMsg(nm), a === b ? null : r.pairs[k][a][b]])];
  const nullP95 = r.perm?.p95;
  Plotly.react("pLolli", [{type: "bar", orientation: "h", y: rows.map(x => x[0]), x: rows.map(x => x[1] ?? 0), width: 0.35,
    marker: {color: css("--accent")}, text: rows.map(x => x[1] == null ? "—" : x[1].toFixed(3)), textposition: "outside", cliponaxis: false,
    hovertemplate: "%{y}: AUC %{x:.3f}<extra></extra>"}],
    baseLayout({xaxis: {range: [0, 1.08], title: {text: t("AUC (kiểm định chéo)")}}, yaxis: {autorange: "reversed", automargin: true},
      shapes: [{type: "line", x0: 0.5, x1: 0.5, yref: "paper", y0: 0, y1: 1, line: {color: css("--axis"), dash: "dot"}},
        ...(nullP95 ? [{type: "line", x0: nullP95, x1: nullP95, yref: "paper", y0: 0, y1: 1, line: {color: css("--critical"), dash: "dash", width: 1.5}}] : [])],
      margin: {l: 8, r: 40, t: 8, b: 44}, showlegend: false}), {...PCFG, displayModeBar: false});
  // Tóm tắt + kết luận
  const bst = r.best, bn = (i) => `B${String(r.batches[i].batch_no).padStart(2, "0")}`;
  const cfg = bst ? (bst.kind === "single" ? t("chỉ {b}", {b: bn(bst.a)}) : `${bn(bst.a)} + ${bn(bst.b)} · ${tMsg(r.feature_sets[bst.kind])}`) : "—";
  let verdict = "";
  if (r.perm) {
    const sig = r.perm.p < 0.05;
    verdict = `<div class="msg ${sig ? "ok" : ""}">${sig
      ? t("<b>Có tín hiệu</b>: AUC tốt nhất vượt {pct}% các lần xáo nhãn (p = {p}).", {pct: fmt((1 - r.perm.p) * 100, 0), p: fmt(r.perm.p, 3)})
      : t("<b>Chưa đủ bằng chứng</b>: với nhãn bị xáo trộn ngẫu nhiên, AUC lớn nhất đã đạt TB {m} (95%: {p95}), nên AUC {auc} có thể chỉ do may rủi (p = {p}).", {m: fmt(r.perm.mean, 2), p95: fmt(r.perm.p95, 2), auc: fmt(bst?.auc, 2), p: fmt(r.perm.p, 2)})}</div>`;
  } else verdict = `<div class="msg">${t("Chưa chạy kiểm định hoán vị — AUC tốt nhất được chọn trong {n} cấu hình nên luôn bị thổi phồng khi N nhỏ.", {n: r.n_configs})}</div>`;
  $("pSummary").innerHTML = `<div class="kv"><span>${t("Nhóm")}</span><b>${esc(PG[r.g1])} (${r.n1}) vs ${esc(PG[r.g2])} (${r.n2})</b>
    <span>${t("Cơ sở")}</span><b>${r.basis === "T" ? "T" : "Counts"} · ${END_LABEL[r.end]} · ${r.range[0]}–${r.range[1]} nm</b>
    <span>${t("Tốt nhất")}</span><b>${esc(cfg)} · AUC ${fmt(bst?.auc, 3)} (n=${bst?.n ?? "—"})</b>
    ${r.perm ? `<span>${t("Max-AUC ngẫu nhiên")}</span><b>${t("TB {m} · 95%: {p95} · {n} lần", {m: fmt(r.perm.mean, 3), p95: fmt(r.perm.p95, 3), n: r.perm.n})}</b>` : ""}</div>${verdict}`;
}

// ---- Đặc trưng vùng bước sóng của bài báo (thêm vào bảng/scatter đặc trưng) ----
FEATS.push(
  {k: "dS_r1", name: t("ΔS trung bình 594–620 nm (batch A → B của mục bài báo)"), short: "ΔS 594–620", d: 4},
  {k: "dS_r2", name: t("ΔS trung bình 660–675 nm (batch A → B)"), short: "ΔS 660–675", d: 4},
  {k: "R_r1", name: t("R trung bình 594–620 nm (batch A → B)"), short: "R 594–620", d: 3},
  {k: "R_r2", name: t("R trung bình 660–675 nm (batch A → B)"), short: "R 660–675", d: 3},
);
$("cFx").innerHTML = $("cFy").innerHTML = FEATS.map(f => `<option value="${f.k}" title="${esc(f.name)}">${f.short}</option>`).join("");
$("cFx").value = C.fx; $("cFy").value = C.fy;
function paperFeatures(rows) {
  if (PP.a == null) return;
  const eps = pEps(rows), wl = wlC();
  const reg = (arr, [a, b]) => { let s = 0, n = 0; wl.forEach((w, i) => { if (w >= a && w <= b && arr[i] != null) { s += arr[i]; n++; } }); return n ? s / n : null; };
  rows.forEach(r => { const d = pDelta(r.egg, eps); if (!d) return;
    r.f.dS_r1 = reg(d.dS, PAPER_REGIONS[0]); r.f.dS_r2 = reg(d.dS, PAPER_REGIONS[1]);
    r.f.R_r1 = reg(d.R, PAPER_REGIONS[0]); r.f.R_r2 = reg(d.R, PAPER_REGIONS[1]); });
}

// ---- Events ----
$("pA").addEventListener("change", e => { PP.a = +e.target.value; renderPaper(); });
$("pB").addEventListener("change", e => { PP.b = +e.target.value; renderPaper(); });
$("pG1").addEventListener("change", e => { PP.g1 = e.target.value; localSet("p.g1", PP.g1); renderPaper(); });
$("pG2").addEventListener("change", e => { PP.g2 = e.target.value; localSet("p.g2", PP.g2); renderPaper(); });
$("pNperm").addEventListener("change", e => { PP.nperm = e.target.value; localSet("p.nperm", PP.nperm); });
$("pDView").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return;
  PP.dview = b.dataset.v; localSet("p.dview", PP.dview); renderPaper(); });
$("pEggs").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return;
  PP.eggs = b.dataset.v; localSet("p.eggs", PP.eggs); PP.result = null; renderPaper(); });
$("pBasis").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; PP.basis = b.dataset.v; localSet("p.basis", PP.basis); renderPaper(); });
$("pHeatKind").addEventListener("change", e => { PP.heatKind = e.target.value; renderScreen(); });
$("pRun").addEventListener("click", runScreen);
$("pCancel").addEventListener("click", () => PP.job && postJSON("/api/job_cancel", {id: PP.job}));
$("pInfo").addEventListener("click", () => openModal("paperModal"));
