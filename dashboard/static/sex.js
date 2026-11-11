"use strict";
// ============================================================================
// Tab "Trứng nở · Trống/Mái": các trứng đã nở có tự tách thành 2 nhóm theo phổ không (không dùng nhãn)?
// Tính toán ở sexclust.py (/api/sex): chia đôi tối ưu từng cấu hình + kiểm định kiểu SigClust + đồng thuận.
// Dùng chung state/helper của index.html, compare.js (S, C, $, groupSelect, setTags, catColor, openEggFromTab, ...).
// ============================================================================
const SX = {res: null, key: null, seq: 0, sel: null, sort: {k: "p", dir: 1}, base: 2, nnull: "200"};
const SX_Q = {shape: t("Hình dạng"), deriv: t("Đạo hàm"), paper: t("Vùng bài báo")};
const SX_END = {blunt: t("Tù"), pointed: t("Nhọn"), both: t("Cả hai")};
const sxColor = (g) => catColor(g ? 6 : 2);
const sxName = (g) => g ? t("Nhóm B") : t("Nhóm A");
const sxCfgName = (c) => `${SX_END[c.end]} · ${SX_Q[c.q]} · ${c.input}`;
const sxShort = (l) => String(+l);
const sxKey = () => "sx.labels." + (S.data?.session || "");

// "15-17-27 5-16-18" -> nhãn trứng của session (dấu gạch ngang là dấu phân cách, không phải khoảng)
function sxParse(txt) {
  const all = S.data?.labels || [], out = [];
  for (const tok of String(txt || "").split(/[^0-9]+/).filter(Boolean)) {
    const l = all.find(x => +x === +tok);
    if (l && !out.includes(l)) out.push(l);
  }
  return out;
}

function renderSex() {
  if (!S.data || $("tabSex").hidden) return;
  if ($("sxLabels").dataset.session !== S.data.session) {
    $("sxLabels").value = localGet(sxKey(), ""); $("sxLabels").dataset.session = S.data.session;
  }
  const labels = sxParse($("sxLabels").value);
  const tags = labels.map(l => S.tags[l] || "");
  const key = JSON.stringify([S.src, S.sig, labels, tags, SX.base, SX.nnull, band(), S.smooth, $("xLo").value, $("xHi").value]);
  if (key !== SX.key) runSex(key, labels); else drawSex();
}
async function runSex(key, labels) {
  SX.key = key; const seq = ++SX.seq;
  $("sxStatus").textContent = t("Đang tính…"); $("sxRun").disabled = true;
  try {
    const [lo, hi] = band();
    const res = await postJSON("/api/sex", {src: S.src, labels, lo, hi, x0: +$("xLo").value, x1: +$("xHi").value,
      smooth: S.smooth, base: SX.base, n_null: +SX.nnull});
    if (seq !== SX.seq) return;
    SX.res = res;
    if (!labels.length) {   // lần đầu: lấy theo tab Tình trạng
      $("sxLabels").value = res.labels.map(sxShort).join(", ");
      SX.key = JSON.stringify([S.src, S.sig, res.labels, res.labels.map(l => S.tags[l] || ""), SX.base, SX.nnull, band(), S.smooth, $("xLo").value, $("xHi").value]);
    }
    const best = [...res.configs.keys()].sort((a, b) => res.configs[a].p - res.configs[b].p)[0];
    if (SX.sel == null || SX.sel >= res.configs.length) SX.sel = best;
    drawSex();
  } catch (ex) { SX.key = null; $("sxStatus").innerHTML = `<span class="flag">${esc(ex.message)}</span>`; }
  finally { if (seq === SX.seq) $("sxRun").disabled = false; }
}

