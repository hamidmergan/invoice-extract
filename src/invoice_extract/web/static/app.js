"use strict";

// Review UI for invoice-extract. Plain DOM, no framework. All data goes in via textContent.

const SVG_NS = "http://www.w3.org/2000/svg";
const RESERVED = new Set(["file", "status"]);
const CLICKABLE_KINDS = new Set(["pdf", "zugferd"]);
const ICONS = { ok: "✓", warning: "!", error: "×" };

const ui = {
  data: { columns: [], files: [], summary: { files: 0, columns: 0, to_check: 0 } },
  selected: null,
  page: 1,
  pages: 0,
  shownFile: null,
  segments: null,
  focusKey: null,
  editing: false,
  timer: null,
};

const $ = (id) => document.getElementById(id);

function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
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

function busy() {
  return ui.data.files.some((f) => f.state !== "done");
}

async function refresh() {
  ui.data = await api("GET", "/api/state");
  if (!ui.data.files.some((f) => f.id === ui.selected)) {
    ui.selected = ui.data.files.length ? ui.data.files[0].id : null;
  }
  render();
  clearTimeout(ui.timer);
  ui.timer = busy() ? setTimeout(guard(refresh), 1000) : null;
}

function selectedFile() {
  return ui.data.files.find((f) => f.id === ui.selected) || null;
}

function cellOf(file, key) {
  return file && file.row && file.row.cells ? file.row.cells[key] : undefined;
}

function display(value) {
  return value == null ? "" : String(value);
}

function statusText(file) {
  if (file.state !== "done") return file.row ? "filling new columns…" : "reading…";
  const row = file.row;
  if (row.error) return row.error;
  const issues = row.checks.filter((c) => c.level !== "ok");
  for (const cell of Object.values(row.cells)) issues.push(...cell.checks.filter((c) => c.level !== "ok"));
  return issues.length ? `Check: ${issues[0].message}` : "OK";
}

// ---- rendering ---------------------------------------------------------

function render() {
  renderStrip();
  renderColumns();
  renderValues();
  renderChecks();
  renderSummary();
  if (!ui.editing) renderTable();
  renderViewer();
}

function dotClass(file) {
  return file.state === "done" ? file.status : file.state;
}

function renderStrip() {
  const strip = $("strip");
  strip.replaceChildren(...ui.data.files.map((f) => el("button", {
    class: "chip" + (f.id === ui.selected ? " selected" : ""),
    title: f.name,
    onclick: () => select(f.id),
  }, el("span", { class: `dot ${dotClass(f)}` }), el("span", { class: "name" }, f.name))));
}

function renderColumns() {
  $("columns-title").textContent = `Columns (${ui.data.columns.length})`;
  $("columns").replaceChildren(...ui.data.columns.map((c) => el("span", { class: "chip", title: c.hint || c.label },
    c.label,
    el("button", { class: "remove", "aria-label": `Remove column ${c.label}`, onclick: guard(() => removeColumn(c)) }, "×"))));
}

function renderValues() {
  const list = $("values");
  const file = selectedFile();
  if (!file || !file.row || file.row.error) {
    list.replaceChildren(el("dd", { class: "muted" }, file ? statusText(file) : "No file selected"));
    return;
  }
  const items = [];
  for (const col of ui.data.columns) {
    if (RESERVED.has(col.key)) continue;
    const cell = cellOf(file, col.key);
    const hasBox = cell && cell.box;
    items.push(el("dt", null, col.label));
    items.push(el("dd", { class: hasBox ? "clickable" : null, onclick: hasBox ? () => focusCell(col.key, cell) : null },
      el("span", { class: "value" }, cell && cell.value != null ? display(cell.value) : "–"),
      cell && cell.source ? el("span", { class: "badge" }, cell.source) : null,
      cell && cell.edited ? el("span", { class: "badge edited" }, "edited") : null));
  }
  list.replaceChildren(...items);
}

function renderChecks() {
  const list = $("checks");
  const file = selectedFile();
  const checks = [];
  if (file && file.row) {
    if (file.row.error) checks.push({ level: "error", message: file.row.error });
    else {
      checks.push(...file.row.checks);
      for (const [key, cell] of Object.entries(file.row.cells)) {
        const label = (ui.data.columns.find((c) => c.key === key) || { label: key }).label;
        for (const c of cell.checks) checks.push({ level: c.level, message: `${label}: ${c.message}` });
      }
    }
  }
  if (!checks.length) {
    list.replaceChildren(el("li", { class: "muted" }, file ? "No checks yet" : "No file selected"));
    return;
  }
  list.replaceChildren(...checks.map((c) => el("li", null,
    el("span", { class: `icon ${c.level}` }, ICONS[c.level] || "•"), el("span", null, c.message))));
}

