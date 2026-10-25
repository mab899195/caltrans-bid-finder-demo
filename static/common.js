// Helpers shared by the Projects and Bid results pages.
const $ = (id) => document.getElementById(id);

const money = (n) => n == null ? "—" : "$" + Math.round(n).toLocaleString("en-US");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function todayISO() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

// --- dollar inputs with thousands separators -----------------------------------
const formatDigits = (digits) => digits ? Number(digits).toLocaleString("en-US") : "";
function parseMoney(text) {
  const digits = String(text ?? "").replace(/\D/g, "");
  return digits ? Number(digits) : null;
}
function setMoney(input, n) {
  input.value = n == null ? "" : n.toLocaleString("en-US");
}
// Re-insert commas as the user types, keeping the caret after the same digit.
function attachMoneyInput(input) {
  input.addEventListener("input", () => {
    const caret = input.selectionStart ?? input.value.length;
    const digitsBefore = input.value.slice(0, caret).replace(/\D/g, "").length;
    const digits = input.value.replace(/\D/g, "").replace(/^0+(?=\d)/, "");
    input.value = formatDigits(digits);
    let pos = 0, seen = 0;
    while (pos < input.value.length && seen < digitsBefore) {
      if (/\d/.test(input.value[pos])) seen++;
      pos++;
    }
    input.setSelectionRange(pos, pos);
  });
}

// --- district picker (<details> with checkboxes named "district") -------------
function setDistricts(list) {
  document.querySelectorAll("input[name=district]").forEach((i) => (i.checked = list.includes(i.value)));
}
function checkedDistricts() {
  return [...document.querySelectorAll("input[name=district]:checked")].map((i) => i.value);
}
function districtSummary(list, names) {
  const all = Object.keys(names);
  if (list.length === 0) return "No districts";
  if (list.length === all.length) return "All districts";
  if (list.length === 1) return `${list[0]} ${names[list[0]]}`;
  return `Districts ${list.join(", ")}`;
}
function initDistrictPicker(names, onChange) {
  $("districts-all").addEventListener("click", () => { setDistricts(Object.keys(names)); onChange(); });
  $("districts-none").addEventListener("click", () => { setDistricts([]); onChange(); });
  document.addEventListener("click", (e) => {
    if (!$("district-picker").contains(e.target)) $("district-picker").open = false;
  });
}

// --- links to Caltrans project pages ------------------------------------------
// Caltrans project URLs contain "ad_id=". Some ad blockers' popup filters treat
// that as an ad and close the new tab within a second. Open a new tab as usual,
// and if it gets closed right away, open the page in this tab instead.
document.addEventListener("click", (e) => {
  const a = e.target.closest('a[target="_blank"][href*="ad_id="]');
  if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  const w = window.open(a.href, "_blank");
  if (!w) { location.href = a.href; return; }
  try { w.opener = null; } catch { /* already cross-origin */ }
  const started = Date.now();
  const timer = setInterval(() => {
    if (w.closed) { clearInterval(timer); location.href = a.href; }
    else if (Date.now() - started > 2500) clearInterval(timer);
  }, 200);
});
