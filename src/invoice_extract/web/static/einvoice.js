"use strict";

// E-invoice converter: invoices on the left, the page with boxes in the middle, the e-invoice form on the right.
// Plain DOM, no framework. All data goes in via textContent or input values.

const SVG_NS = "http://www.w3.org/2000/svg";
const ICONS = { error: "×", warning: "!", info: "i" };
const PARTY = [["name", "Name"], ["street", "Street"], ["postcode", "Postcode"], ["city", "City"],
  ["country", "Country (e.g. DE)"], ["vat_id", "VAT ID"], ["tax_number", "Tax number"],
  ["contact_name", "Contact person"], ["phone", "Phone"], ["email", "E-mail"]];
const GROUPS = [
  ["Invoice", [["number", "Invoice No."], ["type_code", "Type", "select", [["380", "Invoice"], ["381", "Credit note"]]],
    ["issue_date", "Invoice date", "date"], ["currency", "Currency"], ["buyer_reference", "Buyer reference / Leitweg-ID"],
    ["period_start", "Service period from", "date"], ["period_end", "Service period to", "date"],
    ["delivery_date", "Delivery date", "date"], ["notes", "Notes", "notes"]]],
  ["Seller", PARTY.map(([k, l]) => [`seller.${k}`, l])],
  ["Buyer", PARTY.filter(([k]) => !["tax_number", "contact_name", "phone"].includes(k)).map(([k, l]) => [`buyer.${k}`, l])],
  ["Payment", [["payment_means", "Payment means", "select", [["transfer", "Bank transfer"], ["debit", "Direct debit"]]],
    ["due_date", "Due date", "date"], ["payment_terms", "Payment terms"], ["iban", "Seller IBAN"], ["bic", "BIC"],
    ["mandate_reference", "Mandate reference"], ["creditor_id", "Creditor ID"], ["debited_iban", "Debited IBAN (buyer)"],
    ["exemption_reason", "VAT exemption reason"]]],
];
const LINE_COLS = [["description", "Description"], ["quantity", "Qty"], ["unit", "Unit"], ["unit_price", "Unit price"],
  ["vat_rate", "VAT %"], ["amount", "Amount"]];

const ui = { data: { files: [], profile: null }, selected: null, invoice: null, page: 1, pages: 0, shownFile: null,
  focus: null, timer: null, saveTimer: null, dirty: false };

const $ = (id) => document.getElementById(id);

function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) node.append(kid);
  return node;
}

function svg(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

async function api(method, url, body) {
  const options = { method };
  if (body instanceof FormData) options.body = body;
  else if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const res = await fetch(url, options);
  const data = (res.headers.get("content-type") || "").includes("json") ? await res.json() : null;
  if (!res.ok) throw new Error((data && data.detail) || `${method} ${url} failed (${res.status})`);
  return data;
}

let toastTimer = null;
function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 6000);
}

const guard = (fn) => (...args) => Promise.resolve(fn(...args)).catch((e) => toast(e.message));
const format = () => document.querySelector('input[name="format"]:checked').value;
const current = () => ui.data.files.find((f) => f.id === ui.selected) || null;