function renderSummary() {
  const s = ui.data.summary;
  const reading = ui.data.files.filter((f) => f.state !== "done").length;
  let text = `${s.files} files · ${s.columns} columns · ${s.to_check} to check`;
  if (reading) text += ` · ${reading} reading`;
  $("summary").textContent = text;
}

function renderTable() {
  const cols = ui.data.columns;
  const head = el("thead", null, el("tr", null, cols.map((c) => el("th", { title: c.hint || null }, c.label))));
  const body = el("tbody", null, ui.data.files.map((f) => el("tr", {
    class: f.id === ui.selected ? "selected" : null,
    onclick: () => select(f.id),
  }, cols.map((c) => tableCell(f, c)))));
  $("table").replaceChildren(head, body);
}

function tableCell(file, col) {
  if (col.key === "file") return el("td", null, file.name);
  if (col.key === "status") {
    return el("td", { class: file.state === "done" && file.status !== "ok" ? `status-${file.status}` : null },
      statusText(file));
  }
  const cell = cellOf(file, col.key);
  const editable = file.state === "done" && !file.row.error;
  const classes = [editable ? "editable" : "", cell && cell.edited ? "edited" : ""].join(" ").trim();
  const td = el("td", { class: classes || null }, cell ? display(cell.value) : "");
  if (editable) {
    td.addEventListener("click", (event) => {
      event.stopPropagation();
      select(file.id);
      editCell(td, file, col, cell);
    });
  }
  return td;
}