function drawSex() {
  const r = SX.res; if (!r) return;
  const n = r.labels.length, sm = r.summary, cons = r.consensus;
  $("sxBase").innerHTML = r.days.map(d => `<option value="${d}">D${d}</option>`).join("");
  $("sxBase").value = r.base_day;
  $("sxStatus").innerHTML = t("{n} trứng · {c} cấu hình · ngày {d}", {n, c: sm.n_configs, d: r.days.map(d => "D" + d).join(", ")}) +
    (sm.n_partitions ? " · " + t("duyệt hết {m} cách chia đôi", {m: sm.n_partitions}) : "") +
    (r.missing_days.length ? " · " + t("bỏ {d} (thiếu dữ liệu)", {d: r.missing_days.map(d => "D" + d).join(", ")}) : "");

  // ---------- KPI ----------
  const sizes = [0, 1].map(g => cons.filter(x => x === g).length);
  const dB = Math.max(...Object.values(r.confound.d_bright).map(v => Math.abs(v ?? 0)));
  const kpi = (color, v, k) => `<div class="st-kpi" style="--c:var(${color})"><span class="v num">${v}</span><span class="k">${k}</span></div>`;
  $("sxKpis").innerHTML =
    kpi(sm.n_sig > sm.expected * 2 ? "--good" : "--axis", `${sm.n_sig}<small>/${sm.n_configs}</small>`, t("Cấu hình p < 0.05 (ngẫu nhiên ≈ {e})", {e: fmt(sm.expected, 0)}))
    + kpi(sm.min_q < 0.05 ? "--good" : "--axis", fmt(sm.min_p, 3), t("p nhỏ nhất · q nhỏ nhất {q}", {q: fmt(sm.min_q, 2)}))
    + kpi(sm.mean_ari_cons > 0.6 ? "--good" : "--warning", fmt(sm.mean_ari_cons, 2), t("ARI TB với chia đồng thuận"))
    + kpi(dB >= 0.8 ? "--critical" : "--good", `${sizes[0]} / ${sizes[1]}`, t("Chia đồng thuận · |d| độ sáng vỏ {d}", {d: fmt(dB, 1)}));

  // ---------- kết luận ----------
  const grpList = (g) => r.labels.filter((_, i) => cons[i] === g).map(sxShort).join(", ");
  const has = sm.min_q < 0.05 || sm.n_sig > sm.expected * 2;
  const parts = [`<div class="msg ${has ? "ok" : ""}">${has
    ? t("<b>Có dấu hiệu cấu trúc 2 nhóm</b>: {s}/{c} cấu hình vượt ngưỡng ngẫu nhiên (kỳ vọng ≈ {e}).", {s: sm.n_sig, c: sm.n_configs, e: fmt(sm.expected, 0)})
    : t("<b>Chưa thấy 2 nhóm tách rõ</b>: {s}/{c} cấu hình có p < 0.05 trong khi ngẫu nhiên đã kỳ vọng ≈ {e}; p nhỏ nhất {p}. Chia đôi tốt nhất của mỗi cấu hình không mạnh hơn chia đôi dữ liệu không có cụm.", {s: sm.n_sig, c: sm.n_configs, e: fmt(sm.expected, 0), p: fmt(sm.min_p, 3)})}</div>`];
  parts.push(`<div class="msg">${sm.mean_ari_cons < 0.5
    ? t("Các cấu hình <b>không thống nhất</b> cách chia (ARI TB {a} với chia đồng thuận): đổi ngày / đầu đo / đại lượng là trứng đổi nhóm — dấu hiệu của nhiễu hơn là một đặc tính cố định như giới tính.", {a: fmt(sm.mean_ari_cons, 2)})
    : t("Các cấu hình khá thống nhất cách chia (ARI TB {a}).", {a: fmt(sm.mean_ari_cons, 2)})}</div>`);
  parts.push(`<div class="kv"><span><span class="sx-g" style="background:${sxColor(0)}">A</span> ${sizes[0]}</span><b>${esc(grpList(0))}</b>
    <span><span class="sx-g" style="background:${sxColor(1)}">B</span> ${sizes[1]}</span><b>${esc(grpList(1))}</b></div>`);
  if (dB >= 0.8) parts.push(`<div class="msg">${t("Chia đồng thuận trùng với <b>độ sáng vỏ</b> (|d| = {d}, D{day}): nhiều khả năng do vỏ / cách đặt trứng, không phải giới tính.", {d: fmt(dB, 1), day: r.confound.bright_day})}</div>`);
  if (sizes[0] < 3 || sizes[1] < 3) parts.push(`<div class="msg">${t("Chia đồng thuận lệch ({a} / {b}) — với tỷ lệ trống/mái ~1:1 thì cách chia này giống tách vài trứng ngoại lai hơn là 2 giới.", {a: sizes[0], b: sizes[1]})}</div>`);
  if (r.truth) {
    const T = r.truth;
    parts.push(`<div class="msg ${T.p < 0.05 ? "ok" : ""}">${t("Đối chiếu giới tính đã gán ({m} Trống, {f} Mái): chia đồng thuận đúng <b>{a}</b>, ARI {ari}, p chính xác = {p}.", {m: T.n_male, f: T.n_female, a: fmt(T.acc * 100, 0) + "%", ari: fmt(T.ari, 2), p: fmt(T.p, 3)})}</div>`);
  } else {
    parts.push(`<div class="muted small">${t("Khi biết giới tính gà con (≥ 2 mỗi giới), gán Trống / Mái ở bảng Từng trứng để đối chiếu.")}</div>`);
  }
  $("sxVerdict").innerHTML = parts.join("");

  drawSxR2(); drawSxTable(); drawSxCfg(); drawSxConsensus(); drawSxEggs();
}

