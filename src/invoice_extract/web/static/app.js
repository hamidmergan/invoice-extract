"use strict";

// Review UI for invoice-extract (layout C): fields for all invoices on the left, the current invoice on the right,
// the export preview below. Plain DOM, no framework. All data goes in via textContent.

const SVG_NS = "http://www.w3.org/2000/svg";
const PALETTE = ["#2f6fed", "#1f9d55", "#d08a00", "#8b5cf6", "#d64545", "#0e9aa7", "#c2410c", "#be185d",
  "#4d7c0f", "#6366f1", "#0369a1", "#a16207"];
const GROUPS = [["value", "Values"], ["text", "Text blocks"], ["table", "Tables"]];
const ICONS = { ok: "✓", warning: "!", error: "×" };

const ui = {
  data: { mode: "discover", fields: [], files: [], summary: { files: 0, fields: 0, to_check: 0 } },
  selected: null,
  page: 1,
  pages: 0,
  shownFile: null,
  focusKey: null,
  tab: "invoices",
  editing: false,
  timer: null,
};

const $ = (id) => document.getElementById(id);

function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "style") node.style.cssText = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const kid of kids.flat()) {
    if (kid != null && kid !== false) node.append(kid);
  }
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
  toastTimer = setTimeout(() => { box.hidden = true; }, 5000);
}

function guard(fn) {
  return (...args) => Promise.resolve(fn(...args)).catch((e) => toast(e.message));
}

// ---- state -------------------------------------------------------------

async function refresh() {
  ui.data = await api("GET", "/api/state");
  if (!ui.data.files.some((f) => f.id === ui.selected)) {
    ui.selected = ui.data.files.length ? ui.data.files[0].id : null;
  }
  const tables = selectedFields().filter((f) => f.kind === "table").map((f) => f.key);
  if (ui.tab !== "invoices" && !tables.includes(ui.tab)) ui.tab = "invoices";
  render();
  clearTimeout(ui.timer);
  const busy = ui.data.files.some((f) => f.state !== "done");
  ui.timer = busy ? setTimeout(guard(refresh), 1000) : null;
}

const currentFile = () => ui.data.files.find((f) => f.id === ui.selected) || null;
const selectedFields = () => ui.data.fields.filter((f) => f.selected);
const fieldOf = (key) => ui.data.fields.find((f) => f.key === key);
const cellOf = (file, key) => (file && file.row && file.row.cells ? file.row.cells[key] : undefined);
const display = (value) => (value == null ? "" : String(value));

function colorOf(key) {
  const index = ui.data.fields.findIndex((f) => f.key === key);
  return PALETTE[(index < 0 ? 0 : index) % PALETTE.length];
}

function statusText(file) {
  if (file.state === "merging") return "matching fields…";
  if (file.state !== "done") return "reading…";
  const row = file.row;
  if (row.error) return row.error;
  const issues = row.checks.filter((c) => c.level !== "ok");
  for (const cell of Object.values(row.cells)) issues.push(...cell.checks.filter((c) => c.level !== "ok"));
  return issues.length ? `Check: ${issues[0].message}` : "OK";
}

// ---- rendering ---------------------------------------------------------

function render() {
  renderTopbar();
  renderFields();
  renderChecks();
  renderSummary();
  renderTabs();
  if (!ui.editing) renderTable();
  renderViewer();
}

function renderTopbar() {
  const template = ui.data.mode === "template";
  $("mode-badge").hidden = !template;
  $("all-fields-button").hidden = !template;
}

function renderFields() {
  const total = ui.data.files.filter((f) => f.state === "done" && f.row && !f.row.error).length;
  $("fields-title").textContent = `Fields (${ui.data.summary.fields} of ${ui.data.fields.length})`;
  $("fields-help").hidden = ui.data.fields.length > 0;
  const groups = [];
  for (const [kind, title] of GROUPS) {
    const fields = ui.data.fields.filter((f) => f.kind === kind);
    if (!fields.length) continue;
    groups.push(el("div", { class: "group" },
      el("div", { class: "group-head" },
        el("strong", null, title),
        el("span", { class: "spacer" }),
        el("button", { class: "link", onclick: guard(() => selectGroup(kind, true)) }, "All"),
        el("button", { class: "link", onclick: guard(() => selectGroup(kind, false)) }, "None")),
      fields.map((f) => fieldItem(f, total))));
  }
  $("field-groups").replaceChildren(...groups);
}

