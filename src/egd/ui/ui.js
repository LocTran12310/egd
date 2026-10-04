/* EGD UI — shared helpers and components for every EGD page. */
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("egd:" + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("egd:" + k, JSON.stringify(v)); } catch (e) {} },
};
const ago = iso => {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return t("just now"); if (s < 3600) return t("{n}m ago", { n: Math.floor(s / 60) });
  if (s < 86400) return t("{n}h ago", { n: Math.floor(s / 3600) }); return t("{n}d ago", { n: Math.floor(s / 86400) });
};
const when = iso => {   // local time, as the status bar's
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return esc(String(iso).replace("T", " ").slice(0, 16));
  const p = n => String(n).padStart(2, "0");
  return esc(`${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`);
};
const HEALTH = { green: ["ok", N_("Healthy")], amber: ["warn", N_("Attention")], red: ["bad", N_("At risk")] };
const STATUS = { todo: "neutral", ready: "info", doing: "info", review: "warn", blocked: "bad", done: "ok",
  pass: "ok", fail: "bad", stale: "warn", unrun: "neutral", missing: "neutral", unavailable: "neutral", error: "bad", refused: "bad",
  pending: "warn", approved: "ok", rejected: "bad", open: "warn", confirmed: "ok", accepted: "ok", proposed: "warn",
  fixed: "ok", closed: "ok", critical: "bad", major: "bad", minor: "neutral", trivial: "neutral",
  ok: "ok", warn: "warn", bad: "bad", info: "info", neutral: "neutral" };
// st(word) shows the status word in the reader's language; st(cls, label) shows a label already translated
const STATUS_WORDS = [N_("todo"), N_("ready"), N_("doing"), N_("review"), N_("blocked"), N_("done"), N_("pass"), N_("fail"), N_("stale"),
  N_("unrun"), N_("missing"), N_("unavailable"), N_("error"), N_("refused"), N_("pending"), N_("approved"), N_("rejected"), N_("open"),
  N_("confirmed"), N_("accepted"), N_("proposed"), N_("fixed"), N_("closed"), N_("critical"), N_("major"), N_("minor"), N_("trivial")];
const st = (s, label) => `<span class="st ${STATUS[s] || "neutral"}">${esc(label ?? t(String(s ?? "")))}</span>`;
const health = h => `<span class="st ${HEALTH[h]?.[0] || "neutral"}">${esc(HEALTH[h] ? t(HEALTH[h][1]) : h)}</span>`;

// ---------------------------------------------------------------- components
// One Select for the whole console: a button that opens a listbox. Specs are kept by id so
// a re-render can redraw the trigger while the open menu keeps working.
const UI = {
  specs: {},
  select(id, value, options, onChange, opts = {}) {
    UI.specs[id] = { value, options, onChange, opts };
    const cur = options.find(o => o.value === value) || options[0];
    // the accessible name carries the value too ("Repo: All repositories"), as the visible text does
    const name = (opts.label || opts.prefix || id) + (cur ? ": " + cur.label : "");
    return `<div class="select${opts.cls ? " " + esc(opts.cls) : ""}" data-select="${esc(id)}"><button type="button" aria-haspopup="listbox" aria-expanded="false" aria-label="${esc(name)}">
      ${opts.prefix ? `<span class="pre">${esc(opts.prefix)}</span>` : ""}<span class="lbl">${esc(cur ? cur.label : "")}</span>
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 6l4 4 4-4"/></svg></button></div>`;
  },
};