function drawSxR2() {
  const r = SX.res, cs = r.configs;
  const lim = [Math.min(...cs.map(c => Math.min(c.r2, c.null_p95 ?? c.r2))) - 0.02, Math.max(...cs.map(c => Math.max(c.r2, c.null_p95 ?? c.r2))) + 0.02];
  const col = cs.map((c, i) => i === SX.sel ? css("--accent") : c.p < 0.05 ? css("--good") : css("--muted"));
  Plotly.react("sxR2", [
    {x: lim, y: lim, mode: "lines", line: {color: css("--axis"), dash: "dot", width: 1}, hoverinfo: "skip", showlegend: false},
    {x: cs.map(c => c.null_p95), y: cs.map(c => c.r2), mode: "markers", customdata: cs.map((_, i) => i),
      marker: {color: col, size: cs.map((_, i) => i === SX.sel ? 12 : 7), line: {width: 0}},
      text: cs.map(c => `${sxCfgName(c)}<br>p = ${fmt(c.p, 3)}`), hovertemplate: "%{text}<br>R² %{y:.3f} · null 95% %{x:.3f}<extra></extra>", showlegend: false},
  ], baseLayout({xaxis: {title: {text: t("R² ngưỡng 95% của dữ liệu giả")}, range: lim}, yaxis: {title: {text: t("R² chia đôi tốt nhất")}, range: lim},
    margin: {l: 56, r: 12, t: 8, b: 44}}), PCFG);
  const el = $("sxR2"); el.removeAllListeners?.("plotly_click");
  el.on("plotly_click", ev => { const i = ev.points?.[0]?.customdata; if (i != null) { SX.sel = i; drawSex(); } });
}

function drawSxTable() {
  const r = SX.res, truth = !!r.truth;
  const rows = r.configs.map((c, i) => ({...c, i, name: sxCfgName(c)}));
  const {k, dir} = SX.sort;
  rows.sort((a, b) => { const va = a[k], vb = b[k]; if (va == null) return 1; if (vb == null) return -1;
    return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * dir; });
  const th = (key, txt, title = "") => `<th class="sortable" data-sxsort="${key}" title="${esc(title)}">${txt}${k === key ? (dir > 0 ? " ▲" : " ▼") : ""}</th>`;
  $("sxTable").innerHTML = `<thead><tr>${th("name", t("Cấu hình"))}${th("n_pc", "PC", t("Số PC giữ 95% phương sai"))}
      ${th("r2", "R²", t("1 − SSW/SST của chia đôi tốt nhất"))}${th("null_p95", t("Ngưỡng 95%"), t("R² mà 95% dữ liệu giả không vượt"))}
      ${th("p", "p", t("Tỷ lệ dữ liệu giả có R² ≥ thật"))}${th("q_bh", "q", t("p hiệu chỉnh Benjamini–Hochberg"))}
      ${th("silhouette", "Sil.")}<th>${t("Cỡ")}</th>${th("ari_cons", t("ARI đồng thuận"))}${th("d_bright", t("|d| sáng"), t("Cohen's d độ sáng vỏ giữa 2 nhóm"))}
      ${truth ? th("acc", t("Đúng giới"), t("Tỷ lệ đúng so với Trống / Mái đã gán (chọn chiều tốt hơn)")) : ""}</tr></thead>
    <tbody>${rows.map(c => `<tr data-sxcfg="${c.i}" class="${c.i === SX.sel ? "on" : ""}"><td>${esc(c.name)}</td><td class="num">${c.n_pc}</td>
      <td class="num">${fmt(c.r2, 3)}</td><td class="num">${fmt(c.null_p95, 3)}</td><td class="num ${c.p < 0.05 ? "sig" : ""}">${fmt(c.p, 3)}</td>
      <td class="num">${fmt(c.q_bh, 2)}</td><td class="num">${fmt(c.silhouette, 2)}</td><td class="num">${c.sizes.join("/")}</td>
      <td class="num">${fmt(c.ari_cons, 2)}</td><td class="num">${fmt(c.d_bright, 1)}</td>${truth ? `<td class="num">${fmt(c.acc * 100, 0)}%</td>` : ""}</tr>`).join("")}</tbody>`;
}