function fieldItem(field, total) {
  const box = el("input", { type: "checkbox", "aria-label": `Export ${field.label}` });
  box.checked = field.selected;
  box.addEventListener("change", guard(() => toggleField(field.key, box.checked)));
  return el("div", { class: "field" + (field.selected ? "" : " off") + (field.key === ui.focusKey ? " focus" : "") },
    box,
    el("span", { class: "swatch", style: `background:${colorOf(field.key)}` }),
    el("button", { class: "name", title: field.hint || field.label, onclick: () => focusField(field.key) }, field.label),
    el("span", { class: "count" }, `${field.count}/${total}`));
}

function renderChecks() {
  const file = currentFile();
  const checks = [];
  if (file && file.row) {
    if (file.row.error) checks.push({ level: "error", message: file.row.error });
    else {
      checks.push(...file.row.checks);
      for (const [key, cell] of Object.entries(file.row.cells)) {
        const label = cell.label || (fieldOf(key) || { label: key }).label;
        for (const c of cell.checks) checks.push({ level: c.level, message: `${label}: ${c.message}` });
      }
    }
  }
  if (!checks.length) {
    $("checks").replaceChildren(el("li", { class: "muted" }, file ? statusText(file) : "No file selected"));
    return;
  }
  $("checks").replaceChildren(...checks.map((c) => el("li", null,
    el("span", { class: `icon ${c.level}` }, ICONS[c.level] || "•"), el("span", null, c.message))));
}

function renderSummary() {
  const s = ui.data.summary;
  const reading = ui.data.files.filter((f) => f.state !== "done").length;
  let text = `${s.files} files · ${s.fields} fields · ${s.to_check} to check`;
  if (reading) text += ` · ${reading} reading`;
  $("summary").textContent = text;
}

function renderTabs() {
  const tables = selectedFields().filter((f) => f.kind === "table");
  const tabs = [["invoices", "Invoices"], ...tables.map((f) => [f.key, f.label])];
  $("tabs").replaceChildren(...(tabs.length > 1 ? tabs : []).map(([key, label]) => el("button", {
    class: "tab" + (ui.tab === key ? " active" : ""), role: "tab",
    onclick: () => { ui.tab = key; renderTable(); renderTabs(); },
  }, label)));
}

function renderTable() {
  if (ui.tab !== "invoices") return renderLineTable(fieldOf(ui.tab));
  const cols = selectedFields().filter((f) => f.kind !== "table");
  const head = el("thead", null, el("tr", null, el("th", null, "File"),
    cols.map((c) => el("th", { title: c.hint || null }, c.label)), el("th", null, "Status")));
  const body = el("tbody", null, ui.data.files.map((f) => el("tr", {
    class: f.id === ui.selected ? "selected" : null,
    onclick: () => select(f.id),
  }, el("td", null, el("span", { class: `dot ${f.state === "done" ? f.status : f.state}` }), f.name),
  cols.map((c) => tableCell(f, c)),
  el("td", { class: f.state === "done" && f.status !== "ok" ? `status-${f.status}` : null }, statusText(f)))));
  $("table").replaceChildren(head, body);
}

function renderLineTable(field) {
  if (!field) return;
  const numbered = ui.data.files.some((f) => cellOf(f, "invoice_no") && cellOf(f, "invoice_no").value);
  const head = el("thead", null, el("tr", null, el("th", null, "File"), numbered ? el("th", null, "Invoice No.") : null,
    field.table_columns.map((name) => el("th", null, name))));
  const rows = [];
  for (const f of ui.data.files) {
    const cell = cellOf(f, field.key);
    if (!cell || !Array.isArray(cell.value)) continue;
    for (const line of cell.value) {
      rows.push(el("tr", { class: f.id === ui.selected ? "selected" : null, onclick: () => select(f.id) },
        el("td", null, f.name),
        numbered ? el("td", null, display((cellOf(f, "invoice_no") || {}).value)) : null,
        field.table_columns.map((name) => el("td", null, display(line[name])))));
    }
  }
  $("table").replaceChildren(head, el("tbody", null, rows));
}

function tableCell(file, field) {
  const cell = cellOf(file, field.key);
  const editable = file.state === "done" && file.row && !file.row.error;
  const classes = [editable ? "editable" : "", cell && cell.edited ? "edited" : ""].join(" ").trim();
  const text = cell ? display(cell.value) : "";
  const td = el("td", { class: classes || null, title: text.length > 40 ? text : null }, text);
  if (editable) {
    td.addEventListener("click", (event) => {
      event.stopPropagation();
      select(file.id);
      editCell(td, file, field, cell);
    });
  }
  return td;
}

