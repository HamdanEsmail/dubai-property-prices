import { predictPpsqm } from "./model.js";
import { drawMonthly } from "./chart.js";

const state = { stats: null, meta: null, monthly: null, model: null, areaFile: null, areaFileName: null };
const $ = (id) => document.getElementById(id);
const fmtInt = (v) => Math.round(v).toLocaleString("en-US");
const fmtAed = (v) => (v >= 1e6 ? `AED ${(v / 1e6).toFixed(2)}M` : `AED ${fmtInt(v)}`);
const slug = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
const roomsN = (label) => (label === "Studio" ? 0 : /^(\d+) B\/R$/.exec(label) ? Number(RegExp.$1) : null);
const roomsText = (label) => (label === "Studio" ? "studio" : label === "PENTHOUSE" ? "penthouse" : label === "NA" ? "unlisted-rooms" : label);
const typeText = () => $("type").value.toLowerCase();
const DEFAULT_AREA = "Jumeirah Village Circle";

async function loadJson(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

function fill(select, options, value) {
  select.replaceChildren(...options.map((o) => new Option(o, o)));
  select.value = options.includes(value) ? value : options[0];
}

function area() { return state.stats.areas[$("area").value]; }
function sub() { return area()?.subtypes[$("type").value]; }
function cell() { return sub()?.rooms[$("rooms").value]; }

function readUrl() {
  const p = new URLSearchParams(location.search);
  return { area: p.get("area"), type: p.get("type"), rooms: p.get("rooms"), size: p.get("size"), offplan: p.get("offplan") };
}

function writeUrl() {
  const p = new URLSearchParams({ area: $("area").value, type: $("type").value, rooms: $("rooms").value, size: $("size").value, offplan: $("offplan").value });
  history.replaceState(null, "", `?${p}`);
}

function syncControls(initial = {}) {
  const areas = Object.keys(state.stats.areas);
  if (!$("area").options.length) fill($("area"), areas, initial.area || DEFAULT_AREA);
  fill($("type"), Object.keys(area().subtypes), initial.type || $("type").value || "Flat");
  fill($("rooms"), Object.keys(sub().rooms), initial.rooms || $("rooms").value);
  const c = cell();
  $("size").value = initial.size || Math.round(c.median_size);
  const share = sub().defaults.offplan_share ?? 0.5;
  $("offplan").value = initial.offplan ?? (share >= 0.5 ? "1" : "0");
}

function renderMarket() {
  const c = cell(), s = sub(), size = Number($("size").value) || c.median_size;
  const thin = c.thin || c.n < state.stats.thin_cell;
  const r = thin ? s : c;
  $("market-label").textContent = thin
    ? `${$("type").value}s of any size in ${$("area").value}`
    : `${roomsText($("rooms").value)} ${typeText()}s in ${$("area").value}`;
  $("market-range").textContent = `${fmtAed(r.p25 * size)} to ${fmtAed(r.p75 * size)} for ${fmtInt(size)} sqm`;
  const share = s.defaults.offplan_share;
  const mix = share === null ? "" : ` ${Math.round(share * 100)}% of ${typeText()} sales here were off-plan.`;
  const note = $("market-note");
  note.className = thin ? "note warn" : "note";
  note.textContent = thin
    ? `Only ${c.n} sale${c.n === 1 ? "" : "s"} of this exact type and room count in 2026, so this is the wider range for the community: middle half of ${fmtInt(r.n)} sales, median AED ${fmtInt(r.median)} per sqm.${mix}`
    : `Middle half of ${fmtInt(r.n)} registered sales in 2026, median AED ${fmtInt(r.median)} per sqm (AED ${fmtInt(r.p25)} to ${fmtInt(r.p75)}).${mix}`;
}

function renderEstimate() {
  const c = cell(), s = sub(), size = Number($("size").value) || c.median_size;
  if (!state.model) return;
  const d = s.defaults;
  const row = {
    area: $("area").value, property_subtype: $("type").value, rooms: $("rooms").value, rooms_n: roomsN($("rooms").value),
    nearest_metro: d.nearest_metro, nearest_mall: d.nearest_mall, nearest_landmark: d.nearest_landmark,
    actual_area_sqm: size, is_offplan: Number($("offplan").value), is_freehold: d.is_freehold ? 1 : 0,
    parking_n: d.parking_n, month_n: state.model.month_n,
  };
  const p = predictPpsqm(state.model, row);
  $("estimate").textContent = fmtAed(p.point * size);
  $("estimate-range").textContent = `80% range ${fmtAed(p.lo * size)} to ${fmtAed(p.hi * size)} · AED ${fmtInt(p.point)} per sqm`;
  $("model-note").textContent = `${$("offplan").value === "1" ? "Off-plan" : "Ready"} ${roomsText($("rooms").value)} ${typeText()} of ${fmtInt(size)} sqm, priced as of ${state.model.month_label}. Nearby metro, mall and landmark are taken from the community's typical listing.`;
}

async function renderArea() {
  const name = $("area").value;
  if (state.areaFileName !== name) {
    state.areaFile = await loadJson(`data/areas/${slug(name)}.json`);
    state.areaFileName = name;
  }
  if (state.areaFileName !== $("area").value) return; // superseded by a newer selection
  const comps = state.areaFile.comps[$("type").value]?.[$("rooms").value] || [];
  const body = $("comps").querySelector("tbody");
  body.replaceChildren(...comps.map(([date, project, size, value, ppsqm, offplan]) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${date}</td><td class="project">${project || "(project not published)"}</td><td class="num">${fmtInt(size)}</td>` +
      `<td class="num">${fmtInt(value)}</td><td class="num">${fmtInt(ppsqm)}</td>` +
      `<td><span class="tag ${offplan ? "" : "ready"}">${offplan ? "Off-plan" : "Ready"}</span></td>`;
    return tr;
  }));
  $("comps-note").textContent = comps.length
    ? `The ${comps.length} most recent registered sales of ${roomsText($("rooms").value)} ${typeText()}s in ${name}.`
    : `No registered 2026 sales of ${roomsText($("rooms").value)} ${typeText()}s in ${name}.`;
  const monthly = state.areaFile.monthly || {};
  const n = Object.values(monthly).reduce((a, m) => a + m.n, 0);
  $("chart-note").textContent = `Median registered price per sqm across all flats and villas in ${name} (${fmtInt(n)} sales), against all of Dubai.`;
  drawMonthly($("chart"), [
    { key: "dubai", label: "All of Dubai", data: state.monthly.all },
    { key: "community", label: name, data: monthly },
  ], $("tooltip"));
}

function update() {
  renderMarket();
  renderEstimate();
  writeUrl();
  renderArea().catch((e) => { $("comps-note").textContent = `Could not load this community's sales (${e.message}).`; });
}

function wire() {
  $("area").addEventListener("change", () => { syncControls(); update(); });
  $("type").addEventListener("change", () => { syncControls({ type: $("type").value }); update(); });
  $("rooms").addEventListener("change", () => { syncControls({ type: $("type").value, rooms: $("rooms").value }); update(); });
  $("size").addEventListener("input", update);
  $("offplan").addEventListener("change", update);
  let timer;
  window.addEventListener("resize", () => { clearTimeout(timer); timer = setTimeout(() => renderArea(), 150); });
}

async function init() {
  const [stats, meta, monthly] = await Promise.all([loadJson("data/stats.json"), loadJson("data/meta.json"), loadJson("data/monthly.json")]);
  Object.assign(state, { stats, meta, monthly });
  $("as-of").textContent = new Date(meta.data_as_of).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
  $("row-count").textContent = fmtInt(meta.rows);
  const m = meta.metrics;
  $("metrics").textContent = `median error ${m.model.mdape_ppsqm.toFixed(1)}% on ${fmtInt(m.test.rows)} later sales, against ${m.baseline.mdape_ppsqm.toFixed(1)}% for a simple median lookup; the 80% range covered ${(m.model.coverage_80 * 100).toFixed(0)}% of them`;
  syncControls(readUrl());
  wire();
  update();
  try {
    state.model = await loadJson("data/model.json");
    renderEstimate();
  } catch (e) {
    $("estimate").textContent = "unavailable";
    $("model-note").textContent = "The model file could not be loaded; the market range below still works.";
  }
}

init().catch((e) => { document.querySelector("main").insertAdjacentHTML("afterbegin", `<p class="note warn">Could not load the data (${e.message}).</p>`); });