function getPath(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

function setPath(obj, path, value) {
  const keys = path.split(".");
  const last = keys.pop();
  const target = keys.reduce((o, k) => o[k], obj);
  target[last] = value;
}

function issuesOf(file) {
  return file && file.issues && file.issues[format()] ? file.issues[format()] : [];
}

function statusOf(file) {
  if (file.state !== "done") return "pending";
  if (file.error) return "error";
  const issues = issuesOf(file);
  if (issues.some((i) => i.level === "error")) return "error";
  return issues.length ? "warning" : "ok";
}

// ---- state -------------------------------------------------------------

async function refresh() {
  const data = await api("GET", "/api/e/state");
  ui.data = data;
  if (!data.files.some((f) => f.id === ui.selected)) ui.selected = data.files.length ? data.files[0].id : null;
  const file = current();
  if (!ui.dirty) ui.invoice = file && file.invoice ? JSON.parse(JSON.stringify(file.invoice)) : null;
  render();
  clearTimeout(ui.timer);
  ui.timer = data.files.some((f) => f.state !== "done") ? setTimeout(guard(refresh), 1000) : null;
}

function select(id) {
  if (id === ui.selected) return;
  guard(flushSave)();
  ui.selected = id;
  ui.focus = null;
  const file = current();
  ui.invoice = file && file.invoice ? JSON.parse(JSON.stringify(file.invoice)) : null;
  render();
}

// ---- rendering ---------------------------------------------------------

function render() {
  renderFiles();
  renderForm();
  renderStatus();
  renderViewer();
}

function renderFiles() {
  $("files-help").hidden = ui.data.files.length > 0;
  $("files").replaceChildren(...ui.data.files.map((f) => {
    const status = statusOf(f);
    const errorsN = issuesOf(f).filter((i) => i.level === "error").length;
    const note = f.state !== "done" ? "reading…" : f.error ? f.error : errorsN ? `${errorsN} to fix` : "ready";
    return el("li", { class: "e-file" + (f.id === ui.selected ? " selected" : ""), onclick: () => select(f.id) },
      el("span", { class: `dot ${status}` }), el("span", { class: "name" }, f.name), el("span", { class: "muted small-text" }, note));
  }));
}

function renderStatus() {
  const file = current();
  const list = $("problems");
  $("download-button").disabled = !file || statusOf(file) === "error" || statusOf(file) === "pending";
  if (!file) {
    $("status").textContent = "";
    list.replaceChildren();
    return;
  }
  if (file.state !== "done") {
    $("status").textContent = "Reading the invoice…";
    list.replaceChildren();
    return;
  }
  if (file.error) {
    $("status").textContent = file.error;
    list.replaceChildren();
    return;
  }
  const issues = issuesOf(file).concat(file.hints || []);
  const errorsN = issues.filter((i) => i.level === "error").length;
  const name = format() === "xrechnung" ? "XRechnung" : "ZUGFeRD";
  $("status").textContent = errorsN ? `${errorsN} field${errorsN === 1 ? "" : "s"} to fix before the ${name} can be written.`
    : `Ready: the ${name} is valid.`;
  $("status").className = "e-status " + (errorsN ? "error" : "ok");
  list.replaceChildren(...issues.map((i) => el("li", { class: "clickable", onclick: () => focusField(i.field) },
    el("span", { class: `icon ${i.level}` }, ICONS[i.level] || "•"),
    el("span", null, `${i.field}: ${i.message} `, el("span", { class: "muted small-text" }, i.rule)))));
  markIssues(issues);
}

function input(path, label, kind, options) {
  const value = getPath(ui.invoice, path);
  let control;
  if (kind === "select") {
    control = el("select", { "data-field": path }, options.map(([v, l]) => el("option", { value: v }, l)));
    control.value = value || options[0][0];
  } else if (kind === "notes") {
    control = el("textarea", { "data-field": path, rows: 2 });
    control.value = (value || []).join("\n");
  } else {
    control = el("input", { type: kind === "date" ? "date" : "text", "data-field": path });
    control.value = value == null ? "" : value;
  }
  control.addEventListener("input", () => onEdit(path, kind, control));
  control.addEventListener("change", () => onEdit(path, kind, control));
  control.addEventListener("focus", () => { ui.focus = path; drawOverlay(); });
  return el("label", { class: "e-field" }, el("span", null, label), control, el("small", { class: "e-msg" }));
}

function renderForm() {
  const form = $("form");
  const file = current();
  if (!file || !ui.invoice) {
    form.replaceChildren();
    return;
  }
  const active = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.field : null;
  const groups = GROUPS.map(([title, fields]) => el("fieldset", null, el("legend", null, title),
    fields.map(([path, label, kind, options]) => input(path, label, kind, options))));
  form.replaceChildren(...groups, linesTable(), totalsBlock(file));
  if (active) {
    const again = form.querySelector(`[data-field="${CSS.escape(active)}"]`);
    if (again) again.focus();
  }
}

function linesTable() {
  const rows = (ui.invoice.lines || []).map((line, i) => el("tr", null,
    LINE_COLS.map(([key]) => {
      const control = el("input", { type: "text", "data-field": `lines.${i}.${key}`, "aria-label": key });
      control.value = line[key] == null ? "" : line[key];
      control.addEventListener("input", () => onEdit(`lines.${i}.${key}`, "text", control));
      control.addEventListener("focus", () => { ui.focus = "lines"; drawOverlay(); });
      return el("td", null, control, el("small", { class: "e-msg" }));
    }),
    el("td", null, el("button", { type: "button", class: "link", "aria-label": "Remove line",
      onclick: () => { ui.invoice.lines.splice(i, 1); scheduleSave(); renderForm(); } }, "×"))));
  const add = el("button", { type: "button", class: "btn small", onclick: () => {
    ui.invoice.lines.push({ description: "", quantity: "1", unit: "C62", unit_price: "", vat_rate: "19", vat_category: "", amount: "" });
    scheduleSave();
    renderForm();
  } }, "+ Add line");
  return el("fieldset", { "data-field": "lines" }, el("legend", null, "Lines"),
    el("div", { class: "table-wrap" }, el("table", { class: "e-lines" },
      el("thead", null, el("tr", null, LINE_COLS.map(([, l]) => el("th", null, l)), el("th"))), el("tbody", null, rows))),
    add, el("small", { class: "e-msg", "data-msg": "lines" }));
}

function totalsBlock(file) {
  const t = file.totals || {};
  const row = (label, computed, path) => el("tr", null, el("td", null, label), el("td", null, computed || "–"),
    el("td", null, input(path, "printed", "text")));
  return el("fieldset", null, el("legend", null, "Totals (computed from the lines · printed on the invoice)"),
    el("table", { class: "e-totals" }, el("tbody", null,
      row("Net", t.tax_basis, "printed_net"), row("VAT", t.tax_total, "printed_vat"), row("Gross", t.grand_total, "printed_gross"),
      t.rounding && t.rounding !== "0.00" ? el("tr", null, el("td", null, "Rounding"), el("td", null, t.rounding), el("td")) : null,
      el("tr", null, el("td", null, "Amount due"), el("td", null, el("strong", null, t.due || "–")), el("td")))));
}

function markIssues(issues) {
  const form = $("form");
  form.querySelectorAll(".invalid, .warn").forEach((n) => n.classList.remove("invalid", "warn"));
  form.querySelectorAll(".e-msg").forEach((n) => { n.textContent = ""; n.classList.remove("soft"); });
  for (const issue of issues) {
    const control = form.querySelector(`[data-field="${CSS.escape(issue.field)}"]`);
    if (!control) continue;
    const holder = control.tagName === "FIELDSET" ? control.querySelector('[data-msg="lines"]') : control.parentElement.querySelector(".e-msg");
    if (control.tagName !== "FIELDSET") control.classList.add(issue.level === "error" ? "invalid" : "warn");
    if (holder && !holder.textContent) {
      holder.textContent = issue.message;
      holder.classList.toggle("soft", issue.level !== "error");
    }
  }
}

// ---- editing -----------------------------------------------------------

function onEdit(path, kind, control) {
  let value = control.value;
  if (kind === "notes") value = value.split("\n").map((s) => s.trim()).filter(Boolean);
  setPath(ui.invoice, path, value);
  scheduleSave();
}

function scheduleSave() {
  ui.dirty = true;
  clearTimeout(ui.saveTimer);
  ui.saveTimer = setTimeout(guard(save), 400);
}

async function flushSave() {
  // Save a pending edit now; a download waits for it and stops if the edit cannot be saved.
  if (ui.saveTimer || ui.dirty) {
    clearTimeout(ui.saveTimer);
    await save(true);
  }
}

async function save(strict) {
  ui.saveTimer = null;
  const file = current();
  if (!file || !ui.invoice) return;
  const invoice = ui.invoice;
  try {
    const entry = await api("PUT", `/api/e/files/${encodeURIComponent(file.id)}`, { invoice });
    Object.assign(file, entry);
    if (file.id !== ui.selected) {  // the user moved on to another invoice meanwhile
      renderFiles();
      return;
    }
    ui.dirty = false;
    renderFiles();
    renderStatus();
    const totals = $("form").querySelector(".e-totals");
    if (totals) totals.closest("fieldset").replaceWith(totalsBlock(file));
    markIssues(issuesOf(file).concat(file.hints || []));
  } catch (e) {
    toast(e.message);  // e.g. "issue_date: '31.02.' is not a date" — the value stays for the user to fix
    if (strict) throw e;
  }
}

// ---- page viewer -------------------------------------------------------

async function renderViewer() {
  const file = current();
  $("viewer-name").textContent = file ? file.name : "No invoice selected";
  if (!file) {
    ui.shownFile = null;
    $("page-wrap").hidden = true;
    updatePager();
    return;
  }
  if (ui.shownFile !== file.id) {
    ui.shownFile = file.id;
    ui.page = 1;
    const res = await api("GET", `/api/files/${encodeURIComponent(file.id)}/pages`).catch(() => ({ count: 0 }));
    if (ui.shownFile !== file.id) return;
    ui.pages = res.count;
    showPage();
  }
  drawOverlay();
}

function showPage() {
  const file = current();
  updatePager();
  if (!file || !ui.pages) {
    $("page-wrap").hidden = true;
    return;
  }
  $("page-wrap").hidden = false;
  $("page-image").src = `/api/files/${encodeURIComponent(file.id)}/pages/${ui.page}.png`;
}

function updatePager() {
  $("page-label").textContent = ui.pages ? `Page ${ui.page} of ${ui.pages}` : "";
  $("prev-page").disabled = ui.page <= 1;
  $("next-page").disabled = ui.page >= ui.pages;
}

function turnPage(delta) {
  const page = ui.page + delta;
  if (page < 1 || page > ui.pages) return;
  ui.page = page;
  showPage();
}

function drawOverlay() {
  const img = $("page-image");
  const overlay = $("overlay");
  const file = current();
  if (!file || !img.naturalWidth || $("page-wrap").hidden) {
    overlay.replaceChildren();
    return;
  }
  const w = img.naturalWidth;
  const h = img.naturalHeight;
  overlay.setAttribute("viewBox", `0 0 ${w} ${h}`);
  const nodes = [];
  let focused = null;
  const focusBox = ui.focus ? (file.boxes || {})[ui.focus] : null;
  if (focusBox && focusBox.page !== ui.page && focusBox.page <= ui.pages) {
    ui.page = focusBox.page;
    showPage();
    return;
  }
  for (const [path, box] of Object.entries(file.boxes || {})) {
    if (box.page !== ui.page) continue;
    const on = path === ui.focus;
    const rect = svg("rect", { class: "e-box" + (on ? " focus" : ""), x: box.x0 * w, y: box.y0 * h,
      width: (box.x1 - box.x0) * w, height: (box.y1 - box.y0) * h, rx: 3 });
    nodes.push(rect);
    if (on) focused = rect;
  }
  overlay.replaceChildren(...nodes);
  if (focused) focused.scrollIntoView({ block: "center", behavior: "smooth" });
}

function focusField(path) {
  const control = $("form").querySelector(`[data-field="${CSS.escape(path)}"]`);
  if (control) {
    control.scrollIntoView({ block: "center", behavior: "smooth" });
    control.focus({ preventScroll: true });
  }
  ui.focus = path.startsWith("lines") ? "lines" : path;
  drawOverlay();
}

// ---- actions -----------------------------------------------------------

async function uploadFiles(list) {
  if (!list || !list.length) return;
  const form = new FormData();
  for (const file of list) form.append("files", file, file.name);
  await api("POST", "/api/e/files", form);
  await refresh();
}

async function download(file) {
  const res = await fetch(`/api/e/files/${encodeURIComponent(file.id)}/download?format=${format()}`);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(`${file.name}: ${data.detail || "cannot be written"}`);
  }
  const name = (res.headers.get("content-disposition") || "").match(/filename="([^"]+)"/);
  const link = el("a", { href: URL.createObjectURL(await res.blob()), download: name ? name[1] : "einvoice" });
  document.body.append(link);
  link.click();
  link.remove();
}

