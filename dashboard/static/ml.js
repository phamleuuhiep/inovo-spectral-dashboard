"use strict";
// ============================================================================
// Tab "Học máy": xếp hạng ứng viên không phôi khi mới có ít nhãn (chỉ số phát triển, giống trứng đã biết,
// PU learning), học có giám sát khi đủ ≥ 3 nhãn mỗi lớp; về sau dùng cho Trống / Mái. Tính toán ở ml.py.
// Dùng chung state/helper của index.html, compare.js, unsup.js (S, C, $, groupSelect, setTags, uDays, ...).
// ============================================================================
const ML = {
  task: localGet("ml.task", "infertile"), feats: localGet("ml.feats", "dev"), ends: localGet("ml.ends", "both"),
  model: localGet("ml.model", "lr"), nperm: localGet("ml.nperm", "50"), traj: localGet("ml.traj", "shape"),
  da: null, db: null, dr: null, res: null, key: null, seq: 0, sort: {k: "rank", dir: 1},
};
const ML_TASK = {infertile: {pos: t("Không phôi"), neg: t("Có phôi"), list: t("Ứng viên không phôi")}, sex: {pos: t("Trống"), neg: t("Mái"), list: t("Dự đoán Trống / Mái")}};

function mlDefaults() {
  const days = uDays(), ds = days.map(x => x.d);
  // Trống/Mái: bài báo dùng D2 + D5; không phôi: ngày giữa và ngày cuối
  const want = ML.task === "sex" ? [2, 5, 1] : [3, ds.at(-1), 2];
  const pick = (w, i) => ds.includes(w) ? w : ds[Math.min(i, ds.length - 1)];
  if (!ds.includes(ML.da)) ML.da = pick(want[0], 2);
  if (!ds.includes(ML.db)) ML.db = pick(want[1], ds.length - 1);
  if (!ds.includes(ML.dr)) ML.dr = pick(want[2], 1);
  $("mlDA").innerHTML = $("mlDB").innerHTML = $("mlDR").innerHTML = days.map(x => `<option value="${x.d}">${esc(dayLabel(x))}</option>`).join("");
  $("mlDA").value = ML.da; $("mlDB").value = ML.db; $("mlDR").value = ML.dr;
  $("mlFeats").value = ML.feats; $("mlModel").value = ML.model; $("mlPerm").value = ML.nperm; $("mlTrajKind").value = ML.traj;
  document.querySelectorAll("#mlTask button").forEach(b => b.classList.toggle("on", b.dataset.v === ML.task));
  document.querySelectorAll("#mlEnds button").forEach(b => b.classList.toggle("on", b.dataset.v === ML.ends));
  document.querySelectorAll("[data-mlspec]").forEach(el => el.hidden = ML.feats === "dev");
  $("mlListTitle").firstChild.textContent = ML_TASK[ML.task].list + " ";
}

// gọi từ refreshTab (mở tab, đổi nhóm, đổi dữ liệu): chỉ chạy lại khi cấu hình / nhãn / dữ liệu thay đổi
function renderMl() {
  if (!C.data || $("tabMl").hidden) return;
  mlDefaults();
  const key = JSON.stringify([S.src, S.sig, S.tags, ML.task, ML.feats, ML.ends, ML.da, ML.db, ML.dr, ML.model, ML.nperm, band(), $("xLo").value, $("xHi").value]);
  if (key !== ML.key) runMl(key); else drawMl();
}
async function runMl(key) {
  ML.key = key; const seq = ++ML.seq;
  $("mlStatus").textContent = t("Đang tính…"); $("mlRun").disabled = true;
  try {
    const [lo, hi] = band();
    const res = await postJSON("/api/ml", {src: S.src, task: ML.task, feats: ML.feats, ends: ML.ends, da: ML.da, db: ML.db, dr: ML.dr,
      model: ML.model, n_perm: +ML.nperm, lo, hi, x0: +$("xLo").value, x1: +$("xHi").value});
    if (seq !== ML.seq) return;
    ML.res = res; drawMl();
  } catch (ex) { ML.key = null; $("mlStatus").innerHTML = `<span class="flag">${esc(ex.message)}</span>`; }
  finally { if (seq === ML.seq) $("mlRun").disabled = false; }
}