function editCell(td, file, field, cell) {
  if (ui.editing) return;
  ui.editing = true;
  const input = el("input", { type: "text", "aria-label": field.label });
  input.value = cell ? display(cell.value) : "";
  let done = false;
  const finish = () => { done = true; ui.editing = false; renderTable(); };
  input.addEventListener("keydown", guard(async (event) => {
    if (event.key === "Escape") finish();
    if (event.key !== "Enter") return;
    done = true;
    try {
      await api("PATCH", `/api/files/${encodeURIComponent(file.id)}/cells/${encodeURIComponent(field.key)}`,
        { value: input.value });
    } finally {
      ui.editing = false;
      await refresh();
    }
  }));
  input.addEventListener("blur", () => { if (!done) finish(); });
  input.addEventListener("click", (event) => event.stopPropagation());
  td.replaceChildren(input);
  input.focus();
  input.select();
}

// ---- page viewer -------------------------------------------------------

function select(id) {
  if (id === ui.selected) return;
  ui.selected = id;
  render();
}

function flipFile(delta) {
  const files = ui.data.files;
  const index = files.findIndex((f) => f.id === ui.selected);
  if (index < 0) return;
  const next = files[index + delta];
  if (next) select(next.id);
}

async function renderViewer() {
  const file = currentFile();
  const files = ui.data.files;
  const index = files.findIndex((f) => f.id === ui.selected);
  $("viewer-name").textContent = file ? `${file.name} (${index + 1}/${files.length})` : "No file selected";
  $("prev-file").disabled = index <= 0;
  $("next-file").disabled = index < 0 || index >= files.length - 1;
  if (!file) {
    ui.shownFile = null;
    ui.pages = 0;
    $("page-wrap").hidden = true;
    $("viewer-help").textContent = "Add invoices to start: drop them anywhere or use “Add files”.";
    updatePager();
    return;
  }
  if (ui.shownFile !== file.id) {
    ui.shownFile = file.id;
    ui.page = 1;
    ui.pages = 0;
    const res = await api("GET", `/api/files/${encodeURIComponent(file.id)}/pages`).catch(() => ({ count: 0 }));
    if (ui.shownFile !== file.id) return;
    ui.pages = res.count;
    showPage();
  }
  drawOverlay();
}

function showPage() {
  const file = currentFile();
  updatePager();
  if (!file || !ui.pages) {
    $("page-wrap").hidden = true;
    $("viewer-help").textContent = file && file.kind === "xml"
      ? "E-invoice (XML): no page image. The values come straight from the file."
      : "No preview for this file.";
    return;
  }
  $("viewer-help").textContent = "Coloured boxes show what the model found. Faded boxes are not exported.";
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
  const file = currentFile();
  if (!file || !file.row || !img.naturalWidth || $("page-wrap").hidden) {
    overlay.replaceChildren();
    return;
  }
  const w = img.naturalWidth;
  const h = img.naturalHeight;
  overlay.setAttribute("viewBox", `0 0 ${w} ${h}`);
  const font = Math.max(10, Math.round(w / 75));
  const nodes = [];
  let focused = null;
  for (const [key, cell] of Object.entries(file.row.cells || {})) {
    const box = cell.box;
    if (!box || box.page !== ui.page) continue;
    const field = fieldOf(key);
    const off = field ? !field.selected : true;
    const color = colorOf(key);
    const x = box.x0 * w;
    const y = box.y0 * h;
    const rect = svg("rect", { class: "box" + (off ? " off" : "") + (key === ui.focusKey ? " focus" : ""), x, y,
      width: (box.x1 - box.x0) * w, height: (box.y1 - box.y0) * h, rx: 3, fill: color, stroke: color });
    nodes.push(rect);
    if (key === ui.focusKey) focused = rect;
    // the tag sits right of the box, or left of it near the page edge, so it never hides the text
    const label = (field && field.label) || cell.label || key;
    const tagWidth = label.length * font * 0.62 + font;
    const right = box.x1 * w + font / 2;
    const tagX = right + tagWidth <= w ? right : Math.max(0, x - tagWidth - font / 2);
    const tagY = Math.max(0, ((box.y0 + box.y1) / 2) * h - font * 0.7);
    const tag = svg("g", { class: "tag" + (off ? " off" : "") });
    tag.append(svg("rect", { x: tagX, y: tagY, width: tagWidth, height: font * 1.4, rx: 3, fill: color }));
    const text = svg("text", { x: tagX + font / 2, y: tagY + font * 1.05, "font-size": font });
    text.textContent = label;
    tag.append(text);
    nodes.push(tag);
  }
  overlay.replaceChildren(...nodes);
  if (focused) focused.scrollIntoView({ block: "center", behavior: "smooth" });
}