const SX_SYM = {male: "square", female: "diamond"};
function drawSxCfg() {
  const r = SX.res, c = r.configs[SX.sel]; if (!c) return;
  $("sxCfgTitle").innerHTML = `${esc(sxCfgName(c))} <span class="sub">R² ${fmt(c.r2, 3)} · p ${fmt(c.p, 3)}</span>`;
  const tr = [0, 1].map(g => {
    const idx = r.labels.map((_, i) => i).filter(i => c.labels[i] === g);
    return {x: idx.map(i => c.pc[i][0]), y: idx.map(i => c.pc[i][1] ?? 0), mode: "markers+text", name: sxName(g),
      text: idx.map(i => sxShort(r.labels[i])), textposition: "top center", textfont: {size: 11, color: sxColor(g)},
      customdata: idx.map(i => r.labels[i]),
      marker: {color: sxColor(g), size: 11, symbol: idx.map(i => SX_SYM[r.sex[i]] || "circle"), line: {width: 1, color: css("--surface")}},
      hovertemplate: "%{customdata}<extra>" + sxName(g) + "</extra>"};
  });
  Plotly.react("sxPca", tr, baseLayout({xaxis: {title: {text: `PC1 (${fmt(c.pc_var[0] * 100, 0)}%)`}, zeroline: false},
    yaxis: {title: {text: `PC2 (${fmt((c.pc_var[1] ?? 0) * 100, 0)}%)`}, zeroline: false},
    legend: {orientation: "h", y: 1.08}, margin: {l: 56, r: 12, t: 24, b: 44}}), PCFG);
  const el = $("sxPca"); el.removeAllListeners?.("plotly_click");
  el.on("plotly_click", ev => { const l = ev.points?.[0]?.customdata; if (l) openEggFromTab(l); });

  // phổ TB ± SD 2 nhóm cho đúng dữ liệu của cấu hình
  const m = c.input.match(/^D(\d+)(?:−D(\d+))?$/), base = r.base_day;
  const late = m ? null : +c.input.match(/‖D(\d+)/)[1];
  const [a, ref] = m ? [+m[1], m[2] ? +m[2] : null] : [late, base];
  const wl = r.wl, ends = c.end === "both" ? ["blunt", "pointed"] : [c.end];
  const deriv = (v) => v.map((_, i) => { const j0 = Math.max(0, i - 1), j1 = Math.min(v.length - 1, i + 1); return (v[j1] - v[j0]) / (wl[j1] - wl[j0]); });
  const vec = (e, l) => { const s = r.spec[e][l]; const v = s[a].map((x, i) => ref == null ? x : x - s[ref][i]); return c.q === "deriv" ? deriv(v) : v; };
  const traces = [];
  for (const e of ends) for (const g of [0, 1]) {
    const L = r.labels.filter((_, i) => c.labels[i] === g); if (!L.length) continue;
    const V = L.map(l => vec(e, l)), mu = wl.map((_, i) => V.reduce((s, v) => s + v[i], 0) / V.length);
    const sd = wl.map((_, i) => Math.sqrt(V.reduce((s, v) => s + (v[i] - mu[i]) ** 2, 0) / Math.max(1, V.length - 1)));
    const col = sxColor(g), dash = e === "pointed" && ends.length > 1 ? "dash" : "solid", nm = `${sxName(g)}${ends.length > 1 ? " · " + SX_END[e] : ""}`;
    traces.push({x: [...wl, ...[...wl].reverse()], y: [...mu.map((v, i) => v + sd[i]), ...mu.map((v, i) => v - sd[i]).reverse()], fill: "toself",
      fillcolor: col + "22", line: {width: 0}, hoverinfo: "skip", showlegend: false, legendgroup: nm});
    traces.push({x: wl, y: mu, mode: "lines", name: nm, legendgroup: nm, line: {color: col, width: 2, dash}, hovertemplate: `${nm}<br>%{x:.0f} nm: %{y:.4f}<extra></extra>`});
  }
  const shapes = r.paper_bands.map(([x0, x1]) => ({type: "rect", xref: "x", yref: "paper", x0, x1, y0: 0, y1: 1, fillcolor: css("--warning"), opacity: 0.12, line: {width: 0}, layer: "below"}));
  const what = ref == null ? `D${a}` : `D${a} − D${ref}`;
  $("sxSpecSub").textContent = `${what} · ${SX_Q[c.q === "paper" ? "shape" : c.q]} · ${t("TB ± SD")}`;
  Plotly.react("sxSpec", traces, baseLayout({xaxis: {title: {text: "λ (nm)"}}, yaxis: {title: {text: c.q === "deriv" ? "d(S/mean)/dλ" : ref == null ? "S / mean band" : "Δ S / mean band"}},
    shapes, legend: {orientation: "h", y: 1.1}, margin: {l: 60, r: 12, t: 24, b: 44}}), PCFG);
}

