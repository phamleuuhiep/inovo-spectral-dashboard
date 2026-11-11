"use strict";
// ============================================================================
// Tab "Phân cụm" (không giám sát): dựng ma trận trứng × biến ở client (phổ / ΔS / quỹ đạo / đặc trưng),
// server chạy PCA, t-SNE, MDS, K-Means, GMM, Ward, DBSCAN, Isolation Forest, LOF (unsup.py).
// Màu = cụm (thứ tự cố định), hình dạng điểm = nhóm đã gán.
// ============================================================================
const U = {
  rep: localGet("u.rep", "spec"), batch: "auto", mode: localGet("u.mode", "norm"), a: null, b: null, basis: localGet("u.basis", "counts"),
  traj: localGet("u.traj", "dA"), scale: localGet("u.scale2", localGet("u.scale", "1") === "0" ? "none" : "z"), method: localGet("u.method", "kmeans"),
  k: +localGet("u.k", 3), embed: localGet("u.embed", "pca"), eps: "", minS: 3, res: null, M: null, timer: null, seq: 0,
  da: null, db: null, dr: null,                       // ngày A, B và ngày gốc cho Δ
  scanDays: null, scanMetric: localGet("u.scanMetric", "silhouette"), scan: null,
  symBy: localGet("u.symBy", "ref"), refCache: null, track: null,
};
const G_SYMBOL = {male: "circle", female: "square", infertile: "diamond", fertile: "triangle-up"};
const clusterColor = (c) => c < 0 ? css("--muted") : catColor(c % 8);
const clusterName = (c) => c < 0 ? t("Nhiễu") : t("Cụm {n}", {n: c + 1});
const U_MODE = {norm: t("Hình dạng (counts / mean band)"), mm: t("Min–max 0–1 (từng phổ)"), A: "A (OD)", Ac: t("A bỏ offset (A − TB cửa sổ λ)"), T: "T", counts: t("Counts (trừ dark)"), raw: "Raw ADC", dA: t("ΔA so với lần đầu")};
const U_TRAJ = {dA: "ΔA band", counts: "Counts band", rnir: "R/NIR", peak: t("λ đỉnh")};

// ---------- ngày ấp ----------
// Ngày = ngày lịch của batch tính từ ngày bắt đầu ấp (bắt đầu 06/10 -> đo 08/10 là D2), các batch cùng ngày gộp lại.
const dateOnly = (ts) => { const x = new Date(ts); return Date.UTC(x.getFullYear(), x.getMonth(), x.getDate()); };
function uDays() {
  const st = S.data?.incubation_start, m = new Map();
  C.data.batches.forEach((b, bi) => {
    const d = st && b.started ? Math.round((dateOnly(b.started) - dateOnly(st)) / 864e5) : b.age_h != null ? Math.ceil(b.age_h / 24) : null;
    if (d == null) return;
    if (!m.has(d)) m.set(d, []);
    m.get(d).push(bi);
  });
  return [...m.entries()].sort((a, b) => a[0] - b[0]).map(([d, bis]) => ({d, bis}));
}
const dayName = (d) => `D${d}`;
function dayLabel(x) { return `${dayName(x.d)} · ${x.bis.map(bi => "B" + String(C.data.batches[bi].batch_no).padStart(2, "0")).join("+")}`; }

function uDefaults() {
  const bs = C.data.batches, near = (h) => bs.reduce((bi, b, i) => Math.abs((b.age_h ?? 1e9) - h) < Math.abs((bs[bi].age_h ?? 1e9) - h) ? i : bi, 0);
  if (U.a == null || U.a >= bs.length) U.a = near(36);
  if (U.b == null || U.b >= bs.length) U.b = near(108);
  const opts = bs.map((b, i) => `<option value="${i}">B${String(b.batch_no).padStart(2, "0")} · ${fmt(b.age_h, 0)} h</option>`).join("");
  $("uBatch").innerHTML = `<option value="auto">${t("Mới nhất của từng trứng")}</option>` + opts;
  $("uA").innerHTML = $("uB").innerHTML = opts;
  $("uBatch").value = U.batch; $("uA").value = U.a; $("uB").value = U.b;
  // ngày: mặc định A = D3, B = D4, gốc = D2 (hoặc các ngày có sẵn gần nhất)
  const days = uDays(), ds = days.map(x => x.d), pick = (want, i) => ds.includes(want) ? want : ds[Math.min(i, ds.length - 1)];
  if (!ds.includes(U.da)) U.da = pick(3, 2);
  if (!ds.includes(U.db)) U.db = pick(4, 3);
  if (!ds.includes(U.dr)) U.dr = pick(2, 1);
  $("uDA").innerHTML = $("uDB").innerHTML = $("uDR").innerHTML = days.map(x => `<option value="${x.d}">${esc(dayLabel(x))}</option>`).join("");
  $("uDA").value = U.da; $("uDB").value = U.db; $("uDR").value = U.dr;
  if (!U.scanDays || U.scanDays.some(d => !ds.includes(d))) U.scanDays = ds.filter(d => d >= 2);   // D1 chỉ có 1 batch rất sớm
  $("uScanDays").innerHTML = days.map(x => `<button class="chip x${U.scanDays.includes(x.d) ? " on" : ""}" data-sday="${x.d}" title="${esc(dayLabel(x))}">${dayName(x.d)}</button>`).join("");
  $("uScanMetric").value = U.scanMetric;
  $("uRep").value = U.rep; $("uMode").value = U.mode; $("uTraj").value = U.traj; $("uMethod").value = U.method;
  $("uK").value = U.k; $("uEmbed").value = U.embed; $("uScale").value = U.scale;
  document.querySelectorAll("#uBasis button").forEach(x => x.classList.toggle("on", x.dataset.v === U.basis));
  document.querySelectorAll("[data-urep]").forEach(el => el.hidden = !el.dataset.urep.split(" ").includes(U.rep));
  $("uKWrap").hidden = U.method === "dbscan"; $("uEpsWrap").hidden = U.method !== "dbscan";
}

