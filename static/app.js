// Project list: all filtering and sorting happens here, on the stored projects.
// Shared helpers ($, esc, money, money inputs, district picker) are in common.js.
const D = window.DEFAULTS;
const STORE_KEY = "bidFinderFilters";
let projects = [];

function daysLeft(dateStr) {
  if (!dateStr) return null;
  const ms = new Date(dateStr + "T00:00:00") - new Date(todayISO() + "T00:00:00");
  return Math.round(ms / 86400000);
}
const isElectrical = (p) => p.is_electrical;
const STATUS_ORDER = ["new", "reviewing", "bidding", "passed"];

// Sort key per column. null/undefined always sort last, whichever direction.
const SORT_KEYS = {
  contract: (p) => p.contract,
  description: (p) => (p.description || "").toLowerCase(),
  route: (p) => `${p.district} ${p.county_route_pm || ""}`.toLowerCase(),
  estimate: (p) => p.estimate,
  advertised: (p) => p.advertised,
  bid_open: (p) => p.bid_open,
  days_left: (p) => daysLeft(p.bid_open),
  prebid: (p) => (p.mandatory_prebid ? 1 : 0),
  // Real electrical items count most; keywords break ties; unchecked last.
  electrical: (p) => p.electrical_items == null && !p.keyword_hits.length ? null
    : (p.electrical_items || []).filter((i) => !i.minor).length * 100 + p.keyword_hits.length,
  status: (p) => STATUS_ORDER.indexOf(p.status),
};
// First click on a column sorts this way; clicking again reverses it.
const FIRST_DIR = { estimate: "desc", advertised: "desc", prebid: "desc", electrical: "desc" };

// --- filter state -----------------------------------------------------------
function defaults() {
  return { districts: D.districts, min: D.estimate_min, max: D.estimate_max,
           electrical: true, hidePast: true, includeUpcoming: true, status: "", search: "",
           sort: "bid_open", dir: "asc" };
}
let sortState = { sort: "bid_open", dir: "asc" };

function loadState() {
  try {
    const s = { ...defaults(), ...JSON.parse(localStorage.getItem(STORE_KEY) || "{}") };
    if (s.allDistricts) s.districts = Object.keys(D.district_names);  // saved by older version
    delete s.allDistricts;
    if (!SORT_KEYS[s.sort] || !["asc", "desc"].includes(s.dir)) Object.assign(s, { sort: "bid_open", dir: "asc" });
    return s;
  } catch { return defaults(); }
}
function saveState(s) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(s)); } catch { /* private mode */ }
}
function readForm() {
  return {
    districts: checkedDistricts(),
    min: parseMoney($("est-min").value),
    max: parseMoney($("est-max").value),
    electrical: $("electrical-only").checked,
    hidePast: $("hide-past").checked,
    includeUpcoming: $("include-upcoming").checked,
    status: $("status-filter").value,
    search: $("search").value.trim(),
    ...sortState,
  };
}
function writeForm(s) {
  setDistricts(s.districts);
  setMoney($("est-min"), s.min);
  setMoney($("est-max"), s.max);
  $("electrical-only").checked = s.electrical;
  $("hide-past").checked = s.hidePast;
  $("include-upcoming").checked = s.includeUpcoming;
  $("status-filter").value = s.status;
  $("search").value = s.search;
  sortState = { sort: s.sort, dir: s.dir };
}


function applyFilters(s) {
  const today = todayISO();
  const q = s.search.toLowerCase();
  let rows = projects.filter((p) =>
    s.districts.includes(p.district) &&
    (s.min == null || (p.estimate ?? 0) >= s.min) &&
    (s.max == null || (p.estimate ?? 0) <= s.max) &&
    (!s.electrical || isElectrical(p)) &&
    (!s.hidePast || !p.bid_open || p.bid_open >= today) &&
    (s.includeUpcoming || p.stage !== "upcoming") &&
    (!s.status || p.status === s.status) &&
    (!q || [p.contract, p.description, p.location, p.county_route_pm, p.licenses]
      .join(" ").toLowerCase().includes(q))
  );
  const key = SORT_KEYS[s.sort], sign = s.dir === "desc" ? -1 : 1;
  const blank = (v) => v == null || v === "";
  return rows.sort((a, b) => {
    const x = key(a), y = key(b);
    if (blank(x) || blank(y)) return blank(x) - blank(y);
    const c = typeof x === "number" ? x - y : String(x).localeCompare(String(y), "en", { numeric: true });
    return sign * c || a.contract.localeCompare(b.contract);
  });
}