function editCell(td, file, col, cell) {
  if (ui.editing) return;
  ui.editing = true;
  const input = el("input", { type: "text", "aria-label": col.label });
  input.value = cell ? display(cell.value) : "";
  let done = false;
  const finish = () => { done = true; ui.editing = false; renderTable(); };
  input.addEventListener("keydown", guard(async (event) => {
    if (event.key === "Escape") finish();
    if (event.key !== "Enter") return;
    done = true;
    try {
      await api("PATCH", `/api/files/${encodeURIComponent(file.id)}/cells/${encodeURIComponent(col.key)}`,
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
  ui.focusKey = null;
  render();
}

async function renderViewer() {
  const file = selectedFile();
  $("viewer-name").textContent = file ? file.name : "No file selected";
  if (!file) {
    ui.shownFile = null;
    $("page-wrap").hidden = true;
    $("viewer-help").hidden = false;
    updatePager();
    return;
  }
  if (ui.shownFile !== file.id) {
    ui.shownFile = file.id;
    ui.page = 1;
    ui.pages = 0;
    closeSegments();
    const res = await api("GET", `/api/files/${encodeURIComponent(file.id)}/pages`).catch(() => ({ count: 0 }));
    if (ui.shownFile !== file.id) return;
    ui.pages = res.count;
    showPage();
  }
  drawOverlay();
}

function showPage() {
  const file = selectedFile();
  const wrap = $("page-wrap");
  const help = $("viewer-help");
  updatePager();
  if (!file || !ui.pages) {
    wrap.hidden = true;
    help.hidden = false;
    help.textContent = file && file.kind === "xml"
      ? "E-invoice (XML): no page image. The values come straight from the file."
      : "No preview for this file.";
    return;
  }
  help.hidden = false;
  help.textContent = CLICKABLE_KINDS.has(file.kind)
    ? "Click a spot on the page to pick a field as a new column."
    : "Boxes show where the model found each value.";
  wrap.hidden = false;
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
  closeSegments();
  showPage();
}

function drawOverlay() {
  const img = $("page-image");
  const overlay = $("overlay");
  const file = selectedFile();
  if (!file || !img.naturalWidth) {
    overlay.replaceChildren();
    return;
  }
  const w = img.naturalWidth;
  const h = img.naturalHeight;
  overlay.setAttribute("viewBox", `0 0 ${w} ${h}`);
  const font = Math.max(10, Math.round(w / 70));
  const nodes = [];
  for (const col of ui.data.columns) {
    const cell = cellOf(file, col.key);
    const box = cell && cell.box;
    if (!box || box.page !== ui.page) continue;
    const x = box.x0 * w;
    const y = box.y0 * h;
    nodes.push(svg("rect", { class: "box" + (col.key === ui.focusKey ? " focus" : ""), x, y,
      width: (box.x1 - box.x0) * w, height: (box.y1 - box.y0) * h, rx: 3 }));
    // the tag sits right of the box, or left of it near the page edge, so it never hides the text
    const tag = svg("g", { class: "box-tag" });
    const tagWidth = col.label.length * font * 0.62 + font;
    const right = box.x1 * w + font / 2;
    const tagX = right + tagWidth <= w ? right : Math.max(0, x - tagWidth - font / 2);
    const tagY = Math.max(0, ((box.y0 + box.y1) / 2) * h - font * 0.7);
    tag.append(svg("rect", { x: tagX, y: tagY, width: tagWidth, height: font * 1.4, rx: 3 }));
    const text = svg("text", { x: tagX + font / 2, y: tagY + font * 1.05, "font-size": font });
    text.textContent = col.label;
    tag.append(text);
    nodes.push(tag);
  }
  for (const seg of ui.segments || []) {
    const b = seg.box;
    const rect = svg("rect", { class: "seg", x: b.x0 * w, y: b.y0 * h, width: (b.x1 - b.x0) * w,
      height: (b.y1 - b.y0) * h });
    rect.dataset.index = seg.index;
    nodes.push(rect);
  }
  overlay.replaceChildren(...nodes);
}

function focusCell(key, cell) {
  ui.focusKey = key;
  if (cell.box.page !== ui.page && cell.box.page <= ui.pages) {
    ui.page = cell.box.page;
    closeSegments();
    showPage();
  } else {
    drawOverlay();
  }
}

async function onOverlayClick(event) {
  const file = selectedFile();
  if (!file || !CLICKABLE_KINDS.has(file.kind)) return;
  const index = event.target.dataset && event.target.dataset.index;
  if (index !== undefined) {
    openPopover(ui.segments.find((s) => String(s.index) === index));
    return;
  }
  if (ui.segments) {
    closeSegments();
    return;
  }
  ui.segments = await api("GET", `/api/files/${encodeURIComponent(file.id)}/pages/${ui.page}/segments`);
  if (!ui.segments.length) toast("This page has no text layer to pick from.");
  drawOverlay();
}

function closeSegments() {
  ui.segments = null;
  $("popover").hidden = true;
  drawOverlay();
}

function openPopover(seg) {
  if (!seg) return;
  const pop = $("popover");
  pop.dataset.index = seg.index;
  pop.style.left = `${Math.min(seg.box.x0 * 100, 60)}%`;
  pop.style.top = `${seg.box.y1 * 100}%`;
  $("popover-example").textContent = `Example: "${seg.text.trim()}"`;
  $("popover-title").value = "";
  pop.hidden = false;
  $("popover-title").focus();
}

async function createFromPopover() {
  const label = $("popover-title").value.trim();
  if (!label) {
    toast("Give the column a title first.");
    return;
  }
  const file = selectedFile();
  const index = Number($("popover").dataset.index);
  await api("POST", "/api/columns", { label, from: { file_id: file.id, page: ui.page, index } });
  closeSegments();
  await refresh();
}

// ---- actions -----------------------------------------------------------

async function uploadFiles(list) {
  if (!list || !list.length) return;
  const form = new FormData();
  for (const file of list) form.append("files", file, file.name);
  await api("POST", "/api/files", form);
  await refresh();
}

async function removeColumn(col) {
  await api("DELETE", `/api/columns/${encodeURIComponent(col.key)}`);
  await refresh();
}

async function addColumn(event) {
  event.preventDefault();
  const input = $("add-column-label");
  const label = input.value.trim();
  if (!label) return;
  await api("POST", "/api/columns", { label });
  input.value = "";
  await refresh();
}

async function loadTemplate(event) {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  const form = new FormData();
  form.append("file", file, file.name);
  await api("POST", "/api/template", form);
  await refresh();
}

async function clearAll() {
  if (!ui.data.files.length || !confirm("Remove all files from this session?")) return;
  await api("DELETE", "/api/files");
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
  const link = el("a", { href: `/api/export?format=${format}`, download: `invoices.${format}` });
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
  $("prev-page").addEventListener("click", () => turnPage(-1));
  $("next-page").addEventListener("click", () => turnPage(1));
  $("page-image").addEventListener("load", drawOverlay);
  $("overlay").addEventListener("click", guard(onOverlayClick));
  $("popover-cancel").addEventListener("click", () => { $("popover").hidden = true; });
  $("popover-create").addEventListener("click", guard(createFromPopover));
  $("popover-title").addEventListener("keydown", (event) => {
    if (event.key === "Enter") guard(createFromPopover)();
    if (event.key === "Escape") $("popover").hidden = true;
  });
  $("add-column").addEventListener("submit", guard(addColumn));
  $("template-input").addEventListener("change", guard(loadTemplate));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && ui.segments && !ui.editing) closeSegments();
  });
  guard(refresh)();
}

setup();
