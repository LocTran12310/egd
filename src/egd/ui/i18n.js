/* EGD UI — languages. English is the source; other languages map English text to their own.
   t("Inbox") → "Hộp việc" in Vietnamese; t("{n} tasks", {n: 3}) fills placeholders.
   Missing entries fall back to English, so an untranslated string is never blank.
   Data (feature titles, engine messages, what people typed) is never translated.
   tests/test_i18n_keys.py checks that every literal t()/tHtml()/N_() key has a vi entry (ui/i18n-vi.js).
   A snapshot inlines every language; a served page fetches a language on first use. */
const LANGS = { en: "English", vi: "Tiếng Việt" };
const I18N = {};  // language → { English text: translation }, filled by ui/i18n-<lang>.js
// Read once, then remembered: t() runs thousands of times per render. A language switch goes
// through store.set("lang", …), which forgets it (wrapped below).
let LANG_NOW = null;
function getLang() {
  if (LANG_NOW) return LANG_NOW;
  const saved = store.get("lang", null);
  LANG_NOW = saved && LANGS[saved] ? saved : (navigator.language || "").toLowerCase().startsWith("vi") ? "vi" : "en";
  return LANG_NOW;
}
{ const set = store.set; store.set = (k, v) => { if (k === "lang") LANG_NOW = null; set.call(store, k, v); }; }
function t(s, vars) {
  const lang = getLang();
  let out = (lang !== "en" && I18N[lang] && I18N[lang][s]) || s;
  if (vars) out = out.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));
  return out;
}
/* like t(), but the text is escaped and each {name} becomes the given (trusted) HTML */
function tHtml(s, html) {
  return esc(t(s)).replace(/\{(\w+)\}/g, (m, k) => (k in html ? html[k] : m));
}
/* marks an English string for translation where it is defined (tables of labels); t() it when shown */
function N_(s) { return s; }
/* static markup carries data-i18n="English text" (and data-i18n-title / data-i18n-aria); call after every render */
function applyStaticI18n(root = document) {
  root.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll("[data-i18n-title]").forEach(el => { el.title = t(el.dataset.i18nTitle); });
  root.querySelectorAll("[data-i18n-aria]").forEach(el => { el.setAttribute("aria-label", t(el.dataset.i18nAria)); });
  document.documentElement.lang = getLang();
}