function showSortHeaders(s) {
  document.querySelectorAll("#sort-headers th").forEach((th) => {
    if (th.dataset.sort === s.sort) th.setAttribute("aria-sort", s.dir === "asc" ? "ascending" : "descending");
    else th.removeAttribute("aria-sort");
  });
}

// --- rendering --------------------------------------------------------------
function electricalCell(p) {
  const kw = p.keyword_hits.map((k) => `<span class="chip">${esc(k)}</span>`).join("");
  let items;
  if (p.electrical_items == null) {
    items = `<div class="muted">Bid items not checked</div>`;
  } else if (p.electrical_items.length === 0) {
    items = `<div class="muted">No electrical bid items (of ${p.bid_items_count})</div>`;
  } else {
    const major = p.electrical_items.filter((i) => !i.minor);
    const minor = p.electrical_items.filter((i) => i.minor);
    items = [...major, ...minor].map((i) =>
      `<div class="${i.minor ? "minor" : ""}" title="${esc(i.code)} ${esc(i.description)} — ${esc(i.quantity)} ${esc(i.unit)}${i.minor ? " (routine item, not counted)" : ""}">` +
      `<span class="chip ${i.minor ? "" : "elec"}">${esc(i.code)}</span> ${esc(i.description)}</div>`).join("");
  }
  return `<div class="chips">${kw}</div><div class="items">${items}</div>`;
}

function row(p) {
  const d = daysLeft(p.bid_open);
  const dCls = d == null ? "" : d < 0 ? "past" : d <= 7 ? "soon" : "";
  const statusOpts = ["new", "reviewing", "bidding", "passed"].map((s) =>
    `<option value="${s}"${s === p.status ? " selected" : ""}>${s[0].toUpperCase() + s.slice(1)}</option>`).join("");
  const l = p.links;
  return `<tr>
    <td class="contract">${esc(p.contract)}${p.is_new ? '<span class="badge">NEW</span>' : ""}
      <div class="links">
        <a href="${esc(l.listing)}" target="_blank" rel="noopener">Listing</a>
        <a href="${esc(l.bid_items)}" target="_blank" rel="noopener">Bid items</a>
        <a href="${esc(l.planholders)}" target="_blank" rel="noopener">Planholders</a>
        <a href="/alerts/preview/${encodeURIComponent(p.contract)}" target="_blank" rel="noopener">Email preview</a>
      </div></td>
    <td class="desc">${esc(p.description)}<div class="loc">${esc(p.location)}</div></td>
    <td class="nowrap">${esc(p.district)} · ${esc(p.county_route_pm)}</td>
    <td class="num">${money(p.estimate)}</td>
    <td class="date">${p.stage === "upcoming" ? '<span class="badge upcoming" title="Scheduled; not advertised yet">COMING SOON</span><br>' : ""}${esc(p.advertised)}</td>
    <td class="date">${esc(p.bid_open)}${p.postponed ? '<div class="muted">postponed</div>' : ""}</td>
    <td class="num ${dCls}">${d ?? "—"}</td>
    <td>${p.mandatory_prebid ? "<strong>Yes</strong>" : "No"}</td>
    <td class="elec">${electricalCell(p)}</td>
    <td><select data-contract="${esc(p.contract)}" aria-label="Status for ${esc(p.contract)}">${statusOpts}</select></td>
  </tr>`;
}

function render() {
  const s = readForm();
  saveState(s);
  $("district-summary").textContent = districtSummary(s.districts, D.district_names);
  showSortHeaders(s);
  const rows = applyFilters(s);
  $("rows").innerHTML = rows.map(row).join("");
  $("empty").hidden = rows.length > 0;
  const newCount = rows.filter((p) => p.is_new).length;
  $("summary").textContent = `${rows.length} of ${projects.length} advertised projects shown` +
    (newCount ? ` · ${newCount} new since your last visit` : "");
}