async function downloadAll() {
  await flushSave();
  const ready = ui.data.files.filter((f) => statusOf(f) === "ok" || statusOf(f) === "warning");
  if (!ready.length) throw new Error("No invoice is ready yet.");
  for (const file of ready) await download(file);
}

async function clearAll() {
  if (!ui.data.files.length || !confirm("Remove all invoices from this session?")) return;
  await api("DELETE", "/api/e/files");
  ui.dirty = false;
  await refresh();
}

function openProfile() {
  const profile = ui.data.profile || { seller: {} };
  const fields = PARTY.map(([k, l]) => [`seller.${k}`, l]).concat([["iban", "IBAN"], ["bic", "BIC"], ["payment_terms", "Payment terms"]]);
  $("profile-form").replaceChildren(el("fieldset", null, fields.map(([path, label]) => {
    const control = el("input", { type: "text", "data-profile": path });
    control.value = getPath(profile, path) || "";
    return el("label", { class: "e-field" }, el("span", null, label), control);
  })));
  $("profile-dialog").showModal();
}

async function saveProfile() {
  const profile = { seller: {}, iban: "", bic: "", payment_terms: "" };
  $("profile-form").querySelectorAll("[data-profile]").forEach((n) => setPath(profile, n.dataset.profile, n.value.trim()));
  await api("PUT", "/api/e/profile", profile);
  $("profile-dialog").close();
  ui.dirty = false;
  await refresh();
}