// ---------------------------------------------------------------- page swap
// The app re-renders whole pages. UI.swap replaces a container's HTML while keeping what the
// person was doing: the focused control (and caret), and the scroll of every [data-scroll] box.
// Focus is matched by [data-focus], then by the owning select, by id, by a link's href, by a kept
// <details>' summary — the same one of several alike. When the control is gone (an action moved its
// item away), focus goes to the item now in its place in the same [data-list], else to that list's
// [data-head], else to the page's [data-home] heading — never to <body>.
UI.rerender = null;   // set by the app: the function that redraws the current page
UI.noScroll = {};     // data-scroll keys whose position should reset on the next swap
UI.held = null;       // a control that lost focus by being disabled while its action runs
UI.focusKey = el => {
  if (!el || el === document.body || !el.dataset) return null;
  if (el.dataset.focus) return `[data-focus="${CSS.escape(el.dataset.focus)}"]`;
  const sel = el.parentElement && el.parentElement.dataset && el.parentElement.dataset.select;
  if (sel) return `[data-select="${CSS.escape(sel)}"] > button`;
  if (el.id) return "#" + CSS.escape(el.id);
  if (el.tagName === "A" && el.getAttribute("href")) return `a[href="${CSS.escape(el.getAttribute("href"))}"]`;
  if (el.tagName === "SUMMARY" && el.parentElement.dataset.keep) return `details[data-keep="${CSS.escape(el.parentElement.dataset.keep)}"] > summary`;
  return null;
};
const usable = n => !!n && n.isConnected && !n.disabled && !n.inert && n.getClientRects().length > 0;
const focusOn = n => { if (!n.matches(FOCUSABLE) && !n.hasAttribute("tabindex")) n.tabIndex = -1; n.focus({ preventScroll: true }); };
// where focus is inside `box`, in terms that survive a redraw
UI.where = (box, a) => {
  const key = UI.focusKey(a), item = a.closest("[data-item]"), list = item && item.closest("[data-list]");
  return { key, nth: key ? [...box.querySelectorAll(key)].indexOf(a) : -1,
    list: list ? list.dataset.list : null, listAt: list ? [...box.querySelectorAll("[data-list]")].indexOf(list) : -1,
    at: list ? [...list.querySelectorAll("[data-item]")].indexOf(item) : -1 };
};
UI.restore = (box, w) => {
  if (w.key) {
    const all = box.querySelectorAll(w.key), n = all[Math.min(Math.max(w.nth, 0), all.length - 1)];
    if (n && n.disabled && n.getClientRects().length) { UI.held = n; return null; }  // its action runs: it comes back when it is done
    if (usable(n)) return focusOn(n), n;
  }
  if (w.list) {
    const lists = [...box.querySelectorAll("[data-list]")];
    const same = box.querySelector(`[data-list="${CSS.escape(w.list)}"]`), L = same || lists[Math.min(w.listAt, lists.length - 1)];
    if (L) {
      const items = [...L.querySelectorAll("[data-item]")], it = same ? items[Math.min(w.at, items.length - 1)] : items[0];
      const c = it && ([...it.querySelectorAll("button, [data-act]")].find(usable) || [...it.querySelectorAll(FOCUSABLE)].find(usable));
      if (c) return focusOn(c), c;
      if (it && same) return focusOn(it), it;   // the row stays where it was, with nothing left to press
      const h = L.querySelector("[data-head]"); if (h && h.getClientRects().length) return focusOn(h), h;
    }
  }
  const lead = [...box.querySelectorAll(".callout [data-act]")].find(usable);   // what the page asks for next
  if (lead) return focusOn(lead), lead;
  const home = document.querySelector("[data-home]"); if (home) focusOn(home);
  return home;
};
UI.swap = (box, html) => {
  let a = document.activeElement;
  if ((!a || a === document.body) && UI.held && box.contains(UI.held)) a = UI.held;  // disabling it blurred it
  UI.held = null;
  const w = a && a !== document.body && box.contains(a) ? UI.where(box, a) : null;
  let caret = null; try { if (w && a.selectionStart != null) caret = [a.selectionStart, a.selectionEnd]; } catch (e) {}
  const scroll = {};
  box.querySelectorAll("[data-scroll]").forEach(el => { scroll[el.dataset.scroll] = [el.scrollTop, el.scrollLeft]; });
  const y = box.scrollTop;
  box.innerHTML = html;
  box.scrollTop = y;
  box.querySelectorAll("[data-scroll]").forEach(el => {
    const s = scroll[el.dataset.scroll]; if (!s) return;
    el.scrollLeft = s[1]; if (!UI.noScroll[el.dataset.scroll]) el.scrollTop = s[0];
  });
  UI.noScroll = {};
  if (w) { const n = UI.restore(box, w); if (caret && n) try { n.setSelectionRange(caret[0], caret[1]); } catch (e) {} }
};
UI.refresh = () => { if (UI.rerender) UI.rerender(); };

