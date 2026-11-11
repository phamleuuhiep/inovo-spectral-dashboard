"use strict";
// ============================================================================
// Tab "Tình trạng": nhật ký tình trạng từng trứng (ngày, tình trạng, ghi chú, ảnh) + cờ tín hiệu phổ tự động.
// Server: status.py (GET/POST /api/status, PUT/GET /api/status_photo). Dùng chung helper của index.html.
// ============================================================================
const ST_META = {
  ok:        {name: t("Bình thường"), color: "--good"},
  watch:     {name: t("Theo dõi"), color: "--warning"},
  abnormal:  {name: t("Bất thường"), color: "--serious"},
  dead:      {name: t("Chết phôi / không nở"), short: t("Chết phôi"), color: "--critical"},
  infertile: {name: t("Không phôi"), color: "--muted"},
  hatched:   {name: t("Đã nở"), color: "--accent"},
  hatched_dead: {name: t("Đã nở nhưng chết"), short: t("Nở rồi chết"), color: "--st-hdead"},
  removed:   {name: t("Đã loại"), color: "--ink-2"},
};
const STS = {data: null, key: null, egg: localGet("st.egg", ""), filt: "", pick: ""};

const today = () => { const d = new Date(), p = n => String(n).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`; };
const fmtDate = (s) => { const [y, m, d] = String(s).split("-"); return d ? `${d}/${m}/${y}` : s; };
const stBatch = (n) => `B${String(n).padStart(2, "0")}`;
function stChip(k, lg = false) {
  const m = ST_META[k]; if (!m) return "";
  return `<span class="st-chip${lg ? " lg" : ""}" style="--c:var(${m.color})"><span class="gdot"></span>${esc(m.name)}</span>`;
}
function eventsOf(l) { return (STS.data?.events || []).filter(e => e.label === l).sort((a, b) => (b.date + b.created).localeCompare(a.date + a.created)); }
function currentStatus(l) { return eventsOf(l)[0] || null; }
function sigOf(l) { return STS.data?.signals?.labels?.[l] || null; }
function flagBadge(l) {
  const s = sigOf(l); if (!s?.latest) return "";
  const crit = s.latest >= 2;
  return `<span class="st-badge ${crit ? "crit" : "warn"}" title="${esc(crit ? t("Tín hiệu phổ bất thường ở batch mới nhất") : t("Tín hiệu phổ lệch nhẹ ở batch mới nhất"))}">${crit ? "⚠" : "!"} ${t("phổ")}</span>`;
}
// bộ lọc khay: "" | "logged" | "none" | "flag" | "warn" | <status>
function passFilter(l) {
  const c = currentStatus(l)?.status || "", f = STS.filt, s = sigOf(l)?.latest || 0;
  return !f || (f === "logged" ? !!c : f === "none" ? !c : f === "flag" ? s >= 2 : f === "warn" ? s === 1 : c === f);
}
const FILT_NAME = () => ({logged: t("Đã ghi"), none: t("Chưa ghi"), flag: t("Cờ phổ bất thường"), warn: t("Phổ lệch nhẹ"),
  ...Object.fromEntries(Object.entries(ST_META).map(([k, m]) => [k, m.name]))});

async function loadStatus(force = false) {
  if (!S.src || !S.data) return null;
  const key = `${S.src}|${S.sig}`;
  if (!force && STS.key === key && STS.data) return STS.data;
  busy(t("Đang tính tín hiệu…"));
  try { STS.data = await getJSON(`/api/status?src=${enc(S.src)}`); STS.key = key; }
  catch (ex) { toast(t("Lỗi: ") + ex.message); return null; }
  finally { busy(); }
  return STS.data;
}

async function renderStatus() {
  if ($("tabStatus").hidden) return;
  const d = await loadStatus(); if (!d) return;
  const labels = S.data.labels;
  if (!labels.includes(STS.egg)) STS.egg = labels[0] || "";
  renderStatusKpis(); renderStatusGrid(); renderStatusEgg(); renderPatterns();
}

// ---------- thư viện pattern ----------
const PAT_COLOR = {crit: "--critical", warn: "--warning", info: "--muted"};
const VARIANT = () => ({abrupt: t("đột ngột"), gradual: t("từ từ")});
const stText = (o) => o ? (LANG_COL == null ? o.vi : (o.en || o.vi)) : "";
function patternsOf(l) {
  return (STS.data?.patterns || []).filter(p => STS.data.matches?.[p.id]?.[l]).map(p => ({p, m: STS.data.matches[p.id][l]}));
}
function matchText(p, m) {
  if (p.rule.kind === "red_drop") return t("{v}, từ {b} · min z {z}", {v: VARIANT()[m.variant] || m.variant, b: stBatch(m.onset), z: fmt(m.min_z, 1)});
  if (p.rule.kind === "jump") return t("{b} · đầu {e} · z NIR {z}", {b: stBatch(m.batch), e: END_LABEL[m.end], z: fmt(m.z_rel, 1)});
  return "";
}
function renderPatterns() {
  const pats = STS.data?.patterns || [];
  $("stPatterns").innerHTML = pats.length ? pats.map(p => {
    const mm = Object.entries(STS.data.matches?.[p.id] || {}), col = PAT_COLOR[p.level] || "--muted";
    const signs = stText(p.signs) || [];
    return `<div class="st-pat" style="--c:var(${col})">
      <div class="pt">${esc(stText(p.name))}</div>
      <div class="row">${t("Trứng khớp hiện tại:")} ${mm.length ? mm.map(([l, m]) =>
        `<button class="egg-btn" data-st-go="${esc(l)}">${t("Trứng {n}", {n: +l})} <small>${esc(matchText(p, m))}</small></button>`).join("") : `<span class="muted">${t("không có")}</span>`}</div>
      <div class="row">${t("Trứng tham chiếu:")} ${p.refs.map(l => `<b>${+l}</b>`).join(", ")}</div>
      <details ${p.level === "crit" ? "open" : ""}><summary>${t("Dấu hiệu nhận biết qua phổ")}</summary>
        <ol>${signs.map(x => `<li>${esc(x)}</li>`).join("")}</ol></details>
    </div>`;
  }).join("") : `<div class="st-empty">${t("Chưa có pattern nào")}</div>`;
}

function renderStatusKpis() {
  const labels = S.data.labels, n = labels.length, counts = {};
  labels.forEach(l => { const c = currentStatus(l)?.status; if (c) counts[c] = (counts[c] || 0) + 1; });
  const logged = Object.values(counts).reduce((a, b) => a + b, 0);
  const nFlag = labels.filter(l => sigOf(l)?.latest >= 2).length, nWarn = labels.filter(l => sigOf(l)?.latest === 1).length;
  const kpi = (f, color, v, k, extra = "") => `<button class="st-kpi${STS.filt === f ? " on" : ""}" data-st-filt="${f}" style="--c:var(${color})">
      <span class="v num">${v}</span><span class="k">${k}</span>${extra}</button>`;
  // chip từng tình trạng: bấm để lọc riêng tình trạng đó
  const chips = Object.keys(ST_META).filter(k => counts[k]).map(k =>
    `<span class="st-chip" data-st-filt="${k}" style="--c:var(${ST_META[k].color})"><span class="gdot"></span>${esc(ST_META[k].short || ST_META[k].name)} <b class="num">${counts[k]}</b></span>`).join("");
  $("stKpis").innerHTML =
    kpi("logged", "--accent", `${logged}<small>/${n}</small>`, t("Đã ghi tình trạng"), `<span class="bd">${chips}</span>`)
    + kpi("none", "--axis", n - logged, t("Chưa ghi"))
    + kpi("flag", "--critical", nFlag, `<span class="st-badge crit">⚠</span>${t("Cờ phổ bất thường")}`)
    + kpi("warn", "--warning", nWarn, `<span class="st-badge warn">!</span>${t("Phổ lệch nhẹ")}`)
    + `<div class="st-kpi-tools"><button class="icon-btn ghost" data-st-help title="${esc(t("Cách dùng"))}">${svg("help")}</button></div>`;
}

function renderStatusGrid() {
  const shown = S.data.labels.filter(passFilter);
  $("stGrid").innerHTML = shown.map(l => {
    const c = currentStatus(l), m = c && ST_META[c.status];
    return `<button class="st-tile${m ? " has" : ""}${l === STS.egg ? " on" : ""}" data-st-egg="${esc(l)}" style="--c:var(${m ? m.color : "--axis"})">
      <span class="n num">${+l}</span>${flagBadge(l)}
      <span class="s">${m ? esc(m.short || m.name) : t("Chưa ghi")}</span>
      <span class="d num">${c ? fmtDate(c.date) : "&nbsp;"}</span></button>`;
  }).join("") || `<div class="st-empty">${t("Không có trứng")}</div>`;
  $("stFiltTxt").textContent = STS.filt ? t("Lọc: {f} · {n} trứng", {f: FILT_NAME()[STS.filt], n: shown.length}) : t("{n} trứng", {n: shown.length});
  $("stClearFilt").hidden = !STS.filt;
}

function renderStatusPick() {
  $("stPick").innerHTML = Object.entries(ST_META).map(([k, m]) =>
    `<button type="button" data-st-pick="${k}" class="${STS.pick === k ? "on" : ""}" style="--c:var(${m.color})"><span class="gdot"></span>${esc(m.name)}</button>`).join("");
}

function renderStatusEgg() {
  const l = STS.egg; if (!l) return;
  const cur = currentStatus(l), ev = eventsOf(l);
  $("stEggTitle").textContent = t("Trứng {n}", {n: +l});
  $("stEggBadge").innerHTML = cur ? stChip(cur.status, true) : `<span class="st-chip lg" style="--c:var(--axis)">${t("Chưa ghi")}</span>`;
  $("stEggSub").textContent = `label ${l}` + (cur ? ` · ${t("cập nhật {d}", {d: fmtDate(cur.date)})}` : "");
  if (!$("stDate").value) $("stDate").value = today();
  renderStatusPick();
  $("stHist").innerHTML = ev.length ? ev.map(e => `
    <div class="st-ev" style="--c:var(${ST_META[e.status]?.color || "--axis"})">
      <div class="st-ev-h"><span class="num">${fmtDate(e.date)}</span>${stChip(e.status)}<span class="spacer"></span>
        <button class="icon-btn ghost sm" data-st-del="${esc(e.id)}" title="${esc(t("Xoá"))}">${svg("x")}</button></div>
      ${e.note ? `<div class="st-note">${esc(e.note)}</div>` : ""}
      ${e.photo ? `<a href="/api/status_photo?src=${enc(S.src)}&f=${enc(e.photo)}" target="_blank"><img class="st-photo" src="/api/status_photo?src=${enc(S.src)}&f=${enc(e.photo)}" alt=""></a>` : ""}
      <div class="muted small">${t("ghi lúc {ts}", {ts: fmtTs(e.created)})}</div>
    </div>`).join("") : `<div class="st-empty">${t("Chưa có ghi chép nào cho trứng này.")}</div>`;
  renderStatusSignal(l);
}

// kết luận bằng lời cho batch mới nhất có cờ cao nhất
function verdictOf(s, th) {
  const last = [...s.rows].reverse().find(r => r.blunt || r.pointed);
  if (!last) return null;
  let worst = null;
  for (const end of ["blunt", "pointed"]) {
    const v = last[end]; if (!v) continue;
    if (!worst || v.flag > worst.v.flag || (v.flag === worst.v.flag && (v.z_rn ?? 0) < (worst.v.z_rn ?? 0))) worst = {end, v};
  }
  const lvl = worst?.v.flag || 0, w = worst?.v, e = worst ? END_LABEL[worst.end] : "";
  let why = "";
  if (lvl && w) {
    if (w.z_rn != null && w.z_rn <= th.watch.rn) why = t("Đỏ/NIR thấp bất thường (z = {z}, đầu {e}) — đỏ bị hấp thụ mạnh, giống pattern trứng 29", {z: fmt(w.z_rn, 1), e});
    else if (w.z_rel != null && w.z_rel >= th.watch.rel) why = t("NIR sáng hơn đàn (z = {z}, đầu {e}) — ngừng phát triển hoặc phổ nhiễu", {z: fmt(w.z_rel, 1), e});
  }
  return {lvl, batch: stBatch(last.batch_no), age: last.age_h, why};
}

function renderStatusSignal(l) {
  const s = sigOf(l), sig = STS.data.signals;
  if (!s) { Plotly.purge("stSig"); $("stVerdict").innerHTML = `<div class="st-empty">${t("không có phổ")}</div>`; return; }
  const th = sig.thresholds, v = verdictOf(s, th);
  if (v) {
    const C = [["--good", "✓", t("Không có dấu hiệu bất thường")], ["--warning", "!", t("Lệch nhẹ — nên theo dõi")], ["--critical", "⚠", t("Tín hiệu bất thường — nên soi lại")]][v.lvl];
    $("stVerdict").innerHTML = `<div class="st-verdict" style="--c:var(${C[0]})"><span class="ico">${C[1]}</span>
      <div><div class="vt">${C[2]}</div><div class="vd">${t("Batch mới nhất {b} ({h} h)", {b: v.batch, h: fmt(v.age, 0)})}${v.why ? " · " + esc(v.why) : ""}</div>
      ${patternsOf(l).map(({p, m}) => `<div class="pm">${t("Khớp pattern:")} <b>${esc(stText(p.name))}</b> — ${esc(matchText(p, m))}</div>`).join("")}</div></div>`;
  }
  const x = s.rows.map(r => stBatch(r.batch_no));
  const col = {blunt: css("--s-blunt"), pointed: css("--s-pointed")};
  const tr = [];
  for (const end of ["blunt", "pointed"]) {
    tr.push({x, y: s.rows.map(r => r[end]?.z_rn ?? null), name: t("Đỏ/NIR · {e}", {e: END_LABEL[end]}), mode: "lines+markers",
      line: {color: col[end], width: 2.5}, marker: {size: 9}, hovertemplate: "%{x}: z = %{y:.1f}<extra>%{fullData.name}</extra>"});
    tr.push({x, y: s.rows.map(r => r[end]?.z_rel ?? null), name: t("Độ sáng NIR · {e}", {e: END_LABEL[end]}), mode: "lines+markers",
      line: {color: col[end], width: 1.5, dash: "dot"}, marker: {size: 6, symbol: "square"}, opacity: 0.75,
      hovertemplate: "%{x}: z = %{y:.1f}<extra>%{fullData.name}</extra>"});
  }
  const ys = tr.flatMap(t => t.y).filter(v => v != null);
  const lo = Math.min(th.flag.rn - 2, ...ys) - 0.5, hi = Math.max(th.flag.rel + 1.5, ...ys) + 0.5;
  const band = (y0, y1, c, a) => ({type: "rect", xref: "paper", x0: 0, x1: 1, y0, y1, fillcolor: c, opacity: a, line: {width: 0}, layer: "below"});
  const lab = (y, txt, c) => ({xref: "paper", x: 1, y, text: txt, showarrow: false, xanchor: "right", yanchor: "bottom", font: {size: 11, color: c}});
  Plotly.react("stSig", tr, baseLayout({
    yaxis: {title: {text: "robust z"}, range: [lo, hi], zeroline: true}, xaxis: {type: "category"},
    shapes: [band(lo, th.flag.rn, css("--critical"), 0.10), band(th.flag.rn, th.watch.rn, css("--warning"), 0.12),
             band(th.flag.rel, hi, css("--critical"), 0.06)],
    annotations: [lab(th.flag.rn, t("cờ đỏ/NIR ≤ {v}", {v: th.flag.rn}), css("--critical")), lab(th.watch.rn, t("lệch nhẹ ≤ {v}", {v: th.watch.rn}), css("--ink-2")),
                  lab(th.flag.rel, t("cờ sáng NIR ≥ {v}", {v: th.flag.rel}), css("--critical"))],
    legend: {orientation: "h", y: -0.14, x: 0}, margin: {l: 56, r: 12, t: 8, b: 40},
  }), PCFG);
}

async function saveStatus() {
  const l = STS.egg, status = STS.pick, date = $("stDate").value;
  if (!l || !status || !date) return toast(t("Chọn tình trạng và ngày"));
  busy(t("Đang lưu…"));
  try {
    let photo = null;
    const f = $("stPhoto").files[0];
    if (f) {
      const r = await fetch(`/api/status_photo?src=${enc(S.src)}&name=${enc(f.name)}`, {method: "PUT", body: f});
      const j = await r.json(); if (!r.ok) throw new Error(tMsg(j.error)); photo = j.photo;
    }
    const j = await postJSON("/api/status", {src: S.src, event: {label: l, status, date, note: $("stNote").value, photo}});
    STS.data.events = j.events; S.hatchedSession = null;
    if (STS.egg === l) resetStatusForm();
    toast(t("Đã lưu tình trạng trứng {n}", {n: +l}));
    renderStatus();
  } catch (ex) { toast(t("Lỗi: ") + ex.message); }
  finally { busy(); }
}

// form nhập dùng chung cho mọi trứng -> đặt lại mỗi khi đổi trứng, để lựa chọn của trứng trước không mang sang trứng sau
function resetStatusForm() {
  STS.pick = ""; $("stNote").value = ""; $("stPhoto").value = ""; $("stDate").value = today();
  $("stFileTxt").textContent = t("Ảnh"); $("stFileBtn").classList.remove("has");
  renderStatusPick();
}

$("stGrid").addEventListener("click", e => {
  const b = e.target.closest("[data-st-egg]"); if (!b || b.dataset.stEgg === STS.egg) return;
  STS.egg = b.dataset.stEgg; localSet("st.egg", STS.egg); resetStatusForm(); renderStatusGrid(); renderStatusEgg();
});
$("stKpis").addEventListener("click", e => {
  if (e.target.closest("[data-st-help]")) return openModal("statusModal");
  const b = e.target.closest("[data-st-filt]"); if (!b) return;
  e.stopPropagation();
  const f = b.dataset.stFilt; STS.filt = STS.filt === f ? "" : f;
  renderStatusKpis(); renderStatusGrid();
});
$("stPatterns").addEventListener("click", e => {
  const b = e.target.closest("[data-st-go]"); if (!b) return;
  if (b.dataset.stGo !== STS.egg) { STS.egg = b.dataset.stGo; localSet("st.egg", STS.egg); resetStatusForm(); renderStatusGrid(); renderStatusEgg(); }
  $("stEggTitle").scrollIntoView({behavior: "smooth", block: "start"});
});
$("stClearFilt").addEventListener("click", () => { STS.filt = ""; renderStatusKpis(); renderStatusGrid(); });
$("stPick").addEventListener("click", e => {
  const b = e.target.closest("[data-st-pick]"); if (!b) return;
  STS.pick = STS.pick === b.dataset.stPick ? "" : b.dataset.stPick; renderStatusPick();
});
$("stPhoto").addEventListener("change", () => {
  const f = $("stPhoto").files[0];
  $("stFileTxt").textContent = f ? (f.name.length > 18 ? f.name.slice(0, 15) + "…" : f.name) : t("Ảnh");
  $("stFileBtn").classList.toggle("has", !!f);
});
$("stHist").addEventListener("click", async e => {
  const b = e.target.closest("[data-st-del]"); if (!b || !confirm(t("Xoá ghi chép này?"))) return;
  try { const j = await postJSON("/api/status", {src: S.src, op: "delete", id: b.dataset.stDel}); STS.data.events = j.events; S.hatchedSession = null; renderStatus(); }
  catch (ex) { toast(t("Lỗi: ") + ex.message); }
});
$("stNote").addEventListener("keydown", e => { if (e.key === "Enter") saveStatus(); });
$("stSave").addEventListener("click", saveStatus);
$("stOpenEgg").addEventListener("click", () => { if (STS.egg) { setTab("egg"); loadEgg(STS.egg); } });
$("stHelp").addEventListener("click", () => openModal("statusModal"));

// ----------------------------------------------------------------------------
// Lần cập nhật dashboard (header)
// ----------------------------------------------------------------------------
async function loadVersion() {
  try {
    const v = await getJSON("/api/version");
    if (!v.updated) return;
    const d = new Date(v.updated), p = n => String(n).padStart(2, "0");
    $("verBtn").textContent = t("Cập nhật {ts}", {ts: `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`});
    $("verBtn").hidden = false;
    $("verList").innerHTML = `<div class="muted small">${t("Code dashboard sửa lần cuối: {ts}", {ts: v.updated.replace("T", " ")})}</div>`
      + (v.changes || []).map(c => `<div class="ver-it"><span class="num muted">${esc(c.date)}</span><span>${esc(LANG_COL == null ? c.vi : (c.en || c.vi))}</span></div>`).join("");
  } catch {}
}
loadVersion();