// ---------- ma trận đầu vào ----------
function uVecBasis(s, basis) {
  if (basis !== "T") return s.counts;
  const L = C.data.batches[s.bi].lamp; if (!L) return null;
  const Lm = maxOf(L);
  return s.counts.map((v, i) => v == null || L[i] == null || L[i] < 0.03 * Lm ? null : v / L[i]);
}
const uWin = () => { const wl = wlC(), x0 = +$("xLo").value, x1 = +$("xHi").value; return {x0, x1, idx: wl.map((w, i) => w >= x0 && w <= x1 ? i : -1).filter(i => i >= 0)}; };
// Vector phổ 1 phép đo theo đại lượng đang chọn; "mm" = counts làm mượt đưa về 0–1 trong cửa sổ λ;
// "Ac" = A trừ trung bình của chính nó trong cửa sổ λ: bỏ phần dịch đều theo bước sóng (độ dày vỏ, cách đặt trứng, đèn)
function uVec(egg, s, idx) {
  if (U.mode === "T") return uVecBasis(s, "T");
  if (U.mode === "Ac") {
    const a = cVec(egg, s, "A"); if (!a) return null;
    let t = 0, n = 0; idx.forEach(i => { if (a[i] != null && isFinite(a[i])) { t += a[i]; n++; } });
    return n ? a.map(v => v == null ? null : v - t / n) : null;
  }
  if (U.mode !== "mm") return cVec(egg, s, U.mode);
  const c = smooth(s.counts, C.smooth); let lo = Infinity, hi = -Infinity;
  idx.forEach(i => { const v = c[i]; if (v != null && isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
  return hi > lo ? c.map(v => v == null ? null : (v - lo) / (hi - lo)) : null;
}
// Phổ 1 ngày của 1 trứng = trung bình các phép đo trong ngày, chỉ lấy cửa sổ λ
function dayVec(egg, day, idx) {
  const vs = day.bis.map(bi => egg.series.find(z => z.bi === bi)).filter(Boolean).map(s => uVec(egg, s, idx)).filter(Boolean);
  if (!vs.length) return null;
  return idx.map(i => { let s = 0, n = 0; vs.forEach(v => { if (v[i] != null && isFinite(v[i])) { s += v[i]; n++; } }); return n ? s / n : null; });
}
// Khối của cấu hình theo ngày: [[a, r], ...] = phổ ngày a, hoặc hiệu a − r
function dayBlocks(rep, a, b, r) {
  if (rep === "day") return [[a, null]];
  if (rep === "pair") return [[a, null], [b, null]];
  return [[a, null], [b, null], [a, r], [b, r]];
}
const blockName = ([a, r]) => r == null ? dayName(a) : `${dayName(a)} − ${dayName(r)}`;
// Trục nối tiếp các khối: mỗi khối dịch sang phải một đoạn span, ghi lại để vẽ vạch chia và tên khối
function concatAxis(blocks, wlv, x0, x1, y) {
  const span = x1 - x0 + 30;
  if (blocks.length === 1) return {type: "wl", values: wlv, title: `${t("Bước sóng (nm)")} · ${blockName(blocks[0])}`, y};
  return {type: "concat", values: blocks.flatMap((_, j) => wlv.map(w => w + j * span)), names: blocks.map(blockName), span, x0, x1,
    title: t("Bước sóng (nm) · các khối nối tiếp"), y};
}
function axisDecor(ax) {
  if (ax.type === "wl") return {xaxis: {}, shapes: regionShapes(), annotations: []};
  if (ax.type !== "concat") return {xaxis: ax.type === "feat" ? {type: "category"} : {}, shapes: [], annotations: []};
  const tv = [], tt = [], shapes = [], annotations = [];
  ax.names.forEach((nm, j) => {
    const o = j * ax.span;
    for (let w = Math.ceil(ax.x0 / 100) * 100; w <= ax.x1; w += 100) { tv.push(w + o); tt.push(String(w)); }
    regionShapes().forEach(s => shapes.push({...s, x0: s.x0 + o, x1: s.x1 + o}));
    if (j) shapes.push({type: "line", xref: "x", yref: "paper", x0: ax.x0 + o - 15, x1: ax.x0 + o - 15, y0: 0, y1: 1, line: {color: css("--axis"), width: 1}});
    annotations.push({x: (ax.x0 + ax.x1) / 2 + o, y: 1, xref: "x", yref: "paper", yanchor: "bottom", showarrow: false, text: `<b>${esc(nm)}</b>`, font: {size: 11, color: css("--ink-2")}});
  });
  return {xaxis: {tickvals: tv, ticktext: tt}, shapes, annotations};
}
// Ngắt đường ở chỗ nối giữa 2 khối để Plotly không vẽ đoạn nối ngang qua
function breakGaps(x, ...ys) {
  const X = [], Ys = ys.map(() => []);
  x.forEach((v, i) => {
    if (i && v - x[i - 1] > 10) { X.push((v + x[i - 1]) / 2); Ys.forEach(a => a.push(null)); }
    X.push(v); Ys.forEach((a, k) => a.push(ys[k][i]));
  });
  return [X, ...Ys];
}
function uMatrix() {
  const items = allEggs(), wl = wlC(), {x0, x1, idx} = uWin();
  const rows = [], labels = [];
  let axis;
  if (U.rep === "spec") {
    axis = {type: "wl", values: idx.map(i => wl[i]), title: t("Bước sóng (nm)"), y: U_MODE[U.mode]};
    for (const x of items) {
      const s = U.batch === "auto" ? x.egg.series.at(-1) : x.egg.series.find(z => z.bi === +U.batch); if (!s) continue;
      const v = uVec(x.egg, s, idx); if (!v) continue;
      rows.push(idx.map(i => v[i])); labels.push(x.label);
    }
  } else if (["day", "pair", "merge"].includes(U.rep)) {
    const days = Object.fromEntries(uDays().map(x => [x.d, x]));
    const blocks = dayBlocks(U.rep, U.da, U.db, U.dr);
    axis = concatAxis(blocks, idx.map(i => wl[i]), x0, x1, U_MODE[U.mode]);
    for (const x of items) {
      const cache = {}, get = (d) => d in cache ? cache[d] : (cache[d] = days[d] ? dayVec(x.egg, days[d], idx) : null);
      const parts = blocks.map(([a, r]) => { const A = get(a), R = r == null ? null : get(r);
        if (!A || (r != null && !R)) return null;
        return r == null ? A : A.map((v, i) => v == null || R[i] == null ? null : v - R[i]); });
      if (parts.some(p => !p)) continue;
      rows.push(parts.flat()); labels.push(x.label);
    }
  } else if (U.rep === "paper") {
    // Như Li et al. 2026: S_A, S_B, ΔS = S_B − S_A, R = ΔS / (S_A + ε) trên chính quả trứng (A = ngày sớm làm gốc)
    const days = Object.fromEntries(uDays().map(x => [x.d, x]));
    const got = items.map(x => ({x, A: days[U.da] && dayVec(x.egg, days[U.da], idx), B: days[U.db] && dayVec(x.egg, days[U.db], idx)})).filter(o => o.A && o.B);
    const scaleFree = ["T", "norm", "mm"].includes(U.mode);
    const lvl = got.map(o => { const v = o.A.filter(t => t != null); return v.reduce((a, b) => a + b, 0) / Math.max(1, v.length); }).sort((a, b) => a - b);
    U.pEps = scaleFree ? 0.02 : 0.02 * (lvl[Math.floor(lvl.length / 2)] || 1);     // bài báo: ε = 0.02 trên thang T
    axis = concatAxis([[U.da, null], [U.db, null], [U.db, U.da], [U.db, U.da]], idx.map(i => wl[i]), x0, x1, U_MODE[U.mode]);
    axis.names = [`D${U.da}`, `D${U.db}`, `ΔS = D${U.db} − D${U.da}`, `R = ΔS / (D${U.da} + ε)`];
    // bỏ bước sóng mà tín hiệu ngày gốc yếu (< 5% đỉnh của trung vị): ở mép dải, S_gốc ~ 0 làm R vọt lên vô nghĩa
    const med = idx.map((_, i) => { const v = got.map(o => o.A[i]).filter(t => t != null).sort((a, b) => a - b); return v.length ? v[Math.floor(v.length / 2)] : null; });
    const top = Math.max(...med.filter(v => v != null)), weak = med.map(v => v == null || v < 0.05 * top);
    for (const {x, A, B} of got) {
      const dS = B.map((v, i) => v == null || A[i] == null ? null : v - A[i]);
      const R = dS.map((v, i) => v == null ? null : v / (A[i] + U.pEps));
      const cut = (arr) => arr.map((v, i) => weak[i] ? null : v);
      rows.push([...cut(A), ...cut(B), ...cut(dS), ...cut(R)]); labels.push(x.label);
    }
  } else if (U.rep === "dS") {
    axis = {type: "wl", values: idx.map(i => wl[i]), title: t("Bước sóng (nm)"), y: `ΔS (${U.basis === "T" ? "T" : "counts"})`};
    for (const x of items) {
      const sa = x.egg.series.find(z => z.bi === U.a), sb = x.egg.series.find(z => z.bi === U.b); if (!sa || !sb) continue;
      const A = uVecBasis(sa, U.basis), B = uVecBasis(sb, U.basis); if (!A || !B) continue;
      rows.push(idx.map(i => A[i] == null || B[i] == null ? null : B[i] - A[i])); labels.push(x.label);
    }
  } else if (U.rep === "traj") {
    const bs = C.data.batches;
    axis = {type: "batch", values: bs.map(b => b.age_h), title: t("Tuổi ấp (h)"), y: U_TRAJ[U.traj]};
    for (const x of items) {
      const row = bs.map((_, bi) => { const s = x.egg.series.find(z => z.bi === bi); return s ? cScalar(x.egg, s, U.traj) : null; });
      if (row.filter(v => v != null).length < Math.max(3, bs.length / 2)) continue;
      rows.push(row); labels.push(x.label);
    }
  } else {
    const fr = featureRows(items);
    axis = {type: "feat", values: FEATS.map(f => f.short), title: t("Đặc trưng"), y: t("Giá trị (z-score)")};
    for (const r of fr) { rows.push(FEATS.map(f => r.f[f.k])); labels.push(r.label); }
  }
  return {rows, labels, axis};
}

// ---------- chạy ----------
function scheduleUnsup() { clearTimeout(U.timer); U.timer = setTimeout(runUnsup, 250); }
async function runUnsup() {
  const M = uMatrix(), seq = ++U.seq;
  $("uStatus").textContent = t("Đang chạy… {n} trứng × {p} biến", {n: M.rows.length, p: M.axis.values.length});
  if (M.rows.length < 4) { $("uStatus").textContent = t("Cần ít nhất 4 trứng có dữ liệu cho cấu hình này."); return; }
  if (U.rep === "pair" && U.da === U.db) toast(t("Ngày A và B trùng nhau"));
  if (U.rep === "merge" && (U.dr === U.da || U.dr === U.db)) toast(t("Ngày gốc trùng ngày A/B — khối Δ tương ứng bằng 0 và bị bỏ"));
  try {
    const res = await postJSON("/api/unsup", {X: M.rows, k: U.k, method: U.method, embed: U.embed, scale: U.scale,
      eps: U.eps ? +U.eps : null, min_samples: U.minS, groups: M.labels.map(l => groupOf(l))});
    if (seq !== U.seq) return;   // đã có yêu cầu mới hơn
    U.res = res; U.M = M; drawUnsup();
  } catch (ex) { $("uStatus").innerHTML = `<span class="flag">${esc(ex.message)}</span>`; }
}
function renderUnsup() {
  if (!C.data || $("tabUnsup").hidden) return;
  uDefaults(); runUnsup();
  if (U.scan && U.scan.data !== C.data) U.scan = null;   // đổi đầu đo / dữ liệu -> kết quả quét cũ không còn đúng
  if (U.track && U.track.data !== C.data) U.track = null;
  $("uSymBy").value = U.symBy;
  drawScan(); drawTrack();
}

// ---------- cụm tham chiếu ----------
// Ghi lại cụm của từng trứng ở một cấu hình (lưu trong trình duyệt theo session). Hình của trứng = cụm tham chiếu
// và không đổi; cụm của các lần chạy sau được khớp với cụm tham chiếu (cùng màu = cùng cụm).
const REF_SYMBOLS = ["circle", "square", "triangle-up", "diamond", "cross", "star", "x", "pentagon", "hexagram", "triangle-down"];
const refKey = () => "u.ref:" + (C.data?.session || "");
function getRef() {
  if (U.refCache?.key === refKey()) return U.refCache.ref;
  let ref = null; try { ref = JSON.parse(localGet(refKey(), "null")); } catch {}
  U.refCache = {key: refKey(), ref}; return ref;
}
function setRef(ref) { localSet(refKey(), JSON.stringify(ref)); U.refCache = {key: refKey(), ref}; }
const refSymbol = (c) => c == null ? "circle-open" : c < 0 ? "asterisk-open" : REF_SYMBOLS[c % REF_SYMBOLS.length];
const useRefSym = () => U.symBy === "ref" && !!getRef();
function symOf(l) { const ref = getRef(); return useRefSym() ? refSymbol(ref.map[l]) : G_SYMBOL[groupOf(l)] || "circle-open"; }

// ARI giữa 2 cách chia (mảng nhãn cùng thứ tự)
function ariJS(a, b) {
  const n = a.length, c2 = (x) => x * (x - 1) / 2; if (n < 2) return null;
  const ct = new Map(), ra = new Map(), rb = new Map(), inc = (m, k) => m.set(k, (m.get(k) || 0) + 1);
  a.forEach((x, i) => { inc(ct, x + "|" + b[i]); inc(ra, x); inc(rb, b[i]); });
  let sij = 0, sa = 0, sb = 0; ct.forEach(v => sij += c2(v)); ra.forEach(v => sa += c2(v)); rb.forEach(v => sb += c2(v));
  const exp = sa * sb / c2(n), mx = (sa + sb) / 2;
  return mx === exp ? 1 : (sij - exp) / (mx - exp);
}
// Khớp cụm hiện tại với cụm tham chiếu theo số trứng chung (tham lam, ô lớn nhất trước). Trả hàm id hiện tại -> id hiển thị.
function refMatcher(labels, cur) {
  const ref = getRef(); if (!ref) return (c) => c;
  const cnt = new Map();
  labels.forEach((l, i) => { const r = ref.map[l], c = cur[i]; if (r == null || r < 0 || c < 0) return; const k = c + "|" + r; cnt.set(k, (cnt.get(k) || 0) + 1); });
  const m = new Map(), usedR = new Set();
  [...cnt.entries()].sort((a, b) => b[1] - a[1]).forEach(([k]) => { const [c, r] = k.split("|").map(Number); if (!m.has(c) && !usedR.has(r)) { m.set(c, r); usedR.add(r); } });
  let next = Math.max(-1, ...Object.values(ref.map)) + 1;
  [...new Set(cur)].sort((a, b) => a - b).forEach(c => { if (c >= 0 && !m.has(c)) { while (usedR.has(next)) next++; m.set(c, next); usedR.add(next); } });
  return (c) => c < 0 ? -1 : m.get(c);
}
// So 1 kết quả phân cụm (nhãn trứng + cụm) với tham chiếu: ARI, % giữ cụm, trứng đổi cụm
function refCompare(labels, cur) {
  const ref = getRef(); if (!ref) return null;
  const f = refMatcher(labels, cur), idx = labels.map((l, i) => ref.map[l] != null ? i : -1).filter(i => i >= 0);
  if (idx.length < 2) return null;
  const A = idx.map(i => ref.map[labels[i]]), B = idx.map(i => f(cur[i]));
  const moved = idx.filter((i, j) => A[j] !== B[j]).map(i => ({l: labels[i], from: ref.map[labels[i]], to: f(cur[i])}));
  return {ari: ariJS(A, B), keep: 1 - moved.length / idx.length, n: idx.length, moved, f, A, B};
}
function uConfigDesc() {
  const rep = $("uRep").selectedOptions[0].text, sel = (id) => $(id).selectedOptions[0]?.text || "";
  const det = U.rep === "spec" ? sel("uBatch") : ["day", "pair", "merge"].includes(U.rep) ? [U.da, ["pair", "merge"].includes(U.rep) ? U.db : null].filter(x => x != null).map(d => "D" + d).join(" + ") + (U.rep === "merge" ? ` | Δ−D${U.dr}` : "")
    : U.rep === "paper" ? `D${U.da} → D${U.db} (S, ΔS, R)`
    : U.rep === "dS" ? `${sel("uA")} → ${sel("uB")}` : U.rep === "traj" ? sel("uTraj") : "";
  return `${rep}${det ? " · " + det : ""}${["spec", "day", "pair", "merge", "paper"].includes(U.rep) ? " · " + sel("uMode") : ""} · ${sel("uMethod")}${U.method === "dbscan" ? "" : " k=" + U.k} · ${END_LABEL[C.end]} · ${sel("uScale")}`;
}

// ---------- vẽ ----------
function drawUnsup() {
  const r = U.res, M = U.M, n = M.labels.length;
  const cmp = refCompare(M.labels, r.labels), fid = cmp ? cmp.f : (c) => c;
  const lab = r.labels.map(fid);                    // id cụm hiển thị (đã khớp với tham chiếu nếu có)
  const clusters = [...new Set(lab)].sort((a, b) => a - b);
  $("uStatus").textContent = t("{n} trứng × {p} biến · cụm trên {k} PC đầu", {n: r.n, p: r.p, k: r.n_pc_used}) +
    (r.silhouette != null ? ` · silhouette ${fmt(r.silhouette, 2)}` : "") + (r.method === "dbscan" ? ` · eps ${fmt(r.eps, 2)}` : "");
  const info = (i) => `${M.labels[i]}${groupOf(M.labels[i]) ? " · " + GROUP_NAME[groupOf(M.labels[i])] : ""}`;

  // 1) bản đồ 2D
  const emb = r.embedding, embName = {pca: ["PC1", "PC2"], tsne: ["t-SNE 1", "t-SNE 2"], mds: ["MDS 1", "MDS 2"]}[r.embed];
  if (r.embed === "pca") embName.forEach((_, j) => embName[j] += ` (${fmt(r.pca.var[j] * 100, 0)}%)`);
  const t1 = clusters.map(c => {
    const ii = lab.map((v, i) => v === c ? i : -1).filter(i => i >= 0);
    return {x: ii.map(i => emb[i][0]), y: ii.map(i => emb[i][1]), text: ii.map(i => M.labels[i]), mode: "markers+text", name: clusterName(c),
      textposition: "top center", textfont: {size: 10, color: css("--ink-2")},
      marker: {size: 11, color: clusterColor(c), symbol: ii.map(i => symOf(M.labels[i])),
        line: {color: css("--surface"), width: 1.5}},
      customdata: ii.map(i => [info(i), r.anomaly.iforest[i], r.silhouette_samples?.[i]]),
      hovertemplate: `%{customdata[0]}<br>${clusterName(c)} · silhouette %{customdata[2]:.2f}<br>Isolation %{customdata[1]:.3f}<extra></extra>`};
  });
  if (useRefSym()) {          // chú thích hình = cụm tham chiếu
    const ref = getRef(), rc = [...new Set(M.labels.map(l => ref.map[l] ?? null))].sort((a, b) => (a ?? 99) - (b ?? 99));
    rc.forEach(c => t1.push({x: [null], y: [null], mode: "markers", name: c == null ? t("hình: ngoài tham chiếu") : t("hình: {c} (TC)", {c: clusterName(c)}), legendgroup: "shape",
      marker: {size: 10, symbol: refSymbol(c), color: css("--ink-2")}, hoverinfo: "skip"}));
  } else {
    const present = new Set(M.labels.map(l => groupOf(l) || "none"));
    [...GROUPS.map(G => [G.k, G.name]), ["none", t("Chưa gán")]].forEach(([g, nm]) => {
      if (present.has(g)) t1.push({x: [null], y: [null], mode: "markers", name: t("hình: {c}", {c: nm}), legendgroup: "shape",
        marker: {size: 10, symbol: G_SYMBOL[g] || "circle-open", color: css("--ink-2")}, hoverinfo: "skip"});
    });
  }
  Plotly.react("uEmbedPlot", t1, baseLayout({xaxis: {title: {text: embName[0]}, zeroline: false}, yaxis: {title: {text: embName[1]}, zeroline: false},
    margin: {l: 56, r: 8, t: 8, b: 44}}), PCFG);

  // 2) chọn k
  const ks = r.kscan.map(x => x.k), tk = [];
  tk.push({x: ks, y: r.kscan.map(x => x.sil_kmeans), mode: "lines+markers", name: "Silhouette K-Means", line: {color: catColor(0), width: 2}, marker: {size: 7}});
  tk.push({x: ks, y: r.kscan.map(x => x.sil_ward), mode: "lines+markers", name: "Silhouette Ward", line: {color: catColor(1), width: 2, dash: "dash"}, marker: {size: 7}});
  const inr = r.kscan.map(x => x.inertia), bic = r.kscan.map(x => x.bic);
  const norm01 = (a) => { const v = a.filter(x => x != null), lo = Math.min(...v), hi = Math.max(...v); return a.map(x => x == null ? null : hi > lo ? (x - lo) / (hi - lo) : 0); };
  Plotly.react("uKPlot", tk, baseLayout({xaxis: {title: {text: t("Số cụm k")}, dtick: 1}, yaxis: {title: {text: t("Silhouette (cao = tách tốt)")}},
    shapes: U.method !== "dbscan" ? [{type: "line", x0: U.k, x1: U.k, yref: "paper", y0: 0, y1: 1, line: {color: css("--axis"), dash: "dot"}}] : [],
    legend: {orientation: "h", y: 1.12, x: 0}, margin: {l: 56, r: 8, t: 30, b: 44}}), {...PCFG, displayModeBar: false});
  Plotly.react("uElbowPlot", [
    {x: ks, y: norm01(inr), mode: "lines+markers", name: "Inertia (elbow)", line: {color: catColor(2), width: 2}, customdata: inr, hovertemplate: "k=%{x}: inertia %{customdata:.4g}<extra></extra>"},
    {x: ks, y: norm01(bic), mode: "lines+markers", name: "BIC (GMM)", line: {color: catColor(6), width: 2, dash: "dash"}, customdata: bic, hovertemplate: "k=%{x}: BIC %{customdata:.4g}<extra></extra>"}],
    baseLayout({xaxis: {title: {text: t("Số cụm k")}, dtick: 1}, yaxis: {title: {text: t("Chuẩn hoá 0–1 (thấp = tốt)")}},
      legend: {orientation: "h", y: 1.12, x: 0}, margin: {l: 56, r: 8, t: 30, b: 44}}), {...PCFG, displayModeBar: false});

  // 3) hồ sơ trung bình mỗi cụm
  const ax = M.axis, isFeat = ax.type === "feat";
  const prof = clusters.map(c => {
    let vs = M.rows.filter((_, i) => lab[i] === c);
    if (isFeat) {   // đặc trưng: z-score để cùng thang
      const mu = ax.values.map((_, j) => { const v = M.rows.map(rw => rw[j]).filter(x => x != null); return v.reduce((a, b) => a + b, 0) / v.length; });
      const sd = ax.values.map((_, j) => { const v = M.rows.map(rw => rw[j]).filter(x => x != null); return Math.sqrt(v.reduce((a, b) => a + (b - mu[j]) ** 2, 0) / Math.max(1, v.length - 1)) || 1; });
      vs = vs.map(rw => rw.map((x, j) => x == null ? null : (x - mu[j]) / sd[j]));
    }
    const ms = meanSd(vs), col = clusterColor(c);
    let xs = ax.values, lo = ms.mean.map((m, i) => m == null ? null : m - ms.sd[i]), up = ms.mean.map((m, i) => m == null ? null : m + ms.sd[i]), mean = ms.mean;
    if (ax.type === "concat") [xs, lo, up, mean] = breakGaps(xs, lo, up, mean);
    // dải ± SD vẽ theo từng đoạn liên tục, để không tô chéo qua chỗ thiếu dữ liệu
    const segs = []; let st = -1;
    mean.forEach((m, i) => { if (m != null && st < 0) st = i; if ((m == null || i === mean.length - 1) && st >= 0) { segs.push([st, m == null ? i : i + 1]); st = -1; } });
    return [
      ...segs.flatMap(([a, b]) => [
        {x: xs.slice(a, b), y: lo.slice(a, b), mode: "lines", line: {width: 0}, hoverinfo: "skip", showlegend: false},
        {x: xs.slice(a, b), y: up.slice(a, b), mode: "lines", line: {width: 0}, fill: "tonexty", fillcolor: hexA(col, 0.15), hoverinfo: "skip", showlegend: false}]),
      {x: xs, y: mean, mode: isFeat || ax.type === "batch" ? "lines+markers" : "lines", name: `${clusterName(c)} (n=${vs.length})`, line: {color: col, width: 2.2}, marker: {size: 7}}];
  }).flat();
  const deco = axisDecor(ax), topM = ax.type === "concat" ? 24 : 8;
  Plotly.react("uProfPlot", prof, baseLayout({xaxis: {title: {text: ax.title}, ...deco.xaxis}, yaxis: {title: {text: ax.y}},
    shapes: deco.shapes, annotations: deco.annotations, margin: {l: 60, r: 8, t: topM, b: isFeat ? 90 : 44}}), PCFG);

  // 4) PCA: phương sai + loadings
  const v = r.pca.var;
  Plotly.react("uVarPlot", [{type: "bar", x: v.map((_, i) => `PC${i + 1}`), y: v.map(x => x * 100), marker: {color: css("--accent")}, width: 0.5,
    hovertemplate: "%{x}: %{y:.1f}%<extra></extra>"}, {x: v.map((_, i) => `PC${i + 1}`), y: v.map((_, i) => v.slice(0, i + 1).reduce((a, b) => a + b, 0) * 100),
    mode: "lines+markers", line: {color: css("--ink-2"), width: 1.5}, marker: {size: 6}, hovertemplate: t("Tích luỹ: %{y:.1f}%") + "<extra></extra>"}],
    baseLayout({yaxis: {title: {text: t("% phương sai")}, range: [0, 105]}, showlegend: false, margin: {l: 56, r: 8, t: 8, b: 36}}), {...PCFG, displayModeBar: false});
  const lx = r.pca.cols.map(j => ax.values[j]);
  Plotly.react("uLoadPlot", r.pca.loadings.map((ld, j) => { const [x, y] = ax.type === "concat" ? breakGaps(lx, ld) : [lx, ld];
    return {x, y, mode: isFeat ? "markers+lines" : "lines", name: `PC${j + 1}`, line: {color: catColor(j === 0 ? 0 : 1), width: 2, dash: j ? "dash" : "solid"}}; }),
    baseLayout({xaxis: {title: {text: ax.title}, ...deco.xaxis}, yaxis: {title: {text: "Loading"}},
      shapes: [...deco.shapes, {type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: {color: css("--axis"), width: 1}}], annotations: deco.annotations,
      legend: {orientation: "h", y: ax.type === "concat" ? 1.2 : 1.12, x: 0}, margin: {l: 56, r: 8, t: ax.type === "concat" ? 48 : 30, b: isFeat ? 90 : 44}}), PCFG);

  // 5) dendrogram (Ward)
  const d = r.dendro, dt = d.icoord.map((xs, i) => ({x: xs, y: d.dcoord[i], mode: "lines", line: {color: css("--ink-2"), width: 1.2}, hoverinfo: "skip", showlegend: false}));
  const leafX = d.leaves.map((_, i) => 5 + 10 * i);
  dt.push({x: leafX, y: leafX.map(() => 0), mode: "markers", marker: {size: 9, color: d.leaves.map(i => clusterColor(lab[i])),
    symbol: d.leaves.map(i => symOf(M.labels[i]))}, text: d.leaves.map(i => info(i)), hovertemplate: "%{text}<extra></extra>", showlegend: false});
  Plotly.react("uDendroPlot", dt, baseLayout({xaxis: {tickvals: leafX, ticktext: d.leaves.map(i => M.labels[i]), tickangle: -90, tickfont: {size: 9}},
    yaxis: {title: {text: t("Khoảng cách Ward")}},
    shapes: d.cut && U.method !== "dbscan" ? [{type: "line", xref: "paper", x0: 0, x1: 1, y0: d.cut, y1: d.cut, line: {color: css("--critical"), dash: "dash", width: 1}}] : [],
    margin: {l: 56, r: 8, t: 8, b: 60}}), {...PCFG, displayModeBar: false});

  // 6) bất thường
  const iso = r.anomaly.iforest, lof = r.anomaly.lof;
  const ord = M.labels.map((_, i) => i).sort((a, b) => iso[b] - iso[a]);
  const isoThr = [...iso].sort((a, b) => b - a)[Math.max(0, Math.round(n * 0.1) - 1)];
  const flag = (i) => iso[i] >= isoThr && lof[i] > 1.5;
  Plotly.react("uAnomPlot", [{type: "bar", orientation: "h", y: ord.map(i => M.labels[i]), x: ord.map(i => iso[i]),
    marker: {color: ord.map(i => flag(i) ? css("--critical") : css("--muted"))}, customdata: ord.map(i => [lof[i], info(i)]),
    hovertemplate: "%{customdata[1]}<br>Isolation %{x:.3f} · LOF %{customdata[0]:.2f}<extra></extra>"}],
    baseLayout({xaxis: {title: {text: t("Điểm Isolation Forest (cao = bất thường)")}, range: [Math.min(...iso) * 0.95, Math.max(...iso) * 1.02]},
      yaxis: {autorange: "reversed", type: "category", tickfont: {size: 10}}, showlegend: false, margin: {l: 48, r: 8, t: 8, b: 44}}), {...PCFG, displayModeBar: false});

  // 7) bảng + đối chiếu nhóm
  const rows = M.labels.map((l, i) => ({l, i})).sort((a, b) => lab[a.i] - lab[b.i] || iso[b.i] - iso[a.i]);
  const R0 = getRef(), refCell = (l) => R0.map[l] == null ? `<span class="muted">—</span>` : `${symTag(R0.map[l])} ${clusterName(R0.map[l])}`;
  $("uTable").innerHTML = `<thead><tr><th>${t("Trứng")}</th><th>${t("Nhóm")}</th>${R0 ? `<th>${t("Tham chiếu")}</th>` : ""}<th>${t("Cụm")}</th><th>Silhouette</th>${r.prob ? `<th>${t("P(cụm)")}</th>` : ""}<th>Isolation</th><th>LOF</th></tr></thead><tbody>` +
    rows.map(({l, i}) => `<tr><td><a class="link" data-open-egg="${esc(l)}">${esc(l)}</a></td><td>${groupOf(l) ? `${groupDot(l)}${GROUP_NAME[groupOf(l)]}` : `<span class="muted">—</span>`}</td>
      ${R0 ? `<td>${refCell(l)}</td>` : ""}<td${R0 && R0.map[l] != null && R0.map[l] !== lab[i] ? ' class="flag"' : ""}><span class="gdot" style="background:${clusterColor(lab[i])}"></span>${clusterName(lab[i])}</td><td class="num">${fmt(r.silhouette_samples?.[i], 2)}</td>
      ${r.prob ? `<td class="num">${fmt(r.prob[i], 2)}</td>` : ""}<td class="num${flag(i) ? " flag" : ""}">${fmt(iso[i], 3)}</td><td class="num${lof[i] > 1.5 ? " flag" : ""}">${fmt(lof[i], 2)}</td></tr>`).join("") + "</tbody>";
  const a = r.agree;
  $("uAgree").innerHTML = a ? `<div class="kv"><span>ARI</span><b>${fmt(a.ari, 2)}</b><span>NMI</span><b>${fmt(a.nmi, 2)}</b><span>${t("Số trứng có nhóm")}</span><b>${a.n}</b></div>
    <table class="ctab"><thead><tr><th></th>${a.clusters.map(c => `<th><span class="gdot" style="background:${clusterColor(fid(c))}"></span>${clusterName(fid(c))}</th>`).join("")}</tr></thead>
    <tbody>${a.groups.map((g, gi) => `<tr><td>${GROUP_NAME[g] || g}</td>${a.table[gi].map(v => `<td class="num">${v}</td>`).join("")}</tr>`).join("")}</tbody></table>
    <div class="muted small">${t("ARI ≈ 0: cụm không trùng với nhóm; ARI = 1: trùng hoàn toàn.")}</div>`
    : `<div class="muted small">${t("Gán nhóm (tab So sánh) để so cụm với Trống / Mái / Không phôi.")}</div>`;
  // click điểm / thanh -> mở trứng (gắn sau khi Plotly đã vẽ)
  ["uEmbedPlot", "uAnomPlot"].forEach(id => { const el = $(id); el.removeAllListeners?.("plotly_click");
    el.on("plotly_click", ev => { const l = ev.points?.[0]?.text ?? ev.points?.[0]?.y; if (l && C.data.eggs[l]) openEggFromTab(l); }); });
  drawStability(cmp);
}

// ---------- khung ổn định ----------
const SYM_CHAR = {circle: "●", square: "■", "triangle-up": "▲", diamond: "◆", cross: "✚", star: "★", x: "✖", pentagon: "⬟", hexagram: "✶", "triangle-down": "▼"};
const symTag = (c) => `<span class="symtag">${SYM_CHAR[refSymbol(c)] || "○"}</span>`;
function drawStability(cmp) {
  const ref = getRef();
  $("uRefClear").hidden = $("uTrackRun").hidden = !ref;
  $("uRefSub").textContent = ref ? t("tham chiếu: {d} · {n} trứng · {at}", {d: ref.desc, n: Object.keys(ref.map).length, at: ref.at}) : "";
  if (!ref) { $("uRefOut").innerHTML = `<div class="muted small">${t("Chưa có tham chiếu.")}</div>`; $("uTrackOut").hidden = true; return; }
  if (!cmp) { $("uRefOut").innerHTML = `<div class="muted small">${t("Cấu hình hiện tại không có trứng chung với tham chiếu.")}</div>`; return; }
  const refC = [...new Set(cmp.A)].sort((a, b) => a - b), curC = [...new Set(cmp.B)].sort((a, b) => a - b);
  const cell = (r, c) => cmp.A.filter((x, j) => x === r && cmp.B[j] === c).length;
  const head = (c) => `<span class="gdot" style="background:${clusterColor(c)}"></span>${clusterName(c)}`;
  const a = cmp.ari ?? 0, same = t(a >= 0.8 ? "ổn định" : a >= 0.5 ? "khá ổn định" : a >= 0.2 ? "kém ổn định" : "gần như chia lại hoàn toàn");
  $("uRefOut").innerHTML = `<div class="two even tight"><div>
      <div class="kv"><span>${t("Cấu hình hiện tại")}</span><b>${esc(uConfigDesc())}</b>
        <span>${t("ARI với tham chiếu")}</span><b>${fmt(cmp.ari, 2)} — ${same}</b>
        <span>${t("Giữ cụm")}</span><b>${t("{p}% ({a}/{n} trứng)", {p: fmt(cmp.keep * 100, 0), a: cmp.n - cmp.moved.length, n: cmp.n})}</b></div>
      <table class="ctab"><thead><tr><th>${t("Tham chiếu ↓ / hiện tại →")}</th>${curC.map(c => `<th>${head(c)}</th>`).join("")}</tr></thead>
        <tbody>${refC.map(r => `<tr><td>${symTag(r)} ${clusterName(r)}</td>${curC.map(c => `<td class="num${r === c ? " strong" : ""}">${cell(r, c) || ""}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
    <div><div class="mini-t">${t("Trứng đổi cụm ({n})", {n: cmp.moved.length})}</div>
      ${cmp.moved.length ? `<div class="chips">${cmp.moved.map(m => `<button class="chip x" data-open-egg="${esc(m.l)}" title="${esc(t("Mở trứng"))}">${esc(m.l)}: ${symTag(m.from)} ${clusterName(m.from)} → <span class="gdot" style="background:${clusterColor(m.to)};margin:0 3px"></span>${clusterName(m.to)}</button>`).join("")}</div>` : `<div class="muted small">${t("Không trứng nào đổi cụm.")}</div>`}</div></div>`;
}
function setRefNow() {
  if (!U.res || !U.M) return toast(t("Chưa có kết quả phân cụm"));
  const f = refMatcher(U.M.labels, U.res.labels);     // giữ đúng số cụm đang hiển thị
  const map = Object.fromEntries(U.M.labels.map((l, i) => [l, f(U.res.labels[i])]));
  setRef({desc: uConfigDesc(), at: new Date().toLocaleString(LOCALE), map});
  U.symBy = "ref"; localSet("u.symBy", "ref"); $("uSymBy").value = "ref";
  U.track = null; $("uTrackOut").hidden = true;
  drawUnsup(); if (U.scan) drawScan(); toast(t("Đã đặt tham chiếu"));
}

// ---------- theo dõi theo batch & ngày ----------
async function runTrack() {
  const ref = getRef(); if (!ref) return;
  const items = allEggs(), {idx} = uWin(), bs = C.data.batches, days = uDays();
  const vec = {}, configs = [];
  bs.forEach((b, bi) => {
    vec[100 + bi] = items.map(x => { const s = x.egg.series.find(z => z.bi === bi); const v = s && uVec(x.egg, s, idx); return v ? idx.map(i => v[i]) : null; });
    configs.push({name: "B" + String(b.batch_no).padStart(2, "0"), kind: "batch", x: b.age_h, blocks: [[100 + bi, null]]});
  });
  days.forEach(dd => {
    vec[dd.d] = items.map(x => dayVec(x.egg, dd, idx));
    const ages = dd.bis.map(bi => bs[bi].age_h).filter(a => a != null);
    configs.push({name: "D" + dd.d, kind: "day", x: ages.reduce((a, b) => a + b, 0) / Math.max(1, ages.length), blocks: [[dd.d, null]]});
  });
  $("uTrackRun").disabled = true; $("uTrackOut").hidden = false; $("uTrackSub").textContent = t("đang chạy {n} cấu hình…", {n: configs.length});
  try {
    const j = await postJSON("/api/unsup_scan", {days: vec, configs: configs.map(c => ({name: c.name, blocks: c.blocks})),
      k: U.k, method: U.method, scale: U.scale, eps: U.eps ? +U.eps : null, min_samples: U.minS});
    U.track = {configs: configs.map((c, i) => ({...c, ...j.results[i]})), labels: items.map(x => x.label), data: C.data};
    drawTrack();
  } catch (ex) { $("uTrackSub").innerHTML = `<span class="flag">${esc(ex.message)}</span>`; }
  finally { $("uTrackRun").disabled = false; }
}
function drawTrack() {
  const T = U.track, ref = getRef(); $("uTrackOut").hidden = !T || !ref; if (!T || !ref) return;
  const res = T.configs.map(c => ({...c, cmp: c.error ? null : refCompare(c.rows.map(r => T.labels[r]), c.labels)}));
  $("uTrackSub").textContent = `${t("{b} batch + {d} ngày", {b: C.data.batches.length, d: uDays().length})} · ${U_MODE[U.mode] || ""} · ${$("uMethod").selectedOptions[0].text}${U.method === "dbscan" ? "" : " k=" + U.k}`;
  // ARI + tỷ lệ giữ cụm theo tuổi ấp
  const tr = [];
  [["batch", t("từng batch"), "solid"], ["day", t("từng ngày"), "dash"]].forEach(([kind, nm, dash]) => {
    const rs = res.filter(c => c.kind === kind && c.cmp);
    tr.push({x: rs.map(c => c.x), y: rs.map(c => c.cmp.ari), text: rs.map(c => c.name), mode: "lines+markers+text", textposition: "top center",
      textfont: {size: 10, color: css("--ink-2")}, name: `ARI · ${nm}`, line: {color: catColor(0), width: 2, dash}, marker: {size: 7},
      customdata: rs.map(c => [c.cmp.keep * 100, c.cmp.n]), hovertemplate: t("%{text}: ARI %{y:.2f} · giữ cụm %{customdata[0]:.0f}% (n=%{customdata[1]})") + "<extra></extra>"});
    tr.push({x: rs.map(c => c.x), y: rs.map(c => c.cmp.keep), mode: "lines+markers", name: `${t("Giữ cụm")} · ${nm}`, line: {color: catColor(1), width: 1.5, dash},
      marker: {size: 5}, hovertemplate: t("giữ cụm %{y:.0%}") + "<extra></extra>"});
  });
  Plotly.react("uTrackAri", tr, baseLayout({xaxis: {title: {text: t("Tuổi ấp (h)")}}, yaxis: {title: {text: t("ARI · tỷ lệ giữ cụm")}, range: [-0.2, 1.1]},
    legend: {orientation: "h", y: 1.2, x: 0, font: {size: 10, color: css("--ink-2")}}, margin: {l: 56, r: 8, t: 44, b: 44},
    shapes: [{type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: {color: css("--axis"), width: 1}}]}), {...PCFG, displayModeBar: false});
  // bảng trứng × thời gian: màu = cụm đã khớp; cột đầu = tham chiếu
  const cols = [{name: "TC"}, ...res.filter(c => c.kind === "batch"), ...res.filter(c => c.kind === "day")];
  const eggs = T.labels.filter(l => ref.map[l] != null).sort((a, b) => ref.map[a] - ref.map[b] || a.localeCompare(b));
  const val = (c, l) => {
    if (c.name === "TC") return ref.map[l];
    if (!c.cmp) return null;
    const k = c.rows.findIndex(r => T.labels[r] === l);
    return k < 0 ? null : c.cmp.f(c.labels[k]);
  };
  const z = eggs.map(l => cols.map(c => val(c, l)));
  const moves = eggs.map((l, i) => z[i].slice(1).filter(v => v != null && v !== ref.map[l]).length);
  const K = Math.max(1, ...z.flat().filter(v => v != null)) + 2, noise = K - 1;     // nhiễu (-1) -> ô màu xám cuối
  const zz = z.map(r => r.map(v => v == null ? null : v < 0 ? noise : v));
  const scale = [];
  for (let i = 0; i < K; i++) { const col = i === noise ? css("--muted") : clusterColor(i); scale.push([i / K, col], [(i + 1) / K, col]); }
  const hp = $("uTrackHeat"); hp.style.height = Math.max(300, 15 * eggs.length + 80) + "px";
  Plotly.react("uTrackHeat", [{type: "heatmap", x: cols.map(c => c.name === "TC" ? t("TC") : c.name), y: eggs.map((l, i) => `${l} (${moves[i]})`), z: zz, zmin: -0.5, zmax: K - 0.5,
    colorscale: scale, showscale: false, xgap: 1, ygap: 1, text: z.map(r => r.map(v => v == null ? "" : v < 0 ? "–" : String(v + 1))), texttemplate: "%{text}",
    textfont: {size: 9, color: "#fff"}, hovertemplate: t("%{y} · %{x}: cụm %{text}") + "<extra></extra>"}],
    baseLayout({xaxis: {type: "category", side: "top", tickfont: {size: 10}}, yaxis: {type: "category", autorange: "reversed", tickfont: {size: 10}},
      margin: {l: 70, r: 8, t: 30, b: 8}}), {...PCFG, displayModeBar: false});
  hp.removeAllListeners?.("plotly_click");
  hp.on("plotly_click", ev => { const l = String(ev.points?.[0]?.y || "").split(" ")[0]; if (l) openEggFromTab(l); });
}
function openEggFromTab(l) { setTab("egg"); loadEgg(l); window.scrollTo({top: 0}); }

// ---------- quét theo ngày ----------
// Gửi phổ ngày của từng trứng một lần; server tự ghép khối cho từng cấu hình.
async function runScan() {
  const sel = [...U.scanDays].sort((a, b) => a - b);
  if (sel.length < 1) return toast(t("Chọn ít nhất 1 ngày"));
  const days = Object.fromEntries(uDays().map(x => [x.d, x])), items = allEggs(), {idx} = uWin();
  const dayRows = Object.fromEntries(sel.map(d => [d, items.map(x => dayVec(x.egg, days[d], idx))]));
  const configs = [];
  sel.forEach(a => configs.push({name: dayName(a), kind: "day", da: a, blocks: dayBlocks("day", a)}));
  sel.forEach((a, i) => sel.slice(i + 1).forEach(b => configs.push({name: `${dayName(a)} + ${dayName(b)}`, kind: "pair", da: a, db: b, blocks: dayBlocks("pair", a, b)})));
  sel.forEach((r, i) => sel.slice(i + 1).forEach((a, j) => sel.slice(i + j + 2).forEach(b =>
    configs.push({name: `${dayName(a)} + ${dayName(b)} | Δ−${dayName(r)}`, kind: "merge", da: a, db: b, dr: r, blocks: dayBlocks("merge", a, b, r)}))));
  $("uScanRun").disabled = true; $("uScanStatus").textContent = t("Đang chạy {n} cấu hình…", {n: configs.length});
  try {
    const j = await postJSON("/api/unsup_scan", {days: dayRows, configs: configs.map(c => ({name: c.name, blocks: c.blocks})),
      groups: items.map(x => groupOf(x.label)), k: U.k, method: U.method, scale: U.scale, eps: U.eps ? +U.eps : null, min_samples: U.minS});
    U.scan = {configs: configs.map((c, i) => ({...c, ...j.results[i]})), labels: items.map(x => x.label), days: sel, data: C.data,
      desc: `${U_MODE[U.mode]} · ${t("chuẩn hoá {s}", {s: $("uScale").selectedOptions[0].text})} · ${$("uMethod").selectedOptions[0].text}${U.method === "dbscan" ? "" : " k = " + U.k} · ${END_LABEL[C.end]}`};
    drawScan();
  } catch (ex) { $("uScanStatus").innerHTML = `<span class="flag">${esc(ex.message)}</span>`; }
  finally { $("uScanRun").disabled = false; }
}
function drawScan() {
  const sc = U.scan; $("uScanOut").hidden = !sc; if (!sc) return;
  const met = U.scanMetric, metName = $("uScanMetric").selectedOptions[0].text;
  const val = (c) => c?.error ? null : met === "refari" ? (refCompare(c.rows.map(r => sc.labels[r]), c.labels)?.ari ?? null) : c?.[met];
  const noGroups = ["ari", "nmi"].includes(met) && sc.configs.every(c => c[met] == null);
  const noRef = met === "refari" && !getRef();
  const errs = sc.configs.filter(c => c.error);
  $("uScanStatus").innerHTML = `${t("{n} cấu hình", {n: sc.configs.length})} · ${esc(sc.desc)}` + (noGroups ? ` · <span class="flag">${esc(t("{m} cần ≥ 4 trứng đã gán, ≥ 2 nhóm", {m: metName}))}</span>` : "") +
    (noRef ? ` · <span class="flag">${t("chưa đặt cụm tham chiếu")}</span>` : "") +
    (errs.length ? ` · <span class="flag">${t("{n} lỗi", {n: errs.length})}: ${esc(errs[0].name)}: ${esc(tMsg(errs[0].error))}</span>` : "");

  // 1) từng ngày: bản đồ PC1–PC2 nhỏ
  const singles = sc.configs.filter(c => c.kind === "day");
  $("uScanSingles").innerHTML = singles.map((c, i) => `<div><div class="mini-t"><span>${esc(c.name)}</span><span class="muted">${c.error ? t("lỗi") : `${metName} ${fmt(val(c), 2)} · n=${c.n}`}</span></div><div class="plot" id="uMini${i}"></div></div>`).join("");
  singles.forEach((c, i) => {
    if (c.error) return;
    const labs = c.rows.map(r => sc.labels[r]), fm = refMatcher(labs, c.labels), cl = [...new Set(c.labels)].sort((a, b) => a - b);
    const tr = cl.map(k => { const ii = c.labels.map((v, t) => v === k ? t : -1).filter(t => t >= 0);
      return {x: ii.map(t => c.embedding[t][0]), y: ii.map(t => c.embedding[t][1]), text: ii.map(t => labs[t]), mode: "markers", name: clusterName(fm(k)),
        marker: {size: 8, color: clusterColor(fm(k)), symbol: ii.map(t => symOf(labs[t])), line: {color: css("--surface"), width: 1}},
        hovertemplate: `%{text} · ${clusterName(fm(k))}<extra></extra>`}; });
    Plotly.react(`uMini${i}`, tr, baseLayout({showlegend: false, margin: {l: 28, r: 4, t: 4, b: 22}, xaxis: {zeroline: false}, yaxis: {zeroline: false}}), {...PCFG, displayModeBar: false});
    $(`uMini${i}`).on("plotly_click", ev => { const l = ev.points?.[0]?.text; if (l) openEggFromTab(l); });
    $(`uMini${i}`).previousElementSibling.style.cursor = "pointer";
    $(`uMini${i}`).previousElementSibling.onclick = () => openScanConfig(c);
  });

  // 2) ma trận ngày × ngày: đường chéo = 1 ngày, ngoài chéo = ghép 2 ngày
  const ds = sc.days, find = (a, b) => sc.configs.find(c => a === b ? c.kind === "day" && c.da === a : c.kind === "pair" && c.da === Math.min(a, b) && c.db === Math.max(a, b));
  const z = ds.map(a => ds.map(b => val(find(a, b)) ?? null)), lab = ds.map(dayName);
  Plotly.react("uScanHeat", [{type: "heatmap", x: lab, y: lab, z, colorscale: seqScale(), xgap: 2, ygap: 2,
    text: z.map(r => r.map(v => v == null ? "" : v.toFixed(2))), texttemplate: "%{text}", textfont: {size: 11},
    hovertemplate: `%{y} + %{x}<br>${metName} %{z:.3f}<extra></extra>`, colorbar: {thickness: 10, outlinewidth: 0, tickfont: {color: css("--ink-2"), size: 11}}}],
    baseLayout({xaxis: {type: "category"}, yaxis: {type: "category", autorange: "reversed"}, margin: {l: 40, r: 8, t: 8, b: 32}}), {...PCFG, displayModeBar: false});
  const hp = $("uScanHeat"); hp.removeAllListeners?.("plotly_click");
  hp.on("plotly_click", ev => { const p = ev.points?.[0]; if (!p) return; const a = +p.y.slice(1), b = +p.x.slice(1); const c = find(a, b); if (c) openScanConfig(c); });

  // 3) ghép 2 ngày + Δ: sắp theo thước đo
  const ms = sc.configs.filter(c => c.kind === "merge").sort((a, b) => (val(b) ?? -9) - (val(a) ?? -9));
  const mp = $("uScanMerge"); mp.style.height = Math.max(300, 24 * ms.length + 70) + "px";
  Plotly.react("uScanMerge", [{type: "bar", orientation: "h", y: ms.map(c => c.name), x: ms.map(c => val(c) ?? 0), width: 0.55,
    marker: {color: css("--accent")}, text: ms.map(c => val(c) == null ? "—" : val(c).toFixed(3)), textposition: "outside", cliponaxis: false,
    customdata: ms.map(c => c.n), hovertemplate: `%{y}<br>${metName} %{x:.3f} · n=%{customdata}<extra></extra>`}],
    baseLayout({xaxis: {title: {text: metName}}, yaxis: {autorange: "reversed", type: "category", automargin: true}, showlegend: false, margin: {l: 8, r: 40, t: 8, b: 44}}),
    {...PCFG, displayModeBar: false});
  mp.removeAllListeners?.("plotly_click");
  mp.on("plotly_click", ev => { const c = ms.find(m => m.name === ev.points?.[0]?.y); if (c) openScanConfig(c); });
}
// mở 1 cấu hình của bảng quét ở phần phân tích chi tiết
function openScanConfig(c) {
  U.rep = c.kind; U.da = c.da; if (c.db != null) U.db = c.db; if (c.dr != null) U.dr = c.dr;
  localSet("u.rep", U.rep); uDefaults(); scheduleUnsup();
  $("tabUnsup").scrollIntoView({behavior: "smooth", block: "start"});
}

// ---------- events ----------
[["uRep", "rep"], ["uMode", "mode"], ["uTraj", "traj"], ["uMethod", "method"], ["uEmbed", "embed"]].forEach(([id, k]) =>
  $(id).addEventListener("change", e => {
    U[k] = e.target.value; localSet("u." + k, U[k]);
    if (id === "uRep" && U.rep === "paper") {      // mặc định như bài báo: T, ngày gốc D2, ngày sau D5
      const ds = uDays().map(x => x.d);
      if (ds.includes(2)) U.da = 2;
      if (ds.includes(5)) U.db = 5;
      U.mode = "T"; localSet("u.mode", "T");
      toast(t("Như bài báo: đại lượng T, D2 làm gốc, D5 — đổi được"));
    }
    uDefaults(); scheduleUnsup();
  }));
$("uBatch").addEventListener("change", e => { U.batch = e.target.value; scheduleUnsup(); });
$("uA").addEventListener("change", e => { U.a = +e.target.value; scheduleUnsup(); });
$("uB").addEventListener("change", e => { U.b = +e.target.value; scheduleUnsup(); });
$("uK").addEventListener("change", e => { U.k = Math.max(2, Math.min(8, +e.target.value || 3)); localSet("u.k", U.k); scheduleUnsup(); });
$("uEps").addEventListener("change", e => { U.eps = e.target.value; scheduleUnsup(); });
$("uScale").addEventListener("change", e => { U.scale = e.target.value; localSet("u.scale2", U.scale); scheduleUnsup(); });
[["uDA", "da"], ["uDB", "db"], ["uDR", "dr"]].forEach(([id, k]) => $(id).addEventListener("change", e => { U[k] = +e.target.value; scheduleUnsup(); }));
$("uScanDays").addEventListener("click", e => {
  const b = e.target.closest("[data-sday]"); if (!b) return;
  const d = +b.dataset.sday; U.scanDays = U.scanDays.includes(d) ? U.scanDays.filter(x => x !== d) : [...U.scanDays, d];
  b.classList.toggle("on", U.scanDays.includes(d));
});
$("uScanMetric").addEventListener("change", e => { U.scanMetric = e.target.value; localSet("u.scanMetric", U.scanMetric); drawScan(); });
$("uScanRun").addEventListener("click", runScan);
$("uSymBy").addEventListener("change", e => { U.symBy = e.target.value; localSet("u.symBy", U.symBy); if (U.res) drawUnsup(); if (U.scan) drawScan(); });
$("uRefSet").addEventListener("click", setRefNow);
$("uRefClear").addEventListener("click", () => { setRef(null); U.track = null; if (U.res) drawUnsup(); if (U.scan) drawScan(); });
$("uTrackRun").addEventListener("click", runTrack);
$("uRefOut").addEventListener("click", e => { const b = e.target.closest("[data-open-egg]"); if (b) openEggFromTab(b.dataset.openEgg); });
$("uBasis").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; U.basis = b.dataset.v; localSet("u.basis", U.basis); uDefaults(); scheduleUnsup(); });
$("uTable").addEventListener("click", e => { const a = e.target.closest("[data-open-egg]"); if (a) openEggFromTab(a.dataset.openEgg); });
// nút ? -> mở hộp thoại giải thích ở đúng mục
function openExplain(key) {
  const d = $("ex-" + key); if (!d) return;
  openModal("unsupModal"); d.open = true; d.scrollIntoView({block: "start"});
  d.classList.add("flash"); setTimeout(() => d.classList.remove("flash"), 1200);
}
document.addEventListener("click", e => { const b = e.target.closest("[data-explain]"); if (b) openExplain(b.dataset.explain); });
// khung chẩn đoán: Chọn k | PCA | Dendrogram | Bất thường
function setDiag(k) {
  U.diag = k; localSet("u.diag", k);
  document.querySelectorAll("#uDiagTabs button").forEach(b => b.classList.toggle("on", b.dataset.ud === k));
  document.querySelectorAll("[data-udp]").forEach(p => p.hidden = p.dataset.udp !== k);
  document.querySelectorAll("[data-udf]").forEach(el => el.hidden = el.dataset.udf !== k);
  // Plotly vẽ trong khung ẩn bị sai kích thước -> co giãn lại khi hiện
  document.querySelectorAll(`[data-udp="${k}"] .js-plotly-plot`).forEach(el => Plotly.Plots.resize(el));
}
$("uDiagTabs").addEventListener("click", e => { const b = e.target.closest("[data-ud]"); if (b) setDiag(b.dataset.ud); });
$("uDiagHelp").addEventListener("click", () => openExplain({table: "agree"}[U.diag] || U.diag));
setDiag(localGet("u.diag", "table"));
