// Bid results: competitor summary + opened bids, filtered and sorted in the page.
const D = window.DEFAULTS;
const STORE_KEY = "bidFinderResultsFilters";
let results = [];
let unreadable = [];
let sortState = { sort: "bid_open", dir: "desc" };
let compSort = { sort: "bids", dir: "desc" };
let showAllCompetitors = false;
const open = new Set();  // expanded contracts

// Bidder names vary slightly between documents ("DB ELECTRIC, INC." / "DB ELECTRIC INC.").
const nameKey = (n) => String(n || "").toUpperCase().replace(/[.,]/g, " ")
  .replace(/\b(INC|LLC|CO|CORP|CORPORATION|COMPANY|LTD)\b/g, "").replace(/\s+/g, " ").trim();
const companyKey = nameKey(D.company);
const isCompany = (name) => companyKey && nameKey(name).includes(companyKey);
const pct = (x) => x == null ? "—" : `${x > 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
const vsEst = (amount, est) => est ? amount / est - 1 : null;
const hasElectrical = (r) => r.electrical_items.some((i) => !D.minor_items.includes(i.code));

// --- state ------------------------------------------------------------------
function defaults() { return { districts: D.districts, electrical: false, search: "" }; }
function loadState() {
  try { return { ...defaults(), ...JSON.parse(localStorage.getItem(STORE_KEY) || "{}") }; }
  catch { return defaults(); }
}
function readForm() {
  return { districts: checkedDistricts(), electrical: $("electrical-only").checked, search: $("search").value.trim() };
}
function writeForm(s) {
  setDistricts(s.districts);
  $("electrical-only").checked = s.electrical;
  $("search").value = s.search;
}

function filtered(s) {
  const q = s.search.toLowerCase();
  return results.filter((r) =>
    s.districts.includes(r.district) &&
    (!s.electrical || hasElectrical(r)) &&
    (!q || [r.contract, r.description, r.county_route_pm,
            ...r.bidders.map((b) => b.name), ...r.electrical_subs.map((x) => x.sub)]
      .join(" ").toLowerCase().includes(q)));
}

function sortRows(rows, keyFn, dir) {
  const sign = dir === "desc" ? -1 : 1, blank = (v) => v == null || v === "";
  return rows.sort((a, b) => {
    const x = keyFn(a), y = keyFn(b);
    if (blank(x) || blank(y)) return blank(x) - blank(y);
    const c = typeof x === "number" ? x - y : String(x).localeCompare(String(y), "en", { numeric: true });
    return sign * c;
  });
}
function showSort(headerRowId, s) {
  document.querySelectorAll(`#${headerRowId} th`).forEach((th) => {
    if (th.dataset.sort === s.sort) th.setAttribute("aria-sort", s.dir === "asc" ? "ascending" : "descending");
    else th.removeAttribute("aria-sort");
  });
}

// --- competitors -------------------------------------------------------------
const COMP_KEYS = {
  name: (c) => c.name, bids: (c) => c.bids, wins: (c) => c.wins, top3: (c) => c.top3,
  vsEst: (c) => c.vsEst, won: (c) => c.won,
};
function competitors(rows) {
  const by = new Map();
  for (const r of rows) {
    for (const b of r.bidders) {
      const k = nameKey(b.name);
      const c = by.get(k) || { name: b.name, bids: 0, wins: 0, top3: 0, diffs: [], won: 0 };
      if (b.name.length > c.name.length) c.name = b.name;  // keep the most complete spelling
      c.bids++;
      if (b.rank === 1) { c.wins++; c.won += b.amount; }
      if (b.rank <= 3) c.top3++;
      const d = vsEst(b.amount, r.engineers_estimate);
      if (d != null) c.diffs.push(d);
      by.set(k, c);
    }
  }
  return [...by.values()].map((c) => ({
    ...c, vsEst: c.diffs.length ? c.diffs.reduce((a, b) => a + b, 0) / c.diffs.length : null,
  }));
}
function renderCompetitors(rows) {
  const all = sortRows(competitors(rows), COMP_KEYS[compSort.sort], compSort.dir);
  const shown = showAllCompetitors ? all : all.slice(0, 12);
  showSort("comp-headers", compSort);
  $("comp-rows").innerHTML = shown.map((c) => `<tr class="${isCompany(c.name) ? "you" : ""}">
      <td>${esc(c.name)}${isCompany(c.name) ? ' <span class="badge you-badge">YOU</span>' : ""}</td>
      <td class="num">${c.bids}</td>
      <td class="num">${c.wins || "—"}</td>
      <td class="num">${c.top3 || "—"}</td>
      <td class="num">${pct(c.vsEst)}</td>
      <td class="num">${c.won ? money(c.won) : "—"}</td>
    </tr>`).join("") || `<tr><td colspan="6" class="muted">No bidders in these results.</td></tr>`;
  $("comp-scope").textContent = `· ${all.length} bidders across ${rows.length} opened bids`;
  $("comp-more").innerHTML = all.length > 12
    ? `<button type="button" class="link" id="comp-toggle">${showAllCompetitors ? "Show top 12" : `Show all ${all.length}`}</button>` : "";
}

