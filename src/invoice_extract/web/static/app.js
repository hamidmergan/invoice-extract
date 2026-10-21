"use strict";

// Review page for invoice-extract. Plain DOM, no framework. All data goes in via textContent.

const SVG_NS = "http://www.w3.org/2000/svg";
const RESERVED = new Set(["file", "status"]);
const E_INVOICE_KINDS = new Set(["xml", "zugferd"]);
const ICONS = { ok: "✓", warning: "!", error: "×" };

const ui = {
  data: { columns: [], files: [], summary: { files: 0, to_check: 0, reading: 0 } },
  selected: null,
  page: 1,
  pages: 0,
  shown: null, // "<file id>:<state>" of the file the viewer currently shows
  document: null, // the e-invoice document of the shown file, once loaded
  showDocument: false,
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

function issuesOf(file) {
  const row = file.row;
  if (!row) return [];
  if (row.error) return [{ level: "error", message: row.error }];
  const issues = row.checks.filter((c) => c.level !== "ok");
  for (const [key, cell] of Object.entries(row.cells)) {
    const label = (ui.data.columns.find((c) => c.key === key) || { label: key }).label;
    for (const c of cell.checks) {
      if (c.level !== "ok") issues.push({ level: c.level, message: `${label}: ${c.message}` });
    }
  }
  return issues;
}

function statusText(file) {
  if (file.state !== "done") return "reading…";
  if (file.row.error) return file.row.error;
  const issues = issuesOf(file);
  return issues.length ? `Check: ${issues[0].message}` : "OK";
}

// ---- rendering ---------------------------------------------------------

function render() {
  renderSummary();
  if (!ui.editing) renderTable();
  renderValues();
  renderChecks();
  renderViewer();
}

function renderSummary() {
  const s = ui.data.summary;
  if (!s.files) {
    $("summary").textContent = "Add the invoices you received. Every value is checked and can be corrected; "
      + "the table goes to Excel, CSV or JSON.";
    return;
  }
  let text = `${s.files} invoice${s.files === 1 ? "" : "s"} · ${s.to_check} to check`;
  if (s.reading) text += ` · ${s.reading} reading`;
  $("summary").textContent = text;
}

function dotClass(file) {
  return file.state === "done" ? file.status : "pending";
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
  if (col.key === "file") {
    return el("td", { class: "file-cell", title: file.name }, el("span", { class: `dot ${dotClass(file)}` }), file.name);
  }
  if (col.key === "status") {
    return el("td", { class: file.state === "done" && file.status !== "ok" ? `status-${file.status}` : null },
      statusText(file));
  }
  const cell = cellOf(file, col.key);
  const editable = file.state === "done" && !file.row.error && col.kind !== "table";
  const classes = [editable ? "editable" : "", cell && cell.edited ? "edited" : "",
    col.type === "amount" ? "amount" : ""].join(" ").trim();
  const td = el("td", { class: classes || null, title: cell && cell.raw ? cell.raw : null }, cellText(cell));
  if (editable) {
    td.addEventListener("click", (event) => {
      event.stopPropagation();
      // select the row without rebuilding the table: this cell must stay in the page to hold the editor
      if (file.id !== ui.selected) {
        ui.selected = file.id;
        ui.focusKey = null;
        for (const tr of document.querySelectorAll("#table tr.selected")) tr.classList.remove("selected");
        td.parentElement.classList.add("selected");
      }
      editCell(td, file, col, cell);
      renderValues();
      renderChecks();
      renderViewer();
    });
  }
  return td;
}

function cellText(cell) {
  if (!cell || cell.value == null) return "";
  if (cell.kind === "table") return `${cell.value.length} rows`;
  return display(cell.value);
}

function editCell(td, file, col, cell) {
  if (ui.editing) return;
  ui.editing = true;
  const input = el("input", { type: "text", "aria-label": col.label });
  input.value = cell && cell.value != null && cell.kind !== "table" ? display(cell.value) : "";
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

function renderValues() {
  const list = $("values");
  const file = selectedFile();
  if (!file || !file.row || file.row.error) {
    list.replaceChildren(el("dd", { class: "muted" }, file ? statusText(file) : "No invoice selected"));
    return;
  }
  const items = [];
  for (const col of ui.data.columns) {
    if (RESERVED.has(col.key)) continue;
    const cell = cellOf(file, col.key);
    const hasBox = cell && cell.box;
    items.push(el("dt", null, col.label));
    items.push(el("dd", { class: hasBox ? "clickable" : null, title: hasBox ? "Show on the page" : null,
      onclick: hasBox ? () => focusCell(col.key, cell) : null },
    el("span", { class: "value" }, cell && cell.value != null ? cellText(cell) : "–"),
    cell && cell.source ? el("span", { class: "badge", title: sourceTitle(cell.source) }, cell.source) : null,
    cell && cell.edited ? el("span", { class: "badge edited" }, "edited") : null));
  }
  list.replaceChildren(...items);
}

function sourceTitle(source) {
  return { xml: "Read from the e-invoice XML, exact", text: "Read by the model from the PDF text",
    vision: "Read by the vision model from the scan", manual: "Entered by you" }[source] || source;
}

function renderChecks() {
  const list = $("checks");
  const file = selectedFile();
  const checks = file && file.row && !file.row.error
    ? [...file.row.checks, ...Object.entries(file.row.cells).flatMap(([key, cell]) => cell.checks.map((c) => ({
      level: c.level, message: `${(ui.data.columns.find((x) => x.key === key) || { label: key }).label}: ${c.message}`,
    })))]
    : issuesOf(file || { row: null });
  if (!checks.length) {
    list.replaceChildren(el("li", { class: "muted" }, file ? (file.state === "done" ? "Nothing to check" : "reading…")
      : "No invoice selected"));
    return;
  }
  list.replaceChildren(...checks.map((c) => el("li", null,
    el("span", { class: `icon ${c.level}` }, ICONS[c.level] || "•"), el("span", null, c.message))));
}

// ---- viewer ------------------------------------------------------------

function select(id) {
  if (id === ui.selected) return;
  ui.selected = id;
  ui.focusKey = null;
  render();
}

async function renderViewer() {
  const file = selectedFile();
  $("viewer-name").textContent = file ? file.name : "No invoice selected";
  if (!file) {
    ui.shown = null;
    ui.document = null;
    ui.showDocument = false;
    ui.page = 1;
    ui.pages = 0;
    showPage();
    return;
  }
  const key = `${file.id}:${file.state}`;
  if (ui.shown !== key) {
    const sameFile = ui.shown !== null && ui.shown.split(":")[0] === file.id;
    ui.shown = key;
    if (!sameFile) {
      ui.page = 1;
      ui.pages = 0;
      ui.document = null;
      ui.showDocument = false;
    }
    const res = await api("GET", `/api/files/${encodeURIComponent(file.id)}/pages`).catch(() => ({ count: 0 }));
    if (ui.shown !== key) return;
    ui.pages = res.count;
    if (E_INVOICE_KINDS.has(file.kind) && file.state === "done") {
      ui.document = await api("GET", `/api/files/${encodeURIComponent(file.id)}/document`).catch(() => null);
      if (ui.shown !== key) return;
      if (!ui.pages) ui.showDocument = true;
    }
    showPage();
  }
  drawOverlay();
}

function showPage() {
  const file = selectedFile();
  const wrap = $("page-wrap");
  const help = $("viewer-help");
  const sheet = $("document");
  const showDoc = Boolean(file && ui.document && ui.showDocument);
  updatePager();
  $("toggle-document").hidden = !(file && ui.document && ui.pages);
  $("toggle-document").textContent = ui.showDocument ? "Show page" : "Show e-invoice data";
  $("print-button").hidden = !showDoc;
  sheet.hidden = !showDoc;
  if (showDoc) {
    wrap.hidden = true;
    help.hidden = true;
    sheet.replaceChildren(...documentNodes(ui.document));
    return;
  }
  if (!file || !ui.pages) {
    wrap.hidden = true;
    help.hidden = false;
    if (!file) help.textContent = "Add invoices to start. Click a row to see the page and where each value was found; "
      + "click a cell to correct it.";
    else if (file.state !== "done") help.textContent = "Reading…";
    else if (file.row && file.row.error) help.textContent = "This file could not be read.";
    else help.textContent = "No preview for this file.";
    return;
  }
  help.hidden = false;
  help.textContent = file.kind === "scan" ? "Boxes come from the vision model and are best effort."
    : "Boxes show where each value was found. Click a value on the right to jump to it.";
  wrap.hidden = false;
  $("page-image").src = `/api/files/${encodeURIComponent(file.id)}/pages/${ui.page}.png`;
}

function updatePager() {
  const paging = ui.pages > 0 && !ui.showDocument;
  $("page-label").textContent = paging ? `Page ${ui.page} of ${ui.pages}` : "";
  $("prev-page").hidden = !paging;
  $("next-page").hidden = !paging;
  $("prev-page").disabled = ui.page <= 1;
  $("next-page").disabled = ui.page >= ui.pages;
}

function turnPage(delta) {
  const page = ui.page + delta;
  if (page < 1 || page > ui.pages) return;
  ui.page = page;
  showPage();
}

function toggleDocument() {
  ui.showDocument = !ui.showDocument;
  showPage();
  drawOverlay();
}

function drawOverlay() {
  const img = $("page-image");
  const overlay = $("overlay");
  const file = selectedFile();
  if (!file || !img.naturalWidth || $("page-wrap").hidden) {
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
  overlay.replaceChildren(...nodes);
}

function focusCell(key, cell) {
  ui.focusKey = key;
  if (ui.showDocument) ui.showDocument = false;
  if (cell.box.page !== ui.page && cell.box.page <= ui.pages) {
    ui.page = cell.box.page;
    showPage();
  } else {
    showPage();
    drawOverlay();
  }
}

// ---- the e-invoice sheet -----------------------------------------------

function documentNodes(doc) {
  const money = (value) => (value ? `${value} ${doc.currency || ""}`.trim() : "");
  const line = (text, cls) => (text ? el("div", { class: cls || null }, text) : null);
  const party = (p, title) => el("div", { class: "sheet-party" },
    el("h3", null, title),
    el("div", { class: "sheet-name" }, p.name || "–"),
    line(p.street), line([p.postcode, p.city].filter(Boolean).join(" ")), line(p.country),
    line(p.vat_id && `VAT ID ${p.vat_id}`, "muted"), line(p.tax_number && `Tax no. ${p.tax_number}`, "muted"),
    line(p.contact, "muted"), line(p.email, "muted"), line(p.phone, "muted"));
  const facts = [
    ["Date", doc.issue_date], ["Due", doc.due_date], ["Delivery", doc.delivery_date],
    ["Period", doc.period_start || doc.period_end ? `${doc.period_start} – ${doc.period_end}` : ""],
    ["Buyer reference", doc.buyer_reference], ["Order", doc.order_reference], ["Currency", doc.currency],
  ].filter(([, value]) => value);
  const totals = [
    ["Lines total", doc.totals.lines], ["Allowances", doc.totals.allowances], ["Charges", doc.totals.charges],
    ["Net", doc.totals.net], ["VAT", doc.totals.vat], ["Gross", doc.totals.gross], ["Prepaid", doc.totals.prepaid],
    ["Amount due", doc.totals.due],
  ].filter(([, value]) => value);
  const payment = [
    ["Payment", doc.payment.means], ["IBAN", doc.payment.iban], ["BIC", doc.payment.bic],
    ["Account", doc.payment.account_name], ["Terms", doc.payment.terms], ["Mandate", doc.payment.mandate_reference],
  ].filter(([, value]) => value);
  const table = (headers, rows, cls) => el("table", { class: `sheet-table ${cls || ""}`.trim() },
    el("thead", null, el("tr", null, headers.map((h) => el("th", { class: h.right ? "amount" : null }, h.text)))),
    el("tbody", null, rows.map((cells) => el("tr", null,
      cells.map((c, i) => el("td", { class: headers[i].right ? "amount" : null }, c))))));
  const pairs = (items, cls) => el("dl", { class: `sheet-pairs ${cls || ""}`.trim() },
    items.flatMap(([label, value]) => [el("dt", null, label), el("dd", null, value)]));
  return [
    el("header", { class: "sheet-head" },
      el("h2", null, `${doc.kind === "credit note" ? "Credit note" : "Invoice"} ${doc.number}`.trim()),
      pairs(facts)),
    el("div", { class: "sheet-parties" }, party(doc.seller, "From"), party(doc.buyer, "To")),
    doc.lines.length ? table(
      [{ text: "#" }, { text: "Description" }, { text: "Qty", right: true }, { text: "Unit" },
        { text: "Unit price", right: true }, { text: "VAT %", right: true }, { text: "Amount", right: true }],
      doc.lines.map((l, i) => [String(i + 1), l.description, l.quantity, l.unit, l.unit_price, l.vat_rate, l.amount]))
      : el("p", { class: "muted" }, "No lines"),
    el("div", { class: "sheet-foot" },
      doc.taxes.length ? table(
        [{ text: "VAT" }, { text: "Rate %", right: true }, { text: "Basis", right: true }, { text: "Tax", right: true }],
        doc.taxes.map((t) => [t.category, t.rate, t.basis, t.tax]), "sheet-taxes") : null,
      pairs(totals.map(([label, value]) => [label, money(value)]), "sheet-totals")),
    payment.length ? pairs(payment, "sheet-payment") : null,
    doc.notes.length ? el("div", { class: "sheet-notes" }, doc.notes.map((n) => el("p", null, n))) : null,
  ];
}

// ---- actions -----------------------------------------------------------

async function uploadFiles(list) {
  if (!list || !list.length) return;
  const form = new FormData();
  for (const file of list) form.append("files", file, file.name);
  await api("POST", "/api/files", form);
  await refresh();
}

async function clearAll() {
  if (!ui.data.files.length || !confirm("Remove all invoices from this session?")) return;
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
  if (!ui.data.files.length) {
    toast("Add invoices first.");
    return;
  }
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
  $("toggle-document").addEventListener("click", toggleDocument);
  $("print-button").addEventListener("click", () => window.print());
  $("page-image").addEventListener("load", drawOverlay);
  document.addEventListener("keydown", (event) => {
    if (ui.editing || event.target.tagName === "INPUT") return;
    if (event.key === "ArrowLeft") turnPage(-1);
    if (event.key === "ArrowRight") turnPage(1);
  });
  guard(refresh)();
}

setup();