function drawMl() {
  const r = ML.res; if (!r) return;
  const T = ML_TASK[r.task], n = r.labels.length;
  const cnt = (g) => r.groups.filter(x => x === g).length;
  const fert = r.groups.filter(x => ["male", "female", "fertile"].includes(x)).length;
  $("mlStatus").innerHTML = t("{n} trứng · {f} đặc trưng · ngày {d}", {n, f: r.n_features, d: r.days.map(d => "D" + d).join(", ")}) + " · " +
    t("nhãn: Không phôi {i} · Có phôi {f} (Trống {m}, Mái {fe})", {i: cnt("infertile"), f: fert, m: cnt("male"), fe: cnt("female")}) +
    (r.excluded.length ? " · " + t("bỏ {l} (thiếu dữ liệu)", {l: r.excluded.join(", ")}) : "");

  // ---------- bảng ----------
  const rk = r.rank, sup = r.sup;
  const rows = r.labels.map((l, i) => ({l, i, g: r.groups[i], rank: rk?.rank[i], comb: rk?.comb[i], dev: r.dev[i],
    sim: rk?.p_sim[i], pu: rk?.p_pu[i], sup: sup?.score[i]}));
  const k = ML.sort.k, dir = ML.sort.dir;
  const defaultKey = rk ? "rank" : sup ? "sup" : "dev";
  const sk = rows[0]?.[k] === undefined || (k === "rank" && !rk) ? defaultKey : k;
  rows.sort((a, b) => { const va = a[sk], vb = b[sk]; if (va == null) return 1; if (vb == null) return -1;
    return (sk === "rank" || sk === "dev" ? va - vb : vb - va) * dir || a.l.localeCompare(b.l); });
  const bar = (v) => v == null ? `<span class="muted">—</span>` : `<span class="sbar"><i style="width:${Math.round(v * 100)}%"></i></span>${fmt(v, 2)}`;
  const th = (key, txt, title) => `<th class="sortable" data-mlsort="${key}" title="${esc(title)}">${txt}${sk === key ? (dir > 0 ? " ▲" : " ▼") : ""}</th>`;
  const isPos = (g) => r.task === "infertile" ? g === "infertile" : g === "male";
  $("mlTable").innerHTML = `<thead><tr>${rk ? th("rank", t("Hạng"), t("Theo điểm tổng hợp")) : ""}<th>${t("Trứng")}</th><th>${t("Nhóm")}</th>
      ${rk ? th("comb", t("Tổng hợp"), t("Trung bình phân vị hạng: 1 = giống không phôi nhất")) : ""}
      ${th("dev", t("Phát triển"), t("Chỉ số phát triển (không dùng nhãn): âm = phát triển ít"))}
      ${rk ? th("sim", t("Giống"), t("Giống các trứng không phôi đã gán (phân vị)")) + th("pu", "PU", t("PU learning (phân vị)")) : ""}
      ${sup ? th("sup", sup.model.toUpperCase(), t("Xác suất {p} — trứng đã gán: điểm kiểm định chéo; chưa gán: dự đoán", {p: T.pos})) : ""}</tr></thead>
    <tbody>${rows.map(x => `<tr class="${isPos(x.g) ? "lab-pos" : ""}">${rk ? `<td class="num">${x.rank ?? "—"}</td>` : ""}
      <td><a class="link" data-open-egg="${esc(x.l)}">${esc(x.l)}</a></td><td>${groupSelect(x.l, `data-mltag="${esc(x.l)}"`)}</td>
      ${rk ? `<td class="num">${bar(x.comb)}</td>` : ""}<td class="num">${fmt(x.dev, 2)}</td>
      ${rk ? `<td class="num">${fmt(x.sim, 2)}</td><td class="num">${fmt(x.pu, 2)}</td>` : ""}
      ${sup ? `<td class="num">${bar(x.sup)}</td>` : ""}</tr>`).join("")}</tbody>`;
  $("mlListSub").textContent = rk ? t("gán nhãn ngay trong bảng → tự xếp hạng lại") : sup ? "" : t("chưa đủ nhãn");

  // ---------- kiểm chứng ----------
  const parts = [];
  (r.check || []).forEach(c => parts.push(`<div class="msg ${c.dev_rank <= Math.max(3, Math.ceil(c.n * 0.1)) ? "ok" : ""}">${t("Trứng không phôi đã biết <b>{l}</b> đứng <b>{r}/{n}</b> theo chỉ số phát triển — chỉ số này <b>không dùng nhãn</b>, nên đây là phép kiểm tra độc lập: {v}", {l: esc(c.label), r: c.dev_rank, n: c.n, v: c.dev_rank <= 3 ? t("cách đo phát triển nhận ra được trứng không phôi.") : t("chưa tách rõ trứng này, cần thêm nhãn.")})}</div>`));
  if (sup) {
    const p = sup.perm, sig = p && p.p < 0.05;
    parts.push(`<div class="kv"><span>${t("Học có giám sát")}</span><b>${sup.model.toUpperCase()} · ${sup.n_pos} ${T.pos} / ${sup.n_neg} ${T.neg}</b>
      <span>${t("AUC kiểm định chéo")}</span><b>${fmt(sup.auc, 3)} ± ${fmt(sup.auc_sd, 3)} (${sup.k} fold × ${sup.repeats})</b>
      ${p ? `<span>${t("AUC khi xáo nhãn")}</span><b>${t("TB {m} · 95%: {p95} · p = {p} ({n} lần)", {m: fmt(p.mean, 2), p95: fmt(p.p95, 2), p: fmt(p.p, 3), n: p.n})}</b>` : ""}</div>
      <div class="msg ${sig ? "ok" : ""}">${!p ? t("Chưa chạy hoán vị — chưa biết AUC có hơn may rủi không.") : sig ? t("<b>Có tín hiệu</b>: AUC vượt rõ các lần xáo nhãn.") : t("<b>Chưa đủ bằng chứng</b>: AUC chưa vượt rõ mức may rủi — cần thêm nhãn.")}</div>`);
  }
  r.messages.forEach(m => parts.push(`<div class="msg">${esc(tMsg(m))}</div>`));
  if (r.task === "infertile" && !sup) parts.push(`<div class="muted small">${t("Gợi ý: kiểm tra 5 ứng viên đầu bằng ảnh, ghi chú soi trứng (khung Batch ở tab Từng trứng) hoặc soi lại; gán <b>Không phôi</b> / <b>Có phôi</b> khi chắc chắn. Đủ ≥ 3 mỗi lớp sẽ bật học có giám sát.")}</div>`);
  $("mlCheck").innerHTML = parts.join("");

  // ---------- quỹ đạo ----------
  const top = new Set(rk ? rows.filter(x => !x.g).sort((a, b) => a.rank - b.rank).slice(0, 5).map(x => x.l) : []);
  const xs = r.days.map(d => "D" + d), tr = [];
  const style = (l, g) => g === "infertile" ? {c: css("--critical"), w: 3, o: 1, lab: true}
    : ["male", "female", "fertile"].includes(g) ? {c: css("--good"), w: 2, o: 0.9, lab: true}
    : top.has(l) ? {c: css("--s-pointed"), w: 2.2, o: 1, lab: true} : {c: css("--muted"), w: 1, o: 0.45, lab: false};
  const order = r.labels.map((l, i) => i).sort((a, b) => +!!style(r.labels[a], r.groups[a]).lab - +!!style(r.labels[b], r.groups[b]).lab);
  for (const i of order) {
    const l = r.labels[i], st = style(l, r.groups[i]), y = r.traj[l][ML.traj];
    tr.push({x: xs, y, mode: st.lab ? "lines+markers+text" : "lines", name: l, line: {color: st.c, width: st.w}, opacity: st.o,
      marker: {size: 5, color: st.c}, text: y.map((_, k) => st.lab && k === y.length - 1 ? l : ""), textposition: "middle right",
      textfont: {size: 10, color: st.c}, hovertemplate: `${l}<br>%{x}: %{y:.4f}<extra></extra>`, showlegend: false});
  }
  Plotly.react("mlTraj", tr, baseLayout({xaxis: {type: "category"}, yaxis: {title: {text: ML.traj === "shape" ? t("Đổi hình dạng (TB 2 đầu)") : "ΔA band (OD)"}},
    margin: {l: 56, r: 40, t: 8, b: 32}}), PCFG);
  const el = $("mlTraj"); el.removeAllListeners?.("plotly_click");
  el.on("plotly_click", ev => { const l = ev.points?.[0]?.data?.name; if (l) openEggFromTab(l); });
}