function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : "never";
}
function showRunInfo(info) {
  const ok = info.last_success;
  $("last-scrape").textContent = info.scraping ? "Scraping Caltrans…" : `Last scrape: ${fmtTime(ok && ok.finished_at)}`;
  $("refresh-btn").disabled = info.scraping;
  const last = info.last_run, alert = $("run-alert");
  if (last && last.error) {
    alert.className = "alert error";
    alert.textContent = `Last scrape failed (${fmtTime(last.finished_at)}): ${last.error}`;
    alert.hidden = false;
  } else if (last && last.warning) {
    alert.className = "alert";
    alert.textContent = `Last scrape warning: ${last.warning}`;
    alert.hidden = false;
  } else {
    alert.hidden = true;
  }
}

// --- data -------------------------------------------------------------------
async function load() {
  const r = await fetch("/api/projects");
  const data = await r.json();
  projects = data.projects;
  showRunInfo(data);
  render();
  return data;
}

async function pollUntilDone() {
  for (;;) {
    await new Promise((res) => setTimeout(res, 3000));
    const info = await (await fetch("/api/status")).json();
    showRunInfo(info);
    if (!info.scraping) { loadAlertSettings(); return load(); }
  }
}

$("refresh-btn").addEventListener("click", async () => {
  $("refresh-btn").disabled = true;
  $("last-scrape").textContent = "Scraping Caltrans…";
  const r = await fetch("/api/refresh", { method: "POST" });
  if (!r.ok) {
    $("last-scrape").textContent = (await r.json().catch(() => ({}))).error || "Refresh failed";
    $("refresh-btn").disabled = false;
    return;
  }
  pollUntilDone();
});

$("rows").addEventListener("change", async (e) => {
  const sel = e.target.closest("select[data-contract]");
  if (!sel) return;
  const contract = sel.dataset.contract;
  const r = await fetch(`/api/projects/${encodeURIComponent(contract)}/status`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status: sel.value }),
  });
  if (r.ok) {
    projects.find((p) => p.contract === contract).status = sel.value;
  } else {
    sel.value = projects.find((p) => p.contract === contract).status;
  }
});

// --- alert filter ------------------------------------------------------------
function describeAlertFilter(a) {
  const range = a.estimate_min == null && a.estimate_max == null ? "any estimate"
    : `${a.estimate_min == null ? "up to" : money(a.estimate_min)}${a.estimate_min != null && a.estimate_max != null ? " – " : " "}${a.estimate_max == null ? "and up" : money(a.estimate_max)}`;
  return `<strong>${esc(districtSummary(a.districts, D.district_names))}</strong> · ${esc(range)} · ` +
    `${a.electrical_only ? "electrical work only" : "any trade"} · upcoming bids only`;
}
function showAlertSettings(data) {
  $("alert-filter").innerHTML = describeAlertFilter(data.settings);
  $("alert-count").textContent = data.alert_count || "";
}
async function loadAlertSettings() {
  showAlertSettings(await (await fetch("/api/alert-settings")).json());
}
$("save-alert-filter").addEventListener("click", async () => {
  const s = readForm();
  const r = await fetch("/api/alert-settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ districts: s.districts, estimate_min: s.min, estimate_max: s.max,
                           electrical_only: s.electrical }),
  });
  const data = await r.json();
  if (!r.ok) { $("alert-msg").textContent = data.error; return; }
  showAlertSettings(data);
  $("alert-msg").textContent = data.queued
    ? `Saved. ${data.queued} new email${data.queued === 1 ? "" : "s"} added to alerts.`
    : "Saved. No new matching projects.";
});

document.querySelector(".filters").addEventListener("input", render);
initDistrictPicker(D.district_names, render);
attachMoneyInput($("est-min"));
attachMoneyInput($("est-max"));
$("sort-headers").addEventListener("click", (e) => {
  const th = e.target.closest("th[data-sort]");
  if (!th) return;
  const col = th.dataset.sort;
  sortState = col === sortState.sort
    ? { sort: col, dir: sortState.dir === "asc" ? "desc" : "asc" }
    : { sort: col, dir: FIRST_DIR[col] || "asc" };
  render();
});
$("reset").addEventListener("click", () => { writeForm(defaults()); render(); });

(async () => {
  writeForm(loadState());
  await fetch("/api/visit", { method: "POST" });
  loadAlertSettings();
  const data = await load();
  if (data.scraping) pollUntilDone();
})();