// --- opened bids -------------------------------------------------------------
const SORT_KEYS = {
  contract: (r) => r.contract,
  description: (r) => (r.description || "").toLowerCase(),
  bid_open: (r) => r.bid_open,
  estimate: (r) => r.engineers_estimate,
  low: (r) => r.bidders[0]?.amount,
  vs: (r) => vsEst(r.bidders[0]?.amount, r.engineers_estimate),
  bidders: (r) => r.bidders.length,
  low_bidder: (r) => r.bidders[0]?.name,
  electrical: (r) => r.electrical_total || null,
};
const FIRST_DIR = { bid_open: "desc", estimate: "desc", low: "desc", bidders: "desc", electrical: "desc" };

function detail(r) {
  const low = r.bidders[0]?.amount;
  const bidders = r.bidders.map((b) => `<tr class="${isCompany(b.name) ? "you" : ""}">
      <td class="num">${b.rank}</td>
      <td>${esc(b.name)}${isCompany(b.name) ? ' <span class="badge you-badge">YOU</span>' : ""}</td>
      <td class="num">${money(b.amount)}</td>
      <td class="num">${b.rank === 1 ? "low" : pct(b.amount / low - 1)}</td>
      <td class="num">${pct(vsEst(b.amount, r.engineers_estimate))}</td>
    </tr>`).join("");
  const items = r.electrical_items.length
    ? r.electrical_items.map((i) => `<tr class="${D.minor_items.includes(i.code) ? "minor" : ""}">
        <td><span class="chip ${D.minor_items.includes(i.code) ? "" : "elec"}">${esc(i.code)}</span></td>
        <td>${esc(i.description)}</td><td class="num">${esc(i.quantity)} ${esc(i.unit)}</td>
        <td class="num">${money(i.amount)}</td></tr>`).join("")
    : `<tr><td colspan="4" class="muted">No electrical items (sections 86/87) in this contract.</td></tr>`;
  const subs = r.electrical_subs.length
    ? r.electrical_subs.map((x) => `<li><strong>${esc(x.sub)}</strong>
        <span class="muted">for #${x.bidder_rank} ${esc(x.bidder)} · items ${x.items.map(esc).join(", ")}</span></li>`).join("")
    : `<li class="muted">None listed by item number.</li>`;
  return `<div class="detail-grid">
    <div><h3>All bidders</h3>
      <table class="compact inner"><thead><tr><th class="num">#</th><th>Bidder</th><th class="num">Total bid</th>
        <th class="num">vs low</th><th class="num">vs est.</th></tr></thead><tbody>${bidders}</tbody></table></div>
    <div><h3>Electrical items in the low bid${r.electrical_total ? ` · ${money(r.electrical_total)}` : ""}</h3>
      <table class="compact inner"><tbody>${items}</tbody></table>
      <h3>Electrical subcontractors listed</h3><ul class="subs">${subs}</ul>
      <p class="links-row"><a href="${esc(r.links.summary_pdf)}" target="_blank" rel="noopener">Bid Summary (PDF)</a> ·
        <a href="${esc(r.links.listing)}" target="_blank" rel="noopener">Project page</a></p></div>
  </div>`;
}