// ---------------------------------------------------------------- table
// UI.table(id, spec) → HTML. A toolbar (search, filters, count), a scroll box with a sticky
// header and a sticky footer (range + pager), sortable columns. State lives in store per id.
//   spec = { columns: [{ key, label, num?, cls? (td), thCls?, sortValue?(row), render(row), width? }], rows,
//            pageSize = 25, search?(row) → string, filters?: [{ key, label, options: [{value,label}],
//            match(row, value), all? = true, default? }], empty, rowClass?(row), tools? (extra toolbar HTML) }
UI.tables = {};
const TBL_SIZES = [25, 50, 100];
const TBL_SAVED = {};  // id → state as stored; read from localStorage once, then written through
UI.tableState = (id, spec) => {
  const s = TBL_SAVED[id] || (TBL_SAVED[id] = store.get("tbl:" + id, {}) || {});
  return { q: s.q || "", f: s.f || {}, s: s.s || null, d: s.d || 0, p: s.p || 1, n: s.n || (spec && spec.pageSize) || 25 };
};
UI.tableSet = (id, patch, resetPage) => {
  const s = { ...UI.tableState(id, UI.tables[id]), ...patch, t: Date.now() };  // t: when it was last used (pruning)
  if (resetPage) s.p = 1;
  TBL_SAVED[id] = s; store.set("tbl:" + id, s);
  UI.noScroll["tbl:" + id] = true;
};
const tblCmp = (a, b) => {
  const ea = a == null || a === "", eb = b == null || b === "";
  if (ea || eb) return ea && eb ? 0 : ea ? 1 : -1;          // blanks last, whatever the direction
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
};
UI.table = (id, spec) => {
  UI.tables[id] = spec;
  const S = UI.tableState(id, spec), cols = spec.columns, filters = spec.filters || [];
  // a stored filter value that no longer exists (another repo, a removed option) is ignored
  const fval = f => {
    const v = S.f[f.key], ok = v != null && (v === "" ? f.all !== false : f.options.some(o => o.value === v));
    return ok ? v : f.all === false ? (f.default ?? f.options[0]?.value) : "";
  };
  let rows = spec.rows.slice();
  filters.forEach(f => { const v = fval(f); if (v !== "" && v != null) rows = rows.filter(r => f.match(r, v)); });
  const q = S.q.trim().toLowerCase();
  if (q && spec.search) rows = rows.filter(r => String(spec.search(r) || "").toLowerCase().includes(q));
  const sc = S.d && cols.find(c => c.key === S.s && c.sortValue);
  if (sc) rows = rows.map((r, i) => [r, sc.sortValue(r), i]).sort((a, b) => {
    const c = tblCmp(a[1], b[1]);
    return (a[1] == null || a[1] === "" || b[1] == null || b[1] === "" ? c : c * S.d) || a[2] - b[2];
  }).map(x => x[0]);
  const N = rows.length, size = TBL_SIZES.includes(S.n) ? S.n : 25, pages = Math.max(1, Math.ceil(N / size));
  const page = Math.min(Math.max(1, S.p), pages), from = (page - 1) * size;
  const shown = rows.slice(from, from + size);
  const total = spec.rows.length, narrowed = N !== total;
  const fid = k => `tbl:${id}:${k}`;

  const bar = `<div class="tbl-bar">
    ${spec.search ? `<input class="input tbl-q" type="search" data-tbl-q="${esc(id)}" data-focus="${esc(fid("q"))}" data-head value="${esc(S.q)}"
      placeholder="${esc(spec.placeholder || t("Search…"))}" aria-label="${esc(spec.placeholder || t("Search…"))}" autocomplete="off">` : ""}
    ${filters.map(f => UI.select(fid("f:" + f.key), fval(f),
      [...(f.all === false ? [] : [{ value: "", label: f.allLabel || t("All") }]), ...f.options],
      v => { UI.tableSet(id, { f: { ...UI.tableState(id, spec).f, [f.key]: v } }, true); UI.refresh(); }, { prefix: f.label, cls: "tbl-filter" })).join("")}
    <span class="tbl-count">${narrowed ? esc(t("{n} of {total}", { n: N, total })) : esc(t("{n} rows", { n: total }))}</span>
    ${spec.tools ? `<span class="spacer"></span>${spec.tools}` : ""}</div>`;

  const head = cols.map(c => {
    const cls = [c.num ? "num" : "", c.thCls || ""].join(" ").trim();   // c.cls styles the cells only
    if (!c.sortValue) return `<th${cls ? ` class="${cls}"` : ""} scope="col">${c.label ? esc(c.label) : `<span class="sr">${esc(t("Actions"))}</span>`}</th>`;
    const dir = S.s === c.key ? S.d : 0;
    return `<th class="${cls}" scope="col" aria-sort="${dir > 0 ? "ascending" : dir < 0 ? "descending" : "none"}">
      <button type="button" class="tbl-sort ${dir ? "on" : ""}" data-tbl-sort="${esc(id)}" data-key="${esc(c.key)}" data-focus="${esc(fid("s:" + c.key))}"
        title="${esc(t("Sort by {col}", { col: c.label }))}">${esc(c.label)}<span class="arr" aria-hidden="true">${dir > 0 ? "▲" : dir < 0 ? "▼" : "↕"}</span></button></th>`;
  }).join("");
  const body = shown.map(r => `<tr data-item${spec.rowClass && spec.rowClass(r) ? ` class="${esc(spec.rowClass(r))}"` : ""}>${cols.map(c => {
      const cls = [c.num ? "num" : "", c.cls || ""].join(" ").trim();
      return `<td${cls ? ` class="${cls}"` : ""}>${c.render(r)}</td>`; }).join("")}</tr>`).join("")
    || `<tr><td colspan="${cols.length}" class="empty">${total ? esc(t("Nothing matches these filters.")) : spec.empty || esc(t("Nothing here."))}</td></tr>`;
  const pb = (k, label, sym, off, go) => `<button type="button" class="btn sm icon-pg" data-tbl-page="${esc(id)}" data-go="${go}" data-focus="${esc(fid("p:" + k))}"
      aria-label="${esc(label)}" title="${esc(label)}" ${off ? "disabled" : ""}>${sym}</button>`;
  const foot = `<tfoot><tr><td colspan="${cols.length}"><div class="tbl-foot">
      <span class="tbl-range">${N ? esc(t("{a}–{b} of {n}", { a: from + 1, b: from + shown.length, n: N })) : esc(t("0 of {n}", { n: N }))}</span>
      <span class="spacer"></span>
      ${UI.select(fid("n"), String(size), TBL_SIZES.map(n => ({ value: String(n), label: String(n) })),
        v => { UI.tableSet(id, { n: +v }, true); UI.refresh(); }, { prefix: t("Rows"), cls: "sm up" })}
      <span class="tbl-pager">${pb("first", t("First page"), "«", page <= 1, 1)}${pb("prev", t("Previous page"), "‹", page <= 1, page - 1)}
        <span class="tbl-page">${esc(t("Page {p} of {n}", { p: page, n: pages }))}</span>
        ${pb("next", t("Next page"), "›", page >= pages, page + 1)}${pb("last", t("Last page"), "»", page >= pages, pages)}</span>
    </div></td></tr></tfoot>`;
  const colgroup = cols.some(c => c.width) ? `<colgroup>${cols.map(c => c.width ? `<col style="width:${esc(c.width)}">` : "<col>").join("")}</colgroup>` : "";
  return `<div class="tbl" data-list="${esc("tbl:" + id)}">${bar}<div class="panel tbl-scroll" data-scroll="${esc("tbl:" + id)}">
    <table>${colgroup}<thead><tr>${head}</tr></thead><tbody>${body}</tbody>${N ? foot : ""}</table></div></div>`;
};
let tblTimer = null;
document.addEventListener("input", e => {
  const q = e.target.closest?.("[data-tbl-q]"); if (!q) return;
  clearTimeout(tblTimer);
  tblTimer = setTimeout(() => { UI.tableSet(q.dataset.tblQ, { q: q.value }, true); UI.refresh(); }, 140);
});
document.addEventListener("click", e => {
  const s = e.target.closest("[data-tbl-sort]");
  if (s) {
    const id = s.dataset.tblSort, k = s.dataset.key, S = UI.tableState(id, UI.tables[id]);
    const d = S.s !== k || !S.d ? 1 : S.d > 0 ? -1 : 0;            // asc → desc → none
    UI.tableSet(id, { s: d ? k : null, d }, true); UI.refresh(); return;
  }
  const p = e.target.closest("[data-tbl-page]");
  if (p && !p.disabled) {
    UI.tableSet(p.dataset.tblPage, { p: Math.max(1, +p.dataset.go || 1) }); UI.refresh();
  }
});
// The open select's listbox (one for the page). Focus stays on the list, or on its filter box,
// and aria-activedescendant points at the active option: arrow keys move it without moving focus.
const menu = document.createElement("div");
menu.className = "menu";
document.body.appendChild(menu);
let menuState = null;
function openMenu(id, trigger) {
  const spec = UI.specs[id]; if (!spec) return;
  const searchable = spec.options.length > 7, name = spec.opts.label || spec.opts.prefix || id;
  menuState = { id, trigger, active: Math.max(0, spec.options.findIndex(o => o.value === spec.value)), q: "", list: [], typed: "", typedAt: 0 };
  menu.innerHTML = `${searchable ? `<input class="input sm msearch" role="combobox" aria-expanded="true" aria-controls="egd-menu-list" aria-autocomplete="list"
      placeholder="${esc(t("Filter…"))}" aria-label="${esc(t("Filter options"))}" autocomplete="off">` : ""}<div class="opts" id="egd-menu-list" role="listbox" tabindex="-1" aria-label="${esc(name)}"></div>`;
  const r = trigger.getBoundingClientRect();
  menu.style.minWidth = Math.max(r.width, 220) + "px";
  menu.classList.add("on");
  const w = menu.offsetWidth;
  menu.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + "px";
  trigger.setAttribute("aria-expanded", "true"); trigger.setAttribute("aria-controls", "egd-menu-list");
  drawMenu();
  const h = menu.offsetHeight;  // open upwards when there is no room below (e.g. a pager in a sticky footer)
  menu.style.top = (r.bottom + 4 + h > innerHeight - 8 && r.top - 4 - h > 8 ? r.top - 4 - h : r.bottom + 4) + "px";
  const s = menu.querySelector(".msearch");
  if (s) s.oninput = () => { menuState.q = s.value.toLowerCase(); menuState.active = 0; drawMenu(); };
  (s || menu.querySelector(".opts")).focus();
}
// rebuilt only when the filter changes; moving the active option just updates attributes
function drawMenu() {
  const spec = UI.specs[menuState.id], q = menuState.q;
  const list = menuState.list = spec.options.filter(o => !q || (o.label + " " + (o.hint || "")).toLowerCase().includes(q));
  menu.querySelector(".opts").innerHTML = list.map((o, i) => `${o.sep ? `<div class="sep" role="presentation"></div>` : ""}<div class="opt" role="option" id="egd-opt-${i}"
      aria-selected="${o.value === spec.value}" data-i="${i}"><span class="ck" aria-hidden="true">${o.value === spec.value ? "✓" : ""}</span><span class="ol">${esc(o.label)}</span><span class="oh">${esc(o.hint || "")}</span></div>`).join("")
    || `<div class="none">${esc(t("No matches"))}</div>`;
  setActive(menuState.active);
}
function setActive(i) {
  const opts = menu.querySelectorAll(".opt"), holders = menu.querySelectorAll(".opts, .msearch");
  menu.querySelector(".opt.active")?.classList.remove("active");
  if (!opts.length) { holders.forEach(n => n.removeAttribute("aria-activedescendant")); return; }
  const o = opts[menuState.active = Math.max(0, Math.min(i, opts.length - 1))];
  o.classList.add("active");
  holders.forEach(n => n.setAttribute("aria-activedescendant", o.id));
  o.scrollIntoView({ block: "nearest" });
}
function closeMenu(focusTrigger = true) {
  if (!menuState) return;
  menu.classList.remove("on");
  const t = document.querySelector(`[data-select="${CSS.escape(menuState.id)}"] > button`);
  if (t) { t.setAttribute("aria-expanded", "false"); t.removeAttribute("aria-controls"); if (focusTrigger) t.focus(); }
  menuState = null;
  idle();
}
// The app's redraws wait while a dialog or a menu is open (UI.busy); UI.onIdle runs once both are closed.
UI.onIdle = null;
UI.busy = () => dlgOpen() || !!menuState;
function idle() { if (UI.onIdle && !UI.busy()) UI.onIdle(); }
function pickMenu(i) {
  const o = menuState.list[i]; if (!o) return;
  const id = menuState.id, spec = UI.specs[id]; closeMenu();
  if (o.value === spec.value) return;
  // the change usually redraws the select: put focus back on the new trigger — now, and again when
  // an onChange that returns a promise (a language that loads first) has drawn the page
  const refocus = () => {
    const a = document.activeElement;
    if (!a || a === document.body) document.querySelector(`[data-select="${CSS.escape(id)}"] > button`)?.focus();
  };
  const done = spec.onChange(o.value);
  refocus();
  if (done && typeof done.then === "function") done.then(refocus, refocus);
}
menu.addEventListener("mousemove", e => { const o = e.target.closest(".opt"); if (o && menuState && +o.dataset.i !== menuState.active) setActive(+o.dataset.i); });
menu.addEventListener("click", e => { const o = e.target.closest(".opt"); if (o) pickMenu(+o.dataset.i); });
document.addEventListener("click", e => {
  const t = e.target.closest("[data-select] > button");
  if (t) { const id = t.parentElement.dataset.select; if (menuState && menuState.id === id) closeMenu(); else { closeMenu(false); openMenu(id, t); } return; }
  if (menuState && !menu.contains(e.target)) closeMenu(false);
}, true);
document.addEventListener("keydown", e => {
  if (e.isComposing || e.keyCode === 229) return;          // an IME is composing: its keys are not ours
  const t = e.target.closest?.("[data-select] > button");
  if (t && !menuState && ["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); openMenu(t.parentElement.dataset.select, t); return; }
  if (!menuState) return;
  const n = menuState.list.length, inFilter = e.target.classList?.contains("msearch");
  const move = i => { setActive(i); e.preventDefault(); };
  if (e.key === "ArrowDown") move(menuState.active + 1);
  else if (e.key === "ArrowUp") move(menuState.active - 1);
  else if ((e.key === "Home" || e.key === "End") && !inFilter) move(e.key === "Home" ? 0 : n - 1);
  else if (e.key === "Enter" || (e.key === " " && !inFilter)) { pickMenu(menuState.active); e.preventDefault(); }
  else if (e.key === "Escape") { closeMenu(); e.preventDefault(); e.stopImmediatePropagation(); }
  else if (e.key === "Tab") closeMenu();                    // back on the trigger; the Tab then moves on from it
  else if (!inFilter && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {  // type to jump
    const now = Date.now(); menuState.typed = (now - menuState.typedAt < 700 ? menuState.typed : "") + e.key.toLowerCase(); menuState.typedAt = now;
    const i = menuState.list.findIndex(o => o.label.toLowerCase().startsWith(menuState.typed));
    if (i >= 0) move(i);
  }
}, true);
window.addEventListener("resize", () => closeMenu(false));
document.addEventListener("scroll", e => { if (menuState && !menu.contains(e.target)) closeMenu(false); }, true);


// ---------------------------------------------------------------- toasts
// #toasts holds two live regions: news goes to the polite one, a failure to the alert one — each is
// announced once. Hovering keeps a toast, a click dismisses it.
function toast(msg, bad = false, problems = []) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.innerHTML = `<div>${esc(msg)}</div>${problems && problems.length ? `<ul>${problems.slice(0, 8).map(p => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}`;
  $(bad ? "#toasts-alert" : "#toasts-status").appendChild(el);
  let timer;
  const arm = () => { timer = setTimeout(() => el.remove(), bad ? 9000 : 3500); };
  el.onmouseenter = () => clearTimeout(timer); el.onmouseleave = arm; el.onclick = () => el.remove();
  arm();
}


// ---------------------------------------------------------------- dialogs
// One modal at a time. While it is open the rest of the page is inert, Tab wraps inside it,
// Esc closes it (not while an IME is composing) and focus returns to what opened it.
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';
let dlgReturn = null, dlgWhere = null, dlgInert = [];
const dlgOpen = () => $("#dialog").classList.contains("on");
// named by its first heading; else by its search box; else generically
function dlgLabel() {
  const d = $("#dialog"), h = d.querySelector("h1, h2, h3, h4, h5, h6");
  d.removeAttribute("aria-labelledby"); d.removeAttribute("aria-label");
  if (h) { h.id ||= "dialog-title"; d.setAttribute("aria-labelledby", h.id); return; }
  const n = d.querySelector("input[aria-label], input[placeholder]");
  d.setAttribute("aria-label", n ? n.getAttribute("aria-label") || n.placeholder : t("Dialog"));
}
// Initial focus is never a control that commits: `focus` (a selector) when given, else the first
// text field, else the first button that is neither primary nor danger (Cancel), else the dialog.
const TEXTY = 'select, textarea, input:not([type]), input[type="text"], input[type="search"], input[type="number"], input[type="email"], input[type="url"], input[type="password"], input[type="tel"]';
function openDialog(html, wide = false, focus = null) {
  const d = $("#dialog");
  if (!dlgOpen()) {  // a dialog replaced while open keeps the first opener and the inert page
    dlgReturn = document.activeElement;
    dlgWhere = dlgReturn && dlgReturn !== document.body ? UI.where(document.body, dlgReturn) : null;
    dlgInert = [...document.body.children].filter(el => !el.inert && el !== menu && !["dialog", "scrim", "toasts"].includes(el.id) && !/^(SCRIPT|STYLE|TEMPLATE)$/.test(el.tagName));
    dlgInert.forEach(el => { el.inert = true; });
  }
  d.className = "dialog on" + (wide ? " wide" : "");
  d.innerHTML = html; $("#scrim").classList.add("on");
  dlgLabel();
  const first = (focus && d.querySelector(focus)) || [...d.querySelectorAll(TEXTY)].find(usable)
    || [...d.querySelectorAll("button")].find(b => usable(b) && !b.classList.contains("primary") && !b.classList.contains("danger"));
  (usable(first) ? first : d).focus();
}
function closeDialog() {
  if (!dlgOpen()) return;
  const d = $("#dialog");
  if (menuState && d.contains(menuState.trigger)) closeMenu(false);
  d.className = "dialog"; d.innerHTML = ""; d.removeAttribute("aria-labelledby"); d.removeAttribute("aria-label");
  $("#scrim").classList.remove("on");
  dlgInert.forEach(el => { el.inert = false; }); dlgInert = [];
  const back = dlgReturn, where = dlgWhere; dlgReturn = dlgWhere = null;
  if (back && back.isConnected && back !== document.body) back.focus({ preventScroll: true });
  else if (where) UI.restore(document.body, where);  // the page was redrawn under the dialog: its twin, or the next item
  idle();
}
document.addEventListener("keydown", e => {   // after the select's handler: an open listbox takes Esc first
  if (!dlgOpen()) return;
  const d = $("#dialog");
  if (e.key === "Escape") {
    e.stopImmediatePropagation();
    if (!e.isComposing && e.keyCode !== 229) { e.preventDefault(); closeDialog(); }
  } else if (e.key === "Tab") {
    const f = [...d.querySelectorAll(FOCUSABLE)].filter(n => n.getClientRects().length), a = document.activeElement;
    if (!f.length) { e.preventDefault(); d.focus(); return; }
    if (e.shiftKey ? a === f[0] || a === d || !d.contains(a) : a === f[f.length - 1] || !d.contains(a)) {
      e.preventDefault(); (e.shiftKey ? f[f.length - 1] : f[0]).focus();
    }
  }
}, true);


// Inline markdown for one line of text: `code` (taken literally), **bold**, *italic*, ~~struck~~ and
// [links](https://…). Code spans and then links are lifted out first, so emphasis can wrap around
// them but never cross into a link; a link's text gets its own emphasis. Emphasis that would still
// nest wrongly (`*a **b* c**`) is dropped, so the result never leaves a tag open.
const MD_EM = s => {
  const out = s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[^\w*])\*(?![\s*])([^*]+?)\*(?![\w*])/g, "$1<i>$2</i>")
    .replace(/~~(.+?)~~/g, "<s>$1</s>");
  const open = [];
  for (const [, close, tag] of out.matchAll(/<(\/?)([bis])>/g)) {
    if (!close) open.push(tag); else if (open.pop() !== tag) return s;
  }
  return open.length ? s : out;
};
const inlineMd = s => {
  const codes = [], links = [];
  // U+0000 and U+0001 mark the lifted spans below: the text's own are dropped first
  const x = String(s ?? "").replace(/[\u0000\u0001]/g, "").replace(/`([^`\n]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`)
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, text, url) => `\u0001${links.push([text, url]) - 1}\u0001`);
  return MD_EM(esc(x))
    .replace(/\u0001(\d+)\u0001/g, (_, n) => `<a href="${esc(links[n][1])}" target="_blank" rel="noopener noreferrer">${MD_EM(esc(links[n][0]))}</a>`)
    .replace(/\u0000(\d+)\u0000/g, (_, n) => `<code>${esc(codes[n])}</code>`);
};

// block-level patterns, compiled once
const MD = {
  comment: /<!--[\s\S]*?-->/g, starts: /^(```|#{1,4}\s|\||[-*]\s|\d+[.)]\s|>\s?|---\s*$|<\/?details|!\[)/,
  details: /^<details><summary>(.*)<\/summary>$/, rule: /^---\s*$/, kv: /^([\w.-]+):\s*(.*)$/, head: /^(#{1,4})\s+(.*)$/,
  img: /^!\[([^\]]*)\]\(([\w.@-]+)\)$/, tsep: /^\|[\s|:-]+\|$/, tedge: /^\||\|$/g, item: /^\s*([-*]|\d+[.)])\s+/,
  itemText: /^\s*([-*]|\d+[.)])\s+(.*)$/, digit: /\d/, task: /^\[([ xX])\]\s+(.*)$/, isTask: /^\[[ xX]\]\s/, quote: /^>\s?/, indent: /^\s/,
};
function renderMarkdown(text, base) {
  const inline = inlineMd;
  const out = [], lines = String(text || "").replace(MD.comment, "").split("\n"); let i = 0, m;
  const block = ln => MD.starts.test(ln) || !ln.trim();
  const tick = on => `<span class="tick${on ? " on" : ""}" role="img" aria-label="${esc(t(on ? "Done" : "Not done"))}"></span>`;
  while (i < lines.length) {
    const ln = lines[i];
    if (ln.startsWith("```")) { const buf = []; i++; while (i < lines.length && !lines[i].startsWith("```")) buf.push(lines[i++]); out.push(`<pre>${esc(buf.join("\n"))}</pre>`); i++; continue; }
    if ((m = ln.match(MD.details))) { out.push(`<details><summary>${esc(m[1])}</summary>`); i++; continue; }
    if (ln === "</details>") { out.push("</details>"); i++; continue; }
    if (MD.rule.test(ln)) {  // a front-matter block (key: value lines between rules) reads as a small table
      let j = i + 1; const kvs = [];
      while (j < lines.length && (!lines[j].trim() || (m = lines[j].match(MD.kv)))) { if (lines[j].trim()) kvs.push([m[1], m[2]]); j++; }
      if (kvs.length && j < lines.length && MD.rule.test(lines[j])) {
        out.push(`<dl class="kv fm">${kvs.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${inline(v)}</dd>`).join("")}</dl>`); i = j + 1; continue; }
      out.push("<hr>"); i++; continue; }
    if ((m = ln.match(MD.head))) { out.push(`<h${m[1].length + 2}>${inline(m[2])}</h${m[1].length + 2}>`); i++; continue; }
    if ((m = ln.match(MD.img))) { out.push(`<img src="${base}/${encodeURIComponent(m[2])}" alt="${esc(m[1])}">`); i++; continue; }
    if (ln.startsWith("|")) { const rows = []; while (i < lines.length && lines[i].startsWith("|")) { if (!MD.tsep.test(lines[i])) rows.push(lines[i]); i++; }
      const cells = r => r.replace(MD.tedge, "").split("|");
      out.push(`<table>${rows.map((r, n) => `<tr>${cells(r).map(c => n ? `<td>${inline(c.trim())}</td>` : `<th>${inline(c.trim())}</th>`).join("")}</tr>`).join("")}</table>`); continue; }
    if ((m = ln.match(MD.item))) {  // a list; indented lines continue the item above
      const ordered = MD.digit.test(m[1]), items = [];
      while (i < lines.length && lines[i].trim()) {
        const x = lines[i].match(MD.itemText);
        if (x && MD.digit.test(x[1]) !== ordered) break;
        if (x) items.push(x[2]); else if (items.length && MD.indent.test(lines[i])) items[items.length - 1] += " " + lines[i].trim(); else break;
        i++; }
      const li = x => { const tk = x.match(MD.task);  // - [ ] / - [x] task items
        return tk ? `<li class="task">${tick(tk[1] !== " ")}<span>${inline(tk[2])}</span></li>` : `<li>${inline(x)}</li>`; };
      out.push(`<${ordered ? "ol" : "ul"}${items.some(x => MD.isTask.test(x)) ? ` class="tasks"` : ""}>${items.map(li).join("")}</${ordered ? "ol" : "ul"}>`); continue; }
    if (ln.startsWith(">")) { const buf = []; while (i < lines.length && lines[i].startsWith(">")) buf.push(lines[i++].replace(MD.quote, ""));
      out.push(`<blockquote>${inline(buf.join(" "))}</blockquote>`); continue; }
    if (ln.trim()) {  // a paragraph runs until a blank line or another block
      const buf = [ln.trim()]; i++;
      while (i < lines.length && !block(lines[i])) buf.push(lines[i++].trim());
      out.push(`<p>${inline(buf.join(" "))}</p>`); continue; }
    i++;
  }
  return out.join("\n");
}

