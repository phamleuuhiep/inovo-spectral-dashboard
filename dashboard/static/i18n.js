"use strict";
// ============================================================================
// Đa ngôn ngữ. Tiếng Việt là ngôn ngữ gốc: khoá từ điển = câu tiếng Việt trong code / HTML.
//   I18N_STR  (i18n_data.js): {"câu tiếng Việt": [English, 简体中文, 繁體中文]}, {tên} = tham số
//   I18N_HTML (i18n_data.js): khối trợ giúp dài, theo thuộc tính data-i18n="khoá"
//   I18N_PAT  (i18n_data.js): mẫu regex cho thông điệp có tham số từ server (lỗi, cảnh báo, tiến độ)
// Đổi ngôn ngữ = lưu vào localStorage rồi tải lại trang (mọi đồ thị / bảng vẽ lại bằng ngôn ngữ mới).
// ============================================================================
const LANGS = [["vi", "Tiếng Việt"], ["en", "English"], ["zh-Hant", "繁體中文"], ["zh-Hans", "简体中文"]];
const LANG = (() => { try { const l = localStorage.getItem("dash.lang"); return LANGS.some(x => x[0] === l) ? l : "vi"; } catch { return "vi"; } })();
const LANG_COL = {en: 0, "zh-Hans": 1, "zh-Hant": 2}[LANG];          // undefined = tiếng Việt
const LOCALE = {vi: "vi-VN", en: "en-US", "zh-Hant": "zh-TW", "zh-Hans": "zh-CN"}[LANG];
document.documentElement.lang = LANG;

function t(s, vars) {
  let out = LANG_COL == null ? s : (I18N_STR[s]?.[LANG_COL] ?? s);
  if (vars) out = out.replace(/\{(\w+)\}/g, (m, k) => k in vars ? vars[k] : m);
  return out;
}
// Thông điệp do server tạo ra: khớp nguyên câu, rồi theo mẫu ($1, $2 = phần bắt được, cũng được dịch nếu có)
function tMsg(s) {
  if (LANG_COL == null || s == null) return s;
  s = String(s);
  if (I18N_STR[s]) return I18N_STR[s][LANG_COL];
  for (const [re, tr] of I18N_PAT) {
    const m = s.match(re);
    if (m) return tr[LANG_COL].replace(/\$(\d)/g, (_, i) => tMsg(m[+i] ?? ""));
  }
  return s;
}
// Dịch phần HTML tĩnh: khối data-i18n, mọi text node và thuộc tính title / placeholder / aria-label khớp từ điển
function i18nDom(root = document.body) {
  if (LANG_COL == null) return;
  root.querySelectorAll("[data-i18n]").forEach(el => { const h = I18N_HTML[el.dataset.i18n]?.[LANG_COL]; if (h != null) el.innerHTML = h; });
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {acceptNode: n =>
    n.parentElement?.closest("script, style, [data-i18n]") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT});
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    const raw = n.nodeValue, key = raw.replace(/\s+/g, " ").trim(), tr = key && I18N_STR[key];
    if (tr) n.nodeValue = raw.match(/^\s*/)[0] + tr[LANG_COL] + raw.match(/\s*$/)[0];
  }
  root.querySelectorAll("[title], [placeholder], [aria-label]").forEach(el => ["title", "placeholder", "aria-label"].forEach(a => {
    const v = el.getAttribute(a), tr = v && I18N_STR[v];
    if (tr) el.setAttribute(a, tr[LANG_COL]);
  }));
}
function initLangSelect(sel) {
  sel.innerHTML = LANGS.map(([k, nm]) => `<option value="${k}">${nm}</option>`).join("");
  sel.value = LANG;
  sel.addEventListener("change", () => { try { localStorage.setItem("dash.lang", sel.value); } catch {} location.reload(); });
}