function row(r) {
  const low = r.bidders[0], d = vsEst(low?.amount, r.engineers_estimate), isOpen = open.has(r.contract);
  const you = r.bidders.find((b) => isCompany(b.name));
  return `<tr class="result-row${isOpen ? " open" : ""}" data-contract="${esc(r.contract)}">
    <td class="contract"><button type="button" class="expander" aria-expanded="${isOpen}">${isOpen ? "▾" : "▸"} ${esc(r.contract)}</button>
      ${you ? `<div><span class="badge you-badge" title="Your company bid">YOU #${you.rank}</span></div>` : ""}</td>
    <td class="desc">${esc(r.description)}<div class="loc">${esc(r.county_route_pm)}</div></td>
    <td class="date">${esc(r.bid_open)}</td>
    <td class="num">${money(r.engineers_estimate)}</td>
    <td class="num"><strong>${money(low?.amount)}</strong></td>
    <td class="num ${d != null && d < 0 ? "under" : "over"}">${pct(d)}</td>
    <td class="num">${r.bidders.length}</td>
    <td>${esc(low?.name)}</td>
    <td class="num">${r.electrical_total ? money(r.electrical_total) : '<span class="muted">—</span>'}
      ${r.electrical_subs.length ? `<div class="muted small">${r.electrical_subs.length} elec. sub${r.electrical_subs.length > 1 ? "s" : ""}</div>` : ""}</td>
  </tr>${isOpen ? `<tr class="detail-row"><td colspan="9">${detail(r)}</td></tr>` : ""}`;
}

function render() {
  const s = readForm();
  try { localStorage.setItem(STORE_KEY, JSON.stringify(s)); } catch { /* private mode */ }
  $("district-summary").textContent = districtSummary(s.districts, D.district_names);
  const rows = filtered(s);
  renderCompetitors(rows);
  showSort("sort-headers", sortState);
  const sorted = sortRows([...rows], SORT_KEYS[sortState.sort], sortState.dir);
  $("rows").innerHTML = sorted.map(row).join("");
  $("empty").hidden = rows.length > 0;
  $("summary").textContent = `· ${rows.length} of ${results.length}`;

  // Summaries Caltrans posted as scanned images: no data to show, just the PDF.
  const skipped = unreadable.filter((u) => s.districts.includes(u.district));
  $("unreadable").hidden = !skipped.length;
  $("unreadable").innerHTML = skipped.length
    ? `Not shown: ${skipped.length} Bid Summar${skipped.length === 1 ? "y was" : "ies were"} posted as scanned images ` +
      `and can't be read automatically: ` + skipped.map((u) =>
        `<a href="${esc(u.summary_pdf)}" target="_blank" rel="noopener">${esc(u.contract)}</a>`).join(", ") + "."
    : "";
}

function headerSorter(rowId, getState, setState, firstDir) {
  $(rowId).addEventListener("click", (e) => {
    const th = e.target.closest("th[data-sort]");
    if (!th) return;
    const st = getState(), col = th.dataset.sort;
    setState(col === st.sort ? { sort: col, dir: st.dir === "asc" ? "desc" : "asc" }
                             : { sort: col, dir: firstDir[col] || "asc" });
    render();
  });
}
headerSorter("sort-headers", () => sortState, (s) => (sortState = s), FIRST_DIR);
headerSorter("comp-headers", () => compSort, (s) => (compSort = s),
             { bids: "desc", wins: "desc", top3: "desc", won: "desc" });

$("rows").addEventListener("click", (e) => {
  if (e.target.closest("a")) return;
  const tr = e.target.closest("tr.result-row");
  if (!tr) return;
  const c = tr.dataset.contract;
  open.has(c) ? open.delete(c) : open.add(c);
  render();
});
$("comp-more").addEventListener("click", (e) => {
  if (e.target.id === "comp-toggle") { showAllCompetitors = !showAllCompetitors; render(); }
});
document.querySelector(".filters").addEventListener("input", render);
initDistrictPicker(D.district_names, render);

(async () => {
  writeForm(loadState());
  const data = await (await fetch("/api/bid-results")).json();
  results = data.results;
  unreadable = data.unreadable;
  render();
})();