function drawSxConsensus() {
  const r = SX.res, n = r.labels.length;
  const order = r.labels.map((_, i) => i).sort((a, b) => r.consensus[a] - r.consensus[b] || r.support[b] - r.support[a]);
  const names = order.map(i => sxShort(r.labels[i]));
  Plotly.react("sxSame", [{type: "heatmap", z: order.map(i => order.map(j => r.same[i][j])), x: names, y: names, zmin: 0, zmax: 1,
    colorscale: seqScale(), colorbar: {thickness: 10, len: 0.9, tickfont: {color: css("--ink-2"), size: 10}},
    hovertemplate: "%{y} · %{x}: %{z:.2f}<extra></extra>"}],
    baseLayout({xaxis: {type: "category", tickfont: {size: 11}}, yaxis: {type: "category", autorange: "reversed", tickfont: {size: 11}},
      shapes: (() => { const k = r.consensus.filter(x => x === 0).length; return k > 0 && k < n ? [
        {type: "rect", xref: "x", yref: "y", x0: -0.5, x1: k - 0.5, y0: -0.5, y1: k - 0.5, line: {color: sxColor(0), width: 2}},
        {type: "rect", xref: "x", yref: "y", x0: k - 0.5, x1: n - 0.5, y0: k - 0.5, y1: n - 0.5, line: {color: sxColor(1), width: 2}}] : []; })(),
      margin: {l: 44, r: 12, t: 8, b: 36}}), PCFG);

  const cfg = r.configs.map((c, i) => i).sort((a, b) => r.configs[a].p - r.configs[b].p);
  Plotly.react("sxStrip", [{type: "heatmap", z: order.map(i => cfg.map(k => r.configs[k].labels[i])), y: names, x: cfg.map((_, j) => j + 1),
    text: order.map(() => cfg.map(k => `${sxCfgName(r.configs[k])} · p ${fmt(r.configs[k].p, 3)}`)),
    zmin: 0, zmax: 1, colorscale: [[0, sxColor(0)], [0.5, sxColor(0)], [0.5, sxColor(1)], [1, sxColor(1)]], showscale: false, xgap: 0.5, ygap: 1,
    hovertemplate: "%{y} · %{text}<extra></extra>"}],
    baseLayout({xaxis: {title: {text: t("cấu hình (xếp theo p tăng dần)")}, showgrid: false}, yaxis: {type: "category", autorange: "reversed", tickfont: {size: 11}},
      margin: {l: 44, r: 12, t: 8, b: 44}}), PCFG);
  const el = $("sxStrip"); el.removeAllListeners?.("plotly_click");
  el.on("plotly_click", ev => { const x = ev.points?.[0]?.x; if (x) { SX.sel = cfg[x - 1]; drawSex(); } });
}