function focusField(key) {
  ui.focusKey = key;
  const cell = cellOf(currentFile(), key);
  if (!cell || !cell.box) {
    toast("This field has no box on this invoice.");
  } else if (cell.box.page !== ui.page && cell.box.page <= ui.pages) {
    ui.page = cell.box.page;
    showPage();
  }
  renderFields();
  drawOverlay();
}

// ---- actions -----------------------------------------------------------

async function putSelection(keys) {
  await api("PUT", "/api/selection", { keys });
  await refresh();
}

function toggleField(key, on) {
  const keys = new Set(selectedFields().map((f) => f.key));
  if (on) keys.add(key);
  else keys.delete(key);
  return putSelection(ui.data.fields.filter((f) => keys.has(f.key)).map((f) => f.key));
}

function selectGroup(kind, on) {
  const keys = ui.data.fields.filter((f) => (f.kind === kind ? on : f.selected)).map((f) => f.key);
  return putSelection(keys);
}

async function uploadFiles(list) {
  if (!list || !list.length) return;
  const form = new FormData();
  for (const file of list) form.append("files", file, file.name);
  await api("POST", "/api/files", form);
  await refresh();
}

async function loadTemplate(event) {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  const form = new FormData();
  form.append("file", file, file.name);
  await api("POST", "/api/template", form);
  ui.shownFile = null;
  await refresh();
}

async function useAllFields() {
  await api("DELETE", "/api/template");
  ui.shownFile = null;
  await refresh();
}

async function clearAll() {
  if (!ui.data.files.length || !confirm("Remove all files from this session?")) return;
  await api("DELETE", "/api/files");
  ui.focusKey = null;
  await refresh();
}

function toggleExport(open) {
  const list = document.querySelector("#export-menu .menu-list");
  const show = open === undefined ? list.hidden : open;
  list.hidden = !show;
  $("export-button").setAttribute("aria-expanded", String(show));
}

function exportAs(format) {
  toggleExport(false);
  const link = el("a", { href: `/api/export?format=${format}` });
  document.body.append(link);
  link.click();
  link.remove();
}

function setup() {
  $("file-input").addEventListener("change", guard(async (event) => {
    const files = Array.from(event.target.files);
    event.target.value = "";
    await uploadFiles(files);
  }));
  document.addEventListener("dragover", (event) => {
    event.preventDefault();
    document.body.classList.add("dragging");
  });
  document.addEventListener("dragleave", (event) => {
    if (!event.relatedTarget) document.body.classList.remove("dragging");
  });
  document.addEventListener("drop", guard(async (event) => {
    event.preventDefault();
    document.body.classList.remove("dragging");
    await uploadFiles(Array.from(event.dataTransfer.files));
  }));
  $("export-button").addEventListener("click", (event) => {
    event.stopPropagation();
    toggleExport();
  });
  document.querySelectorAll("#export-menu .menu-item").forEach((item) => {
    item.addEventListener("click", () => exportAs(item.dataset.format));
  });
  document.addEventListener("click", () => toggleExport(false));
  $("clear-button").addEventListener("click", guard(clearAll));
  $("all-fields-button").addEventListener("click", guard(useAllFields));
  $("template-input").addEventListener("change", guard(loadTemplate));
  $("prev-file").addEventListener("click", () => flipFile(-1));
  $("next-file").addEventListener("click", () => flipFile(1));
  $("prev-page").addEventListener("click", () => turnPage(-1));
  $("next-page").addEventListener("click", () => turnPage(1));
  $("page-image").addEventListener("load", drawOverlay);
  document.addEventListener("keydown", (event) => {
    if (ui.editing || event.target.tagName === "INPUT") return;
    if (event.key === "ArrowRight") flipFile(1);
    if (event.key === "ArrowLeft") flipFile(-1);
  });
  guard(refresh)();
}

setup();