async function loadProfile(event) {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  const form = new FormData();
  form.append("file", file, file.name);
  await api("POST", "/api/e/profile", form);
  await refresh();
  openProfile();
}

function setup() {
  $("file-input").addEventListener("change", guard(async (event) => {
    const files = Array.from(event.target.files);
    event.target.value = "";
    await uploadFiles(files);
  }));
  document.addEventListener("dragover", (event) => { event.preventDefault(); document.body.classList.add("dragging"); });
  document.addEventListener("dragleave", (event) => { if (!event.relatedTarget) document.body.classList.remove("dragging"); });
  document.addEventListener("drop", guard(async (event) => {
    event.preventDefault();
    document.body.classList.remove("dragging");
    await uploadFiles(Array.from(event.dataTransfer.files));
  }));
  document.querySelectorAll('input[name="format"]').forEach((r) => r.addEventListener("change", () => { renderFiles(); renderStatus(); }));
  $("download-button").addEventListener("click", guard(async () => { await flushSave(); await download(current()); }));
  $("download-all-button").addEventListener("click", guard(downloadAll));
  $("clear-button").addEventListener("click", guard(clearAll));
  $("profile-button").addEventListener("click", openProfile);
  $("profile-cancel").addEventListener("click", () => $("profile-dialog").close());
  $("profile-save").addEventListener("click", guard(saveProfile));
  $("profile-input").addEventListener("change", guard(loadProfile));
  $("prev-page").addEventListener("click", () => turnPage(-1));
  $("next-page").addEventListener("click", () => turnPage(1));
  $("page-image").addEventListener("load", drawOverlay);
  guard(refresh)();
}

setup();