function drawSxEggs() {
  const r = SX.res, cf = r.confound;
  $("sxEggs").innerHTML = `<thead><tr><th>${t("Trứng")}</th><th>${t("Ngày nở")}</th><th>${t("Nhóm đồng thuận")}</th>
      <th title="${esc(t("Tỷ lệ cấu hình xếp trứng này cùng nhóm với các trứng cùng nhóm đồng thuận"))}">${t("Mức đồng thuận")}</th>
      <th title="${esc(t("log10 counts trong band, ngày D{d}", {d: cf.bright_day}))}">${t("Độ sáng vỏ · tù")}</th><th title="${esc(t("log10 counts trong band, ngày D{d}", {d: cf.bright_day}))}">${t("Độ sáng vỏ · nhọn")}</th><th>${t("Giới tính")}</th></tr></thead>
    <tbody>${r.labels.map((l, i) => { const g = r.consensus[i];
      return `<tr><td><a class="link" data-open-egg="${esc(l)}">${esc(l)}</a></td><td>${esc(cf.hatch[i] ? fmtDate(cf.hatch[i]) : "—")}</td>
      <td><span class="sx-g" style="background:${sxColor(g)}">${g ? "B" : "A"}</span></td><td class="num"><span class="sbar"><i style="width:${Math.round(r.support[i] * 100)}%"></i></span>${fmt(r.support[i], 2)}</td>
      <td class="num">${fmt(cf.bright.blunt[i], 3)}</td><td class="num">${fmt(cf.bright.pointed[i], 3)}</td>
      <td>${groupSelect(l, `data-sxtag="${esc(l)}"`)}</td></tr>`; }).join("")}</tbody>`;
}

// ---------- events ----------
$("sxLabels").addEventListener("change", e => { localSet(sxKey(), e.target.value); SX.sel = null; renderSex(); });
$("sxHatched").addEventListener("click", () => { $("sxLabels").value = ""; localSet(sxKey(), ""); SX.sel = null; renderSex(); });
$("sxBase").addEventListener("change", e => { SX.base = +e.target.value; SX.sel = null; renderSex(); });
$("sxNull").addEventListener("change", e => { SX.nnull = e.target.value; renderSex(); });
$("sxRun").addEventListener("click", () => { SX.key = null; renderSex(); });
$("sxHelp").addEventListener("click", () => openModal("sxModal"));
// đồ thị trong khung "Chi tiết kỹ thuật" được vẽ khi đang đóng (bề rộng 0) -> co giãn lại khi mở
$("sxMore").addEventListener("toggle", e => { if (e.target.open) ["sxPca", "sxSpec", "sxSame", "sxStrip"].forEach(id => Plotly.Plots.resize($(id))); });
$("sxTable").addEventListener("click", e => {
  const h = e.target.closest("[data-sxsort]");
  if (h) { const k = h.dataset.sxsort; SX.sort = {k, dir: SX.sort.k === k ? -SX.sort.dir : (k === "p" || k === "q_bh" || k === "name" ? 1 : -1)}; drawSxTable(); return; }
  const row = e.target.closest("[data-sxcfg]"); if (row) { SX.sel = +row.dataset.sxcfg; drawSex(); }
});
$("sxEggs").addEventListener("change", e => { const s = e.target.closest("[data-sxtag]"); if (s) setTags({[s.dataset.sxtag]: s.value || null}); });
$("sxEggs").addEventListener("click", e => { const a = e.target.closest("[data-open-egg]"); if (a) openEggFromTab(a.dataset.openEgg); });