// ---------- events ----------
$("mlTask").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return;
  ML.task = b.dataset.v; localSet("ml.task", ML.task); ML.da = ML.db = ML.dr = null;
  if (ML.task === "sex" && ML.feats === "dev") ML.feats = "spec";   // giới tính không có tín hiệu "phát triển"
  renderMl(); });
$("mlEnds").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; ML.ends = b.dataset.v; localSet("ml.ends", ML.ends); renderMl(); });
[["mlFeats", "feats"], ["mlModel", "model"], ["mlPerm", "nperm"]].forEach(([id, k]) => $(id).addEventListener("change", e => { ML[k] = e.target.value; localSet("ml." + k, ML[k]); renderMl(); }));
[["mlDA", "da"], ["mlDB", "db"], ["mlDR", "dr"]].forEach(([id, k]) => $(id).addEventListener("change", e => { ML[k] = +e.target.value; renderMl(); }));
$("mlTrajKind").addEventListener("change", e => { ML.traj = e.target.value; localSet("ml.traj", ML.traj); drawMl(); });
$("mlRun").addEventListener("click", () => { ML.key = null; renderMl(); });
$("mlHelp").addEventListener("click", () => openModal("mlModal"));
$("mlTable").addEventListener("change", e => { const s = e.target.closest("[data-mltag]"); if (s) setTags({[s.dataset.mltag]: s.value || null}); });
$("mlTable").addEventListener("click", e => {
  const h = e.target.closest("[data-mlsort]");
  if (h) { const k = h.dataset.mlsort; ML.sort = {k, dir: ML.sort.k === k ? -ML.sort.dir : 1}; drawMl(); return; }
  const a = e.target.closest("[data-open-egg]"); if (a) openEggFromTab(a.dataset.openEgg);
});