// ---------------------------------------------------------------- shell plumbing
(function ensureOverlays() {
  for (const [id, cls] of [["scrim", "scrim"], ["dialog", "dialog"], ["toasts", "toasts"]]) {
    if (!document.getElementById(id)) { const el = document.createElement("div"); el.id = id; el.className = cls; document.body.appendChild(el); }
  }
  const d = document.getElementById("dialog");
  d.setAttribute("role", "dialog"); d.setAttribute("aria-modal", "true"); d.tabIndex = -1;
  // a page may fill the open dialog itself (a transcript after "Loading…"): name it again, keep focus inside
  new MutationObserver(() => {
    if (!dlgOpen()) return;
    dlgLabel(); if (!d.contains(document.activeElement)) d.focus();
  }).observe(d, { childList: true });
  const ts = document.getElementById("toasts");
  ts.innerHTML = `<div id="toasts-alert" role="alert"></div><div id="toasts-status" role="status" aria-live="polite"></div>`;
  document.getElementById("scrim").onclick = () => closeDialog();
})();
// The theme button is a toggle for the dark theme (aria-pressed says whether it is on).
function initTheme(button) {
  const root = document.documentElement, saved = store.get("theme", null); if (saved) root.dataset.theme = saved;
  const dark = () => (root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
  const show = () => button && button.setAttribute("aria-pressed", String(dark()));
  show();
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", show);
  if (button) button.onclick = () => {
    root.dataset.theme = dark() ? "light" : "dark"; store.set("theme", root.dataset.theme); show();
  };
}
